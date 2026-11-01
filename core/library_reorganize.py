"""Move a library album's files to where the naming template puts them.

Where a file belongs is planned from the catalogue
(:mod:`core.library2.reorganize_plan`): the library's own titles, credits
and positions, manual fixes included. This module is the executor that
acts on that plan (:func:`reorganize_album_rename_only`), plus the shared
pieces the planner borrows: the post-process context the path builder
reads, the album info, the deleted/quarantine guard and the source lists.

It used to plan on its own too, from a provider's tracklist or from each
file's tags, and to send every file through staging and the download
post-processing. Neither is reached any more: a provider or a tag reader
could disagree with the catalogue the Library page shows, and the
preview with the move.
"""

import errno
import hashlib
import json
import os
import re
import shutil
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from core.metadata_service import (
    get_client_for_source,
    get_primary_source,
    get_source_priority,
)
from core.source_ids import id_column
from utils.logging_config import get_logger

logger = get_logger("library_reorganize")


def _canonical_file_path(path: Any) -> str:
    """Return one comparison form for relative, absolute and symlinked paths.

    The library resolver normally returns an absolute container path while the
    path builder may preserve a configured spelling such as ``./Transfer``.
    ``normpath`` alone leaves those different even when they name the same
    location, which made the old finalizer unlink the user's only copy as the
    supposed source.
    """
    if path in (None, ""):
        return ""
    try:
        return os.path.normcase(os.path.realpath(os.path.abspath(os.fspath(path))))
    except (TypeError, ValueError, OSError):
        return ""


def _same_physical_file(left: Any, right: Any) -> bool:
    """Whether two path spellings designate the same physical file/location."""
    left_canonical = _canonical_file_path(left)
    right_canonical = _canonical_file_path(right)
    if not left_canonical or not right_canonical:
        return False
    if left_canonical == right_canonical:
        return True
    try:
        return bool(os.path.exists(left) and os.path.exists(right)
                    and os.path.samefile(left, right))
    except (OSError, TypeError, ValueError):
        return False


def resolve_album_profile_id(db, album_id, tracks=None, resolve_file_path_fn=None) -> Optional[int]:
    """the owning profile for a reorganize operation (#1504).

    File location wins over the DB: resolve the first track's on-disk path
    and match it against own-library roots. Falls back to the album row's
    ``owner_profile_id``. Returns None for the shared library (unchanged
    behavior)."""
    from core.imports.paths import profile_id_for_path
    # 1. file location wins — where the files actually sit right now
    try:
        track_list = tracks
        if track_list is None:
            _, track_list = load_album_and_tracks(db, album_id)
        for t in track_list or []:
            db_path = t.get('file_path') if isinstance(t, dict) else None
            if not db_path:
                continue
            resolved = resolve_file_path_fn(db_path) if resolve_file_path_fn else db_path
            if resolved:
                pid = profile_id_for_path(resolved)
                if pid:
                    return pid
                break  # resolved but shared — don't keep looking
    except Exception as e:
        logger.debug("[Reorganize] profile resolution from path failed: %s", e)
    # 2. DB fallback: whose library the album's files are recorded in. Ours:
    # Library v2 keeps the owner per file, not per album row.
    try:
        conn = db._get_connection()
        try:
            row = conn.execute(
                "SELECT f.owner_profile_id FROM lib2_track_files f"
                "  JOIN lib2_tracks t ON t.id = f.track_id"
                " WHERE t.album_id = ? AND COALESCE(f.file_state,'active') = 'active'"
                "   AND f.owner_profile_id IS NOT NULL"
                " ORDER BY f.is_primary DESC, f.id LIMIT 1", (str(album_id),)
            ).fetchone()
            if row and row[0]:
                return int(row[0])
        finally:
            conn.close()
    except Exception as e:
        logger.debug("[Reorganize] profile resolution from album row failed: %s", e)
    return None


SUPPORTED_SOURCES = ('spotify', 'itunes', 'deezer', 'discogs', 'hydrabase')

# Compatibility aliases projected by :func:`load_album_and_tracks`.
_ALBUM_ID_COLUMNS = {
    'spotify': 'spotify_album_id',
    'itunes': 'itunes_album_id',
    'deezer': 'deezer_id',
    'discogs': 'discogs_id',
    'hydrabase': 'soul_id',
    'musicbrainz': 'musicbrainz_release_id',
}

# Human-facing label for each source.
SOURCE_LABELS = {
    'spotify': 'Spotify',
    'itunes': 'Apple Music (iTunes)',
    'deezer': 'Deezer',
    'discogs': 'Discogs',
    'hydrabase': 'Hydrabase',
}


def _extract_source_ids(album_data: dict) -> Dict[str, str]:
    """Pull the per-source album-ID strings off an album row."""
    return {
        source: (album_data.get(column) or '')
        for source, column in _ALBUM_ID_COLUMNS.items()
    }


def available_sources_for_album(album_data: dict) -> List[dict]:
    """Return the list of metadata sources the user can pick for this
    album's reorganize. Every entry has both (a) a stored album ID on
    the local row AND (b) an authenticated / configured client on this
    SoulSync instance.

    Returns entries in source-priority order (preferred source first).
    Each entry is ``{'source': str, 'label': str}``. No API calls —
    purely local inspection.
    """
    source_ids = _extract_source_ids(album_data)
    try:
        primary = get_primary_source()
    except Exception:
        primary = 'deezer'

    out = []
    for source in get_source_priority(primary):
        if source not in SUPPORTED_SOURCES:
            continue
        if not source_ids.get(source):
            continue
        if get_client_for_source(source) is None:
            continue
        out.append({
            'source': source,
            'label': SOURCE_LABELS.get(source, source),
        })
    return out


def authed_sources() -> List[dict]:
    """Return all metadata sources the user has authed/configured on
    this SoulSync instance. Doesn't require any album-specific stored
    ID — used by the bulk "Reorganize All" picker where each album
    has its own ID coverage and we just want to know which sources
    are reachable. Returned in priority order."""
    try:
        primary = get_primary_source()
    except Exception:
        primary = 'deezer'

    out = []
    for source in get_source_priority(primary):
        if source not in SUPPORTED_SOURCES:
            continue
        if get_client_for_source(source) is None:
            continue
        out.append({
            'source': source,
            'label': SOURCE_LABELS.get(source, source),
        })
    return out


_UNKNOWN_ARTIST_NAMES = {'unknown artist', 'unknown', ''}


def _is_unknown_artist(artist_name: Optional[str]) -> bool:
    if not artist_name:
        return True
    return str(artist_name).strip().lower() in _UNKNOWN_ARTIST_NAMES


def _source_album_artist(api_album) -> str:
    """the album artist the metadata source gives the album, '' when it gives
    none. every source shape: a spotify-style artists list, a bare artist or
    artist_name, or a tag-mode album_artist."""
    if not isinstance(api_album, dict):
        return ''
    artists = api_album.get('artists')
    if isinstance(artists, list) and artists:
        first = artists[0]
        name = first.get('name') if isinstance(first, dict) else first
    else:
        name = (api_album.get('album_artist') or api_album.get('artist')
                or api_album.get('artist_name'))
        if isinstance(name, dict):
            name = name.get('name')
    name = str(name or '').strip()
    return '' if _is_unknown_artist(name) else name


# Featured-artist credit: "(feat. X)" / "[ft X]" / a trailing "feat. X". The
# parenthesised form is stripped wherever it appears; the bare form only when
# something follows it (so a song literally named "The Feat" is left alone, and
# "Defeat"/"Lift" never trip the word-boundary). Case-insensitive.
_FEAT_RE = re.compile(
    r"""\s*[\(\[]\s*(?:feat|ft|featuring)\b\.?[^)\]]*[\)\]]   # (feat. X) / [ft. X]
        | \s+(?:feat|ft|featuring)\b\.?\s+\S.*$               # trailing  feat. X ...
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Detection only (does a title carry ANY feat credit?) — word-boundary so
# "Defeat"/"Lift" never trip it. Used to avoid double-crediting.
_FEAT_DETECT_RE = re.compile(r"\b(?:feat|ft|featuring)\b", re.IGNORECASE)


def _feat_in_title_enabled() -> bool:
    """Whether the user asked featured artists to live in the track title
    (Settings → Metadata). Read live so the reorganize honors the same
    switch the download path does. Isolated in a helper so tests can
    monkeypatch it without a full config manager."""
    try:
        from core.settings import config_manager
        return bool(config_manager.get("metadata_enhancement.tags.feat_in_title", False))
    except Exception:
        return False


def _preserve_casing_enabled() -> bool:
    """Whether the reorganize leaves a title/album alone when the metadata
    source differs from the user's file only by letter-case (#1078 QT3496:
    already-organized files were flagged for cosmetic re-casing). Default on.
    Isolated so tests can monkeypatch without a config manager."""
    try:
        from core.settings import config_manager
        return bool(config_manager.get("library.reorganize_preserve_casing", True))
    except Exception:
        return True


def _keep_user_year(api_release_date, user_year):
    """Prefer the user's own album year over the source's original-release
    year when preserving is on (#1080 QT3496: a file imported as [2023] — a
    reissue/edition year the user chose — was being 'corrected' to the
    source's 2020 original). Returns a release_date string the path builder
    reads for $year; falls back to the source value."""
    if not _preserve_casing_enabled():
        return api_release_date
    uy = str(user_year or "").strip()
    if len(uy) == 4 and uy.isdigit():
        src_year = str(api_release_date or "")[:4]
        if uy != src_year:
            return uy
    return api_release_date


def _keep_user_casing(source_value, user_value):
    """Return the USER's string when it matches the source only by case, else
    the source string. Case-only means identical after casefold — so genuine
    edits (punctuation, words, feat additions) still adopt the source; only
    cosmetic capitalization churn is suppressed."""
    if not _preserve_casing_enabled():
        return source_value
    s = str(source_value or "")
    u = str(user_value or "")
    if u and s and s != u and s.strip().casefold() == u.strip().casefold():
        return u
    return source_value


def _extract_feat_credit(title: str) -> str:
    """The '(feat. X)' credit substring from a title (leading space trimmed),
    or '' when there's none. Lets us carry a user's own credit forward when
    the API only knows the primary artist."""
    if not title:
        return ''
    m = _FEAT_RE.search(str(title))
    return m.group(0).strip() if m else ''


def _apply_feat_credit(track_name: str, normalized_artists: list, local_title: str) -> str:
    """#1078: when feat_in_title is on, make sure the clean title the
    reorganize builds carries the featured-artist credit — so the FILENAME
    keeps it too (the tag writer re-adds it for the tag, but the filename is
    built straight from this clean title and was dropping "(feat. X)").

    Precedence when the API's own track name has no credit:
      1. featured artists from the API track's artist list (canonical names),
      2. else the credit already present in the user's file title (the API
         only knows the primary — don't strip what the user curated).
    A track name that already carries a credit is left untouched."""
    name = str(track_name or '')
    if _FEAT_DETECT_RE.search(name):
        return name
    featured = [
        (a.get('name') if isinstance(a, dict) else str(a))
        for a in (normalized_artists[1:] if normalized_artists else [])
    ]
    featured = [f for f in featured if f]
    if featured:
        return f"{name} (feat. {', '.join(featured)})".strip()
    credit = _extract_feat_credit(local_title)
    if credit:
        return f"{name} {credit}".strip()
    return name


def load_album_and_tracks(db, album_id):
    """Load the album row + all its track rows from the local DB.

    Returns ``(album_dict | None, tracks_list)``. ``album_dict`` is None
    when the album doesn't exist; tracks_list is empty when the album
    has no tracks. The caller decides what status to surface for each
    state.
    """
    conn = None
    try:
        conn = db._get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT al.*, al.primary_artist_id AS artist_id,
                   al.spotify_id AS spotify_album_id,
                   al.musicbrainz_id AS musicbrainz_release_id,
                   json_extract(al.external_ids, '$.itunes') AS itunes_album_id,
                   json_extract(al.external_ids, '$.deezer') AS deezer_id,
                   json_extract(al.external_ids, '$.discogs') AS discogs_id,
                   ar.name AS artist_name,
                   ar.spotify_id AS artist_spotify_id,
                   json_extract(ar.external_ids, '$.itunes') AS artist_itunes_id,
                   json_extract(ar.external_ids, '$.deezer') AS artist_deezer_id,
                   json_extract(ar.external_ids, '$.discogs') AS artist_discogs_id,
                   ar.musicbrainz_id AS artist_musicbrainz_id
            FROM lib2_albums al
            JOIN lib2_artists ar ON al.primary_artist_id = ar.id
            WHERE al.id = ?
            """,
            (str(album_id),),
        )
        album_row = cursor.fetchone()
        if not album_row:
            return None, []
        # Older and partial schemas may lack some enrichment columns. Read the
        # artist side by position because album and artist rows share names
        # like id and deezer_id; a combined sqlite3.Row resolves those names
        # to the album value.
        columns = [column[0] for column in cursor.description]
        if '_artist_columns_start' in columns:
            artist_start = columns.index('_artist_columns_start')
            album_data = {
                name: album_row[i] for i, name in enumerate(columns)
                if i < artist_start
            }
            artist_columns = {
                name: album_row[i] for i, name in enumerate(columns)
                if i > artist_start
            }
            for source in ('spotify', 'itunes', 'deezer', 'discogs', 'musicbrainz'):
                album_data[f'artist_{source}_id'] = artist_columns.get(id_column(source, 'artist'))
        else:
            album_data = dict(album_row)  # Legacy dict-backed database adapters.

        # the files of the library being reorganized (#1199): the same album
        # can have a copy in two of them, each in its own folder
        from core.library2.sql_util import owner_clause
        owner = owner_clause(column="f.owner_profile_id")
        cursor.execute(
            f"""
            SELECT t.*, ar.name AS artist_name,
                   (SELECT f.path FROM lib2_track_files f
                     WHERE f.track_id=t.id AND f.file_state='active'{owner}
                     ORDER BY f.is_primary DESC, f.id LIMIT 1) AS file_path
            FROM lib2_tracks t
            JOIN lib2_albums al ON t.album_id = al.id
            JOIN lib2_artists ar ON al.primary_artist_id = ar.id
            WHERE t.album_id = ?
              AND EXISTS (SELECT 1 FROM lib2_track_files f
                           WHERE f.track_id=t.id AND f.file_state='active'{owner})
            ORDER BY COALESCE(t.disc_number, 1), t.track_number, t.id
            """,
            (str(album_id),),
        )
        tracks = [dict(r) for r in cursor.fetchall()]
        return album_data, tracks
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: S110 — finally-block cleanup, logger may be torn down
                pass


def is_multi_artist_compilation(
    album_artist: Optional[str],
    tracks: Optional[List[dict]] = None,
    api_tracks: Optional[List[dict]] = None,
    threshold: int = 3,
) -> bool:
    """Detect if an album release is a compilation based on artist signals.

    A release is identified as a compilation if:
    1. The album artist name is explicitly 'Various Artists', 'Various', 'VA', 'V.A.', etc.
    2. Or there are multiple tracks (>= threshold, default 3) with distinct primary
       artists, where neither any single track artist nor the album artist accounts
       for more than 50% of the total tracks.
    """
    clean_aa = str(album_artist or '').strip().lower()
    if clean_aa in ('various artists', 'various', 'va', 'v.a.', 'soundtrack'):
        return True

    track_artists: List[str] = []
    if api_tracks:
        for t in api_tracks:
            a_list = t.get('artists')
            if isinstance(a_list, list) and a_list:
                first = a_list[0]
                name = first.get('name') if isinstance(first, dict) else str(first)
                if name and str(name).strip():
                    track_artists.append(str(name).strip().lower())
            elif t.get('artist') or t.get('artist_name'):
                name = t.get('artist') or t.get('artist_name')
                if name and str(name).strip():
                    track_artists.append(str(name).strip().lower())
    elif tracks:
        for t in tracks:
            name = t.get('track_artist') or t.get('artist_name') or t.get('artist')
            if name and str(name).strip():
                track_artists.append(str(name).strip().lower())

    if len(track_artists) < threshold:
        return False

    unique_artists = set(track_artists)
    if len(unique_artists) < threshold:
        return False

    total = len(track_artists)
    from collections import Counter
    counts = Counter(track_artists)
    max_count = max(counts.values())
    if (max_count / total) > 0.50:
        return False

    if clean_aa and (counts.get(clean_aa, 0) / total) > 0.50:
        return False

    return True


def _build_post_process_context(
    api_album: dict,
    api_track: dict,
    artist_name: str,
    album_title: str,
    total_discs: int,
    local_title: Optional[str] = None,
    local_year: Optional[str] = None,
    record_type: Optional[str] = None,
    type_source: Optional[str] = None,
    album_artist: Optional[str] = None,
    profile_id: Optional[int] = None,
    local_explicit: Any = None,
    local_album_explicit: Any = None,
    local_artist_mbid: Optional[str] = None,
) -> dict:
    """Build the same shape `import_album_process` builds so post-process
    treats this exactly like a fresh download with full Spotify-style
    metadata in hand.

    ``local_title`` is the user's own current track title — used only to
    carry a featured-artist credit forward when feat_in_title is on and the
    API doesn't supply one (#1078).

    ``profile_id`` (#1504): the owning profile for own-library files. Stamped
    into the context so ``transfer_root_for_context()`` routes the destination
    to the profile's own library instead of the shared folder. None = shared
    (unchanged behavior)."""
    track_number = int(api_track.get('track_number') or 1)
    disc_number = int(api_track.get('disc_number') or 1)
    track_artists = api_track.get('artists')
    if not track_artists:
        if api_track.get('artist'):
            track_artists = [api_track['artist']]
        elif api_track.get('artist_name'):
            track_artists = [api_track['artist_name']]
        else:
            track_artists = [artist_name]
    normalized_artists = [
        ({'name': a} if isinstance(a, str) else a) for a in track_artists
    ]
    # album artist comes from the source, like every other tag reorganize
    # writes. it used to be the name soulsync already had, which on navidrome
    # IS the file's old album artist tag, so a wrong variation got written
    # straight back every run (LettuceSnob). the library name is only the
    # fallback, and a case-only difference keeps the user's casing.
    library_album_artist = album_artist or artist_name
    source_album_artist = _source_album_artist(api_album)
    album_artist_name = (
        _keep_user_casing(source_album_artist, library_album_artist)
        if source_album_artist else library_album_artist
    )
    primary_track_artist = ''
    if normalized_artists:
        first_a = normalized_artists[0]
        if isinstance(first_a, dict) and first_a.get('name'):
            primary_track_artist = first_a['name']

    eff_type = (
        record_type
        or api_album.get('record_type')
        or api_album.get('album_type')
        or ''
    ).strip().lower()
    if eff_type in ('compile', 'compilations'):
        eff_type = 'compilation'
    is_comp = (eff_type == 'compilation') or bool(api_album.get('is_compilation'))

    api_album_id = api_album.get('id') or api_album.get('album_id') or ''
    api_album_name = api_album.get('name') or api_album.get('title') or album_title
    # #1078: keep the user's album-folder casing when the source differs only
    # by case (album_title is the user's own library album name).
    api_album_name = _keep_user_casing(api_album_name, album_title)
    api_album_release = (
        api_album.get('release_date')
        or api_album.get('releaseDate')
        or ''
    )
    # #1080: keep the user's own album year ($year) — a reissue/edition year
    # they imported with, not the source's original-release year.
    api_album_release = _keep_user_year(api_album_release, local_year)
    api_album_total_tracks = (
        api_album.get('total_tracks')
        or api_album.get('totalTracks')
        or 0
    )
    # Spotify shape: {'images': [{'url': ...}, ...]}.
    # Deezer shape: {'image_url': '...'}.
    api_album_image = api_album.get('image_url') or ''
    if not api_album_image:
        images = api_album.get('images')
        if isinstance(images, list) and images:
            first = images[0]
            if isinstance(first, dict):
                api_album_image = first.get('url') or ''

    track_name = api_track.get('name') or api_track.get('title') or ''
    # #1078: keep the featured-artist credit on the CLEAN title when the user
    # asked for feat-in-title. The tag writer re-adds it to the tag, but the
    # filename is built straight from this clean title and was silently
    # dropping "(feat. X)" — flagging already-correct files for "correction".
    if _feat_in_title_enabled():
        track_name = _apply_feat_credit(track_name, normalized_artists, local_title or '')
    # #1078: keep the user's own title casing when the source title differs
    # ONLY by case — no cosmetic rename/re-tag on already-organized files.
    # Runs AFTER feat so "Song (feat. X)" vs a bare source title stays a real
    # change; this only collapses pure capitalization differences. Both the
    # filename and the title tag are built from this string, so they agree.
    track_name = _keep_user_casing(track_name, local_title or '')

    effective_artist_name = primary_track_artist if (is_comp and primary_track_artist) else album_artist_name

    # which source the release type came from, for $albumtype only (a source
    # with its own EP label is taken at its word). tag mode has none: its
    # record_type is the library's, so it keeps the track-count split and a
    # reorganize files a release where the download did
    _type_source = type_source if type_source and type_source != 'tags' else ''
    # the source's own album lookup is reliable for a source that labels EPs
    # itself (deezer's catch-all 'album' only comes from its TRACK lookups).
    # when the release type agrees with it, lock it, so a reorganize files a
    # short album where the artist page shows it: Flow State Sampler, a
    # three-track deezer album, would otherwise go back to Single/ by count.
    # spotify and tag mode keep the count split, same as their downloads
    from core.imports.paths import _source_labels_eps
    _api_type = str(api_album.get('record_type') or api_album.get('album_type') or '').strip().lower()
    _type_locked = bool(
        _type_source and _source_labels_eps(_type_source)
        and _api_type and _api_type == (eff_type or 'album')
    )
    return {
        '_album_type_source': _type_source,
        'spotify_artist': {
            'name': effective_artist_name,
            'id': '',
            'genres': [],
            # #1859: the library artist, so $albumartistinfo renders what the
            # import rendered for it
            'musicbrainz_id': local_artist_mbid or '',
        },
        'spotify_album': {
            'id': api_album_id,
            'name': api_album_name,
            'release_date': api_album_release,
            'total_tracks': api_album_total_tracks,
            'total_discs': total_discs,
            # Reorganize is the caller that really knows: it counted the discs
            # off the source tracklist it just resolved, so the path builder must
            # not go and ask a provider again (that lookup's success or failure
            # would decide the destination).
            'total_discs_declared': True,
            'image_url': api_album_image,
            # #1299: keeps a same-named release in its own folder
            'disambiguation': str(api_album.get('disambiguation') or '').strip(),
            'album_type': eff_type or 'album',
            'record_type': eff_type or 'album',
            'album_type_locked': _type_locked,
            # $atypes labels a folder with every qualifier the release carries,
            # and Live/Soundtrack/Remix exist only as secondary types. Without
            # them here a reorganize renders $atypes empty and RENAMES
            # "[2017][EP][Live] Audiotree Live" to "[2017] Audiotree Live",
            # stripping the labels the variable exists to preserve.
            'secondary_types': list(api_album.get('secondary_types') or []),
            'is_compilation': is_comp,
            'artists': [{'name': album_artist_name}],
            # #1860: $explicit in a folder. the source's word, else the
            # library's, so a tag-mode album keeps the badge its import gave it
            'explicit': (api_album.get('explicit') if api_album.get('explicit') is not None
                         else local_album_explicit),
        },
        'track_info': {
            'name': track_name,
            'id': api_track.get('id', ''),
            'track_number': track_number,
            'disc_number': disc_number,
            'duration_ms': api_track.get('duration_ms', 0),
            'artists': normalized_artists,
            'uri': api_track.get('uri', ''),
            '_explicit_artist_context': {'name': album_artist_name},
            'explicit': (api_track.get('explicit') if api_track.get('explicit') is not None
                         else local_explicit),
        },
        'original_search_result': {
            'title': track_name,
            'artist': effective_artist_name,
            'album': api_album_name,
            'track_number': track_number,
            'disc_number': disc_number,
            'spotify_clean_title': track_name,
            'spotify_clean_album': api_album_name,
            'artists': normalized_artists,
        },
        'album_type': eff_type or 'album',
        'record_type': eff_type or 'album',
        'is_compilation': is_comp,
        'is_album_download': True,
        'has_clean_spotify_data': True,
        'has_full_spotify_metadata': True,
        # A reorganize processes the user's OWN library files, not slskd
        # transfers — same as the Import page (#804). Skips the integrity
        # check's duration-agreement leg: the re-resolved API tracklist can
        # legitimately disagree with the user's copy (a different master /
        # long version), and that mismatch was QUARANTINING a copy of a file
        # that stayed happily in the library (TheHomeGuy: 'Through Glass'
        # 283s vs Discogs' 241s). Size + parse corruption legs still run.
        'is_local_import': True,
        # This pipeline run mutates an EXISTING library file. The reorganize
        # runner owns its one catalogue path update after the destination is
        # proven present; ordinary download registration would insert a second
        # lib2_track_files row for the same track and destination.
        '_library_reorganize': True,
        # ...and for the same reason, the AcoustID identity leg does not get to
        # quarantine this file. A reorganize stages a COPY of a track the user
        # ALREADY OWNS and runs it through the download post-process; when the
        # fingerprint disagreed, that leg moved the copy into ss_quarantine and
        # the whole run reported `failed`, so a rename that should have been a
        # no-op only worked on a second attempt with "Rename only" ticked. The
        # disagreement is routinely legitimate — a different master, a regional
        # release, or an artist credited in another script (Sawano Hiroyuki
        # fingerprints as 澤野弘之). Identity of files already in the library is
        # the AcoustID Scanner's job, and that one raises a finding instead of
        # moving anyone's audio. The size and parse-corruption legs still run:
        # skipping identity is not skipping safety.
        '_skip_quarantine_check': 'acoustid',
        # Reorganize destinations must come from the CURRENT template alone.
        # The #829 existing-folder reuse would resolve to the folder the album
        # already lives in — the very folder reorganize is trying to move it
        # out of — so preview computed "unchanged" for every already-together
        # album and both the Tools job and Reorganize All silently no-opped
        # after a template change.
        '_no_album_folder_reuse': True,
        # #1504: own-library routing. stamped at the operation entry point;
        # transfer_root_for_context() reads it to pick the profile's own
        # library root instead of the shared folder. None = shared.
        'profile_id': profile_id,
    }


def _is_in_deleted_quarantine(resolved_path, transfer_dir) -> bool:
    """True when ``resolved_path`` lives inside the duplicate-cleaner
    quarantine folder (``<transfer_dir>/.deleted/...``, or the legacy bare
    ``deleted`` spelling older installs still carry).

    The Duplicate Cleaner (``core/library/duplicate_cleaner.py``) moves
    de-duplicated files into the quarantine. If the user's
    media server scans the transfer folder (e.g. a ``/music`` root that
    contains both the library and the transfer dir), those quarantined
    files get real rows in SoulSync's DB — and Reorganize, being purely
    DB-driven, would otherwise dutifully move them back OUT of /deleted
    to the template location. This guard makes Reorganize skip them so
    the quarantine stays quarantined (#746).

    Anchored to the transfer dir specifically so a legitimately
    named artist/album like "Deleted" elsewhere in the library is NOT
    skipped. When ``transfer_dir`` is unavailable we fall back to an exact
    ``deleted`` path-SEGMENT match (mirrors the cleaner's own
    ``if 'deleted' in dirs`` skip) — never a substring, so "Undeleted"
    or "deleted scenes" stay safe.
    """
    if not resolved_path:
        return False

    def _norm(p):
        # normpath collapses redundant separators / '..'; normcase applies
        # the platform's case rule (lowercases on Windows, no-op on posix);
        # fold to '/' so the segment/prefix checks are separator-agnostic.
        return os.path.normcase(os.path.normpath(p)).replace('\\', '/')

    norm = _norm(resolved_path)
    if transfer_dir:
        for name in ('.deleted', 'deleted'):     # hidden spelling + legacy installs
            quarantine = _norm(os.path.join(transfer_dir, name))
            if norm == quarantine or norm.startswith(quarantine + '/'):
                return True
        return False
    return any(seg in ('.deleted', 'deleted') for seg in norm.split('/'))


def _display_relative_to_root(path, root):
    """``path`` shown relative to ``root`` when it lives inside it, else whole.

    A raw ``startswith`` was wrong twice over. It matched a SIBLING root
    (``/music/Transfer2`` starts with ``/music/Transfer``), and it compared two
    strings that different code paths had spelled differently — the proposed
    path came from the path builder rooted at the config value, the current
    path from the resolver (absolute, symlinks resolved). With a relative root
    configured the two never shared a prefix, so the preview trimmed one column
    and printed the raw stored value in the other, for the very same file.
    """
    if not path or not root:
        return path or ''
    p = os.path.normpath(str(path))
    r = os.path.normpath(str(root))
    if p == r:
        return ''
    if p.startswith(r + os.sep) or (os.altsep and p.startswith(r + os.altsep)):
        return p[len(r):].lstrip(os.sep).lstrip('/')
    return path


def _trim_to_transfer(db_path, resolved, transfer_dir):
    """Compose the user-facing 'current path' string — relative to the
    transfer dir if the file lives there, else the raw DB value."""
    if resolved and transfer_dir:
        trimmed = _display_relative_to_root(resolved, transfer_dir)
        if trimmed != resolved:
            return trimmed
    return db_path or 'No file'


def _build_album_info(context: dict) -> dict:
    """Build the ``album_info`` dict that ``_build_final_path_for_track``
    consumes to enter ALBUM MODE. Without this (passing None) the path
    builder falls through to SINGLE MODE and produces per-track folders
    named after each track title — the exact bug we're fixing.

    Mirrors the shape the download path produces at write time.
    """
    spotify_album = context.get('spotify_album', {}) or {}
    track_info = context.get('track_info', {}) or {}
    return {
        'is_album': True,
        'album_name': spotify_album.get('name') or 'Unknown Album',
        'clean_track_name': track_info.get('name') or 'Unknown Track',
        'track_number': track_info.get('track_number') or 1,
        'disc_number': track_info.get('disc_number') or 1,
        'album_image_url': spotify_album.get('image_url') or '',
        'spotify_album_id': spotify_album.get('id') or '',
        'album_type': spotify_album.get('album_type') or context.get('album_type') or '',
        'record_type': spotify_album.get('record_type') or context.get('record_type') or '',
        'is_compilation': spotify_album.get('is_compilation') or context.get('is_compilation') or False,
    }


def _persist_compilation_record_type(db, album_id: str) -> None:
    """Persist compilation record_type to the database for the given album."""
    if not db or not album_id:
        return
    try:
        if hasattr(db, 'update_album_fields'):
            db.update_album_fields(album_id, {'record_type': 'compilation'})
        elif hasattr(db, '_get_connection'):
            conn = db._get_connection()
            try:
                conn.execute("UPDATE lib2_albums SET album_type = 'compilation' WHERE id = ?", (int(album_id),))
                conn.commit()
            finally:
                conn.close()
    except Exception as e:
        logger.warning("[Reorganize] Failed to persist compilation record_type for album %s: %s", album_id, e)


def _rename_track_in_place(
    current_abs: str, new_abs: str, *, sibling_paths=(), moved_paths=None,
) -> Tuple[bool, Optional[str]]:
    """Move ONE file from ``current_abs`` to ``new_abs`` in place — no copy, no re-tag,
    no post-processing. Creates the destination folder, carries explicitly supplied
    sibling-format files along with the renamed stem, and
    falls back to a cross-device move when the rename crosses a filesystem boundary.

    Refuses to overwrite a DIFFERENT existing file at the destination (returns an error
    instead) — never silent data loss. Returns ``(ok, error_message)``.

    Native plans supply proven ``sibling_paths``; the default authorizes only
    the selected audio file. ``moved_paths`` records only moves
    that actually succeeded, for exact catalogue updates and rollback.
    """
    try:
        if current_abs and not os.path.exists(current_abs):
            return False, 'source file no longer on disk'
        same = _same_physical_file(current_abs, new_abs)
        if same:
            return True, None
        if os.path.lexists(new_abs) and not same:
            return False, 'destination already exists'
        os.makedirs(os.path.dirname(new_abs), exist_ok=True)
        # Siblings are collected BEFORE the rename (the source stem is gone
        # afterwards) but carried AFTER it succeeds (iss29-E02): moving them
        # first meant a failed canonical rename left the album split across two
        # directories, with no error path that could put it back.
        siblings = list(sibling_paths)
        try:
            os.rename(current_abs, new_abs)
        except OSError as e:
            if getattr(e, 'errno', None) == errno.EXDEV:
                shutil.move(current_abs, new_abs)  # crosses a filesystem boundary
            else:
                raise
        if moved_paths is not None:
            moved_paths[current_abs] = new_abs
        # Carry sibling-format audio to the same destination with the renamed stem,
        # so lossy-copy pairs don't get orphaned.
        for sibling_src in siblings:
            sibling_dst = _move_sibling_to_destination(sibling_src, new_abs)
            if sibling_dst and moved_paths is not None:
                moved_paths[sibling_src] = sibling_dst
        # And the track's own sidecars. A .lrc is part of the track — SoulSync
        # writes it itself — and the import path has carried it since
        # lilbob5769's report. Reorganize looked for sibling AUDIO only and
        # stranded the lyrics in the old folder. Same helper as the import, so
        # a reorganized album and a freshly downloaded one end up with the same
        # files beside each other.
        #
        # Best-effort by design: the audio is already at its destination here.
        # Failing the move over a sidecar would report a track that did not
        # move, and leave the catalogue pointing at a path nothing is at.
        try:
            from core.imports.file_ops import move_companion_sidecars
            move_companion_sidecars(current_abs, new_abs)
        except Exception as sidecar_err:  # noqa: BLE001
            logger.warning("[Reorganize/rename] sidecar move failed for %s: %s",
                           os.path.basename(new_abs), sidecar_err)
        return True, None
    except Exception as e:
        return False, str(e)


def reorganize_album_rename_only(
    *,
    album_id: str,
    db,
    transfer_dir: str,
    resolve_file_path_fn: Callable[[Optional[str]], Optional[str]],
    build_final_path_fn: Callable,
    update_track_path_fn: Optional[Callable[[object, str], None]] = None,
    cleanup_empty_dir_fn: Optional[Callable[[str], None]] = None,
    on_progress: Optional[Callable[[dict], None]] = None,
    primary_source: Optional[str] = None,
    strict_source: bool = False,
    metadata_source: str = 'api',
    stop_check: Optional[Callable[[], bool]] = None,
    preview_fn: Optional[Callable] = None,
    profile_id: Optional[int] = None,
) -> dict:
    """RENAME-ONLY reorganize (#875): move each track's file to the path the current
    naming scheme dictates, and nothing else — no copy-to-staging, no re-tag, no
    quality/AcoustID checks.

    It acts on EXACTLY what the catalogue preview computed
    (:func:`core.library2.reorganize_bridge.catalogue_preview_fn`, injectable via
    ``preview_fn`` for testability), so the apply can never disagree with what the user
    saw, and ONLY files whose path actually changes are touched — files marked
    ``unchanged`` are skipped, which is what keeps a rename from rewriting the whole
    album (the #875 complaint). Tags and audio are left byte-for-byte alone.

    Returns ``{status, source, total, moved, skipped, failed, errors}``.
    """
    if preview_fn is None:
        from core.library2.reorganize_bridge import catalogue_preview_fn as preview_fn
    summary = {
        'status': 'completed', 'source': None, 'total': 0,
        'moved': 0, 'skipped': 0, 'failed': 0, 'errors': [],
    }

    def _emit(**updates):
        if on_progress is None:
            return
        try:
            on_progress(updates)
        except Exception as e:
            logger.debug("[Reorganize/rename] progress emit failed: %s", e)

    preview = preview_fn(
        album_id=album_id, db=db, transfer_dir=transfer_dir,
        resolve_file_path_fn=resolve_file_path_fn,
        build_final_path_fn=build_final_path_fn,
        primary_source=primary_source, strict_source=strict_source,
        metadata_source=metadata_source,
        profile_id=profile_id,
    )
    summary['source'] = preview.get('source')
    if not preview.get('success'):
        summary['status'] = preview.get('status', 'error')
        return summary

    tracks = preview.get('tracks', [])
    summary['total'] = len(tracks)
    src_dirs_touched: Set[str] = set()

    for t in tracks:
        if stop_check and stop_check():
            break
        title = t.get('title', 'Unknown')
        _emit(current_track=title)

        # Skip anything that isn't a real, changing move. `unchanged` is the key one —
        # it's why a rename no longer rewrites files whose name didn't change.
        # `current_path_abs` is the other one: the preview leaves it empty when the
        # stored path resolves to nothing on disk (`file_exists` False). Passing that
        # to the mover fails on os.rename — but only AFTER os.makedirs has built the
        # destination tree, so an unresolvable track used to litter the library with
        # empty folders, which is exactly what the preview avoids with create_dirs=False.
        if (not t.get('matched') or t.get('unchanged')
                or t.get('collision') or not t.get('new_path_abs')
                or not t.get('current_path_abs')):
            summary['skipped'] += 1
            _emit(skipped=summary['skipped'])
            continue

        current_abs = t.get('current_path_abs')
        new_abs = t.get('new_path_abs')
        # #1504: cross-library move guard: a file never leaves its library.
        try:
            from core.imports.paths import library_containing
            if library_containing(current_abs) != library_containing(new_abs):
                logger.error(
                    "[Reorganize/rename] REFUSING cross-library move: %s -> %s",
                    current_abs, new_abs,
                )
                summary['failed'] += 1
                summary['errors'].append({
                    'track_id': t.get('track_id'), 'title': title,
                    'error': f'refusing cross-library move {current_abs} -> {new_abs}',
                })
                _emit(failed=summary['failed'], errors=list(summary['errors']))
                continue
        except Exception as guard_err:
            logger.warning("[Reorganize/rename] move-guard check failed: %s", guard_err)
        moved_paths = {}
        sibling_paths = [f['current_path_abs'] for f in t.get('sibling_files', [])]
        ok, err = _rename_track_in_place(
            current_abs, new_abs, sibling_paths=sibling_paths, moved_paths=moved_paths,
        )
        if not ok:
            summary['failed'] += 1
            summary['errors'].append({
                'track_id': t.get('track_id'), 'title': title,
                'error': err or 'rename failed',
            })
            _emit(failed=summary['failed'], errors=list(summary['errors']))
            continue

        # File is at its new home — update the DB directly (authoritative; no need to
        # round-trip through a server scan to learn what we just did).
        #
        # A rename MOVES the only copy, so a failed catalogue update is NOT
        # recoverable by "a scan will reconcile": the file sits at a path nothing
        # points at, the track reads as MISSING, and the wishlist re-downloads
        # something the user already owns. Reported against a fresh library — songs
        # downloaded, Reorganize run while the import still held the write lock.
        # Put the file back and fail the track loudly instead.
        if update_track_path_fn:
            try:
                if t.get('file_id') is not None:
                    update_track_path_fn(
                        t.get('track_id'), new_abs, file_id=t['file_id'],
                        source_db_path=t['source_db_path'], current_path_abs=current_abs,
                        sibling_files=t.get('sibling_files', []), moved_paths=moved_paths,
                    )
                else:
                    update_track_path_fn(t.get('track_id'), new_abs)
            except Exception as db_err:
                rollback_errors = []
                # Never rediscover siblings in the destination: a refused
                # sibling destination may hold another recording. Undo only
                # files this attempt demonstrably moved.
                for old_path, moved_path in reversed(list(moved_paths.items())):
                    restored, restore_err = _rename_track_in_place(
                        moved_path, old_path, sibling_paths=[],
                    )
                    if not restored:
                        rollback_errors.append(f'{moved_path}: {restore_err}')
                undone = not rollback_errors
                undo_err = '; '.join(rollback_errors)
                if undone:
                    detail = f'{db_err} (move undone)'
                    logger.warning(
                        "[Reorganize/rename] catalogue update failed for %s: %s "
                        "— moved %s back to %s",
                        t.get('track_id'), db_err, new_abs, current_abs,
                    )
                else:
                    detail = (f'{db_err} — and the file could not be moved back '
                              f'to {current_abs}: {undo_err}')
                    logger.error(
                        "[Reorganize/rename] catalogue update failed for %s (%s) AND "
                        "the rollback failed (%s): the file is at %s while the "
                        "catalogue still names %s",
                        t.get('track_id'), db_err, undo_err, new_abs, current_abs,
                    )
                summary['failed'] += 1
                summary['errors'].append({
                    'track_id': t.get('track_id'), 'title': title, 'error': detail,
                })
                _emit(failed=summary['failed'], errors=list(summary['errors']))
                continue
        if current_abs:
            src_dirs_touched.add(os.path.dirname(current_abs))
        summary['moved'] += 1
        _emit(moved=summary['moved'],
              processed=summary['moved'] + summary['skipped'] + summary['failed'])

    for src_dir in src_dirs_touched:
        if cleanup_empty_dir_fn:
            try:
                cleanup_empty_dir_fn(src_dir)
            except Exception as e:
                logger.debug("[Reorganize/rename] cleanup of %s failed: %s", src_dir, e)
        try:
            _prune_empty_source_dirs(src_dir)   # #985: library-safe prune (transfer-dir-independent)
        except Exception as e:
            logger.debug("[Reorganize/rename] source prune of %s failed: %s", src_dir, e)

    if summary.get('moved', 0) > 0 and preview.get('record_type') == 'compilation':
        _persist_compilation_record_type(db, album_id)

    if (metadata_source == 'api' and _run_placed_everything(summary, stop_check)
            and tracks and all(t.get('matched') and t.get('file_exists')
                               and not t.get('collision') for t in tracks)):
        mark_album_placed(db, album_id, preview.get('source'), preview.get('source_album_id'))
    return summary


def naming_fingerprint() -> str:
    """Short hash of every setting that decides where reorganize puts a file.

    stored with the placement (#1701) so a scan can tell "the user changed their
    naming" (re-check the album) from "soulsync's naming code changed" (leave the
    placed album alone)."""
    try:
        from core.settings import config_manager
        get = config_manager.get
    except Exception:
        def get(_key, default=None):
            return default
    keys = (
        'file_organization', 'soulseek.transfer_path', 'library.music_paths',
        'library.reorganize_preserve_casing',
        'metadata_enhancement.tags.feat_in_title',
    )
    values = {k: get(k, None) for k in keys}
    # a new file_organization toggle lands in everyone's stored section on
    # their next settings save. left in while off, that alone would change
    # the hash and unsettle every placed album, so off counts as absent.
    org = values.get('file_organization')
    if isinstance(org, dict) and not org.get('unicode_substitution'):
        values['file_organization'] = {k: v for k, v in org.items()
                                       if k != 'unicode_substitution'}
    blob = json.dumps(values, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode('utf-8')).hexdigest()[:16]


def _run_placed_everything(summary: dict, stop_check) -> bool:
    """True when an apply run finished without a failure or a stop, so the album
    sits where its plan says."""
    if summary.get('status') != 'completed' or summary.get('failed'):
        return False
    try:
        return not (stop_check and stop_check())
    except Exception:
        return False


def mark_album_placed(db, album_id, source, source_album_id, fingerprint=None) -> bool:
    """Remember native placement and close its completed move findings (#1701)."""
    conn = None
    try:
        conn = db._get_connection()
        from core.library2.reorganize_placement import record_placement
        placed = record_placement(conn, album_id, fingerprint or naming_fingerprint())
        conn.execute(
            "UPDATE repair_findings SET status = 'resolved', user_action = 'obsolete', "
            "resolved_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP "
            "WHERE job_id = 'library_reorganize' AND finding_type = 'path_mismatch' "
            "AND status = 'pending' AND entity_type = 'track' "
            "AND entity_id IN (SELECT 'lib2:' || id FROM lib2_tracks WHERE album_id = ?)",
            (str(album_id),),
        )
        conn.commit()
        return placed
    except Exception as e:
        logger.warning("[Reorganize] couldn't close findings of album %s: %s", album_id, e)
    finally:
        if conn:
            conn.close()
    return False


# A disc subfolder inside an album: "Disc 1", "CD 2", "Disk 01", "Vol. 3", etc.
_DISC_DIR_RE = re.compile(r'^(disc|disk|cd|vol|volume)\s*\.?\s*\d+$', re.IGNORECASE)


def _rmdir_if_empty(dir_path: str) -> bool:
    """rmdir ``dir_path`` if it holds no non-hidden entries (clearing hidden junk like
    .DS_Store first) and isn't a protected/configured root. Returns True if removed."""
    try:
        from core.imports.file_ops import protected_root_dirs
        if os.path.normpath(dir_path) in protected_root_dirs():
            return False
    except Exception as e:
        logger.debug("[Reorganize] protected-root check failed for %s: %s", dir_path, e)
    try:
        entries = os.listdir(dir_path)
    except OSError:
        return False
    if any(not e.startswith('.') for e in entries):
        return False  # real content remains
    try:
        for hidden in entries:
            try:
                os.remove(os.path.join(dir_path, hidden))
            except OSError:
                pass
        os.rmdir(dir_path)
        logger.info(f"[Reorganize] Pruned empty source dir: {dir_path}")
        return True
    except OSError:
        return False


def _prune_empty_source_dirs(start_dir: str) -> None:
    """Prune the emptied SOURCE folders after a reorganize move.

    #985: the injected empty-dir cleanup is bounded by the transfer/download folder,
    but a Library Reorganize moves files that live in the MEDIA library — usually a
    different path — so that pruner's ``startswith(transfer_dir)`` guard never matches
    and the emptied source folders (``Album/Disc 1`` and the old ``Album`` dir) are
    left behind.

    Removes the moved file's own dir, AND the album dir directly above it IF that dir
    was a disc subfolder (``Disc N``/``CD N``). It deliberately NEVER climbs to the
    artist dir or the library root — bounded to the album level — so it can only ever
    delete the album/disc folders the reorganize actually emptied, never a root
    (which isn't in the protected set when the library lives outside the transfer
    folder — exactly this bug's setup).
    """
    d = os.path.normpath(start_dir)
    was_disc = bool(_DISC_DIR_RE.match(os.path.basename(d)))
    if not _rmdir_if_empty(d):
        return
    if was_disc:                          # the parent is the album dir — prune if now empty
        _rmdir_if_empty(os.path.dirname(d))


# Sidecar / cleanup helpers --------------------------------------------------


# Audio extensions used to decide whether a source directory still has
# tracks the user might care about (i.e. a per-track failure left audio
# behind that we shouldn't strip the cover art from).
_AUDIO_EXTS = frozenset(
    {'.flac', '.mp3', '.m4a', '.ogg', '.opus', '.wav', '.aac', '.wma', '.mp4'}
)


def _move_sibling_to_destination(sibling_src: str, canonical_dst: str) -> Optional[str]:
    """Move a sibling-format audio file to the same destination
    directory as the canonical, preserving its extension.

    Example: canonical at ``/library/Artist/Album/01 Track.opus`` +
    sibling source ``/old/01 Track.flac`` → destination ``/library/
    Artist/Album/01 Track.flac``. The destination filename uses the
    canonical's stem (post-template-rename) + the sibling's original
    extension — so a renamed canonical gets matching siblings.

    Returns the destination path on success, None on failure (logged
    at warning, doesn't raise — sibling moves are best-effort).

    iss29-E02: refuses to overwrite a DIFFERENT file already at the
    destination, exactly like ``_rename_track_in_place``. ``shutil.move``
    resolves to ``os.rename`` for a regular file on POSIX and overwrites
    silently — no error, no log, no counter. With lossy-copy enabled and a
    destination already holding a file under the canonical's post-rename stem
    (an earlier partial run, a second edition), that destroyed a file nothing
    in the catalogue was tracking.
    """
    dst_dir = os.path.dirname(canonical_dst)
    canonical_stem = os.path.splitext(os.path.basename(canonical_dst))[0]
    _, sibling_ext = os.path.splitext(sibling_src)
    sibling_dst = os.path.join(dst_dir, canonical_stem + sibling_ext)
    if _same_physical_file(sibling_src, sibling_dst):
        return sibling_dst  # already at the right place
    if os.path.lexists(sibling_dst):
        logger.warning(
            "[Reorganize] Not moving sibling-format file %s → %s: destination "
            "already exists; leaving the source in place",
            sibling_src, sibling_dst,
        )
        return None
    try:
        os.makedirs(dst_dir, exist_ok=True)
        shutil.move(sibling_src, sibling_dst)
        return sibling_dst
    except OSError as e:
        logger.warning(
            "[Reorganize] Couldn't move sibling-format file %s → %s: %s",
            sibling_src, sibling_dst, e,
        )
        return None
