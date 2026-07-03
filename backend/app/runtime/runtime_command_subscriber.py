from __future__ import annotations

import asyncio
import logging
import queue
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass

from redis import Redis
from redis.client import PubSub
from redis.exceptions import RedisError

from app.core.config import get_settings
from app.services.execution_event_service import run_command_channel


logger = logging.getLogger(__name__)
REDIS_RECONNECT_SECONDS = 1.0
PUBSUB_READ_TIMEOUT_SECONDS = 0.2


@dataclass
class _Registration:
    loop: asyncio.AbstractEventLoop
    wakeup: asyncio.Event


@dataclass
class _Operation:
    kind: str
    run_id: str = ""
    acknowledgement: Future[bool] | None = None


class RuntimeCommandSubscriber:
    def __init__(self) -> None:
        self._registrations: dict[str, _Registration] = {}
        self._registrations_lock = threading.Lock()
        self._operations: queue.Queue[_Operation] = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._thread_lock = threading.Lock()
        self._redis: Redis | None = None
        self._pubsub: PubSub | None = None
        self._next_reconnect_at = 0.0

    async def register(self, run_id: str, wakeup: asyncio.Event, timeout: float) -> bool:
        loop = asyncio.get_running_loop()
        with self._registrations_lock:
            self._registrations[run_id] = _Registration(loop=loop, wakeup=wakeup)
        self._ensure_thread()

        acknowledgement: Future[bool] = Future()
        self._operations.put(_Operation("subscribe", run_id, acknowledgement))
        try:
            return await asyncio.wait_for(asyncio.wrap_future(acknowledgement), timeout=timeout)
        except asyncio.TimeoutError:
            return False

    def unregister(self, run_id: str) -> None:
        with self._registrations_lock:
            self._registrations.pop(run_id, None)
        if self._thread:
            self._operations.put(_Operation("unsubscribe", run_id))

    def shutdown(self) -> None:
        self._stop.set()
        self._operations.put(_Operation("shutdown"))
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=2.0)
        if not thread or not thread.is_alive():
            self._close_connection()
        with self._registrations_lock:
            self._registrations.clear()

    def _ensure_thread(self) -> None:
        with self._thread_lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="runtime-command-subscriber",
                daemon=True,
            )
            self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            self._process_operations()
            if self._stop.is_set():
                break
            if self._pubsub is None:
                self._connect_if_due()
                if self._pubsub is None:
                    self._stop.wait(REDIS_RECONNECT_SECONDS)
                    continue
            with self._registrations_lock:
                has_registrations = bool(self._registrations)
            if not has_registrations:
                self._stop.wait(PUBSUB_READ_TIMEOUT_SECONDS)
                continue
            try:
                message = self._pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=PUBSUB_READ_TIMEOUT_SECONDS,
                )
                if message and message.get("type") == "message":
                    self._dispatch(str(message.get("channel") or ""))
            except (RedisError, RuntimeError) as exc:
                logger.warning("Runtime command Pub/Sub disconnected: %s", exc)
                self._close_connection()
                self._next_reconnect_at = time.monotonic() + REDIS_RECONNECT_SECONDS

        self._close_connection()

    def _process_operations(self) -> None:
        while True:
            try:
                operation = self._operations.get_nowait()
            except queue.Empty:
                return

            if operation.kind == "shutdown":
                self._stop.set()
                self._acknowledge(operation, True)
                return
            if operation.kind == "subscribe":
                subscribed = self._subscribe(operation.run_id)
                self._acknowledge(operation, subscribed)
            elif operation.kind == "unsubscribe":
                self._unsubscribe(operation.run_id)

    def _connect_if_due(self) -> None:
        if time.monotonic() < self._next_reconnect_at:
            return
        try:
            self._redis = Redis.from_url(
                get_settings().redis_url,
                decode_responses=True,
                socket_connect_timeout=1.0,
                socket_timeout=1.0,
            )
            self._pubsub = self._redis.pubsub(ignore_subscribe_messages=True)
            with self._registrations_lock:
                channels = [run_command_channel(run_id) for run_id in self._registrations]
            if channels:
                self._pubsub.subscribe(*channels)
            self._next_reconnect_at = 0.0
        except RedisError as exc:
            logger.warning("Runtime command Pub/Sub connection failed: %s", exc)
            self._close_connection()
            self._next_reconnect_at = time.monotonic() + REDIS_RECONNECT_SECONDS

    def _subscribe(self, run_id: str) -> bool:
        if self._pubsub is None:
            self._connect_if_due()
        if self._pubsub is None:
            return False
        try:
            self._pubsub.subscribe(run_command_channel(run_id))
            return True
        except RedisError as exc:
            logger.warning("Runtime command subscription failed for run %s: %s", run_id, exc)
            self._close_connection()
            self._next_reconnect_at = time.monotonic() + REDIS_RECONNECT_SECONDS
            return False

    def _unsubscribe(self, run_id: str) -> None:
        if self._pubsub is None:
            return
        try:
            self._pubsub.unsubscribe(run_command_channel(run_id))
        except RedisError:
            self._close_connection()
            self._next_reconnect_at = time.monotonic() + REDIS_RECONNECT_SECONDS

    def _dispatch(self, channel: str) -> None:
        prefix = "run:"
        suffix = ":commands"
        if not channel.startswith(prefix) or not channel.endswith(suffix):
            return
        run_id = channel[len(prefix) : -len(suffix)]
        with self._registrations_lock:
            registration = self._registrations.get(run_id)
        if not registration:
            return
        try:
            registration.loop.call_soon_threadsafe(registration.wakeup.set)
        except RuntimeError:
            self.unregister(run_id)

    def _close_connection(self) -> None:
        pubsub, redis = self._pubsub, self._redis
        self._pubsub = None
        self._redis = None
        if pubsub:
            try:
                pubsub.close()
            except RedisError:
                pass
        if redis:
            try:
                redis.close()
            except RedisError:
                pass

    def _acknowledge(self, operation: _Operation, result: bool) -> None:
        if operation.acknowledgement and not operation.acknowledgement.done():
            operation.acknowledgement.set_result(result)


_subscriber = RuntimeCommandSubscriber()


def get_runtime_command_subscriber() -> RuntimeCommandSubscriber:
    return _subscriber


def shutdown_runtime_command_subscriber() -> None:
    _subscriber.shutdown()
