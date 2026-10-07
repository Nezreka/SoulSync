"""Run a video watchlist automation for every profile that follows something.

the video watchlist and wishlist are per-profile, but the system scan
automations are owned by admin (profile 1) and every handler reads its lists
through the background profile. so a non-admin's followed people, studios,
channels, playlists and shows were never scanned.

``run_per_profile`` runs the handler once as its owner, then, when the owner
is admin, once more for each other profile with follows of that kind, with the
background profile set to that profile. reads and writes then land on that
profile's own lists, exactly like the owner pass. an automation a non-admin
owns keeps running for that profile only.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Callable, Dict, Iterable, List, Optional

from utils.logging_config import get_logger

logger = get_logger("automation.video_profile_fanout")

ADMIN_PROFILE_ID = 1


def _owner_profile() -> int:
    try:
        from core.profile_context import get_current_profile_id
        return int(get_current_profile_id() or ADMIN_PROFILE_ID)
    except Exception:  # noqa: BLE001 - no context means the admin-owned system run
        return ADMIN_PROFILE_ID


def _default_follower_profiles(kinds: Iterable[str]) -> List[int]:
    """profiles with at least one followed row of these kinds."""
    from api.video import get_video_db
    return get_video_db().watchlist_profile_ids(list(kinds))


def _profile_names() -> Dict[int, str]:
    try:
        from database.music_database import get_database
        return {int(p['id']): str(p.get('name') or p['id'])
                for p in (get_database().get_all_profiles() or [])}
    except Exception:  # noqa: BLE001 - names are only for the log
        return {}


def extra_profiles(owner: int, followers: Iterable[int]) -> List[int]:
    """the profiles to scan after the owner pass. only admin fans out."""
    if owner != ADMIN_PROFILE_ID:
        return []
    return sorted({int(p) for p in followers if p is not None} - {owner})


_SUMMED = ('people', 'studios', 'channels', 'playlists', 'shows', 'movies_added',
           'upcoming', 'promoted', 'videos_added', 'episodes_added', 'shows_pruned')


def _merge(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    out = dict(results[0])
    for r in results[1:]:
        for k, v in r.items():
            if k in _SUMMED and isinstance(v, int) and isinstance(out.get(k, 0), int):
                out[k] = out.get(k, 0) + v
            elif k not in out:
                out[k] = v
    errors = [r.get('error') for r in results if r.get('status') == 'error']
    if errors:
        out['status'] = 'error'
        out['error'] = '; '.join(str(e) for e in errors if e)
    return out


def run_per_profile(
    run_pass: Callable[[Any, int, bool], Dict[str, Any]],
    deps: Any,
    kinds: Iterable[str],
    *,
    follower_profiles: Optional[Callable[[Iterable[str]], List[int]]] = None,
    owner_fn: Optional[Callable[[], int]] = None,
) -> Dict[str, Any]:
    """``run_pass(deps, profile_id, is_owner)`` runs one handler pass.

    the owner pass always runs first and unchanged. each extra pass runs with
    the background profile set to that profile. only the last pass may report
    a terminal status to the progress tracker, so the card doesn't flip to
    finished halfway through.
    """
    from core.profile_context import reset_background_profile, set_background_profile

    owner = (owner_fn or _owner_profile)()
    try:
        extras = extra_profiles(owner, (follower_profiles or _default_follower_profiles)(kinds))
    except Exception as e:  # noqa: BLE001 - can't list followers: scan the owner like before
        logger.warning("could not list profiles following %s: %s", list(kinds), e)
        extras = []
    names = _profile_names() if extras else {}
    passes = [owner] + extras
    results: List[Dict[str, Any]] = []
    for i, pid in enumerate(passes):
        last = i == len(passes) - 1
        pass_deps = deps if last and not extras else _pass_deps(
            deps, label=None if pid == owner else names.get(pid, 'profile %s' % pid),
            terminal=last)
        token = None if pid == owner else set_background_profile(pid)
        try:
            results.append(run_pass(pass_deps, pid, pid == owner) or {})
        except Exception as e:  # noqa: BLE001 - one profile failing must not skip the rest
            logger.exception("video watchlist pass for profile %s failed", pid)
            results.append({'status': 'error', 'error': str(e)})
        finally:
            if token is not None:
                reset_background_profile(token)
    return _merge(results)


def _pass_deps(deps: Any, *, label: Optional[str], terminal: bool) -> Any:
    real = deps.update_progress

    def update_progress(*args, **kwargs):
        if not terminal:
            kwargs.pop('status', None)
        if label and kwargs.get('log_line'):
            kwargs['log_line'] = '[%s] %s' % (label, kwargs['log_line'])
        return real(*args, **kwargs)

    if dataclasses.is_dataclass(deps):
        return dataclasses.replace(deps, update_progress=update_progress)
    clone = type('PassDeps', (), {})()
    clone.__dict__.update(getattr(deps, '__dict__', {}))
    clone.update_progress = update_progress
    return clone
