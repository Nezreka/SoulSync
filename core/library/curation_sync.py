"""Collect per-user curation signals from the media servers.

What people deliberately CHOSE about a track — favourited it, rated it, put it
in a playlist — as opposed to ``play_count``, which only records what happened.
The Expired Download Cleaner uses these to decide what it must never delete
(Cremonies).

Deliberately thin and free of Flask/global state so it can be unit-tested with
stub clients. Each media-server client optionally implements
``get_curation_signals()`` returning ``{user: [{path, favorite, rating,
in_playlist}, ...]}``; a client that doesn't implement it simply contributes
nothing, exactly like ``get_track_play_counts``.

The whole point of this data is to STOP deletions, so the failure policy is
strict: a user we could not read is left untouched rather than stored as empty,
and the sync is only stamped as successful when at least one user's signals
were actually written. A sweep that silently returns nothing must not refresh
the stamp, because a fresh-but-empty stamp tells the cleaner "nobody likes
anything" — which is precisely the state that deletes a library.
"""

from __future__ import annotations

from typing import Any, Dict, List

from core.library.expired_cleanup import path_suffix_key
from utils.logging_config import get_logger

logger = get_logger("library.curation_sync")


def normalize_signals(raw: Dict[str, List[Dict[str, Any]]]) -> Dict[str, List[Dict[str, Any]]]:
    """Turn a client's raw ``{user: [{path, ...}]}`` into storage rows keyed by
    ``track_key``. Rows with no usable path are dropped; a user whose rows all
    drop out still yields an empty list, which the caller treats as "read
    successfully, likes nothing" — distinct from "could not read"."""
    out: Dict[str, List[Dict[str, Any]]] = {}
    for user, rows in (raw or {}).items():
        cleaned = []
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            key = path_suffix_key(row.get('path'))
            if not key:
                continue
            cleaned.append({
                'track_key': key,
                'favorite': bool(row.get('favorite')),
                'rating': row.get('rating'),
                'in_playlist': bool(row.get('in_playlist')),
                'source_path': row.get('path'),
            })
        out[str(user)] = cleaned
    return out


def navidrome_user_credentials(db) -> List[tuple]:
    """Every saved Navidrome account, as ``(username, password)`` pairs.

    Subsonic scopes starred items and ratings to the AUTHENTICATED user and
    offers no admin impersonation for them, so "keep it if ANYONE starred it"
    genuinely requires each user's own credentials — which is what Cremonies
    said. They live in the existing named-credential store (its schema has
    covered navidrome all along; nothing read it until now).

    Returns [] on any failure, which makes the caller fall back to the single
    configured account rather than reading nothing.
    """
    try:
        sets = db.list_service_credentials('navidrome') or []
    except Exception as e:
        logger.debug("curation sync: could not list navidrome credentials: %s", e)
        return []

    pairs = []
    for meta in sets:
        try:
            full = db.get_service_credential(meta.get('id'))
        except Exception as e:
            logger.debug("curation sync: credential %s unreadable: %s", meta.get('id'), e)
            continue
        payload = (full or {}).get('payload') or {}
        username = payload.get('username')
        password = payload.get('password')
        if username and password:
            pairs.append((username, password))
    return pairs


def sync_curation_signals(db, clients: Dict[str, Any],
                          user_credentials: Dict[str, List[tuple]] = None) -> Dict[str, Any]:
    """Read every configured server's curation signals and store them.

    ``clients`` maps server name → client (or None). Returns a summary dict:
    ``{servers, users, rows, stamped}``.

    The stamp is what unblocks deletion in the cleaner, so it is only written
    when at least one user was genuinely read. Everything else — a server that
    is down, a client without the method, an exception mid-sweep — leaves the
    previous stamp in place, which ages out and blocks deletion by itself.
    """
    summary = {'servers': [], 'users': 0, 'rows': 0, 'stamped': False}

    for server, client in (clients or {}).items():
        if client is None:
            continue
        reader = getattr(client, 'get_curation_signals', None)
        if not callable(reader):
            continue  # server doesn't support it — contributes nothing
        # Servers whose per-user data needs per-user credentials (Subsonic)
        # take them here. Passed per CALL, never assigned onto the client:
        # mutating a shared singleton's identity is how one user's view leaks
        # into every other caller.
        accounts = (user_credentials or {}).get(server) or []
        try:
            raw = (reader(users=accounts) if accounts else reader()) or {}
        except TypeError:
            # Client doesn't accept per-user credentials (Jellyfin reads every
            # user with one admin key, so it has no need for them).
            raw = reader() or {}
        except Exception as e:
            logger.warning("curation sync: %s failed, leaving its stored signals "
                           "untouched: %s", server, e)
            continue

        normalized = normalize_signals(raw)
        if not normalized:
            logger.info("curation sync: %s returned no readable users", server)
            continue

        wrote_any = False
        for user, rows in normalized.items():
            try:
                stored = db.replace_curation_signals(server, user, rows)
            except Exception as e:
                logger.warning("curation sync: storing %s/%s failed: %s", server, user, e)
                continue
            summary['rows'] += stored
            summary['users'] += 1
            wrote_any = True
        if wrote_any:
            summary['servers'].append(server)

    if summary['servers']:
        try:
            db.mark_curation_sync()
            summary['stamped'] = True
        except Exception as e:
            logger.warning("curation sync: could not stamp completion: %s", e)
    else:
        logger.warning("curation sync: nothing was read from any server — the "
                       "previous stamp stands and will age out, which keeps "
                       "everything rather than deleting it")

    logger.info("curation sync: %d server(s), %d user(s), %d signal rows",
                len(summary['servers']), summary['users'], summary['rows'])
    return summary
