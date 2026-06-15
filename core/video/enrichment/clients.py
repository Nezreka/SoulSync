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
                              params={"api_key": self.api_key,
                                      "append_to_response": "external_ids,credits,images",
                                      "include_image_language": "en,null"},
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
                # The FULL season list (poster may be None) — drives both the
                # season-poster backfill and the episode cascade (so missing
                # episodes/seasons get represented, not just what's on the server).
                seasons = []
                for s in (dr.get("seasons") or []):
                    sn = s.get("season_number")
                    if sn is None:
                        continue
                    seasons.append({"season_number": sn,
                                    "poster_url": (self.IMG + s["poster_path"]) if s.get("poster_path") else None})
                if seasons:
                    meta["seasons"] = seasons
            self._add_credits(meta, dr.get("credits") or {}, dr.get("created_by") or [])
            logo = self._pick_logo((dr.get("images") or {}).get("logos") or [])
            if logo:
                meta["logo_url"] = self.LOGO + logo
        except Exception:
            logger.exception("TMDB details fetch failed for %s", title or tmdb_id)
        return {"id": tmdb_id, "metadata": {k: v for k, v in meta.items() if v}}

    PROFILE = "https://image.tmdb.org/t/p/w185"
    LOGO = "https://image.tmdb.org/t/p/w500"

    @staticmethod
    def _pick_logo(logos):
        """Prefer an English title logo, then a language-neutral one, then any."""
        if not logos:
            return None
        for lang in ("en", None):
            for lg in logos:
                if lg.get("iso_639_1") == lang and lg.get("file_path"):
                    return lg["file_path"]
        return logos[0].get("file_path")

    def _person(self, c, job=None, character=None):
        return {"name": c["name"], "tmdb_id": c.get("id"), "job": job, "character": character,
                "photo_url": (self.PROFILE + c["profile_path"]) if c.get("profile_path") else None}

    def _add_credits(self, meta, credits, created_by):
        """Parse TMDB cast/crew into meta['cast'] / meta['crew']."""
        cast = [self._person(c, character=c.get("character"))
                for c in (credits.get("cast") or [])[:20] if c.get("name")]
        if cast:
            meta["cast"] = cast
        # Crew: headline jobs only (directors / writers); plus TV creators, which
        # live in the top-level created_by, not the crew list.
        wanted = {"Director", "Writer", "Screenplay"}
        crew = [self._person(c, job=c.get("job")) for c in (credits.get("crew") or [])
                if c.get("name") and c.get("job") in wanted]
        crew += [self._person(c, job="Creator") for c in created_by if c.get("name")]
        if crew:
            meta["crew"] = crew

    POSTER_W = "https://image.tmdb.org/t/p/w300"
    PROVIDER = "https://image.tmdb.org/t/p/original"

    def extras(self, kind, tmdb_id, region="US"):
        """Live detail extras (not cached — providers change): a trailer, the
        'where to watch' providers for a region, and similar titles."""
        if not self.api_key or tmdb_id is None:
            return {}
        import requests
        path = ("/movie/" if kind == "movie" else "/tv/") + str(tmdb_id)
        r = requests.get(self.BASE + path, params={
            "api_key": self.api_key, "append_to_response": "videos,watch/providers,similar"}, timeout=15)
        r.raise_for_status()
        return self._parse_extras(kind, r.json() or {}, region)

    def _parse_extras(self, kind, d, region="US"):
        """Pull trailer / where-to-watch / similar out of a TMDB detail body. Shared
        by extras() and full_detail() so the search detail can render them too."""
        out = {}

        # Trailer — prefer a YouTube "Trailer", fall back to a teaser.
        trailer = None
        for v in (d.get("videos") or {}).get("results") or []:
            if v.get("site") == "YouTube" and v.get("type") in ("Trailer", "Teaser") and v.get("key"):
                trailer = {"key": v["key"], "name": v.get("name")}
                if v.get("type") == "Trailer":
                    break
        if trailer:
            out["trailer"] = trailer

        # Where to watch (one region; JustWatch-powered).
        wp = ((d.get("watch/providers") or {}).get("results") or {}).get(region) or {}
        provs, seen = [], set()
        for grp in ("flatrate", "free", "ads", "rent", "buy"):
            for p in (wp.get(grp) or []):
                name = p.get("provider_name")
                if name and name not in seen:
                    seen.add(name)
                    provs.append({"name": name,
                                  "logo": (self.PROVIDER + p["logo_path"]) if p.get("logo_path") else None})
        if provs:
            out["providers"] = provs[:8]
            out["providers_link"] = wp.get("link")
            out["region"] = region

        # More like this.
        sim = []
        for s in ((d.get("similar") or {}).get("results") or [])[:14]:
            title = s.get("title") or s.get("name")
            if title and s.get("id"):
                sim.append({"title": title, "tmdb_id": s["id"], "kind": kind,
                            "poster": (self.POSTER_W + s["poster_path"]) if s.get("poster_path") else None})
        if sim:
            out["similar"] = sim
        return out

    def season_episodes(self, tv_id, season_number):
        """Episode-level data for one season (still/overview/rating) — the show
        worker cascades over a show's seasons to backfill episodes the media
        server lacked. Returns {'overview', 'episodes': [...]} or None."""
        if not self.api_key or tv_id is None or season_number is None:
            return None
        import requests
        r = requests.get(self.BASE + "/tv/" + str(tv_id) + "/season/" + str(season_number),
                         params={"api_key": self.api_key}, timeout=15)
        r.raise_for_status()
        data = r.json() or {}
        out = []
        for e in (data.get("episodes") or []):
            en = e.get("episode_number")
            if en is None:
                continue
            ep = {"episode_number": en, "title": e.get("name"), "overview": e.get("overview"),
                  "air_date": e.get("air_date") or None, "runtime_minutes": e.get("runtime"),
                  "rating": e.get("vote_average") or None}
            if e.get("still_path"):
                ep["still_url"] = self.IMG + e["still_path"]
            out.append(ep)
        return {"overview": data.get("overview"),
                "poster_url": (self.IMG + data["poster_path"]) if data.get("poster_path") else None,
                "episodes": out}

    def search(self, query):
        """Multi-search (movies / TV / people) for the in-app search page. Returns
        a flat list of {kind, tmdb_id, title, year, poster, ...} — no external IDs,
        everything resolves back into SoulSync."""
        if not self.api_key or not (query or "").strip():
            return []
        import requests
        r = requests.get(self.BASE + "/search/multi", params={
            "api_key": self.api_key, "query": query, "include_adult": "false"}, timeout=15)
        r.raise_for_status()
        out = []
        for it in ((r.json() or {}).get("results") or [])[:32]:
            mt, tid = it.get("media_type"), it.get("id")
            if not tid:
                continue
            if mt == "movie":
                out.append({"kind": "movie", "tmdb_id": tid, "title": it.get("title"),
                            "year": (it.get("release_date") or "")[:4] or None,
                            "overview": it.get("overview"), "rating": it.get("vote_average") or None,
                            "poster": (self.POSTER_W + it["poster_path"]) if it.get("poster_path") else None})
            elif mt == "tv":
                out.append({"kind": "show", "tmdb_id": tid, "title": it.get("name"),
                            "year": (it.get("first_air_date") or "")[:4] or None,
                            "overview": it.get("overview"), "rating": it.get("vote_average") or None,
                            "poster": (self.POSTER_W + it["poster_path"]) if it.get("poster_path") else None})
            elif mt == "person":
                known = [k.get("title") or k.get("name") for k in (it.get("known_for") or [])]
                out.append({"kind": "person", "tmdb_id": tid, "title": it.get("name"),
                            "known_for": ", ".join([k for k in known if k][:3]) or None,
                            "department": it.get("known_for_department"),
                            "poster": (self.PROFILE + it["profile_path"]) if it.get("profile_path") else None})
        return out

    def trending(self, window="week"):
        """Trending movies + shows this week — fills the search page when idle."""
        if not self.api_key:
            return []
        import requests
        r = requests.get(self.BASE + "/trending/all/" + window,
                         params={"api_key": self.api_key}, timeout=15)
        r.raise_for_status()
        out = []
        for it in ((r.json() or {}).get("results") or []):
            mt, tid = it.get("media_type"), it.get("id")
            if not tid or mt not in ("movie", "tv"):
                continue
            if mt == "movie":
                out.append({"kind": "movie", "tmdb_id": tid, "title": it.get("title"),
                            "year": (it.get("release_date") or "")[:4] or None,
                            "rating": it.get("vote_average") or None,
                            "poster": (self.POSTER_W + it["poster_path"]) if it.get("poster_path") else None})
            else:
                out.append({"kind": "show", "tmdb_id": tid, "title": it.get("name"),
                            "year": (it.get("first_air_date") or "")[:4] or None,
                            "rating": it.get("vote_average") or None,
                            "poster": (self.POSTER_W + it["poster_path"]) if it.get("poster_path") else None})
        return out[:20]

    def full_detail(self, kind, tmdb_id):
        """Complete detail for a TMDB title NOT in the library — shaped like the
        library detail payload but with direct image URLs (so the same detail UI
        renders it). Seasons carry counts; episodes load lazily per season."""
        if not self.api_key or tmdb_id is None:
            return None
        import requests
        path = ("/movie/" if kind == "movie" else "/tv/") + str(tmdb_id)
        r = requests.get(self.BASE + path, params={
            "api_key": self.api_key,
            "append_to_response": "external_ids,credits,images,videos,watch/providers,similar",
            "include_image_language": "en,null"}, timeout=15)
        r.raise_for_status()
        dr = r.json() or {}
        if not dr.get("id"):
            return None
        ext = dr.get("external_ids") or {}
        logo = self._pick_logo((dr.get("images") or {}).get("logos") or [])
        cmeta = {}
        self._add_credits(cmeta, dr.get("credits") or {}, dr.get("created_by") or [])
        out = {
            "kind": kind, "tmdb_id": tmdb_id,
            "title": dr.get("title") or dr.get("name"),
            "overview": dr.get("overview"), "tagline": dr.get("tagline") or None,
            "status": dr.get("status"), "rating": dr.get("vote_average") or None,
            "imdb_id": ext.get("imdb_id") or dr.get("imdb_id"),
            "poster_url": (self.IMG + dr["poster_path"]) if dr.get("poster_path") else None,
            "backdrop_url": (self.IMG + dr["backdrop_path"]) if dr.get("backdrop_path") else None,
            "logo": (self.LOGO + logo) if logo else None,
            "genres": [g.get("name") for g in (dr.get("genres") or []) if g.get("name")],
            "cast": [{"name": p["name"], "character": p.get("character"),
                      "photo": p.get("photo_url"), "tmdb_id": p.get("tmdb_id")}
                     for p in cmeta.get("cast") or []],
            "crew": [{"name": p["name"], "job": p.get("job"), "tmdb_id": p.get("tmdb_id")}
                     for p in cmeta.get("crew") or []],
            "_extras": self._parse_extras(kind, dr),
        }
        if kind == "movie":
            out["year"] = (dr.get("release_date") or "")[:4] or None
            out["release_date"] = dr.get("release_date") or None
            out["runtime_minutes"] = dr.get("runtime")
            out["studio"] = next((c.get("name") for c in (dr.get("production_companies") or [])), None)
        else:
            out["year"] = (dr.get("first_air_date") or "")[:4] or None
            out["first_air_date"] = dr.get("first_air_date") or None
            out["last_air_date"] = dr.get("last_air_date") or None
            ert = dr.get("episode_run_time") or []
            out["runtime_minutes"] = ert[0] if ert else None
            out["network"] = next((n.get("name") for n in (dr.get("networks") or [])), None)
            out["tvdb_id"] = _int(ext.get("tvdb_id"))
            seasons = []
            for s in (dr.get("seasons") or []):
                num = s.get("season_number")
                if num is None:
                    continue
                seasons.append({
                    "season_number": num,
                    "title": s.get("name") or ("Specials" if num == 0 else "Season %d" % num),
                    "poster_url": (self.POSTER_W + s["poster_path"]) if s.get("poster_path") else None,
                    "episode_count": s.get("episode_count") or 0})
            out["_seasons"] = sorted(seasons, key=lambda s: s["season_number"])
        return out

    def person(self, tmdb_id):
        """Person detail + their filmography (cast + crew credits) for the in-app
        person page. Everything points back to TMDB ids we resolve in SoulSync."""
        if not self.api_key or tmdb_id is None:
            return None
        import requests
        r = requests.get(self.BASE + "/person/" + str(tmdb_id), params={
            "api_key": self.api_key, "append_to_response": "combined_credits,external_ids"}, timeout=15)
        r.raise_for_status()
        d = r.json() or {}
        if not d.get("id"):
            return None
        cc = d.get("combined_credits") or {}
        seen, credits = set(), []
        for c in (cc.get("cast") or []) + (cc.get("crew") or []):
            mt, tid = c.get("media_type"), c.get("id")
            if not tid or mt not in ("movie", "tv"):
                continue
            kind = "movie" if mt == "movie" else "show"
            key = (kind, tid)
            if key in seen:
                continue
            seen.add(key)
            date = c.get("release_date") or c.get("first_air_date") or ""
            credits.append({
                "kind": kind, "tmdb_id": tid, "title": c.get("title") or c.get("name"),
                "year": (date or "")[:4] or None, "date": date or None,
                "role": c.get("character") or c.get("job") or None,
                "popularity": c.get("popularity") or 0,
                "poster": (self.POSTER_W + c["poster_path"]) if c.get("poster_path") else None})
        credits.sort(key=lambda x: x["popularity"], reverse=True)
        return {
            "tmdb_id": d.get("id"), "name": d.get("name"),
            "biography": d.get("biography") or None,
            "known_for": d.get("known_for_department") or None,
            "birthday": d.get("birthday") or None, "deathday": d.get("deathday") or None,
            "place_of_birth": d.get("place_of_birth") or None,
            "photo": (self.PROFILE + d["profile_path"]) if d.get("profile_path") else None,
            "credits": credits}


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


class OMDbAuthError(Exception):
    """OMDb rejected the API key (HTTP 401 / 'Invalid API key!'). Distinct from a
    transient error or a genuine 'no rating' so the worker can pause instead of
    churning the whole library on a bad key."""


class OMDBClient:
    """Ratings provider — IMDb / Rotten Tomatoes / Metacritic by imdb_id. Not a
    matcher (we already have the id), so it's used as a ratings backfill, not a
    worker."""
    BASE = "https://www.omdbapi.com/"

    def __init__(self, api_key):
        self.api_key = api_key or None

    @property
    def enabled(self):
        return bool(self.api_key)

    def test(self):
        if not self.api_key:
            return False, "No OMDb API key set"
        import requests
        try:
            r = requests.get(self.BASE, params={"apikey": self.api_key, "i": "tt0111161"}, timeout=12)
            d = r.json() if r.status_code == 200 else {}
            if d.get("Response") == "True":
                return True, "OMDb connection OK"
            if "invalid api key" in (d.get("Error") or "").lower():
                return False, "Invalid OMDb API key"
            return False, "OMDb returned HTTP " + str(r.status_code)
        except Exception:
            logger.exception("OMDb test failed")
            return False, "Could not reach OMDb"

    def ratings(self, imdb_id):
        if not self.api_key or not imdb_id:
            return None
        import requests
        r = requests.get(self.BASE, params={"apikey": self.api_key, "i": imdb_id}, timeout=12)
        # A bad/expired key is a 401 (sometimes a 200 with "Invalid API key!") — a
        # config problem that affects EVERY item, so flag it distinctly.
        if r.status_code == 401:
            raise OMDbAuthError("OMDb rejected the API key (HTTP 401)")
        r.raise_for_status()
        d = r.json() or {}
        if d.get("Response") != "True":
            if "invalid api key" in (d.get("Error") or "").lower():
                raise OMDbAuthError(d.get("Error") or "Invalid OMDb API key")
            return None        # genuine "no data for this title"
        out = {}
        ir = d.get("imdbRating")
        if ir and ir != "N/A":
            try:
                out["imdb_rating"] = float(ir)
            except (TypeError, ValueError):
                pass
        for rt in (d.get("Ratings") or []):
            if rt.get("Source") == "Rotten Tomatoes":
                try:
                    out["rt_rating"] = int((rt.get("Value") or "").rstrip("%"))
                except (TypeError, ValueError):
                    pass
        ms = d.get("Metascore")
        if ms and ms != "N/A":
            try:
                out["metacritic"] = int(ms)
            except (TypeError, ValueError):
                pass
        return out


def build_clients(db) -> dict:
    """Construct the source clients from the saved API keys (in video_settings).
    OMDb is included as a worker (a ratings filler) alongside the matchers."""
    return {
        "tmdb": TMDBClient(db.get_setting("tmdb_api_key")),
        "tvdb": TVDBClient(db.get_setting("tvdb_api_key")),
        "omdb": OMDBClient(db.get_setting("omdb_api_key")),
    }
