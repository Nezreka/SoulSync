"""Atomic event ingestion with durable, one-to-one source provenance."""
from datetime import datetime, timezone

CROSS_SOURCE_TOLERANCE_SECONDS = 10


def _text(value):
    return str(value or "").strip().casefold()


def _timestamp(value):
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def insert_import_events(database, events, source):
    """Keep repeats; link only exact same-source or close cross-source events.

    An alias records each source's original timestamp even when no new history
    row is needed. UNIQUE(history_id, source) prevents a canonical play from
    consuming more than one event from that source, including on later runs.
    """
    incoming = {}
    for event in events:
        if not event.get("title") or not event.get("played_at"):
            continue
        key = (_text(event["title"]), _text(event.get("artist")), _timestamp(event["played_at"]))
        incoming.setdefault(key, event)
    if not incoming:
        return 0
    conn = database._get_connection()
    try:
        # Serialize lookup + matching + writes across both importer workers.
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS listening_import_events (
                source TEXT NOT NULL,
                title TEXT NOT NULL,
                artist TEXT NOT NULL,
                listened_at INTEGER NOT NULL,
                history_id INTEGER NOT NULL REFERENCES listening_history(id) ON DELETE CASCADE,
                PRIMARY KEY (source, title, artist, listened_at),
                UNIQUE (history_id, source)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_listening_import_source_time ON listening_import_events(source, listened_at)")
        low = min(key[2] for key in incoming) - CROSS_SOURCE_TOLERANCE_SECONDS
        high = max(key[2] for key in incoming) + CROSS_SOURCE_TOLERANCE_SECONDS
        # Clean only this window on connections without foreign-key enforcement.
        conn.execute("""
            DELETE FROM listening_import_events
            WHERE source = ? AND listened_at BETWEEN ? AND ?
              AND NOT EXISTS (SELECT 1 FROM listening_history WHERE id = history_id)
        """, (source, low, high))
        aliases = conn.execute("""
            SELECT title, artist, listened_at, history_id FROM listening_import_events
            WHERE source = ? AND listened_at BETWEEN ? AND ?
        """, (source, low, high)).fetchall()
        known = {(title, artist, ts): history_id for title, artist, ts, history_id in aliases}
        pending = {key: ev for key, ev in incoming.items() if key not in known}
        rows = conn.execute("""
            SELECT h.id, h.title, h.artist, h.album, h.played_at, h.server_source,
                   a.listened_at
            FROM listening_history h
            LEFT JOIN listening_import_events a ON a.history_id = h.id AND a.source = ?
            WHERE h.played_at >= datetime(?, 'unixepoch')
              AND h.played_at <= datetime(?, 'unixepoch')
        """, (source, low, high)).fetchall()
        candidates = []
        by_track = {}
        for key, event in pending.items():
            by_track.setdefault(key[:2], []).append((key, event))
        for history_id, title, artist, album, played_at, existing_source, alias_ts in rows:
            if alias_ts is not None:
                continue  # This row already represents a play from this source.
            name = (_text(title), _text(artist))
            timestamp = _timestamp(played_at)
            for key, event in by_track.get(name, []):
                delta = abs(key[2] - timestamp)
                if existing_source == source:
                    if delta != 0:
                        continue
                elif delta > CROSS_SOURCE_TOLERANCE_SECONDS:
                    continue
                elif not name[1]:
                    continue  # No artist: insufficient evidence across services.
                if existing_source != source and _text(album) and _text(event.get("album")):
                    if _text(album) != _text(event["album"]):
                        continue
                candidates.append((delta, key, history_id))
        # Reserve exact/closest pairs first, independently of input ordering.
        matches = {}
        used = set()
        for _, key, history_id in sorted(candidates):
            if key not in matches and history_id not in used:
                matches[key] = history_id
                used.add(history_id)
        inserted = 0
        for key, event in pending.items():
            history_id = matches.get(key)
            if history_id is None:
                cursor = conn.execute("""
                    INSERT OR IGNORE INTO listening_history
                        (track_id, title, artist, album, played_at, duration_ms, server_source, db_track_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (event.get("track_id"), event["title"], event.get("artist", ""),
                      event.get("album", ""), event["played_at"], event.get("duration_ms", 0),
                      source, event.get("db_track_id")))
                if cursor.rowcount:
                    inserted += 1
                    history_id = cursor.lastrowid
                else:
                    row = conn.execute("""
                        SELECT id FROM listening_history
                        WHERE track_id = ? AND played_at = ? AND server_source = ?
                    """, (event.get("track_id"), event["played_at"], source)).fetchone()
                    if row is None:
                        raise RuntimeError("Could not resolve existing listening event")
                    history_id = row[0]
            # A later server event can supply the library link missing from an import.
            if event.get("db_track_id") is not None:
                conn.execute("UPDATE listening_history SET db_track_id = COALESCE(db_track_id, ?) WHERE id = ?",
                             (event["db_track_id"], history_id))
            conn.execute("""
                INSERT OR IGNORE INTO listening_import_events
                    (source, title, artist, listened_at, history_id) VALUES (?, ?, ?, ?, ?)
            """, (source, *key, history_id))
            # A history read must not scrobble the same event back to its origin.
            if source in ("lastfm", "listenbrainz"):
                conn.execute(f"UPDATE listening_history SET scrobbled_{source} = 1 WHERE id = ?", (history_id,))
        conn.commit()
        return inserted
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
