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

# the accounts that make a pile a profile's own. listenbrainz is a token,
# last.fm is just a username (its scrobbles are public, the app's key reads them)
ACCOUNT_COLUMNS = {
    'listenbrainz': 'listenbrainz_token',
    'lastfm': 'lastfm_username',
}
_HAS_ACCOUNT = " OR ".join(
    f"({col} IS NOT NULL AND {col} != '')" for col in ACCOUNT_COLUMNS.values()
)


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
            f"SELECT 1 FROM profiles WHERE id = ? AND ({_HAS_ACCOUNT})", (pid,),
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
            f"SELECT id FROM profiles WHERE ({_HAS_ACCOUNT}) AND id != ? ORDER BY id",
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
