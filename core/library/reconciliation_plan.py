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
from core.text.fold import fold_title
from core.text.title_match import numeric_tokens_differ, recording_version_markers, titles_plausibly_same


_LEADING_POSITION = re.compile(r'^\s*(?:\d+[-_. ]+){1,2}')
_EDITORIAL_TITLE_SUFFIX = re.compile(
    r'\s*[\(\[]\s*(?:bonus\s+track|bonus)\s*[\)\]]\s*$', re.IGNORECASE)


def _version_markers(value: str) -> frozenset[str]:
    """Fold equivalent catalogue wording without erasing real versions."""
    markers = set(recording_version_markers(value))
    if markers.intersection({'alt', 'alternate', 'alternative'}):
        markers.difference_update({'alt', 'alternate', 'alternative'})
        markers.add('alternate')
    if 'acoustic' in markers:
        # Catalogues commonly call the same acoustic take an acoustic session.
        markers.difference_update({'session', 'sessions'})
    return frozenset(markers)


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
    return _artist_credit_supports(recording_artist, slot_artist)


def _artist_credit_supports(recording_artist: str, slot_artist: str) -> bool:
    """Accept a release artist contained in a fuller recording credit.

    AcoustID/MusicBrainz often returns the complete collaboration while a
    release slot carries the album artist.  ``Bastille & Hans Zimmer`` is
    positive evidence for a Bastille slot; treating it as a different artist
    leaves an otherwise exact title/duration match needlessly ambiguous.
    """
    if not slot_artist or not recording_artist:
        return True
    if _title_score(recording_artist, slot_artist) >= 0.75:
        return True
    recording = f' {_identity_text(recording_artist)} '
    expected = f' {_identity_text(slot_artist)} '
    return len(expected.strip()) >= 3 and expected in recording


def audio_contradicts_local_title(file: dict) -> bool:
    """Whether strong fingerprint identities all name different recordings."""
    title = str((file.get('tags') or {}).get('title') or
                (file.get('db') or {}).get('title') or '')
    recordings = (file.get('acoustid') or {}).get('recordings') or []
    strong = [recording for recording in recordings
              if (float(recording.get('score') or 0) >= 0.90 and
                  str(recording.get('title') or '').strip())]
    return bool(title and strong) and all(
        _title_score(str(recording.get('title') or ''), title) < 0.83
        and not _same_base_recording_title(recording.get('title'), title)
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
    return bool(a and b and a == b)


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
        and not _same_base_recording_title(recording.get('title'), slot_title)
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
            elif _same_base_recording_title(recording.get('title'), slot_title):
                # The local title and slot already passed the symmetric
                # version check above. A bare AcoustID recording label is
                # therefore positive audio evidence, rather than merely an
                # exception to the wrong-audio veto.
                fingerprint_weight = max(fingerprint_weight, int(5_000 * score))
                reasons = ['AcoustID base recording title matches release slot']

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


def reconcile_folder(files: list[dict], slots: list[dict],
                     forced_pairs: dict[int, int] | None = None) -> dict[str, Any]:
    """Assign every file at most one slot and explain uncertain leftovers."""
    if not files:
        return {'pairs': [], 'extra_files': [], 'competing_files': {},
                'missing_slots': list(range(len(slots)))}
    weighted = [[_pair_weight(file, slot, slots) for slot in slots] for file in files]
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
    pairs = []
    for file_index, slot_index in assigned:
        weight, reasons = weighted[file_index][slot_index]
        evidence = files[file_index].get('acoustid') or {}
        # A top-scored fingerprint spanning different songs cannot authorize a
        # confident repair even when one candidate names the release slot.
        recordings = evidence.get('recordings') or []
        slot = slots[slot_index]
        tags = files[file_index].get('tags') or {}
        top_score = max((float(recording.get('score') or 0)
                         for recording in recordings), default=0)
        top_recordings = [
            recording for recording in recordings
            if float(recording.get('score') or 0) >= top_score
        ]
        # Multiple MusicBrainz rows can describe the same named recording.
        # If every top-scored row supports this exact album slot, differences
        # such as "Angeleyes" / "Angel Eyes" are catalogue aliases rather
        # than competing audio identities.
        one_slot_identity = bool(top_recordings) and all(
            _recording_supports_slot(recording, slot)
            for recording in top_recordings
        )
        ambiguous_fingerprint = (
            fingerprint_is_ambiguous(recordings) and not one_slot_identity)
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
        def unresolved_alternative(
            other_index: int, other: dict, *, assigned_slot_index=slot_index,
            candidate_file_index=file_index, candidate_recordings=top_recordings,
        ) -> bool:
            if other_index == assigned_slot_index:
                return False
            delta = _duration_delta_ms(files[candidate_file_index], other)
            if delta is not None and delta > 15000:
                return False
            if not any(_recording_supports_slot(recording, other)
                       for recording in candidate_recordings):
                return False
            owner_index = assigned_by_slot.get(other_index)
            if owner_index is None or owner_index == candidate_file_index:
                return True
            owner = files[owner_index]
            owner_tags = owner.get('tags') or {}
            owner_support = sum((
                _title_score(str(owner_tags.get('title') or ''),
                             str(other.get('title') or '')) >= 0.83,
                _title_score(_filename_title(str(owner.get('path') or '')),
                             str(other.get('title') or '')) >= 0.83,
                bool(owner_tags.get('mbid') and _recording_id(other)
                     and owner_tags['mbid'] == _recording_id(other)),
            ))
            owner_delta = _duration_delta_ms(owner, other)
            owner_weight = weighted[owner_index][other_index][0]
            contender_weight = weighted[candidate_file_index][other_index][0]
            owner_recordings = (owner.get('acoustid') or {}).get('recordings') or []
            owner_top_score = max((float(recording.get('score') or 0)
                                   for recording in owner_recordings), default=0)
            owner_audio_supports_slot = any(
                float(recording.get('score') or 0) >= owner_top_score
                and _recording_supports_slot(recording, other)
                for recording in owner_recordings
            )
            # The alternative identity is already accounted for when another
            # local file independently names that slot, fits its duration, and
            # scores at least as well. The album-level one-to-one assignment is
            # then stronger evidence than an AcoustID many-to-many link.
            return not (
                owner_support >= 2
                and owner_audio_supports_slot
                and (owner_delta is None or owner_delta <= 3000)
                and owner_weight >= contender_weight
            )

        competing_release_slot = any(
            unresolved_alternative(other_index, other)
            for other_index, other in enumerate(slots)
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
            owner_slot = slots[owner_slot_index]
            owner = files[other_index]
            owner_tags = owner.get('tags') or {}
            owner_support = sum((
                _title_score(str(owner_tags.get('title') or ''),
                             str(owner_slot.get('title') or '')) >= 0.83,
                _title_score(_filename_title(str(owner.get('path') or '')),
                             str(owner_slot.get('title') or '')) >= 0.83,
                bool(owner_tags.get('mbid') and _recording_id(owner_slot)
                     and owner_tags['mbid'] == _recording_id(owner_slot)),
            ))
            owner_recordings = (owner.get('acoustid') or {}).get('recordings') or []
            owner_audio_supports_slot = any(
                _recording_supports_slot(recording, owner_slot)
                for recording in owner_recordings
                if float(recording.get('score') or 0) >= 0.80
            )
            owner_delta = _duration_delta_ms(owner, owner_slot)
            owner_weight = weighted[other_index][owner_slot_index][0]
            # A noisy many-to-many fingerprint link does not make this file a
            # real rival once its title, filename, duration, and audio already
            # place it more strongly in a different release slot.
            return not (
                owner_support >= 2
                and owner_audio_supports_slot
                and (owner_delta is None or owner_delta <= 3000)
                and owner_weight >= contender_weight
            )

        competing_file = any(
            unresolved_competing_file(other_index)
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
            'confidence': 'manual' if forced_pairs.get(file_index) == slot_index else
                          'ambiguous' if ambiguous or close_alternative or competing_file
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
                if _title_score(extra_title, str(neighbor.get('title') or '')) < 0.83:
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
