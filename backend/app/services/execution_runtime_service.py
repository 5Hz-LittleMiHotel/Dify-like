from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.db.models import App, HumanTask, Message, Run, WorkflowVersion
from app.db.session import SessionLocal
from app.runtime.execution_control import ExecutionControl
from app.runtime.workflow_executor import WorkflowExecutor
from app.services.chat_service import MAX_AGENT_HISTORY_MESSAGES, _append_error, _record_timeline_event
from app.services.execution_event_service import append_run_event


TERMINAL_RUN_STATUSES = {"success", "rejected", "error"}


def enqueue_workflow_run(run_id: str) -> None:
    from app.worker.celery_app import execute_workflow_run

    execute_workflow_run.delay(run_id)


def execute_workflow_run_sync(run_id: str) -> None:
    asyncio.run(_execute_workflow_run(run_id))


async def _execute_workflow_run(run_id: str) -> None:
    control = ExecutionControl(run_id)
    with SessionLocal() as db:
        run = db.scalar(select(Run).where(Run.id == run_id).with_for_update(skip_locked=True))
        if not run or run.status in TERMINAL_RUN_STATUSES or run.status == "running":
            return
        app = db.get(App, run.app_id)
        workflow_version = db.get(WorkflowVersion, run.workflow_version_id)
        input_message = db.get(Message, run.input_message_id) if run.input_message_id else None
        output_message = db.get(Message, run.output_message_id) if run.output_message_id else None
        if not app or not workflow_version or not input_message or not output_message:
            _fail_run(db, run, output_message, "Execution dependencies are missing", [])
            return

        run.status = "running"
        run.phase = "workflow"
        run.ended_at = None
        db.commit()
        append_run_event(db, run.id, "run_resumed", {"run_id": run.id, "status": "running"})

        history_messages = _history_before_input(db, run, input_message)
        metadata = output_message.metadata_json if isinstance(output_message.metadata_json, dict) else {}
        timeline = metadata.get("timeline") if isinstance(metadata.get("timeline"), list) else []
        partial_answer = output_message.content or ""
        executor = WorkflowExecutor(
            db,
            app,
            workflow_version.spec_json,
            run.id,
            run.workflow_id,
            control=control,
        )

        await control.start()
        try:
            async for event in executor.execute(
                input_message.content,
                run.conversation_id,
                _conversation_user_id(db, run.conversation_id),
                history_messages=history_messages,
                checkpoint=run.checkpoint_json if isinstance(run.checkpoint_json, dict) else {},
            ):
                event_type = str(event.get("type") or "")
                if event_type == "workflow_node_started":
                    run.current_node_id = str(event.get("node_id") or "")
                    run.phase = str(event.get("node_type") or "workflow")
                    db.commit()
                    continue
                if event_type == "workflow_checkpoint":
                    run.checkpoint_json = event.get("checkpoint") or {}
                    run.current_node_id = str((event.get("checkpoint") or {}).get("next_node_id") or "")
                    db.commit()
                    continue

                if event_type != "human_required":
                    _record_timeline_event(timeline, event)
                if event_type == "message_delta":
                    partial_answer += str(event.get("content") or "")
                if event_type == "human_required":
                    task = _create_human_task(db, run, event, executor.checkpoint)
                    payload = {**event, "human_task_id": task.id, "run_id": run.id}
                    _record_timeline_event(timeline, payload)
                    append_run_event(db, run.id, "human_required", payload)
                    _save_output(output_message, partial_answer, "waiting_human", timeline, executor)
                    db.commit()
                    return
                if event_type == "adapter_error":
                    message = str(event.get("message") or "Agent adapter error")
                    _append_error(timeline, message)
                    append_run_event(db, run.id, "error", {"message": message, "run_id": run.id})
                    _fail_run(db, run, output_message, message, timeline, executor, partial_answer)
                    return
                if event_type == "agent_interrupted":
                    append_run_event(db, run.id, "agent_interrupted", {"run_id": run.id})
                    continue
                if event_type in {
                    "retrieval",
                    "thinking_delta",
                    "message_delta",
                    "tool_call",
                    "tool_result",
                    "workflow_warning",
                }:
                    append_run_event(db, run.id, event_type, event)
                elif event_type == "final":
                    partial_answer = str(event.get("content") or partial_answer)

            if executor.interrupted:
                run.status = "interrupted"
                run.phase = "interrupted"
                run.checkpoint_json = executor.checkpoint
                run.ended_at = datetime.now(timezone.utc)
                _save_output(output_message, partial_answer, "interrupted", timeline, executor)
                db.commit()
                append_run_event(db, run.id, "interrupted", {"run_id": run.id, "status": "interrupted"})
                return

            answer = executor.result.answer or partial_answer
            run.status = "success"
            run.phase = "success"
            run.current_node_id = ""
            run.checkpoint_json = {}
            run.ended_at = datetime.now(timezone.utc)
            run.latency_ms = _run_latency_ms(run)
            _save_output(output_message, answer, "completed", timeline, executor)
            output_message.created_at = datetime.now(timezone.utc)
            db.commit()
            append_run_event(
                db,
                run.id,
                "final",
                {
                    "conversation_id": run.conversation_id,
                    "run_id": run.id,
                    "answer": answer,
                    "tool_calls": executor.result.tool_calls,
                    "retrieved_chunks": executor.result.retrieved_chunks,
                },
            )
        except Exception as exc:
            message = str(exc)
            _append_error(timeline, message)
            _fail_run(db, run, output_message, message, timeline, executor, partial_answer)
            append_run_event(db, run.id, "error", {"message": message, "run_id": run.id})
        finally:
            await control.close()


def _history_before_input(db, run: Run, input_message: Message) -> list[dict[str, str]]:
    messages = list(
        db.scalars(
            select(Message)
            .where(Message.conversation_id == run.conversation_id)
            .order_by(Message.created_at.asc())
        )
    )
    prior: list[Message] = []
    for message in messages:
        if message.id == input_message.id:
            break
        prior.append(message)
    return [
        {"role": message.role, "content": message.content}
        for message in prior[-MAX_AGENT_HISTORY_MESSAGES:]
        if message.role in {"user", "assistant"} and message.content
    ]


def _conversation_user_id(db, conversation_id: str) -> str:
    from app.db.models import Conversation

    conversation = db.get(Conversation, conversation_id)
    return str(conversation.user_id if conversation else "")


def _create_human_task(db, run: Run, event: dict[str, Any], checkpoint: dict[str, Any]) -> HumanTask:
    task = HumanTask(
        run_id=run.id,
        node_id=str(event.get("node_id") or "human"),
        input_type=str(event.get("input_type") or "confirm"),
        title=str(event.get("title") or "需要人工输入"),
        description=str(event.get("description") or ""),
        required=bool(event.get("required", True)),
        default_json=event.get("default"),
        output_key=str(event.get("output_key") or "human_input"),
    )
    db.add(task)
    run.status = "waiting_human"
    run.phase = "waiting_human"
    run.current_node_id = task.node_id
    run.checkpoint_json = checkpoint
    run.ended_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(task)
    return task


def _save_output(
    output: Message,
    content: str,
    status: str,
    timeline: list[dict[str, Any]],
    executor: WorkflowExecutor,
) -> None:
    output.content = content
    output.metadata_json = {
        "status": status,
        "timeline": timeline,
        "tool_calls": executor.result.tool_calls,
        "retrieved_chunks": executor.result.retrieved_chunks,
    }


def _fail_run(
    db,
    run: Run,
    output: Message | None,
    error: str,
    timeline: list[dict[str, Any]],
    executor: WorkflowExecutor | None = None,
    partial_answer: str = "",
) -> None:
    run.status = "error"
    run.phase = "error"
    run.error = error
    run.ended_at = datetime.now(timezone.utc)
    run.latency_ms = _run_latency_ms(run)
    if output:
        output.content = partial_answer
        output.metadata_json = {
            "status": "error",
            "timeline": timeline,
            "tool_calls": executor.result.tool_calls if executor else [],
            "retrieved_chunks": executor.result.retrieved_chunks if executor else [],
        }
    db.commit()


def _run_latency_ms(run: Run) -> int:
    now = datetime.now(timezone.utc)
    created_at = run.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return max(int((now - created_at).total_seconds() * 1000), 0)
