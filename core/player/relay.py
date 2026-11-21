"""relay a media server's stream to a client, Range both ways, so the
client can seek. the upstream url carries the server's token: it is
fetched here and never sent on."""

from __future__ import annotations

from typing import Optional

PASS_HEADERS = ("content-type", "content-length", "content-range", "accept-ranges", "last-modified")
CHUNK = 256 * 1024


def relay(url: str, range_header: Optional[str], mime: str):
    """a flask Response streaming url's bytes, or (json, status) when the
    server won't answer"""
    import requests
    from flask import Response, jsonify, stream_with_context

    head = {"Range": range_header} if range_header else {}
    try:
        up = requests.get(url, headers=head, stream=True, timeout=(10, 60))
    except requests.RequestException:
        return jsonify({"success": False, "error": "Your media server didn't answer"}), 502
    if up.status_code >= 400:
        up.close()
        return jsonify({"success": False,
                        "error": f"Your media server refused the stream (HTTP {up.status_code})"}), 502
    headers = {k: v for k, v in up.headers.items() if k.lower() in PASS_HEADERS}
    headers.setdefault("Accept-Ranges", "bytes")
    headers.setdefault("Content-Type", mime)

    def body():
        try:
            for chunk in up.iter_content(chunk_size=CHUNK):
                if chunk:
                    yield chunk
        finally:
            up.close()

    return Response(stream_with_context(body()), status=up.status_code, headers=headers)
