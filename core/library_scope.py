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

This branch adds the admin's pick on top (E-04/E-11, see
docs/library-v2-dir-ownership.md): an admin chooses a library in the header,
and that choice is both what they see and where what they start lands. And
the library a DOWNLOAD belongs to is decided once, while the request still
exists, and travels on the batch (``library_owner_id``) -- the workers that
finish it run with no request and no session to ask.
"""

from __future__ import annotations

import contextlib
import contextvars
import threading
import time
from typing import Any, Optional, Union

Scope = Union[None, str, int]

_UNSET = object()
# default is UNSET, not None: None is a real scope (the admin's)
_explicit_scope: "contextvars.ContextVar[object]" = contextvars.ContextVar("library_scope", default=_UNSET)

_mode_cache: dict = {}
_mode_cache_lock = threading.Lock()
_MODE_CACHE_TTL_S = 30.0

# The one switch for per-directory libraries: False means the read scope
# filters, downloads land in the selected directory, the per-profile scans run
# and the admin gets the switcher. Kept as a named constant because it is the
# single place to turn the whole feature off again if a directory-shaped bug
# shows up in the wild, and every piece still reads it. An install on which
# nobody keeps a library of its own is unaffected either way: the predicates
# are absent there (`any_own_library_exists`).
SCOPE_PARKED = False


def set_library_scope(scope: Scope):
    """force a scope for the current context (a scan running for one
    profile). returns a token for reset_library_scope."""
    return _explicit_scope.set(scope)


def reset_library_scope(token) -> None:
    try:
        _explicit_scope.reset(token)
    except Exception:  # noqa: BLE001 - token from another context
        _explicit_scope.set(_UNSET)


def invalidate_library_scope_cache() -> None:
    with _mode_cache_lock:
        _mode_cache.clear()


def library_config_changed() -> None:
    """A profile's library, the shared folder or the active media server changed.

    Drops the cached modes and re-derives which folder -- and so which library
    -- every file is in (core.library2.library_roots). Cheap when nothing that
    matters changed: the folder table is compared first."""
    invalidate_library_scope_cache()
    try:
        from core.library2.library_roots import sync_library_roots
        sync_library_roots()
    except Exception:  # noqa: BLE001, S110 - never fails the save that triggered it
        pass


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


def wishes_in_own_library(profile_id: Optional[int], db=None) -> bool:
    """E-13: may this non-admin profile wish -- monitor, search, grab -- in
    the library it reads? Only in one of its own, and only when it may
    download at all. Whether the caller is an admin is the caller's call."""
    try:
        if not profile_id or library_scope_for_profile(profile_id) != int(profile_id):
            return False
        if db is None:
            from database.music_database import get_database
            db = get_database()
        return bool((db.get_profile(int(profile_id)) or {}).get("can_download", 1))
    except Exception:  # noqa: BLE001 - unreadable mode: no wishing
        return False


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


# ── this branch: the pick, the owner of a new file, and the batch ────────────


def own_library_ids() -> frozenset:
    """Ids of the profiles that keep a library of their own and can use it now.

    Cached like the per-profile mode and invalidated by the same call. Empty
    while the feature is parked or the active media server has no own
    libraries: then every profile reads the shared library anyway.
    """
    if SCOPE_PARKED:
        return frozenset()
    now = time.monotonic()
    with _mode_cache_lock:
        hit = _mode_cache.get("own_ids")
        if hit and now - hit[0] < _MODE_CACHE_TTL_S:
            return hit[1]
    ids: frozenset = frozenset()
    try:
        if own_library_supported():
            from database.music_database import get_database
            ids = frozenset(int(p["id"]) for p in (get_database().get_own_library_profiles() or []))
    except Exception:  # noqa: BLE001 - unreadable means nothing to separate
        ids = frozenset()
    with _mode_cache_lock:
        _mode_cache["own_ids"] = (now, ids)
    return ids


def any_own_library_exists() -> bool:
    """Does ANY profile keep a library of its own?

    The real gate for the scope predicates. `SCOPE_PARKED` is the kill switch;
    this is the far more common case: an install where nobody has a second
    directory has nothing to separate, so the predicates must be ABSENT rather
    than merely true. A tautological clause is not free -- it is two correlated
    EXISTS over lib2_tracks/lib2_track_files per artist row, in both the COUNT
    and the page query, with the deliberate `+` keeping SQLite off the owner
    index. That is the shape the perf work measured at 21.7s.
    """
    return bool(own_library_ids())


SESSION_KEY = "library_scope"


def session_scope():
    """The library an ADMIN picked in the header switcher, or _UNSET.

    Admins are not confined to one library the way a profile is -- they manage
    the instance, so they get to look at any of them, and what they pick is
    also where their grabs land (E-04/E-07). The pick lives in the session, so
    it survives a page change and cannot leak to anyone else. Non-admins never
    have one: a profile's scope is its own library, full stop.
    """
    if SCOPE_PARKED:
        return _UNSET
    try:
        from flask import has_request_context, session
        if not has_request_context():
            return _UNSET
        raw = session.get(SESSION_KEY)
        if raw is None:
            return _UNSET
        from core.profile_context import is_admin_request
        if not is_admin_request():
            return _UNSET
    except Exception:  # noqa: BLE001 - no request, no pick
        return _UNSET
    live = own_library_ids()
    if not live:
        # nothing to pick between (any more): the pick is void, not "shared"
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
    return picked if picked in live else _UNSET


def owner_for_scope(scope: Scope) -> Optional[int]:
    """The owner a file written in ``scope`` gets. The shared library and
    "every library" are both the shared folder: there is no folder called
    all, and writing into the house is the safe reading of it."""
    if isinstance(scope, bool) or scope is None or isinstance(scope, str):
        return None
    return int(scope)


def scope_for_owner(owner: Optional[int]) -> Scope:
    return "shared" if owner is None else int(owner)


def owner_for_new_file(profile_id=None):
    """Whose library a file being written right now belongs to. None = shared.

    In this order: a scope set explicitly for this unit of work (a batch's
    worker, a scan for one profile); the library an admin picked (E-04: a
    grab lands where the page was pointed); the profile's own library; shared.

    An explicit scope and a pick are decisions even when they say "shared" --
    an admin who picked the shared library and then triggers a download
    carrying another profile's id meant the shared library.
    """
    if SCOPE_PARKED:
        return None
    forced = _explicit_scope.get()
    if forced is not _UNSET and forced is not None:
        return owner_for_scope(forced)
    picked = session_scope()
    if picked is not _UNSET:
        return owner_for_scope(picked)
    if not profile_id:
        return None
    # Only now is the profile's own mode worth a database read. Evaluating it
    # eagerly meant a cache miss opened a second connection from inside the
    # caller's open write transaction.
    return owner_for_scope(library_scope_for_profile(profile_id))


BATCH_OWNER_KEY = "library_owner_id"


def batch_library_owner(batch: Any) -> Optional[int]:
    """The library a download batch fills. None = shared.

    The key is the decision: present -- even as None, which is "the shared
    library, on purpose" -- means it was decided while a request (and the
    admin's pick) still existed. Absent, the batch's own profile decides.
    """
    if not isinstance(batch, dict):
        return None
    if BATCH_OWNER_KEY in batch:
        owner = batch.get(BATCH_OWNER_KEY)
        try:
            return int(owner) if owner is not None else None
        except (TypeError, ValueError):
            return None
    if not any_own_library_exists():
        return None  # one library: no per-profile lookup, often under tasks_lock
    return owner_for_scope(library_scope_for_profile(batch.get("profile_id")))


def batch_scope(batch: Any) -> Scope:
    """The scope a batch's workers ask "do we already have this" through."""
    return scope_for_owner(batch_library_owner(batch))


def stamp_batch_owner(batch: Any) -> Any:
    """Decide a new batch's library if its creator did not, and who it acts for.

    A batch an admin starts while working in someone's own library acts for
    that library's profile (E-12): its failed tracks go back onto THAT
    wishlist, its history is that profile's. Only the caller's own profile is
    swapped -- a batch created for an explicit other profile keeps it.
    Idempotent.
    """
    if not isinstance(batch, dict) or not any_own_library_exists():
        return batch  # one library: nothing to decide, the batch stays as built
    try:
        pid = batch.get("profile_id")
        if pid is not None:
            acting = acting_profile_id(pid)
            if acting != pid:
                from core.profile_context import get_current_profile_id
                if get_current_profile_id() == pid:
                    batch["profile_id"] = acting
        if BATCH_OWNER_KEY not in batch:
            batch[BATCH_OWNER_KEY] = owner_for_new_file(batch.get("profile_id"))
    except Exception:  # noqa: BLE001, S110 - undecided still resolves by profile later
        pass
    return batch


@contextlib.contextmanager
def library_scope(scope: Scope):
    """Run a block under ``scope`` (a batch's worker, a job for one library)."""
    token = set_library_scope(scope)
    try:
        yield scope
    finally:
        reset_library_scope(token)


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


def acting_profile_id(profile_id: Optional[int]) -> Optional[int]:
    """Whose per-profile library intent a request acts on (E-12).

    Wishlist and watchlist entries belong to the library they fill. An admin
    who picked someone's own library in the header is working in it: what they
    add goes onto that profile's lists, and those are the lists they see. With
    no such pick -- and for everyone else -- it is the caller's own profile.
    """
    picked = session_scope()
    if picked is not _UNSET and owner_for_scope(picked) is not None:
        return int(picked)
    return profile_id
