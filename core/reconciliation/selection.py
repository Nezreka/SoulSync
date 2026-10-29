"""Candidate scoring, edition equivalence and release selection policy."""
from __future__ import annotations

from core.matching.audio_verification import fingerprint_is_ambiguous, normalize
from core.metadata.album_tracks import get_album_for_source, get_album_tracks_for_source
from core.reconciliation.candidates import ScoredCandidate
from core.reconciliation.display import _album_titles_compatible
from core.reconciliation.grouping import _album_identity_title
from core.reconciliation.match_cache import cached_match
from core.reconciliation.plan import (
    _title_score,
    _version_markers,
    file_version_markers,
    reconcile_folder,
    semantic_title_key,
    slot_version_markers,
)
from core.repair_jobs.base import JobContext
from core.text.fold import fold_title
from utils.logging_config import get_logger

logger = get_logger('reconciliation.selection')


def _track_items(data) -> list[dict]:
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        items = data.get('items') or data.get('tracks') or []
        if isinstance(items, dict):
            items = items.get('items') or []
        return [row for row in items if isinstance(row, dict)]
    return []



def _normalize_slot(item: dict, source: str) -> dict:
    def positive_int(value, default=None):
        try:
            number = int(value)
            return number if number > 0 else default
        except (TypeError, ValueError):
            return default

    artists = item.get('artists') or []
    artist_names = [str(a.get('name')).strip() for a in artists
                    if isinstance(a, dict) and str(a.get('name') or '').strip()]
    artist_names.extend(str(a).strip() for a in artists
                        if not isinstance(a, dict) and str(a or '').strip())
    artist_names = list(dict.fromkeys(artist_names))
    artist = ' & '.join(artist_names)
    return {
        'title': item.get('name') or item.get('title') or '',
        'artist': artist or '',
        'artist_names': artist_names,
        'track_number': positive_int(item.get('track_number')),
        'disc_number': positive_int(item.get('disc_number'), 1),
        'medium_format': item.get('medium_format') or '',
        'release_track_id': item.get('release_track_id') or '',
        'duration_ms': positive_int(item.get('duration_ms'), 0),
        'preview_url': item.get('preview_url') or item.get('previewUrl') or '',
        'source_track_id': item.get('id') or '',
        'recording_mbid': item.get('id') if source == 'musicbrainz' else '',
        'recording_disambiguation': (
            str(item.get('disambiguation') or '').strip()
            if source == 'musicbrainz' else ''
        ),
    }



def _match_release(files, slots, *, source, release_id, forced_pairs=None, catalogue_review=False):
    plan = (reconcile_folder(files, slots, forced_pairs=forced_pairs, catalogue_review=True)
            if catalogue_review else cached_match(files, slots, source=source, release_id=release_id,
                        forced_pairs=forced_pairs, compute=reconcile_folder))
    # The cached solver retains a complete partition. Intentional companions
    # share their source's assignment without consuming another release slot.
    assigned_paths = {files[pair['file_index']]['path'] for pair in plan['pairs']}
    return {**plan, 'extra_files': [index for index in plan['extra_files']
                                   if files[index].get('companion_of') not in assigned_paths]}



def _review_pairs(files, plan):
    pairs_by_path = {files[pair['file_index']]['path']: pair for pair in plan['pairs']}
    return [*plan['pairs'], *[
        {**pairs_by_path[file['companion_of']], 'file_index': index}
        for index, file in enumerate(files)
        if file.get('companion_of') in pairs_by_path and file['path'] not in pairs_by_path]]



def _score_release_candidates(files: list[dict], candidates: list[tuple[str, str]],
                              release_cache: dict,
                              exact_musicbrainz_releases: set[str] | None = None, *,
                              audio_copy_release=None) -> list[tuple]:
    scored = []
    aliases = {fold_title(name): [row['artist_name'], *row['artist_aliases']]
               for file in files for row in [file.get('db') or {}]
               if row.get('artist_name') and row.get('artist_aliases')
               for name in [row['artist_name'], *row['artist_aliases']]}
    for source, release_id in candidates:
        audio_copy = audio_copy_release == (source, release_id)
        key = (source, release_id, 'audio_copy') if audio_copy else (source, release_id)
        if key not in release_cache:
            is_exact_release = (source == 'musicbrainz' and
                                release_id in (exact_musicbrainz_releases or set()))
            raw = (get_album_tracks_for_source(source, release_id, exact_release=True)
                   if is_exact_release else
                   get_album_tracks_for_source(source, release_id))
            items = _track_items(raw)
            excluded = 0 if audio_copy else sum(item.get('video') is True for item in items)
            release_cache[key] = [{**_normalize_slot(item, source),
                                   'excluded_video_tracks': excluded, **({'audio_copy_of_video': True} if audio_copy else {})}
                                  for item in items if audio_copy or item.get('video') is not True]
        slots = [{**slot, 'artist_aliases': aliases.get(fold_title(slot.get('artist') or ''), [])}
                 for slot in release_cache[key]] if aliases else release_cache[key]
        if not slots:
            continue
        plan = _match_release(files, slots, source=source, release_id=release_id)
        clear = sum(pair['confidence'] == 'clear' for pair in plan['pairs'])
        matched = len(plan['pairs'])
        scored.append(ScoredCandidate(clear, matched, -len(plan['extra_files']),
                       source, release_id, slots, plan))
    return scored



def _continuous_numbering(scored: tuple) -> int:
    """Describe an actual one-disc 1..N source tracklist for the review UI."""
    slots = scored[5]
    positions = [slot.get('track_number') for slot in slots]
    discs = {slot.get('disc_number') or 1 for slot in slots}
    return int(bool(slots) and discs == {1} and positions == list(range(1, len(slots) + 1)))



def _inferred_partial(files: list[dict], album: dict | None, watched: set[str], saved_intent) -> bool:
    """A lone playlist acquisition is not an instruction to collect its album."""
    return bool(not saved_intent and album and len(files) == 1
        and files[0].get('playlists')
        and str(album.get('artist_name') or '').strip().casefold() not in watched)



def _complete_collection_fit(files: list[dict], scored: tuple) -> int:
    """Prefer an exact tracklist only when the folder looks like a collection.

    This resolves a full 11-track album against an 11-track release instead of
    calling a 14-track deluxe edition an equal fit. A lone playlist track is
    deliberately excluded: its absent album siblings are acquisition intent,
    not evidence for the shortest release that happens to contain the song.
    """
    files = [file for file in files if not file.get('companion_of')]
    if len(files) <= 1 or (len(files) < 5 and any(file.get('playlists') for file in files)):
        return 0
    slots, plan = scored[5], scored[6]
    return int(not plan['extra_files'] and not plan['missing_slots'] and
               len(slots) == len(files))



def _distinctive_position_conflict(files: list[dict], selected: tuple,
                                   candidates: list[tuple]) -> bool:
    """Hold metadata when a plausible edition preserves a version's position.

    A short, completely matched release can otherwise win over a longer
    edition that puts a distinct live/remix file at its existing track number.
    The missing opener is an edition question, not proof that track 16 is #1.
    """
    selected_pairs = {pair['file_index']: selected[5][pair['slot_index']]
                      for pair in selected[6]['pairs']}
    for file_index, selected_slot in selected_pairs.items():
        file = files[file_index]
        tags = file.get('tags') or {}
        current_number = tags.get('track_number')
        markers = file_version_markers(file)
        local_key = semantic_title_key({'title': tags.get('title') or ''})[0]
        if not markers and local_key:
            recordings = (file.get('acoustid') or {}).get('recordings') or []
            best = max((float(row.get('score') or 0) for row in recordings), default=0)
            markers = frozenset().union(*(
                _version_markers(str(row.get('title') or ''))
                for row in recordings
                if float(row.get('score') or 0) >= best and
                semantic_title_key({'title': row.get('title') or ''})[0] == local_key
            ))
        if not markers or not current_number or current_number == selected_slot.get('track_number'):
            continue
        for candidate in candidates:
            if candidate is selected or candidate[0] < selected[0] - 1 or candidate[1] < selected[1] - 1:
                continue
            alternate = next((slot for slot in candidate[5]
                              if slot.get('track_number') == current_number and
                              semantic_title_key(slot)[0] == local_key and
                              slot_version_markers(slot) == markers), None)
            if alternate:
                return True
        if (current_number >= len(files) + 2 and
                not slot_version_markers(selected_slot)):
            # The catalog search may not contain that edition, but the
            # fingerprint still contradicts renumbering a versioned #16 to #1.
            return True
    return False



def _single_position_outlier(files: list[dict], scored: tuple) -> bool:
    """One high-numbered version can signal an edition beyond a complete short list."""
    for pair in scored[6]['pairs']:
        file = files[pair['file_index']]
        number = int((file.get('tags') or {}).get('track_number') or 0)
        slot_number = int(scored[5][pair['slot_index']].get('track_number') or 0)
        if number >= len(files) + 2 and number != slot_number:
            return True
    return False



def _candidate_rank(files: list[dict], scored: tuple, preferred_source: str = '') -> tuple:
    rank = (*scored[:3], _complete_collection_fit(files, scored))
    # A configured provider is a tie breaker after actual file coverage. It
    # must never outrank a release that identifies more audio or fewer extras.
    source_fit = int(bool(preferred_source) and scored[3] == preferred_source)
    return (*rank, source_fit)



def _promote_album_context_candidate(
        scored: list[tuple], context_candidates: list[tuple[str, str]]) -> None:
    """Prefer a title-derived candidate when one file has equal audio fit."""
    if not scored or not context_candidates:
        return
    best_audio_fit = scored[0][:3]
    contextual = next((row for candidate in context_candidates for row in scored
                       if row[:3] == best_audio_fit and
                       (row[3], row[4]) == candidate), None)
    if contextual is not None and contextual is not scored[0]:
        scored.remove(contextual)
        scored.insert(0, contextual)



def _single_file_album_context_conflict(album: dict | None,
                                        release_meta: dict | None) -> bool:
    """A lone recording cannot silently replace a differently named album."""
    current = str((album or {}).get('album_title') or '').strip()
    release = str((release_meta or {}).get('name') or
                  (release_meta or {}).get('title') or '').strip()
    if not current or not release or _album_titles_compatible(current, release):
        return False
    return _title_score(_album_identity_title(current),
                        _album_identity_title(release)) < 0.86



def _release_metadata(scored: tuple, cache: dict, exact_releases: set[str]) -> dict:
    source, release_id = scored[3], scored[4]
    key = (source, release_id)
    if key not in cache:
        exact = source == 'musicbrainz' and release_id in exact_releases
        cache[key] = ((get_album_for_source(source, release_id, exact_release=True)
                       if exact else get_album_for_source(source, release_id)) or {})
    return cache[key]



def _supported_current_edition(files, album, candidates):
    """Retain a coherent existing edition supported by every owned file."""
    if not album or album.get('canonical_locked'):
        return None
    identity = album.get('canonical_source'), str(album.get('canonical_album_id') or '')
    members = [file for file in files if not file.get('companion_of')]
    if identity[0] != 'musicbrainz' or not members or not all(
        (file.get('tags') or {}).get('musicbrainz_release_id') == identity[1]
        for file in members):
        return None
    return next((row for row in candidates if (row.source, row.release_id) == identity
                 and row.clear == row.matched == len(members)
                 and not row.plan['extra_files'] and not row.plan['missing_slots']), None)



def _edition_preference(meta: dict) -> tuple[int, int]:
    media = meta.get('media') or []
    formats = ' '.join(str(row.get('format') or '') for row in media
                       if isinstance(row, dict))
    formats += ' ' + str(meta.get('format') or '')
    country = str(meta.get('country') or '').casefold()
    digital = int(any(word in formats.casefold() for word in ('digital', 'download', 'stream')))
    international = int(country in ('xw', 'worldwide', 'international', 'world'))
    return digital, international



def _full_coverage_digital_alternative(selected: tuple, ranked: list[tuple],
                                       file_count: int, metadata) -> bool:
    """A near-equal digital edition should remain an explicit album choice."""
    clear, matched, _, _, _, _, plan = selected
    if (matched != file_count or
            plan['extra_files'] or plan['missing_slots'] or
            _edition_preference(metadata(selected))[0]):
        return False
    return any(
        row[3] == 'musicbrainz' and row[1] == matched and row[0] >= clear - 1 and
        not row[6]['extra_files'] and not row[6]['missing_slots'] and
        _edition_preference(metadata(row))[0]
        for row in ranked[1:6]
    )



def _has_unexplained_identified_extras(files: list[dict], scored: tuple) -> bool:
    """Whether a plausible album file fits no slot or competing local copy."""
    plan = scored[6]
    competing = plan.get('competing_files') or {}
    unexplained = [index for index in plan['extra_files'] if not competing.get(index)]
    if not unexplained or scored[1] == 0:
        return False
    for index in unexplained:
        recordings = (files[index].get('acoustid') or {}).get('recordings') or []
        if ((files[index].get('acoustid') or {}).get('status') != 'ok' or
                fingerprint_is_ambiguous(recordings) or
                max((float(row.get('score') or 0) for row in recordings), default=0) < 0.80):
            return False
    return True



def _systematic_numbering_drift(files: list[dict], scored: tuple) -> bool:
    _clear, matched, _extras, _source, _release_id, slots, plan = scored
    if matched < 2:
        return False
    changed = 0
    for pair in plan['pairs']:
        tags = files[pair['file_index']].get('tags') or {}
        slot = slots[pair['slot_index']]
        if (tags.get('track_number') != slot.get('track_number') or
                (tags.get('disc_number') or 1) != (slot.get('disc_number') or 1)):
            changed += 1
    return changed >= 2 and changed * 2 >= matched



def _release_group_candidates(release_id: str, files: list[dict], context: JobContext,
                              cache: dict[str, list[tuple[str, str]]]) -> list[tuple[str, str]]:
    """Find bounded alternate editions when shape/numbering says to look wider."""
    if release_id in cache:
        return cache[release_id]
    client = context.mb_client
    if not client:
        cache[release_id] = []
        return []
    try:
        release = client.get_release(release_id, includes=['release-groups']) or {}
        group_id = str((release.get('release-group') or {}).get('id') or '')
        if not group_id and hasattr(client, 'get_release_group'):
            group = client.get_release_group(release_id) or {}
            group_id = str(group.get('id') or '')
        releases = (client.browse_release_group_releases(group_id, limit=100)
                    if group_id else [])
    except Exception as exc:
        logger.debug('Could not expand release group for %s: %s', release_id, exc)
        releases = []

    def media_shape(row):
        media = row.get('media') or []
        count = 0
        for medium in media:
            if not isinstance(medium, dict):
                continue
            try:
                count += int(medium.get('track-count') or
                             len(medium.get('tracks') or []))
            except (TypeError, ValueError):
                continue
        digital = any('digital' in str(medium.get('format') or '').casefold()
                      for medium in media if isinstance(medium, dict))
        return count, len(media), digital

    wanted = len(files)
    releases = [row for row in releases if row.get('id') and row.get('id') != release_id]
    releases.sort(key=lambda row: (
        media_shape(row)[0] == wanted,
        media_shape(row)[2],
        str(row.get('country') or '').casefold() in ('xw', 'worldwide', 'international'),
        media_shape(row)[1] == 1,
        str(row.get('status') or '').casefold() == 'official',
    ), reverse=True)
    cache[release_id] = [('musicbrainz', str(row['id'])) for row in releases[:12]]
    return cache[release_id]



def _candidate_shape(scored: tuple, *, ignore_positions: bool = False) -> tuple:
    """The release facts reconciliation can distinguish, excluding edition ID."""
    _clear, _matched, _extras, _source, _release_id, slots, plan = scored

    def slot_shape(slot: dict) -> tuple:
        track_number = slot.get('track_number')
        return (0 if ignore_positions else slot.get('disc_number') or 1,
                False if ignore_positions else track_number is None,
                0 if ignore_positions else track_number or 0, semantic_title_key(slot),
                normalize(slot.get('recording_disambiguation') or ''))

    pairs = tuple(sorted(
        (pair['file_index'], *slot_shape(slots[pair['slot_index']]))
        for pair in plan['pairs']))
    missing = tuple(sorted(
        slot_shape(slots[index])
        for index in plan['missing_slots']))
    return len(slots), pairs, missing, tuple(sorted(plan['extra_files']))



def _candidate_shapes_equivalent(left: tuple, right: tuple, *, ignore_positions: bool = False) -> bool:
    """Compare release mappings without treating absent detail as conflict.

    MusicBrainz often supplies a recording comment or version marker that
    iTunes/Deezer omit. Missing provider detail is unknown, not evidence of a
    different edition. Two explicit, different markers or comments still keep
    the releases distinct. Ignoring positions is reserved for comparing the
    same audio across formats; competing layouts within one format stay distinct.
    """
    if left[3] == right[3] == 'musicbrainz':
        def recording_shape(candidate):
            slots, plan = candidate[5], candidate[6]
            if not slots or any(not slot.get('recording_mbid') for slot in slots):
                return None
            def identity(index):
                slot = slots[index]
                return (0 if ignore_positions else slot.get('disc_number') or 1,
                        0 if ignore_positions else slot.get('track_number') or 0,
                        slot['recording_mbid'])
            return (len(slots), tuple(sorted((pair['file_index'], *identity(pair['slot_index']))
                    for pair in plan['pairs'])),
                    tuple(sorted(identity(index) for index in plan['missing_slots'])),
                    tuple(sorted(plan['extra_files'])))
        identity = recording_shape(left)
        if identity is not None and identity == recording_shape(right):
            # Release-specific translations/arrangement labels may differ even
            # when every positioned recording and file assignment is identical.
            return True
    left_shape = _candidate_shape(left, ignore_positions=ignore_positions)
    right_shape = _candidate_shape(right, ignore_positions=ignore_positions)
    if left_shape[0] != right_shape[0] or left_shape[3] != right_shape[3]:
        return False

    def compatible_slot(a: tuple, b: tuple, prefix_length: int) -> bool:
        if a[:prefix_length] != b[:prefix_length]:
            return False
        left_title, right_title = a[prefix_length], b[prefix_length]
        # Provider spelling and capitalization differences do not define a
        # different edition when the positions, assignments, and version
        # markers agree. Numeric/version changes remain hard boundaries in
        # _title_score and the marker comparison below.
        if _title_score(left_title[0], right_title[0]) < 0.94:
            return False
        left_markers, right_markers = left_title[1], right_title[1]
        if left_markers and right_markers and left_markers != right_markers:
            return False
        left_comment, right_comment = a[prefix_length + 1], b[prefix_length + 1]
        return not left_comment or not right_comment or left_comment == right_comment

    left_pairs = sorted(left_shape[1], key=lambda row: (*row[:4], row[4][0]))
    right_pairs = sorted(right_shape[1], key=lambda row: (*row[:4], row[4][0]))
    if (len(left_pairs) != len(right_pairs) or
            any(not compatible_slot(a, b, 4)
                for a, b in zip(left_pairs, right_pairs, strict=True))):
        return False
    left_missing = sorted(left_shape[2], key=lambda row: (*row[:3], row[3][0]))
    right_missing = sorted(right_shape[2], key=lambda row: (*row[:3], row[3][0]))
    return (len(left_missing) == len(right_missing) and
            all(compatible_slot(a, b, 3)
                for a, b in zip(left_missing, right_missing, strict=True)))



def _owned_candidate(candidate: tuple) -> ScoredCandidate:
    """Compare partial acquisitions using only the recordings actually owned."""
    clear, matched, extras, source, release, slots, plan = candidate
    pairs = sorted(plan['pairs'], key=lambda pair: pair['file_index'])
    return ScoredCandidate(clear, matched, extras, source, release,
        [slots[pair['slot_index']] for pair in pairs],
        {**plan, 'pairs': [{**pair, 'slot_index': index} for index, pair in enumerate(pairs)],
         'missing_slots': []})



def _owned_candidates_equivalent(left: tuple, right: tuple) -> bool:
    first, second = _owned_candidate(left), _owned_candidate(right)
    if first.source == second.source == 'musicbrainz':
        for a, b in zip(first.slots, second.slots):
            if a.get('recording_mbid') and b.get('recording_mbid') and a['recording_mbid'] != b['recording_mbid']:
                return False
    return _candidate_shapes_equivalent(first, second)



def _retain_partial_release(files, album, scored, saved_intent, watched, metadata):
    if not album or album.get('canonical_locked'):
        return False
    current = next((row for row in scored if (row[3], row[4]) ==
                    (album.get('canonical_source'), str(album.get('canonical_album_id') or ''))), None)
    partial = (saved_intent and saved_intent[0] == 'partial' and not saved_intent[3]
               and current and saved_intent[1:3] == (current[3], current[4]))
    if not partial and not _inferred_partial(files, album, watched, saved_intent):
        return False
    if (not current or current[:3] != scored[0][:3] or current[6]['extra_files']
            or any(pair['confidence'] not in ('clear', 'manual') for pair in current[6]['pairs'])
            or _single_file_album_context_conflict(album, metadata(current))):
        return False
    if not all(_owned_candidates_equivalent(current, row)
               for row in scored if row[:3] == current[:3]):
        return False
    scored.remove(current)
    scored.insert(0, current)
    return True



def _candidate_difference(selected: tuple, other: tuple) -> str:
    """Explain the actual mapping difference without exposing ranking scores."""
    first, second = ScoredCandidate(*selected), ScoredCandidate(*other)
    left = {pair['file_index']: first.slots[pair['slot_index']] for pair in first.plan['pairs']}
    right = {pair['file_index']: second.slots[pair['slot_index']] for pair in second.plan['pairs']}
    if left.keys() != right.keys() or first.plan['extra_files'] != second.plan['extra_files']:
        return 'Different local files are assigned or remain unmatched.'
    if _candidate_shapes_equivalent(first, second):
        if first.clear != second.clear:
            return 'The same track mapping has different assignment confidence.'
        return 'The same track mapping; catalogue edition details differ.'
    for index, slot in left.items():
        alternate = right[index]
        label = slot.get('title') or 'An owned track'
        if (slot.get('disc_number') or 1, slot.get('track_number')) != (
                alternate.get('disc_number') or 1, alternate.get('track_number')):
            return (f'{label}: disc {slot.get("disc_number") or 1}, track {slot.get("track_number")} '
                    f'→ disc {alternate.get("disc_number") or 1}, track {alternate.get("track_number")}.')
        def single(item, source):
            return ScoredCandidate(1, 1, 0, source, '', [item],
                {'pairs': [{'file_index': 0, 'slot_index': 0}], 'missing_slots': [], 'extra_files': []})
        if not _candidate_shapes_equivalent(single(slot, first.source), single(alternate, second.source)):
            return f'{label}: a different recording or version is proposed.'
    if _owned_candidates_equivalent(first, second):
        return 'Only unowned release tracks differ.'
    return 'The release tracklist or assignment evidence differs.'



def _review_candidates(selected: tuple, ranked: list[tuple], blockers=()) -> list[tuple]:
    """Keep the selected edition and the actual conflicting alternatives visible."""
    result = [selected]
    identities = {(selected[3], selected[4])}
    for candidate in [*blockers, *ranked]:
        identity = (candidate[3], candidate[4])
        if identity in identities:
            continue
        if len(result) >= 6:
            break
        # Spend the first slots on distinct outcomes. Equivalent catalogue
        # editions are filled afterward, if the six-card limit allows them.
        if candidate in blockers or not any(_candidate_shapes_equivalent(candidate, row) for row in result):
            result.append(candidate)
            identities.add(identity)
    for candidate in ranked:
        if len(result) >= 6:
            break
        if (candidate[3], candidate[4]) not in identities:
            result.append(candidate)
            identities.add((candidate[3], candidate[4]))
    return result

