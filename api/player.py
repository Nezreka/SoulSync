"""the /player app's own endpoints: where each profile is in what it plays.

every route reads and writes the caller's own rows only. the profile comes
from the session (get_current_profile_id), never from the request, so there
is no way to name someone else's.
"""

from __future__ import annotations

import threading
import time

from flask import Blueprint, jsonify, request

from core.permissions import requires, requires_any, self_only
from core.player import catalog, progress, watched
from core.profile_context import get_current_profile_id
from utils.logging_config import get_logger

logger = get_logger("player")

bp = Blueprint("player", __name__)


def create_blueprint():
    return bp


def get_database():
    """the music library (the guide's music channels read it)"""
    from database.music_database import get_database as _get
    return _get()


def player_db():
    """player.db: progress, favorite channels, home layouts"""
    from database.player_database import get_player_database
    return get_player_database()


def _profile():
    pid = get_current_profile_id()
    return int(pid) if pid is not None else None


def _signed_out():
    return jsonify({"success": False, "error": "Not signed in"}), 401


def _bad(message):
    return jsonify({"success": False, "error": str(message)}), 400


def _failed(what, message="Something went wrong saving your place"):
    logger.exception("player %s failed", what)
    return jsonify({"success": False, "error": message}), 500


@bp.route("/api/player/progress", methods=["GET"])
@self_only
def get_progress():
    pid = _profile()
    if pid is None:
        return _signed_out()
    try:
        row = progress.get(player_db(), pid, request.args.get("item_type"),
                           request.args.get("item_id"))
    except progress.ProgressError as e:
        return _bad(e)
    except Exception:  # noqa: BLE001
        return _failed("read")
    return jsonify({"success": True, "progress": row})


@bp.route("/api/player/progress", methods=["PUT"])
@self_only
def put_progress():
    pid = _profile()
    if pid is None:
        return _signed_out()
    body = request.get_json(silent=True) or {}
    finished = body.get("finished")
    if finished is not None and not isinstance(finished, bool):
        return _bad("finished must be true or false")
    try:
        row = progress.save(player_db(), pid, body.get("item_type"), body.get("item_id"),
                            body.get("position_seconds"), body.get("duration_seconds"),
                            finished=finished, meta=body.get("meta"))
    except progress.ProgressError as e:
        return _bad(e)
    except Exception:  # noqa: BLE001
        return _failed("save")
    return jsonify({"success": True, "progress": row})


@bp.route("/api/player/progress/lookup", methods=["POST"])
@self_only
def lookup_progress():
    """many items of one type in one call: {item_type, item_ids: [...]}."""
    pid = _profile()
    if pid is None:
        return _signed_out()
    body = request.get_json(silent=True) or {}
    try:
        rows = progress.lookup(player_db(), pid, body.get("item_type"), body.get("item_ids"))
    except progress.ProgressError as e:
        return _bad(e)
    except Exception:  # noqa: BLE001
        return _failed("lookup")
    return jsonify({"success": True, "progress": rows})


@bp.route("/api/player/progress/finish", methods=["POST"])
@self_only
def finish_progress():
    pid = _profile()
    if pid is None:
        return _signed_out()
    body = request.get_json(silent=True) or {}
    try:
        row = progress.finish(player_db(), pid, body.get("item_type"), body.get("item_id"),
                              body.get("duration_seconds"), meta=body.get("meta"))
    except progress.ProgressError as e:
        return _bad(e)
    except Exception:  # noqa: BLE001
        return _failed("finish")
    return jsonify({"success": True, "progress": row,
                    "watched": watched.mark(body.get("item_type"), body.get("item_id"))})


@bp.route("/api/player/watched", methods=["POST"])
@self_only
def mark_watched():
    """the player is into the credits of a movie or episode: it counts as
    watched (in soulsync and on the media server account)"""
    pid = _profile()
    if pid is None:
        return _signed_out()
    body = request.get_json(silent=True) or {}
    return jsonify({"success": True, "watched": watched.mark(body.get("item_type"), body.get("item_id"))})


@bp.route("/api/player/progress", methods=["DELETE"])
@self_only
def delete_progress():
    pid = _profile()
    if pid is None:
        return _signed_out()
    try:
        removed = progress.remove(player_db(), pid, request.args.get("item_type"),
                                  request.args.get("item_id"))
    except progress.ProgressError as e:
        return _bad(e)
    except Exception:  # noqa: BLE001
        return _failed("remove")
    return jsonify({"success": True, "removed": removed})


@bp.route("/api/player/continue", methods=["GET"])
@self_only
def continue_rail():
    pid = _profile()
    if pid is None:
        return _signed_out()
    raw = request.args.get("item_type") or ""
    types = [t for t in raw.split(",") if t.strip()]
    try:
        items = progress.continue_list(player_db(), pid, types, request.args.get("limit"))
    except progress.ProgressError as e:
        return _bad(e)
    except Exception:  # noqa: BLE001
        return _failed("continue")
    return jsonify({"success": True, "items": items})


def get_video_db():
    from api.video import get_video_db as _get
    return _get()


def _video_view():
    """(sees movies, sees shows, sees youtube, rating cap) for this request,
    off the same checks the video blueprint's gate and kids filters use."""
    from api.video import access
    from api.video.kids import video_cap
    side = access.any_side()
    return (side and access.has_kind("movie"), side and access.has_kind("show"), side, video_cap())


RATING = 4  # content_rating's place in a movie or show row (catalog.MOVIE_FIELDS)
_CACHE_SECONDS = 120
_cache: dict = {}
_cache_lock = threading.Lock()


def shape_video_catalog(raw, sees_movies, sees_shows, sees_youtube, cap):
    """drop the kinds and ratings this profile can't have. pure, so the
    filtering is tested apart from the db."""
    from api.video.kids import kind_allows

    def ok(kind, rating):
        return cap is None or kind_allows(cap, kind, rating)

    return {
        "fields": {"movie": list(catalog.MOVIE_FIELDS), "show": list(catalog.SHOW_FIELDS)},
        "genres": {str(k): v for k, v in (raw.get("genres") or {}).items()},
        "movies": [m for m in raw.get("movies", []) if sees_movies and ok("movie", m[RATING])],
        "shows": [s for s in raw.get("shows", []) if sees_shows and ok("show", s[RATING])],
        "recent_episodes": [e for e in raw.get("recent_episodes", [])
                            if sees_shows and ok("show", e.get("content_rating"))],
        "youtube": list(raw.get("youtube", [])) if sees_youtube else [],
        "kinds": {"movies": bool(sees_movies), "shows": bool(sees_shows), "youtube": bool(sees_youtube)},
    }


def _read_catalog(sees_movies, sees_shows, sees_youtube):
    """the unfiltered read, shared for a couple of minutes: on a big library
    it is a few seconds of table scans, and every screen opening the player
    asks for it."""
    key = (sees_movies, sees_shows, sees_youtube)
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < _CACHE_SECONDS:
            return hit[1]
        conn = get_video_db()._get_connection()
        try:
            raw = catalog.read_all(conn, sees_movies, sees_shows, sees_youtube)
        finally:
            conn.close()
        _cache[key] = (time.monotonic(), raw)
        return raw


@bp.route("/api/player/video-catalog", methods=["GET"])
@requires_any("movies.access", "tv.access")
def video_catalog():
    """the owned movies, shows (with a sample of episodes) and youtube
    downloads, with runtimes, genres, networks and ratings: what the guide
    schedules and the home rails show. read only."""
    try:
        sees_movies, sees_shows, sees_youtube, cap = _video_view()
        raw = _read_catalog(sees_movies, sees_shows, sees_youtube)
        return jsonify({"success": True, **shape_video_catalog(raw, sees_movies, sees_shows, sees_youtube, cap)})
    except Exception:  # noqa: BLE001
        logger.exception("player video catalog failed")
        return jsonify({"success": False, "error": "Could not read the video library"}), 500


_collections_cache: dict = {}


@bp.route("/api/player/collections", methods=["GET"])
@requires_any("movies.access", "tv.access")
def collections():
    """the collection studio's collections, each with the members this
    profile can see (it's the catalog's own list, so kinds and the kids cap
    hold). a collection with nothing visible isn't listed. resolving is
    cached for half an hour: a list collection may fetch a remote list."""
    from core.player import collections as coll
    try:
        sees_movies, sees_shows, sees_youtube, cap = _video_view()
        now = time.monotonic()
        hit = _collections_cache.get("all")
        if hit and now - hit[0] < 1800:
            data = hit[1]
        else:
            data = coll.read(get_video_db())
            if not data.get("partial"):
                _collections_cache["all"] = (now, data)
        shaped = shape_video_catalog(_read_catalog(sees_movies, sees_shows, sees_youtube),
                                     sees_movies, sees_shows, sees_youtube, cap)
        visible = {"movie": {r[0] for r in shaped.get("movies") or []},
                   "show": {r[0] for r in shaped.get("shows") or []}}
        out = []
        for c in data["collections"]:
            members = [m for m in c["members"] if m in visible.get(c["media_type"], set())]
            if members:
                out.append(dict(c, members=members))
        return jsonify({"success": True, "collections": out})
    except Exception:  # noqa: BLE001
        logger.exception("player collections failed")
        return jsonify({"success": False, "error": "Could not read collections"}), 500


def _youtube_gate():
    """the video side's own rule for youtube: any video access at all."""
    from api.video import access
    if not access.any_side():
        return jsonify({"success": False, "error": "Video isn't part of your profile"}), 403
    return None


@bp.route("/api/player/youtube", methods=["GET"])
@requires_any("movies.access", "tv.access")
def youtube_home():
    """followed channels, newest uploads from them, newest downloads. read only:
    nothing here calls youtube."""
    from core.player import youtube
    refused = _youtube_gate()
    if refused:
        return refused
    pid = _profile()
    if pid is None:
        return _signed_out()
    try:
        conn = get_video_db()._get_connection()
        try:
            return jsonify({"success": True, **youtube.home(conn, pid)})
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        logger.exception("player youtube home failed")
        return jsonify({"success": False, "error": "Could not read YouTube"}), 500


@bp.route("/api/player/youtube/channel/<channel_id>", methods=["GET"])
@requires_any("movies.access", "tv.access")
def youtube_channel(channel_id):
    from core.player import youtube
    refused = _youtube_gate()
    if refused:
        return refused
    pid = _profile()
    if pid is None:
        return _signed_out()
    if not channel_id or len(channel_id) > 64:
        return _bad("unknown channel")
    try:
        conn = get_video_db()._get_connection()
        try:
            data = youtube.channel(conn, channel_id, pid)
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        logger.exception("player youtube channel failed")
        return jsonify({"success": False, "error": "Could not read this channel"}), 500
    if data is None:
        return jsonify({"success": False, "error": "Channel not found"}), 404
    return jsonify({"success": True, **data})


_music_cache: dict = {}
# a library's genres move slowly; a cold read is a pass over every owned track
# (seconds on a 300k-track library), so a profile pays it a few times a day
_MUSIC_CACHE_SECONDS = 6 * 3600


@bp.route("/api/player/music-channels", methods=["GET"])
@requires("music.library")
def music_channels():
    """the guide's genre and vibe channels from the caller's owned tracks.
    cached hours per profile and scope: it is one pass over the tracks."""
    from contextlib import closing

    from core.player import music_channels as mc
    pid = _profile()
    if pid is None:
        return _signed_out()
    key = (pid, str(_session_scope()))
    now = time.monotonic()
    hit = _music_cache.get(key)
    if hit and now - hit[0] < _MUSIC_CACHE_SECONDS:
        return jsonify({"success": True, "channels": hit[1]})
    try:
        with closing(get_database()._get_connection()) as conn:
            channels = mc.read(conn)
    except Exception:  # noqa: BLE001
        logger.exception("player music channels failed")
        return jsonify({"success": False, "error": "Could not read the music library"}), 500
    _music_cache[key] = (time.monotonic(), channels)
    return jsonify({"success": True, "channels": channels})


def _session_scope():
    """the library scope the caller picked (#1199), part of the cache key"""
    try:
        from flask import session
        return session.get("library_scope")
    except Exception:  # noqa: BLE001
        return None


@bp.route("/api/player/guide/favorites", methods=["GET"])
@self_only
def get_favorites():
    from core.player import favorites
    pid = _profile()
    if pid is None:
        return _signed_out()
    try:
        return jsonify({"success": True, "channels": favorites.get(player_db(), pid)})
    except Exception:  # noqa: BLE001
        return _failed("favorites read")


@bp.route("/api/player/guide/favorites", methods=["PUT"])
@self_only
def put_favorites():
    from core.player import favorites
    pid = _profile()
    if pid is None:
        return _signed_out()
    body = request.get_json(silent=True) or {}
    try:
        return jsonify({"success": True,
                        "channels": favorites.put(player_db(), pid, body.get("channels"))})
    except favorites.FavoritesError as e:
        return _bad(e)
    except Exception:  # noqa: BLE001
        return _failed("favorites save")


@bp.route("/api/player/watchlist", methods=["GET"])
@self_only
def get_watchlist():
    """this profile's watchlist, newest first: [{item_type, item_id, meta, added_at}]"""
    from core.player import watchlist
    pid = _profile()
    if pid is None:
        return _signed_out()
    try:
        return jsonify({"success": True, "items": watchlist.list_items(player_db(), pid)})
    except Exception:  # noqa: BLE001
        return _failed("watchlist read")


@bp.route("/api/player/watchlist", methods=["PUT"])
@self_only
def add_to_watchlist():
    """{item_type: movie|show|youtube, item_id, meta?}"""
    from core.player import watchlist
    pid = _profile()
    if pid is None:
        return _signed_out()
    body = request.get_json(silent=True) or {}
    try:
        item = watchlist.add(player_db(), pid, body.get("item_type"), body.get("item_id"), body.get("meta"))
        return jsonify({"success": True, "item": item})
    except watchlist.WatchlistError as e:
        return _bad(e)
    except Exception:  # noqa: BLE001
        return _failed("watchlist save")


@bp.route("/api/player/watchlist", methods=["DELETE"])
@self_only
def remove_from_watchlist():
    from core.player import watchlist
    pid = _profile()
    if pid is None:
        return _signed_out()
    try:
        removed = watchlist.remove(player_db(), pid, request.args.get("item_type"), request.args.get("item_id"))
        return jsonify({"success": True, "removed": removed})
    except watchlist.WatchlistError as e:
        return _bad(e)
    except Exception:  # noqa: BLE001
        return _failed("watchlist remove")


@bp.route("/api/player/home-layout", methods=["GET"])
@self_only
def get_home_layout():
    """this screen's home rows (?device=), else the profile's shared ones,
    else the default; scope says which (device, all, or null). always clean,
    and with the catalog of rows and styles (core/player/home_layout.py is
    the contract every player app follows)."""
    from core.player import home_layout
    pid = _profile()
    if pid is None:
        return _signed_out()
    try:
        device = home_layout.device(request.args.get("device"))
        return jsonify({"success": True, **home_layout.get(player_db(), pid, device),
                        "catalog": home_layout.catalog()})
    except home_layout.LayoutError as e:
        return _bad(e)
    except Exception:  # noqa: BLE001
        return _failed("home layout read")


@bp.route("/api/player/home-layout", methods=["PUT"])
@self_only
def put_home_layout():
    """{layout, device}: device is this screen's id, or '*' for every
    screen of the profile (which drops this screen's own)"""
    from core.player import home_layout
    pid = _profile()
    if pid is None:
        return _signed_out()
    body = request.get_json(silent=True) or {}
    try:
        device = home_layout.device(body.get("device"))
        db = player_db()
        saved = home_layout.put(db, pid, body.get("layout"), device)
        if device == home_layout.ALL and body.get("from_device"):
            home_layout.reset(db, pid, home_layout.device(body.get("from_device")))
        return jsonify({"success": True, "layout": saved,
                        "scope": "all" if device == home_layout.ALL else "device"})
    except home_layout.LayoutError as e:
        return _bad(e)
    except Exception:  # noqa: BLE001
        return _failed("home layout save")


@bp.route("/api/player/home-layout", methods=["DELETE"])
@self_only
def reset_home_layout():
    """?device=: this screen follows the shared layout again. no device:
    every screen of the profile goes back to the default."""
    from core.player import home_layout
    pid = _profile()
    if pid is None:
        return _signed_out()
    try:
        home_layout.reset(player_db(), pid, home_layout.device(request.args.get("device")))
        return jsonify({"success": True})
    except home_layout.LayoutError as e:
        return _bad(e)
    except Exception:  # noqa: BLE001
        return _failed("home layout reset")


KEYWORD_MAX = 80


@bp.route("/api/player/maker", methods=["GET"])
@requires_any("movies.access", "tv.access")
def maker_titles():
    """everything a studio (?kind=studio) or a network (?kind=network) made,
    by name: its tmdb card and a page of titles (?sort=popular|newest|rated
    &page=), owned ones stamped with their library id. a profile without
    movies gets no studio, one without tv no network; rating caps apply."""
    from api.video.kids import filter_tmdb_items
    from core.player import makers
    from core.video.enrichment.engine import get_video_enrichment_engine

    which = request.args.get("kind")
    name = (request.args.get("name") or "").strip()
    if which not in ("studio", "network") or not name or len(name) > 200:
        return _bad("a kind (studio or network) and a name are required")
    try:
        page = max(1, min(20, int(request.args.get("page") or 1)))
    except ValueError:
        page = 1
    sort = request.args.get("sort") or "popular"
    try:
        sees_movies, sees_shows, _youtube, cap = _video_view()
        kind = "movie" if which == "studio" else "show"
        if not (sees_movies if kind == "movie" else sees_shows):
            return jsonify({"success": False, "error": "That isn't part of your profile"}), 403
        eng = get_video_enrichment_engine()
        if kind == "movie":
            maker = makers.studio_id(eng, name)
            card = eng.company_detail(maker) if maker else None
        else:
            conn = get_video_db()._get_connection()
            try:
                ids = [r[0] for r in conn.execute(
                    "SELECT s.tmdb_id FROM shows s JOIN show_networks sn ON sn.show_id = s.id "
                    "JOIN networks n ON n.id = sn.network_id "
                    "WHERE n.name = ? AND s.tmdb_id IS NOT NULL LIMIT 3", (name,))]
            finally:
                conn.close()
            maker = makers.network_id(eng, name, ids)
            card = makers.network_card(eng, maker) if maker else None
        if not maker:
            return jsonify({"success": True, "found": False, "titles": []})
        items = makers.titles(eng, kind, maker, sort, page)
        if cap is not None:
            items = filter_tmdb_items(get_video_db(), items, cap)
        return jsonify({"success": True, "found": True, "card": card, "titles": items,
                        "page": page, "has_more": len(items) >= 15})
    except Exception:  # noqa: BLE001
        logger.exception("player maker failed for %s %s", which, name)
        return jsonify({"success": False, "error": "Could not look that up"}), 500


LOGO_MAX = 12


@bp.route("/api/player/logos", methods=["GET"])
@requires_any("movies.access", "tv.access")
def title_logos():
    """tmdb title logos (the wordmark art) for a few titles at once, for the
    home billboard: ?items=movie:603,show:1399 (up to 12). the engine keeps
    each a day, a miss included; a cold set is fetched side by side."""
    import concurrent.futures

    from core.video.enrichment.engine import get_video_enrichment_engine
    wanted = []
    for part in (request.args.get("items") or "").split(",")[:LOGO_MAX]:
        kind, _, ident = part.partition(":")
        if kind in ("movie", "show") and ident.isdigit():
            wanted.append((kind, int(ident)))
    if not wanted:
        return jsonify({"success": True, "logos": {}})
    try:
        eng = get_video_enrichment_engine()
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
            found = list(ex.map(lambda w: eng.title_logo(w[0], w[1]), wanted))
        return jsonify({"success": True,
                        "logos": {f"{k}:{i}": (url or None) for (k, i), url in zip(wanted, found, strict=True)}})
    except Exception:  # noqa: BLE001 - the billboard falls back to the text title
        logger.exception("player logos failed")
        return jsonify({"success": True, "logos": {}})


@bp.route("/api/player/keyword", methods=["GET"])
@requires_any("movies.access", "tv.access")
def keyword_titles():
    """a tmdb keyword's titles ('heist', 'based on novel or book'): the
    video side's own discover, by the keyword's id, owned ones stamped with
    their library id. kinds and rating caps as everywhere else."""
    name = (request.args.get("name") or "").strip()
    if not name or len(name) > KEYWORD_MAX:
        return _bad("a keyword is required")
    try:
        from api.video.kids import filter_tmdb_items
        from core.video.enrichment.engine import get_video_enrichment_engine
        sees_movies, sees_shows, _youtube, cap = _video_view()
        eng = get_video_enrichment_engine()
        keyword_id = eng.keyword_id(name)
        if not keyword_id:
            return jsonify({"success": True, "keyword": name, "found": False, "movies": [], "shows": []})
        out = {}
        for kind, sees in (("movie", sees_movies), ("show", sees_shows)):
            items = []
            if sees:
                for page in (1, 2):
                    items += eng.discover_filter(kind, keywords=str(keyword_id), page=page) or []
            seen, unique = set(), []
            for item in items:
                if item.get("tmdb_id") not in seen:
                    seen.add(item.get("tmdb_id"))
                    unique.append({**item, "kind": kind})
            out[kind] = filter_tmdb_items(get_video_db(), unique, cap) if cap is not None else unique
        return jsonify({"success": True, "keyword": name, "found": True,
                        "movies": out["movie"], "shows": out["show"]})
    except Exception:  # noqa: BLE001
        logger.exception("player keyword failed for %s", name)
        return jsonify({"success": False, "error": "Could not look that keyword up"}), 500


# ── playlists (music) ────────────────────────────────────────────────────

@bp.route("/api/player/playlists", methods=["GET"])
@requires("music.access")
def player_playlists():
    """this profile's music playlists: yours (Liked Songs first) and mirrored"""
    pid = _profile()
    if pid is None:
        return _signed_out()
    from core.player import playlists
    try:
        return jsonify({"success": True, **playlists.list_for(get_database(), pid)})
    except Exception:  # noqa: BLE001
        return _failed("playlists", "Couldn't load your playlists.")


@bp.route("/api/player/playlists/<int:playlist_id>", methods=["GET"])
@requires("music.access")
def player_playlist(playlist_id):
    """one of this profile's playlists with its songs, each with the library
    track it plays (track_id) or null when this profile can't play it"""
    pid = _profile()
    if pid is None:
        return _signed_out()
    from core.player import playlists
    from core.player.sources.music import _scope, hides_explicit
    try:
        out = playlists.detail(get_database(), pid, playlist_id, scope=_scope(),
                               hide_explicit=hides_explicit())
    except Exception:  # noqa: BLE001
        return _failed("playlist", "Couldn't load that playlist.")
    if out is None:
        return jsonify({"success": False, "error": "not_found", "message": "No such playlist."}), 404
    return jsonify({"success": True, "playlist": out})


# ── player settings (per profile) ────────────────────────────────────────

@bp.route("/api/player/settings", methods=["GET"])
@self_only
def get_player_settings():
    """this profile's player settings, each with what it does"""
    from core.player import settings
    pid = _profile()
    if pid is None:
        return _signed_out()
    try:
        return jsonify({"success": True, "settings": settings.get(player_db(), pid),
                        "about": {k: v[2] for k, v in settings.SETTINGS.items()}})
    except Exception:  # noqa: BLE001
        return _failed("settings read", "Couldn't load your settings.")


@bp.route("/api/player/settings", methods=["PUT"])
@self_only
def put_player_settings():
    """{key: value, ...}: all saved or none. turning sharing off takes you
    off everyone's watching-now at once"""
    from core.player import presence, settings
    pid = _profile()
    if pid is None:
        return _signed_out()
    try:
        out = settings.put(player_db(), pid, request.get_json(silent=True))
    except settings.SettingsError as e:
        return _bad(e)
    except Exception:  # noqa: BLE001
        return _failed("settings save", "Couldn't save that setting.")
    if not out["share_activity"]:
        presence.forget_profile(pid)
    return jsonify({"success": True, "settings": out})


# ── watching now (who plays what, live) ─────────────────────────────────

def _shares(profile_id: int) -> bool:
    from core.player import settings
    try:
        return bool(settings.get(player_db(), profile_id)["share_activity"])
    except Exception:  # noqa: BLE001 - can't tell: don't show it
        return False


def _viewer_may_see(s) -> bool:
    """the viewer may see this kind of media, and this film or show under
    its rating cap"""
    from core.permissions import map_refuses
    from core.player.presence import KIND_PERMISSIONS
    perms = KIND_PERMISSIONS.get(s.item_type, ())
    if not perms or all(map_refuses(p) for p in perms):
        return False
    if s.item_type not in ("movie", "episode"):
        return True
    try:
        from api.video import get_video_db
        from api.video.kids import library_title_allowed, video_cap
        cap = video_cap()
        if cap is None:
            return True
        if s.item_type == "movie":
            return library_title_allowed(get_video_db(), "movie", int(s.item_id), cap)
        show = (s.meta or {}).get("parent_id")
        return bool(show) and library_title_allowed(get_video_db(), "show", int(show), cap)
    except Exception:  # noqa: BLE001 - can't tell: don't show it
        return False


_who: dict = {"at": 0.0, "map": {}}


def _profiles_by_id() -> dict:
    """names and colours, kept a minute"""
    if time.time() - _who["at"] > 60:
        try:
            rows = get_database().get_all_profiles() or []
        except Exception:  # noqa: BLE001
            rows = []
        _who["map"] = {int(r["id"]): {"name": r.get("name") or "Someone",
                                      "avatar_color": r.get("avatar_color"),
                                      "avatar_url": r.get("avatar_url")} for r in rows if r.get("id")}
        _who["at"] = time.time()
    return _who["map"]


@bp.route("/api/player/presence", methods=["POST"])
@self_only
def presence_beat():
    """a screen plays something: {session, item_type, item_id, position,
    duration, playing, live, channel?, meta?}, every few seconds. a profile
    that doesn't share is never kept (shared: false)"""
    from core.player import presence
    pid = _profile()
    if pid is None:
        return _signed_out()
    if not _shares(pid):
        return jsonify({"success": True, "shared": False})
    try:
        presence.beat(pid, request.get_json(silent=True))
    except presence.PresenceError as e:
        return _bad(e)
    return jsonify({"success": True, "shared": True})


@bp.route("/api/player/presence", methods=["DELETE"])
@self_only
def presence_stop():
    from core.player import presence
    pid = _profile()
    if pid is None:
        return _signed_out()
    presence.stop(pid, request.args.get("session") or "")
    return jsonify({"success": True})


@bp.route("/api/player/presence", methods=["GET"])
@self_only
def presence_list():
    """what others on this server are watching or listening to right now:
    profiles that share, things this profile may see"""
    from core.player import presence
    pid = _profile()
    if pid is None:
        return _signed_out()
    rows = presence.visible_to(pid, presence.everyone(), shares=_shares, may_see=_viewer_may_see)
    who = _profiles_by_id()
    for r in rows:
        r["profile"] = who.get(r["profile_id"], {"name": "Someone"})
    return jsonify({"success": True, "sessions": rows, "sharing": _shares(pid)})


@bp.route("/api/player/presence/together", methods=["GET"])
@self_only
def presence_together():
    """titles you and others (that share) have both started lately: your
    place and theirs. a show is one title, whichever episode you're each on"""
    from core.player import presence
    pid = _profile()
    if pid is None:
        return _signed_out()
    try:
        rows = presence.together(player_db(), pid, shares=_shares, may_see=_viewer_may_see)
    except Exception:  # noqa: BLE001
        return _failed("together", "Couldn't work that out.")
    who = _profiles_by_id()
    for r in rows:
        for o in r["others"]:
            o["profile"] = who.get(o["profile_id"], {"name": "Someone"})
    return jsonify({"success": True, "titles": rows})
