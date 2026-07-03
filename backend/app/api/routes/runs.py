from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.db.models import App, HumanTask, Message, Run, User, Workflow
from app.db.session import get_db
from app.schemas import GuidanceRequest, HumanTaskRespond, RunCommandOut, RunOut, RunStepOut
from app.services.execution_event_service import append_run_event, create_run_command, get_pending_human_task
from app.services.execution_runtime_service import enqueue_workflow_run
from app.services.run_log_service import get_run_for_user, list_run_steps, list_runs
from app.services.run_event_stream_service import stream_run_events

router = APIRouter(tags=["runs"])


@router.get("/workflows/{workflow_id}/runs", response_model=list[RunOut])
def list_workflow_runs(workflow_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    workflow = db.scalar(
        select(Workflow)
        .join(App, App.id == Workflow.app_id)
        .where(Workflow.id == workflow_id, (App.owner_user_id == current_user.id) | (Workflow.published_version_id.is_not(None)))
    )
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return list_runs(db, workflow_id, current_user.id)


@router.get("/runs/{run_id}/steps", response_model=list[RunStepOut])
def get_run_steps(run_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not get_run_for_user(db, run_id, current_user.id):
        raise HTTPException(status_code=404, detail="Run not found")
    return list_run_steps(db, run_id)


@router.get("/runs/{run_id}", response_model=RunOut)
def get_run(run_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    run = get_run_for_user(db, run_id, current_user.id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return _run_payload(db, run, get_pending_human_task(db, run.id))


@router.get("/runs/{run_id}/events")
def get_run_events(
    run_id: str,
    after_id: int = Query(default=0, ge=0),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not get_run_for_user(db, run_id, current_user.id):
        raise HTTPException(status_code=404, detail="Run not found")
    cursor = after_id
    if last_event_id and last_event_id.isdigit():
        cursor = max(cursor, int(last_event_id))
    return StreamingResponse(stream_run_events(run_id, cursor), media_type="text/event-stream")


@router.post("/runs/{run_id}/interrupt", response_model=RunCommandOut)
def interrupt_run(run_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    run = get_run_for_user(db, run_id, current_user.id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.status not in {"queued", "running", "interrupt_requested"}:
        raise HTTPException(status_code=409, detail=f"Run cannot be interrupted from status '{run.status}'")
    return create_run_command(db, run.id, "interrupt")


@router.post("/runs/{run_id}/guidance", response_model=RunCommandOut)
def guide_run(
    run_id: str,
    payload: GuidanceRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    run = get_run_for_user(db, run_id, current_user.id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.status not in {"running", "interrupt_requested", "interrupted"}:
        raise HTTPException(status_code=409, detail=f"Run cannot accept guidance from status '{run.status}'")
    guidance_message = Message(
        conversation_id=run.conversation_id,
        role="user",
        content=payload.content,
        metadata_json={"run_id": run.id, "source": "runtime_guidance"},
    )
    db.add(guidance_message)
    db.commit()
    command = create_run_command(
        db,
        run.id,
        "guidance",
        {"content": payload.content, "message_id": guidance_message.id},
    )
    append_run_event(db, run.id, "guidance_received", {"run_id": run.id, "content": payload.content})
    if run.status == "interrupted":
        run.status = "queued"
        run.phase = "queued"
        run.ended_at = None
        db.commit()
        enqueue_workflow_run(run.id)
    return command


@router.post("/human-tasks/{task_id}/respond", response_model=RunOut)
def respond_human_task(
    task_id: str,
    payload: HumanTaskRespond,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    task = db.scalar(select(HumanTask).where(HumanTask.id == task_id).with_for_update())
    if not task:
        raise HTTPException(status_code=404, detail="Human task not found")
    run = get_run_for_user(db, task.run_id, current_user.id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    if task.status != "pending" or run.status != "waiting_human":
        raise HTTPException(status_code=409, detail="Human task has already been handled")

    now = datetime.now(timezone.utc)
    task.responded_by_user_id = current_user.id
    task.responded_at = now
    if payload.action == "reject":
        task.status = "rejected"
        run.status = "rejected"
        run.phase = "rejected"
        run.ended_at = now
        _update_human_timeline(db, run, task, "rejected", None)
        db.commit()
        append_run_event(db, run.id, "rejected", {"run_id": run.id, "human_task_id": task.id})
        return _run_payload(db, run, None)

    _validate_human_value(task, payload.value)
    task.status = "submitted"
    task.response_json = payload.value
    checkpoint = dict(run.checkpoint_json or {})
    context = dict(checkpoint.get("context") or {})
    context[task.output_key] = payload.value
    human_inputs = dict(context.get("human_inputs") or {})
    human_inputs[task.node_id] = payload.value
    context["human_inputs"] = human_inputs
    checkpoint["context"] = context
    run.checkpoint_json = checkpoint
    run.status = "queued"
    run.phase = "queued"
    run.current_node_id = str(checkpoint.get("next_node_id") or "")
    run.ended_at = None
    _update_human_timeline(db, run, task, "submitted", payload.value)
    db.commit()
    append_run_event(
        db,
        run.id,
        "human_submitted",
        {"run_id": run.id, "human_task_id": task.id, "value": payload.value},
    )
    enqueue_workflow_run(run.id)
    return _run_payload(db, run, None)


def _validate_human_value(task: HumanTask, value) -> None:
    if task.required and (value is None or value == ""):
        raise HTTPException(status_code=422, detail="A value is required")
    if task.input_type == "confirm" and not isinstance(value, bool):
        raise HTTPException(status_code=422, detail="Confirm input must be a boolean")
    if task.input_type == "text" and value is not None and not isinstance(value, str):
        raise HTTPException(status_code=422, detail="Text input must be a string")
    if task.input_type == "json" and value is not None and not isinstance(value, (dict, list)):
        raise HTTPException(status_code=422, detail="JSON input must be an object or array")


def _run_payload(db: Session, run: Run, human_task: HumanTask | None) -> dict:
    return {
        "id": run.id,
        "app_id": run.app_id,
        "workflow_id": run.workflow_id,
        "workflow_version_id": run.workflow_version_id,
        "conversation_id": run.conversation_id,
        "status": run.status,
        "phase": run.phase,
        "current_node_id": run.current_node_id,
        "latency_ms": run.latency_ms,
        "error": run.error,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "ended_at": run.ended_at,
        "last_event_id": _last_event_id(db, run.id),
        "human_task": human_task,
    }


def _last_event_id(db: Session, run_id: str) -> int:
    from app.db.models import RunEvent

    return int(db.scalar(select(func.max(RunEvent.id)).where(RunEvent.run_id == run_id)) or 0)


def _update_human_timeline(db: Session, run: Run, task: HumanTask, status: str, value) -> None:
    output = db.get(Message, run.output_message_id) if run.output_message_id else None
    if not output or not isinstance(output.metadata_json, dict):
        return
    metadata = dict(output.metadata_json)
    timeline = list(metadata.get("timeline") or [])
    for item in timeline:
        if isinstance(item, dict) and item.get("kind") == "human" and item.get("task_id") == task.id:
            item["status"] = status
            if status == "submitted":
                item["value"] = value
            break
    metadata["timeline"] = timeline
    metadata["status"] = "rejected" if status == "rejected" else "streaming"
    output.metadata_json = metadata
