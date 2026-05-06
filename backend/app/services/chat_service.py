import json
from collections.abc import AsyncIterator
from time import perf_counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Conversation, Message
from app.runtime.agent_runner import AgentRunner
from app.services.app_service import get_enabled_tool_names
from app.services.rag_service import retrieve_chunks
from app.services.run_log_service import add_step, create_run, finish_run


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def get_or_create_conversation(db: Session, app_id: str, conversation_id: str | None) -> Conversation:
    if conversation_id:
        conversation = db.get(Conversation, conversation_id)
        if conversation:
            return conversation
    conversation = Conversation(app_id=app_id)
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


def add_message(db: Session, conversation_id: str, role: str, content: str, metadata_json: dict | None = None) -> Message:
    message = Message(
        conversation_id=conversation_id,
        role=role,
        content=content,
        metadata_json=metadata_json or {},
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


async def chat_once(db: Session, app, query: str, conversation_id: str | None = None) -> dict:
    started = perf_counter()
    conversation = get_or_create_conversation(db, app.id, conversation_id)
    user_message = add_message(db, conversation.id, "user", query)
    run = create_run(db, app.id, conversation.id, user_message.id)

    retrieved = retrieve_chunks(db, app.id, query)
    add_step(db, run.id, "retrieval", "default_retrieval", {"query": query}, {"chunks": retrieved})

    enabled_tools = get_enabled_tool_names(db, app.id)
    runner = AgentRunner()
    tool_calls = []
    answer = ""
    async for event in runner.run(query, app.system_prompt, enabled_tools, retrieved):
        if event["type"] == "tool_call":
            tool_calls.append(event)
            add_step(
                db,
                run.id,
                "tool_call",
                event["name"],
                event["input"],
                event["output"],
            )
        elif event["type"] == "final":
            answer = event["content"]

    output = add_message(
        db,
        conversation.id,
        "assistant",
        answer,
        {"tool_calls": tool_calls, "retrieved_chunks": retrieved},
    )
    finish_run(db, run, started, output_message_id=output.id)
    return {
        "conversation_id": conversation.id,
        "run_id": run.id,
        "answer": answer,
        "tool_calls": tool_calls,
        "retrieved_chunks": retrieved,
    }


async def chat_stream(db: Session, app, query: str, conversation_id: str | None = None) -> AsyncIterator[str]:
    started = perf_counter()
    conversation = get_or_create_conversation(db, app.id, conversation_id)
    user_message = add_message(db, conversation.id, "user", query)
    run = create_run(db, app.id, conversation.id, user_message.id)
    yield _sse("run_started", {"conversation_id": conversation.id, "run_id": run.id})

    retrieved = retrieve_chunks(db, app.id, query)
    add_step(db, run.id, "retrieval", "default_retrieval", {"query": query}, {"chunks": retrieved})
    yield _sse("retrieval", {"chunks": retrieved})

    enabled_tools = get_enabled_tool_names(db, app.id)
    runner = AgentRunner()
    tool_calls = []
    deltas = []
    try:
        async for event in runner.run(query, app.system_prompt, enabled_tools, retrieved):
            if event["type"] == "tool_call":
                tool_calls.append(event)
                add_step(db, run.id, "tool_call", event["name"], event["input"], event["output"])
                yield _sse("tool_call", event)
            elif event["type"] == "message_delta":
                deltas.append(event["content"])
                yield _sse("message_delta", {"content": event["content"]})
            elif event["type"] == "final":
                answer = event["content"]
                output = add_message(
                    db,
                    conversation.id,
                    "assistant",
                    answer,
                    {"tool_calls": tool_calls, "retrieved_chunks": retrieved},
                )
                finish_run(db, run, started, output_message_id=output.id)
                yield _sse(
                    "final",
                    {
                        "conversation_id": conversation.id,
                        "run_id": run.id,
                        "answer": answer,
                        "tool_calls": tool_calls,
                        "retrieved_chunks": retrieved,
                    },
                )
    except Exception as exc:
        finish_run(db, run, started, status="error", error=str(exc))
        add_step(db, run.id, "error", "runtime_error", {}, {}, error=str(exc))
        yield _sse("error", {"message": str(exc)})


def list_messages(db: Session, conversation_id: str) -> list[Message]:
    return list(db.scalars(select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at.asc())))
