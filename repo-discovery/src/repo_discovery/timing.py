"""Wall-clock samples that are repeated when the machine was paused or its clock stepped.

A cloud VM can be paused while no agent turn is open and resumed later. On resume the
guest's clocks may jump: CLOCK_MONOTONIC can include the pause (kvm-clock follows the
host), and the wall clock can be stepped to catch up. A sample that spans either measures
the jump, not the work.

``PauseGuard`` runs a heartbeat thread on CLOCK_MONOTONIC. A gap between beats longer than
``gap`` seconds, or a difference of more than ``skew`` seconds between how far the
monotonic and wall clocks moved in one beat, is recorded as a jump. ``PauseGuard.time``
repeats a sample that overlaps a jump, up to ``retries`` times.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any


class PauseGuard:
    def __init__(
        self,
        tick: float = 0.05,
        gap: float = 1.0,
        skew: float = 0.25,
        retries: int = 3,
        monotonic: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
    ) -> None:
        self.tick, self.gap, self.skew, self.retries = tick, gap, skew, retries
        self._monotonic, self._wall = monotonic, wall
        self.jumps: list[tuple[float, float, float]] = []
        self.samples = self.repeated = self.unresolved = 0
        self._lock = threading.Lock()
        self._last = monotonic()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._beat, name="pause-guard", daemon=True)
        self._thread.start()

    def _beat(self) -> None:
        mono, wall = self._monotonic(), self._wall()
        while not self._stop.wait(self.tick):
            m, w = self._monotonic(), self._wall()
            step = (w - wall) - (m - mono)
            with self._lock:
                if m - mono > self.gap or abs(step) > self.skew:
                    self.jumps.append((mono, m, step))
                self._last = m
            mono, wall = m, w

    def crossed(self, start: float, end: float) -> bool:
        """Whether a jump overlaps [start, end], once a beat after ``end`` has been seen."""
        while True:
            with self._lock:
                if self._last >= end:
                    return any(a < end and b > start for a, b, _ in self.jumps)
            if not self._thread.is_alive():
                raise RuntimeError("pause guard heartbeat stopped")
            time.sleep(self.tick / 5)

    def time(self, fn: Callable[..., Any], *args: Any, before: Callable[[], Any] | None = None):
        """``(fn(*args), seconds)``; ``before`` runs ahead of each attempt, untimed."""
        self.samples += 1
        for attempt in range(self.retries + 1):
            if before is not None:
                before()
            start = self._monotonic()
            out = fn(*args)
            end = self._monotonic()
            if not self.crossed(start, end):
                return out, end - start
            if attempt < self.retries:
                self.repeated += 1
        self.unresolved += 1
        return out, end - start

    def close(self) -> None:
        self._stop.set()
        self._thread.join()

    def summary(self) -> dict:
        with self._lock:
            jumps = [
                {"gap_s": round(b - a, 3), "wall_step_s": round(s, 3)} for a, b, s in self.jumps
            ]
        return {
            "clock": "CLOCK_MONOTONIC",
            "tick_s": self.tick,
            "gap_s": self.gap,
            "skew_s": self.skew,
            "samples": self.samples,
            "repeated": self.repeated,
            "unresolved": self.unresolved,
            "jumps": jumps,
        }
