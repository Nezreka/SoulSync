"""Write .br and .gz copies of the web UI's text files, for the Docker image.

Run by path at image build (``python core/webui/precompress.py webui/static``),
so it imports nothing from the app. The server serves a copy only when the
browser takes that encoding and the copy is at least as new as the file
(core.http_compression), so a stale or missing copy is never served.

Max quality: this runs once per image, the server never pays for it.
"""

from __future__ import annotations

import gzip
import os
import sys

try:
    import brotli
except Exception:  # noqa: BLE001 - gzip copies still help
    brotli = None

EXTENSIONS = {".js", ".mjs", ".css", ".svg", ".json", ".html", ".htm", ".txt", ".map",
              ".xml", ".webmanifest"}
MIN_BYTES = 1024


def _write(path: str, data: bytes) -> None:
    # written now, so newer than its file: the server only takes a copy at
    # least as new as the file (an edited file outdates its copy)
    with open(path, "wb") as fh:
        fh.write(data)


def precompress(root: str) -> tuple:
    files = plain = gz_total = br_total = 0
    for folder, _dirs, names in os.walk(root):
        for name in names:
            if os.path.splitext(name)[1].lower() not in EXTENSIONS:
                continue
            path = os.path.join(folder, name)
            st = os.stat(path)
            if st.st_size < MIN_BYTES:
                continue
            with open(path, "rb") as fh:
                data = fh.read()
            files += 1
            plain += len(data)
            packed = gzip.compress(data, compresslevel=9, mtime=0)
            if len(packed) < len(data):
                _write(path + ".gz", packed)
                gz_total += len(packed)
            if brotli is not None:
                packed = brotli.compress(data, quality=11)
                if len(packed) < len(data):
                    _write(path + ".br", packed)
                    br_total += len(packed)
    return files, plain, gz_total, br_total


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "webui/static"
    n, raw, gz, br = precompress(target)
    print(f"precompressed {n} files: {raw:,} B plain, {gz:,} B gzip, {br:,} B brotli")
