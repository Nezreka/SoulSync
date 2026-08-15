"""Read the file list out of a .torrent, without a new dependency.

#1149 asks for "prefer a release with verified FLAC files over a title-based
guess". We already fetch the .torrent server-side before handing it to the
client (#1139), so the real file list is in memory at exactly the moment we
still have the option not to enqueue it. It was simply never read.

Bencode is a four-token format and only the ``info`` dictionary is needed, so
this is a small decoder rather than a dependency. A self-hosted app pays for
every requirement in install friction, and pulling one in to read a list of
filenames is a poor trade.

CONTRACT: this NEVER raises. A torrent it cannot parse returns None, which
callers must read as "no evidence" and fall back to the title, not as
"rejected" — a decoder bug must not become an outage where nothing downloads.
"""

from __future__ import annotations

from typing import Any, List, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("quality.torrent_contents")

# A .torrent is metadata; anything this large is not one, and we would rather
# refuse to parse than chew through it.
MAX_TORRENT_BYTES = 16 * 1024 * 1024


def _decode(data: bytes, index: int) -> Tuple[Any, int]:
    """Decode one bencode value at ``index``; returns (value, next_index)."""
    kind = data[index:index + 1]

    if kind == b'i':                                    # i<int>e
        end = data.index(b'e', index)
        return int(data[index + 1:end]), end + 1

    if kind == b'l':                                    # l<values>e
        values: List[Any] = []
        index += 1
        while data[index:index + 1] != b'e':
            value, index = _decode(data, index)
            values.append(value)
        return values, index + 1

    if kind == b'd':                                    # d<key><value>...e
        out: dict = {}
        index += 1
        while data[index:index + 1] != b'e':
            key, index = _decode(data, index)
            value, index = _decode(data, index)
            if isinstance(key, bytes):
                out[key] = value
        return out, index + 1

    # <length>:<bytes>
    colon = data.index(b':', index)
    length = int(data[index:colon])
    start = colon + 1
    return data[start:start + length], start + length


def _text(raw: Any) -> str:
    if isinstance(raw, bytes):
        return raw.decode('utf-8', errors='replace')
    return str(raw or '')


def torrent_file_names(payload: Optional[bytes]) -> Optional[List[str]]:
    """Every file name inside a .torrent, or None when it cannot be read.

    Handles both layouts: a multi-file torrent lists ``info.files`` with each
    entry's ``path`` as a segment list, and a single-file torrent has only
    ``info.name``.

    None and [] mean different things. None is "could not read it" (fall back
    to the title); [] is "read it, and it declares no files", which is itself
    a reason not to trust the release.
    """
    if not payload or not isinstance(payload, (bytes, bytearray)):
        return None
    if len(payload) > MAX_TORRENT_BYTES:
        logger.debug("torrent payload too large to inspect (%d bytes)", len(payload))
        return None

    try:
        meta, _ = _decode(bytes(payload), 0)
    except Exception as e:
        logger.debug("could not decode .torrent: %s", e)
        return None

    if not isinstance(meta, dict):
        return None
    info = meta.get(b'info')
    if not isinstance(info, dict):
        return None

    files = info.get(b'files')
    if isinstance(files, list):
        names: List[str] = []
        for entry in files:
            if not isinstance(entry, dict):
                continue
            segments = entry.get(b'path')
            if isinstance(segments, list) and segments:
                names.append('/'.join(_text(s) for s in segments))
            elif entry.get(b'path.utf-8'):
                utf8 = entry.get(b'path.utf-8')
                if isinstance(utf8, list):
                    names.append('/'.join(_text(s) for s in utf8))
        return names

    name = info.get(b'name')
    if name:
        return [_text(name)]
    return []
