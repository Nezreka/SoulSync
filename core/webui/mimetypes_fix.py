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
