"""Conservative, read-only standard release planning; never pins a canonical ID."""
import re

from core.edition_grouping import edition_group_key, reduce_edition_group
from core.metadata.album_tracks import (
    get_album_for_source, get_album_tracks_for_source, get_artist_albums_for_source,
)

_EXTENDED = re.compile(r'\b(deluxe|expanded|anniversary|bonus|extended|collector|special|legacy|ultimate|limited|tour)\b', re.I)
_REGIONAL = re.compile(r'\b(japan(?:ese)?|regional|international|uk|us|european|australian)\s+(?:edition|version)\b', re.I)


def _get(item, key, default=None):
    return item.get(key, default) if isinstance(item, dict) else getattr(item, key, default)


def _title(item):
    return _get(item, 'name') or _get(item, 'title') or ''


def _norm(value):
    return re.sub(r'[^\w]+', ' ', str(value or '').casefold()).strip()


def _items(response):
    if isinstance(response, list):
        return response
    if isinstance(response, dict):
        value = response.get('items') or response.get('tracks') or []
        return value.get('items', []) if isinstance(value, dict) else value
    return []


def plan_standard_album(row, local_tracks, source, *, prefer_explicit=True,
                        fetch_album=None, fetch_tracks=None, fetch_releases=None):
    """Require an exact artist/title, full tracklists and consensus between editions.

    Canonical IDs and cached/local counts are deliberately not evidence of a
    standard edition. Discovery uses the existing provider artist catalogue.
    Missing/partial catalogues and conflicting regional tracklists fail closed.
    """
    fetch_album = fetch_album or get_album_for_source
    fetch_tracks = fetch_tracks or get_album_tracks_for_source
    fetch_releases = fetch_releases or get_artist_albums_for_source
    plan = {'artist': row['artist_name'], 'album': row['album_title'],
            'source': source, 'status': 'skipped', 'reason': '',
            'existing_tracks': [], 'missing_tracks': [], 'proposed_downloads': []}

    def skip(reason):
        plan['reason'] = reason
        return plan

    try:
        releases = fetch_releases(source, '', row['artist_name'], album_type='album', limit=50)
        if not releases or len(releases) >= 50:
            return skip('Provider catalogue unavailable or potentially truncated')
        group = [r for r in releases if edition_group_key(_title(r)) == edition_group_key(row['album_title'])]
        candidates = []
        for release in group:
            name = _title(release)
            if _EXTENDED.search(name):
                continue
            if _REGIONAL.search(name):
                return skip('Ambiguous regional release')
            aid = _get(release, 'id') or _get(release, 'album_id')
            if not aid:
                return skip('Release identifier unavailable')
            meta = fetch_album(source, str(aid))
            if not meta or _norm(_title(meta)) != _norm(name):
                return skip('Release metadata unavailable or conflicting')
            if str(meta.get('id') or aid) != str(aid) or meta.get('album_type', 'album') != 'album':
                return skip('Release identity/type conflicts with catalogue')
            artists = meta.get('artists') or []
            names = [_get(a, 'name', a if isinstance(a, str) else '') for a in artists]
            artist = meta.get('artist') or meta.get('artist_name')
            if isinstance(artist, dict):
                artist = artist.get('name')
            if artist:
                names.append(artist)
            if _norm(row['artist_name']) not in {_norm(n) for n in names}:
                return skip('Release artist cannot be verified')
            tracks = _items(fetch_tracks(source, str(aid)))
            expected = meta.get('total_tracks') or meta.get('track_count') or meta.get('nb_tracks')
            if not expected or int(expected) != len(tracks) or not tracks:
                return skip('Full expected tracklist unavailable')
            slots = [(t.get('disc_number', 1), t.get('track_number')) for t in tracks]
            if (len(set(slots)) != len(tracks) or any(not t.get('id') or not _title(t) or not t.get('track_number') for t in tracks)):
                return skip('Incomplete or ambiguous track metadata')
            discs = sorted({t.get('disc_number', 1) for t in tracks})
            if discs != list(range(1, len(discs) + 1)):
                return skip('Non-contiguous disc metadata')
            for disc in discs:
                numbers = sorted(t['track_number'] for t in tracks if t.get('disc_number', 1) == disc)
                if numbers != list(range(1, len(numbers) + 1)):
                    return skip('Non-contiguous tracklist')
            tracks = sorted(tracks, key=lambda t: (t.get('disc_number', 1), t['track_number']))
            candidates.append({'album_id': str(aid), 'name': name, 'tracks': tracks,
                               'track_count': len(tracks), 'explicit': meta.get('explicit', False) or any(t.get('explicit') for t in tracks)})
        if not candidates:
            return skip('No verified standard release found')
        # Do not equate "shortest" with "standard". Every unlabelled standard
        # candidate must agree on the ordered songs, including remasters.
        signatures = {tuple(_norm(_title(t)) for t in c['tracks']) for c in candidates}
        if len(signatures) != 1:
            return skip('Conflicting standard/remaster/regional tracklists')
        originals = [c for c in candidates if not re.search(r'\bremaster(?:ed)?\b', c['name'], re.I)]
        if not originals:
            return skip('Remaster-only catalogue cannot verify original standard tracklist')
        selected = reduce_edition_group(originals, 'one_standard', prefer_explicit)[0]
        tracks = selected['tracks']
        owned, used = [], set()
        for track in tracks:
            matches = []
            for i, local in enumerate(local_tracks):
                if i in used:
                    continue
                if _norm(_title(track)) != _norm(local.get('title')):
                    if _norm(edition_group_key(_title(track))) == _norm(edition_group_key(local.get('title'))):
                        return skip('Local song edition qualifier needs review before downloading')
                    continue
                rd = track.get('duration_ms') or (track.get('duration', 0) * 1000)
                ld = local.get('duration_ms', 0)
                if not rd or not ld:
                    return skip('Local/reference duration unavailable for matching song')
                if abs(float(rd) - float(ld)) > 5000:
                    return skip('Local song version conflicts with standard track duration')
                matches.append(i)
            if matches:
                used.add(matches[0])
                owned.append(track)
        missing = [t for t in tracks if t not in owned]
        plan.update(status='ready', release=selected['name'], album_id=selected['album_id'],
                    expected_track_count=len(tracks), existing_tracks=owned, missing_tracks=missing,
                    proposed_downloads=missing)
        return plan
    except Exception as exc:
        return skip(f'Metadata lookup failed: {type(exc).__name__}')


def enqueue_standard_plan(db, plan, *, profile_id=1):
    """Use the wishlist's persistent deduplication, download priorities and importer."""
    if plan['status'] != 'ready':
        return 0
    created = 0
    for track in plan['missing_tracks']:
        data = dict(track)
        data['standard_completion_per_track'] = True
        data['album'] = {'id': plan['album_id'], 'name': plan['release'],
                         'total_tracks': plan['expected_track_count'], 'album_type': 'album'}
        data['artists'] = track.get('artists') or [{'name': plan['artist']}]
        data['uri'] = f"{plan['source']}:track:{track['id']}"
        created += bool(db.add_to_wishlist(
            data, failure_reason='Missing standard album track', source_type='album',
            source_info={'source': plan['source'], 'source_track_id': track['id'],
                         'is_album': True, 'reason': 'standard_album_completion',
                         'album_title': plan['release'], 'artist': plan['artist'],
                         'track_number': track.get('track_number'), 'disc_number': track.get('disc_number', 1)},
            profile_id=profile_id))
    return created
