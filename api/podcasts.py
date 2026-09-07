"""Podcasts API Blueprint — exposes endpoints for podcast discovery, RSS feed ingestion,
and background episode downloads.

Endpoints:
  - GET  /api/podcasts/search: Search podcasts by query string via iTunes Search API.
  - GET  /api/podcasts/featured: Return curated/trending podcasts across genres (cached).
  - GET  /api/podcasts/show: Fetch full RSS feed and episodes for a show.
  - POST /api/podcasts/download: Queue an episode download in the background.
  - GET  /api/podcasts/downloads: Retrieve status of all active and completed downloads.
  - GET  /api/podcasts/audio-proxy: Stream audio with range-request forwarding for CORS fallback.

Self-contained and purely additive. Does not modify any existing tables or routes.
"""

from __future__ import annotations

import hashlib
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests
from flask import Blueprint, Response, jsonify, request

from core.podcast_client import (
    PodcastEpisode,
    PodcastShow,
    get_podcast_client,
)
from core.podcast_download_client import PodcastDownloadClient
from utils.logging_config import get_logger

logger = get_logger("podcasts.api")

# ---------------------------------------------------------------------------
# Download Manager State
# ---------------------------------------------------------------------------

_download_lock = threading.Lock()
_downloads: Dict[str, Dict[str, Any]] = {}
_download_client: Optional[PodcastDownloadClient] = None

# Cache for featured, search results, and parsed show feeds
_featured_cache: Dict[str, Dict[str, Any]] = {}
_show_cache: Dict[str, Dict[str, Any]] = {}
_CACHE_TTL = 3600.0  # 1 hour in seconds
_SHOW_CACHE_TTL = 900.0  # 15 minutes in seconds


def _db():
    try:
        from database.music_database import get_database
        return get_database()
    except Exception:
        return None


def _get_download_client() -> PodcastDownloadClient:
    global _download_client
    if _download_client is None:
        _download_client = PodcastDownloadClient()
    return _download_client


def _make_download_id(enclosure_url: str, guid: str = "") -> str:
    seed = (guid or enclosure_url or str(time.time())).encode("utf-8")
    return hashlib.sha256(seed).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------

def episode_to_dict(ep: PodcastEpisode) -> Dict[str, Any]:
    return {
        "guid": ep.guid,
        "title": ep.title,
        "enclosure_url": ep.enclosure_url,
        "enclosure_type": ep.enclosure_type,
        "enclosure_length": ep.enclosure_length,
        "pub_date": ep.pub_date.isoformat() if ep.pub_date else None,
        "duration_seconds": ep.duration_seconds,
        "description": ep.description,
        "show_notes": ep.show_notes,
        "season": ep.season,
        "episode_number": ep.episode_number,
        "episode_type": ep.episode_type,
        "artwork_url": ep.artwork_url,
        "chapter_url": ep.chapter_url,
        "transcript_url": ep.transcript_url,
        "show_title": getattr(ep, "show_title", None),
        "author": getattr(ep, "author", None),
    }


def show_to_dict(show: PodcastShow, include_episodes: bool = True) -> Dict[str, Any]:
    d = {
        "title": show.title,
        "author": show.author,
        "description": show.description,
        "artwork_url": show.artwork_url,
        "feed_url": show.feed_url,
        "itunes_id": show.itunes_id,
        "website": show.website,
        "language": show.language,
        "explicit": show.explicit,
        "categories": show.categories,
        "episode_count": show.episode_count,
    }
    if include_episodes:
        d["episodes"] = [episode_to_dict(ep) for ep in show.episodes]
    else:
        d["episodes"] = []
    return d


# ---------------------------------------------------------------------------
# Blueprint Factory
# ---------------------------------------------------------------------------

def create_podcasts_blueprint() -> Blueprint:
    bp = Blueprint("podcasts_api", __name__, url_prefix="/api/podcasts")

    @bp.route("/search", methods=["GET"])
    def search():
        """Search podcasts via iTunes Search API."""
        query = request.args.get("q", "").strip()
        if not query:
            return jsonify({"success": True, "results": []})

        try:
            limit = min(50, max(1, int(request.args.get("limit", 20))))
        except (ValueError, TypeError):
            limit = 20

        client = get_podcast_client()
        shows = client.search_podcasts(query, limit=limit)
        results = [show_to_dict(s, include_episodes=False) for s in shows]
        return jsonify({"success": True, "query": query, "results": results})

    @bp.route("/featured", methods=["GET"])
    def featured():
        """Return curated top podcasts across genres with in-memory caching."""
        category = request.args.get("category", "Trending").strip()
        cache_key = category.lower()

        now = time.time()
        cached = _featured_cache.get(cache_key)
        if cached and (now - cached["timestamp"]) < _CACHE_TTL:
            return jsonify({"success": True, "category": category, "results": cached["results"]})

        # Map display category to search term
        term_map = {
            "trending": "top podcast",
            "technology": "technology",
            "news": "news politics",
            "true crime": "true crime",
            "comedy": "comedy",
            "science": "science",
            "business": "business finance",
            "culture": "culture history",
            "music": "music",
            "health": "health fitness",
            "mental health": "mental health psychology",
            "sports": "sports",
            "film": "film movies",
            "gaming": "video games gaming",
            "education": "education learning",
            "fiction": "audio drama fiction",
            "kids": "kids family",
            "philosophy": "philosophy wisdom",
            "arts": "arts design",
            "society": "society documentary",
            "automotive": "cars automotive",
        }
        term = term_map.get(cache_key, category)

        try:
            limit = min(50, max(1, int(request.args.get("limit", 40))))
        except (ValueError, TypeError):
            limit = 40

        client = get_podcast_client()
        shows = client.search_podcasts(term, limit=limit)
        results = [show_to_dict(s, include_episodes=False) for s in shows]

        _featured_cache[cache_key] = {"timestamp": now, "results": results}
        return jsonify({"success": True, "category": category, "results": results})

    @bp.route("/show", methods=["GET"])
    def show_detail():
        """Fetch full show metadata and episode list from RSS feed URL."""
        feed_url = request.args.get("url", "").strip()
        itunes_id_raw = request.args.get("itunes_id", "").strip()
        itunes_id: Optional[int] = None
        if itunes_id_raw.isdigit():
            itunes_id = int(itunes_id_raw)

        client = get_podcast_client()
        show_hint = None

        if itunes_id is not None:
            show_hint = client.lookup_by_itunes_id(itunes_id)
            if not feed_url and show_hint and show_hint.feed_url:
                feed_url = show_hint.feed_url

        if not feed_url:
            if show_hint:
                return jsonify({"success": True, "show": show_to_dict(show_hint, include_episodes=False)})
            return jsonify({"success": False, "error": "Feed URL or valid iTunes ID required"}), 400

        now = time.time()
        cached_show = _show_cache.get(feed_url)
        if cached_show and (now - cached_show["timestamp"]) < _SHOW_CACHE_TTL:
            return jsonify({"success": True, "show": cached_show["show"]})

        show = client.fetch_feed(feed_url, show_hint=show_hint)
        if show is None:
            return jsonify({"success": False, "error": f"Failed to fetch or parse feed at {feed_url}"}), 502

        show_data = show_to_dict(show, include_episodes=True)
        _show_cache[feed_url] = {"timestamp": now, "show": show_data}

        return jsonify({"success": True, "show": show_data})

    @bp.route("/download", methods=["POST"])
    def download_episode():
        """Trigger background download of a podcast episode."""
        data = request.get_json(silent=True) or {}
        enclosure_url = data.get("enclosure_url", "").strip()
        title = data.get("title", "Episode").strip()
        guid = data.get("guid", enclosure_url).strip()
        show_title = data.get("show_title", "Podcasts").strip()
        author = data.get("author", "").strip()
        pub_date_raw = data.get("pub_date")
        pub_date = None
        if pub_date_raw:
            try:
                pub_date = datetime.fromisoformat(str(pub_date_raw).replace("Z", "+00:00"))
            except Exception:
                pub_date = None
        artwork_url = data.get("artwork_url", "").strip()
        duration_seconds = data.get("duration_seconds")

        if not enclosure_url:
            return jsonify({"success": False, "error": "enclosure_url is required"}), 400

        download_id = _make_download_id(enclosure_url, guid)

        with _download_lock:
            existing = _downloads.get(download_id)
            if existing and existing.get("status") in ("downloading", "queued"):
                return jsonify({"success": True, "download_id": download_id, "status": existing["status"]})

            record = {
                "download_id": download_id,
                "title": title,
                "show_title": show_title,
                "author": author,
                "artwork_url": artwork_url,
                "enclosure_url": enclosure_url,
                "duration_seconds": duration_seconds,
                "status": "queued",
                "progress_bytes": 0,
                "total_bytes": 0,
                "percent": 0.0,
                "file_path": None,
                "error": None,
                "started_at": time.time(),
                "completed_at": None,
            }
            _downloads[download_id] = record

        # Run background worker thread
        def _worker():
            with _download_lock:
                if download_id in _downloads:
                    _downloads[download_id]["status"] = "downloading"

            def _progress(downloaded: int, total: int):
                with _download_lock:
                    rec = _downloads.get(download_id)
                    if rec:
                        rec["progress_bytes"] = downloaded
                        rec["total_bytes"] = total
                        if total > 0:
                            rec["percent"] = round((downloaded / total) * 100.0, 1)

            try:
                dl_client = _get_download_client()
                ep = PodcastEpisode(
                    guid=guid,
                    title=title,
                    enclosure_url=enclosure_url,
                    enclosure_type=data.get("enclosure_type", "audio/mpeg"),
                    enclosure_length=data.get("enclosure_length"),
                    pub_date=pub_date,
                    duration_seconds=duration_seconds,
                    description="",
                    show_notes="",
                    season=data.get("season"),
                    episode_number=data.get("episode_number"),
                    episode_type=data.get("episode_type", "full"),
                    artwork_url=artwork_url,
                    chapter_url=None,
                    transcript_url=None,
                    show_title=show_title,
                    author=author,
                )
                file_path = dl_client.download_episode(
                    ep,
                    progress_callback=_progress,
                    show_title=show_title,
                    author=author,
                )
                with _download_lock:
                    rec = _downloads.get(download_id)
                    if rec:
                        if file_path:
                            rec["status"] = "completed"
                            rec["file_path"] = file_path
                            rec["percent"] = 100.0
                            rec["completed_at"] = time.time()
                        else:
                            rec["status"] = "error"
                            rec["error"] = "Download failed or file was invalid"
            except Exception as exc:
                logger.error("Podcast download failed for %s: %s", title, exc)
                with _download_lock:
                    rec = _downloads.get(download_id)
                    if rec:
                        rec["status"] = "error"
                        rec["error"] = str(exc)

        thread = threading.Thread(target=_worker, daemon=True, name=f"podcast-dl-{download_id}")
        thread.start()

        return jsonify({"success": True, "download_id": download_id, "status": "queued"})

    @bp.route("/downloads", methods=["GET"])
    def get_downloads():
        """Return status of all tracked podcast downloads."""
        with _download_lock:
            # Sort newest started first
            items = sorted(_downloads.values(), key=lambda x: x.get("started_at", 0), reverse=True)
            return jsonify({"success": True, "downloads": items})

    @bp.route("/audio-proxy", methods=["GET"])
    def audio_proxy():
        """Streaming proxy with range-request forwarding for enclosures."""
        target_url = request.args.get("url", "").strip()
        if not target_url:
            return jsonify({"error": "Missing url parameter"}), 400

        headers = {}
        if "Range" in request.headers:
            headers["Range"] = request.headers["Range"]

        headers["User-Agent"] = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

        try:
            req = requests.get(target_url, headers=headers, stream=True, timeout=15)
            forward_headers = {}
            for h in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges"):
                if h in req.headers:
                    forward_headers[h] = req.headers[h]

            def _generate():
                for chunk in req.iter_content(chunk_size=32 * 1024):
                    if chunk:
                        yield chunk

            return Response(_generate(), status=req.status_code, headers=forward_headers)
        except Exception as exc:
            logger.warning("Audio proxy error for %s: %s", target_url, exc)
            return jsonify({"error": f"Failed to stream audio: {exc}"}), 502

    # -----------------------------------------------------------------------
    # Watchlist endpoints (parity with artists and labels)
    # -----------------------------------------------------------------------

    @bp.route("/watchlist", methods=["GET"])
    def podcasts_watchlist_list():
        """List all watchlisted podcasts."""
        db = _db()
        if db is None:
            return jsonify({"success": True, "podcasts": []})
        try:
            profile_id = int(request.args.get("profile_id", 1))
        except (ValueError, TypeError):
            profile_id = 1
        try:
            items = db.get_watchlist_podcasts(profile_id=profile_id)
            return jsonify({"success": True, "podcasts": items})
        except Exception as exc:
            logger.exception("podcasts_watchlist_list failed: %s", exc)
            return jsonify({"success": True, "podcasts": []})

    @bp.route("/watchlist/check", methods=["POST"])
    def podcasts_watchlist_check():
        """Check if a podcast is currently in the watchlist."""
        body = request.get_json(silent=True) or {}
        feed_url = str(body.get("feed_url") or "").strip()
        itunes_id = body.get("itunes_id")
        try:
            itunes_id = int(itunes_id) if itunes_id is not None else None
        except (ValueError, TypeError):
            itunes_id = None

        db = _db()
        if db is None or (not feed_url and itunes_id is None):
            return jsonify({"success": True, "is_watching": False, "podcast": None})

        try:
            pod = db.get_watchlist_podcast(feed_url=feed_url or None, itunes_id=itunes_id)
            is_watching = pod is not None
            return jsonify({"success": True, "is_watching": is_watching, "podcast": pod})
        except Exception as exc:
            logger.exception("podcasts_watchlist_check failed: %s", exc)
            return jsonify({"success": False, "is_watching": False, "podcast": None})

    @bp.route("/watchlist/add", methods=["POST"])
    def podcasts_watchlist_add():
        """Add or update a podcast in the watchlist."""
        body = request.get_json(silent=True) or {}
        feed_url = str(body.get("feed_url") or "").strip()
        title = str(body.get("title") or body.get("name") or "").strip()
        if not feed_url or not title:
            return jsonify({"success": False, "error": "feed_url and title are required"}), 400

        itunes_id = body.get("itunes_id")
        try:
            itunes_id = int(itunes_id) if itunes_id is not None else None
        except (ValueError, TypeError):
            itunes_id = None

        retention_days = body.get("retention_days", 14)
        try:
            retention_days = max(0, int(retention_days))
        except (ValueError, TypeError):
            retention_days = 14

        auto_download = bool(body.get("auto_download", True))
        author = str(body.get("author") or "").strip() or None
        description = str(body.get("description") or "").strip() or None
        artwork_url = str(body.get("artwork_url") or "").strip() or None
        website = str(body.get("website") or "").strip() or None

        episode_count = body.get("episode_count")
        try:
            episode_count = int(episode_count) if episode_count is not None else None
        except (ValueError, TypeError):
            episode_count = None

        profile_id = body.get("profile_id", 1)
        try:
            profile_id = int(profile_id)
        except (ValueError, TypeError):
            profile_id = 1

        db = _db()
        if db is None:
            return jsonify({"success": False, "error": "database unavailable"}), 500

        try:
            ok = db.add_watchlist_podcast(
                feed_url,
                title,
                itunes_id=itunes_id,
                author=author,
                description=description,
                artwork_url=artwork_url,
                website=website,
                auto_download=auto_download,
                retention_days=retention_days,
                episode_count=episode_count,
                profile_id=profile_id,
            )
            pod = db.get_watchlist_podcast(feed_url=feed_url)
            return jsonify({"success": bool(ok), "is_watching": True, "podcast": pod})
        except Exception as exc:
            logger.exception("podcasts_watchlist_add failed for %s: %s", title, exc)
            return jsonify({"success": False, "error": f"Failed to add to watchlist: {exc}"}), 500

    @bp.route("/watchlist/remove", methods=["POST"])
    def podcasts_watchlist_remove():
        """Remove a podcast from the watchlist."""
        body = request.get_json(silent=True) or {}
        feed_url = str(body.get("feed_url") or "").strip()
        itunes_id = body.get("itunes_id")
        try:
            itunes_id = int(itunes_id) if itunes_id is not None else None
        except (ValueError, TypeError):
            itunes_id = None

        if not feed_url and itunes_id is None:
            return jsonify({"success": False, "error": "feed_url or itunes_id required"}), 400

        db = _db()
        if db is None:
            return jsonify({"success": False, "error": "database unavailable"}), 500

        try:
            ok = db.remove_watchlist_podcast(feed_url=feed_url or None, itunes_id=itunes_id)
            return jsonify({"success": bool(ok), "is_watching": False})
        except Exception as exc:
            logger.exception("podcasts_watchlist_remove failed: %s", exc)
            return jsonify({"success": False, "error": f"Failed to remove: {exc}"}), 500

    @bp.route("/watchlist/settings", methods=["POST"])
    def podcasts_watchlist_settings():
        """Update settings (auto_download, retention_days) for a watchlisted podcast."""
        body = request.get_json(silent=True) or {}
        feed_url = str(body.get("feed_url") or "").strip()
        if not feed_url:
            return jsonify({"success": False, "error": "feed_url is required"}), 400

        auto_download = body.get("auto_download")
        if auto_download is not None:
            auto_download = bool(auto_download)

        retention_days = body.get("retention_days")
        if retention_days is not None:
            try:
                retention_days = max(0, int(retention_days))
            except (ValueError, TypeError):
                retention_days = None

        db = _db()
        if db is None:
            return jsonify({"success": False, "error": "database unavailable"}), 500

        try:
            ok = db.update_watchlist_podcast_settings(
                feed_url,
                auto_download=auto_download,
                retention_days=retention_days,
            )
            pod = db.get_watchlist_podcast(feed_url=feed_url)
            return jsonify({"success": bool(ok), "podcast": pod})
        except Exception as exc:
            logger.exception("podcasts_watchlist_settings failed for %s: %s", feed_url, exc)
            return jsonify({"success": False, "error": f"Failed to update settings: {exc}"}), 500

    return bp
