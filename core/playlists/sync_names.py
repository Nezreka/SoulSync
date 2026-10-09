"""the name a mirrored playlist syncs under on the media server.

the server playlist is found by name, so two mirrors with the same name that
sync as the same server account write over each other every sync, silently
(a deezer "Blumple" and a spotify "Blumple"; two profiles' "Discover Weekly"
when one of them has no server login of its own and syncs as the app account).

the rule, only ever applied when two mirrors would land on one server playlist:

* different profiles: the admin keeps the plain name (else the lowest profile
  id does); everyone else gets their profile name on the end,
  "Release Radar - ThomasClan".
* one profile, two sources: the oldest mirror keeps the name, the others get
  their source, "Blumple (Deezer)".

profiles on different server accounts never clash, so nothing changes for them.
inside SoulSync mirrors are tracked by id; this only names the server side.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

from utils.logging_config import get_logger

logger = get_logger("playlists.sync_names")

ADMIN_PROFILE_ID = 1
SHARED_ACCOUNT = 'shared'

_SOURCE_LABELS = {
    'spotify': 'Spotify', 'spotify_public': 'Spotify', 'deezer': 'Deezer', 'tidal': 'Tidal',
    'qobuz': 'Qobuz', 'youtube': 'YouTube', 'listenbrainz': 'ListenBrainz',
    'beatport': 'Beatport', 'file': 'File', 'itunes': 'Apple Music',
    'soulsync': 'SoulSync',
}


def _norm(name: Any) -> str:
    return str(name or '').strip().lower()


def _base_name(mirror: Dict[str, Any]) -> str:
    from core.playlists.naming import effective_mirrored_name
    return effective_mirrored_name(mirror) or str(mirror.get('name') or 'Playlist')


def _source_label(source: Any) -> str:
    s = str(source or '').strip()
    return _SOURCE_LABELS.get(s.lower(), s.replace('_', ' ').title() or 'Other')


def resolve_sync_names(mirrors: Iterable[Dict[str, Any]], account_of: Dict[Any, str],
                       profile_names: Dict[Any, str]) -> Dict[Any, str]:
    """{mirror id: the name it syncs under}. pure.

    ``account_of`` maps a profile id to the server account it syncs as
    (``'shared'`` for the app account); ``profile_names`` to its display name."""
    groups: Dict[tuple, list] = {}
    rows = list(mirrors)
    for m in rows:
        key = (account_of.get(m.get('profile_id'), SHARED_ACCOUNT), _norm(_base_name(m)))
        groups.setdefault(key, []).append(m)

    out: Dict[Any, str] = {}
    for members in groups.values():
        members.sort(key=lambda m: int(m.get('id') or 0))
        pids = sorted({m.get('profile_id') for m in members}, key=lambda p: int(p or 0))
        keeper = ADMIN_PROFILE_ID if ADMIN_PROFILE_ID in pids else pids[0]
        seen_by_profile: Dict[Any, int] = {}
        for m in members:
            pid = m.get('profile_id')
            name = _base_name(m)
            if pid != keeper:
                name = f"{name} - {profile_names.get(pid) or f'Profile {pid}'}"
            nth = seen_by_profile.get(pid, 0)
            seen_by_profile[pid] = nth + 1
            if nth:
                name = f"{name} ({_source_label(m.get('source'))})"
            out[m.get('id')] = name

    # two same-source copies would still match: the id makes them distinct
    taken: Dict[tuple, Any] = {}
    for m in sorted(rows, key=lambda m: int(m.get('id') or 0)):
        acct = account_of.get(m.get('profile_id'), SHARED_ACCOUNT)
        key = (acct, _norm(out[m.get('id')]))
        if key in taken:
            out[m.get('id')] = f"{out[m.get('id')]} #{m.get('id')}"
        taken[(acct, _norm(out[m.get('id')]))] = m.get('id')
    return out


def server_account_of(db: Any, server: Optional[str], profile_id: Any) -> str:
    """the server account a profile's syncs run as: its own user, else the app account."""
    try:
        if server == 'navidrome':
            login = db.get_profile_navidrome_login(profile_id)
            if login and login[0]:
                return f"user:{_norm(login[0])}"
        elif server == 'plex':
            link = db.get_profile_plex_home_user(profile_id)
            if link and link.get('token'):
                return f"user:{_norm(link.get('title') or link.get('token'))}"
        elif server == 'jellyfin':
            user_id = (db.get_profile_server_library(profile_id) or {}).get('jellyfin_user_id')
            if user_id:
                return f"user:{_norm(user_id)}"
    except Exception as e:
        logger.debug("server account for profile %s: %s", profile_id, e)
    return SHARED_ACCOUNT


def all_sync_names(db: Any, server: Optional[str]) -> Dict[Any, str]:
    """every mirror's sync name on this server."""
    try:
        with db._get_connection() as conn:
            rows = [dict(zip(('id', 'profile_id', 'name', 'custom_name', 'source'), r, strict=True))
                    for r in conn.execute(
                        "SELECT id, COALESCE(profile_id, 1), name, custom_name, source "
                        "FROM mirrored_playlists").fetchall()]
    except Exception as e:
        logger.debug("sync names: mirrors unreadable: %s", e)
        return {}
    try:
        profile_names = {p.get('id'): p.get('name') for p in db.get_all_profiles() or []}
    except Exception:
        profile_names = {}
    pids = {r['profile_id'] for r in rows}
    account_of = {pid: server_account_of(db, server, pid) for pid in pids}
    return resolve_sync_names(rows, account_of, profile_names)


def sync_name_for(db: Any, server: Optional[str], mirror: Dict[str, Any]) -> str:
    """the one mirror's sync name; its plain name when anything goes wrong."""
    names = all_sync_names(db, server)
    return names.get(mirror.get('id')) or _base_name(mirror)


__all__ = ['resolve_sync_names', 'server_account_of', 'all_sync_names', 'sync_name_for']
