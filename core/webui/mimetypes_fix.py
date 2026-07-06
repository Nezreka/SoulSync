"""Force correct MIME types for web assets, independent of the host OS.

Python's ``mimetypes`` reads the OS type registry, and on Windows the ``.js``
extension is frequently mapped to ``text/plain`` (by installed software or an old
default). Flask's static serving then hands the React bundle out as text/plain —
and browsers REFUSE a ``<script type="module">`` served with a non-JS MIME type
(strict MIME checking per the HTML spec). The result: the module-loaded pages
(Import, Stats) render as a black screen while the classic-script shell keeps
working. Registering the types at startup overrides the bad registry lookup for
this process. (Bug #979)
"""

from __future__ import annotations

import mimetypes

# ext -> the type we always want served, regardless of the OS registry.
_WEB_TYPES = {
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".webmanifest": "application/manifest+json",
}


def ensure_web_mimetypes() -> None:
    """Register the correct MIME type for each web asset extension. ``add_type``
    overrides any earlier (e.g. registry-sourced) mapping, so this is safe to call
    once at startup and idempotent."""
    for ext, ctype in _WEB_TYPES.items():
        mimetypes.add_type(ctype, ext)


# MIME types a browser accepts for a <script type="module"> (per the HTML spec's
# module-script MIME check). Anything else → the module is refused → black screen.
_JS_MIME_TYPES = frozenset({
    "text/javascript", "application/javascript",
    "application/ecmascript", "text/ecmascript",
})


def corrected_script_content_type(path: str, current: str | None) -> str | None:
    """For a ``.js``/``.mjs`` response, return the Content-Type to serve — forcing a
    valid JavaScript type when the current one isn't one — else ``None`` to leave it
    unchanged.

    The registry approach above (``ensure_web_mimetypes``) is fragile across hosts:
    a Docker base image with a minimal ``/etc/mime.types`` (or an older Python) can
    still resolve ``.js`` to ``application/octet-stream``/``text/plain``, and the
    browser then REFUSES the ``type="module"`` React bundle → the Import/Stats pages
    render as a black screen (#979/#986). Enforcing the header at the HTTP layer is
    OS/registry-independent and bulletproof. Classic ``<script>`` shells are exempt
    from this MIME check, which is why only the module-loaded pages went black.
    """
    p = (path or "").lower()
    if not (p.endswith(".js") or p.endswith(".mjs")):
        return None
    base = (current or "").split(";")[0].strip().lower()
    if base in _JS_MIME_TYPES:
        return None
    return "text/javascript; charset=utf-8"
