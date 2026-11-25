"""the subsonic-response envelope, as json, jsonp or xml.

a payload is plain python: dicts, lists, scalars. the one rule that tells
the two formats apart lives here:

* a scalar in a dict is a json field and an xml attribute
* a dict is a json object and an xml child element
* a list is a json array and one xml element per item, named by its key
* ``Text("...")`` is a json string and an xml child element with that
  text (artistInfo's biography, albumInfo's notes)
* a dict's ``"value"`` key is its xml text (a genre's name, a lyric line)

None is left out of both. booleans are true/false in both.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional, Tuple
from xml.sax.saxutils import escape, quoteattr

API_VERSION = "1.16.1"
SERVER_TYPE = "soulsync"
XMLNS = "http://subsonic.org/restapi"

# error codes (subsonic + opensubsonic)
GENERIC = 0
MISSING_PARAM = 10
CLIENT_TOO_OLD = 20
WRONG_CREDENTIALS = 40
AUTH_NOT_SUPPORTED = 42
CONFLICTING_AUTH = 43
INVALID_API_KEY = 44
NOT_AUTHORIZED = 50
NOT_FOUND = 70

_server_version = "unknown"


def set_server_version(version: str) -> None:
    global _server_version
    _server_version = str(version or "unknown").split("+")[0]


class Text(str):
    """a string that is an xml child element, not an attribute."""


class SubsonicError(Exception):
    """raised anywhere under a method: becomes a failed response."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = int(code)
        self.message = message


def missing(name: str) -> SubsonicError:
    return SubsonicError(MISSING_PARAM, "Required parameter is missing: %s" % name)


def not_found(what: str = "Item") -> SubsonicError:
    return SubsonicError(NOT_FOUND, "%s not found" % what)


def envelope(status: str = "ok") -> Dict[str, Any]:
    return {"status": status, "version": API_VERSION, "type": SERVER_TYPE,
            "serverVersion": _server_version, "openSubsonic": True}


def ok(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    body = envelope("ok")
    if payload:
        body.update(payload)
    return body


def failed(code: int, message: str) -> Dict[str, Any]:
    body = envelope("failed")
    body["error"] = {"code": int(code), "message": message}
    return body


# ── json ─────────────────────────────────────────────────────────────────

def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items() if v is not None}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value if v is not None]
    if isinstance(value, Text):
        return str(value)
    return value


def to_json(body: Dict[str, Any]) -> str:
    return json.dumps({"subsonic-response": _clean(body)}, ensure_ascii=False, separators=(",", ":"))


_CALLBACK = re.compile(r"^[A-Za-z_$][\w$.]{0,63}$")


def safe_callback(name: str) -> Optional[str]:
    """a jsonp callback that can only be a function name."""
    name = (name or "").strip()
    return name if _CALLBACK.match(name) else None


# ── xml ──────────────────────────────────────────────────────────────────

def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _element(name: str, value: Any, out: list) -> None:
    if value is None:
        return
    if isinstance(value, dict):
        attrs, children, text = [], [], None
        for k, v in value.items():
            if v is None:
                continue
            if k == "value" and not isinstance(v, (dict, list, tuple)):
                text = _scalar(v)
            elif isinstance(v, (dict, list, tuple)) or isinstance(v, Text):
                children.append((k, v))
            else:
                attrs.append(" %s=%s" % (k, quoteattr(_scalar(v))))
        out.append("<%s%s" % (name, "".join(attrs)))
        if not children and text is None:
            out.append("/>")
            return
        out.append(">")
        if text is not None:
            out.append(escape(text))
        for k, v in children:
            _element(k, v, out)
        out.append("</%s>" % name)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _element(name, item, out)
        return
    out.append("<%s>%s</%s>" % (name, escape(_scalar(value)), name))


def to_xml(body: Dict[str, Any]) -> str:
    out = ['<?xml version="1.0" encoding="UTF-8"?>']
    attrs = {"xmlns": XMLNS}
    children = {}
    for k, v in body.items():
        if v is None:
            continue
        if isinstance(v, (dict, list, tuple, Text)):
            children[k] = v
        else:
            attrs[k] = v
    out.append("<subsonic-response%s" % "".join(" %s=%s" % (k, quoteattr(_scalar(v))) for k, v in attrs.items()))
    if not children:
        out.append("/>")
        return "".join(out)
    out.append(">")
    for k, v in children.items():
        _element(k, v, out)
    out.append("</subsonic-response>")
    return "".join(out)


# ── the choice ───────────────────────────────────────────────────────────

def render(body: Dict[str, Any], fmt: str, callback: str = "") -> Tuple[str, str]:
    """(text, mimetype) for f=json|jsonp|xml (xml when unknown, as the spec
    says)."""
    fmt = (fmt or "xml").lower()
    if fmt == "json":
        return to_json(body), "application/json"
    if fmt == "jsonp":
        cb = safe_callback(callback)
        if cb:
            return "%s(%s);" % (cb, to_json(body)), "application/javascript"
        return to_json(body), "application/json"
    return to_xml(body), "text/xml"


__all__ = ["API_VERSION", "SERVER_TYPE", "Text", "SubsonicError", "missing", "not_found", "ok", "failed",
           "envelope", "render", "to_json", "to_xml", "safe_callback", "set_server_version",
           "GENERIC", "MISSING_PARAM", "CLIENT_TOO_OLD", "WRONG_CREDENTIALS", "AUTH_NOT_SUPPORTED",
           "CONFLICTING_AUTH", "INVALID_API_KEY", "NOT_AUTHORIZED", "NOT_FOUND"]
