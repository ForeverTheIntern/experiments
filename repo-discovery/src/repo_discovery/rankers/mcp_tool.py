"""A stdio MCP server for the agent ranker's (V5) tools, as it registers them per case.

The CLI starts the server from the ``mcp.json`` in the case's ``HOME`` and speaks
newline-delimited JSON-RPC to it: ``initialize``, ``ping``, ``tools/list`` and
``tools/call``. A tool has a ``name``, a ``description``, an input ``schema`` and
``respond(arguments)``, which returns the result text and whether it is an error.

Each tool module runs its own one-tool server (``python -m <module> CONFIG``). To serve
several tools from one server, run ``python -m repo_discovery.rankers.mcp_tool
MODULE=CONFIG...``, where each module's ``from_config(path)`` builds its tool.
"""

from __future__ import annotations

import importlib
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

SERVER = "repo-discovery"
PROTOCOL = "2025-06-18"


class Tool(Protocol):
    name: str
    description: str
    schema: dict

    def respond(self, arguments: dict) -> tuple[str, bool]: ...


def handle(tools: Tool | Sequence[Tool], message: dict) -> dict | None:
    """The JSON-RPC response to ``message``, or None for a notification."""
    by_name = {t.name: t for t in ([tools] if hasattr(tools, "respond") else tools)}
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
        result = {
            "tools": [
                {"name": t.name, "description": t.description, "inputSchema": t.schema}
                for t in by_name.values()
            ]
        }
    elif method == "tools/call" and params.get("name") in by_name:
        arguments = params.get("arguments")
        tool = by_name[params["name"]]
        text, error = tool.respond(arguments if isinstance(arguments, dict) else {})
        result = {"content": [{"type": "text", "text": text}], "isError": error}
    else:
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": -32601, "message": f"unknown method {method}"},
        }
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def serve(*tools: Tool, stdin=sys.stdin, stdout=sys.stdout) -> None:
    for line in stdin:
        try:
            message = json.loads(line)
        except ValueError:
            continue
        response = handle(tools, message) if isinstance(message, dict) else None
        if response is not None:
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()


def load(specs: Sequence[str]) -> list[Tool]:
    """The tools named by ``MODULE=CONFIG`` specs, in order."""
    tools = []
    for spec in specs:
        module, _, config = spec.partition("=")
        tools.append(importlib.import_module(module).from_config(Path(config)))
    return tools


if __name__ == "__main__":
    serve(*load(sys.argv[1:]))
