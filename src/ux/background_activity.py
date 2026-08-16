"""Reusable progress heartbeat for long-running user-triggered work.

The project UX rule is simple: background work must never look frozen. Callers
publish meaningful phase changes immediately and this helper emits bounded,
periodic heartbeats while a phase is still active. It deliberately carries only
observable execution state; it must never expose prompts, secrets or chain of
thought.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class ActivitySnapshot:
    phase: str
    summary: str
    elapsed_seconds: int
    idle_seconds: int
    heartbeat: bool = False


class BackgroundActivity:
    """Publish phase changes plus periodic liveness heartbeats.

    ``on_update`` is expected to update one durable UI surface in-place. The
    callback is invoked immediately by :meth:`start` / :meth:`set_phase` and
    subsequently by a daemon heartbeat thread until :meth:`stop` is called.
    """

    def __init__(
        self,
        on_update: Callable[[ActivitySnapshot], None],
        *,
        heartbeat_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._on_update = on_update
        self._heartbeat_seconds = max(float(heartbeat_seconds), 1.0)
        self._clock = clock
        self._started_at = 0.0
        self._last_activity_at = 0.0
        self._phase = "working"
        self._summary = "Working in the background."
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self, phase: str, summary: str) -> None:
        now = self._clock()
        with self._lock:
            self._started_at = now
            self._last_activity_at = now
            self._phase = phase
            self._summary = summary
        self._emit(heartbeat=False)
        self._thread = threading.Thread(
            target=self._heartbeat_loop,
            name="background-activity-heartbeat",
            daemon=True,
        )
        self._thread.start()

    def set_phase(self, phase: str, summary: str) -> None:
        now = self._clock()
        with self._lock:
            self._phase = phase
            self._summary = summary
            self._last_activity_at = now
        self._emit(heartbeat=False)

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=min(self._heartbeat_seconds, 2.0))

    def __enter__(self) -> "BackgroundActivity":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(self._heartbeat_seconds):
            self._emit(heartbeat=True)

    def _emit(self, *, heartbeat: bool) -> None:
        with self._lock:
            now = self._clock()
            started_at = self._started_at or now
            snapshot = ActivitySnapshot(
                phase=self._phase,
                summary=self._summary,
                elapsed_seconds=max(0, int(now - started_at)),
                idle_seconds=max(0, int(now - (self._last_activity_at or now))),
                heartbeat=heartbeat,
            )
        try:
            self._on_update(snapshot)
        except Exception:
            # UI feedback must never crash the underlying business operation.
            # The caller is responsible for logging publisher failures.
            return
