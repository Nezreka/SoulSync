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
        conn.execute('INSERT OR REPLACE INTO reconcile_case_redirects SELECT case_id, ? FROM reconcile_album_plans WHERE album_folder = ? AND case_id != ?', (new_case, old_folder, new_case))
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
