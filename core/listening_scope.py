"""whose listening history a profile reads and writes (#1293).

one table, a few piles. every play lands in the shared pile (owner 1, the
admin's) unless the profile has its own listenbrainz or last.fm connected. then
that history and its web-player plays are its own pile, and its stats,
recently played and discovery read only that.

a profile without either keeps reading and writing the shared pile, same as
before this existed. nobody's page goes empty on update.

the admin always reads the shared pile. the media-server poll, the global
last.fm/listenbrainz imports and the scrobbler all live there, and a navidrome
admin can't see anyone else's plays anyway, which is the whole reason for this.
"""

from __future__ import annotations

from typing import List, Optional

from utils.logging_config import get_logger

logger = get_logger("listening_scope")

SHARED_OWNER = 1
# plays by a media-server account no profile is linked to: kept, in nobody's
# pile, until a profile links that account and claims them
UNCLAIMED = 0

# the accounts that make a pile a profile's own. listenbrainz is a token,
# last.fm is just a username (its scrobbles are public, the app's key reads them)
ACCOUNT_COLUMNS = {
    'listenbrainz': 'listenbrainz_token',
    'lastfm': 'lastfm_username',
}
_HAS_ACCOUNT = " OR ".join(
    f"({col} IS NOT NULL AND {col} != '')" for col in ACCOUNT_COLUMNS.values()
)

# a profile linked to its own media-server account owns a pile too: the
# server's history says who played what, so its plays are its own (a kid's
# katy perry stays out of the admin's stats and mixes)
MEDIA_ACCOUNT_COLUMNS = ('plex_account_id', 'plex_home_user_id')


def _owns_pile_sql(conn) -> str:
    """the "this profile has its own pile" condition, for the columns this
    schema has (an old one may not have the plex ones yet)"""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(profiles)").fetchall()}
    parts = [_HAS_ACCOUNT]
    parts += [f"({c} IS NOT NULL AND {c} != '')" for c in MEDIA_ACCOUNT_COLUMNS if c in cols]
    return " OR ".join(parts)


def listening_owner(database, profile_id: Optional[int]) -> int:
    """the pile a profile reads and writes. SHARED_OWNER unless it has its own
    listenbrainz or last.fm. no profile (a background job, a test) means the
    shared pile, never everyone's."""
    try:
        pid = int(profile_id) if profile_id is not None else SHARED_OWNER
    except (TypeError, ValueError):
        return SHARED_OWNER
    if pid == SHARED_OWNER:
        return SHARED_OWNER
    conn = None
    try:
        conn = database._get_connection()
        row = conn.execute(
            f"SELECT 1 FROM profiles WHERE id = ? AND ({_owns_pile_sql(conn)})", (pid,),
        ).fetchone()
        return pid if row else SHARED_OWNER
    except Exception as e:
        # can't tell, so read the shared pile. that's what everyone read before.
        logger.debug("listening owner lookup failed for profile %s: %s", pid, e)
        return SHARED_OWNER
    finally:
        if conn is not None:
            conn.close()


def listening_owners(database) -> List[int]:
    """every pile that exists: the shared one plus each profile with its own."""
    owners = [SHARED_OWNER]
    conn = None
    try:
        conn = database._get_connection()
        rows = conn.execute(
            f"SELECT id FROM profiles WHERE ({_owns_pile_sql(conn)}) AND id != ? ORDER BY id",
            (SHARED_OWNER,),
        ).fetchall()
        owners.extend(int(r[0]) for r in rows)
    except Exception as e:
        logger.debug("listening owners lookup failed: %s", e)
    finally:
        if conn is not None:
            conn.close()
    return owners


def profiles_with_account(database, service: str) -> List[int]:
    """the profiles (never the admin) with their own account on one service.
    each importer only runs for these, a last.fm-only pile has no listenbrainz
    to import."""
    col = ACCOUNT_COLUMNS[service]
    conn = None
    try:
        conn = database._get_connection()
        rows = conn.execute(
            f"SELECT id FROM profiles WHERE {col} IS NOT NULL AND {col} != '' AND id != ? ORDER BY id",
            (SHARED_OWNER,),
        ).fetchall()
        return [int(r[0]) for r in rows]
    except Exception as e:
        logger.debug("%s profiles lookup failed: %s", service, e)
        return []
    finally:
        if conn is not None:
            conn.close()


def has_own_account(database, profile_id: Optional[int], service: str) -> bool:
    try:
        pid = int(profile_id)
    except (TypeError, ValueError):
        return False
    return pid != SHARED_OWNER and pid in profiles_with_account(database, service)


def owner_clause(owner: Optional[int], alias: str = "") -> str:
    """sql for "this pile only". the unary + keeps sqlite off any index on the
    column, the owner filter is never selective enough to drive a query and
    letting it pick one is how #1199's searches went 2ms to 18s."""
    try:
        owner_i = int(owner) if owner is not None else SHARED_OWNER
    except (TypeError, ValueError):
        owner_i = SHARED_OWNER
    prefix = f"{alias}." if alias else ""
    return f"+{prefix}profile_id = {owner_i}"


def play_duration_sql(alias: str = "") -> str:
    """sql for how long one play lasted, in ms.

    only web-player plays carry a duration. plex, last.fm and listenbrainz
    plays come in without one, so 689 plays summed to 45 minutes. those fall
    back to the length of the library track the play is linked to. Ours: that
    link is ``lib2_track_id`` (``db_track_id`` is the media server's id), a
    primary-key lookup on lib2_tracks.
    """
    col = f"{alias}." if alias else "listening_history."
    return (f"(CASE WHEN {col}duration_ms > 0 THEN {col}duration_ms "
            f"ELSE COALESCE((SELECT _pt.duration FROM lib2_tracks _pt "
            f"WHERE _pt.id = {col}lib2_track_id), 0) END)")


def owner_key(base: str, owner: Optional[int]) -> str:
    """a metadata key per pile. the shared pile keeps the old bare key so every
    cache and import state written before this still reads."""
    try:
        owner_i = int(owner) if owner is not None else SHARED_OWNER
    except (TypeError, ValueError):
        owner_i = SHARED_OWNER
    return base if owner_i == SHARED_OWNER else f"{base}_p{owner_i}"


# every metadata key a pile owns. owner_key() suffixes each for a profile's own
# pile. keep this in step with the stats cache and the listenbrainz importer.
STATS_CACHE_RANGES = ('7d', '30d', '12m', 'all')
PILE_KEY_BASES = (
    *(f'stats_cache_{r}' for r in STATS_CACHE_RANGES),
    'stats_cache_recent',
    'stats_cache_year',
    'listenbrainz_listening_import_state',
    'lastfm_listening_import_state',
)


def pile_cache_keys(owner: Optional[int]) -> List[str]:
    """just the stats caches of a profile's pile, not any importer's state."""
    return [k for k in pile_keys(owner) if not k.startswith(IMPORT_STATE_PREFIXES)]


IMPORT_STATE_PREFIXES = ('listenbrainz_listening_import_state', 'lastfm_listening_import_state')


def pile_keys(owner: Optional[int]) -> List[str]:
    """a profile pile's metadata keys, for wiping it. never the shared pile's."""
    try:
        owner_i = int(owner)
    except (TypeError, ValueError):
        return []
    if owner_i == SHARED_OWNER:
        return []
    return [owner_key(base, owner_i) for base in PILE_KEY_BASES]


# -- plays by media-server account (#plex per-user history) ------------------

# plex reports the server owner's own plays as account 1, everyone else under
# their plex.tv user id (checked against a live server, Oct 2026)
PLEX_OWNER_ACCOUNT = '1'


def media_account_owners(database, server_source: str) -> dict:
    """{server account id: pile} for every profile linked to an account on
    this server. plex only for now; the owner's account is the shared pile"""
    if server_source != 'plex':
        return {}
    owners = {PLEX_OWNER_ACCOUNT: SHARED_OWNER}
    conn = None
    try:
        conn = database._get_connection()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(profiles)").fetchall()}
        # sign-in's own record first, then the home-user link; lowest id wins
        # a tie, so two profiles on one account can't split its plays
        for col in [c for c in MEDIA_ACCOUNT_COLUMNS if c in cols]:
            for pid, account in conn.execute(
                    f"SELECT id, {col} FROM profiles WHERE {col} IS NOT NULL AND {col} != '' "
                    f"AND id != ? ORDER BY id DESC", (SHARED_OWNER,)).fetchall():
                owners.setdefault(str(account), int(pid))
    except Exception as e:
        logger.debug("media account owners lookup failed: %s", e)
    finally:
        if conn is not None:
            conn.close()
    return owners


def media_account_owner(owners: dict, account_id) -> int:
    """the pile a play by this server account belongs to. no account (a
    server that doesn't say, an old row) = the shared pile, as before"""
    if account_id is None or account_id == '':
        return SHARED_OWNER
    return owners.get(str(account_id), UNCLAIMED)


def reattribute_media_plays(database, server_source: str = 'plex') -> int:
    """re-file every play whose account now belongs to a different pile: a
    profile linked (or unlinked) since, or rows from before accounts were
    kept. returns how many plays moved. the same play already sitting in the
    target pile wins and the stray copy goes"""
    owners = media_account_owners(database, server_source)
    if not owners:
        return 0
    conn = None
    moved = 0
    try:
        conn = database._get_connection()
        accounts = [r[0] for r in conn.execute(
            "SELECT DISTINCT server_account_id FROM listening_history "
            "WHERE server_source = ? AND server_account_id IS NOT NULL", (server_source,)).fetchall()]
        for account in accounts:
            pile = media_account_owner(owners, account)
            cur = conn.execute(
                "UPDATE OR IGNORE listening_history SET profile_id = ? "
                "WHERE server_source = ? AND server_account_id = ? AND profile_id != ?",
                (pile, server_source, account, pile))
            moved += cur.rowcount
            # anything left in the wrong pile collided with the same play
            # already filed right: a duplicate
            conn.execute(
                "DELETE FROM listening_history "
                "WHERE server_source = ? AND server_account_id = ? AND profile_id != ?",
                (server_source, account, pile))
        conn.commit()
    except Exception as e:
        logger.error("re-filing %s plays by account failed: %s", server_source, e)
        if conn is not None:
            conn.rollback()
        return 0
    finally:
        if conn is not None:
            conn.close()
    if moved:
        logger.info("re-filed %d %s plays into their accounts' piles", moved, server_source)
    return moved
