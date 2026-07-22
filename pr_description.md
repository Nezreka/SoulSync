# soulsync 2.8.51 — `dev` → `main`

quick hotfix on top of 2.8.5 for one fresh-install bug.

---

## #983 — watchlist artist settings errored on a fresh install

on a brand-new database, opening a watchlist artist's settings could fail with `no such column: preferred_metadata_source` (restarting worked around it).

root cause: first-run setup rebuilds the `watchlist_artists` table (for profile-scoped uniqueness) by copying it from a hardcoded column list — and that list was missing two newer columns (`preferred_metadata_source`, `auto_download`) that get added by later ALTER migrations. on a fresh install the rebuild fires right after those columns are created, silently dropping them. the artist-config endpoint reads `preferred_metadata_source` directly, so it 500'd until a restart's ALTER re-added it. upgraders never hit it — their columns already existed before that rebuild ran.

fix: added both columns to every rebuild path so they survive. regression tests added that fail on the old code with the exact error and pass on the fix. same bug class as the earlier `amazon_artist_id` fix.
