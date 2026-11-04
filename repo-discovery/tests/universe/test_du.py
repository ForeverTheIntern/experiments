from __future__ import annotations

import os
from pathlib import Path

from repo_discovery.universe.pipeline import _du_walk


def test_du_walk_counts_hard_links_once(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "one.txt").write_text("x" * 5000)
    os.link(tmp_path / "a" / "one.txt", tmp_path / "linked.txt")
    (tmp_path / "two.txt").write_text("yy")
    found = _du_walk(tmp_path)
    assert found["files"] == 2
    assert found["apparent_bytes"] == 5002 + (tmp_path / "a").lstat().st_size
    assert found["disk_bytes"] >= 5002
