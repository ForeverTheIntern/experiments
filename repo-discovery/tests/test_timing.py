from __future__ import annotations

import time

import pytest

from repo_discovery.timing import PauseGuard


class Clocks:
    """Real clocks plus offsets a test can jump."""

    def __init__(self) -> None:
        self.mono = self.wall = 0.0

    def monotonic(self) -> float:
        return time.monotonic() + self.mono

    def time(self) -> float:
        return time.time() + self.wall


@pytest.fixture
def clocks():
    return Clocks()


@pytest.fixture
def guard(clocks):
    g = PauseGuard(
        tick=0.01, gap=0.5, skew=0.25, retries=2, monotonic=clocks.monotonic, wall=clocks.time
    )
    yield g
    g.close()


def test_quiet_sample_is_timed_once(guard):
    out, seconds = guard.time(sum, [1, 2, 3])
    assert out == 6
    assert 0 <= seconds < 0.5
    assert (guard.samples, guard.repeated, guard.unresolved) == (1, 0, 0)
    assert guard.jumps == []


@pytest.mark.parametrize("which", ["both", "wall"])
def test_sample_across_a_jump_is_repeated(guard, clocks, which):
    calls = []

    def work():
        calls.append(1)
        if len(calls) == 1:
            if which == "both":
                clocks.mono += 5
            clocks.wall += 5
        return len(calls)

    out, seconds = guard.time(work)
    assert out == 2
    assert seconds < 0.5
    assert (guard.repeated, guard.unresolved) == (1, 0)
    assert len(guard.jumps) == 1
    jump = guard.summary()["jumps"][0]
    if which == "both":
        assert jump["gap_s"] >= 5
    else:
        assert jump["wall_step_s"] == pytest.approx(5, abs=0.1)


def test_gives_up_after_retries_and_counts_it(guard, clocks):
    resets = []

    def work():
        clocks.mono += 5
        clocks.wall += 5

    guard.time(work, before=lambda: resets.append(1))
    assert len(resets) == 3
    assert (guard.repeated, guard.unresolved) == (2, 1)
    assert guard.summary()["unresolved"] == 1


def test_jump_outside_the_sample_is_ignored(guard, clocks):
    clocks.mono += 5
    clocks.wall += 5
    time.sleep(0.05)
    _, seconds = guard.time(sum, [1])
    assert seconds < 0.5
    assert guard.repeated == 0
    assert len(guard.jumps) == 1
