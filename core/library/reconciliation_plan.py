"""Pure file-to-release reconciliation from independent evidence layers.

This planner makes no file or database changes. A match is a hypothesis with
reasons, not permission to edit; callers only propose repairs for unambiguous
high-confidence pairs and keep the competing evidence in the finding.
"""

from __future__ import annotations

import os
import re
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Any

from core.matching.assignment import assign_max_weight
from core.matching.artist_aliases import artist_names_match
from core.matching.audio_verification import fingerprint_is_ambiguous, normalize
from core.text.fold import fold_title
from core.text.title_match import (
    _recording_qualifier_segments, _RECORDING_NOISE_TOKENS, numeric_tokens_differ,
    recording_version_markers, titles_plausibly_same,
)


_LEADING_POSITION = re.compile(r'^\s*(?:\d+[-_. ]+){1,2}')
_EDITORIAL_TITLE_SUFFIX = re.compile(
    r'\s*[\(\[]\s*(?:bonus\s+track|bonus)\s*[\)\]]\s*$', re.IGNORECASE)


@lru_cache(maxsize=32768)
def _version_markers(value: str) -> frozenset[str]:
    """Fold equivalent catalogue wording without erasing real versions."""
    markers = set(recording_version_markers(value))
    if markers.intersection({'alt', 'alternate', 'alternative'}):
        markers.difference_update({'alt', 'alternate', 'alternative'})
        markers.add('alternate')
    if 'acoustic' in markers:
        # Catalogues commonly call the same acoustic take an acoustic session.
        markers.difference_update({'session', 'sessions'})
    for segment in _recording_qualifier_segments(value):
        if re.search(r'\b(?:radio|single)\s+(?:edit|version)\b', segment, re.I):
            markers.add('edit')
        words = fold_title(segment, drop_brackets=False).split()
        if (not markers and words[-1:] == ['mix'] and
                {word for word in words[:-1] if not word.isdigit()} - _RECORDING_NOISE_TOKENS
                - {'club', 'dance', 'video', 'long', 'short', 'digital', 'new', 'old', 'the'}):
            # Catalogue credits may call the same named remix a "Maceo Plex mix".
            markers.add('remix')
    return frozenset(markers)


@lru_cache(maxsize=32768)
def _named_version_details(title: str):
    """Parse qualifier facts once, rather than for every assignment edge."""
    segments = _recording_qualifier_segments(title)
    marked = [(segment, _version_markers(f'({segment})')) for segment in segments]
    if not any(markers for _, markers in marked):
        return frozenset(), None
    tokens = set()
    for segment, markers in marked:
        # Dates can occupy a separate bracket after a live-performance label.
        if markers or re.fullmatch(r'(?:[A-Za-z]{3,9}\s+)?\d{4}', segment):
            tokens.update(fold_title(segment, drop_brackets=False).split())
            tokens.difference_update(markers)
    tokens -= {'version', 'mix', 'recording', 'recorded', 'performance',
               'session', 'sessions', 'alternate', 'alternative', 'alt',
               'at', 'in', 'from', 'the', 'a', 'an', 'import', 'bbc', 'radio', 'single'}
    text = ' '.join(segments)
    iso = re.search(r'\b((?:19|20)\d{2})[-/]([01]?\d)[-/]([0-3]?\d)\b', text)
    dmy = re.search(r'(?<!\d)([0-3]?\d)[/-]([01]?\d)[/-](\d{2}|\d{4})(?!\d)', text)
    date = tuple(map(int, iso.groups())) if iso else tuple(map(int, reversed(dmy.groups()))) if dmy else None
    return frozenset(str(int(token)) if token.isdigit() else token for token in tokens), date


def _named_version_conflicts(left: str, right: str) -> bool:
    """Compare named performances while allowing omitted detail and typos."""
    a, left_date = _named_version_details(left)
    b, right_date = _named_version_details(right)
    if left_date and right_date and (left_date[1:] != right_date[1:] or
            (left_date[0] != right_date[0] if min(left_date[0], right_date[0]) >= 100
             else left_date[0] % 100 != right_date[0] % 100)):
        return True
    if left_date and right_date:
        year = str(max(left_date[0], right_date[0]))
        a = (a - {str(left_date[0])}) | {year}
        b = (b - {str(right_date[0])}) | {year}
    # A year-only local label can agree with a catalogue's precise concert
    # date. Two fully specified dates must still agree on month and day.
    def year_precision(tokens, date, other):
        numbers = {token for token in other if token.isdigit()}
        if date and len(numbers) == 1:
            year = next(iter(numbers))
            if len(year) == 4 and (int(year) == date[0] if date[0] >= 100 else int(year) % 100 == date[0]):
                return frozenset(token for token in tokens if not token.isdigit()) | {year}
        return tokens
    a, b = year_precision(a, left_date, b), year_precision(b, right_date, a)
    if not a or not b or a == b:
        return False
    unmatched_a, unmatched_b = a - b, b - a
    # Utrecht/Utrech is a spelling difference; Alpha/Alpha Beta adds a name.
    if (len(unmatched_a) == len(unmatched_b) == 1 and
            _version_markers(left).intersection(_version_markers(right), {'live', 'session', 'sessions'})):
        x, y = next(iter(unmatched_a)), next(iter(unmatched_b))
        if x.isalpha() and y.isalpha() and min(len(x), len(y)) >= 6:
            return SequenceMatcher(None, x, y).ratio() < .90
    return True


def _title_score(a: str, b: str) -> float:
    left, right = normalize(a), normalize(b)
    if not left or not right:
        return 0.0
    ratio = SequenceMatcher(None, left, right).ratio()
    if ratio < 0.78 or not titles_plausibly_same(left, right, ratio):
        return 0.0
    if numeric_tokens_differ(a, b):
        return 0.0
    left_versions, right_versions = _version_markers(a), _version_markers(b)
    if left_versions != right_versions or _named_version_conflicts(a, b):
        return 0.0
    return ratio


def _filename_title(path: str, artist: str = '') -> str:
    stem = os.path.splitext(os.path.basename(path))[0]
    title = _LEADING_POSITION.sub('', stem)
    parts = re.split(r'\s+[-–—]\s+', title, maxsplit=1) if artist else []
    if len(parts) == 2 and fold_title(parts[0]) == fold_title(artist):
        return parts[1]
    return title


def _recording_id(slot: dict) -> str:
    return str(slot.get('recording_mbid') or slot.get('musicbrainz_recording_id')
               or slot.get('recording_id') or '')


def _disambiguation_markers(value: object) -> frozenset[str]:
    """Treat a MusicBrainz recording comment as qualifier evidence.

    Recording disambiguation is plain text (``Connect Sets acoustic``,
    ``instrumental``), while :func:`recording_version_markers` deliberately
    inspects only qualifier text. Wrapping it keeps ordinary words out of the
    version vocabulary and reuses the same conservative marker rules as title
    matching.
    """
    text = str(value or '').strip()
    return _version_markers(f'({text})') if text else frozenset()


def _identity_text(value: object) -> str:
    folded = re.sub(r'[^\w\s]+', ' ', str(value or '').casefold())
    return re.sub(r'\s+', ' ', folded).strip()


def file_version_markers(file: dict) -> frozenset[str]:
    """Known performance/version markers from independent local layers."""
    tags = file.get('tags') or {}
    row = file.get('db') or {}
    markers = set(_version_markers(str(tags.get('title') or '')))
    markers.update(_version_markers(_filename_title(str(file.get('path') or ''))))
    markers.update(_version_markers(str(row.get('title') or '')))
    markers.update(_disambiguation_markers(row.get('recording_disambiguation')))
    return frozenset(markers)


def slot_version_markers(slot: dict) -> frozenset[str]:
    markers = set(_version_markers(
        str(slot.get('title') or slot.get('name') or '')))
    markers.update(_disambiguation_markers(slot.get('recording_disambiguation')))
    return frozenset(markers)


def display_title_compatible(local_title: str, slot: dict) -> bool:
    """Keep useful local qualifier text when MB confirms the same version.

    Release track titles are often bare while the recording disambiguation
    carries ``live``, ``demo`` or similar detail. In that case a local title
    such as ``Home (Live)`` is more useful than the bare release title and is
    still the same recording identity.
    """
    slot_title = str(slot.get('title') or slot.get('name') or '')
    if _named_version_conflicts(local_title, slot_title):
        return False
    if _title_score(local_title, slot_title) >= 0.83:
        return True
    left, right = normalize(local_title), normalize(slot_title)
    if not left or not right:
        return False
    ratio = SequenceMatcher(None, left, right).ratio()
    if ratio < 0.83 or not titles_plausibly_same(left, right, ratio):
        return False
    if numeric_tokens_differ(local_title, slot_title):
        return False
    return _version_markers(local_title) == slot_version_markers(slot)


def title_difference_kind(local_title: str, slot: dict) -> str:
    """Classify display-title drift while retaining recording versions."""
    slot_title = str(slot.get('title') or slot.get('name') or '')
    if local_title == slot_title:
        return 'exact'
    if fold_title(local_title, drop_brackets=False) == fold_title(
            slot_title, drop_brackets=False):
        return 'typography'
    local_clean = _EDITORIAL_TITLE_SUFFIX.sub('', str(local_title or '')).strip()
    slot_clean = _EDITORIAL_TITLE_SUFFIX.sub('', slot_title).strip()
    local_versions = _version_markers(local_clean)
    slot_versions = slot_version_markers({**slot, 'title': slot_clean})
    if local_versions != slot_versions:
        return 'version'
    if numeric_tokens_differ(local_clean, slot_clean):
        return 'material'
    if _named_version_conflicts(local_clean, slot_clean):
        return 'version'
    if local_versions:
        # Both sides identify the same named performance/version, but one
        # carries useful display detail that the other omits. Preserve it.
        return 'version_detail'
    if (fold_title(local_clean) == fold_title(slot_clean) or
            display_title_compatible(local_clean, {**slot, 'title': slot_clean})):
        return 'flavor'
    return 'material'


def semantic_title_key(slot: dict) -> tuple[str, tuple[str, ...]]:
    """Release-comparison key that ignores harmless provider title flavor."""
    title = _EDITORIAL_TITLE_SUFFIX.sub(
        '', str(slot.get('title') or slot.get('name') or '')).strip()
    return fold_title(title), tuple(sorted(slot_version_markers({**slot, 'title': title})))


def versions_compatible(file: dict, slot: dict, *, local_markers=None, remote_markers=None) -> bool:
    """Require consistent version evidence; exact sessions can supply live context."""
    local = file_version_markers(file) if local_markers is None else local_markers
    remote = slot_version_markers(slot) if remote_markers is None else remote_markers
    file_disambiguation = str(
        (file.get('db') or {}).get('recording_disambiguation') or '').strip()
    slot_disambiguation = str(slot.get('recording_disambiguation') or '').strip()
    slot_title = str(slot.get('title') or slot.get('name') or '')
    tags = file.get('tags') or {}
    if any(_named_version_conflicts(str(title or ''), slot_title) for title in (
            tags.get('title'),
            _filename_title(str(file.get('path') or ''),
                            str(tags.get('track_artist') or tags.get('artist') or '')),
            (file.get('db') or {}).get('title'))):
        return False
    if (file_disambiguation and slot_disambiguation and
            _identity_text(file_disambiguation) != _identity_text(slot_disambiguation)):
        return False
    if local == remote:
        return True
    # A release may name an exact session while its recording comment adds
    # "live". Do not manufacture a studio/live conflict when independent audio
    # identity and the full qualified title both identify that same session.
    tags = file.get('tags') or {}
    delta = _duration_delta_ms(file, slot)
    return bool(
        remote - local == {'live'} and not local - remote
        and local.intersection({'session', 'sessions'})
        and _identity_text(tags.get('title')) == _identity_text(slot.get('title'))
        and delta is not None and delta <= 1000
        and _artist_credit_supports(str(tags.get('artist') or ''), str(slot.get('artist') or ''))
        and _recording_id(slot)
        and any(recording.get('mbid') == _recording_id(slot)
                and float(recording.get('score') or 0) >= 0.90
                for recording in (file.get('acoustid') or {}).get('recordings') or [])
    )


def _recording_supports_slot(recording: dict, slot: dict) -> bool:
    recording_mbid = str(recording.get('mbid') or '')
    slot_mbid = _recording_id(slot)
    if recording_mbid and slot_mbid and recording_mbid == slot_mbid:
        return True
    slot_title = str(slot.get('title') or slot.get('name') or '')
    if _title_score(str(recording.get('title') or ''), slot_title) < 0.88:
        return False
    recording_artist = str(recording.get('artist') or '')
    slot_artist = str(slot.get('artist') or '')
    return _artist_credit_supports(recording_artist, slot_artist, slot.get('artist_aliases'))


def _artist_credit_supports(recording_artist: str, slot_artist: str, aliases=None) -> bool:
    """Accept a release artist contained in a fuller recording credit.

    AcoustID/MusicBrainz often returns the complete collaboration while a
    release slot carries the album artist.  ``Bastille & Hans Zimmer`` is
    positive evidence for a Bastille slot; treating it as a different artist
    leaves an otherwise exact title/duration match needlessly ambiguous.
    """
    if not slot_artist or not recording_artist:
        return True
    # Only equality after folding is accepted here. SequenceMatcher would
    # produce the same answer at threshold 1, at considerable per-edge cost.
    def same_name(left, right):
        folded = fold_title(left, drop_brackets=False)
        return float(bool(folded) and folded == fold_title(right, drop_brackets=False))
    return (artist_names_match(slot_artist, recording_artist, aliases=aliases,
                               threshold=1.0, similarity=same_name)[0] or
            artist_names_match(recording_artist, slot_artist,
                               threshold=1.0, similarity=same_name)[0])


def audio_contradicts_local_title(file: dict) -> bool:
    """Whether strong fingerprint identities all name different recordings."""
    title = str((file.get('tags') or {}).get('title') or
                (file.get('db') or {}).get('title') or '')
    recordings = (file.get('acoustid') or {}).get('recordings') or []
    strong = [recording for recording in recordings
              if (float(recording.get('score') or 0) >= 0.90 and
                  str(recording.get('title') or '').strip())]
    return bool(title and strong) and all(
        (_title_score(str(recording.get('title') or ''), title) < 0.83
         and not _same_base_recording_title(recording.get('title'), title))
        or not _artist_credit_supports(
            str(recording.get('artist') or ''),
            str((file.get('tags') or {}).get('track_artist') or
                (file.get('tags') or {}).get('artist') or ''),
            (file.get('db') or {}).get('artist_aliases'))
        for recording in strong
    )


def _same_base_recording_title(left: object, right: object) -> bool:
    """Whether two recording labels differ only by a bracketed qualifier.

    AcoustID can legitimately attach the same audio to a bare MusicBrainz
    recording and a qualified release track. Pelagial's two copies of
    ``Epipelagic`` are the concrete example: both are instrumental audio even
    though only the second disc labels the track ``(Instrumental)``. The local
    title and release slot still have to agree on their version markers; this
    helper only prevents the bare AcoustID label from vetoing that agreement.
    """
    a, b = fold_title(str(left or '')), fold_title(str(right or ''))
    return bool(a and b and a == b and not _named_version_conflicts(
        str(left or ''), str(right or '')))


def _duration_delta_ms(file: dict, slot: dict) -> int | None:
    tags = file.get('tags') or {}
    file_duration = int(tags.get('duration_ms') or file.get('duration_ms') or 0)
    slot_duration = int(slot.get('duration_ms') or 0)
    if not file_duration or not slot_duration:
        return None
    return abs(file_duration - slot_duration)


def _recording_duration_fits(recording: dict, local_duration: int) -> bool:
    if not local_duration:
        return True
    try:
        length = int(recording.get('duration_ms') or recording.get('length') or 0)
    except (TypeError, ValueError):
        return True
    return not length or abs(local_duration - length) <= 15000


def _duration_weight(delta_ms: int) -> int:
    """Reward precise duration fits without letting duration outrank identity.

    Catalog and decoder durations routinely differ by fractions of a second.
    Inside the existing three-second compatibility window, retain that
    precision as a tie-breaker instead of assigning every fit the same score.
    """
    if delta_ms <= 3000:
        return 800 - round(delta_ms * 600 / 3000)
    if delta_ms <= 15000:
        return 100 - round((delta_ms - 3000) * 75 / 12000)
    return 0


class _MatchFacts:
    """Facts owned by one immutable candidate analysis, never shared with apply.

    Expensive facts are evaluated on first use: early rejected pairs do not
    force extra title comparisons. Each slot-wide alternative search runs at
    most once per file, and duplicate positions retain all slot objects.
    """

    def __init__(self, files: list[dict], slots: list[dict]):
        self.files, self.slots = files, slots  # retain objects used as identity keys
        self.positions: dict[tuple[int, int], list[dict]] = {}
        for slot in slots:
            position = (int(slot.get('disc_number') or 1),
                        int(slot.get('track_number') or 0))
            self.positions.setdefault(position, []).append(slot)
        self._cache: dict[str, dict] = {}

    def _memo(self, name, key, compute):
        cache = self._cache.setdefault(name, {})
        if key not in cache:
            cache[key] = compute()
        return cache[key]

    def title_score(self, left, right):
        # SequenceMatcher is not necessarily symmetric; preserve argument order.
        return self._memo('title', (left, right), lambda: _title_score(left, right))

    def file_markers(self, file):
        return self._memo('file_versions', id(file), lambda: file_version_markers(file))

    def slot_markers(self, slot):
        return self._memo('slot_versions', id(slot), lambda: slot_version_markers(slot))

    def compatible(self, file, slot):
        return self._memo('compatible', (id(file), id(slot)),
                          lambda: versions_compatible(file, slot,
                              local_markers=self.file_markers(file),
                              remote_markers=self.slot_markers(slot)))

    def delta(self, file, slot):
        return self._memo('duration', (id(file), id(slot)),
                          lambda: _duration_delta_ms(file, slot))

    def supports(self, recording, slot):
        return self._memo('support', (id(recording), id(slot)),
                          lambda: _recording_supports_slot(recording, slot))

    def strong_recordings(self, file):
        return self._memo('strong', id(file), lambda: [
            recording for recording in (file.get('acoustid') or {}).get('recordings') or []
            if float(recording.get('score') or 0) >= 0.90])

    def owner_is_established(self, file, slot, *, top_only: bool,
                             owner_weight: int, contender_weight: int) -> bool:
        tags = file.get('tags') or {}
        support = sum((
            self.title_score(str(tags.get('title') or ''), str(slot.get('title') or '')) >= 0.83,
            self.title_score(_filename_title(str(file.get('path') or '')),
                             str(slot.get('title') or '')) >= 0.83,
            bool(tags.get('mbid') and _recording_id(slot) and tags['mbid'] == _recording_id(slot)),
        ))
        recordings = (file.get('acoustid') or {}).get('recordings') or []
        threshold = (max((float(row.get('score') or 0) for row in recordings), default=0)
                     if top_only else 0.80)
        audio_support = any(float(row.get('score') or 0) >= threshold and self.supports(row, slot)
                            for row in recordings)
        delta = self.delta(file, slot)
        return (support >= 2 and audio_support and (delta is None or delta <= 3000)
                and owner_weight >= contender_weight)

    def has_close_alternative(self, file, slot):
        def collect():
            return frozenset(id(other) for other in self.slots
                if self.delta(file, other) is not None and self.delta(file, other) <= 3000
                and any(self.supports(recording, other)
                        for recording in self.strong_recordings(file)))
        supported = self._memo('close_alternatives', id(file), collect)
        return len(supported) > int(id(slot) in supported)


def _pair_weight(file: dict, slot: dict, slots: list[dict] | None = None,
                 facts: _MatchFacts | None = None) -> tuple[int, list[str]]:
    if file.get('companion_of'):
        return 0, []
    facts = facts or _MatchFacts([file], slots or [slot])
    tags = file.get('tags') or {}
    evidence = file.get('acoustid') or {}
    recordings = evidence.get('recordings') or []
    slot_title = str(slot.get('title') or slot.get('name') or '')
    slot_artist = str(slot.get('artist') or '')
    # Repeated titles on one release need their positions kept distinct. A
    # fuzzy fingerprint title may describe either performance (Mezzanine has
    # both "Exchange" and "(Exchange)"). Do not let that fuzzy title swap two
    # numbered files unless an exact recording identity proves the swap.
    local_number = int(tags.get('track_number') or 0)
    local_disc = int(tags.get('disc_number') or 1)
    if (slots and local_number and
            (local_disc, local_number) !=
            (int(slot.get('disc_number') or 1), int(slot.get('track_number') or 0)) and
            any(other is not slot and
                (int(other.get('disc_number') or 1), int(other.get('track_number') or 0)) ==
                (local_disc, local_number) and
                facts.title_score(str(other.get('title') or other.get('name') or ''), slot_title) >= 0.83
                for other in facts.positions.get((local_disc, local_number), []))):
        exact_slot_id = _recording_id(slot)
        if not exact_slot_id or not (
                tags.get('mbid') == exact_slot_id or
                any(recording.get('mbid') == exact_slot_id and
                    float(recording.get('score') or 0) >= 0.90
                    for recording in recordings)):
            return 0, []
    reasons: list[str] = []
    fingerprint_weight = 0
    alternate_wording = False
    strong_recordings = facts.strong_recordings(file)
    if not facts.compatible(file, slot):
        local_versions = facts.file_markers(file)
        local_disambiguation = str(
            (file.get('db') or {}).get('recording_disambiguation') or '').strip()
        exact_audio_identity = bool(
            _recording_id(slot) and any(
                recording.get('mbid') == _recording_id(slot)
                and float(recording.get('score') or 0) >= 0.90
                for recording in strong_recordings
            )
        )
        # Explicit local version evidence is a hard boundary. When the local
        # title is bare, an exact high-score recording MBID may supply the
        # missing version identity and safely populate the newly stored MB
        # disambiguation field.
        # An alternate extended title and an alternate extra-bridge title can
        # describe the same recording. Keep this a reviewable hypothesis only
        # with independent exact audio identity, artist and duration support.
        # Never relax live/studio, remix, acoustic or instrumental boundaries.
        release_versions = facts.slot_markers(slot)
        delta = facts.delta(file, slot)
        alternate_wording = (
            exact_audio_identity and delta is not None and delta <= 1000
            and 'alternate' in local_versions.intersection(release_versions)
            and local_versions.symmetric_difference(release_versions) == {'extended'}
            and any(recording.get('mbid') == _recording_id(slot)
                    and recording.get('artist') and slot_artist
                    and _artist_credit_supports(recording['artist'], slot_artist)
                    for recording in strong_recordings)
        )
        if not alternate_wording and (local_versions or local_disambiguation or not exact_audio_identity):
            return 0, []
    if strong_recordings and all(
        recording.get('mbid') != _recording_id(slot)
        and (not _artist_credit_supports(str(recording.get('artist') or ''),
                                        slot_artist, slot.get('artist_aliases'))
             or (facts.title_score(str(recording.get('title') or ''), slot_title) < 0.83
                 and not _same_base_recording_title(recording.get('title'), slot_title)))
        for recording in strong_recordings
    ):
        # A convincing fingerprint for another song must not become a match
        # merely because both the filename and embedded tags are wrong.
        return 0, []
    current_delta = facts.delta(file, slot)
    if current_delta is not None and current_delta > 30000 and slots:
        # A tied AcoustID result can link one fingerprint to several recordings.
        # If this slot is wildly wrong for the measured audio length while a
        # different fingerprint-supported slot fits closely, do not let stale
        # tags/filename/MBID consume the wrong slot. The joint assignment can
        # then expose the real missing slot and duplicate/unassigned file.
        better_supported_slot = facts.has_close_alternative(file, slot)
        if better_supported_slot:
            return 0, []
    if evidence.get('status') == 'ok':
        for recording in recordings:
            score = float(recording.get('score') or 0)
            if score < 0.80:
                continue
            if _recording_id(slot) and recording.get('mbid') == _recording_id(slot):
                fingerprint_weight = max(fingerprint_weight, int(10_000 * score))
                reasons = ['AcoustID recording ID matches release slot']
            elif facts.supports(recording, slot):
                fingerprint_weight = max(fingerprint_weight, int(5_000 * score))
                reasons = ['AcoustID recording title matches release slot']
            elif (_same_base_recording_title(recording.get('title'), slot_title)
                  and _artist_credit_supports(str(recording.get('artist') or ''),
                                              slot_artist, slot.get('artist_aliases'))):
                # The local title and slot already passed the symmetric
                # version check above. A bare AcoustID recording label is
                # therefore positive audio evidence, rather than merely an
                # exception to the wrong-audio veto.
                fingerprint_weight = max(fingerprint_weight, int(5_000 * score))
                reasons = ['AcoustID base recording title matches release slot']

    if alternate_wording:
        reasons.append('Exact audio identity supports alternate version wording; review qualifiers')
    tag_title = facts.title_score(str(tags.get('title') or ''), slot_title)
    filename_title = facts.title_score(_filename_title(str(file.get('path') or '')), slot_title)
    if not fingerprint_weight and max(tag_title, filename_title) < 0.83:
        return 0, []

    weight = fingerprint_weight
    if tag_title >= 0.83:
        weight += int(1200 * tag_title)
        reasons.append('embedded title agrees')
    if filename_title >= 0.83:
        weight += int(1500 * filename_title)
        reasons.append('filename agrees')
    if tags.get('mbid') and _recording_id(slot) and tags['mbid'] == _recording_id(slot):
        weight += 300 if fingerprint_weight else 1200
        reasons.append('embedded recording ID agrees')
    row = file.get('db') or {}
    file_disambiguation = str(row.get('recording_disambiguation') or '').strip()
    slot_disambiguation = str(slot.get('recording_disambiguation') or '').strip()
    if (file_disambiguation and slot_disambiguation and
            _identity_text(file_disambiguation) == _identity_text(slot_disambiguation)):
        weight += 300
        reasons.append('recording disambiguation agrees')

    delta = facts.delta(file, slot)
    if delta is not None:
        if delta <= 3000:
            weight += _duration_weight(delta)
            reasons.append(f'duration differs by {delta / 1000:.2f}s')
        elif delta <= 15000:
            weight += _duration_weight(delta)
            reasons.append(f'duration differs by {delta / 1000:.2f}s')
        elif delta > 30000 and not fingerprint_weight:
            return 0, []
    if tag_title >= 0.83 and tags.get('track_number') == slot.get('track_number'):
        weight += 100
    return weight, reasons


def reconcile_folder(files: list[dict], slots: list[dict],
                     forced_pairs: dict[int, int] | None = None) -> dict[str, Any]:
    """Assign every file at most one slot and explain uncertain leftovers."""
    if not files:
        return {'pairs': [], 'extra_files': [], 'competing_files': {},
                'missing_slots': list(range(len(slots)))}
    facts = _MatchFacts(files, slots)
    weighted = [[_pair_weight(file, slot, slots, facts) for slot in slots] for file in files]
    forced_pairs = forced_pairs or {}
    if len(set(forced_pairs.values())) != len(forced_pairs):
        raise ValueError('Two manual assignments cannot use the same release slot')
    for file_index, slot_index in forced_pairs.items():
        if not (0 <= file_index < len(files) and 0 <= slot_index < len(slots)):
            raise ValueError('Manual assignment is outside this release')
        weighted[file_index] = [((1_000_000, ['Manually assigned after review'])
                                 if index == slot_index else (0, []))
                                for index in range(len(slots))]
    assigned = assign_max_weight([[weight for weight, _ in row] for row in weighted])
    assigned_by_slot = {slot_index: file_index for file_index, slot_index in assigned}
    assigned_by_file = {file_index: slot_index for file_index, slot_index in assigned}
    largest_slot_number = max((int(item.get('track_number') or 0)
                               for item in slots), default=0)
    has_multiple_discs = len({item.get('disc_number') or 1 for item in slots}) > 1
    pairs = []
    for file_index, slot_index in assigned:
        weight, reasons = weighted[file_index][slot_index]
        evidence = files[file_index].get('acoustid') or {}
        # A top-scored fingerprint spanning different songs cannot authorize a
        # confident repair even when one candidate names the release slot.
        recordings = evidence.get('recordings') or []
        slot = slots[slot_index]
        tags = files[file_index].get('tags') or {}
        local_duration = int(tags.get('duration_ms') or
                             files[file_index].get('duration_ms') or 0)
        # Some providers attach a verified MusicBrainz recording length. A
        # large mismatch rules out that linked identity, while an absent
        # length provides no evidence either way.
        plausible_recordings = [recording for recording in recordings
                                if _recording_duration_fits(recording, local_duration)]
        if not plausible_recordings:
            plausible_recordings = recordings
        top_score = max((float(recording.get('score') or 0)
                         for recording in plausible_recordings), default=0)
        top_recordings = [
            recording for recording in plausible_recordings
            if float(recording.get('score') or 0) >= top_score
        ]
        # Multiple MusicBrainz rows can describe the same named recording.
        # If every top-scored row supports this exact album slot, differences
        # such as "Angeleyes" / "Angel Eyes" are catalogue aliases rather
        # than competing audio identities.
        one_slot_identity = bool(top_recordings) and all(
            facts.supports(recording, slot)
            for recording in top_recordings
        )
        ambiguous_fingerprint = (
            fingerprint_is_ambiguous(plausible_recordings) and not one_slot_identity)
        top_supports_slot = any(
            float(recording.get('score') or 0) >= top_score
            and facts.supports(recording, slot)
            for recording in plausible_recordings
        )
        corroborating_metadata = sum((
            facts.title_score(str(tags.get('title') or ''), str(slot.get('title') or '')) >= 0.83,
            facts.title_score(_filename_title(str(files[file_index].get('path') or '')),
                         str(slot.get('title') or '')) >= 0.83,
            bool(tags.get('mbid') and _recording_id(slot)
                 and tags['mbid'] == _recording_id(slot)),
        ))
        artist_agrees = bool(tags.get('artist') and slot.get('artist')) and _artist_credit_supports(
            str(tags.get('artist') or ''), str(slot.get('artist') or ''))
        position_agrees = (
            bool(tags.get('track_number') and slot.get('track_number'))
            and
            int(tags.get('disc_number') or 1) == int(slot.get('disc_number') or 1)
            and int(tags.get('track_number') or 0) == int(slot.get('track_number') or 0)
        )
        top_recording_id_agrees = bool(_recording_id(slot)) and any(
            str(recording.get('mbid') or '') == _recording_id(slot)
            for recording in top_recordings)
        assigned_duration_delta = facts.delta(files[file_index], slot)
        def unresolved_alternative(
            other_index: int, other: dict, *, assigned_slot_index=slot_index,
            candidate_file_index=file_index, candidate_recordings=top_recordings,
        ) -> bool:
            if other_index == assigned_slot_index:
                return False
            delta = facts.delta(files[candidate_file_index], other)
            if delta is not None and delta > 15000:
                return False
            if not any(facts.supports(recording, other)
                       for recording in candidate_recordings):
                return False
            owner_index = assigned_by_slot.get(other_index)
            if owner_index is None or owner_index == candidate_file_index:
                return True
            # Top-score links resolve an alternative slot only when its owner
            # independently agrees; the competing-file check below uses .80.
            return not facts.owner_is_established(
                files[owner_index], other, top_only=True,
                owner_weight=weighted[owner_index][other_index][0],
                contender_weight=weighted[candidate_file_index][other_index][0])

        competing_release_slot = any(
            unresolved_alternative(other_index, other)
            for other_index, other in enumerate(slots)
        )
        fingerprint_context_resolves = (
            ambiguous_fingerprint and top_supports_slot
            and not competing_release_slot
            and assigned_duration_delta is not None and assigned_duration_delta <= 3000
            and (corroborating_metadata >= 2 or
                 (artist_agrees and top_recording_id_agrees) or
                 (artist_agrees and position_agrees and corroborating_metadata >= 1))
        )
        ambiguous = ambiguous_fingerprint and not fingerprint_context_resolves
        alternative = max((w for j, (w, _) in enumerate(weighted[file_index])
                           if j != slot_index), default=None)
        close_alternative = alternative is not None and alternative >= weight * 0.92
        # A duplicate copy can have a poorer filename or stale tags while
        # sharing the same strong AcoustID identity. Leave room for those
        # weaker metadata layers before declaring either copy the keeper.
        def unresolved_competing_file(other_index: int, *,
                                      assigned_slot_index=slot_index,
                                      assigned_file_index=file_index,
                                      assigned_weight=weight) -> bool:
            contender_weight = weighted[other_index][assigned_slot_index][0]
            if other_index == assigned_file_index or contender_weight < assigned_weight * 0.80:
                return False
            owner_slot_index = assigned_by_file.get(other_index)
            if owner_slot_index is None or owner_slot_index == assigned_slot_index:
                return True
            return not facts.owner_is_established(
                files[other_index], slots[owner_slot_index], top_only=False,
                owner_weight=weighted[other_index][owner_slot_index][0],
                contender_weight=contender_weight)

        competing_file = any(
            unresolved_competing_file(other_index)
            for other_index in range(len(files))
        )
        version_wording_review = bool(file_version_markers(files[file_index])) and not versions_compatible(
            files[file_index], slot)
        ambiguity_reasons = []
        if version_wording_review:
            ambiguity_reasons.append('Recording identity agrees, but the local and release version wording differs')
        if ambiguous:
            ambiguity_reasons.append(
                'AcoustID returned competing identities not resolved by the other evidence')
        if close_alternative:
            ambiguity_reasons.append('another release track fits nearly as well')
        if competing_file:
            ambiguity_reasons.append('another local file fits this release track')
        # A high numbered file outside this release can belong to a different
        # album even when AcoustID links the fingerprint to this song too.
        # Versioned top recordings make that provenance conflict actionable.
        local_number = int(tags.get('track_number') or 0)
        # A continuous album position is valid even when the selected edition
        # restarts its track numbers on each disc. Compare the assigned slot,
        # not just the largest per-disc position, before inferring provenance.
        continuous_position_agrees = has_multiple_discs and local_number == slot_index + 1
        outlying_version = (
            local_number > largest_slot_number + 1
            and local_number != int(slot.get('track_number') or 0)
            and not continuous_position_agrees
            and any(_version_markers(str(recording.get('title') or ''))
                    - slot_version_markers(slot)
                    for recording in top_recordings)
        )
        if outlying_version:
            ambiguity_reasons.append(
                'file position is outside this release and audio suggests another version')
        pairs.append({
            'file_index': file_index, 'slot_index': slot_index,
            'score': weight, 'reasons': reasons,
            'duration_delta_ms': assigned_duration_delta,
            'ambiguity_reasons': ambiguity_reasons,
            'confidence': 'manual' if forced_pairs.get(file_index) == slot_index else
                          'ambiguous' if ambiguous or close_alternative or competing_file or outlying_version or version_wording_review
                          else 'clear' if weight >= 4500 else 'likely',
        })
    # A fingerprint can still name the predominant song when audio from the
    # neighbouring track was appended to it. Opposite, near-equal duration
    # errors on adjacent slots are stronger evidence of a bad boundary than
    # either recording ID alone. Ask for both recordings to be heard.
    for left in pairs:
        if left['confidence'] == 'manual':
            continue
        left_slot = slots[left['slot_index']]
        left_file = files[left['file_index']]
        left_duration = int((left_file.get('tags') or {}).get('duration_ms') or
                            left_file.get('duration_ms') or 0)
        left_expected = int(left_slot.get('duration_ms') or 0)
        left_diff = left_duration - left_expected
        if not left_duration or not left_expected or abs(left_diff) < 20000:
            continue
        for right in pairs:
            if right is left or right['confidence'] == 'manual':
                continue
            right_slot = slots[right['slot_index']]
            if (int(left_slot.get('disc_number') or 1) !=
                    int(right_slot.get('disc_number') or 1) or
                    abs(int(left_slot.get('track_number') or 0) -
                        int(right_slot.get('track_number') or 0)) != 1):
                continue
            right_file = files[right['file_index']]
            right_duration = int((right_file.get('tags') or {}).get('duration_ms') or
                                 right_file.get('duration_ms') or 0)
            right_expected = int(right_slot.get('duration_ms') or 0)
            if not right_duration or not right_expected:
                continue
            right_diff = right_duration - right_expected
            if left_diff * right_diff >= 0 or abs(left_diff + right_diff) > 3000:
                continue
            for pair, neighbor in ((left, right_slot), (right, left_slot)):
                pair['confidence'] = 'ambiguous'
                reason = ('Adjacent tracks have complementary length differences; '
                          f'listen for audio crossing the boundary with '
                          f'{neighbor.get("title") or neighbor.get("name") or "the next track"}')
                if reason not in pair['ambiguity_reasons']:
                    pair['ambiguity_reasons'].append(reason)
    used_files = {pair['file_index'] for pair in pairs}
    used_slots = {pair['slot_index'] for pair in pairs}
    extra_files = [i for i in range(len(files)) if i not in used_files]
    missing_slots = [i for i in range(len(slots)) if i not in used_slots]
    for pair in pairs:
        if pair['confidence'] == 'manual':
            continue
        slot = slots[pair['slot_index']]
        file = files[pair['file_index']]
        file_length = int((file.get('tags') or {}).get('duration_ms') or
                          file.get('duration_ms') or 0)
        slot_length = int(slot.get('duration_ms') or 0)
        difference = file_length - slot_length
        if not file_length or not slot_length or abs(difference) < 20000:
            continue
        for missing_index in missing_slots:
            neighbor = slots[missing_index]
            if (int(slot.get('disc_number') or 1) !=
                    int(neighbor.get('disc_number') or 1) or
                    abs(int(slot.get('track_number') or 0) -
                        int(neighbor.get('track_number') or 0)) != 1):
                continue
            for extra_index in extra_files:
                extra = files[extra_index]
                extra_title = str((extra.get('tags') or {}).get('title') or
                                  _filename_title(extra.get('path') or ''))
                if facts.title_score(extra_title, str(neighbor.get('title') or '')) < 0.83:
                    continue
                extra_length = int((extra.get('tags') or {}).get('duration_ms') or
                                   extra.get('duration_ms') or 0)
                neighbor_length = int(neighbor.get('duration_ms') or 0)
                if not extra_length or not neighbor_length:
                    continue
                other_difference = extra_length - neighbor_length
                if difference * other_difference >= 0 or abs(difference + other_difference) > 3000:
                    continue
                pair['confidence'] = 'ambiguous'
                reason = ('Adjacent unmatched track has an opposite length error; '
                          f'listen for audio crossing the boundary with '
                          f'{neighbor.get("title") or "the next track"}')
                if reason not in pair['ambiguity_reasons']:
                    pair['ambiguity_reasons'].append(reason)
    # Keep a large mismatch on its own track even when the neighbouring file
    # was removed. The absence of complementary evidence cannot confirm audio.
    for pair in pairs:
        if pair['confidence'] == 'manual':
            continue
        slot = slots[pair['slot_index']]
        file = files[pair['file_index']]
        actual = int((file.get('tags') or {}).get('duration_ms') or
                     file.get('duration_ms') or 0)
        expected = int(slot.get('duration_ms') or 0)
        if not actual or not expected or abs(actual - expected) < max(20000, expected * 0.06):
            continue
        pair['confidence'] = 'ambiguous'
        pair['ambiguity_reasons'].append(
            f'Audio length differs from the release track by '
            f'{abs(actual - expected) / 1000:.1f} seconds; listen for a '
            'different version or a track boundary error')
    competing_files = {
        extra_index: [
            pair['file_index'] for pair in pairs
            if weighted[extra_index][pair['slot_index']][0] >= pair['score'] * 0.80
        ] for extra_index in extra_files
    }
    return {
        'pairs': pairs,
        'extra_files': extra_files,
        'competing_files': {index: rivals for index, rivals in competing_files.items() if rivals},
        'missing_slots': missing_slots,
    }
