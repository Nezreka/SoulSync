"""who may see and touch which playlist on the media server (#1414).

the server playlists page used the shared app-account client for everything,
so every profile listed every playlist on the server and could edit or delete
any of them. the rule now:

* the admin works as the app account and sees everything.
* a profile linked to its own server user (navidrome login, plex home user,
  jellyfin user; #1265) works through that user's connection and sees what the
  server shows that user. navidrome also lists other users' public playlists,
  so those are filtered out by owner.
* a profile with no server user of its own is on the shared app account, where
  the server cannot tell its playlists apart. it sees the playlists its own
  mirrors and syncs made, by name, and nothing else.

the decision is pure (``playlist_allowed``); the listing takes a client and a
database so it can be tested with stubs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, List, Optional, Set

from utils.logging_config import get_logger

logger = get_logger("sync.server_playlist_access")


@dataclass
class PlaylistScope:
    """what one profile may reach on the server."""
    client: Any
    is_admin: bool
    # the server user the profile acts as, or None on the shared app account
    acting_as: Optional[str] = None
    # shared-account profiles: the names their own mirrors and syncs write
    own_names: Set[str] = field(default_factory=set)

    @property
    def shared(self) -> bool:
        return not self.is_admin and not self.acting_as


def _norm(name: Any) -> str:
    return str(name or '').strip().lower()


def client_for_profile(server: str, base_client: Any, profile_id: Optional[int]) -> Any:
    """the profile's own server connection, or the shared client when it has none."""
    from services.sync_service import (
        jellyfin_client_for_profile,
        navidrome_client_for_profile,
        plex_client_for_profile,
    )
    pick = {
        'navidrome': navidrome_client_for_profile,
        'plex': plex_client_for_profile,
        'jellyfin': jellyfin_client_for_profile,
    }.get(server)
    if pick is None or base_client is None or not profile_id:
        return base_client
    return pick(profile_id, base_client)


def own_playlist_names(db: Any, profile_id: Optional[int], server: Optional[str] = None) -> Set[str]:
    """names the profile's mirrors and past syncs write on the server. a mirror
    that shares a name with another one syncs under a suffixed name, so it is
    the final sync name that counts here (core/playlists/sync_names)."""
    names: Set[str] = set()
    if not profile_id:
        return names
    try:
        from core.playlists.sync_names import all_sync_names
        final = all_sync_names(db, server)
        for pl in db.get_mirrored_playlists(profile_id) or []:
            n = _norm(final.get(pl.get('id')) or pl.get('custom_name') or pl.get('name'))
            if n:
                names.add(n)
    except Exception as e:
        logger.debug("own playlist names: mirrors unreadable for %s: %s", profile_id, e)
    try:
        with db._get_connection() as conn:
            rows = conn.execute(
                "SELECT DISTINCT playlist_name FROM sync_history WHERE profile_id = ?",
                (profile_id,)).fetchall()
        for row in rows:
            n = _norm(row[0])
            if n:
                names.add(n)
    except Exception as e:
        logger.debug("own playlist names: sync history unreadable for %s: %s", profile_id, e)
    return names


def scope_for(server: str, base_client: Any, db: Any, profile_id: Optional[int],
              is_admin: bool) -> PlaylistScope:
    if is_admin:
        return PlaylistScope(client=base_client, is_admin=True)
    client = client_for_profile(server, base_client, profile_id)
    acting_as = getattr(client, 'acting_as', None) if client is not base_client else None
    if acting_as:
        return PlaylistScope(client=client, is_admin=False, acting_as=str(acting_as))
    return PlaylistScope(client=base_client, is_admin=False,
                         own_names=own_playlist_names(db, profile_id, server))


def _owned_by_view(server: str, scope: PlaylistScope, playlist: Any) -> bool:
    """a playlist listed through the profile's own connection is theirs, except
    navidrome's public playlists of other users, which carry their owner."""
    if server != 'navidrome':
        return True
    owner = getattr(playlist, 'owner', None)
    return owner is None or _norm(owner) == _norm(scope.acting_as)


def list_playlists(server: str, client: Any) -> List[Any]:
    """the client's playlists, metadata only. the plex client's own listing
    loads every track of every playlist, far too slow for a page list or an
    access check, so plex reads the raw playlist headers instead."""
    if server == 'plex':
        from types import SimpleNamespace
        server_obj = getattr(client, 'server', None)
        if server_obj is None:
            return []
        return [SimpleNamespace(id=str(pl.ratingKey), title=pl.title, owner=None,
                                leaf_count=getattr(pl, 'leafCount', 0) or 0)
                for pl in server_obj.playlists()
                if getattr(pl, 'playlistType', None) == 'audio']
    return list(client.get_all_playlists() or [])


def visible_playlists(server: str, scope: PlaylistScope) -> List[Any]:
    """the playlists the profile may see, from the server."""
    try:
        listed = list_playlists(server, scope.client)
    except Exception as e:
        logger.warning("server playlists: listing failed for %s: %s", server, e)
        return []
    if scope.is_admin:
        return list(listed)
    if scope.acting_as:
        return [p for p in listed if _owned_by_view(server, scope, p)]
    return [p for p in listed if _norm(getattr(p, 'title', '')) in scope.own_names]


def resolve_allowed(server: str, scope: PlaylistScope, playlist_id: Any,
                    playlist_name: str) -> Optional[tuple]:
    """the (id, name) the profile may act on, or None.

    the endpoints act id-first with a name fallback, so the check must hand
    back the playlist it checked: a profile could otherwise pair another
    user's id with the name of one of its own playlists, pass on the name and
    then edit the other one by id. resolving from the profile's own listing
    also picks up the live id after plex/jellyfin recreate a playlist on edit.
    the admin gets what it asked for."""
    if scope.is_admin:
        return str(playlist_id or ''), str(playlist_name or '')
    visible = visible_playlists(server, scope)
    pid, pname = str(playlist_id or ''), _norm(playlist_name)
    by_id = [p for p in visible if pid and str(getattr(p, 'id', '')) == pid]
    hit = by_id or [p for p in visible if pname and _norm(getattr(p, 'title', '')) == pname]
    if not hit:
        return None
    return str(getattr(hit[0], 'id', '')), str(getattr(hit[0], 'title', ''))


def _profile_names(db: Any) -> dict:
    try:
        return {p.get('id'): p.get('name') or f"Profile {p.get('id')}" for p in db.get_all_profiles() or []}
    except Exception:
        return {}


def admin_split(server: str, base_client: Any, db: Any) -> tuple:
    """the admin's view: (its own playlists, server-admin groups, everyone else's groups).

    each group is ``{'owner', 'profile', 'playlists'}``: ``owner`` is the server
    user, ``profile`` the SoulSync profile linked to it (None when no profile is).
    navidrome lists every user's playlists to an admin with their owner. plex
    and jellyfin only show a user's playlists through that user, so there the
    groups are the profiles linked to a server user of their own (and the
    server-admin list is always empty).

    #1542: on navidrome, playlists owned by other server admins get their own
    section, above "everyone else". adminRole comes from Subsonic getUser,
    cached for an hour; when the lookup fails the owner stays in "everyone
    else" (current behavior preserved).
    """
    try:
        listed = list_playlists(server, base_client)
    except Exception as e:
        logger.warning("server playlists: admin listing failed for %s: %s", server, e)
        return [], [], []
    names = _profile_names(db)
    if server == 'navidrome':
        me = _norm(getattr(base_client, 'username', ''))
        mine = [p for p in listed if getattr(p, 'owner', None) is None or _norm(p.owner) == me]
        by_login = {}
        for pid, pname in names.items():
            try:
                login = db.get_profile_navidrome_login(pid)
            except Exception:
                login = None
            if login and login[0]:
                by_login[_norm(login[0])] = pname
        admin_groups: dict = {}
        other_groups: dict = {}
        is_admin = getattr(base_client, 'is_server_admin', None)
        for p in listed:
            owner = getattr(p, 'owner', None)
            if owner is None or _norm(owner) == me:
                continue
            target = other_groups
            if callable(is_admin):
                try:
                    if is_admin(owner):
                        target = admin_groups
                except Exception as e:  # noqa: BLE001 - lookup failed: keep current grouping
                    logger.debug("server admin lookup failed for %r: %s", owner, e)
            g = target.setdefault(_norm(owner), {'owner': str(owner),
                                                'profile': by_login.get(_norm(owner)),
                                                'playlists': []})
            g['playlists'].append(p)
        return mine, list(admin_groups.values()), list(other_groups.values())
    groups = []
    for pid, pname in names.items():
        view = client_for_profile(server, base_client, pid)
        acting_as = getattr(view, 'acting_as', None) if view is not base_client else None
        if not acting_as:
            continue
        try:
            theirs = list_playlists(server, view)
        except Exception as e:
            logger.warning("server playlists: listing as %s failed: %s", pname, e)
            continue
        if theirs:
            groups.append({'owner': str(acting_as), 'profile': pname, 'playlists': theirs})
    return list(listed), [], groups


__all__ = [
    'PlaylistScope',
    'client_for_profile',
    'own_playlist_names',
    'scope_for',
    'visible_playlists',
    'resolve_allowed',
    'admin_split',
    'list_playlists',
]
