"""Find the VM pauses in agent traces, and the cases they cut across.

Usage::

    python scripts/vm_pauses.py traces/n50-u1000 traces/bm25f-u1000 ...

A cloud VM pauses about ten minutes after an agent's turn ends and resumes at the next
event for it. The runner times cases on a monotonic clock, which stops during a pause,
but a case in flight still stalls for up to a minute after resume, so its time is not
comparable with the others. Its quality and cost stand.

A trace that records ``wall_seconds`` crossed a pause when its wall time exceeds its
monotonic ``seconds``. For older traces, a pause is a gap of more than ``GAP_S`` in all
activity (event ``timestamp_ms`` and trace write times) across the directories given, so
pass every directory that was running then. A case crossed it when its directory was
active before the gap and its trace was written after it, but its monotonic time puts the
agent's start before the gap ended. A gap with no directory active on both sides is idle
time between runs, and the cases that start in it are not flagged.
"""

from __future__ import annotations

import argparse
import datetime
import itertools
import json
from collections.abc import Iterable, Iterator
from pathlib import Path

GAP_S = 300
# An agent starting after a gap emits its first event about 5 s in; more than this means
# it started before the gap ended.
SLACK_S = 10


def _activity(directory: Path) -> Iterator[float]:
    for trace in directory.glob("*.json"):
        yield trace.stat().st_mtime
        events = trace.with_name(f"{trace.stem}.events.jsonl")
        if events.is_file():
            for line in events.read_text().splitlines():
                stamp = json.loads(line).get("timestamp_ms")
                if stamp:
                    yield stamp / 1e3


class Pauses:
    """The VM pauses seen across trace directories, as (start, end) epoch seconds."""

    def __init__(self, trace_dirs: Iterable[Path]) -> None:
        self.since: dict[Path, float] = {}
        times: list[float] = []
        for directory in trace_dirs:
            seen = list(_activity(directory))
            if seen:
                self.since[directory.resolve()] = min(seen)
            times += seen
        times.sort()
        self.windows = [(a, b) for a, b in itertools.pairwise(times) if b - a > GAP_S]

    def crossed(self, trace: dict, path: Path) -> bool:
        """Whether the case whose trace is at ``path`` was in flight during a pause."""
        if "wall_seconds" in trace:
            return trace["wall_seconds"] - trace["seconds"] > SLACK_S
        written = path.stat().st_mtime
        since = self.since.get(path.parent.resolve(), written)
        return any(
            since < start and written > end and written - trace["seconds"] < end - SLACK_S
            for start, end in self.windows
        )


def _utc(seconds: float) -> str:
    return datetime.datetime.fromtimestamp(seconds, datetime.UTC).strftime("%Y-%m-%d %H:%M:%S")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("trace_dirs", type=Path, nargs="+")
    args = parser.parse_args(argv)
    pauses = Pauses(args.trace_dirs)
    for start, end in pauses.windows:
        print(f"gap: no activity from {_utc(start)} to {_utc(end)} UTC, {end - start:.0f} s")
    for directory in args.trace_dirs:
        hit = []
        for path in sorted(directory.glob("*.json")):
            trace = json.loads(path.read_text())
            if pauses.crossed(trace, path):
                hit.append(f"{path.stem} ({trace['status']})")
        print(f"{directory.name}: {len(hit)} crossed" + (": " + ", ".join(hit) if hit else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
