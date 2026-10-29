"""The ``open_repo`` tool for on-demand fetching (L5): a stdio MCP server, one per case.

The agent starts from ``candidates.txt`` alone and this tool is its only route to packet
content. ``open_repo(repo)`` places that candidate's full packet read-only under
``packets/<owner>__<name>/`` in the workspace and returns the packet's file list. The
first open of a repository costs 1 against the budget; reopening one, naming a
repository that isn't a candidate (matched case-insensitively), and any call once the
budget is spent cost nothing.

The server reads a JSON config written by the ranker: ``workspace``, ``budget``,
``log`` and ``candidates`` (``owner/name`` to its packet directory). It appends one JSON
line per call to ``log`` and replays that log on start, so a resumed session (a new
server process) keeps the budget already spent. Run it as
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

from repo_discovery.cases import repo_key

SERVER = "repo-discovery"
TOOL = "open_repo"
PACKETS = "packets"
PROTOCOL = "2025-06-18"
DESCRIPTION = (
    "Open one candidate repository's evidence packet: README and docs, manifest files, API "
    "contract files, and a list of every file path in the repository. The packet is placed "
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
    opened: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
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
        )

    def call(self, repo: object) -> tuple[str, bool]:
        """Open ``repo``; returns the tool's text and whether it is an error."""
        name = _clean(repo) if isinstance(repo, str) else ""
        found = self.by_key.get(repo_key(name)) if "/" in name else None
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
            self._place(found)
            self.opened.append(found)
            outcome = "opened"
            left = self.budget - len(self.opened)
            text = self._listing(found, f"Opened. Budget left: {left} of {self.budget}.")
        self._log(repo, found, outcome)
        return text, outcome in ("not_candidate", "exhausted")

    def _target(self, repo: str) -> Path:
        owner, name = repo.split("/")
        return self.workspace / PACKETS / f"{owner}__{name}"

    def _place(self, repo: str) -> None:
        packets = self.workspace / PACKETS
        target = self._target(repo)
        packets.chmod(packets.stat().st_mode | stat.S_IWUSR)
        try:
            source = self.candidates[repo]
            if source.is_dir():
                shutil.copytree(source, target, symlinks=False)
            else:
                target.mkdir()
            for path in [*target.rglob("*"), target]:
                path.chmod(path.stat().st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
        finally:
            packets.chmod(packets.stat().st_mode & ~stat.S_IWUSR)

    def _listing(self, repo: str, header: str) -> str:
        target = self._target(repo)
        files = sorted(
            p.relative_to(self.workspace).as_posix() for p in target.rglob("*") if p.is_file()
        )
        return "\n".join([header, f"{len(files)} files:", *files])

    def _log(self, asked: object, repo: str | None, outcome: str) -> None:
        if self.log is None:
            return
        entry = {
            "t": time.time(),
            "asked": asked,
            "repo": repo,
            "outcome": outcome,
            "opened": len(self.opened),
        }
        with self.log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")


def handle(tool: OpenRepo, message: dict) -> dict | None:
    """The JSON-RPC response to ``message``, or None for a notification."""
    method, params, msg_id = message.get("method"), message.get("params") or {}, message.get("id")
    if msg_id is None:
        return None
    if method == "initialize":
        result = {
            "protocolVersion": params.get("protocolVersion") or PROTOCOL,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER, "version": "1"},
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": [{"name": TOOL, "description": DESCRIPTION, "inputSchema": SCHEMA}]}
    elif method == "tools/call" and params.get("name") == TOOL:
        text, error = tool.call((params.get("arguments") or {}).get("repo"))
        result = {"content": [{"type": "text", "text": text}], "isError": error}
    else:
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": -32601, "message": f"unknown method {method}"},
        }
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def serve(tool: OpenRepo, stdin=sys.stdin, stdout=sys.stdout) -> None:
    for line in stdin:
        try:
            message = json.loads(line)
        except ValueError:
            continue
        response = handle(tool, message) if isinstance(message, dict) else None
        if response is not None:
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()


if __name__ == "__main__":
    serve(OpenRepo.from_config(Path(sys.argv[1])))
