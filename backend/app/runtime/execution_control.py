from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timezone
from typing import Any

from app.db.models import Run
from app.db.session import SessionLocal
from app.runtime.runtime_command_subscriber import get_runtime_command_subscriber
from app.services.execution_event_service import append_run_event, list_pending_commands, mark_command_processed


COMMAND_FALLBACK_POLL_SECONDS = 3.0
SUBSCRIPTION_CONFIRM_TIMEOUT_SECONDS = 1.0


class ExecutionControl:
    def __init__(self, run_id: str):
        self.run_id = run_id
        self.phase = "queued"
        self.stop_requested = False
        self._resume_inputs: deque[tuple[str, str]] = deque()
        self._session: Any = None
        self._monitor_task: asyncio.Task | None = None
        self._interrupt_task: asyncio.Task | None = None
        self._command_wakeup = asyncio.Event()
        self._command_scan_lock = asyncio.Lock()
        self._subscriber_registered = False
        self._closed = False
        self._interrupt_sent = False

    async def start(self) -> None:
        await self._consume_commands()
        subscriber = get_runtime_command_subscriber()
        await subscriber.register(
            self.run_id,
            self._command_wakeup,
            timeout=SUBSCRIPTION_CONFIRM_TIMEOUT_SECONDS,
        )
        self._subscriber_registered = True
        await self._consume_commands()
        self._monitor_task = asyncio.create_task(self._monitor())

    async def close(self) -> None:
        if self._subscriber_registered:
            get_runtime_command_subscriber().unregister(self.run_id)
            self._subscriber_registered = False
        self._closed = True
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        if self._interrupt_task:
            await self._interrupt_task

    def register_session(self, session: Any) -> None:
        self._session = session
        self._interrupt_sent = False
        self._interrupt_task = None

    def set_phase(self, phase: str) -> None:
        self.phase = phase
        with SessionLocal() as db:
            run = db.get(Run, self.run_id)
            if run:
                run.phase = phase
                run.updated_at = datetime.now(timezone.utc)
                db.commit()
        self._interrupt_if_needed()

    def has_resume_input(self) -> bool:
        return bool(self._resume_inputs)

    def pop_resume_input(self) -> tuple[str, str] | None:
        if not self._resume_inputs:
            return None
        self._interrupt_sent = False
        self.stop_requested = False
        return self._resume_inputs.popleft()

    async def wait_for_resume_input(self, timeout: float = 1.0) -> tuple[str, str] | None:
        resume_input = self.pop_resume_input()
        if resume_input is not None:
            return resume_input
        try:
            await asyncio.wait_for(self._command_wakeup.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        self._command_wakeup.clear()
        await self._consume_commands()
        return self.pop_resume_input()

    def checkpoint_agent(self) -> dict[str, Any] | None:
        if self._session is None:
            return None
        return self._session.state_dict()

    async def _monitor(self) -> None:
        while not self._closed:
            try:
                await asyncio.wait_for(
                    self._command_wakeup.wait(),
                    timeout=COMMAND_FALLBACK_POLL_SECONDS,
                )
            except asyncio.TimeoutError:
                pass
            self._command_wakeup.clear()
            await self._consume_commands()
            self._interrupt_if_needed()

    async def _consume_commands(self) -> None:
        async with self._command_scan_lock:
            commands = await asyncio.to_thread(self._consume_commands_from_db, self.phase)
            for command_type, payload in commands:
                if command_type == "interrupt":
                    self.stop_requested = True
                elif command_type == "guidance":
                    content = str(payload.get("content") or "").strip()
                    if content:
                        self._resume_inputs.append(("guidance", content))
                elif command_type == "continue":
                    self._resume_inputs.append(("continue", ""))

    def _consume_commands_from_db(self, phase: str) -> list[tuple[str, dict[str, Any]]]:
        consumed: list[tuple[str, dict[str, Any]]] = []
        with SessionLocal() as db:
            for command in list_pending_commands(db, self.run_id):
                if command.command_type == "interrupt":
                    run = db.get(Run, self.run_id)
                    if run and run.status == "running":
                        run.status = "interrupt_requested"
                        run.phase = "interrupt_requested"
                        db.commit()
                    append_run_event(
                        db,
                        self.run_id,
                        "interrupt_requested",
                        {"run_id": self.run_id, "phase": phase},
                    )
                consumed.append((command.command_type, dict(command.payload_json or {})))
                mark_command_processed(db, command)
        return consumed

    def _interrupt_if_needed(self) -> None:
        if self._session is None or self._interrupt_sent:
            return
        if not (self.stop_requested or self._resume_inputs):
            return
        if self.phase not in {"thinking", "streaming", "agent"}:
            return
        self._interrupt_task = asyncio.create_task(self._session.interrupt())
        self._interrupt_sent = True
