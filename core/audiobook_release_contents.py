"""What is actually inside a release, before you download it.

A release row shows a name and a size, which is not enough to decide with. Two
"Project Hail Mary" torrents of about the same size can be one m4b, or 87 mp3
chapters, or the book plus a PDF and somebody's whole discography. The listener
picking a release is the only one who can tell those apart, and they were being
asked to do it blind.

Where the file list comes from, per protocol:

  soulseek  free. The search result already carries every file and its size,
            because that is how a peer's folder is browsed in the first place.
            No network at all.
  torrent   the .torrent itself. SoulSync already fetches it server-side before
            handing it to the client, and core/quality/torrent_contents.py
            already decodes bencode for the music side's file verification, so
            this is two existing pieces joined up.
  usenet    the NZB, which is XML listing one <file> per part with its segment
            byte counts.

A magnet with no .torrent URL cannot answer: the file list lives in the swarm
and getting it means joining, which is a download. That case says so rather
than pretending the release is empty.

CONTRACT: never raises. Anything unreadable comes back as ``files: []`` with a
``note`` explaining why, because a preview failing must never stop somebody
grabbing the release anyway.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional
from xml.etree import ElementTree

from utils.logging_config import get_logger

logger = get_logger("audiobook_release_contents")

# An NZB is text. Anything this size is not one.
MAX_NZB_BYTES = 8 * 1024 * 1024

FETCH_TIMEOUT = 25

_AUDIO_SUFFIXES = (".mp3", ".m4a", ".m4b", ".flac", ".ogg", ".opus", ".wav", ".aac", ".wma")


def _field(release: Any, name: str) -> Any:
    if isinstance(release, dict):
        return release.get(name)
    return getattr(release, name, None)


def is_audio(name: str) -> bool:
    return str(name or "").lower().endswith(_AUDIO_SUFFIXES)


def summarise(files: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Counts and totals for a file list, so the UI does no arithmetic.

    Audio and extras are counted apart because they answer different
    questions: how many chapters am I getting, and how much of this size is
    not the book.
    """
    audio = [f for f in files if is_audio(f.get("name", ""))]
    extras = [f for f in files if not is_audio(f.get("name", ""))]
    formats: List[str] = []
    for entry in audio:
        suffix = str(entry.get("name", "")).rsplit(".", 1)[-1].lower()
        if suffix and suffix not in formats:
            formats.append(suffix)
    return {
        "total": len(files),
        "audio_count": len(audio),
        "extra_count": len(extras),
        "audio_bytes": sum(int(f.get("size") or 0) for f in audio),
        "total_bytes": sum(int(f.get("size") or 0) for f in files),
        "formats": sorted(formats),
    }


# ---------------------------------------------------------------------------
# Per protocol
# ---------------------------------------------------------------------------

def _from_soulseek(release: Any) -> Optional[List[Dict[str, Any]]]:
    payload = _field(release, "soulseek") or {}
    entries = payload.get("files") if isinstance(payload, dict) else None
    if not entries:
        return None
    files = []
    for entry in entries:
        name = str(entry.get("filename") or "")
        # Peers share Windows paths; only the leaf is meaningful in a preview.
        leaf = re.split(r"[\\/]+", name)[-1] if name else ""
        if leaf:
            files.append({"name": leaf, "size": int(entry.get("size") or 0)})
    return files


def _from_torrent(release: Any) -> tuple:
    """(files, note). Returns ([], note) when it genuinely cannot be read."""
    url = str(_field(release, "download_url") or "")
    magnet = str(_field(release, "magnet_uri") or "")

    if not url.lower().startswith(("http://", "https://")):
        if magnet:
            return [], ("This release is magnet-only. Its file list lives in the "
                        "swarm, so it cannot be read without starting the download.")
        return [], "That release has no download link to inspect."

    try:
        from core.torrent_clients.base import fetch_torrent_payload
        payload, redirect_magnet = fetch_torrent_payload(url, timeout=FETCH_TIMEOUT)
    except Exception as exc:                                # noqa: BLE001
        logger.debug("Could not fetch the torrent for a preview: %s", exc)
        return [], "Could not reach the indexer to read this release."

    if payload is None:
        if redirect_magnet:
            return [], ("This release is magnet-only. Its file list lives in the "
                        "swarm, so it cannot be read without starting the download.")
        return [], "Could not reach the indexer to read this release."

    from core.quality.torrent_contents import torrent_file_entries
    entries = torrent_file_entries(payload)
    if entries is None:
        return [], "That .torrent could not be read."
    return [{"name": name, "size": size} for name, size in entries], ""


def _from_nzb(release: Any) -> tuple:
    """(files, note) for a usenet release, read out of the NZB XML."""
    url = str(_field(release, "download_url") or "")
    if not url.lower().startswith(("http://", "https://")):
        return [], "That release has no NZB link to inspect."

    try:
        import requests
        response = requests.get(url, timeout=FETCH_TIMEOUT)
        if response.status_code != 200:
            return [], "Could not reach the indexer to read this release."
        raw = response.content[:MAX_NZB_BYTES]
    except Exception as exc:                                # noqa: BLE001
        logger.debug("Could not fetch the NZB for a preview: %s", exc)
        return [], "Could not reach the indexer to read this release."

    return _parse_nzb(raw)


def _parse_nzb(raw: bytes) -> tuple:
    """Files and sizes out of NZB XML.

    An NZB has one <file> per posted part, each carrying a subject line and a
    list of segments with byte counts. The real filename is quoted inside the
    subject, which is a convention rather than a rule, so a subject that does
    not follow it falls back to the whole line rather than being dropped.
    """
    try:
        root = ElementTree.fromstring(raw)
    except Exception as exc:                                # noqa: BLE001
        logger.debug("Could not parse the NZB: %s", exc)
        return [], "That NZB could not be read."

    files: List[Dict[str, Any]] = []
    # NZB uses a namespace; match on the tag's local name so both forms work.
    for node in root.iter():
        if not node.tag.endswith("file"):
            continue
        subject = str(node.attrib.get("subject") or "").strip()
        if not subject:
            continue

        quoted = re.search(r'"([^"]+)"', subject)
        name = quoted.group(1) if quoted else subject

        size = 0
        for child in node.iter():
            if child.tag.endswith("segment"):
                try:
                    size += int(child.attrib.get("bytes") or 0)
                except (TypeError, ValueError):
                    continue
        files.append({"name": name, "size": size})

    if not files:
        return [], "That NZB lists no files."
    return files, ""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def contents_for(release: Any) -> Dict[str, Any]:
    """What a release contains: ``{files, summary, note, protocol}``.

    ``note`` is filled when the list could not be read and explains why in
    words the person choosing a release can act on. An empty list with an empty
    note means the release genuinely declares no files.
    """
    protocol = str(_field(release, "protocol") or "").lower()

    if protocol == "soulseek":
        files = _from_soulseek(release)
        if files is None:
            return {"protocol": protocol, "files": [], "summary": summarise([]),
                    "note": "That Soulseek result carried no file list."}
        note = ""
    elif protocol == "torrent":
        files, note = _from_torrent(release)
    elif protocol == "usenet":
        files, note = _from_nzb(release)
    else:
        return {"protocol": protocol, "files": [], "summary": summarise([]),
                "note": f"Nothing can be read from a {protocol or 'unknown'} release."}

    # Natural order, so chapter 2 sits before chapter 10 the way it will on
    # disk rather than the way a plain string sort would put it.
    from core.audiobook_organizer import natural_key
    files = sorted(files, key=lambda f: natural_key(str(f.get("name") or "")))

    return {"protocol": protocol, "files": files, "summary": summarise(files), "note": note}
