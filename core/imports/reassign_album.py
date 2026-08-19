"""Reassign a whole album to a different artist/release.

TheHomeGuy: "Is there any way to re assign an album to a different artist? I
have one that is on the wrong artist... i have had this happen when a featured
artist is taken as the album artist."

WHY THIS IS NOT A DATABASE UPDATE. SoulSync's library is a MIRROR of the media
server, which reads tags off the files. Repointing ``albums.artist_id`` alone
survives exactly until the next full refresh (``clear_server_data`` drops every
row for the server and repopulates), and Jellyfin shows the wrong artist the
whole time regardless. The tags are the source of truth.

WHY IT IS NOT A BESPOKE RE-TAG + MOVE EITHER. That would be a second
implementation of what the import pipeline already does, and the two would
drift. The re-identify TRACK feature (#889) already settled this question: it
stages a COPY of the library file back into auto-import with a single-use DB
hint, and lets the pipeline re-file it. Tags, folder layout and database rows
then all come from the code that handles a fresh download.

So an album reassign is the same trick, N times: one hint per track, all
pointing at the release the user picked.

THE PART THAT IS GENUINELY NEW is deciding WHICH local file becomes WHICH
track on the target release. A track re-identify has one file and one chosen
track; an album has to line up two tracklists that may not agree on length,
numbering or titles. That is what this module is for, and it is deliberately
pure so the mapping can be shown to the user for confirmation BEFORE anything
touches the disk — an album is many files, and a silent wrong guess is many
misfiled tracks rather than one.
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from utils.logging_config import get_logger

logger = get_logger("imports.reassign_album")


def normalize_title(text: Any) -> str:
    """Casefold, strip diacritics and punctuation, collapse whitespace.

    Deliberately does NOT strip edition qualifiers (Remastered / Live /
    Acoustic): two recordings of one song under different qualifiers are
    different tracks, and folding them together is how a live version ends up
    filed as the studio one.
    """
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(text))
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    # Apostrophes are DELETED, not turned into a space: taggers and metadata
    # sources disagree constantly about them, and "Don't Stop" must normalise
    # to the same thing as "Dont Stop". Turning them into spaces splits one
    # word into two and drops the similarity below the floor.
    without_quotes = re.sub(r"['\u2019\u02bc`]", "", stripped)
    # `[\W_]` and NOT `[^a-z0-9]`: the ASCII-only version reduced any
    # non-Latin title to an EMPTY string, so two identical Japanese, Korean,
    # Cyrillic or Greek titles scored 0.0 similarity and could never be
    # matched by title at all. `\w` is Unicode-aware in Python 3, so scripts
    # are kept and only punctuation and underscores are dropped.
    cleaned = re.sub(r"[\W_]+", " ", without_quotes.casefold(), flags=re.UNICODE)
    return " ".join(cleaned.split())


def title_similarity(a: Any, b: Any) -> float:
    """0..1 token overlap (Jaccard) between two titles."""
    left = set(normalize_title(a).split())
    right = set(normalize_title(b).split())
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


# Above this, two differently-numbered tracks are confidently the same song.
# Set high on purpose: a wrong pairing here misfiles a track under a name it
# does not have, and leaving it UNMAPPED (visible, excluded) is a better
# failure than guessing.
TITLE_MATCH_FLOOR = 0.8


@dataclass
class TrackPairing:
    """One local file lined up against one track on the target release."""
    local_id: Any
    local_title: str
    local_track_number: Optional[int]
    local_path: str
    target_id: Optional[str] = None
    target_title: str = ""
    target_track_number: Optional[int] = None
    target_disc_number: Optional[int] = None
    # 'track_number' | 'title' | None — shown in the UI so the user can see
    # WHY a pairing was proposed, not just that it was.
    matched_by: Optional[str] = None

    @property
    def mapped(self) -> bool:
        return self.target_id is not None


@dataclass
class ReassignPlan:
    pairings: List[TrackPairing] = field(default_factory=list)

    @property
    def mapped(self) -> List[TrackPairing]:
        return [p for p in self.pairings if p.mapped]

    @property
    def unmapped(self) -> List[TrackPairing]:
        return [p for p in self.pairings if not p.mapped]


def _int_or_none(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _target_title(target: Dict[str, Any]) -> str:
    return str(target.get("name") or target.get("title") or "")


def map_album_tracks(
    local_tracks: Sequence[Dict[str, Any]],
    target_tracks: Sequence[Dict[str, Any]],
) -> List[TrackPairing]:
    """Line the album's local files up against the target release's tracklist.

    Two passes, most-trustworthy first:

    1. **Track number.** The strongest signal, and the only one that survives a
       title the tagger mangled. Used only when the number is UNAMBIGUOUS on
       both sides — a duplicated number (multi-disc flattened into one folder)
       is skipped rather than guessed at.
    2. **Title similarity**, for whatever pass 1 left over. Greedy on the best
       score above ``TITLE_MATCH_FLOOR``, and strictly one-to-one so two local
       files can never claim the same target track.

    Anything still unmatched comes back with ``target_id=None``. That is a
    RESULT, not a failure: the caller shows it and excludes it. An album is
    many files, so a silent wrong guess is many misfiled tracks, and "I could
    not place these three" is a far better answer than three wrong answers.
    """
    pairings: List[TrackPairing] = []
    for local in local_tracks or ():
        pairings.append(TrackPairing(
            local_id=local.get("id"),
            local_title=str(local.get("title") or ""),
            local_track_number=_int_or_none(local.get("track_number")),
            local_path=str(local.get("file_path") or ""),
        ))

    remaining = list(target_tracks or ())

    # Pass 1 — unambiguous track numbers only.
    by_number: Dict[int, List[Dict[str, Any]]] = {}
    for target in remaining:
        number = _int_or_none(target.get("track_number"))
        if number is not None:
            by_number.setdefault(number, []).append(target)

    local_number_counts: Dict[int, int] = {}
    for pairing in pairings:
        if pairing.local_track_number is not None:
            local_number_counts[pairing.local_track_number] = (
                local_number_counts.get(pairing.local_track_number, 0) + 1)

    claimed: List[int] = []
    for pairing in pairings:
        number = pairing.local_track_number
        if number is None:
            continue
        candidates = by_number.get(number) or []
        if len(candidates) != 1 or local_number_counts.get(number, 0) != 1:
            # Ambiguous on one side or the other — leave it to the title pass
            # rather than pick one of two tracks that share a number.
            continue
        target = candidates[0]
        _apply(pairing, target, "track_number")
        claimed.append(id(target))

    remaining = [t for t in remaining if id(t) not in claimed]

    # Pass 2 — title similarity, greedy on the best score, one-to-one.
    for pairing in pairings:
        if pairing.mapped or not remaining:
            continue
        best = None
        best_score = 0.0
        for target in remaining:
            score = title_similarity(pairing.local_title, _target_title(target))
            if score > best_score:
                best, best_score = target, score
        if best is not None and best_score >= TITLE_MATCH_FLOOR:
            _apply(pairing, best, "title")
            remaining = [t for t in remaining if t is not best]

    return pairings


def _apply(pairing: TrackPairing, target: Dict[str, Any], matched_by: str) -> None:
    pairing.target_id = str(target.get("id") or "") or None
    pairing.target_title = _target_title(target)
    pairing.target_track_number = _int_or_none(target.get("track_number"))
    pairing.target_disc_number = _int_or_none(target.get("disc_number")) or 1
    pairing.matched_by = matched_by if pairing.target_id else None
    if pairing.target_id is None:
        # A target row with no id cannot become a hint — the import needs it to
        # fetch the release. Treat it as unmatched rather than half-matched.
        pairing.target_title = ""
        pairing.matched_by = None


def build_reassign_plan(
    local_tracks: Sequence[Dict[str, Any]],
    target_tracks: Sequence[Dict[str, Any]],
) -> ReassignPlan:
    """The mapping, ready to show the user before anything is staged."""
    return ReassignPlan(pairings=map_album_tracks(local_tracks, target_tracks))


def hint_fields_for(
    pairing: TrackPairing,
    *,
    source: str,
    album_id: str,
    album_name: str,
    artist_id: Optional[str],
    artist_name: str,
    album_type: Optional[str] = None,
) -> Dict[str, Any]:
    """The hint payload for ONE paired file.

    Every track of the reassigned album carries the SAME release identity and
    its own track identity — which is exactly what makes the import pipeline
    file them together under the new artist.
    """
    return {
        "source": source,
        "track_id": pairing.target_id,
        "album_id": str(album_id),
        "artist_id": str(artist_id) if artist_id else None,
        "track_title": pairing.target_title or pairing.local_title,
        "album_name": album_name,
        "artist_name": artist_name,
        "album_type": album_type,
        "track_number": pairing.target_track_number or pairing.local_track_number,
        "disc_number": pairing.target_disc_number or 1,
        "isrc": None,
    }





def apply_album_reassign(
    plan: ReassignPlan,
    *,
    source: str,
    album_id: str,
    album_name: str,
    artist_id: Optional[str],
    artist_name: str,
    album_type: Optional[str],
    staging_dir: str,
    cursor: Any,
    replace: bool = True,
) -> Dict[str, Any]:
    """Stage every mapped file and write its hint. Returns a per-file summary.

    Ordering is deliberate: stage the COPY first, then write the hint. A hint
    without its file is a row that can never be consumed and sits pending
    forever.

    If the hint cannot be written the staged copy is REMOVED again. Leaving it
    is not harmless: auto-import would pick it up as an ordinary file and match
    it however it likes, so the user ends up with the original AND a duplicate
    filed somewhere they did not ask for — N times over for an album.

    Partial success is a real outcome and is reported as one. An album is many
    files, and a single unreadable track must not abandon the other eleven —
    the caller shows what was staged and what was not.
    """
    from core.imports.rematch_apply import build_reidentify_hint, stage_file_for_reidentify
    from core.imports.rematch_hints import create_hint

    staged: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []

    for pairing in plan.mapped:
        try:
            staging = stage_file_for_reidentify(
                pairing.local_path, staging_dir, pairing.local_id)
        except FileNotFoundError:
            failed.append({'title': pairing.local_title,
                           'error': 'file is no longer on disk'})
            continue
        except Exception as exc:                        # noqa: BLE001 - one file must not sink the album
            failed.append({'title': pairing.local_title, 'error': str(exc)})
            continue

        try:
            hint = build_reidentify_hint(
                pairing.local_id,
                hint_fields_for(
                    pairing, source=source, album_id=album_id, album_name=album_name,
                    artist_id=artist_id, artist_name=artist_name, album_type=album_type,
                ),
                staging['staged_path'],
                staging.get('content_hash'),
                replace=replace,
            )
            create_hint(cursor, hint)
        except Exception as exc:                        # noqa: BLE001
            # Take the staged copy back out, or auto-import will treat it as a
            # new file and duplicate the track.
            try:
                os.remove(staging['staged_path'])
            except OSError as cleanup_exc:
                logger.warning("Could not remove orphaned staged copy %s: %s",
                               staging['staged_path'], cleanup_exc)
            failed.append({'title': pairing.local_title, 'error': f'could not record hint: {exc}'})
            continue

        staged.append({'title': pairing.local_title,
                       'target_title': pairing.target_title,
                       'staged_path': staging['staged_path']})

    return {
        'staged': staged,
        'failed': failed,
        'skipped': [{'title': p.local_title, 'reason': 'no matching track on the target release'}
                    for p in plan.unmapped],
    }


__all__ = [
    "TITLE_MATCH_FLOOR",
    "apply_album_reassign",
    "ReassignPlan",
    "TrackPairing",
    "build_reassign_plan",
    "hint_fields_for",
    "map_album_tracks",
    "normalize_title",
    "title_similarity",
]
