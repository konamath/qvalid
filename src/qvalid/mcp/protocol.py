"""JSON-RPC over stdio, written by hand and as a pure function. See D075.

Hand written for the same reason ``report/svg.py`` draws its own charts and
``ui/server.py`` uses the standard library: the surface is small, the dependency
would be permanent, and the promise that nothing here reaches the network is
worth more when it is verifiable by reading the file.

**Testable without a transport**, which is the whole shape of this module.
:func:`handle` takes the bytes of one request and returns the bytes of one
response, so every test drives the exact thing a client sends. D069 is the
reason that mattered enough to design for: the browser path had been broken from
the first day because every test called the page functions with a dictionary
already built, and the one layer between the wire and the handler was the one
layer nothing exercised. This module is that layer, and it is tested first.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from qvalid.exceptions import QvalError
from qvalid.mcp.tools import call_tool, tool_catalogue

__all__ = ["PROTOCOL_VERSION", "handle"]

PROTOCOL_VERSION = "2024-11-05"
"""The revision this speaks, echoed back on initialise so a client can refuse."""

_PARSE_ERROR = -32700
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_INTERNAL_ERROR = -32603


def _reply(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _failure(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def handle(raw: bytes, cache_root: str | Path) -> bytes | None:
    """Answer one request.

    Parameters
    ----------
    raw : bytes
        One JSON-RPC message, without a trailing newline.
    cache_root : str or Path
        The cache the tools read.

    Returns
    -------
    bytes or None
        The response, or ``None`` for a notification. A notification is a
        message with no ``id``, and the specification says it gets no answer;
        replying to one is the mistake that makes a client hang waiting for a
        response it will then fail to match.

    Notes
    -----
    A tool that raises a typed error becomes a result carrying ``isError``,
    not a protocol level failure. The distinction matters: a protocol failure
    says the request was malformed, while a refused tool call says the request
    was understood and the answer is no. Collapsing the two would tell an agent
    its query was wrong when the cache was simply empty.
    """
    try:
        message = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return json.dumps(_failure(None, _PARSE_ERROR, f"not JSON: {exc}")).encode()
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return json.dumps(_failure(None, _INVALID_REQUEST, "not a JSON-RPC 2.0 object")).encode()

    request_id = message.get("id")
    method = message.get("method")
    params = message.get("params") or {}
    if request_id is None:
        return None

    if method == "initialize":
        return json.dumps(
            _reply(
                request_id,
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "quantify-cache", "version": _version()},
                },
            )
        ).encode()

    if method == "tools/list":
        return json.dumps(_reply(request_id, {"tools": tool_catalogue()})).encode()

    if method == "tools/call":
        name = str(params.get("name", ""))
        arguments = params.get("arguments") or {}
        try:
            payload = call_tool(cache_root, name, arguments)
        except QvalError as exc:
            return json.dumps(
                _reply(
                    request_id,
                    {
                        "isError": True,
                        "content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}],
                    },
                )
            ).encode()
        except Exception as exc:  # pragma: no cover - a bug here, not a refusal
            return json.dumps(_failure(request_id, _INTERNAL_ERROR, repr(exc))).encode()
        return json.dumps(
            _reply(
                request_id,
                {
                    "isError": False,
                    "content": [{"type": "text", "text": json.dumps(payload)}],
                    "structuredContent": payload
                    if isinstance(payload, dict)
                    else {"items": payload},
                },
            )
        ).encode()

    return json.dumps(_failure(request_id, _METHOD_NOT_FOUND, f"no method {method!r}")).encode()


def _version() -> str:
    from qvalid import __version__

    return __version__
