"""Exact case redirects after verified inventory relocation."""

def resolve_case(db, key: str) -> str:
    conn = db._get_connection()
    try:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'reconcile_case_redirects'").fetchone():
            return key
        seen = set()
        while key not in seen:
            seen.add(key)
            row = conn.execute('SELECT new_case_id FROM reconcile_case_redirects WHERE old_case_id = ?', (key,)).fetchone()
            if not row:
                break
            key = row[0]
        return key
    finally:
        conn.close()


def record_relocation(db, old_folder: str, new_case: str) -> None:
    conn = db._get_connection()
    try:
        conn.execute('CREATE TABLE IF NOT EXISTS reconcile_case_redirects (old_case_id TEXT PRIMARY KEY, new_case_id TEXT NOT NULL)')
        target = conn.execute('SELECT album_id, album_folder, source, release_id, profile_id FROM reconcile_album_plans WHERE case_id=?', (new_case,)).fetchone()
        if not target:
            return
        from core.reconciliation.decisions import rebind_review
        previous_keys = [row[0] for row in conn.execute(
            'SELECT case_id FROM reconcile_album_plans WHERE album_folder=? AND case_id!=? AND profile_id=?',
            (old_folder, new_case, target[4]))]
        for old_case in previous_keys:
            rebind_review(conn, old_case, new_case, target[0])
        conn.execute('INSERT OR REPLACE INTO reconcile_case_redirects SELECT case_id, ? FROM reconcile_album_plans WHERE album_folder = ? AND case_id != ? AND profile_id=?', (new_case, old_folder, new_case, target[4]))
        if target and conn.execute("SELECT 1 FROM sqlite_master WHERE name='reconcile_fulfillment'").fetchone():
            from core.reconciliation.fulfillment import ensure_schema
            ensure_schema(conn)
            # Rebind exact acquisition targets only when the selected release
            # survives the move. A link redirect alone cannot settle a download.
            conn.execute('''INSERT INTO reconcile_fulfillment
                (case_id,profile_id,album_id,album_folder,source,release_id,source_track_id,state,published_path,refresh_needed,updated_at,replacement_request)
                SELECT ?,profile_id,?,?,source,release_id,source_track_id,state,published_path,1,updated_at,replacement_request
                FROM reconcile_fulfillment WHERE album_folder=? AND source=? AND release_id=? AND case_id!=? AND profile_id=?
                ON CONFLICT(case_id,source,release_id,source_track_id) DO UPDATE SET
                    state=excluded.state, published_path=excluded.published_path,
                    replacement_request=excluded.replacement_request,
                    refresh_needed=1, updated_at=excluded.updated_at
                WHERE excluded.updated_at > reconcile_fulfillment.updated_at
                   OR (excluded.updated_at = reconcile_fulfillment.updated_at
                       AND reconcile_fulfillment.state IN ('requested','untracked')
                       AND excluded.state IN ('published','fulfilled','cancelled'))''',
                (new_case, str(target[0]), target[1], old_folder, target[2], target[3], new_case, target[4]))
            conn.execute('''DELETE FROM reconcile_fulfillment WHERE album_folder=? AND source=? AND release_id=? AND case_id!=? AND profile_id=?''',
                         (old_folder, target[2], target[3], new_case, target[4]))
        conn.commit()
    finally:
        conn.close()


def record_observed_relocations(db, observed: set[str]) -> None:
    """Keep old links valid when a full sweep discovers an earlier file move."""
    import json
    conn = db._get_connection()
    try:
        rows = conn.execute('SELECT case_id, album_folder, profile_id, plan_json FROM reconcile_album_plans').fetchall()
    finally:
        conn.close()
    memberships = [(key, folder, profile, set((json.loads(raw or '{}').get('inventory') or {}).get('track_ids') or []))
                   for key, folder, profile, raw in rows]
    current = [row for row in memberships if row[0] in observed]
    for key, folder, profile, ids in memberships:
        if key in observed or not ids:
            continue
        matches = [new_key for new_key, _, new_profile, new_ids in current
                   if new_profile == profile and ids.issubset(new_ids)]
        if len(matches) == 1:
            record_relocation(db, folder, matches[0])
