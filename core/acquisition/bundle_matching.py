"""Edition-aware matching of a bundle inventory to the expected tracklist.

Implements audit §13.4 steps 7-8: the inventoried audio files of one
completed bundle are matched against the ReleaseEdition tracklist the
request was created for. ``match_bundle`` is a pure, deterministic
function over inventory dicts; only :func:`load_expected_tracks` reads the
catalog. Confidence values and the auto-import threshold are fixed
constants so tests pin the exact decision behaviour — an ambiguous bundle
must end in ``needs_review``, never in a silent partial import.

Titles never merge different recordings: normalization strips featuring
credits and punctuation only. Live/Remaster/version markers stay part of
the compared title (ADR-04) so a "Song (Live)" file cannot auto-match the
studio track.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple


DECISION_IMPORT_READY = "import_ready"
DECISION_NEEDS_REVIEW = "needs_review"

# Fixed confidence ladder (tests pin these — change deliberately). Values
# below AUTO_IMPORT_MIN_CONFIDENCE always end in needs_review: position
# without any title/duration corroboration and bare title matches are not
# enough evidence for an unattended import.
CONFIDENCE_POSITION_AND_TITLE = 1.0
CONFIDENCE_POSITION_TITLE_SIMILAR = 0.9
CONFIDENCE_TITLE_EXACT_DURATION = 0.85
CONFIDENCE_POSITION_DURATION = 0.8
CONFIDENCE_TITLE_EXACT = 0.75
CONFIDENCE_TITLE_SIMILAR = 0.7
CONFIDENCE_POSITION_ONLY = 0.6
AUTO_IMPORT_MIN_CONFIDENCE = 0.8

# A same-position title below this similarity contradicts the expected
# tracklist (wrong edition smell) and must not match by position alone.
POSITION_TITLE_MIN_SIMILARITY = 0.6
TITLE_SIMILAR_MIN = 0.85
TITLE_SIMILAR_MARGIN = 0.05

DURATION_WARN_SECONDS = 10.0
DURATION_SUSPECT_SECONDS = 30.0
DURATION_WARN_PENALTY = 0.1
DURATION_SUSPECT_PENALTY = 0.3


_FEAT_RE = re.compile(
    r"[(\[][^)\]]*\b(?:feat|featuring|ft|with)\b[^)\]]*[)\]]"
    r"|\bfeat\.?\s+.+$|\bft\.?\s+.+$",
    re.IGNORECASE,
)
_DISC_DIR_RE = re.compile(r"\b(?:cd|disc|disk)[\s._-]*(\d{1,2})\b", re.IGNORECASE)
_TRACK_FROM_NAME_RE = re.compile(r"^\s*(\d{1,2})\s*(?:[-._ ]|$)")
_DISC_TRACK_FROM_NAME_RE = re.compile(r"^\s*(\d)\s*-\s*(\d{2})\s*(?:[-._ ]|$)")

# Version markers are identity, not noise (ADR-04): a title carrying one of
# these words may only match a title carrying the same set of them. This is
# what keeps "Song (Live)" from ever auto-matching the studio "Song".
_VERSION_MARKERS = frozenset({
    "live", "remaster", "remastered", "remix", "remixed", "acoustic",
    "demo", "instrumental", "edit", "mix", "version", "radio",
    "extended", "mono", "stereo", "unplugged", "karaoke",
})


def _version_marker_conflict(title_key_a: str, title_key_b: str) -> bool:
    markers_a = frozenset(title_key_a.split()) & _VERSION_MARKERS
    markers_b = frozenset(title_key_b.split()) & _VERSION_MARKERS
    return markers_a != markers_b


def normalize_title(value: Any) -> str:
    """Casefolded, feat-stripped, punctuation-free comparison key."""
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = _FEAT_RE.sub(" ", text)
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return " ".join(text.casefold().split())


def title_similarity(a: str, b: str) -> float:
    """Deterministic [0..1] similarity of two already-normalized titles."""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


@dataclass(frozen=True)
class ExpectedTrack:
    """One row of the tracklist the request is expected to fulfil."""

    release_track_id: Optional[int]
    recording_id: Optional[int]
    track_id: Optional[int]
    disc_number: int
    track_number: Optional[int]
    title: str
    duration_seconds: Optional[float]

    @property
    def key(self) -> str:
        if self.release_track_id is not None:
            return f"release_track:{self.release_track_id}"
        return f"recording:{self.recording_id}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "expected_key": self.key,
            "release_track_id": self.release_track_id,
            "recording_id": self.recording_id,
            "track_id": self.track_id,
            "disc_number": self.disc_number,
            "track_number": self.track_number,
            "expected_title": self.title,
            "expected_duration_seconds": self.duration_seconds,
        }


def _duration_seconds(raw_ms: Any) -> Optional[float]:
    try:
        value = float(raw_ms)
    except (TypeError, ValueError):
        return None
    return value / 1000.0 if value > 0 else None


def _expected_from_rows(rows: Sequence[Any]) -> Tuple[ExpectedTrack, ...]:
    expected = []
    for row in rows:
        data = dict(row)
        expected.append(ExpectedTrack(
            release_track_id=(
                int(data["release_track_id"])
                if data.get("release_track_id") is not None else None),
            recording_id=(
                int(data["recording_id"])
                if data.get("recording_id") is not None else None),
            track_id=(
                int(data["track_id"])
                if data.get("track_id") is not None else None),
            disc_number=int(data.get("disc_number") or 1),
            track_number=(
                int(data["track_number"])
                if data.get("track_number") else None),
            title=str(data.get("title") or "").strip(),
            duration_seconds=_duration_seconds(data.get("duration_ms")),
        ))
    return tuple(expected)


def _edition_tracks(conn: Any, edition_id: int) -> Tuple[ExpectedTrack, ...]:
    rows = conn.execute(
        """SELECT rt.id AS release_track_id,
                  rt.recording_id AS recording_id,
                  rt.track_id AS track_id,
                  rt.disc_number AS disc_number,
                  rt.track_number AS track_number,
                  COALESCE(rt.title_override, rec.title) AS title,
                  COALESCE(rt.duration, rec.duration) AS duration_ms
             FROM lib2_release_tracks rt
             JOIN lib2_recordings rec ON rec.id = rt.recording_id
            WHERE rt.release_edition_id = ?
            ORDER BY rt.disc_number, rt.track_number, rt.id""",
        (int(edition_id),),
    ).fetchall()
    return _expected_from_rows(rows)


def load_expected_tracks(
    conn: Any,
    scope: str,
    entity_id: int,
    *,
    search_options: Optional[Mapping[str, Any]] = None,
) -> Tuple[ExpectedTrack, ...]:
    """Load the expected tracklist for one acquisition scope.

    Returns an empty tuple when the scope has no defined tracklist
    (``artist_missing``, an edition without materialized tracks). The
    matcher turns that into ``needs_review`` instead of guessing.
    """
    scope = str(scope or "").strip().lower()
    entity_id = int(entity_id)
    if scope == "release_edition":
        return _edition_tracks(conn, entity_id)
    if scope == "release_group":
        row = conn.execute(
            """SELECT id FROM lib2_release_editions
                WHERE release_group_id=? AND is_default=1""",
            (entity_id,),
        ).fetchone()
        if row is None:
            return ()
        return _edition_tracks(conn, int(row[0]))
    if scope == "recording":
        rows = conn.execute(
            """SELECT NULL AS release_track_id,
                      rec.id AS recording_id,
                      (SELECT rt.track_id FROM lib2_release_tracks rt
                        WHERE rt.recording_id=rec.id AND rt.track_id IS NOT NULL
                        ORDER BY rt.id LIMIT 1) AS track_id,
                      1 AS disc_number,
                      NULL AS track_number,
                      rec.title AS title,
                      rec.duration AS duration_ms
                 FROM lib2_recordings rec WHERE rec.id=?""",
            (entity_id,),
        ).fetchall()
        return _expected_from_rows(rows)
    if scope == "upgrade":
        entity_type = str(
            (search_options or {}).get("entity_type") or "").strip().lower()
        if entity_type in {"recording", "release_edition"}:
            return load_expected_tracks(conn, entity_type, entity_id)
        return ()
    return ()


# ---------------------------------------------------------------------------
# File-side fact extraction (pure, filename fallbacks for untagged bundles)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _FileFacts:
    index: int
    file: Mapping[str, Any]
    relative_path: str
    title_key: str
    track_number: Optional[int]
    disc_number: Optional[int]
    duration_seconds: Optional[float]


def _facts_for_file(index: int, file: Mapping[str, Any]) -> _FileFacts:
    relative_path = str(file.get("relative_path") or "")
    name = relative_path.rsplit("/", 1)[-1]
    stem = name.rsplit(".", 1)[0] if "." in name else name

    track_number = file.get("track_number")
    disc_number = file.get("disc_number")
    if track_number is None:
        pair = _DISC_TRACK_FROM_NAME_RE.match(stem)
        if pair is not None:
            disc_number = disc_number or int(pair.group(1))
            track_number = int(pair.group(2))
        else:
            single = _TRACK_FROM_NAME_RE.match(stem)
            if single is not None:
                track_number = int(single.group(1))
    if disc_number is None and "/" in relative_path:
        directory = relative_path.rsplit("/", 1)[0]
        disc_match = _DISC_DIR_RE.search(directory)
        if disc_match is not None:
            disc_number = int(disc_match.group(1))

    title = file.get("title")
    if not title:
        # Strip the leading numbering we just parsed for a usable title key.
        stripped = _DISC_TRACK_FROM_NAME_RE.sub("", stem)
        stripped = _TRACK_FROM_NAME_RE.sub("", stripped)
        title = stripped
    duration = file.get("duration_seconds")
    try:
        duration_seconds = float(duration) if duration else None
    except (TypeError, ValueError):
        duration_seconds = None
    return _FileFacts(
        index=index,
        file=file,
        relative_path=relative_path,
        title_key=normalize_title(title),
        track_number=(
            int(track_number)
            if track_number and int(track_number) > 0 else None),
        disc_number=(
            int(disc_number)
            if disc_number and int(disc_number) > 0 else None),
        duration_seconds=duration_seconds,
    )


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TrackMatch:
    expected: ExpectedTrack
    file: Mapping[str, Any]
    confidence: float
    strategy: str
    warnings: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            **self.expected.to_dict(),
            "relative_path": str(self.file.get("relative_path") or ""),
            "confidence": self.confidence,
            "strategy": self.strategy,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class BundleMatchReport:
    decision: str
    matches: Tuple[TrackMatch, ...]
    rejections: Tuple[Dict[str, Any], ...]

    @property
    def import_ready(self) -> bool:
        return self.decision == DECISION_IMPORT_READY

    def matches_payload(self) -> Tuple[Dict[str, Any], ...]:
        return tuple(match.to_dict() for match in self.matches)

    def rejections_payload(self) -> Tuple[Dict[str, Any], ...]:
        return tuple(dict(item) for item in self.rejections)


def _duration_check(
    expected: ExpectedTrack, facts: _FileFacts,
) -> Tuple[float, Tuple[str, ...]]:
    if expected.duration_seconds is None or facts.duration_seconds is None:
        return 0.0, ()
    diff = abs(expected.duration_seconds - facts.duration_seconds)
    if diff > DURATION_SUSPECT_SECONDS:
        return DURATION_SUSPECT_PENALTY, (
            f"duration_mismatch:{diff:.0f}s",)
    if diff > DURATION_WARN_SECONDS:
        return DURATION_WARN_PENALTY, (f"duration_drift:{diff:.0f}s",)
    return 0.0, ()


def _duration_confirms(
    expected: ExpectedTrack, facts: _FileFacts,
) -> bool:
    return (
        expected.duration_seconds is not None
        and facts.duration_seconds is not None
        and abs(expected.duration_seconds - facts.duration_seconds)
        <= DURATION_WARN_SECONDS
    )


def _effective_disc(
    facts: _FileFacts, expected_discs: frozenset[int],
) -> Optional[int]:
    if facts.disc_number is not None:
        return facts.disc_number
    if expected_discs == frozenset({1}):
        return 1
    return None


def match_bundle(
    expected: Sequence[ExpectedTrack],
    files: Sequence[Mapping[str, Any]],
) -> BundleMatchReport:
    """Deterministically match inventory files to the expected tracklist.

    Position first, then exact titles, then unique high similarity.
    Every leftover on either side and every sub-threshold confidence is a
    structured rejection; any rejection forces ``needs_review``.
    """
    rejections: list[Dict[str, Any]] = []
    if not expected:
        return BundleMatchReport(
            decision=DECISION_NEEDS_REVIEW,
            matches=(),
            rejections=(
                {"code": "no_expected_tracklist"},
            ),
        )

    facts = [_facts_for_file(index, file) for index, file in enumerate(files)]
    expected_discs = frozenset(track.disc_number for track in expected)
    open_expected: dict[str, ExpectedTrack] = {
        track.key: track for track in expected}
    open_files: dict[int, _FileFacts] = {item.index: item for item in facts}
    matches: list[TrackMatch] = []

    def commit(
        track: ExpectedTrack,
        item: _FileFacts,
        confidence: float,
        strategy: str,
        warnings: Tuple[str, ...] = (),
    ) -> None:
        penalty, duration_warnings = _duration_check(track, item)
        matches.append(TrackMatch(
            expected=track,
            file=item.file,
            confidence=round(max(0.0, confidence - penalty), 2),
            strategy=strategy,
            warnings=warnings + duration_warnings,
        ))
        open_expected.pop(track.key, None)
        open_files.pop(item.index, None)

    # Pass 1: disc+track position with a non-contradicting title.
    by_position: dict[Tuple[int, int], list[ExpectedTrack]] = {}
    for track in expected:
        if track.track_number is not None:
            by_position.setdefault(
                (track.disc_number, track.track_number), []).append(track)
    for item in list(facts):
        if item.index not in open_files or item.track_number is None:
            continue
        disc = _effective_disc(item, expected_discs)
        if disc is None:
            candidates = [
                track
                for (track_disc, number), tracks in by_position.items()
                for track in tracks
                if number == item.track_number and track.key in open_expected
            ]
            if len(candidates) > 1:
                rejections.append({
                    "code": "ambiguous_position",
                    "relative_path": item.relative_path,
                    "track_number": item.track_number,
                    "reason": "multi_disc_bundle_without_disc_number",
                })
                continue
        else:
            candidates = [
                track
                for track in by_position.get((disc, item.track_number), ())
                if track.key in open_expected
            ]
        if len(candidates) != 1:
            continue
        track = candidates[0]
        expected_key = normalize_title(track.title)
        if item.title_key and expected_key:
            similarity = title_similarity(expected_key, item.title_key)
            if similarity >= 1.0:
                commit(track, item, CONFIDENCE_POSITION_AND_TITLE,
                       "position_and_title")
            elif (
                similarity >= POSITION_TITLE_MIN_SIMILARITY
                and not _version_marker_conflict(expected_key, item.title_key)
            ):
                commit(track, item, CONFIDENCE_POSITION_TITLE_SIMILAR,
                       "position_title_similar")
            # else: contradicting title — leave for the title passes.
        elif _duration_confirms(track, item):
            commit(track, item, CONFIDENCE_POSITION_DURATION,
                   "position_duration")
        else:
            commit(
                track, item, CONFIDENCE_POSITION_ONLY,
                "position_only",
                warnings=("untagged_title_unverified_duration",),
            )

    # Pass 2: exact normalized title, unique on both sides.
    open_titles: dict[str, list[ExpectedTrack]] = {}
    for track in open_expected.values():
        key = normalize_title(track.title)
        if key:
            open_titles.setdefault(key, []).append(track)
    for item in list(open_files.values()):
        if not item.title_key:
            continue
        candidates = open_titles.get(item.title_key, ())
        live = [track for track in candidates if track.key in open_expected]
        if len(live) != 1:
            continue
        track = live[0]
        position_agrees = (
            item.track_number is not None
            and track.track_number == item.track_number
        )
        if _duration_confirms(track, item):
            confidence, strategy = (
                CONFIDENCE_TITLE_EXACT_DURATION, "title_exact_duration")
        else:
            confidence, strategy = (CONFIDENCE_TITLE_EXACT, "title_exact")
        commit(
            track, item, confidence, strategy,
            warnings=() if position_agrees or item.track_number is None
            else ("position_disagrees",),
        )

    # Pass 3: unique high-similarity title with a clear margin.
    for item in list(open_files.values()):
        if not item.title_key:
            continue
        scored = sorted(
            (
                (
                    title_similarity(
                        normalize_title(track.title), item.title_key),
                    track.key,
                    track,
                )
                for track in open_expected.values()
                if not _version_marker_conflict(
                    normalize_title(track.title), item.title_key)
            ),
            key=lambda entry: (-entry[0], entry[1]),
        )
        if not scored or scored[0][0] < TITLE_SIMILAR_MIN:
            continue
        if len(scored) > 1 and scored[0][0] - scored[1][0] < TITLE_SIMILAR_MARGIN:
            rejections.append({
                "code": "ambiguous_title",
                "relative_path": item.relative_path,
                "similarity": round(scored[0][0], 3),
            })
            continue
        commit(scored[0][2], item, CONFIDENCE_TITLE_SIMILAR, "title_similar")

    for track in open_expected.values():
        rejections.append({
            "code": "missing_expected_track",
            **{
                key: value
                for key, value in track.to_dict().items()
                if key in {
                    "expected_key", "disc_number",
                    "track_number", "expected_title",
                }
            },
        })
    for item in open_files.values():
        rejections.append({
            "code": "unmatched_file",
            "relative_path": item.relative_path,
            "title": item.file.get("title"),
        })
    for match in matches:
        if match.confidence < AUTO_IMPORT_MIN_CONFIDENCE:
            rejections.append({
                "code": "low_confidence",
                "relative_path": str(match.file.get("relative_path") or ""),
                "expected_key": match.expected.key,
                "confidence": match.confidence,
            })

    matches.sort(key=lambda match: (
        match.expected.disc_number,
        match.expected.track_number or 0,
        match.expected.key,
    ))
    rejections.sort(key=lambda item: (str(item.get("code")), str(item)))
    decision = (
        DECISION_IMPORT_READY if not rejections else DECISION_NEEDS_REVIEW)
    return BundleMatchReport(
        decision=decision,
        matches=tuple(matches),
        rejections=tuple(rejections),
    )


__all__ = [
    "AUTO_IMPORT_MIN_CONFIDENCE",
    "CONFIDENCE_POSITION_AND_TITLE",
    "CONFIDENCE_POSITION_DURATION",
    "CONFIDENCE_POSITION_ONLY",
    "CONFIDENCE_POSITION_TITLE_SIMILAR",
    "CONFIDENCE_TITLE_EXACT",
    "CONFIDENCE_TITLE_EXACT_DURATION",
    "CONFIDENCE_TITLE_SIMILAR",
    "DECISION_IMPORT_READY",
    "DECISION_NEEDS_REVIEW",
    "BundleMatchReport",
    "ExpectedTrack",
    "TrackMatch",
    "load_expected_tracks",
    "match_bundle",
    "normalize_title",
    "title_similarity",
]
