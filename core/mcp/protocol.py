"""mcp over streamable http, the json-rpc part. no flask in here.

two eras of client are out there:
- 2026-07-28: no handshake. every request carries its version in
  params._meta and the MCP-Protocol-Version header. server/discover is the
  optional "who are you".
- 2025-03-26 to 2025-11-25: initialize first, then requests with the header.

we answer both, one json response per post, no sessions, no sse. a
notification gets 202 and nothing else.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional, Tuple

MODERN = "2026-07-28"
LEGACY = ("2025-11-25", "2025-06-18", "2025-03-26")
SUPPORTED = (MODERN,) + LEGACY
# the header was new in 2025-06-18, so a request without one is older
NO_HEADER_VERSION = "2025-03-26"

META_VERSION = "io.modelcontextprotocol/protocolVersion"
META_SERVER = "io.modelcontextprotocol/serverInfo"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
HEADER_MISMATCH = -32020
UNSUPPORTED_VERSION = -32022

# a tool result bigger than this gets cut, models choke on huge blobs
MAX_TEXT = 100_000

# 2026-07-28 lists say how long a client may cache them. the tool list
# depends on the key's scope, so it's private to that caller
CACHE = {"tools/list": 300_000, "server/discover": 3_600_000}

Reply = Tuple[int, Optional[Dict[str, Any]]]


def _error(rid, code, message, data=None) -> Dict[str, Any]:
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": rid, "error": err}


def tool_text(payload: Any) -> str:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, default=str)
    if len(text) > MAX_TEXT:
        text = text[:MAX_TEXT] + f"\n... cut at {MAX_TEXT} characters, ask for less (a smaller limit or a page)"
    return text


def tool_result(payload: Any, *, is_error: bool = False) -> Dict[str, Any]:
    out = {"content": [{"type": "text", "text": tool_text(payload)}], "isError": bool(is_error)}
    if not is_error and isinstance(payload, dict) and len(tool_text(payload)) < MAX_TEXT:
        out["structuredContent"] = payload
    return out


class Server:
    """tools(): the tools this caller may see. call(tool, args): the result
    dict from tool_result. both come from the flask side."""

    def __init__(self, *, name: str, version: str, instructions: str,
                 tools: Callable[[], List[Any]], call: Callable[[Any, Dict[str, Any]], Dict[str, Any]]):
        self.info = {"name": name, "title": "SoulSync", "version": version}
        self.instructions = instructions
        self._tools = tools
        self._call = call

    def handle(self, raw: bytes, headers) -> Reply:
        try:
            msg = json.loads(raw or b"")
        except (ValueError, UnicodeDecodeError):
            return 400, _error(None, PARSE_ERROR, "Parse error")
        if not isinstance(msg, dict):
            # batches were never part of streamable http, one message per post
            return 400, _error(None, INVALID_REQUEST, "send one JSON-RPC message per request")
        if msg.get("jsonrpc") != "2.0":
            return 400, _error(msg.get("id"), INVALID_REQUEST, "jsonrpc must be 2.0")
        method = msg.get("method")
        if not isinstance(method, str):
            if "result" in msg or "error" in msg:
                return 202, None  # a client answering something, we never ask
            return 400, _error(msg.get("id"), INVALID_REQUEST, "method is required")
        if "id" not in msg:
            return 202, None  # notifications/initialized and friends
        rid = msg["id"]
        params = msg.get("params") or {}
        if not isinstance(params, dict):
            return 400, _error(rid, INVALID_PARAMS, "params must be an object")

        if method == "initialize":
            return 200, self._initialize(rid, params)

        meta = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
        body_version = meta.get(META_VERSION)
        header_version = (headers.get("MCP-Protocol-Version") or "").strip() or None
        if body_version and header_version and body_version != header_version:
            return 400, _error(rid, HEADER_MISMATCH, "MCP-Protocol-Version header doesn't match _meta")
        version = body_version or header_version or NO_HEADER_VERSION
        if version not in SUPPORTED:
            data = {"supported": list(SUPPORTED), "requested": version}
            code = UNSUPPORTED_VERSION if body_version else INVALID_PARAMS
            return 400, _error(rid, code, "Unsupported protocol version", data)
        modern = version == MODERN
        named = (headers.get("Mcp-Method") or "").strip()
        if modern and named and named != method:
            return 400, _error(rid, HEADER_MISMATCH, "Mcp-Method header doesn't match the body")

        try:
            result = self._dispatch(method, params, modern)
        except _RpcError as e:
            # legacy clients read json-rpc errors off a 200, modern ones expect real codes
            status = (404 if e.code == METHOD_NOT_FOUND else 400) if modern else 200
            return status, _error(rid, e.code, e.message)
        if modern:
            result = {"resultType": "complete", **result,
                      "_meta": {**(result.get("_meta") or {}), META_SERVER: self.info}}
            if method in CACHE:
                result.update(ttlMs=CACHE[method], cacheScope="private")
        return 200, {"jsonrpc": "2.0", "id": rid, "result": result}

    def _initialize(self, rid, params) -> Dict[str, Any]:
        asked = params.get("protocolVersion")
        version = asked if asked in LEGACY else LEGACY[0]
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": self.info,
            "instructions": self.instructions,
        }}

    def _dispatch(self, method, params, modern) -> Dict[str, Any]:
        if method == "ping":
            return {}
        if method == "server/discover":
            return {"supportedVersions": list(SUPPORTED), "capabilities": {"tools": {"listChanged": False}},
                    "instructions": self.instructions}
        if method == "tools/list":
            return {"tools": [t.listing() for t in self._tools()]}
        if method == "tools/call":
            name = params.get("name")
            tool = next((t for t in self._tools() if t.name == name), None)
            if tool is None:
                raise _RpcError(INVALID_PARAMS, f"Unknown tool: {name}")
            args = params.get("arguments")
            return self._call(tool, {} if args is None else args)
        raise _RpcError(METHOD_NOT_FOUND, f"Method not found: {method}")


class _RpcError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message
