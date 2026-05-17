"""Build a T2Tunes media plan for a track without downloading media.

This runner calls ``tools.t2tunes_probe.T2TunesClient`` and validates the
parts of a future download source that are safe to exercise live:

- search resolution
- album metadata lookup
- cover URL discovery + HEAD probe
- per-codec media lookup
- stream URL HEAD/range probe

It writes a JSON plan that a developer can inspect while building an
integration. It intentionally does not download or decrypt protected media.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

TOOL_DIR = Path(__file__).resolve().parent
if str(TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(TOOL_DIR))

from t2tunes_probe import T2TunesClient, T2TunesSearchItem  # noqa: E402


def _slug(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in value)
    parts = [part for part in cleaned.split("-") if part]
    return "-".join(parts[:12]) or "t2tunes"


def _item_score(item: T2TunesSearchItem, query: str, explicit: Optional[bool]) -> int:
    text = f"{item.artist_name} {item.title} {item.album_name}".lower()
    score = 0
    for token in query.lower().split():
        if token in text:
            score += 10
    if item.is_track:
        score += 20
    if explicit is True and "explicit" in text:
        score += 50
    if explicit is False and "clean" in text:
        score += 50
    if explicit is True and "clean" in text:
        score -= 40
    if explicit is False and "explicit" in text:
        score -= 40
    return score


def _choose_result(
    items: Iterable[T2TunesSearchItem],
    *,
    query: str,
    asin: Optional[str],
    explicit: Optional[bool],
) -> Optional[T2TunesSearchItem]:
    items = list(items)
    if asin:
        return next((item for item in items if item.asin == asin or item.album_asin == asin), None)
    if not items:
        return None
    return max(items, key=lambda item: _item_score(item, query, explicit))


def _first_album(metadata: Dict[str, Any]) -> Dict[str, Any]:
    albums = metadata.get("albumList")
    if isinstance(albums, list) and albums:
        album = albums[0]
        return album if isinstance(album, dict) else {}
    return {}


def _cover_url_from_metadata(metadata: Dict[str, Any]) -> str:
    album = _first_album(metadata)
    image = album.get("image")
    return image if isinstance(image, str) else ""


def _head_url(client: T2TunesClient, url: str) -> Dict[str, Any]:
    if not url:
        return {"ok": False, "reason": "missing url"}
    return client.probe_stream(url)


def build_plan(
    *,
    query: str,
    base_url: str,
    country: str,
    codecs: List[str],
    asin: Optional[str],
    explicit: Optional[bool],
    timeout: int,
    probe_urls: bool,
) -> Dict[str, Any]:
    client = T2TunesClient(base_url, country=country, timeout=timeout)
    status = client.status()
    search_items = client.search(query)
    selected = _choose_result(search_items, query=query, asin=asin, explicit=explicit)

    plan: Dict[str, Any] = {
        "base_url": base_url,
        "country": country,
        "query": query,
        "status": status,
        "result_count": len(search_items),
        "selected": selected.__dict__ if selected else None,
        "album_metadata": None,
        "cover": None,
        "formats": [],
        "notes": [
            "This plan validates API behavior only.",
            "It intentionally does not download or decrypt protected media.",
        ],
    }

    if not selected:
        return plan

    album_asin = selected.album_asin or selected.asin
    metadata = client.album_metadata(album_asin)
    cover_url = _cover_url_from_metadata(metadata)
    plan["album_metadata"] = {
        "asin": album_asin,
        "title": _first_album(metadata).get("title"),
        "track_count": _first_album(metadata).get("trackCount"),
        "image": cover_url,
        "label": _first_album(metadata).get("label"),
    }
    plan["cover"] = {
        "url": cover_url,
        "probe": _head_url(client, cover_url) if probe_urls else None,
    }

    for codec in codecs:
        format_client = T2TunesClient(base_url, country=country, codec=codec, timeout=timeout)
        streams = format_client.media_from_asin(selected.asin)
        entries = []
        for stream in streams:
            entry = stream.__dict__.copy()
            entry["stream_probe"] = _head_url(format_client, stream.stream_url) if probe_urls else None
            entries.append(entry)
        plan["formats"].append({
            "requested_codec": codec,
            "stream_count": len(entries),
            "streams": entries,
        })

    return plan


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a safe T2Tunes media plan for a track.")
    parser.add_argument("query", help="Search query, e.g. 'Kendrick Lamar Not Like Us'")
    parser.add_argument("--base-url", default="https://t2tunes.site")
    parser.add_argument("--country", default="US")
    parser.add_argument("--codec", action="append", dest="codecs", choices=("flac", "opus", "eac3"))
    parser.add_argument("--asin", help="Prefer a specific track or album ASIN from search results")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--explicit", action="store_true", help="Prefer explicit search results")
    group.add_argument("--clean", action="store_true", help="Prefer clean search results")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--no-probe", action="store_true", help="Do not HEAD-probe cover/stream URLs")
    parser.add_argument(
        "--output",
        help="Optional JSON output path. Defaults to ./testTEST/<query>-plan.json",
    )
    args = parser.parse_args()

    explicit = True if args.explicit else False if args.clean else None
    plan = build_plan(
        query=args.query,
        base_url=args.base_url,
        country=args.country,
        codecs=args.codecs or ["flac", "opus", "eac3"],
        asin=args.asin,
        explicit=explicit,
        timeout=args.timeout,
        probe_urls=not args.no_probe,
    )

    text = json.dumps(plan, indent=2, sort_keys=True)
    output_path = Path(args.output) if args.output else Path.cwd() / "testTEST" / f"{_slug(args.query)}-plan.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text + "\n", encoding="utf-8")
    plan["_written_to"] = str(output_path)
    text = json.dumps(plan, indent=2, sort_keys=True)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
