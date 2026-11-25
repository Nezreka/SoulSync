"""Song identification — the engine behind the listen button.

POST /api/listen/identify   multipart ``audio``: a browser clip, or any audio file
GET  /api/listen/status     whether the engine can run at all (ffmpeg + shazamio)

The clip is transcoded to 16 kHz mono WAV by ffmpeg and fingerprinted against
Shazam's public recognition endpoint through ``shazamio`` — the same signature
SongRec sends, and it needs no key or account. The answer is then re-searched
against the configured metadata source, so the client receives a real track
object (id, album, artists, cover) that the wishlist modal already knows how to
take, rather than a title the user has to retype into the search box.

Recognition holds a gunicorn thread for a few seconds. Two run at once and the
third is answered 429 rather than queued: a lookup that hangs on the far end
must not eat the worker's thread pool, which every open socket.io tab shares.
Both ffmpeg and the recogniser are bounded, and every failure below the route is
converted to a JSON answer — a browser that has already shown its microphone
sheet must never receive Flask's HTML error page.
"""

from __future__ import annotations

import asyncio
import difflib
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any

from flask import Blueprint, jsonify, request
from werkzeug.exceptions import HTTPException

from core.permissions import requires
from utils.logging_config import get_logger

logger = get_logger("listen")

bp = Blueprint("listen_routes", __name__)

# a 10 s browser clip is ~100 kB; this is room for a whole song file, since the
# upload button takes whatever the user drags in
MAX_UPLOAD_BYTES = 12 * 1024 * 1024
# Shazam answers from seconds of audio. Decoding is bounded so a user who drops
# a 90-minute DJ set does not hold the thread for the length of the file.
CLIP_SECONDS = 30
FFMPEG_TIMEOUT_S = 60
# The caller is waiting on a modal, and the worker's thread is shared with
# socket.io. Shazam's own retry ladder can run for minutes; this is the ceiling.
RECOGNIZE_TIMEOUT_S = 20
# Closing the HTTP session should take milliseconds. Bounded separately so a
# half-open connection cannot keep the recognition thread after the answer.
RECOGNIZE_CLOSE_TIMEOUT_S = 5
# below this, a title match is a different song and "add to wishlist" would
# download something nobody asked for
MIN_TITLE_RATIO = 0.75

#: The runtime dependencies this feature is allowed to use, named once so the
#: status endpoint and the tests cannot drift apart.
REQUIRED_DEPENDENCIES = (
    ("ffmpeg", "ffmpeg is not available in the container"),
    ("shazamio", "shazamio is not installed or could not be imported"),
)

_slots = threading.BoundedSemaphore(2)
_engine_lock = threading.Lock()
_engine_ok = None          # only the positive answer is cached; a missing
_engine_reason = ""        # package is worth re-checking after an install

# Injected by configure() — web_server callables, bound once at import time.
# The names are a closed set: a typo in the wiring fails at boot, not at the
# first microphone click.
_DEPENDENCIES = (
    "get_database",
    "build_search_deps",
    "library_check",
    "media_server_engine",       # engine object, or a getter for the live one
    "config_manager",
)
get_database = None
build_search_deps = None
library_check = None
media_server_engine = None
config_manager = None


class ClipTimeout(Exception):
    """ffmpeg took longer than FFMPEG_TIMEOUT_S."""


class ClipUndecodable(Exception):
    """ffmpeg ran and refused the bytes as audio."""


class ClipUnreadable(Exception):
    """The temporary clip could not be written or read (disk, permissions)."""


class EngineMissing(Exception):
    """ffmpeg disappeared between the status check and the transcode."""


class RecognizerTimeout(Exception):
    """Shazam did not answer inside RECOGNIZE_TIMEOUT_S."""


class RecognizerError(Exception):
    """The recogniser could not be constructed or reached."""


def configure(**deps: Any) -> None:
    """Bind the app's live callables, refusing anything not on the list."""
    unknown = set(deps) - set(_DEPENDENCIES)
    if unknown:
        raise KeyError(f"listen_routes.configure: unknown dependency {sorted(unknown)!r}")
    globals().update(deps)


def create_blueprint():
    return bp


def _engine() -> dict:
    """Whether the exact runtime dependency set is present, and why not."""
    global _engine_ok, _engine_reason
    if _engine_ok:
        return {"ok": True, "reason": ""}
    with _engine_lock:
        if _engine_ok:
            return {"ok": True, "reason": ""}
        missing = [
            (module, reason)
            for module, reason in REQUIRED_DEPENDENCIES
            if not _dependency_available(module)
        ]
        if missing:
            _module, reason = missing[0]
            _engine_reason = reason
            return {"ok": False, "reason": _engine_reason}
        _engine_ok = True
        _engine_reason = ""
        return {"ok": True, "reason": ""}


def _dependency_available(name: str) -> bool:
    if name == "ffmpeg":
        return shutil.which(name) is not None
    return _module_available(name)


def _module_available(name: str) -> bool:
    try:
        __import__(name)
        return True
    except Exception:
        return False


def _to_wav(src: Path, dst: Path) -> None:
    """One channel at 16 kHz — what the fingerprint wants, and nothing else.

    Every failure is typed: the route turns each one into a JSON answer instead
    of letting ``subprocess``'s exception become a 500 HTML page.
    """
    try:
        subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(src),
             "-t", str(CLIP_SECONDS), "-ac", "1", "-ar", "16000",
             "-c:a", "pcm_s16le", str(dst)],
            check=True, capture_output=True, timeout=FFMPEG_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as e:
        raise ClipTimeout(f"ffmpeg exceeded {FFMPEG_TIMEOUT_S}s") from e
    except FileNotFoundError as e:
        raise EngineMissing("ffmpeg is not available in the container") from e
    except subprocess.CalledProcessError as e:
        detail = (e.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        raise ClipUndecodable(detail[-1] if detail else "ffmpeg could not decode the clip") from e
    except OSError as e:
        raise ClipUnreadable(str(e)) from e

    try:
        if not dst.exists() or dst.stat().st_size == 0:
            raise ClipUndecodable("ffmpeg produced an empty WAV")
    except OSError as e:
        raise ClipUnreadable(str(e)) from e


def _recognize(wav: Path) -> dict:
    """Run the recogniser on its own event loop, bounded and always closed.

    gunicorn's gthread workers have no loop, and shazamio's client is bound to
    the loop it was created on, so each call builds one and closes it — the
    "Unclosed client session" warning came from not closing the Shazam object.
    A hung far end is cancelled here rather than holding the worker forever.
    """
    try:
        from shazamio import Shazam
    except Exception as e:  # install problem, not a code path
        raise RecognizerError(f"shazamio is not installed ({e})") from e

    loop = asyncio.new_event_loop()
    shazam = None
    try:
        try:
            shazam = Shazam()
        except Exception as e:
            raise RecognizerError(f"the recogniser could not be created ({e})") from e
        try:
            return loop.run_until_complete(
                asyncio.wait_for(shazam.recognize(str(wav)), timeout=RECOGNIZE_TIMEOUT_S)
            )
        except (asyncio.TimeoutError, TimeoutError) as e:
            raise RecognizerTimeout(
                f"Shazam did not answer within {RECOGNIZE_TIMEOUT_S}s"
            ) from e
        except asyncio.CancelledError as e:
            raise RecognizerError("recognition was cancelled") from e
        except Exception as e:
            raise RecognizerError(str(e)) from e
    finally:
        if shazam is not None:
            try:
                loop.run_until_complete(
                    asyncio.wait_for(shazam.close(), timeout=RECOGNIZE_CLOSE_TIMEOUT_S)
                )
            except (Exception, asyncio.CancelledError):
                logger.debug("listen: closing the Shazam session failed", exc_info=True)
        try:
            pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        except (Exception, asyncio.CancelledError):
            logger.debug("listen: cancelling recogniser tasks failed", exc_info=True)
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        except (Exception, asyncio.CancelledError):
            logger.debug("listen: shutting down async generators failed", exc_info=True)
        loop.close()


def _parse_answer(answer: dict):
    """Shazam's payload → the fields the card shows, or None when unmatched."""
    track = (answer or {}).get("track") or {}
    if not track.get("title"):
        return None

    meta = {}
    for section in track.get("sections") or []:
        for row in section.get("metadata") or []:
            if row.get("title"):
                meta[str(row["title"]).lower()] = row.get("text")

    images = track.get("images") or {}
    share = track.get("share") or {}
    genres = track.get("genres") or {}
    if isinstance(genres, list):
        genres = genres[0] if genres else {}
    matches = track.get("matches") or answer.get("matches") or []

    return {
        "title": track.get("title"),
        "artist": track.get("subtitle"),
        "album": meta.get("album"),
        "label": meta.get("label"),
        "released": meta.get("released") or meta.get("release date"),
        "genre": (genres or {}).get("primary"),
        "cover_url": images.get("coverart") or share.get("image"),
        "background_url": images.get("background"),
        "shazam_url": share.get("href"),
        "shazam_id": track.get("key"),
        "isrc": track.get("isrc"),
        "apple_id": track.get("albumadamid"),
        "duration_s": track.get("duration"),
        "match_offset_s": (matches[0].get("offset") if matches else None),
    }


def _best_row(ident, rows):
    """The catalogue row that is the same recording, or None.

    Title carries the decision; the artist only separates two rows of the same
    title. A near-miss returns nothing on purpose — offering a one-click
    wishlist add for the wrong song is worse than offering a plain search.
    """
    try:
        from core.text.normalize import normalize_key
    except Exception:  # pragma: no cover
        def normalize_key(value):
            return (value or "").strip().casefold()

    want_title = normalize_key(ident.get("title") or "")
    want_artist = normalize_key(ident.get("artist") or "")
    if not want_title:
        return None

    best, best_score = None, 0.0
    for row in rows or []:
        title_ratio = difflib.SequenceMatcher(None, want_title, normalize_key(row.get("name") or "")).ratio()
        score = title_ratio
        if want_artist:
            score += 0.5 * difflib.SequenceMatcher(None, want_artist, normalize_key(row.get("artist") or "")).ratio()
        if score > best_score:
            best, best_score = row, score
    if best is None:
        return None
    if difflib.SequenceMatcher(None, want_title, normalize_key(best.get("name") or "")).ratio() < MIN_TITLE_RATIO:
        return None
    return best


def _resolve(ident):
    """Re-search the identification on the sources the download pipeline uses."""
    if build_search_deps is None:
        return None
    query = " ".join(p for p in (ident.get("artist"), ident.get("title")) if p).strip()
    if not query:
        return None

    try:
        from core.search import sources as _sources
        deps = build_search_deps()
        for client, source in (
            (deps.get_metadata_fallback_client(), deps.get_metadata_fallback_source()),
            (deps.spotify_client, "spotify"),
        ):
            if client is None:
                continue
            try:
                answer = _sources.search_source(query, client, source)
            except Exception as e:
                logger.debug("listen: %s search for %r failed: %s", source, query, e)
                continue
            row = _best_row(ident, answer.get("tracks") or [])
            if row:
                return row
    except Exception as e:
        logger.warning("listen: metadata resolution failed: %s", e)
    return None


def _in_library(row):
    """True/False, or None when the question could not be asked."""
    engine = media_server_engine() if callable(media_server_engine) else media_server_engine
    if not row or library_check is None or get_database is None:
        return None
    try:
        from core.profile_context import get_current_profile_id
        plex = engine.client("plex") if engine else None
        answer = library_check.check_library_presence(
            database=get_database(),
            plex_client=plex,
            config_manager=config_manager,
            profile_id=get_current_profile_id(),
            albums=[],
            tracks=[row],
        )
        rows = answer.get("tracks") or []
        return bool(rows and rows[0].get("in_library"))
    except Exception as e:
        logger.debug("listen: library presence check failed: %s", e)
        return None


def _error(code: str, message: str, status: int):
    """The one JSON shape every failure below the route returns."""
    return jsonify({"success": False, "error": code, "message": message}), status


@bp.errorhandler(Exception)
def _listen_unhandled(error):
    """A browser with a microphone sheet open must never receive HTML."""
    if isinstance(error, HTTPException):
        return _error(
            (error.name or "http_error").lower().replace(" ", "_"),
            error.description or "The request could not be handled.",
            error.code or 500,
        )
    logger.error("listen: unhandled error: %s", error, exc_info=True)
    return _error("internal_error", "The identification failed on the server.", 500)


@bp.route("/api/listen/status", methods=["GET"])
@requires("music.access")
def listen_status():
    engine = _engine()
    return jsonify({
        "success": True,
        "available": engine["ok"],
        "reason": engine["reason"],
        "dependencies": [name for name, _reason in REQUIRED_DEPENDENCIES],
    })


@bp.route("/api/listen/identify", methods=["POST"])
@requires("music.access")
def identify_song():
    """Identify the song in an uploaded clip.

    Body: multipart/form-data, field ``audio`` — the browser records
    ``audio/webm;codecs=opus`` (Safari: ``audio/mp4``), and ffmpeg reads both.
    Returns ``identification`` (Shazam's answer), ``match`` (the metadata
    source's track object, when one was found) and ``in_library``.
    """
    engine = _engine()
    if not engine["ok"]:
        return _error("engine_unavailable", engine["reason"], 503)

    length = request.content_length or 0
    if length > MAX_UPLOAD_BYTES:
        return _error(
            "too_large",
            f"Clip must be under {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
            413,
        )

    upload = request.files.get("audio")
    if upload is None:
        return _error("no_audio", "Send the clip as the multipart field 'audio'.", 400)

    if not _slots.acquire(blocking=False):
        return _error(
            "busy",
            "Two identifications are already running. Try again in a moment.",
            429,
        )

    answer = None
    try:
        try:
            with tempfile.TemporaryDirectory(prefix="listen-") as workdir:
                raw = Path(workdir) / "clip"
                wav = Path(workdir) / "clip.wav"
                try:
                    upload.save(raw)
                except OSError as e:
                    logger.info("listen: clip could not be saved: %s", e)
                    return _error("clip_unreadable", "The clip could not be received.", 500)
                try:
                    if raw.stat().st_size > MAX_UPLOAD_BYTES:
                        return _error(
                            "too_large",
                            f"Clip must be under {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
                            413,
                        )
                except OSError as e:
                    logger.info("listen: clip could not be measured: %s", e)
                    return _error("clip_unreadable", "The clip could not be received.", 500)

                try:
                    _to_wav(raw, wav)
                except ClipTimeout as e:
                    logger.info("listen: ffmpeg timed out: %s", e)
                    return _error("decode_timeout", "That clip took too long to decode.", 504)
                except ClipUndecodable as e:
                    logger.info("listen: clip could not be decoded: %s", e)
                    return _error("undecodable", "That clip could not be read as audio.", 400)
                except EngineMissing as e:
                    logger.warning("listen: ffmpeg disappeared: %s", e)
                    return _error("engine_unavailable", str(e), 503)
                except ClipUnreadable as e:
                    logger.warning("listen: clip workspace failed: %s", e)
                    return _error("clip_unreadable", "The clip could not be processed.", 500)

                try:
                    answer = _recognize(wav)
                except RecognizerTimeout as e:
                    logger.info("listen: recogniser timed out: %s", e)
                    return _error(
                        "recogniser_timeout",
                        "The recogniser took too long to answer. Try again.",
                        504,
                    )
                except RecognizerError as e:
                    logger.warning("listen: recognition failed: %s", e)
                    return _error(
                        "recogniser_failed",
                        "The recogniser could not be reached. Try again.",
                        502,
                    )
        except OSError as e:
            logger.warning("listen: temporary workspace failed: %s", e)
            return _error("clip_unreadable", "The clip could not be processed.", 500)
    finally:
        _slots.release()

    ident = _parse_answer(answer)
    if not ident:
        return jsonify({
            "success": True,
            "identification": None,
            "match": None,
            "in_library": None,
            "message": "No match. The clip may be too short, too quiet, "
                       "or the song may not be in Shazam's catalogue.",
        })

    logger.info("listen: identified %s — %s", ident.get("artist"), ident.get("title"))
    row = _resolve(ident)
    return jsonify({
        "success": True,
        "identification": ident,
        "match": row,
        "in_library": _in_library(row),
        "query": " ".join(p for p in (ident.get("artist"), ident.get("title")) if p).strip(),
    })
