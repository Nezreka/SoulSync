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


def _pair_weight(file: dict, slot: dict) -> tuple[int, list[str]]:
    tags = file.get('tags') or {}
    evidence = file.get('acoustid') or {}
    recordings = evidence.get('recordings') or []
    slot_title = str(slot.get('title') or slot.get('name') or '')
    slot_artist = str(slot.get('artist') or '')
    reasons: list[str] = []
    fingerprint_weight = 0
    strong_recordings = [recording for recording in recordings
                         if float(recording.get('score') or 0) >= 0.90]
    if strong_recordings and all(
        recording.get('mbid') != _recording_id(slot)
        and _title_score(str(recording.get('title') or ''), slot_title) < 0.83
        for recording in strong_recordings
    ):
        # A convincing fingerprint for another song must not become a match
        # merely because both the filename and embedded tags are wrong.
        return 0, []
    if evidence.get('status') == 'ok':
        for recording in recordings:
            score = float(recording.get('score') or 0)
            if score < 0.80:
                continue
            if _recording_id(slot) and recording.get('mbid') == _recording_id(slot):
                fingerprint_weight = max(fingerprint_weight, int(10_000 * score))
                reasons = ['AcoustID recording ID matches release slot']
            elif _title_score(str(recording.get('title') or ''), slot_title) >= 0.88:
                artist = str(recording.get('artist') or '')
                if not slot_artist or not artist or _title_score(artist, slot_artist) >= 0.75:
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

    file_duration = int(tags.get('duration_ms') or file.get('duration_ms') or 0)
    slot_duration = int(slot.get('duration_ms') or 0)
    if file_duration and slot_duration:
        delta = abs(file_duration - slot_duration)
        if delta <= 3000:
            weight += 500
            reasons.append(f'duration differs by {delta / 1000:.2f}s')
        elif delta <= 15000:
            weight += 100
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
    weighted = [[_pair_weight(file, slot) for slot in slots] for file in files]
    assigned = assign_max_weight([[weight for weight, _ in row] for row in weighted])
    pairs = []
    for file_index, slot_index in assigned:
        weight, reasons = weighted[file_index][slot_index]
        evidence = files[file_index].get('acoustid') or {}
        # A top-scored fingerprint spanning different songs cannot authorize a
        # confident repair even when one candidate names the release slot.
        ambiguous = fingerprint_is_ambiguous(evidence.get('recordings') or [])
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
        pairs.append({
            'file_index': file_index, 'slot_index': slot_index,
            'score': weight, 'reasons': reasons,
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
