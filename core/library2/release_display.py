"""Which of an artist's album rows are the same release, for display.

A discography lists a release once. The catalogue can hold it more than once:
one track of an album filed in another folder is its own album on the media
server (bbno$'s self-titled album stood as "1/22" beside the complete
"21/21"), and a provider's entry for a release sits beside the owned copy
until the duplicate repair folds it. The artist page's Discovery view drew a
card for every row, so one album showed three times.

Nothing is merged here. The rows stay as they are, My Library still shows each
owned copy, and this only says which row stands for the release.

Two rows are one release when they share the title key and the single/release
bucket, and then:

  - both know their MusicBrainz release group: the groups are the same. Two
    albums called "Weezer" are two groups, and stay two.
  - otherwise they share a trusted provider release id, or their years do not
    disagree. A same-titled album from another year is another album.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _bucket(album_type: Any) -> str:
    return "single" if str(album_type or "").lower() == "single" else "release"


def _year(entry: Dict[str, Any]) -> Optional[int]:
    for value in (entry.get("release_date"), entry.get("year")):
        text = str(value or "").strip()[:4]
        if len(text) == 4 and text.isdigit():
            return int(text)
    return None


def same_release(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """`a` and `b` carry: title_key, bucket, group (release group id or ''),
    ids (trusted provider release ids by source) and year."""
    if not a["title_key"] or a["title_key"] != b["title_key"] or a["bucket"] != b["bucket"]:
        return False
    if a["group"] and b["group"]:
        return a["group"] == b["group"]
    if any(b["ids"].get(source) == value for source, value in a["ids"].items()):
        return True
    return a["year"] is None or b["year"] is None or a["year"] == b["year"]


def _lead_key(entry: Dict[str, Any]) -> tuple:
    present = int(entry.get("tracks_present") or 0)
    total = int(entry.get("track_count") or 0)
    return (
        -present,                                        # the copy with the most of it
        0 if entry.get("origin") != "discography" else 1,  # a real copy over an entry
        -(present / total) if total else 0,              # then the more complete
        0 if entry.get("monitored") else 1,
        int(entry["id"]),
    )


def stamp_display_groups(entries: List[Dict[str, Any]], raw_rows: Dict[int, Any]) -> None:
    """Set on every entry:

    ``release_lead_id``  the row that stands for its release (itself, mostly)
    ``release_copies``   how many rows of that release have files

    ``raw_rows`` maps an album id to its catalogue row, for the ids the page
    payload does not carry.
    """
    from core.library2.dedup_repair import _release_ids
    from core.library2.importer import release_title_key

    facts: Dict[int, Dict[str, Any]] = {}
    for entry in entries:
        album_id = int(entry["id"])
        raw = raw_rows.get(album_id)
        try:
            group = str(raw["musicbrainz_release_group_id"] or "").strip() if raw is not None else ""
        except (IndexError, KeyError):
            group = ""
        facts[album_id] = {
            "title_key": release_title_key(entry.get("title") or ""),
            "bucket": _bucket(entry.get("album_type")),
            "group": group,
            "ids": _release_ids(raw) if raw is not None else {},
            "year": _year(entry),
        }

    # same title key first: an artist page holds a few hundred rows, and only
    # the handful sharing a title ever need comparing
    by_title: Dict[tuple, List[Dict[str, Any]]] = {}
    for entry in entries:
        fact = facts[int(entry["id"])]
        by_title.setdefault((fact["title_key"], fact["bucket"]), []).append(entry)

    for members in by_title.values():
        groups: List[List[Dict[str, Any]]] = []
        for entry in sorted(members, key=_lead_key):
            fact = facts[int(entry["id"])]
            # a row joins a group only when it is the same release as the
            # group's lead: A like B and B like C does not make A like C
            home = next((g for g in groups if same_release(facts[int(g[0]["id"])], fact)), None)
            if home is None:
                groups.append([entry])
            else:
                home.append(entry)
        for group_rows in groups:
            lead_id = int(group_rows[0]["id"])
            copies = sum(1 for e in group_rows if int(e.get("tracks_present") or 0) > 0)
            for entry in group_rows:
                entry["release_lead_id"] = lead_id
                entry["release_copies"] = copies
