"""TMDB / TVDB match clients for the video enrichment workers.

Thin adapters: ``.enabled`` (an API key is configured) and ``.match(kind, title,
year) -> {"id", "metadata"} | None``. These talk to real TMDB/TVDB APIs and are
validated against the live services; the worker LOGIC is unit-tested with a fake
client. Keys come from video_settings.
"""

from __future__ import annotations

from utils.logging_config import get_logger

logger = get_logger("video_enrichment.clients")


def _int(val):
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


class TMDBClient:
    BASE = "https://api.themoviedb.org/3"
    IMG = "https://image.tmdb.org/t/p/original"

    def __init__(self, api_key):
        self.api_key = api_key or None

    @property
    def enabled(self):
        return bool(self.api_key)

    def test(self):
        if not self.api_key:
            return False, "No TMDB API key set"
        import requests
        try:
            r = requests.get(self.BASE + "/configuration", params={"api_key": self.api_key}, timeout=12)
            if r.status_code == 200:
                return True, "TMDB connection OK"
            if r.status_code == 401:
                return False, "Invalid TMDB API key"
            return False, "TMDB returned HTTP " + str(r.status_code)
        except Exception:
            logger.exception("TMDB test failed")
            return False, "Could not reach TMDB"

    def match(self, kind, title, year, known_id=None):
        if not self.api_key:
            return None
        import requests
        # The server already knows the TMDB id → go straight to the details
        # fetch (accurate, one call). Otherwise fall back to a title/year search.
        tmdb_id = _int(known_id)
        meta = {}
        if tmdb_id is None:
            if not title:
                return None
            path = "/search/movie" if kind == "movie" else "/search/tv"
            params = {"api_key": self.api_key, "query": title}
            if year:
                params["year" if kind == "movie" else "first_air_date_year"] = year
            resp = requests.get(self.BASE + path, params=params, timeout=15)
            # A non-200 (429 rate-limit, 5xx, timeout-as-error) is a FAILED call,
            # not "no match" — raise so the worker records 'error' (retried later)
            # instead of burning the item to 'not_found'.
            resp.raise_for_status()
            results = (resp.json() or {}).get("results") or []
            if not results:
                return None
            tmdb_id = results[0].get("id")
            meta["overview"] = results[0].get("overview")
            if tmdb_id is None:
                return None
        try:
            detail_path = "/movie/" if kind == "movie" else "/tv/"
            dr = requests.get(self.BASE + detail_path + str(tmdb_id),
                              params={"api_key": self.api_key, "append_to_response": "external_ids"},
                              timeout=15).json() or {}
            meta["overview"] = dr.get("overview") or meta.get("overview")
            if dr.get("backdrop_path"):
                meta["backdrop_url"] = self.IMG + dr["backdrop_path"]
            ext = dr.get("external_ids") or {}
            meta["imdb_id"] = ext.get("imdb_id") or dr.get("imdb_id")
            # Everything TMDB offers (same call) — the worker backfills only the
            # gaps the server left.
            meta["tagline"] = dr.get("tagline")
            meta["status"] = dr.get("status")
            if dr.get("vote_average"):
                meta["rating"] = dr.get("vote_average")
            gs = [g.get("name") for g in (dr.get("genres") or []) if g.get("name")]
            if gs:
                meta["genres"] = gs
            if kind == "movie":
                meta["release_date"] = dr.get("release_date")
                meta["runtime_minutes"] = dr.get("runtime")
            else:
                meta["first_air_date"] = dr.get("first_air_date")
                meta["last_air_date"] = dr.get("last_air_date")
                ert = dr.get("episode_run_time") or []
                if ert:
                    meta["runtime_minutes"] = ert[0]
                meta["tvdb_id"] = _int(ext.get("tvdb_id"))
        except Exception:
            logger.exception("TMDB details fetch failed for %s", title or tmdb_id)
        return {"id": tmdb_id, "metadata": {k: v for k, v in meta.items() if v}}


class TVDBClient:
    BASE = "https://api4.thetvdb.com/v4"

    def __init__(self, api_key):
        self.api_key = api_key or None
        self._token = None

    @property
    def enabled(self):
        return bool(self.api_key)

    def test(self):
        if not self.api_key:
            return False, "No TVDB API key set"
        try:
            token = self._auth()
            if token:
                return True, "TVDB connection OK"
            return False, "TVDB login failed — check the key"
        except Exception:
            logger.exception("TVDB test failed")
            return False, "Could not reach TVDB"

    def _auth(self, force=False):
        if self._token and not force:
            return self._token
        import requests
        self._token = None
        r = requests.post(self.BASE + "/login", json={"apikey": self.api_key}, timeout=15).json() or {}
        self._token = (r.get("data") or {}).get("token")
        return self._token

    def _authed_get(self, path, params=None):
        """GET with the bearer token, transparently re-authenticating once if the
        cached token has expired (401). Raises on any other non-200 so the worker
        records 'error' rather than a false 'not_found'."""
        import requests
        token = self._auth()
        if not token:
            return None
        r = requests.get(self.BASE + path, headers={"Authorization": "Bearer " + token},
                         params=params, timeout=15)
        if r.status_code == 401 and self._auth(force=True):   # token expired → refresh once
            r = requests.get(self.BASE + path, headers={"Authorization": "Bearer " + self._token},
                             params=params, timeout=15)
        r.raise_for_status()
        return r.json() or {}

    def match(self, kind, title, year, known_id=None):
        if kind != "show" or not self.api_key:
            return None
        tvdb_id = _int(known_id)
        meta = {}
        if tvdb_id is None:
            if not title:
                return None
            r = self._authed_get("/search", {"query": title, "type": "series"})
            results = (r or {}).get("data") or []
            if not results:
                return None
            top = results[0]
            tvdb_id = _int(top.get("tvdb_id") or top.get("id"))
            meta["overview"] = top.get("overview")
        else:
            # Known id from the server → fetch the extended record (overview +
            # genres + everything TVDB offers).
            try:
                dr = self._authed_get("/series/" + str(tvdb_id) + "/extended")
                sd = (dr or {}).get("data") or {}
                meta["overview"] = sd.get("overview")
                gs = [g.get("name") for g in (sd.get("genres") or []) if g.get("name")]
                if gs:
                    meta["genres"] = gs
            except Exception:
                logger.exception("TVDB details fetch failed for %s", title or tvdb_id)
        if tvdb_id is None:
            return None
        return {"id": tvdb_id, "metadata": {k: v for k, v in meta.items() if v}}


def build_clients(db) -> dict:
    """Construct the source clients from the saved API keys (in video_settings)."""
    return {
        "tmdb": TMDBClient(db.get_setting("tmdb_api_key")),
        "tvdb": TVDBClient(db.get_setting("tvdb_api_key")),
    }
