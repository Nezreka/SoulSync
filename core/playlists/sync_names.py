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
* once a mirror has synced it keeps the name it synced under, so a delete
  never moves another mirror onto its server playlist.

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
}


def _norm(name: Any) -> str:
    return str(name or '').strip().lower()


def _base_name(mirror: Dict[str, Any]) -> str:
    from core.playlists.naming import effective_mirrored_name
    return effective_mirrored_name(mirror) or str(mirror.get('name') or 'Playlist')


def _source_label(source: Any) -> str:
    s = str(source or '').strip()
    return _SOURCE_LABELS.get(s.lower(), s.replace('_', ' ').title() or 'Other')


def _made_from(account: str, mirror: Dict[str, Any]) -> str:
    """what a kept name stays valid for: the same server account and own name."""
    return f"{account}|{_norm(_base_name(mirror))}"


def resolve_sync_names(mirrors: Iterable[Dict[str, Any]], account_of: Dict[Any, str],
                       profile_names: Dict[Any, str]) -> Dict[Any, str]:
    """{mirror id: the name it syncs under}. pure.

    ``account_of`` maps a profile id to the server account it syncs as
    (``'shared'`` for the app account); ``profile_names`` to its display name.

    a mirror that has synced keeps the name it synced under (``server_sync_name``)
    while its own name and server account are unchanged, so deleting one of two
    same-named mirrors doesn't move the other onto the deleted one's server
    playlist, and a mirror made again later gets the freed name back."""
    rows = sorted(mirrors, key=lambda m: int(m.get('id') or 0))
    acct = {m.get('id'): account_of.get(m.get('profile_id'), SHARED_ACCOUNT) for m in rows}

    out: Dict[Any, str] = {}
    taken: set = set()
    for m in rows:
        kept = str(m.get('server_sync_name') or '').strip()
        if kept and m.get('server_sync_base') == _made_from(acct[m.get('id')], m):
            key = (acct[m.get('id')], _norm(kept))
            if key not in taken:
                out[m.get('id')] = kept
                taken.add(key)

    # who keeps the plain name in each clash: the admin, else the lowest profile id
    pids_by_group: Dict[tuple, set] = {}
    for m in rows:
        pids_by_group.setdefault((acct[m.get('id')], _norm(_base_name(m))), set()).add(m.get('profile_id'))

    for m in rows:
        if m.get('id') in out:
            continue
        pid = m.get('profile_id')
        base = _base_name(m)
        pids = sorted(pids_by_group[(acct[m.get('id')], _norm(base))], key=lambda p: int(p or 0))
        if (ADMIN_PROFILE_ID if ADMIN_PROFILE_ID in pids else pids[0]) != pid:
            base = f"{base} - {profile_names.get(pid) or f'Profile {pid}'}"
        # a second source on one profile gets the source; two same-source copies the id
        for name in (base, f"{base} ({_source_label(m.get('source'))})", f"{base} #{m.get('id')}"):
            if (acct[m.get('id')], _norm(name)) not in taken:
                break
        out[m.get('id')] = name
        taken.add((acct[m.get('id')], _norm(name)))
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
            rows = [dict(zip(('id', 'profile_id', 'name', 'custom_name', 'source',
                              'server_sync_name', 'server_sync_base'), r, strict=True))
                    for r in conn.execute(
                        "SELECT id, COALESCE(profile_id, 1), name, custom_name, source, "
                        "server_sync_name, server_sync_base FROM mirrored_playlists").fetchall()]
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


def claim_sync_name(db: Any, server: Optional[str], mirror: Dict[str, Any]) -> str:
    """the name a sync of this mirror writes on the server, remembered so it
    stays this mirror's name. every mirror sync goes through here."""
    name = sync_name_for(db, server, mirror)
    if mirror.get('id') is not None:
        try:
            account = server_account_of(db, server, mirror.get('profile_id') or ADMIN_PROFILE_ID)
            db.set_mirrored_playlist_sync_name(mirror.get('id'), name, _made_from(account, mirror))
        except Exception as e:
            logger.debug("sync name for mirror %s not saved: %s", mirror.get('id'), e)
    return name


__all__ = ['resolve_sync_names', 'server_account_of', 'all_sync_names', 'sync_name_for',
           'claim_sync_name']
