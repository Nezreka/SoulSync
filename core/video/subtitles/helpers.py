"""Generic subtitle helpers: pure parsing plus the filesystem-injected write loop.

Moved verbatim out of the old ``core/video/subtitles.py`` module when it became a
package — behavior is identical, and the package ``__init__`` re-exports these so
existing callers keep working.

Isolated: stdlib only; no music imports.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Callable


#: A language code that is safe to embed in a sidecar filename: 2–3 letters
#: with an optional region/script subtag (pt-br, zh-cn, sr-latn). Anything
#: else (notably anything containing ``/`` or ``.``) is dropped by
#: ``parse_langs`` and rejected by ``validate_lang_codes`` — a code flows
#: into ``srt_name`` and from there into a filesystem path.
_LANG_CODE_RE = re.compile(r"^[a-z]{2,3}(-[a-z]{2,4})?$")


#: Hard cap on override language lists: each code materializes one wanted
#: row per import, so an unbounded list is an accidental DoS on the queue.
MAX_LANG_CODES = 32


def valid_lang_code(code: Any) -> bool:
    """True when ``code`` is safe to embed in a sidecar filename.

    The filesystem-boundary guard for language codes that arrive via DB rows
    (which may predate the ``parse_langs`` filtering): callers must skip —
    never fetch or write for — codes that fail this check.
    """
    return bool(_LANG_CODE_RE.match(str(code or "").strip().lower()))


def parse_langs(raw: Any) -> list:
    """'en, es ; fr' → ['en','es','fr'] (lower-cased, de-duped). Defaults to ['en'].

    Tokens that are not valid language codes (``_LANG_CODE_RE``) are DROPPED:
    a language code flows into the sidecar filename (``srt_name``), so an
    unfiltered code like ``../../evil`` would write outside the media folder.
    """
    out = []
    for tok in re.split(r"[,\s;]+", str(raw or "")):
        t = tok.strip().lower()
        if t and t not in out and _LANG_CODE_RE.match(t):
            out.append(t)
    return out or ["en"]


_DEFAULT_PROVIDER_ORDER = ["opensubtitles"]


def parse_provider_order(raw: Any) -> list:
    """A JSON list string, list, or tuple → cleaned provider ids in order
    (non-empty strings, stripped, de-duped). Non-string entries are dropped;
    garbage (or a missing/empty value) falls back to ``["opensubtitles"]``.
    Never raises."""
    try:
        order = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:  # noqa: BLE001 - json.loads can raise RecursionError at
        # extreme nesting; the contract is "never raises"
        order = None
    if not isinstance(order, (list, tuple)):
        return list(_DEFAULT_PROVIDER_ORDER)
    cleaned = []
    for p in order:
        s = p.strip() if isinstance(p, str) else ""
        if s and s not in cleaned:
            cleaned.append(s)
    return cleaned or list(_DEFAULT_PROVIDER_ORDER)


def srt_name(video_path: Any, lang: str, hi: bool = False,
             forced: bool = False) -> str:
    """Sidecar filename for a video+language (no directory).

    Bazarr/Plex/Jellyfin tier convention — every variant gets a DISTINCT name
    so hi/forced rows can never overwrite each other (or the plain track):
    ``<stem>.<lang>.srt``, ``<stem>.<lang>.hi.srt``,
    ``<stem>.<lang>.forced.srt``, ``<stem>.<lang>.hi.forced.srt``.

    Backward compatible: the old two-arg call keeps producing the plain name.
    """
    stem = os.path.splitext(os.path.basename(str(video_path or "")))[0]
    name = "%s.%s" % (stem, lang)
    if hi:
        name += ".hi"
    if forced:
        name += ".forced"
    return name + ".srt"


def write_subtitles(video_path: str, langs: list, identity: dict, fetch: Callable, fs: Any) -> None:
    """For each language not already present as ``<stem>.<lang>.srt`` next to the video,
    fetch and write it via the injected ``fetch`` + ``fs`` (``list_dir``, ``write_text``).
    Idempotent + best-effort."""
    if not fetch:
        return
    folder = os.path.dirname(str(video_path or ""))
    try:
        existing = {str(n).lower() for n in (fs.list_dir(folder) or [])}
    except Exception:   # noqa: BLE001
        existing = set()
    for lang in (langs or []):
        name = srt_name(video_path, lang)
        if name.lower() in existing:
            continue
        try:
            text = fetch(identity, lang)
            if text:
                fs.write_text(os.path.join(folder, name), text)
        except Exception:   # noqa: BLE001 - a quota miss / network blip is expected, never fatal
            pass


def validate_lang_codes(languages: Any) -> list:
    """['en', 'ES'] → ['en', 'es'] (lower-cased, de-duped, order kept).

    Raises ValueError when the input isn't a non-empty list/tuple of 2–3
    letter alpha codes with an optional region/script subtag (pt-br, zh-cn,
    zh-tw, sr-latn), or when it holds more than ``MAX_LANG_CODES`` codes —
    an unbounded list would materialize one wanted row per code per import.
    Shared by the DB override writer and the API so both reject the same
    garbage the same way.
    """
    if not isinstance(languages, (list, tuple)) or not languages:
        raise ValueError("languages must be a non-empty list of language codes")
    out = []
    for lang in languages:
        code = str(lang or "").strip().lower()
        if code in out:
            continue
        if not _LANG_CODE_RE.match(code):
            raise ValueError("invalid language code: %r" % (lang,))
        out.append(code)
    if len(out) > MAX_LANG_CODES:
        raise ValueError("too many language codes (max %d)" % MAX_LANG_CODES)
    return out


class SubtitleLookupError(Exception):
    """A subtitle override lookup failed transiently (DB hiccup, …).

    Distinct from "no override exists" (which falls back to the global
    language list): the effective language set is UNKNOWN. Callers that
    DELETE rows based on the set (the worker's residue checks) must skip
    deletion when this is raised; callers that CREATE rows (the import hook)
    fall back to the global list so a lookup hiccup never loses a fetch.
    """


def show_override_for_download(db: Any, dl: Any) -> list | None:
    """Per-show override languages for an episode-shaped download dict.

    Fresh episode grabs key their ``subtitle_wanted`` rows on the download
    row (the episode row doesn't exist until the scanner ingests the file),
    so the library-item lookup in ``effective_subtitle_languages`` can't see
    them — but the grab knows the show's TMDB id, which maps to the library
    show row the per-show override lives on.

    Returns the override languages, or None when the download isn't
    episode-shaped, the show isn't in the library (yet), or no override
    exists.

    Raises :class:`SubtitleLookupError` when the override can't be determined
    because a DB lookup failed (transient) — callers must NOT treat that as
    "no override" (which would silently degrade to the global list and let
    the worker's residue checks delete still-wanted rows).
    """
    dl = dl or {}
    if str(dl.get("kind") or "").lower() == "movie":
        return None
    mid = dl.get("media_id")
    if not mid:
        return None
    src = str(dl.get("media_source") or "").lower()
    if src == "tmdb":
        try:
            show_tmdb = int(mid)
        except (TypeError, ValueError):
            return None  # unresolvable identity, not a transient failure
    elif src == "library":
        try:
            show_tmdb, _imdb = db.media_tmdb_id("show", mid)
        except Exception as e:  # noqa: BLE001 - transient: unknown, not "no override"
            raise SubtitleLookupError(
                "media_tmdb_id lookup failed") from e
    else:
        return None
    if show_tmdb is None:
        return None
    try:
        show_id = db.library_id_for_tmdb("show", show_tmdb)
    except Exception as e:  # noqa: BLE001 - transient: unknown, not "no override"
        raise SubtitleLookupError(
            "library_id_for_tmdb lookup failed") from e
    if show_id is None:
        return None
    try:
        return db.subtitle_override_get("show", show_id)
    except Exception as e:  # noqa: BLE001 - transient: unknown, not "no override"
        raise SubtitleLookupError(
            "subtitle_override_get lookup failed") from e


def effective_subtitle_languages(db: Any, kind: Any, item_id: Any, settings: Any) -> list:
    """The subtitle languages that apply to one video, honoring per-item overrides.

    Precedence: (1) a ``subtitle_overrides`` row for the movie itself, or for an
    episode's parent show — including a fresh episode grab still keyed on its
    download row, resolved through the download's show TMDB id; (2) the global
    ``subtitle_langs`` setting.

    ``kind`` is the wanted-row video kind ('movie' | 'episode' | 'download' |
    ...). A 'download' row whose download is gone (or was never episode-shaped)
    falls back to the global setting.

    Raises :class:`SubtitleLookupError` when an override lookup fails
    transiently — the effective set is then UNKNOWN, not "global". Callers
    that delete rows based on this set must skip deletion on that signal
    (mirroring the orphan check's None-means-unknown handling); callers that
    only need a fetch list fall back to the global languages themselves.
    """
    k = str(kind or "").strip().lower()
    override = None
    try:
        if k == "movie":
            override = db.subtitle_override_get("movie", item_id)
        elif k == "episode":
            show_id = db.subtitle_show_for_episode(item_id)
            if show_id is not None:
                override = db.subtitle_override_get("show", show_id)
        elif k == "download":
            # A missing row (cleared download) reads as "no override" — only a
            # raised DB error is a transient failure (UNKNOWN).
            dl = db.get_video_download(item_id)
            override = show_override_for_download(db, dl)
    except SubtitleLookupError:
        raise
    except Exception as e:  # noqa: BLE001 - transient: unknown, never "global"
        raise SubtitleLookupError(
            "override lookup failed for %r %r" % (kind, item_id)) from e
    if override:
        return list(override)
    return parse_langs((settings or {}).get("subtitle_langs"))
