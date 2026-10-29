"""Apply upstream edition preference to automatic native monitoring only."""
from collections import defaultdict
import json

from core.edition_grouping import edition_group_key, reduce_edition_group, EDITION_PREFERENCE_VALUES


def select_automatic_album_ids(conn, album_ids, config_manager=None, *, profile_id=1):
    ids = list(dict.fromkeys(int(i) for i in album_ids))
    if not ids:
        return []
    if config_manager is None:
        from core.settings import config_manager
    preference = config_manager.get('watchlist.edition_preference', 'all')
    if preference not in EDITION_PREFERENCE_VALUES:
        preference = 'all'
    artists = {r[0] for r in conn.execute('SELECT primary_artist_id FROM lib2_albums WHERE id IN (SELECT value FROM json_each(?))', (json.dumps(ids),))}
    groups = defaultdict(list)
    for r in conn.execute("SELECT al.id, al.primary_artist_id, al.title, al.album_type, COALESCE(al.expected_track_count,al.track_count,0) AS track_count, al.explicit, al.canonical_locked, rule.monitored AS explicit_monitored FROM lib2_albums al LEFT JOIN lib2_monitor_rules rule ON rule.entity_type='album' AND rule.entity_id=al.id AND rule.profile_id=? AND rule.provenance='user_explicit' WHERE al.primary_artist_id IN (SELECT value FROM json_each(?))", (profile_id, json.dumps(sorted(artists)))):
        if r['primary_artist_id'] in artists:
            groups[(r['primary_artist_id'], r['album_type'], edition_group_key(r['title']))].append(dict(r))
    chosen = set()
    for rows in groups.values():
        eligible = [r for r in rows if r['explicit_monitored'] != 0]
        explicit = [r for r in eligible if r['explicit_monitored'] == 1 or r['canonical_locked']]
        selected = eligible if preference == 'all' else (explicit or reduce_edition_group(eligible, preference))
        chosen.update(r['id'] for r in selected)
    return [i for i in ids if i in chosen]
