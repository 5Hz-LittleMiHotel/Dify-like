from __future__ import annotations

import json
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import HumanTask, RunCommand, RunEvent


def run_event_channel(run_id: str) -> str:
    return f"run:{run_id}:events"


def run_command_channel(run_id: str) -> str:
    return f"run:{run_id}:commands"


@lru_cache
def get_sync_redis() -> Redis:
    return Redis.from_url(get_settings().redis_url, decode_responses=True)


def append_run_event(db: Session, run_id: str, event_type: str, payload: dict[str, Any]) -> RunEvent:
    event = RunEvent(run_id=run_id, event_type=event_type, payload_json=payload)
    db.add(event)
    db.commit()
    db.refresh(event)
    try:
        get_sync_redis().publish(run_event_channel(run_id), json.dumps({"event_id": event.id}))
    except RedisError:
        pass
    return event


def list_run_events(db: Session, run_id: str, after_id: int = 0) -> list[RunEvent]:
    return list(
        db.scalars(
            select(RunEvent)
            .where(RunEvent.run_id == run_id, RunEvent.id > after_id)
            .order_by(RunEvent.id.asc())
        )
    )


def create_run_command(
    db: Session,
    run_id: str,
    command_type: str,
    payload: dict[str, Any] | None = None,
) -> RunCommand:
    command = RunCommand(
        run_id=run_id,
        command_type=command_type,
        payload_json=payload or {},
        status="pending",
    )
    db.add(command)
    db.commit()
    db.refresh(command)
    try:
        get_sync_redis().publish(run_command_channel(run_id), json.dumps({"command_id": command.id}))
    except RedisError:
        pass
    return command


def list_pending_commands(db: Session, run_id: str) -> list[RunCommand]:
    return list(
        db.scalars(
            select(RunCommand)
            .where(RunCommand.run_id == run_id, RunCommand.status == "pending")
            .order_by(RunCommand.id.asc())
        )
    )


def mark_command_processed(db: Session, command: RunCommand) -> None:
    command.status = "processed"
    command.processed_at = datetime.now(timezone.utc)
    db.commit()


def get_pending_human_task(db: Session, run_id: str) -> HumanTask | None:
    return db.scalar(
        select(HumanTask)
        .where(HumanTask.run_id == run_id, HumanTask.status == "pending")
        .order_by(HumanTask.created_at.desc())
    )
