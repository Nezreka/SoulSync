"""Write Kodi/Jellyfin-style sidecars next to imported video — NFO metadata + the full
artwork set — so the library is self-describing on disk (server-agnostic + portable).

The metadata comes from the on-demand TMDB detail fetch (``engine.tmdb_detail``), passed
in as ``meta``. This module is pure — NFO XML building + a write plan — with the
filesystem injected, so it's unit-tested without disk or network. Best-effort BY
CONTRACT: the caller treats every failure as non-fatal (a missing poster or a flaky
fetch never breaks an import).

Layout written:
  Movie folder:  movie.nfo, poster.jpg, fanart.jpg, clearlogo.png
  Show root:     tvshow.nfo, poster.jpg, fanart.jpg, clearlogo.png, seasonNN-poster.jpg

Isolated: stdlib only; no music imports.
"""

from __future__ import annotations

from typing import Any
from xml.sax.saxutils import escape as _xesc

_HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'


def _t(tag: str, value: Any) -> str:
    """A single XML element, or '' when the value is empty (so absent fields are
    simply omitted rather than written blank)."""
    if value is None:
        return ""
    s = str(value).strip()
    if not s:
        return ""
    return "  <%s>%s</%s>\n" % (tag, _xesc(s), tag)


def _year(meta: dict) -> str:
    y = meta.get("year")
    return str(y) if y else ""


def _uniqueids(meta: dict, *, tmdb_default: bool) -> str:
    out = ""
    tmdb = meta.get("tmdb_id")
    if tmdb:
        out += '  <uniqueid type="tmdb"%s>%s</uniqueid>\n' % (
            ' default="true"' if tmdb_default else "", _xesc(str(tmdb)))
    if meta.get("tvdb_id"):
        out += '  <uniqueid type="tvdb"%s>%s</uniqueid>\n' % (
            ' default="true"' if not tmdb_default else "", _xesc(str(meta["tvdb_id"])))
    if meta.get("imdb_id"):
        out += '  <uniqueid type="imdb">%s</uniqueid>\n' % _xesc(str(meta["imdb_id"]))
    return out


def _genres(meta: dict) -> str:
    return "".join(_t("genre", g) for g in (meta.get("genres") or []) if g)


def _actors(meta: dict, limit: int = 20) -> str:
    out = ""
    for i, c in enumerate((meta.get("cast") or [])[:limit]):
        if not isinstance(c, dict) or not c.get("name"):
            continue
        out += "  <actor>\n    <name>%s</name>\n" % _xesc(str(c["name"]))
        if c.get("character"):
            out += "    <role>%s</role>\n" % _xesc(str(c["character"]))
        out += "    <order>%d</order>\n  </actor>\n" % i
    return out


def _artwork_tags(meta: dict) -> str:
    out = ""
    if meta.get("poster_url"):
        out += '  <thumb aspect="poster">%s</thumb>\n' % _xesc(str(meta["poster_url"]))
    if meta.get("logo"):
        out += '  <thumb aspect="clearlogo">%s</thumb>\n' % _xesc(str(meta["logo"]))
    if meta.get("backdrop_url"):
        out += "  <fanart>\n    <thumb>%s</thumb>\n  </fanart>\n" % _xesc(str(meta["backdrop_url"]))
    return out


def nfo_movie(meta: dict) -> str:
    """A Kodi/Jellyfin-compatible ``movie.nfo`` from a TMDB detail dict."""
    meta = meta if isinstance(meta, dict) else {}
    body = (
        _t("title", meta.get("title"))
        + _t("originaltitle", meta.get("original_title"))
        + _t("year", _year(meta))
        + _t("plot", meta.get("overview"))
        + _t("outline", meta.get("overview"))
        + _t("tagline", meta.get("tagline"))
        + _t("runtime", meta.get("runtime_minutes"))
        + _t("mpaa", meta.get("content_rating"))
        + _t("studio", meta.get("studio"))
        + _t("premiered", meta.get("release_date"))
        + _t("status", meta.get("status"))
        + _t("rating", meta.get("rating"))
        + _genres(meta)
        + _uniqueids(meta, tmdb_default=True)
        + _artwork_tags(meta)
        + _actors(meta)
    )
    return _HEAD + "<movie>\n" + body + "</movie>\n"


def nfo_tvshow(meta: dict) -> str:
    """A Kodi/Jellyfin-compatible ``tvshow.nfo`` from a TMDB detail dict."""
    meta = meta if isinstance(meta, dict) else {}
    body = (
        _t("title", meta.get("title"))
        + _t("year", _year(meta))
        + _t("plot", meta.get("overview"))
        + _t("outline", meta.get("overview"))
        + _t("tagline", meta.get("tagline"))
        + _t("runtime", meta.get("runtime_minutes"))
        + _t("mpaa", meta.get("content_rating"))
        + _t("studio", meta.get("network"))
        + _t("premiered", meta.get("first_air_date"))
        + _t("status", meta.get("status"))
        + _t("rating", meta.get("rating"))
        + _genres(meta)
        + _uniqueids(meta, tmdb_default=True)
        + _artwork_tags(meta)
        + _actors(meta)
    )
    return _HEAD + "<tvshow>\n" + body + "</tvshow>\n"


def _season_art(meta: dict) -> list:
    """(url, filename) pairs for season posters — seasonNN-poster.jpg (Specials → 00)."""
    out = []
    for s in (meta.get("_seasons") or []):
        if not isinstance(s, dict) or not s.get("poster_url"):
            continue
        try:
            n = int(s.get("season_number"))
        except (TypeError, ValueError):
            continue
        out.append((s["poster_url"], "season%02d-poster.jpg" % n))
    return out


def plan_sidecars(scope: Any, meta: dict, settings: dict) -> dict:
    """Decide what to write for one imported item. Returns
    ``{"nfo": (filename, content) | None, "art": [(url, filename), ...]}`` — gated by
    the ``write_nfo`` / ``save_artwork`` settings. Pure."""
    meta = meta if isinstance(meta, dict) else {}
    settings = settings if isinstance(settings, dict) else {}
    sc = str(scope or "").lower()
    nfo = None
    art = []

    if settings.get("write_nfo"):
        if sc == "movie":
            nfo = ("movie.nfo", nfo_movie(meta))
        elif sc == "episode":
            nfo = ("tvshow.nfo", nfo_tvshow(meta))

    if settings.get("save_artwork"):
        if meta.get("poster_url"):
            art.append((meta["poster_url"], "poster.jpg"))
        if meta.get("backdrop_url"):
            art.append((meta["backdrop_url"], "fanart.jpg"))
        if meta.get("logo"):
            art.append((meta["logo"], "clearlogo.png"))
        if sc == "episode":
            art.extend(_season_art(meta))

    return {"nfo": nfo, "art": art}


def write(folder: str, scope: Any, meta: dict, settings: dict, fs: Any) -> None:
    """Write the planned sidecars into ``folder`` via the injected ``fs``
    (``list_dir``, ``makedirs``, ``write_text(path, str)``, ``save_url(url, path)``).
    Idempotent: skips any file already present so upgrades/re-imports don't refetch.
    Best-effort: each file is independent and a failure is swallowed."""
    import os
    plan = plan_sidecars(scope, meta, settings)
    if not plan["nfo"] and not plan["art"]:
        return
    try:
        existing = {str(n).lower() for n in (fs.list_dir(folder) or [])}
    except Exception:   # noqa: BLE001
        existing = set()
    try:
        fs.makedirs(folder)
    except Exception:   # noqa: BLE001
        return

    if plan["nfo"]:
        name, content = plan["nfo"]
        if name.lower() not in existing:
            try:
                fs.write_text(os.path.join(folder, name), content)
            except Exception:   # noqa: BLE001
                pass
    for url, name in plan["art"]:
        if name.lower() in existing:
            continue
        try:
            fs.save_url(url, os.path.join(folder, name))
        except Exception:   # noqa: BLE001
            pass


def write_for(dest_path: str, scope: Any, poster_url: Any, detail: Any,
              settings: dict, fs: Any) -> None:
    """Resolve the sidecar folder from a finished file's path and write into it. The
    folder is the movie folder (parent of the file) or, for an episode, the SHOW root
    (parent of the Season folder) so show-level art/NFO land once. ``poster_url`` is the
    baseline from the download row; ``detail`` (the TMDB fetch, or None) fills the rest —
    so a poster still lands even when the detail fetch is unavailable."""
    import os
    sc = str(scope or "").lower()
    if sc == "movie":
        folder = os.path.dirname(dest_path)
    elif sc == "episode":
        folder = os.path.dirname(os.path.dirname(dest_path))   # show root, above Season NN
    else:
        return
    if not folder:
        return
    meta = {"poster_url": poster_url} if poster_url else {}
    if isinstance(detail, dict):
        meta.update(detail)
    write(folder, sc, meta, settings, fs)


__all__ = ["nfo_movie", "nfo_tvshow", "plan_sidecars", "write", "write_for"]
