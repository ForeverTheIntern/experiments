from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from repo_discovery.rankers import sandbox

needs_namespaces = pytest.mark.skipif(
    sandbox.unavailable() is not None and not os.environ.get("REQUIRE_NAMESPACES"),
    reason=f"no isolation here: {sandbox.unavailable()}",
)

LOOK = """
import json, os, sys
own, other, target = sys.argv[1:]
with open(os.path.join(own, "new.txt"), "w") as handle:
    handle.write("kept")
print(json.dumps({
    "other": os.path.exists(other),
    "target": open(target).read(),
    "own": sorted(os.listdir(own)),
    "cwd": os.getcwd(),
    "pid": os.getpid(),
}))
"""


def test_kept_drops_missing_nested_and_unhidden_paths(tmp_path: Path) -> None:
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "c").mkdir()
    paths = [tmp_path / "a" / "b", tmp_path / "a", tmp_path / "c", tmp_path / "gone", "/usr"]
    assert sandbox.kept([*paths, tmp_path], hide=[str(tmp_path)]) == [
        str(tmp_path / "a"),
        str(tmp_path / "c"),
    ]


@needs_namespaces
def test_wrap_shows_only_kept_paths(tmp_path: Path) -> None:
    own, other = tmp_path / "own", tmp_path / "other"
    own.mkdir()
    other.mkdir()
    (other / "gold.txt").write_text("acme/ledger")
    target, replacement = own / "universe.jsonl", tmp_path / "masked.jsonl"
    target.write_text("gold")
    replacement.write_text("masked")
    keep = [own, sys.prefix, sys.base_prefix]
    command = [sys.executable, "-c", LOOK, str(own), str(other / "gold.txt"), str(target)]
    run = subprocess.run(
        sandbox.wrap(command, keep, over=[(replacement, target)], cwd=own),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert run.returncode == 0, run.stderr
    seen = json.loads(run.stdout)
    assert not seen["other"]
    assert seen["target"] == "masked" and target.read_text() == "gold"
    assert seen["own"] == ["new.txt", "universe.jsonl"] and seen["cwd"] == str(own)
    assert seen["pid"] < 100
    assert (own / "new.txt").read_text() == "kept"
