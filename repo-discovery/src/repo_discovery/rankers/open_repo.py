"""The ``open_repo`` tool for on-demand fetching (L5): a stdio MCP server, one per case.

The agent starts from ``candidates.txt`` alone and this tool is its only route to packet
content. ``open_repo(repo)`` places that candidate's packet read-only under
``packets/<owner>__<name>/`` in the workspace and returns the packet's file list. The
first open of a repository costs 1 against the budget; reopening one, naming a
repository that isn't a candidate (matched case-insensitively), and any call once the
budget is spent cost nothing.

The packet is the full one (L4) unless ``level`` names a cut: then only the files
:func:`repo_discovery.fetch_levels.kept_files` keeps at that level are placed (the README
at L2, the README and manifests at L3), without the cut's empty path list, and the tool's
description says so.

The server reads a JSON config written by the ranker: ``workspace``, ``budget``,
``log``, ``candidates`` (``owner/name`` to its packet directory) and optionally
``level``. It appends one JSON line per call to ``log``, with the files and bytes placed
by each open, and replays that log on start, so a resumed session (a new server process)
keeps the budget already spent. Run it as
``python -m repo_discovery.rankers.open_repo CONFIG``.
"""

from __future__ import annotations

import json
import shutil
import stat
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from repo_discovery.cases import repo_key
from repo_discovery.fetch_levels import FULL, kept_files
from repo_discovery.rankers.mcp_tool import serve

TOOL = "open_repo"
PACKETS = "packets"
PACKET_CONTENTS = {
    "L2": "its README only",
    "L3": "its README and package manifest files only",
    FULL: "README and docs, manifest files, API contract files, and a list of every file "
    "path in the repository",
}
DESCRIPTION = (
    "Open one candidate repository's evidence packet: {contents}. The packet is placed "
    "under `packets/<owner>__<name>/` in the workspace, where the read, grep and glob tools "
    "can see it, and its file list is returned. Each repository opened for the first time "
    "uses one unit of the budget; reopening one is free."
)
SCHEMA = {
    "type": "object",
    "properties": {
        "repo": {"type": "string", "description": "A candidate from candidates.txt, as owner/name."}
    },
    "required": ["repo"],
}


def _clean(name: str) -> str:
    name = name.strip().strip("`").removeprefix("https://github.com/").strip("/")
    if "/" not in name and "__" in name:
        name = name.replace("__", "/", 1)
    return name


@dataclass
class OpenRepo:
    workspace: Path
    budget: int
    candidates: dict[str, Path]
    log: Path | None = None
    level: str = FULL
    opened: list[str] = field(default_factory=list)
    name: ClassVar[str] = TOOL
    schema: ClassVar[dict] = SCHEMA

    def __post_init__(self) -> None:
        if self.level not in PACKET_CONTENTS:
            raise ValueError(f"level must be one of {sorted(PACKET_CONTENTS)}, got {self.level!r}")
        self.description = DESCRIPTION.format(contents=PACKET_CONTENTS[self.level])
        self.by_key = {repo_key(repo): repo for repo in self.candidates}
        if self.log is not None and self.log.is_file():
            for line in self.log.read_text().splitlines():
                entry = json.loads(line)
                if entry["outcome"] == "opened":
                    self.opened.append(entry["repo"])

    @classmethod
    def from_config(cls, path: Path) -> OpenRepo:
        config = json.loads(path.read_text())
        return cls(
            Path(config["workspace"]),
            int(config["budget"]),
            {repo: Path(packet) for repo, packet in config["candidates"].items()},
            Path(config["log"]) if config.get("log") else None,
            config.get("level", FULL),
        )

    def respond(self, arguments: dict) -> tuple[str, bool]:
        return self.call(arguments.get("repo"))

    def call(self, repo: object) -> tuple[str, bool]:
        """Open ``repo``; returns the tool's text and whether it is an error."""
        name = _clean(repo) if isinstance(repo, str) else ""
        found = self.by_key.get(repo_key(name)) if "/" in name else None
        placed: list[Path] = []
        if found is None:
            outcome, text = "not_candidate", f"{name or repo!r} is not in candidates.txt."
        elif found in self.opened:
            outcome, text = "reopened", self._listing(found, "Already open, at no cost.")
        elif len(self.opened) >= self.budget:
            outcome = "exhausted"
            text = (
                f"Budget exhausted: {len(self.opened)} of {self.budget} repositories are "
                "already open. Opened packets stay readable."
            )
        else:
            placed = self._place(found)
            self.opened.append(found)
            outcome = "opened"
            left = self.budget - len(self.opened)
            text = self._listing(found, f"Opened. Budget left: {left} of {self.budget}.")
        self._log(repo, found, outcome, placed)
        return text, outcome in ("not_candidate", "exhausted")

    def _target(self, repo: str) -> Path:
        owner, name = repo.split("/")
        return self.workspace / PACKETS / f"{owner}__{name}"

    def _place(self, repo: str) -> list[Path]:
        """Copy ``repo``'s packet at this level into the workspace; returns the files."""
        packets = self.workspace / PACKETS
        target = self._target(repo)
        packets.chmod(packets.stat().st_mode | stat.S_IWUSR)
        try:
            source = self.candidates[repo]
            target.mkdir()
            for rel in kept_files(source, self.level) if source.is_dir() else []:
                (target / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source / rel, target / rel)
            files = [p for p in target.rglob("*") if p.is_file()]
            for path in [*target.rglob("*"), target]:
                path.chmod(path.stat().st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
        finally:
            packets.chmod(packets.stat().st_mode & ~stat.S_IWUSR)
        return files

    def _listing(self, repo: str, header: str) -> str:
        target = self._target(repo)
        files = sorted(
            p.relative_to(self.workspace).as_posix() for p in target.rglob("*") if p.is_file()
        )
        return "\n".join([header, f"{len(files)} files:", *files])

    def _log(self, asked: object, repo: str | None, outcome: str, placed: list[Path]) -> None:
        if self.log is None:
            return
        entry = {
            "t": time.time(),
            "asked": asked,
            "repo": repo,
            "outcome": outcome,
            "opened": len(self.opened),
        }
        if outcome == "opened":
            entry.update(files=len(placed), bytes=sum(p.stat().st_size for p in placed))
        with self.log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")


from_config = OpenRepo.from_config

if __name__ == "__main__":
    serve(from_config(Path(sys.argv[1])))
