from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timezone
from typing import Any

from app.db.models import Run
from app.db.session import SessionLocal
from app.services.execution_event_service import append_run_event, list_pending_commands, mark_command_processed


class ExecutionControl:
    def __init__(self, run_id: str):
        self.run_id = run_id
        self.phase = "queued"
        self.stop_requested = False
        self._resume_inputs: deque[tuple[str, str]] = deque()
        self._session: Any = None
        self._monitor_task: asyncio.Task | None = None
        self._interrupt_task: asyncio.Task | None = None
        self._closed = False
        self._interrupt_sent = False

    async def start(self) -> None:
        self._consume_commands()
        self._monitor_task = asyncio.create_task(self._monitor())

    async def close(self) -> None:
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
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            self._consume_commands()
            resume_input = self.pop_resume_input()
            if resume_input is not None:
                return resume_input
            await asyncio.sleep(0.1)
        return None

    def checkpoint_agent(self) -> dict[str, Any] | None:
        if self._session is None:
            return None
        return self._session.state_dict()

    async def _monitor(self) -> None:
        while not self._closed:
            self._consume_commands()
            self._interrupt_if_needed()
            await asyncio.sleep(0.25)

    def _consume_commands(self) -> None:
        with SessionLocal() as db:
            for command in list_pending_commands(db, self.run_id):
                if command.command_type == "interrupt":
                    self.stop_requested = True
                    run = db.get(Run, self.run_id)
                    if run and run.status == "running":
                        run.status = "interrupt_requested"
                        run.phase = "interrupt_requested"
                        db.commit()
                    append_run_event(
                        db,
                        self.run_id,
                        "interrupt_requested",
                        {"run_id": self.run_id, "phase": self.phase},
                    )
                elif command.command_type == "guidance":
                    content = str(command.payload_json.get("content") or "").strip()
                    if content:
                        self._resume_inputs.append(("guidance", content))
                elif command.command_type == "continue":
                    self._resume_inputs.append(("continue", ""))
                mark_command_processed(db, command)

    def _interrupt_if_needed(self) -> None:
        if self._session is None or self._interrupt_sent:
            return
        if not (self.stop_requested or self._resume_inputs):
            return
        if self.phase not in {"thinking", "streaming", "agent"}:
            return
        self._interrupt_task = asyncio.create_task(self._session.interrupt())
        self._interrupt_sent = True
