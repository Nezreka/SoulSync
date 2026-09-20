"""whose library a caller is looking at (#1199).

a profile can have a library of its own (its own output folder, its own
library on the media server). rows the scan of that library writes carry
the profile as owner; the shared library's rows carry none. the scope is:

  'shared'  the shared library: rows with no owner (today's library,
            exactly as before). the admin and every plain profile
  <int>     an own-library profile: its rows only
  None      every row of every library. nobody's default; a job that
            really needs all of it sets it explicitly

the admin is a user of the shared library like anyone else: a track that
only exists in someone's own library is not the admin's (a copy of their
own is the answer, by design), and their library page shows nothing of
another profile's.

resolved from the current profile (request or background) with a short
cache on the profile's mode so the db is not asked on every query. a
scan or a worker acting for one profile sets it explicitly.
"""

from __future__ import annotations

import contextvars
import threading
import time
from typing import Optional, Union

Scope = Union[None, str, int]

_UNSET = object()
# default is UNSET, not None: None is a real scope (the admin's)
_explicit_scope: "contextvars.ContextVar[object]" = contextvars.ContextVar("library_scope", default=_UNSET)

_mode_cache: dict = {}
_mode_cache_lock = threading.Lock()
_MODE_CACHE_TTL_S = 30.0


# The one switch for per-directory libraries. False means: the read scope
# filters, the download target follows the selected directory, the per-profile
# scans run, and the admin gets the switcher. It was True for exactly as long
# as any one of those was missing -- half of the feature is worse than none of
# it, because a profile would be shown the admin's tracks as its own while its
# downloads went somewhere else.
#
# Kept as a named constant rather than deleted: it is the single place to turn
# the feature off again if a directory-shaped bug shows up in the wild, and
# every piece still reads it.
SCOPE_PARKED = True


def set_library_scope(scope: Scope):
    """force a scope for the current context (a scan running for one
    profile). returns a token for reset_library_scope."""
    return _explicit_scope.set(scope)


def reset_library_scope(token) -> None:
    try:
        _explicit_scope.reset(token)
    except Exception:  # noqa: BLE001 - token from another context
        _explicit_scope.set(_UNSET)


_any_own_library: dict = {}


def any_own_library_exists() -> bool:
    """Does ANY profile keep a library of its own?

    The real gate for the scope predicates. `SCOPE_PARKED` is the kill switch;
    this is the far more common case: an install where nobody has a second
    directory has nothing to separate, so the predicates must be ABSENT rather
    than merely true. A tautological clause is not free -- it is two correlated
    EXISTS over lib2_tracks/lib2_track_files per artist row, in both the COUNT
    and the page query, with the deliberate `+` keeping SQLite off the owner
    index. That is the shape the perf work measured at 21.7s.

    Cached like the per-profile mode, and invalidated by the same call.
    """
    if SCOPE_PARKED:
        return False
    now = time.monotonic()
    with _mode_cache_lock:
        hit = _any_own_library.get("v")
        if hit and now - hit[0] < _MODE_CACHE_TTL_S:
            return hit[1]
    try:
        from database.music_database import get_database
        exists = bool(get_database().get_own_library_profiles())
    except Exception:  # noqa: BLE001 - unreadable means nothing to separate
        exists = False
    with _mode_cache_lock:
        _any_own_library["v"] = (now, exists)
    return exists


def owner_for_new_file(profile_id=None):
    """Whose library a file being written right now belongs to. None = shared.

    The SELECTED scope wins over the profile that started the download: an
    admin who switched the library page to someone else's directory and
    grabbed a track there meant that directory, and the file has to end up
    where it was put (E-04 in docs/library-v2-dir-ownership.md). Only when
    nothing is selected does the download's own profile decide.

    None while the feature is parked, whatever else is true -- that is the
    NULL every existing row has, so a parked build writes what it wrote
    yesterday.
    """
    if SCOPE_PARKED:
        return None
    # A PICK is not the same as no pick, and 'shared' is a pick: an admin who
    # selected the shared library and then triggers a download carrying another
    # profile's id meant the shared library. Testing the session first, and
    # only falling through when there is nothing selected, is what makes
    # "the selected scope wins" true for every value and not just for ints.
    picked = session_scope()
    if picked is not _UNSET:
        return int(picked) if not isinstance(picked, str) and picked is not None else None
    if not profile_id:
        return None
    # Only now is the profile's own mode worth a database read. Evaluating it
    # eagerly meant a cache miss opened a second connection from inside the
    # caller's open write transaction.
    scope = library_scope_for_profile(profile_id)
    return int(scope) if not isinstance(scope, str) and scope is not None else None


def carrying_scope(fn):
    """Wrap ``fn`` so it runs under the scope in effect right now.

    A ContextVar does not cross a thread start: a pool worker begins with an
    empty context, so ``current_library_scope()`` there falls back to the
    request-less default and answers "do we own this" from the shared library.
    Anything handed to an executor from inside a scoped block has to carry the
    scope with it explicitly, and this is how.
    """
    import functools

    scope = current_library_scope()

    @functools.wraps(fn)
    def _run(*args, **kwargs):
        token = set_library_scope(scope)
        try:
            return fn(*args, **kwargs)
        finally:
            reset_library_scope(token)

    return _run


def invalidate_library_scope_cache() -> None:
    with _mode_cache_lock:
        _mode_cache.clear()
        _any_own_library.clear()


def own_library_supported() -> bool:
    from core.settings import config_manager
    return config_manager.get_active_media_server() in ('plex', 'jellyfin')


def library_scope_for_profile(profile_id: Optional[int]) -> Scope:
    """the scope a profile reads the library through."""
    if not profile_id or int(profile_id) == 1:
        return 'shared'          # profile 1 is always on the shared library
    if SCOPE_PARKED:
        return 'shared'
    if not own_library_supported():
        return 'shared'
    pid = int(profile_id)
    now = time.monotonic()
    with _mode_cache_lock:
        hit = _mode_cache.get(pid)
        if hit and now - hit[0] < _MODE_CACHE_TTL_S:
            return hit[1]
    scope: Scope = 'shared'
    try:
        from database.music_database import get_database
        if get_database().get_profile_library(pid).get('mode') == 'own':
            scope = pid
    except Exception:  # noqa: BLE001 - a db that will not answer reads as the shared library
        scope = 'shared'
    with _mode_cache_lock:
        _mode_cache[pid] = (now, scope)
    return scope


SESSION_KEY = "library_scope"


def session_scope():
    """The directory an ADMIN picked in the library page switcher, or _UNSET.

    Admins are not confined to one library the way a profile is -- they manage
    the instance, so they get to look at any of them, and what they pick is
    also where their grabs land (E-04/E-07). The pick lives in the session, so
    it survives a page change and cannot leak to anyone else. Non-admins never
    have one: a profile's scope is its own library, full stop.
    """
    if SCOPE_PARKED:
        return _UNSET
    try:
        from flask import session

        from core.profile_context import is_admin_request
        raw = session.get(SESSION_KEY)
        if raw is None or not is_admin_request():
            return _UNSET
    except Exception:  # noqa: BLE001 - no request, no pick
        return _UNSET
    if raw == "all":
        return None            # every library at once
    if raw == "shared":
        return "shared"
    try:
        picked = int(raw)
    except (TypeError, ValueError):
        return _UNSET
    # Re-checked on every read, not only when it was stored. A profile can stop
    # keeping its own library, or be deleted, while the pick sits in a session:
    # left unchecked the page then filters on an id nothing owns and comes back
    # empty with no explanation, and a grab is stamped for a library whose
    # folder no longer resolves -- a file on disk no scope can ever see.
    try:
        from database.music_database import get_database
        live = {int(p["id"]) for p in (get_database().get_own_library_profiles() or [])}
    except Exception:  # noqa: BLE001 - cannot verify, do not trust
        return _UNSET
    return picked if picked in live else _UNSET


def current_library_scope() -> Scope:
    """the scope of whoever is asking right now."""
    forced = _explicit_scope.get()
    if forced is not _UNSET:
        return forced
    picked = session_scope()
    if picked is not _UNSET:
        return picked
    from core.profile_context import get_current_profile_id
    return library_scope_for_profile(get_current_profile_id())


def library_artist_id(artist_id, server_source, owner_profile_id=None):
    """Jellyfin artists are server-global; each own library needs its own parent row.
    Album and track IDs remain native so playback and playlist writes are unchanged.
    """
    value = str(artist_id)
    if server_source == 'jellyfin' and owner_profile_id is not None:
        prefix = f'own-jellyfin:{int(owner_profile_id)}:'
        if not value.startswith(prefix):
            return prefix + native_jellyfin_artist_id(value)
    return value


def native_jellyfin_artist_id(artist_id):
    value = str(artist_id)
    parts = value.split(':', 2)
    if len(parts) == 3 and parts[0] == 'own-jellyfin' and parts[1].isdigit():
        return parts[2]
    return value
