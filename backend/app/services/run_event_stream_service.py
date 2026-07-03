from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from redis import asyncio as aioredis
from redis.exceptions import RedisError

from app.core.config import get_settings
from app.db.models import Run
from app.db.session import SessionLocal
from app.services.execution_event_service import list_run_events, run_event_channel


def _sse(event_id: int, event_type: str, payload: dict) -> str:
    return (
        f"id: {event_id}\n"
        f"event: {event_type}\n"
        f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
    )


async def stream_run_events(run_id: str, after_id: int = 0) -> AsyncIterator[str]:
    redis = None
    pubsub = None
    try:
        redis = aioredis.from_url(get_settings().redis_url, decode_responses=True)
        pubsub = redis.pubsub()
        await pubsub.subscribe(run_event_channel(run_id))
    except RedisError:
        pubsub = None

    last_id = after_id
    try:
        while True:
            with SessionLocal() as db:
                events = list_run_events(db, run_id, last_id)
                for event in events:
                    last_id = event.id
                    yield _sse(event.id, event.event_type, event.payload_json)
                run = db.get(Run, run_id)
                terminal = not run or run.status in {"success", "rejected", "error"}
            if terminal and not events:
                return

            if pubsub:
                try:
                    await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                except RedisError:
                    pubsub = None
            else:
                await asyncio.sleep(0.75)
            yield ": heartbeat\n\n"
    finally:
        if pubsub:
            await pubsub.close()
        if redis:
            await redis.close()
