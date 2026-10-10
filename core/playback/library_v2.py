"""Native catalogue adapters for the existing playback/acquisition pipeline."""
from __future__ import annotations

from contextlib import closing

from core.library2.artist_aliases import resolve_alias_group
from core.library2.sql_util import intent_profile_id, monitored_sql, owner_clause
from core.library2.track_files import primary_order


def artist_queue_rows(conn, artist_id, *, page=1, limit=100):
    """One row per credited track, bounded to owned or wanted releases.

    Catalogue-only discography releases are never opted into acquisition by
    artist Play. A partially owned/wanted release includes its known siblings.
    File picks and intent use the same scope kernels as the library readers.
    """
    ids = resolve_alias_group(conn, artist_id)
    marks = ','.join('?' for _ in ids)
    owner = owner_clause(column='tf.owner_profile_id')
    intent = intent_profile_id()
    scope = f"""WITH eligible_albums AS (
        SELECT DISTINCT t.album_id FROM lib2_tracks t
        WHERE EXISTS (SELECT 1 FROM lib2_track_files tf WHERE tf.track_id=t.id
                      AND COALESCE(tf.file_state,'active')='active'
                      AND COALESCE(tf.path,'')<>''{owner})
           OR COALESCE((SELECT w.wanted FROM lib2_wanted_tracks w
                        WHERE w.track_id=t.id AND w.profile_id={intent}),
                       {monitored_sql('track', 't')})=1
    ), scope_tracks AS (
        SELECT t.id FROM lib2_tracks t JOIN lib2_albums al ON al.id=t.album_id
        WHERE t.album_id IN (SELECT album_id FROM eligible_albums)
          AND (al.primary_artist_id IN ({marks}) OR EXISTS (
              SELECT 1 FROM lib2_track_artists ta
              WHERE ta.track_id=t.id AND ta.artist_id IN ({marks})))
    ), ranked AS (
        SELECT tf.*, ROW_NUMBER() OVER (
            PARTITION BY tf.track_id ORDER BY {primary_order('tf')}) AS rank
        FROM lib2_track_files tf JOIN scope_tracks s ON s.id=tf.track_id
        WHERE COALESCE(tf.file_state,'active')='active'
          AND COALESCE(tf.path,'')<>''{owner}
    )"""
    params = [*ids, *ids]
    total = conn.execute(f'{scope} SELECT COUNT(*) FROM scope_tracks', params).fetchone()[0]
    rows = conn.execute(f"""{scope}
        SELECT r.id AS file_id, t.id AS track_id, t.title AS track_title,
               t.track_number, t.disc_number, t.duration,
               al.id AS album_id, al.title AS album_title, al.image_url AS album_image_url,
               ar.id AS artist_id, ar.name AS artist_name,
               COALESCE(r.path,'') AS path, r.format, r.bitrate,
               CASE WHEN r.id IS NULL THEN 'missing' ELSE 'active' END AS file_state,
               1 AS is_primary
        FROM scope_tracks s JOIN lib2_tracks t ON t.id=s.id
        JOIN lib2_albums al ON al.id=t.album_id
        LEFT JOIN ranked r ON r.track_id=t.id AND r.rank=1
        LEFT JOIN lib2_artists ar ON ar.id=COALESCE((
            SELECT ta.artist_id FROM lib2_track_artists ta WHERE ta.track_id=t.id
            ORDER BY CASE WHEN ta.role='primary' THEN 0 ELSE 1 END,
                     ta.position, ta.artist_id LIMIT 1), al.primary_artist_id)
        ORDER BY al.title, al.id, t.disc_number, t.track_number, t.id
        LIMIT ? OFFSET ?""", [*params, limit, (page-1)*limit]).fetchall()
    rows = [dict(row) for row in rows]
    from core.library2.recording_links import reference_owners
    borrowed = reference_owners(conn, [row['track_id'] for row in rows if not row['file_id']])
    for row in rows:
        if linked := borrowed.get(row['track_id']):
            row.update(file_id=linked['file_id'], path=linked['path'], file_state='active')
    return rows, total


def resolve_native_queue_track(db, raw, *, profile_id, library_owner_id, is_admin):
    """Resolve a named native row before prefetch, ignoring client import data.

    Reuse the grab rights/context and wishlist payload kernels without writing
    monitoring intent or a wishlist entry. Only the correlation id comes from
    the player; provider ids, file choice and profile come from the catalogue.
    """
    from core.library2.grab_context import profile_may_grab, resolve_lib2_grab_context
    from core.library2.sql_util import entity_visible
    from core.library2.wishlist_mirror import track_wishlist_payload
    from core.library2.editions import album_release_ids, default_edition_id
    from core.library2.provider_ids import preferred_provider_identity
    from core.library2.track_files import primary_file_row

    state, context = resolve_lib2_grab_context(db, raw)
    if state != 'ok' or not context.get('track_id'):
        raise ValueError('Unknown Library v2 entity for playback')
    if not is_admin and not profile_may_grab(db, profile_id, raw):
        raise PermissionError('Downloads require an owned library for this profile')

    track_id, album_id = context['track_id'], context['album_id']
    with closing(db._get_connection()) as conn:
        if not entity_visible(conn, 'track', track_id):
            raise PermissionError('Track is outside the selected library')
        payload = track_wishlist_payload(conn, track_id)
        album_row = conn.execute('SELECT * FROM lib2_albums WHERE id=?', (album_id,)).fetchone()
        album_ids = album_release_ids(album_row)
        edition_id = default_edition_id(conn, album_id)
        file = primary_file_row(conn, track_id, scoped=True)
        if file and (file.get('file_state') or 'active') != 'active':
            file = None
        if not file:
            from core.library2.recording_links import reference_owner
            reference = reference_owner(conn, track_id)
            if reference:
                borrowed = conn.execute('SELECT * FROM lib2_track_files WHERE id=?',
                                        (reference['file_id'],)).fetchone()
                if borrowed and (borrowed['file_state'] or 'active') == 'active':
                    file = dict(borrowed)
        # Stable ids may be assigned by the shared payload builder.
        conn.commit()

    info = {key: value for key, value in payload.items() if not key.startswith('_')}
    if not info.get('artists'):
        info['artists'] = [{'name': context['artist_name']}]
    track_ids = dict(info.get('provider_ids') or {})
    pin_source = str(album_row['canonical_source'] or '').strip().lower()
    source, source_id = preferred_provider_identity(
        track_ids, (pin_source, info.get('source')),
    )
    source = source or 'library_v2'
    info.update({
        'id': source_id or info['id'], 'source_track_id': source_id,
        'source': source, 'provider': source,
        'title': info['name'], 'lib2_track_id': track_id, 'lib2_album_id': album_id,
        'release_edition_id': edition_id, 'lib2_entity': context,
        'profile_id': profile_id, 'library_owner_id': library_owner_id,
        'quality_profile_id': context['quality_profile_id'],
        'quality_profile_source': context['quality_profile_source'],
        'quality_profile_source_id': context['quality_profile_source_id'],
        'quality_profile_explicit': context['quality_profile_explicit'],
        '_queue_request_id': raw.get('_queue_request_id'),
        'file_path': file['path'] if file else '',
    })
    album = dict(info['album'])
    # A Tidal track never borrows a Deezer/Spotify album id. A pin overrides
    # the group id for its own source through the existing edition kernel.
    album['id'] = album_ids.get(source) or f'lib2-album:{album_id}'
    album['provider_ids'] = album_ids
    info['album'] = album
    info['source_info'] = {
        **payload['_source_info'],
        'lib2_track_id': track_id, 'lib2_album_id': album_id,
        'release_edition_id': edition_id,
        'metadata_source': source,
        'track_provider_ids': track_ids, 'album_provider_ids': album_ids,
    }
    info['_source_album_id'] = album['id']
    info['_explicit_album_context'] = album
    info['_explicit_artist_context'] = (info['artists'] or [{'name': context['artist_name']}])[0]
    info['_is_explicit_album_download'] = True
    return info
