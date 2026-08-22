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


def _accepts_users(reader) -> bool:
    """Whether a client's ``get_curation_signals`` takes per-user credentials.
    Jellyfin reads every user with one admin key and has no such parameter."""
    try:
        import inspect

        return 'users' in inspect.signature(reader).parameters
    except (TypeError, ValueError):
        return False


def _accepts_status(marker) -> bool:
    """Whether the database adapter takes the structured sweep record."""
    if not callable(marker):
        return False
    try:
        import inspect

        return bool(inspect.signature(marker).parameters)
    except (TypeError, ValueError):
        return False


def normalize_signals(raw: Dict[str, List[Dict[str, Any]]]) -> Dict[str, List[Dict[str, Any]]]:
    """Turn a client's raw ``{user: [{path, ...}]}`` into storage rows keyed by
    ``track_key``. Rows with no usable path are dropped; a user whose rows all
    drop out still yields an empty list, which the caller treats as "read
    successfully, likes nothing" — distinct from "could not read"."""
    out: Dict[str, List[Dict[str, Any]]] = {}
    for user, rows in (raw or {}).items():
        cleaned = []
        offered = 0
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            offered += 1
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
        if offered and not cleaned:
            # The server returned rows for this user and NONE of them carried a
            # usable path — e.g. Jellyfin omitting Path because the key lacks
            # the rights, or a server shape we don't parse. Storing that as an
            # empty set would read as "they like nothing" and withdraw
            # protection from everything they starred. Treat it as a failed
            # read and leave their stored signals alone.
            logger.warning("curation sync: %s returned %d row(s) with no usable "
                           "path — leaving their stored signals untouched",
                           user, offered)
            continue
        out[str(user)] = cleaned
    return out


#: How long stored signals stay good enough to skip a sweep. Retention windows
#: are measured in weeks, so half-hourly freshness buys nothing and costs a
#: per-user server scan every poll. Comfortably inside the cleaner's 48h
#: staleness limit, so a couple of missed sweeps still cannot strand it.
SWEEP_INTERVAL_HOURS = 6.0


def curation_sweep_due(config_manager, db, now=None) -> bool:
    """Whether a curation sweep should run at all right now.

    Two separate questions, and the first one matters most: **is anyone using
    this?** The Expired Download Cleaner is an opt-in repair job that ships
    disabled, but the sweep rides the listening-stats poll, which runs on
    virtually every install. Without this gate every user would pay per-user
    server scans every 30 minutes to feed a feature they never turned on.

    Then: is the stored data still fresh enough? Signals older than
    ``SWEEP_INTERVAL_HOURS`` get refreshed; anything newer is left alone.
    """
    if config_manager is None:
        return False
    try:
        # Defaults MUST match how the repair worker reads the same keys
        # (core/repair_worker.py:366 and :456): master_enabled defaults ON,
        # the job's own enabled falls back to its default_enabled (False for
        # this job), and settings fall back to its default_settings. Getting
        # master_enabled's default wrong the other way would leave the sweep
        # silently never running on installs that simply have no such key,
        # while the job itself happily ran.
        if not config_manager.get('repair.master_enabled', True):
            return False
        job = config_manager.get('repair.jobs.expired_download_cleaner', {})
        if not isinstance(job, dict) or not job.get('enabled', False):
            return False
        settings = job.get('settings') or {}
        if not isinstance(settings, dict):
            settings = {}
        if not settings.get('use_curation_signals', True):
            return False
    except Exception as e:
        logger.debug("curation sweep gate: config unreadable (%s) — not sweeping", e)
        return False

    try:
        from datetime import datetime, timezone

        from core.library.expired_cleanup import parse_ts
        last = parse_ts(db.get_curation_sync_at())
        if last is None:
            return True  # never swept and the feature is on — do it now
        now = now or datetime.now(timezone.utc)
        return (now - last).total_seconds() / 3600.0 >= SWEEP_INTERVAL_HOURS
    except Exception as e:
        logger.debug("curation sweep gate: stamp unreadable (%s) — sweeping", e)
        return True


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
    ``{servers, users, rows, stamped, complete, failed}``.

    The stamp is what unblocks deletion in the cleaner, so it records whether
    the sweep was COMPLETE — every server that supports curation read, and every
    one of its users stored. A partial sweep used to stamp itself fresh anyway
    (L2-001): Bob's write failing while Alice's succeeded produced a snapshot
    that said "Bob likes nothing", and the cleaner deleted on it. Everything that
    goes wrong now lands in ``failed`` and marks the record incomplete, which
    makes the cleaner keep everything until a clean sweep replaces it.
    """
    summary = {'servers': [], 'users': 0, 'rows': 0, 'stamped': False,
               'complete': True, 'failed': []}
    expected_servers = []

    for server, client in (clients or {}).items():
        if client is None:
            # The caller named this server as active, so it IS expected to
            # contribute; we simply could not reach it. Skipping silently would
            # record a "complete" sweep that quietly omits everyone on it.
            expected_servers.append(server)
            summary['failed'].append(server)
            summary['complete'] = False
            continue
        reader = getattr(client, 'get_curation_signals', None)
        if not callable(reader):
            # This server genuinely cannot report curation signals. Nothing is
            # missing from the snapshot because of it.
            continue
        # Servers whose per-user data needs per-user credentials (Subsonic)
        # take them here. Passed per CALL, never assigned onto the client:
        # mutating a shared singleton's identity is how one user's view leaks
        # into every other caller.
        accounts = (user_credentials or {}).get(server) or []
        # Ask by SIGNATURE, not by catching TypeError: a TypeError raised deep
        # inside a client's own logic would otherwise be mistaken for "doesn't
        # take credentials" and silently retried without them, hiding a real
        # bug and reading the wrong user's data.
        wants_users = accounts and _accepts_users(reader)
        expected_servers.append(server)
        try:
            raw = (reader(users=accounts) if wants_users else reader()) or {}
        except Exception as e:
            logger.warning("curation sync: %s failed, leaving its stored signals "
                           "untouched: %s", server, e)
            summary['failed'].append(server)
            summary['complete'] = False
            continue

        normalized = normalize_signals(raw)
        if not normalized:
            # A server that supports curation and returned no readable user is
            # not a server that told us nobody curates anything — it is a server
            # we failed to read.
            logger.warning("curation sync: %s returned no readable users", server)
            summary['failed'].append(server)
            summary['complete'] = False
            continue

        wrote_any = False
        for user, rows in normalized.items():
            try:
                stored = db.replace_curation_signals(server, user, rows)
            except Exception as e:
                logger.warning("curation sync: storing %s/%s failed: %s", server, user, e)
                summary['failed'].append(f"{server}/{user}")
                summary['complete'] = False
                continue
            summary['rows'] += stored
            summary['users'] += 1
            wrote_any = True
        if wrote_any:
            summary['servers'].append(server)

    if not expected_servers:
        # No configured server can report curation signals, so there is nothing
        # for this feature to protect here and nothing to be incomplete about.
        # Recording that explicitly is what lets the cleaner tell "curation
        # cannot work on this install" from "curation has never run yet".
        logger.info("curation sync: no configured server supports curation signals")

    status = {
        'complete': bool(summary['complete']),
        'expected_servers': sorted(expected_servers),
        'servers': sorted(summary['servers']),
        'failed': sorted(summary['failed']),
        'users': summary['users'],
        'rows': summary['rows'],
    }
    # Ask by SIGNATURE, same reasoning as _accepts_users above: a TypeError
    # from inside the adapter must not be mistaken for "this adapter is the old
    # one", which would silently stamp an incomplete sweep as a good one.
    structured = _accepts_status(getattr(db, 'mark_curation_sync', None))
    try:
        if structured:
            # The record is written even for a failed sweep: "we tried and it
            # did not work" is exactly what the cleaner has to know, and only
            # a complete sweep counts as stamped.
            db.mark_curation_sync(status)
            summary['stamped'] = bool(status['complete'])
        elif status['complete'] and summary['servers']:
            # An adapter predating the structured record cannot carry the
            # caveat, so only a clean sweep may stamp at all.
            db.mark_curation_sync()
            summary['stamped'] = True
    except Exception as e:
        logger.warning("curation sync: could not stamp completion: %s", e)
    if not summary['complete']:
        logger.warning("curation sync: incomplete (%s) — the cleaner keeps "
                       "everything until a clean sweep replaces this",
                       ", ".join(status['failed']) or "unknown")

    logger.info("curation sync: %d server(s), %d user(s), %d signal rows",
                len(summary['servers']), summary['users'], summary['rows'])
    return summary
