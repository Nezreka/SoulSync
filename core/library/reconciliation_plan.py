"""Pure file-to-release reconciliation from independent evidence layers.

This planner makes no file or database changes. A match is a hypothesis with
reasons, not permission to edit; callers only propose repairs for unambiguous
high-confidence pairs and keep the competing evidence in the finding.
"""

from __future__ import annotations

import os
import re
from difflib import SequenceMatcher
from typing import Any

from core.matching.assignment import assign_max_weight
from core.matching.audio_verification import fingerprint_is_ambiguous, normalize
from core.text.title_match import numeric_tokens_differ, recording_version_markers, titles_plausibly_same


_LEADING_POSITION = re.compile(r'^\s*(?:\d+[-_. ]+){1,2}')


def _title_score(a: str, b: str) -> float:
    left, right = normalize(a), normalize(b)
    if not left or not right:
        return 0.0
    ratio = SequenceMatcher(None, left, right).ratio()
    if ratio < 0.78 or not titles_plausibly_same(left, right, ratio):
        return 0.0
    if numeric_tokens_differ(a, b):
        return 0.0
    left_versions, right_versions = recording_version_markers(a), recording_version_markers(b)
    if left_versions != right_versions:
        return 0.0
    return ratio


def _filename_title(path: str) -> str:
    stem = os.path.splitext(os.path.basename(path))[0]
    return _LEADING_POSITION.sub('', stem)


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
    return recording_version_markers(f'({text})') if text else frozenset()


def _identity_text(value: object) -> str:
    folded = re.sub(r'[^\w\s]+', ' ', str(value or '').casefold())
    return re.sub(r'\s+', ' ', folded).strip()


def file_version_markers(file: dict) -> frozenset[str]:
    """Known performance/version markers from independent local layers."""
    tags = file.get('tags') or {}
    row = file.get('db') or {}
    markers = set(recording_version_markers(str(tags.get('title') or '')))
    markers.update(recording_version_markers(_filename_title(str(file.get('path') or ''))))
    markers.update(recording_version_markers(str(row.get('title') or '')))
    markers.update(_disambiguation_markers(row.get('recording_disambiguation')))
    return frozenset(markers)


def slot_version_markers(slot: dict) -> frozenset[str]:
    markers = set(recording_version_markers(
        str(slot.get('title') or slot.get('name') or '')))
    markers.update(_disambiguation_markers(slot.get('recording_disambiguation')))
    return frozenset(markers)


def versions_compatible(file: dict, slot: dict) -> bool:
    """Require symmetric version evidence, including stored MB comments."""
    if file_version_markers(file) != slot_version_markers(slot):
        return False
    file_disambiguation = str(
        (file.get('db') or {}).get('recording_disambiguation') or '').strip()
    slot_disambiguation = str(slot.get('recording_disambiguation') or '').strip()
    return not (file_disambiguation and slot_disambiguation) or (
        _identity_text(file_disambiguation) == _identity_text(slot_disambiguation)
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
    return not slot_artist or not recording_artist or _title_score(recording_artist, slot_artist) >= 0.75


def _duration_delta_ms(file: dict, slot: dict) -> int | None:
    tags = file.get('tags') or {}
    file_duration = int(tags.get('duration_ms') or file.get('duration_ms') or 0)
    slot_duration = int(slot.get('duration_ms') or 0)
    if not file_duration or not slot_duration:
        return None
    return abs(file_duration - slot_duration)


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


def _pair_weight(file: dict, slot: dict, slots: list[dict] | None = None) -> tuple[int, list[str]]:
    tags = file.get('tags') or {}
    evidence = file.get('acoustid') or {}
    recordings = evidence.get('recordings') or []
    slot_title = str(slot.get('title') or slot.get('name') or '')
    slot_artist = str(slot.get('artist') or '')
    reasons: list[str] = []
    fingerprint_weight = 0
    strong_recordings = [recording for recording in recordings
                         if float(recording.get('score') or 0) >= 0.90]
    if not versions_compatible(file, slot):
        local_versions = file_version_markers(file)
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
        if local_versions or local_disambiguation or not exact_audio_identity:
            return 0, []
    if strong_recordings and all(
        recording.get('mbid') != _recording_id(slot)
        and _title_score(str(recording.get('title') or ''), slot_title) < 0.83
        for recording in strong_recordings
    ):
        # A convincing fingerprint for another song must not become a match
        # merely because both the filename and embedded tags are wrong.
        return 0, []
    current_delta = _duration_delta_ms(file, slot)
    if current_delta is not None and current_delta > 30000 and slots:
        # A tied AcoustID result can link one fingerprint to several recordings.
        # If this slot is wildly wrong for the measured audio length while a
        # different fingerprint-supported slot fits closely, do not let stale
        # tags/filename/MBID consume the wrong slot. The joint assignment can
        # then expose the real missing slot and duplicate/unassigned file.
        better_supported_slot = any(
            other is not slot
            and (_duration_delta_ms(file, other) or 0) <= 3000
            and any(_recording_supports_slot(recording, other)
                    for recording in strong_recordings)
            for other in slots
            if _duration_delta_ms(file, other) is not None
        )
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
            elif _recording_supports_slot(recording, slot):
                fingerprint_weight = max(fingerprint_weight, int(5_000 * score))
                reasons = ['AcoustID recording title matches release slot']

    tag_title = _title_score(str(tags.get('title') or ''), slot_title)
    filename_title = _title_score(_filename_title(str(file.get('path') or '')), slot_title)
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

    delta = _duration_delta_ms(file, slot)
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


def reconcile_folder(files: list[dict], slots: list[dict]) -> dict[str, Any]:
    """Assign every file at most one slot and explain uncertain leftovers."""
    if not files:
        return {'pairs': [], 'extra_files': [], 'competing_files': {},
                'missing_slots': list(range(len(slots)))}
    weighted = [[_pair_weight(file, slot, slots) for slot in slots] for file in files]
    assigned = assign_max_weight([[weight for weight, _ in row] for row in weighted])
    pairs = []
    for file_index, slot_index in assigned:
        weight, reasons = weighted[file_index][slot_index]
        evidence = files[file_index].get('acoustid') or {}
        # A top-scored fingerprint spanning different songs cannot authorize a
        # confident repair even when one candidate names the release slot.
        recordings = evidence.get('recordings') or []
        ambiguous_fingerprint = fingerprint_is_ambiguous(recordings)
        slot = slots[slot_index]
        tags = files[file_index].get('tags') or {}
        top_score = max((float(recording.get('score') or 0)
                         for recording in recordings), default=0)
        top_supports_slot = any(
            float(recording.get('score') or 0) >= top_score
            and _recording_supports_slot(recording, slot)
            for recording in recordings
        )
        corroborating_metadata = sum((
            _title_score(str(tags.get('title') or ''), str(slot.get('title') or '')) >= 0.83,
            _title_score(_filename_title(str(files[file_index].get('path') or '')),
                         str(slot.get('title') or '')) >= 0.83,
            bool(tags.get('mbid') and _recording_id(slot)
                 and tags['mbid'] == _recording_id(slot)),
        ))
        assigned_duration_delta = _duration_delta_ms(files[file_index], slot)
        top_recordings = [
            recording for recording in recordings
            if float(recording.get('score') or 0) >= top_score
        ]
        competing_release_slot = any(
            not _recording_supports_slot(recording, slot)
            and any(
                other_index != slot_index
                and _recording_supports_slot(recording, other)
                and ((_duration_delta_ms(files[file_index], other) is None) or
                     _duration_delta_ms(files[file_index], other) <= 15000)
                for other_index, other in enumerate(slots)
            )
            for recording in top_recordings
        )
        fingerprint_context_resolves = (
            ambiguous_fingerprint and top_supports_slot
            and not competing_release_slot
            and assigned_duration_delta is not None and assigned_duration_delta <= 3000
            and corroborating_metadata >= 2
        )
        ambiguous = ambiguous_fingerprint and not fingerprint_context_resolves
        alternatives = sorted((w for j, (w, _) in enumerate(weighted[file_index])
                               if j != slot_index), reverse=True)
        close_alternative = bool(alternatives and alternatives[0] >= weight * 0.92)
        # A duplicate copy can have a poorer filename or stale tags while
        # sharing the same strong AcoustID identity. Leave room for those
        # weaker metadata layers before declaring either copy the keeper.
        competing_file = any(
            other_index != file_index and weighted[other_index][slot_index][0] >= weight * 0.80
            for other_index in range(len(files))
        )
        ambiguity_reasons = []
        if ambiguous:
            ambiguity_reasons.append(
                'AcoustID returned competing identities not resolved by the other evidence')
        if close_alternative:
            ambiguity_reasons.append('another release track fits nearly as well')
        if competing_file:
            ambiguity_reasons.append('another local file fits this release track')
        pairs.append({
            'file_index': file_index, 'slot_index': slot_index,
            'score': weight, 'reasons': reasons,
            'duration_delta_ms': assigned_duration_delta,
            'ambiguity_reasons': ambiguity_reasons,
            'confidence': 'ambiguous' if ambiguous or close_alternative or competing_file
                          else 'clear' if weight >= 4500 else 'likely',
        })
    used_files = {pair['file_index'] for pair in pairs}
    used_slots = {pair['slot_index'] for pair in pairs}
    extra_files = [i for i in range(len(files)) if i not in used_files]
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
        'missing_slots': [i for i in range(len(slots)) if i not in used_slots],
    }
