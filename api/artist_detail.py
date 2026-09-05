"""Artist detail family - lifted from web_server.py.

the artist detail payload (library + source-only), similar artists
(+stream), top tracks, discography (+gap fill, download, completion,
completion-stream), artist/album art options + set/delete + write to
disk, the graph endpoints, and album tracks. bodies byte-identical;
only the decorator changed and spotify_client / hydrabase_worker /
dev_mode_enabled became getters.
"""

import json
import os
import threading
import time

from urllib.parse import urlparse

from flask import Blueprint, Response, jsonify, request

from api.source_playlists import (
    _get_deezer_client,
    _get_discogs_client,
    _get_itunes_client,
    _get_metadata_fallback_source,
)
from core.metadata import normalize_image_url as fix_artist_image_url
from core.library2.provider_ids import ARTIST_IDS_SQL as _ARTIST_IDS_SQL
from core.search import by_id as _search_by_id
from core.search import orchestrator as _search_orchestrator
from core.metadata.cache import get_metadata_cache
from core.profile_context import get_current_profile_id
from utils.logging_config import get_logger

logger = get_logger("web_server")

# injected by configure()
get_database = None
config_manager = None
media_server_engine = None
_mark_request_free_ok_for_spotify = None
_resolve_library_file_path = None
_build_search_deps = None
_spotify_client = None
_hydrabase_worker = None
_dev_mode_enabled = None


def configure(**deps):
    g = globals()
    for name, value in deps.items():
        if name not in g:
            raise KeyError(f"artist_detail.configure: unknown dep {name!r}")
        g[name] = value


bp = Blueprint('artist_detail', __name__)


def create_blueprint():
    return bp

from core.artist_source_lookup import (
    SOURCE_ID_FIELD as _SOURCE_ID_FIELD,
    SOURCE_ONLY_ARTIST_SOURCES as _SOURCE_ONLY_ARTIST_SOURCES,
    find_library_artist_for_source as _core_find_library_artist_for_source,
    sources_resolvable_in_library as _core_sources_resolvable_in_library,
)


def _catalogue_name_key(name):
    """The catalogue's folded artist key. SQLite's LOWER() is ASCII-only, so a
    stored "Björk" never answered a searched "björk" (iss29-D13)."""
    from core.library2.importer import normalize_name

    return normalize_name(str(name or ""))


def _find_library_artist_for_source(database, source, source_artist_id, artist_name=None):
    """Thin wrapper that injects the active-server context for the core lookup."""
    try:
        active_server = config_manager.get_active_media_server()
    except Exception:
        active_server = None
    return _core_find_library_artist_for_source(
        database, source, source_artist_id, artist_name, active_server=active_server
    )


def _resolve_source_artist_name(source, artist_id):
    """Resolve a source artist's display name by id, or '' on any failure.

    Reuses the #775 link-resolver's per-source artist fetch so we have one
    place that knows each source's get-by-id quirks. Used by the artist-detail
    library upgrade to disambiguate a duplicated/corrupt source id by name
    when the URL-driven navigation didn't carry a name.
    """
    try:
        deps = _build_search_deps()
        client, _available = _search_orchestrator.resolve_client(source, deps)
        if client is None:
            return ''
        data = _search_by_id._fetch_artist(client, source, artist_id)
        return (data or {}).get('name') or ''
    except Exception as e:
        logger.debug(f"Source artist name resolution failed for {source}:{artist_id}: {e}")
        return ''


def _build_source_only_artist_detail(artist_id, artist_name, source):
    """Thin wrapper around ``core.artist_source_detail.build_source_only_artist_detail``.

    Builds the per-source client bag from web_server's module globals (each
    source's module-level client + Last.fm api key), forwards to the pure
    implementation in ``core/``, and wraps the (dict, status) return in
    ``jsonify``.
    """
    from core.artist_source_detail import build_source_only_artist_detail
    from core.metadata.discography_strict import (
        get_artist_detail_discography as _get_artist_detail_discography,
    )

    # Resolve the per-source clients defensively — the original inline code
    # wrapped the whole source-side lookup in try/except so a failing
    # client helper (e.g. Spotify auth probe during a rate-limit ban,
    # Discogs client init error) would degrade gracefully to empty
    # enrichment instead of 500-ing the request. Preserve that.
    sp = None
    dz = None
    it = None
    dc = None
    try:
        if _spotify_client() and _spotify_client().is_spotify_authenticated():
            sp = _spotify_client()
    except Exception as e:
        logger.debug(f"Spotify client resolution failed: {e}")
    try:
        dz = _get_deezer_client()
    except Exception as e:
        logger.debug(f"Deezer client resolution failed: {e}")
    try:
        it = _get_itunes_client()
    except Exception as e:
        logger.debug(f"iTunes client resolution failed: {e}")
    try:
        discogs_token = config_manager.get('discogs.token', '') or ''
        if discogs_token:
            dc = _get_discogs_client(discogs_token)
    except Exception as e:
        logger.debug(f"Discogs client resolution failed: {e}")

    az = None
    js = None
    try:
        from core.metadata.registry import get_amazon_client
        az = get_amazon_client()
    except Exception as e:
        logger.debug(f"Amazon client resolution failed: {e}")
    try:
        from core.metadata.registry import get_jiosaavn_client, is_source_enabled
        if is_source_enabled('jiosaavn'):
            js = get_jiosaavn_client()
    except Exception as e:
        logger.debug(f"JioSaavn client resolution failed: {e}")

    bc = None
    try:
        from core.metadata.registry import get_bandcamp_client, is_source_enabled
        if is_source_enabled('bandcamp'):
            bc = get_bandcamp_client()
    except Exception as e:
        logger.debug(f"Bandcamp client resolution failed: {e}")

    try:
        lastfm_api_key = config_manager.get('lastfm.api_key', '') or None
    except Exception:
        lastfm_api_key = None

    payload, status = build_source_only_artist_detail(
        artist_id,
        artist_name,
        source,
        spotify_client=sp,
        deezer_client=dz,
        itunes_client=it,
        discogs_client=dc,
        amazon_client=az,
        jiosaavn_client=js,
        bandcamp_client=bc,
        lastfm_api_key=lastfm_api_key,
        discography_loader=_get_artist_detail_discography,
    )
    return jsonify(payload), status


@bp.route('/api/artist-detail/<artist_id>')
def get_artist_detail(artist_id):
    """Artist detail for an artist the catalogue does NOT hold.

    ``artist_id`` is a provider's id and ``?source=<provider>&name=<name>`` says
    whose — the response is synthesized from that source (name + image +
    discography) so Library V2's discovery mode can render an artist it has no
    row for (ldp-02).

    There is no library branch any more. It used to look ``artist_id`` up as a
    catalogue row first, which was both dead (every owned artist opens in
    Library V2, which resolves it there) and wrong: a numeric provider id can
    collide with a catalogue id, and the endpoint then answered with a stranger's
    library (the iss29-B04c family).
    """
    try:
        source_param = (request.args.get('source', '') or '').strip().lower()
        artist_name_arg = (request.args.get('name', '') or '').strip()
        if not source_param:
            return jsonify({
                "success": False,
                "error": "source is required — the library view lives in Library V2",
            }), 400

        from core.metadata.registry import experimental_source_rejected
        if experimental_source_rejected(source_param):
            return jsonify({
                "success": False,
                "error": f"{source_param} is not enabled",
            }), 503

        _mark_request_free_ok_for_spotify(source_param)
        logger.info(f"Getting artist detail for {source_param}:{artist_id}")
        return _build_source_only_artist_detail(
            artist_id, artist_name_arg, source_param
        )
    except Exception as e:
        logger.error(f"Error getting artist detail: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/api/library/debug-photos')
def debug_library_photos():
    """Debug endpoint to check artist photo URLs"""
    try:
        database = get_database()

        with database._get_connection() as conn:
            cursor = conn.cursor()

            # Library v2 is the catalogue here: the legacy `artists` table is
            # empty, so the upstream version of this endpoint reported "no
            # artists have photos" on every install. `thumb_url` is kept as the
            # response key because that is the name the debug output documents.
            cursor.execute("""
                SELECT name, image_url AS thumb_url, server_source
                FROM lib2_artists
                WHERE image_url IS NOT NULL AND image_url != ''
                LIMIT 10
            """)

            artists_with_photos = cursor.fetchall()

            # Get first 10 artists without photos
            cursor.execute("""
                SELECT name, image_url AS thumb_url, server_source
                FROM lib2_artists
                WHERE image_url IS NULL OR image_url = ''
                LIMIT 10
            """)

            artists_without_photos = cursor.fetchall()

            return jsonify({
                "artists_with_photos": [dict(row) for row in artists_with_photos],
                "artists_without_photos": [dict(row) for row in artists_without_photos],
                "total_with_photos": len(artists_with_photos),
                "total_without_photos": len(artists_without_photos)
            })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@bp.route('/api/artist/similar/<path:artist_name>/stream')
def get_similar_artists_stream(artist_name):
    """Stream MusicMap similar artists using source-priority metadata matching."""
    from core.metadata_service import iter_musicmap_similar_artist_events

    def generate():
        logger.info(f"Streaming similar artists for: {artist_name}")
        for event in iter_musicmap_similar_artist_events(artist_name, limit=20):
            yield f"data: {json.dumps(event)}\n\n"
            if event.get('artist'):
                time.sleep(0.1)

    return Response(generate(), mimetype='text/event-stream')

@bp.route('/api/artist/similar/<path:artist_name>')
def get_similar_artists(artist_name):
    """Get MusicMap similar artists using source-priority metadata matching."""
    from core.metadata_service import get_musicmap_similar_artists

    try:
        logger.info(f"Getting similar artists for: {artist_name}")
        result = get_musicmap_similar_artists(artist_name, limit=20)
        if not result.get('success'):
            error = result.get('error', 'Failed to fetch similar artists')
            status_code = int(result.get('status_code') or 500)
            return jsonify({
                "success": False,
                "error": error
            }), status_code

        return jsonify({
            "success": True,
            "artist": artist_name,
            "similar_artists": result.get('similar_artists', []),
            "total_found": result.get('total_found', 0),
            "total_matched": result.get('total_matched', 0),
            "source_priority": result.get('source_priority', []),
        })

    except Exception as e:
        logger.error(f"Error getting similar artists: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@bp.route('/api/artist/<artist_id>/write-image-to-disk', methods=['POST'])
def write_artist_image_to_disk(artist_id):
    """Write `artist.jpg` to the artist's folder on disk.

    Issue #572 (rhwc): Navidrome has no API for setting an artist
    image — it reads `artist.jpg` from the artist's folder during
    library scans. SoulSync's `update_artist_poster` for Navidrome
    is a NO-OP today. This endpoint closes the gap by:

    1. Resolving the artist's folder on disk via any of their albums'
       tracks (`_resolve_library_file_path` handles Docker mount
       translation + the same library-path probes #558 settled on)
    2. Fetching an artist photo URL from the configured metadata source
       priority chain (Spotify → Deezer → ... already wired through
       `core.metadata_service.get_artist_image_url`)
    3. Downloading the image bytes and writing `<artist>/artist.jpg`
       atomically via the pure helpers in `core/library/artist_image.py`
    4. Triggering a Navidrome library scan so the file gets picked
       up immediately

    Request body (JSON, all optional):
        - ``image_url`` — explicit URL to use, bypassing metadata
          source resolution (useful for "use this exact photo" UX)
        - ``overwrite`` — when True, replace existing `artist.jpg`
          (default False respects user-supplied files)
        - ``source_override`` — pin the metadata source for URL
          resolution (e.g. ``"deezer"``)
    """
    try:
        from core.library.artist_image import (
            derive_artist_folder,
            download_image_bytes,
            write_artist_jpg,
        )
        from core.metadata_service import get_artist_image_url as _get_artist_image_url

        data = request.get_json(silent=True) or {}
        explicit_url = (data.get('image_url') or '').strip() or None
        overwrite = bool(data.get('overwrite', False))
        source_override = (data.get('source_override') or '').strip().lower() or None

        db = get_database()
        # #1069: TEXT ids (Navidrome/Jellyfin) — never int() an artist id.
        artist_id = str(artist_id or '').strip()
        if not artist_id:
            return jsonify({"success": False, "error": "Invalid artist id"}), 400

        artist_row = db.get_artist(artist_id)
        if artist_row is None:
            return jsonify({"success": False, "error": "Artist not found"}), 404

        # Find a track file on disk so we can derive the artist folder.
        # Walk albums in DB order; first one with a resolvable track wins.
        albums = db.get_albums_by_artist(artist_id)
        if not albums:
            return jsonify({"success": False,
                            "error": "No albums for this artist; cannot derive folder."}), 400

        resolved_track_path = None
        for album in albums:
            tracks = db.get_tracks_by_album(album.id)
            for tr in tracks:
                if not getattr(tr, 'file_path', None):
                    continue
                candidate = _resolve_library_file_path(tr.file_path) or tr.file_path
                if candidate and os.path.exists(candidate):
                    resolved_track_path = candidate
                    break
            if resolved_track_path:
                break

        if not resolved_track_path:
            return jsonify({"success": False,
                            "error": "Could not locate any track file on disk to derive the artist folder. "
                                     "Configure Settings → Library → Music Paths to point at the library mount."}), 400

        album_folder = os.path.dirname(resolved_track_path)
        artist_folder = derive_artist_folder(album_folder)
        if not artist_folder or not os.path.isdir(artist_folder):
            return jsonify({"success": False,
                            "error": f"Resolved artist folder is invalid: {artist_folder!r}"}), 400

        # Pick the image URL. Explicit override (from request body)
        # wins so users can paste a specific photo URL. Otherwise
        # resolve from the active metadata source.
        if explicit_url:
            image_url = explicit_url
        else:
            try:
                image_url = _get_artist_image_url(
                    artist_id,
                    source_override=source_override,
                    artist_name=getattr(artist_row, 'name', None),
                )
            except Exception as exc:
                logger.error(f"artist image lookup failed: {exc}")
                image_url = None

        if not image_url:
            return jsonify({"success": False,
                            "error": "No artist image URL found from metadata sources."}), 404

        image_bytes = download_image_bytes(image_url)
        if not image_bytes:
            return jsonify({"success": False,
                            "error": f"Failed to download image from {image_url}"}), 502

        success, detail = write_artist_jpg(artist_folder, image_bytes, overwrite=overwrite)
        if not success:
            return jsonify({"success": False, "error": detail}), 400

        # If the active media server is Navidrome, trigger a scan so
        # the new file gets indexed without waiting for the next
        # automatic scan cycle.
        scan_triggered = False
        try:
            active_server = config_manager.get_active_media_server()
            if active_server == 'navidrome':
                nav = media_server_engine.client('navidrome')
                if nav is not None:
                    nav.trigger_library_scan()
                    scan_triggered = True
        except Exception as exc:
            logger.debug(f"Navidrome scan trigger after artist image write failed: {exc}")

        return jsonify({
            "success": True,
            "written_to": detail,
            "image_url": image_url,
            "scan_triggered": scan_triggered,
        })

    except Exception as e:
        logger.error(f"Error writing artist image to disk: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/artist/<artist_id>/image', methods=['GET'])
def get_artist_image(artist_id):
    """Get an artist image URL using source-aware metadata resolution."""
    try:
        from core.metadata_service import get_artist_image_url as _get_artist_image_url

        source_override = request.args.get('source', '').strip().lower() or None
        plugin = request.args.get('plugin', '').strip().lower() or None
        # `name` is optional but required for sources that don't store
        # artist images directly (MusicBrainz) — the resolver falls back
        # to searching iTunes/Deezer by name.
        artist_name = request.args.get('name', '').strip() or None
        image_url = _get_artist_image_url(
            artist_id,
            source_override=source_override,
            plugin=plugin,
            artist_name=artist_name,
        )
        # Same first-party treatment the similar-artists listing gets: hand the
        # browser a SoulSync url, never a raw third-party CDN one, so a content
        # blocker can't silently drop it (#1201).
        if image_url:
            try:
                from core.image_cache import cached_image_url
                image_url = cached_image_url(image_url) or image_url
            except Exception as exc:   # noqa: BLE001 - art must never 500
                logger.debug("artist image cache registration failed: %s", exc)
        return jsonify({"success": True, "image_url": image_url})
    except Exception as e:
        logger.error(f"Error fetching artist image: {e}")
        return jsonify({"success": False, "image_url": None, "error": str(e)})

@bp.route('/api/artist/<artist_id>/top-tracks', methods=['GET'])
def get_artist_top_tracks_endpoint(artist_id):
    """Return an artist's top-N tracks via the primary metadata source.

    Issue #513: users want a "top X popular songs" path that doesn't pull
    the entire discography. Spotify's `artist_top_tracks` endpoint and
    Deezer's `/artist/{id}/top` both expose this; iTunes / Discogs /
    MusicBrainz don't have popularity ranking, so this endpoint returns
    `success=False` for those primary sources and the frontend falls back
    to the existing Last.fm display-only sidebar.

    Resolves per-source artist IDs from the DB row (matching what
    /discography already does) so a Spotify ID in the URL still works
    when Deezer is primary, and vice versa.
    """
    try:
        primary_source = _get_metadata_fallback_source()
        if primary_source not in ('spotify', 'deezer'):
            return jsonify({
                'success': False,
                'reason': 'unsupported_source',
                'source': primary_source,
                'tracks': [],
            })

        try:
            limit = max(1, min(int(request.args.get('limit', 10)), 50))
        except (TypeError, ValueError):
            limit = 10

        # Per-source ID resolution from the DB — same pattern as
        # /discography. Without this, the frontend's chosen ID type
        # (Spotify, Deezer, iTunes, library DB id) decides which source
        # can answer; we want the URL ID to be neutral.
        resolved_id = artist_id
        try:
            _db = get_database()
            _conn = _db._get_connection()
            try:
                _cur = _conn.cursor()
                _cur.execute("""
                    SELECT spotify_id AS spotify_artist_id,
                           json_extract(external_ids, '$.deezer') AS deezer_id
                    FROM lib2_artists
                    WHERE id = ?
                       OR spotify_id = ?
                       OR json_extract(external_ids, '$.itunes') = ?
                       OR json_extract(external_ids, '$.deezer') = ?
                       OR musicbrainz_id = ?
                    LIMIT 1
                """, (artist_id, artist_id, artist_id, artist_id, artist_id))
                _row = _cur.fetchone()
                if _row:
                    if primary_source == 'spotify' and _row['spotify_artist_id']:
                        resolved_id = str(_row['spotify_artist_id'])
                    elif primary_source == 'deezer' and _row['deezer_id']:
                        resolved_id = str(_row['deezer_id'])
            finally:
                _conn.close()
        except Exception as e:
            logger.debug("top-tracks per-source ID resolution failed: %s", e)

        tracks = []
        if primary_source == 'spotify':
            if not _spotify_client() or not _spotify_client().is_spotify_authenticated():
                return jsonify({
                    'success': False,
                    'reason': 'spotify_not_authenticated',
                    'source': 'spotify',
                    'tracks': [],
                })
            market = config_manager.get('spotify.market', 'US') or 'US'
            tracks = _spotify_client().get_artist_top_tracks(resolved_id, country=market, limit=limit)
        else:  # deezer
            deezer_client = _get_deezer_client()
            if not deezer_client:
                return jsonify({
                    'success': False,
                    'reason': 'deezer_unavailable',
                    'source': 'deezer',
                    'tracks': [],
                })
            tracks = deezer_client.get_artist_top_tracks(resolved_id, limit=limit)

        if not tracks:
            return jsonify({
                'success': False,
                'reason': 'no_tracks_found',
                'source': primary_source,
                'tracks': [],
            })

        return jsonify({
            'success': True,
            'source': primary_source,
            'resolved_artist_id': resolved_id,
            'tracks': tracks,
        })
    except Exception as e:
        logger.exception("Error fetching artist top tracks for %s", artist_id)
        return jsonify({"success": False, "error": str(e), "tracks": []}), 500


def _resolve_artist_source_ids(artist_id) -> dict:
    """Per-source artist ids from the enriched library row, keyed by source.

    Looks the row up by ANY id the frontend might send (library PK or any
    provider id) and returns every stored provider id, so each source gets
    ITS OWN id instead of someone else's (which Deezer would happily accept
    as a different artist). Lifted from the discography endpoint; the
    gap-fill endpoint shares it."""
    artist_source_ids = {}
    try:
        _db = get_database()
        _conn = _db._get_connection()
        try:
            _cur = _conn.cursor()
            _cur.execute("""
                SELECT spotify_id AS spotify_artist_id,
                       json_extract(external_ids, '$.itunes') AS itunes_artist_id,
                       json_extract(external_ids, '$.deezer') AS deezer_id,
                       musicbrainz_id
                FROM lib2_artists
                WHERE id = ?
                   OR spotify_id = ?
                   OR json_extract(external_ids, '$.itunes') = ?
                   OR json_extract(external_ids, '$.deezer') = ?
                   OR musicbrainz_id = ?
                LIMIT 1
            """, (artist_id, artist_id, artist_id, artist_id, artist_id))
            _row = _cur.fetchone()
            if _row:
                if _row['spotify_artist_id']:
                    artist_source_ids['spotify'] = str(_row['spotify_artist_id'])
                if _row['itunes_artist_id']:
                    artist_source_ids['itunes'] = str(_row['itunes_artist_id'])
                if _row['deezer_id']:
                    artist_source_ids['deezer'] = str(_row['deezer_id'])
                if _row['musicbrainz_id']:
                    artist_source_ids['musicbrainz'] = str(_row['musicbrainz_id'])
                logger.info(
                    f"Discography: resolved per-source IDs for artist_id={artist_id} → "
                    f"{artist_source_ids}"
                )
        finally:
            _conn.close()
    except Exception as _id_exc:
        logger.debug(f"Could not resolve per-source artist IDs for {artist_id}: {_id_exc}")
    return artist_source_ids


@bp.route('/api/artist/<artist_id>/discography/gap-fill', methods=['GET'])
def get_artist_discography_gap_fill(artist_id):
    """Releases OTHER metadata sources know that the base source's
    discography doesn't (#1067 — the hybrid/gap-fill view option).

    Strictly additive + conservative: only sources whose ENRICHED per-source
    artist id we hold are consulted (never a fuzzy name search — the wrong
    artist's discography as 'gap-fill' would be worse than no feature), each
    source is fetched with fallback disabled so a failing source contributes
    nothing instead of double-counting another, and the dedup shows a
    borderline edition twice rather than merging it wrongly. The base page's
    own load path is untouched — this endpoint only ever ADDS cards."""
    try:
        artist_name = request.args.get('artist_name', '').strip()
        base_source = (request.args.get('base_source', '') or '').strip().lower()

        artist_source_ids = _resolve_artist_source_ids(artist_id)

        from core.metadata.discography_gapfill import gap_fill_buckets
        from core.metadata.lookup import MetadataLookupOptions
        from core.metadata.discography_strict import get_artist_detail_discography

        def _fetch(source, source_artist_id, name=''):
            # OTHER-source fetches pass NO artist name: the per-source lookup
            # has an internal search-by-name fallback when the id yields
            # nothing (album_tracks.get_artist_albums_for_source), and a stale
            # enriched id must degrade to "no gap-fill from this source" —
            # never to a name search that could pick the wrong artist. Only
            # the base fetch keeps the name (mirrors the page's own load).
            disc = get_artist_detail_discography(
                source_artist_id,
                artist_name=name,
                options=MetadataLookupOptions(
                    source_override=source,
                    allow_fallback=False,   # a down source must not answer from another
                    skip_cache=False,
                    max_pages=0,
                    limit=200,
                    artist_source_ids=artist_source_ids or None,
                ),
            )
            if disc.get('state') != 'results':
                return None
            return {'albums': disc.get('albums') or [],
                    'eps': disc.get('eps') or [],
                    'singles': disc.get('singles') or []}

        # Resolve the BASE the same way the page did. An explicit base_source
        # (source-only artist pages) pins it; otherwise fetch with NO override
        # so ragnarlotus's Library-discography-source setting (#1068 — primary /
        # automatic / explicit) picks the source exactly like the page's own
        # load, and read back which source actually answered.
        if base_source:
            base = _fetch(base_source, artist_source_ids.get(base_source) or artist_id,
                          name=artist_name)
            resolved_base = base_source
        else:
            disc = get_artist_detail_discography(
                artist_id,
                artist_name=artist_name,
                options=MetadataLookupOptions(
                    skip_cache=False, max_pages=0, limit=200,
                    artist_source_ids=artist_source_ids or None,
                ),
            )
            if disc.get('state') == 'results':
                base = {'albums': disc.get('albums') or [],
                        'eps': disc.get('eps') or [],
                        'singles': disc.get('singles') or []}
                resolved_base = str(disc.get('source') or '').lower()
            else:
                base, resolved_base = None, ''

        if base is None:
            # No base to diff against — returning gaps would duplicate the page
            return jsonify({"success": True, "gaps": {"albums": [], "eps": [], "singles": []},
                            "sources_checked": [], "base_source": resolved_base,
                            "note": "Base discography unavailable."})

        candidates = [s for s in ('spotify', 'deezer', 'itunes', 'musicbrainz')
                      if s != resolved_base and artist_source_ids.get(s)]
        if not candidates:
            return jsonify({"success": True, "gaps": {"albums": [], "eps": [], "singles": []},
                            "sources_checked": [], "base_source": resolved_base,
                            "note": "No other sources have a verified id for this artist yet "
                                    "(enrichment provides them)."})

        others = {}
        checked = []
        for source in candidates:
            disc = _fetch(source, artist_source_ids[source])
            if disc is not None:
                others[source] = disc
                checked.append(source)

        gaps = gap_fill_buckets(base, others, checked)
        return jsonify({"success": True, "gaps": gaps,
                        "sources_checked": checked, "base_source": resolved_base})
    except Exception as e:
        logger.error(f"Discography gap-fill failed for {artist_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/artist/<artist_id>/discography', methods=['GET'])
def get_artist_discography(artist_id):
    """Get an artist's complete discography (albums and singles)"""
    try:
        # Get optional artist name for fallback searches
        artist_name = request.args.get('artist_name', '').strip()
        # Optional source override from multi-source search tabs
        source_override = request.args.get('source', '').strip().lower()
        _mark_request_free_ok_for_spotify(source_override)

        # Mirror to Hydrabase P2P network
        if _hydrabase_worker() and _dev_mode_enabled() and artist_name:
            _hydrabase_worker().enqueue(artist_name, 'artist.albums')

        effective_override_source = source_override
        if source_override == 'hydrabase':
            plugin = request.args.get('plugin', '').strip().lower()
            if plugin == 'deezer':
                effective_override_source = 'deezer'
            elif plugin == 'itunes' or artist_id.isdigit():
                effective_override_source = 'itunes'
            else:
                effective_override_source = 'spotify'

        from core.metadata.lookup import MetadataLookupOptions
        # #877: use the artist-DETAIL discography so the Download Discography modal
        # gets the SAME release-type split (albums / eps / singles) the Artist
        # Detail view shows — EPs were being lumped into singles before, leaving
        # the modal's EPs toggle dead.
        from core.metadata.discography_strict import get_artist_detail_discography as _get_artist_discography

        # Server-side per-source ID resolution. Look up the library row
        # by ANY of the IDs the frontend might send: library DB id,
        # spotify_artist_id, itunes_artist_id, deezer_id, or
        # musicbrainz_id. Once matched, pull every stored provider ID
        # and dispatch the right ID to each source via
        # ``artist_source_ids``. Mirrors what the watchlist scanner
        # already does.
        #
        # Without this, the frontend's ID choice fully decides which
        # source can answer correctly:
        #   - sends DB id 194687 → Deezer accepts (wrong: it's a real
        #     Deezer ID for a different artist)
        #   - sends Spotify ID `1bDWGdIC...` → Deezer rejects → falls
        #     back to fuzzy name search → may pick wrong artist
        # With server-side resolution, every source gets its OWN stored
        # ID regardless of which one the URL carries.
        artist_source_ids = _resolve_artist_source_ids(artist_id)

        discography = _get_artist_discography(
            artist_id,
            artist_name=artist_name,
            options=MetadataLookupOptions(
                source_override=effective_override_source,
                allow_fallback=True,
                skip_cache=False,
                max_pages=0,
                # Discord report: prolific artists (Bach, Beatles
                # complete box, deep dance/electronic catalogues)
                # showed only ~50 entries in the Download Discography
                # modal. Spotify's `max_pages=0` already paginates
                # through everything (per-page is clamped to 10
                # internally), but Deezer / iTunes / Discogs /
                # Hydrabase all honor the outer `limit` as a hard
                # cap. 200 lines up with iTunes's and Discogs's own
                # internal caps and covers near-everyone's full
                # catalogue.
                limit=200,
                artist_source_ids=artist_source_ids or None,
            ),
        )

        if discography.get('state') == 'error':
            return jsonify({
                "success": False,
                "state": "error",
                "error": discography.get(
                    "error", "Could not access the discography provider"
                ),
                "source": discography.get("source", "unknown"),
            }), int(discography.get("status_code") or 502)

        album_list = discography['albums']
        eps_list = discography.get('eps', [])
        singles_list = discography['singles']
        active_source = discography['source']
        source_priority = discography['source_priority']

        # Gather artist enrichment info from cache + library
        artist_info = {}
        try:
            cache = get_metadata_cache()
            cache_sources = []
            if active_source:
                cache_sources.append(active_source)
            for source in source_priority:
                if source not in cache_sources:
                    cache_sources.append(source)

            # Try metadata cache for genres, image, followers
            cached = None
            for src in cache_sources:
                cached = cache.get_entity(src, 'artist', artist_id)
                if cached:
                    break
            if not cached and artist_name:
                # Try by name across all sources
                for src in cache_sources:
                    db_tmp = get_database()
                    conn_tmp = db_tmp._get_connection()
                    try:
                        cur = conn_tmp.cursor()
                        cur.execute("""
                            SELECT genres, image_url, followers, popularity, external_urls
                            FROM metadata_cache_entities
                            WHERE entity_type = 'artist' AND name COLLATE NOCASE = ? AND source = ?
                            LIMIT 1
                        """, (artist_name, src))
                        row = cur.fetchone()
                        if row:
                            cached = dict(row)
                            break
                    finally:
                        conn_tmp.close()
            if cached:
                try:
                    artist_info['genres'] = json.loads(cached.get('genres', '[]')) if isinstance(cached.get('genres'), str) else (cached.get('genres') or [])
                except Exception:
                    artist_info['genres'] = []
                artist_info['image_url'] = cached.get('image_url')
                artist_info['followers'] = cached.get('followers')
                artist_info['popularity'] = cached.get('popularity')
                try:
                    artist_info['external_urls'] = json.loads(cached.get('external_urls', '{}')) if isinstance(cached.get('external_urls'), str) else (cached.get('external_urls') or {})
                except Exception:
                    artist_info['external_urls'] = {}

            # Try library for full enrichment (Last.fm bio, stats, service IDs)
            if artist_name:
                db_lib = get_database()
                conn_lib = db_lib._get_connection()
                try:
                    cur_lib = conn_lib.cursor()
                    cur_lib.execute("""
                        SELECT id, summary, genres, image_url AS thumb_url,
                               soul_id, style, mood, label,
                               json_extract(enrichment, '$.lastfm.bio') AS lastfm_bio,
                               json_extract(enrichment, '$.lastfm.listeners') AS lastfm_listeners,
                               json_extract(enrichment, '$.lastfm.playcount') AS lastfm_playcount,
                               json_extract(enrichment, '$.lastfm.tags') AS lastfm_tags,
                               {_ARTIST_IDS_SQL}
                        FROM lib2_artists WHERE name_key = ? LIMIT 1
                    """.format(_ARTIST_IDS_SQL=_ARTIST_IDS_SQL), (_catalogue_name_key(artist_name),))
                    lib_row = cur_lib.fetchone()
                    if lib_row:
                        lib = dict(lib_row)
                        artist_info['library_id'] = lib['id']
                        # Image fallback
                        if not artist_info.get('image_url') and lib['thumb_url']:
                            artist_info['image_url'] = fix_artist_image_url(lib['thumb_url'])
                        # Genres fallback
                        if not artist_info.get('genres') and lib['genres']:
                            try:
                                artist_info['genres'] = json.loads(lib['genres'])
                            except Exception as e:
                                logger.debug("genres json parse failed: %s", e)
                        # Last.fm enrichment
                        if lib.get('lastfm_bio'):
                            artist_info['lastfm_bio'] = lib['lastfm_bio']
                        if lib.get('lastfm_listeners'):
                            artist_info['lastfm_listeners'] = lib['lastfm_listeners']
                        if lib.get('lastfm_playcount'):
                            artist_info['lastfm_playcount'] = lib['lastfm_playcount']
                        if lib.get('lastfm_tags'):
                            try:
                                artist_info['lastfm_tags'] = json.loads(lib['lastfm_tags']) if isinstance(lib['lastfm_tags'], str) else lib['lastfm_tags']
                            except Exception as e:
                                logger.debug("lastfm_tags json parse failed: %s", e)
                        if lib.get('lastfm_url'):
                            artist_info['lastfm_url'] = lib['lastfm_url']
                        if lib.get('genius_url'):
                            artist_info['genius_url'] = lib['genius_url']
                        # Service IDs for badges
                        for key in ['spotify_artist_id', 'musicbrainz_id', 'deezer_id', 'itunes_artist_id',
                                    'audiodb_id', 'discogs_id', 'tidal_id', 'qobuz_id', 'genius_id', 'soul_id']:
                            if lib.get(key):
                                artist_info[key] = lib[key]
                        # Bio fallback from summary
                        if not artist_info.get('lastfm_bio') and lib.get('summary'):
                            artist_info['bio'] = lib['summary']
                finally:
                    conn_lib.close()
        except Exception as e:
            logger.debug(f"Artist info enrichment failed (non-fatal): {e}")

        return jsonify({
            "albums": album_list,
            "eps": eps_list,
            "singles": singles_list,
            "source": active_source or (source_priority[0] if source_priority else "unknown"),
            "artist_info": artist_info,
        })

    except Exception as e:
        logger.exception("Error fetching artist discography for %s", artist_id)
        return jsonify({"error": str(e)}), 500

_ART_OPTIONS_CACHE = {}                       # (artist_lower, album_lower) -> (ts, candidates)
_ART_OPTIONS_CACHE_LOCK = threading.Lock()
_ART_OPTIONS_TTL_S = 900                       # 15 min — gathering is several slow external calls
_ART_OPTIONS_EMPTY_TTL_S = 60                  # empties retry fast: one hiccup must not stick


def _looks_like_image(data: bytes) -> bool:
    """Magic-byte sniff — pasted custom URLs must not poison the thumb/poster/
    artist.jpg with an HTML page. JPEG/PNG/GIF/WEBP/BMP cover real art."""
    if not data or len(data) < 12:
        return False
    return (data[:2] == b"\xff\xd8" or data[:8] == b"\x89PNG\r\n\x1a\n"
            or data[:4] == b"GIF8" or (data[:4] == b"RIFF" and data[8:12] == b"WEBP")
            or data[:2] == b"BM")


@bp.route('/api/album/<album_id>/art-options', methods=['GET'])
def get_album_art_options(album_id):
    """Candidate cover-art images for an album, for the art picker (read-only).

    Gathers from Cover Art Archive (a front cover per edition across the release-group) +
    Deezer/iTunes/Spotify/AudioDB (their single validated best), fanned out concurrently. ``artist``
    and ``album`` come from the caller — the enhanced library view already has them. Results are
    cached briefly since the gather is several slow external calls.
    """
    try:
        artist = (request.args.get('artist') or '').strip()
        album = (request.args.get('album') or '').strip()
        if not artist or not album:
            return jsonify({"error": "artist and album query params are required"}), 400

        cache_key = (artist.lower(), album.lower())
        now = time.time()
        with _ART_OPTIONS_CACHE_LOCK:
            hit = _ART_OPTIONS_CACHE.get(cache_key)
            if hit and now - hit[0] < _ART_OPTIONS_TTL_S:
                return jsonify({"album_id": album_id, "count": len(hit[1]),
                                "candidates": hit[1], "cached": True})

        metadata = {}
        try:
            from core.metadata import album_mbid_cache
            from core.metadata.source import normalize_album_cache_key
            mbid = album_mbid_cache.lookup(normalize_album_cache_key(album), artist.lower())
            if mbid:
                metadata["musicbrainz_release_id"] = mbid
        except Exception as exc:
            logger.debug("[art-options] release-MBID resolve failed: %s", exc)

        from core.metadata.art_lookup import gather_album_art_candidates
        candidates = gather_album_art_candidates(artist, album, metadata)
        with _ART_OPTIONS_CACHE_LOCK:
            _ART_OPTIONS_CACHE[cache_key] = (now, candidates)
        return jsonify({"album_id": album_id, "count": len(candidates), "candidates": candidates})
    except Exception as e:
        logger.error("[art-options] failed for album %s: %s", album_id, e, exc_info=True)
        return jsonify({"error": str(e)}), 500


def _derive_album_folder(db, album_id):
    """The album's folder on disk, from the first resolvable track path (Docker-safe). None if no
    track file can be located (e.g. paths aren't mapped in this container)."""
    try:
        tracks = db.get_tracks_by_album(int(album_id))
    except Exception:
        return None
    for tr in (tracks or []):
        raw = getattr(tr, 'file_path', None)
        if not raw:
            continue
        resolved = _resolve_library_file_path(raw) or raw
        if resolved and os.path.exists(resolved):
            return os.path.dirname(resolved)
    return None


def _overwrite_cover_jpg(url, folder):
    """Download ``url`` and OVERWRITE cover.jpg in ``folder`` (the picker is *replacing* art, so the
    existing-file guard in download_cover_art doesn't apply). Returns True on success."""
    import urllib.request  # not bound at module level (only urllib.parse is); matches the local-import pattern used elsewhere
    req = urllib.request.Request(url, headers={"User-Agent": "SoulSync/1.0", "Accept": "image/*"})
    with urllib.request.urlopen(req, timeout=15) as resp:   # noqa: S310 (user-chosen art URL)
        data = resp.read()
    if not data:
        return False
    with open(os.path.join(folder, "cover.jpg"), "wb") as handle:
        handle.write(data)
    return True


@bp.route('/api/album/<album_id>/art', methods=['POST'])
def set_album_art(album_id):
    """Apply a cover chosen in the picker: set the album's DB art URL and overwrite cover.jpg in the
    album folder. This also sets ``albums.art_locked``, which is what makes the choice stick — the
    old "non-empty thumb_url pins it" reasoning only held against enrichment workers, and a library
    sync happily wrote the server's cover back over it. Body: ``{"url": "<image url>"}``."""
    try:
        data = request.get_json(silent=True) or {}
        url = (data.get('url') or '').strip()
        if not url:
            return jsonify({"error": "url is required"}), 400

        db = get_database()
        if not db.set_album_thumb_url(album_id, url):
            return jsonify({"error": "Album not found"}), 404

        # Invalidate the cached options for this album's art so a re-open reflects the change.
        cover_written = False
        folder = _derive_album_folder(db, album_id)
        if folder:
            try:
                cover_written = _overwrite_cover_jpg(url, folder)
                logger.info("[set-art] album %s cover.jpg -> %s", album_id, folder)
            except Exception as exc:
                logger.warning("[set-art] cover.jpg write failed for album %s: %s", album_id, exc)
        else:
            logger.info("[set-art] no on-disk folder for album %s — DB art updated only", album_id)

        return jsonify({"success": True, "album_id": album_id, "thumb_url": url,
                        "cover_written": cover_written})
    except Exception as e:
        logger.error("[set-art] failed for album %s: %s", album_id, e, exc_info=True)
        return jsonify({"error": str(e)}), 500


@bp.route('/api/album/<album_id>/art', methods=['DELETE'])
def clear_album_art_lock(album_id):
    """Hand this album's cover back to the media server.

    The art picker can only offer covers it finds on external sources, and for
    an obscure release it finds none — so without this there is no way to undo a
    pick. The current image stays until the next library sync replaces it."""
    try:
        db = get_database()
        if not db.clear_art_lock('album', album_id):
            return jsonify({"error": "Album not found"}), 404
        logger.info("[set-art] album %s art unlocked — the server owns it again", album_id)
        return jsonify({"success": True, "album_id": album_id, "art_locked": False})
    except Exception as e:
        logger.error("[set-art] unlock failed for album %s: %s", album_id, e, exc_info=True)
        return jsonify({"error": str(e)}), 500


@bp.route('/api/artist/<artist_id>/art', methods=['DELETE'])
def clear_artist_art_lock(artist_id):
    """Hand this artist's photo back to the media server. See the album twin."""
    try:
        # #1069: TEXT ids (Navidrome/Jellyfin) — never int() an artist id.
        artist_id = str(artist_id or '').strip()
        if not artist_id:
            return jsonify({"error": "Invalid artist id"}), 400
        db = get_database()
        if not db.clear_art_lock('artist', artist_id):
            return jsonify({"error": "Artist not found"}), 404
        logger.info("[set-artist-art] artist %s art unlocked — the server owns it again", artist_id)
        return jsonify({"success": True, "artist_id": artist_id, "art_locked": False})
    except Exception as e:
        logger.error("[set-artist-art] unlock failed for %s: %s", artist_id, e, exc_info=True)
        return jsonify({"error": str(e)}), 500


@bp.route('/api/artist/<artist_id>/art-options', methods=['GET'])
def get_artist_art_options(artist_id):
    """Candidate artist photos for the artist image picker (read-only).

    One candidate per CONNECTED metadata source (Spotify/Deezer/iTunes/AudioDB/
    Discogs/…), resolved concurrently: the artist's stored per-source id when
    the library row has one (exact), otherwise a name search on that source.
    Mirrors the album art-options endpoint."""
    try:
        # #1069 (matvei4iz): artists.id is TEXT since the id-columns migration —
        # Navidrome/Jellyfin ids are strings ("7dB07x8Q…"), and int() here made
        # the whole picker 400 for every non-Plex backend. Ids are opaque.
        artist_id = str(artist_id or '').strip()
        if not artist_id:
            return jsonify({"error": "Invalid artist id"}), 400
        db = get_database()
        artist_row = db.get_artist(artist_id)
        if artist_row is None:
            return jsonify({"error": "Artist not found"}), 404
        name = getattr(artist_row, 'name', '') or ''

        # Key by ROW id, not name: two artists sharing a name must not share
        # a cache slot. Empty results only stick for a minute — a transient
        # source failure used to poison the picker with 'no photos' for 15.
        cache_key = ('artist', artist_id)
        now = time.time()
        with _ART_OPTIONS_CACHE_LOCK:
            hit = _ART_OPTIONS_CACHE.get(cache_key)
            if hit and now - hit[0] < (_ART_OPTIONS_TTL_S if hit[1] else _ART_OPTIONS_EMPTY_TTL_S):
                return jsonify({"artist_id": artist_id, "count": len(hit[1]),
                                "candidates": hit[1], "cached": True})

        source_ids = {col: getattr(artist_row, col, None)
                      for col in ('spotify_artist_id', 'deezer_id', 'itunes_artist_id',
                                  'audiodb_id', 'discogs_id')}
        from core.metadata.artist_image import gather_artist_image_candidates
        candidates = gather_artist_image_candidates(name, source_ids)
        with _ART_OPTIONS_CACHE_LOCK:
            _ART_OPTIONS_CACHE[cache_key] = (now, candidates)
        return jsonify({"artist_id": artist_id, "count": len(candidates), "candidates": candidates})
    except Exception as e:
        # No blanket (TypeError, ValueError) → "Invalid artist id" anymore —
        # it mislabeled any deep ValueError as an id problem (#1069).
        logger.error("[artist-art-options] failed for %s: %s", artist_id, e, exc_info=True)
        return jsonify({"error": str(e)}), 500


@bp.route('/api/artist/<artist_id>/art', methods=['POST'])
def set_artist_art(artist_id):
    """Apply a photo chosen in the artist image picker — everywhere:

    1. SoulSync DB (``artists.thumb_url`` + ``artists.art_locked``, which is what
       stops the next library sync writing the server's photo back over it)
    2. the active media server (Plex/Jellyfin poster upload; Navidrome has no
       API and is covered by step 3)
    3. ``artist.jpg`` in the artist's folder on disk (what Navidrome reads;
       Plex/Jellyfin also honor it as a fallback) + a Navidrome scan nudge

    Steps 2 and 3 are best-effort — the response reports each target so the
    UI can say exactly what happened. Body: ``{"url": "<image url>"}``."""
    try:
        data = request.get_json(silent=True) or {}
        url = (data.get('url') or '').strip()
        if not url:
            return jsonify({"error": "url is required"}), 400

        # #1069: TEXT ids (Navidrome/Jellyfin) — never int() an artist id.
        artist_id = str(artist_id or '').strip()
        if not artist_id:
            return jsonify({"error": "Invalid artist id"}), 400
        db = get_database()
        artist_row = db.get_artist(artist_id)
        if artist_row is None:
            return jsonify({"error": "Artist not found"}), 404
        artist_name = getattr(artist_row, 'name', '') or ''

        # Download FIRST (server upload + disk write share the bytes) and
        # validate: a custom URL that resolves to an HTML page must abort
        # before anything is pinned. A failed download stays best-effort —
        # hotlink-protected art can still render in the browser.
        image_bytes = None
        try:
            import urllib.request
            req = urllib.request.Request(url, headers={"User-Agent": "SoulSync/1.0",
                                                       "Accept": "image/*"})
            with urllib.request.urlopen(req, timeout=15) as resp:   # noqa: S310 (user-chosen art URL)
                image_bytes = resp.read() or None
        except Exception as exc:
            logger.warning("[set-artist-art] image download failed: %s", exc)
        if image_bytes is not None and not _looks_like_image(image_bytes):
            return jsonify({"error": "That URL doesn't point to an image"}), 400

        if not db.set_artist_thumb_url(artist_id, url):
            return jsonify({"error": "Could not update artist"}), 500

        # 2. Active media server poster (Plex/Jellyfin have APIs; Navidrome's
        #    update_artist_poster is a documented no-op — disk write covers it).
        server_updated = False
        if image_bytes and media_server_engine:
            try:
                active = config_manager.get_active_media_server()
                client = media_server_engine.client(active)
                if client and hasattr(client, 'update_artist_poster'):
                    server_artist = None
                    if hasattr(client, '_search_artists_by_name'):        # Plex
                        matches = client._search_artists_by_name(title=artist_name, limit=1)
                        server_artist = matches[0] if matches else None
                    elif hasattr(client, 'get_artist_by_id'):             # Jellyfin
                        server_artist = client.get_artist_by_id(str(artist_id))
                    if server_artist is not None:
                        server_updated = bool(client.update_artist_poster(server_artist, image_bytes))
            except Exception as exc:
                logger.warning("[set-artist-art] server poster update failed: %s", exc)

        # 3. artist.jpg on disk (Navidrome's mechanism) + scan nudge.
        disk_written = False
        try:
            from core.library.artist_image import derive_artist_folder, write_artist_jpg
            albums = db.get_albums_by_artist(artist_id) or []
            artist_folder = None
            for album in albums:
                for tr in (db.get_tracks_by_album(album.id) or []):
                    raw = getattr(tr, 'file_path', None)
                    if not raw:
                        continue
                    resolved = _resolve_library_file_path(raw) or raw
                    if resolved and os.path.exists(resolved):
                        artist_folder = derive_artist_folder(os.path.dirname(resolved))
                        break
                if artist_folder:
                    break
            if artist_folder and image_bytes:
                ok, detail = write_artist_jpg(artist_folder, image_bytes, overwrite=True)
                disk_written = bool(ok)
                if not ok:
                    logger.warning("[set-artist-art] artist.jpg write failed: %s", detail)
                elif media_server_engine:
                    nav = media_server_engine.client('navidrome')
                    if nav and config_manager.get_active_media_server() == 'navidrome':
                        try:
                            nav.trigger_library_scan()
                        except Exception:
                            logger.debug("[set-artist-art] navidrome scan nudge failed", exc_info=True)
        except Exception as exc:
            logger.warning("[set-artist-art] disk write failed: %s", exc)

        # Invalidate the candidates cache so a re-open reflects reality.
        # (#1069: this popped a NAME-keyed entry while the cache stores by ID —
        # a dead pop, leaving stale candidates for a minute after Apply.)
        with _ART_OPTIONS_CACHE_LOCK:
            _ART_OPTIONS_CACHE.pop(('artist', artist_id), None)

        return jsonify({"success": True, "artist_id": artist_id, "thumb_url": url,
                        "server_updated": server_updated, "disk_written": disk_written})
    except Exception as e:
        logger.error("[set-artist-art] failed for %s: %s", artist_id, e, exc_info=True)
        return jsonify({"error": str(e)}), 500


@bp.route('/api/graph/library', methods=['GET'])
def get_library_graph():
    """Library "Taste Map": EVERY library artist as a node, grouped by genre + wired by similarity.

    Returns {"nodes": [{key,label,kind,owned,primary_genre,popularity,thumb} | {key,label,kind,genre}],
    "edges": [{source,target,weight,kind}]}. Every artist is included (attached to a per-genre hub node
    so a force layout clusters them); similarity edges come from similar_artists (resolved in-memory —
    a SQL self-join is too slow at 75k rows).
    """
    try:
        from core.graph.artist_graph import build_genre_grouped_map
        from core.graph.library_artists import load_library_artists
        db = get_database()
        conn = db._get_connection()
        try:
            cur = conn.cursor()
            owned, meta, artists = load_library_artists(cur)
            rows = cur.execute(
                "SELECT source_artist_id, similar_artist_name, similar_artist_spotify_id, "
                "similar_artist_deezer_id, similar_artist_itunes_id, occurrence_count, popularity "
                "FROM similar_artists WHERE profile_id = ?", (get_current_profile_id(),)
            ).fetchall()
        finally:
            conn.close()
        graph = build_genre_grouped_map(artists, rows, owned, artist_meta=meta)
        n_artists = sum(1 for n in graph["nodes"] if n.get("kind") == "artist")
        n_genres = sum(1 for n in graph["nodes"] if n.get("kind") == "genre")
        return jsonify({**graph, "counts": {
            "nodes": len(graph["nodes"]), "edges": len(graph["edges"]),
            "artists": n_artists, "genres": n_genres,
        }})
    except Exception as e:
        logger.error("[library-graph] failed: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


@bp.route('/api/graph/discovery', methods=['GET'])
def get_discovery_graph():
    """Discovery Web: owned artists as anchors + their UNOWNED similar artists as discovery candidates.

    Candidates are enriched from the metadata cache (image/genres/popularity) so they render as real
    artists you could add, not bare dots. Returns the WHOLE frontier by default — its real size is
    modest (only artists whose similars were fetched can anchor); ``seed``/``per`` query params
    optionally trim to the top anchors / top candidates per anchor.
    """
    try:
        from core.graph.artist_graph import build_discovery_map

        def _opt_int(name):
            raw = request.args.get(name)
            if raw is None:
                return None
            try:
                v = int(raw)
            except (TypeError, ValueError):
                return None
            return max(1, v) if v > 0 else None

        seed = _opt_int('seed')
        per = _opt_int('per')
        db = get_database()
        conn = db._get_connection()
        try:
            cur = conn.cursor()
            owned, owned_meta, rows = _discovery_load_inputs(cur)
            graph = build_discovery_map(rows, owned, owned_meta, seed_count=seed, per_anchor=per)
        finally:
            conn.close()

        n_owned = sum(1 for n in graph["nodes"] if n.get("kind") == "owned")
        n_disc = sum(1 for n in graph["nodes"] if n.get("kind") == "discovery")
        return jsonify({**graph, "counts": {
            "nodes": len(graph["nodes"]), "edges": len(graph["edges"]),
            "owned": n_owned, "discovery": n_disc,
        }})
    except Exception as e:
        logger.error("[discovery-graph] failed: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


def _discovery_load_inputs(cur):
    """Shared input load for the discovery routes: owned artists + this profile's similar_artists.

    similar_artists is per-profile (unique on profile_id + source + name); without the filter, a
    multi-profile install double-counts every anchor->target pair and leaks one profile's discovery
    taste into another's graph.

    Rows include the table's OWN image_url/genres columns (~99% of rows carry image + real
    popularity) — enriching from metadata_cache_entities instead measured 18-250s per request
    (random reads into a 1.3M-row table), for data these rows already have.
    """
    from core.graph.library_artists import load_library_artists

    owned, owned_meta, _nodes = load_library_artists(cur)
    rows = cur.execute(
        "SELECT source_artist_id, similar_artist_name, similar_artist_spotify_id, "
        "similar_artist_deezer_id, similar_artist_itunes_id, occurrence_count, popularity, "
        "image_url, genres "
        "FROM similar_artists WHERE profile_id = ?", (get_current_profile_id(),)
    ).fetchall()
    return owned, owned_meta, rows


@bp.route('/api/graph/discovery/expand', methods=['POST'])
def expand_discovery_graph():
    """Expand-on-click for the Discovery Web: one node's similar artists, minus what's on screen.

    POST JSON: ``key`` (normalized artist name), ``ids`` (external ids — for unowned candidates whose
    similars are keyed by id), ``exclude`` (node keys already in the graph — JSON body because artist
    names can contain commas), ``per`` (max new nodes). Same node/edge shape as /api/graph/discovery.
    """
    try:
        from core.graph.artist_graph import expand_discovery_node
        payload = request.get_json(silent=True) or {}
        node_key = str(payload.get('key') or '').strip().lower()
        if not node_key:
            return jsonify({"error": "missing key"}), 400
        node_ids = [i for i in (payload.get('ids') or []) if i]
        exclude = {k for k in (payload.get('exclude') or []) if k}
        try:
            per = int(payload.get('per') or 10)
        except (TypeError, ValueError):
            per = 10
        per = max(1, min(per, 30))
        db = get_database()
        conn = db._get_connection()
        try:
            cur = conn.cursor()
            owned, owned_meta, rows = _discovery_load_inputs(cur)
            graph = expand_discovery_node(rows, owned, node_key, node_ids, owned_meta, per=per, exclude=exclude)
        finally:
            conn.close()
        return jsonify({**graph, "counts": {"nodes": len(graph["nodes"]), "edges": len(graph["edges"])}})
    except Exception as e:
        logger.error("[discovery-expand] failed: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


@bp.route('/api/graph/discovery/preview/<deezer_id>', methods=['GET'])
def get_discovery_preview(deezer_id):
    """30-second Deezer preview for a discovery candidate's top track (hear it before you add it).

    Deliberately Deezer-only + explicit: the generic top-tracks endpoint routes by the CONFIGURED
    primary source and resolves ids via the library DB — a candidate isn't in the library, and its
    Deezer id would be garbage to a Spotify query (whose previews are deprecated anyway).
    """
    try:
        if not str(deezer_id).isdigit():
            return jsonify({"success": False, "reason": "not_a_deezer_id"}), 400
        client = _get_deezer_client()
        if not client:
            return jsonify({"success": False, "reason": "deezer_unavailable"}), 503
        tracks = client.get_artist_top_tracks(str(deezer_id), limit=3) or []
        for t in tracks:
            if t.get('preview_url'):
                return jsonify({
                    "success": True,
                    "track": t.get('name'),
                    "artist": ((t.get('artists') or [{}])[0] or {}).get('name'),
                    "preview_url": t['preview_url'],
                })
        return jsonify({"success": False, "reason": "no_preview"})
    except Exception as e:
        logger.error("[discovery-preview] failed: %s", e, exc_info=True)
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/library/artist/<int:artist_id>/thumb', methods=['GET'])
def get_library_artist_thumb(artist_id):
    """Browser-loadable thumb URL for ONE library artist, by library DB id.

    Used by the Artist Web side panel. Two deliberate choices:
    - Lazily per-click, not eagerly for every node: normalize_image_url registers the URL in the
      image cache (a DB write transaction each) — doing that for ~5k artists per graph load takes
      minutes; one artist per panel open is instant.
    - By LIBRARY id against the artists table — the generic /api/artist/<id>/image resolver sends
      whatever id it gets to external providers, so a library row id returned whichever Deezer/iTunes
      artist happened to own that number (wrong photo, essentially always).
    """
    try:
        db = get_database()
        conn = db._get_connection()
        try:
            cur = conn.cursor()
            row = cur.execute("SELECT image_url AS thumb_url FROM lib2_artists WHERE id = ?", (artist_id,)).fetchone()
        finally:
            conn.close()
        thumb = row['thumb_url'] if row else None
        url = fix_artist_image_url(thumb) if thumb else None
        return jsonify({"success": True, "image_url": url})
    except Exception as e:
        logger.error("[artist-thumb] failed: %s", e, exc_info=True)
        return jsonify({"success": False, "image_url": None}), 500


@bp.route('/api/library/export/m3u', methods=['GET'])
def export_library_m3u():
    """Download an extended-M3U playlist of the entire library. Always current (built on request)."""
    try:
        db = get_database()
        entries = db.get_all_library_tracks_for_export()
        from core.library.m3u_export import build_m3u
        content = build_m3u(entries,
                            entry_base_path=config_manager.get('m3u_export.entry_base_path', '') or '',
                            rewrite_from=config_manager.get('m3u_export.rewrite_from', '') or '',
                            rewrite_to=config_manager.get('m3u_export.rewrite_to', '') or '')
        return Response(
            content,
            mimetype='audio/x-mpegurl',
            headers={'Content-Disposition': 'attachment; filename="soulsync_library.m3u"'},
        )
    except Exception as e:
        logger.error("[library-m3u] export failed: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


@bp.route('/api/album/<album_id>/tracks', methods=['GET'])
def get_album_tracks(album_id):
    """Get tracks for specific album formatted for download missing tracks modal"""
    try:
        album_name = request.args.get('name', '').strip()
        artist_name = request.args.get('artist', '').strip()
        source_override = request.args.get('source', '').strip().lower()
        if source_override == 'hydrabase':
            plugin = request.args.get('plugin', '').strip().lower()
            if plugin in ('itunes', 'deezer'):
                source_override = plugin
            elif album_id.isdigit():
                source_override = 'itunes'
            else:
                source_override = 'spotify'

        _mark_request_free_ok_for_spotify(source_override)

        from core.metadata_service import get_artist_album_tracks as _get_artist_album_tracks

        result = _get_artist_album_tracks(
            album_id,
            artist_name=artist_name,
            album_name=album_name,
            source_override=source_override or None,
        )

        if not result.get('success'):
            return jsonify({"error": result.get('error', 'Album not found')}), result.get('status_code', 404)

        logger.info(
            "Successfully formatted %s tracks for album %s",
            len(result.get('tracks', [])),
            result.get('album', {}).get('name', album_name or album_id),
        )
        return jsonify({
            'success': True,
            'album': result['album'],
            'tracks': result['tracks'],
            'source': result.get('source'),
            'source_priority': result.get('source_priority', []),
            'resolved_album_id': result.get('resolved_album_id'),
        })

    except Exception as e:
        logger.exception("Error fetching album tracks for album %s", album_id)
        return jsonify({"error": str(e)}), 500

@bp.route('/api/artist/<artist_id>/concerts', methods=['GET'])
def artist_concerts(artist_id):
    """Upcoming dates and recent setlists for one artist.

    Name comes from the query string because this is called from source-only
    artist pages too, where there is no library row to read it off. The MBID is
    optional but worth passing: setlist.fm's name matching is loose enough that
    two bands sharing a name return each other's shows.

    Always 200. Both providers are optional, and a page section that 500s
    because the user never configured a concert API is worse than one that says
    "not set up".
    """
    from core.concerts_client import artist_concerts as _lookup

    name = (request.args.get('name') or '').strip()
    mbid = (request.args.get('mbid') or '').strip()
    if not name:
        # fall back to the library row when the caller did not say
        try:
            from database.music_database import MusicDatabase
            row = MusicDatabase().get_artist_by_id(artist_id)
            name = str((row or {}).get('name') or '').strip()
            mbid = mbid or str((row or {}).get('musicbrainz_id') or '').strip()
        except Exception:   # noqa: BLE001 - no row is just "no name"
            logger.debug('no library row for artist %s, concerts need a ?name',
                         artist_id, exc_info=True)
    if not name:
        return jsonify({"artist": "", "upcoming": [], "setlists": [],
                        "providers": {}}), 200
    try:
        return jsonify(_lookup(name, mbid=mbid)), 200
    except Exception as e:   # noqa: BLE001
        logger.error("Concert lookup failed for %s: %s", name, e)
        return jsonify({"artist": name, "upcoming": [], "setlists": [],
                        "providers": {}, "error": str(e)}), 200


@bp.route('/api/artist/<artist_id>/record', methods=['GET'])
def get_artist_db_record(artist_id):
    """Return the COMPLETE database record for a library artist — every column of
    the ``artists`` row (all source IDs + match statuses, cached bios / tags /
    similar / urls, timestamps, soul_id, etc.) plus owned album/track counts.

    Powers the artist-detail "DB Record" inspector. JSON-encoded text columns
    (genres, aliases, lastfm_tags/similar, discogs_urls, …) are decoded into real
    arrays/objects so the dump is clean rather than escaped strings.
    """
    try:
        database = get_database()
        conn = database._get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM lib2_artists WHERE id = ?", (str(artist_id),))
            row = cur.fetchone()
            if row is None:
                return jsonify({"success": False, "error": "Artist not found in library"}), 404

            record = {}
            for key in row.keys():
                val = row[key]
                if isinstance(val, str):
                    s = val.strip()
                    if s and s[0] in '[{':
                        try:
                            val = json.loads(s)
                        except Exception:  # noqa: S110 — leave non-JSON text as-is
                            pass
                record[key] = val

            # Owned counts off the v2 catalogue. Tracks hang off albums, not off
            # the artist row, so the track count walks album -> track rather than
            # reading a legacy tracks.artist_id that does not exist here.
            counts = {}
            count_sql = {
                'albums': "SELECT COUNT(*) FROM lib2_albums WHERE primary_artist_id = ?",
                'tracks': ("SELECT COUNT(*) FROM lib2_tracks t "
                           "JOIN lib2_albums al ON al.id = t.album_id "
                           "WHERE al.primary_artist_id = ?"),
            }
            for label, sql in count_sql.items():
                try:
                    cur.execute(sql, (str(artist_id),))
                    counts[label] = cur.fetchone()[0]
                except Exception:
                    counts[label] = None
        finally:
            conn.close()

        return jsonify({
            "success": True,
            "artist_id": str(artist_id),
            "counts": counts,
            "record": record,
        })
    except Exception as e:
        logger.error(f"Artist DB record fetch failed for {artist_id}: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.route('/api/artist/<artist_id>/download-discography', methods=['POST'])
def download_discography(artist_id):
    """Add selected albums from an artist's discography to the wishlist.

    Resolves each album through the same source-aware path that the
    individual-album flow uses, so albums whose IDs come from a
    fallback/provider-specific source (e.g. Deezer-formatted IDs surfaced
    via Hydrabase) don't fail with "Album not found" when the primary
    source can't look them up directly.
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "request body required"}), 400

        # Preferred payload: per-album metadata so each album can be resolved
        # through its own source. Falls back to the legacy album_ids list,
        # in which case every album is looked up under the artist-level source.
        albums_payload = data.get('albums')
        legacy_album_ids = data.get('album_ids')
        if not albums_payload and not legacy_album_ids:
            return jsonify({"success": False, "error": "albums or album_ids required"}), 400

        artist_name = data.get('artist_name', 'Unknown Artist')
        artist_source = (data.get('source') or '').strip().lower() or None

        if albums_payload:
            album_entries = [
                {
                    'id': str(a.get('id', '')),
                    'name': a.get('name') or a.get('title') or '',
                    'source': (a.get('source') or '').strip().lower() or artist_source,
                    'artist_name': a.get('artist_name') or artist_name,
                }
                for a in albums_payload if a.get('id')
            ]
        else:
            album_entries = [
                {
                    'id': str(aid),
                    'name': '',
                    'source': artist_source,
                    'artist_name': artist_name,
                }
                for aid in legacy_album_ids if aid
            ]

        if not album_entries:
            return jsonify({"success": False, "error": "no valid albums in payload"}), 400

        from database.music_database import MusicDatabase
        from core.metadata.album_tracks import get_artist_album_tracks
        from core.metadata.discography_filters import (
            content_type_skip_reason,
            load_global_content_filter_settings,
            track_already_owned,
            track_artist_matches,
        )
        db = MusicDatabase()
        profile_id = get_current_profile_id()
        # Honor the same content-type filters the watchlist scanner uses
        # (issue #559). One read at the top — settings don't change
        # mid-stream and the four bool reads aren't worth re-running per
        # track.
        content_settings = load_global_content_filter_settings(config_manager)
        # Library-ownership check uses the active media server so the
        # match is scoped to the same source whose tracks the user can
        # actually see in their library. None falls through to a
        # cross-server search inside check_track_exists.
        active_server = None
        try:
            active_server = config_manager.get_active_media_server()
        except Exception as e:
            logger.debug("active media server lookup failed: %s", e)

        # Pre-fetch the artist's owned library tracks ONCE so the per-track
        # ownership check scores in-memory instead of firing fuzzy SQL scans
        # against the whole library for every track (which, on a large library
        # and an artist the user owns nothing of, was ~15-30s PER TRACK — every
        # title/artist variation fell through to a full-table fuzzy fallback).
        # Same batched path the discography backfill job + completion-stream use.
        # Crucially we pass an empty list (not None) when nothing is owned, so the
        # owns-nothing case still takes the fast in-memory path → instant.
        owned_candidate_tracks = []
        try:
            cand_albums = db.get_candidate_albums_for_artist(
                artist_name, server_source=active_server
            )
            if cand_albums:
                owned_candidate_tracks = db.get_candidate_tracks_for_albums(
                    [a.id for a in cand_albums]
                ) or []
        except Exception as _cand_err:
            logger.debug("Discography: candidate pre-fetch failed for %s: %s", artist_name, _cand_err)
            owned_candidate_tracks = []

        total_added = 0
        total_skipped = 0
        total_skipped_artist = 0
        total_skipped_filter = 0
        total_skipped_owned = 0

        def generate_ndjson():
            nonlocal total_added, total_skipped, total_skipped_artist, total_skipped_filter, total_skipped_owned

            for entry in album_entries:
                album_id = entry['id']
                hint_album_name = entry['name']
                hint_artist = entry['artist_name']
                source_override = entry['source']
                try:
                    result = get_artist_album_tracks(
                        album_id,
                        artist_name=hint_artist,
                        album_name=hint_album_name,
                        source_override=source_override,
                    )

                    if not result.get('success'):
                        message = result.get('error') or 'Album not found'
                        yield json.dumps({
                            "album_id": album_id,
                            "name": hint_album_name or album_id,
                            "status": "error",
                            "message": message,
                        }) + '\n'
                        continue

                    album = result.get('album', {}) or {}
                    tracks = result.get('tracks', []) or []
                    album_name = album.get('name') or hint_album_name or 'Unknown'
                    album_images = album.get('images') or (
                        [{'url': album['image_url']}] if album.get('image_url') else []
                    )
                    album_type = album.get('album_type', 'album')
                    release_date = album.get('release_date', '') or ''
                    album_artists = album.get('artists') or [{'name': hint_artist}]
                    resolved_album_id = result.get('resolved_album_id') or album.get('id') or album_id
                    resolved_source = result.get('source') or source_override or 'unknown'

                    if not tracks:
                        yield json.dumps({
                            "album_id": album_id,
                            "name": album_name,
                            "status": "error",
                            "message": "No tracks",
                        }) + '\n'
                        continue

                    added = 0
                    skipped = 0
                    skipped_artist = 0
                    skipped_filter = 0
                    skipped_owned = 0

                    for track in tracks:
                        track_name = track.get('name', '')
                        if not track_name:
                            continue
                        track_artists = track.get('artists', []) or album_artists
                        track_id = track.get('id', '')

                        # Issue #559: drop tracks where the requested
                        # artist isn't in the track's artists list
                        # (cross-artist compilation / appears_on
                        # contamination). Keeps features.
                        if not track_artist_matches(track_artists, hint_artist):
                            skipped_artist += 1
                            continue

                        # Issue #559: honor watchlist global content-type
                        # filters (live / remix / acoustic / instrumental)
                        # for one-off discography downloads too — same
                        # contract as the discography backfill repair job.
                        skip_reason = content_type_skip_reason(track_name, album_name, content_settings)
                        if skip_reason:
                            skipped_filter += 1
                            continue

                        # Skowl (Discord): clicking Download Discography
                        # twice re-queued every track because add_to_wishlist
                        # only dedups against the wishlist, not the library.
                        # Same library-ownership check the discography
                        # backfill repair job uses. Format-agnostic so
                        # Blasphemy mode (FLAC→MP3) doesn't false-miss.
                        if track_already_owned(db, track_name, hint_artist, album_name, active_server,
                                               candidate_tracks=owned_candidate_tracks):
                            skipped_owned += 1
                            continue

                        spotify_track_data = {
                            'id': track_id,
                            'name': track_name,
                            'artists': track_artists if isinstance(track_artists, list) else [{'name': str(track_artists)}],
                            'album': {
                                'id': str(resolved_album_id),
                                'name': album_name,
                                'artists': album_artists,
                                'images': album_images,
                                'album_type': album_type,
                                'release_date': release_date,
                                'total_tracks': len(tracks),
                            },
                            'duration_ms': track.get('duration_ms', 0),
                            'explicit': track.get('explicit', False),
                            'track_number': track.get('track_number', 0),
                            'disc_number': track.get('disc_number', 1),
                            'uri': track.get('uri', ''),
                            'preview_url': track.get('preview_url'),
                            'external_urls': track.get('external_urls', {}),
                            'is_local': False,
                            '_source': resolved_source,
                        }

                        try:
                            was_added = db.add_to_wishlist(
                                spotify_track_data=spotify_track_data,
                                failure_reason="Added via Download Discography",
                                source_type="discography",
                                source_info=json.dumps({
                                    'artist_name': hint_artist,
                                    'album_name': album_name,
                                    'album_type': album_type,
                                    'source': resolved_source,
                                }),
                                profile_id=profile_id,
                            )
                            if was_added:
                                added += 1
                            else:
                                skipped += 1
                        except Exception:
                            skipped += 1

                    total_added += added
                    total_skipped += skipped
                    total_skipped_artist += skipped_artist
                    total_skipped_filter += skipped_filter
                    total_skipped_owned += skipped_owned
                    logger.warning(
                        f"[Discography] {album_name} ({resolved_source}): {added} added, "
                        f"{skipped} skipped (wishlist), {skipped_artist} skipped (artist mismatch), "
                        f"{skipped_filter} skipped (content filter), "
                        f"{skipped_owned} skipped (already in library)"
                    )
                    yield json.dumps({
                        "album_id": album_id,
                        "name": album_name,
                        "status": "done",
                        "tracks_added": added,
                        "tracks_skipped": skipped,
                        "tracks_skipped_artist": skipped_artist,
                        "tracks_skipped_filter": skipped_filter,
                        "tracks_skipped_owned": skipped_owned,
                        "tracks_total": len(tracks),
                        "source": resolved_source,
                    }) + '\n'

                except Exception as album_err:
                    yield json.dumps({
                        "album_id": album_id,
                        "name": hint_album_name or album_id,
                        "status": "error",
                        "message": str(album_err),
                    }) + '\n'

            logger.warning(
                f"[Discography] Complete for {artist_name}: {total_added} tracks added, "
                f"{total_skipped} skipped (wishlist), {total_skipped_artist} skipped (artist mismatch), "
                f"{total_skipped_filter} skipped (content filter), "
                f"{total_skipped_owned} skipped (already in library) across {len(album_entries)} albums"
            )
            yield json.dumps({
                "status": "complete",
                "total_added": total_added,
                "total_skipped": total_skipped,
                "total_skipped_artist": total_skipped_artist,
                "total_skipped_filter": total_skipped_filter,
                "total_skipped_owned": total_skipped_owned,
                "total_albums": len(album_entries),
            }) + '\n'

        # Response instead of app.response_class: identical class, no app import
        return Response(generate_ndjson(), mimetype='application/x-ndjson', headers={'X-Accel-Buffering': 'no'})

    except Exception as e:
        logger.error(f"Error in download discography: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@bp.route('/api/artist/<artist_id>/completion', methods=['POST'])
def check_artist_discography_completion(artist_id):
    """Check completion status for artist's albums and singles"""
    try:
        data = request.get_json()
        if not data or 'discography' not in data:
            return jsonify({"error": "Missing discography data"}), 400
        from core.metadata_service import check_artist_discography_completion as _check_artist_discography_completion

        discography = data['discography']
        source_override = (data.get('source') or '').strip().lower() or None
        result = _check_artist_discography_completion(
            discography,
            artist_name=data.get('artist_name', 'Unknown Artist'),
            source_override=source_override,
        )
        return jsonify(result)
    except Exception as e:
        logger.error(f"Error checking discography completion: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@bp.route('/api/artist/<artist_id>/completion-stream', methods=['POST'])
def check_artist_discography_completion_stream(artist_id):
    """Stream completion status for artist's albums and singles one by one"""
    # Capture request data BEFORE the generator function
    try:
        data = request.get_json()
        if not data or 'discography' not in data:
            return jsonify({"error": "Missing discography data"}), 400
    except Exception as e:
        return jsonify({"error": "Invalid request data"}), 400

    # Extract data for the generator
    discography = data['discography']
    artist_name = data.get('artist_name', 'Unknown Artist')
    source_override = (data.get('source') or '').strip().lower() or None
    from core.metadata_service import iter_artist_discography_completion_events

    def generate_completion_stream():
        try:
            logger.info(f"Starting streaming completion check for artist: {artist_name}")
            for event in iter_artist_discography_completion_events(
                discography,
                artist_name=artist_name,
                source_override=source_override,
            ):
                yield f"data: {json.dumps(event)}\n\n"
                if event.get('type') in ('album_completion', 'single_completion'):
                    # Small delay to make the streaming effect visible
                    time.sleep(0.1)  # 100ms delay between items
        except Exception as e:
            logger.error(f"Error in streaming completion check: {e}")
            import traceback
            traceback.print_exc()
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"

    return Response(
        generate_completion_stream(),
        content_type='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'Cache-Control'
        }
    )
