"""A stdio MCP server for one tool, as the agent ranker (V5) registers them per case.

The CLI starts the server from the ``mcp.json`` in the case's ``HOME`` and speaks
newline-delimited JSON-RPC to it: ``initialize``, ``ping``, ``tools/list`` and
``tools/call``. A tool has a ``name``, a ``description``, an input ``schema`` and
``respond(arguments)``, which returns the result text and whether it is an error.
"""

from __future__ import annotations

import json
import sys
from typing import Protocol

SERVER = "repo-discovery"
PROTOCOL = "2025-06-18"


class Tool(Protocol):
    name: str
    description: str
    schema: dict

    def respond(self, arguments: dict) -> tuple[str, bool]: ...


def handle(tool: Tool, message: dict) -> dict | None:
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
        result = {
            "tools": [
                {"name": tool.name, "description": tool.description, "inputSchema": tool.schema}
            ]
        }
    elif method == "tools/call" and params.get("name") == tool.name:
        arguments = params.get("arguments")
        text, error = tool.respond(arguments if isinstance(arguments, dict) else {})
        result = {"content": [{"type": "text", "text": text}], "isError": error}
    else:
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": -32601, "message": f"unknown method {method}"},
        }
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def serve(tool: Tool, stdin=sys.stdin, stdout=sys.stdout) -> None:
    for line in stdin:
        try:
            message = json.loads(line)
        except ValueError:
            continue
        response = handle(tool, message) if isinstance(message, dict) else None
        if response is not None:
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()
