# Issue #1248: Navidrome playlist and library identity recovery

## Evidence

The supplied `Issue/app.log` records 2,901 library tracks before the September 13 incremental scan, then 1,889 new rows during post-scan reconciliation (line 31868). The subsequent count is 4,790. At 17:16, sync reports 67 matches out of 86 and submits 67 IDs in replace mode (lines 40102–40109). That success message only counted submitted IDs, while the user's screenshot showed seven server entries. The log does not include the full song-ID inventory, so it cannot prove which individual IDs were rejected.

Isolated reproductions demonstrated that a stale cached Navidrome ID was accepted at 100% confidence, that reconciliation could remove a valid current entry in favor of that obsolete ID, and that inserting a new server ID for an existing path left both rows counted. The underlying identity-handling code predates 3.4.1.

## Changes

- Every Navidrome create, replace, reconcile and append operation validates its requested IDs. IDs present in a freshly read playlist need no full inventory. Other IDs use a fresh paginated inventory, with bounded network requests and scan-state checks. Failed, changing, malformed or repeated pages abort validation.
- Obsolete IDs can resolve to exactly one live song at the same normalized path, corroborated by title and available duration. Ambiguous or conflicting evidence refuses the write. There is no fuzzy substitution in this recovery path.
- Verify playlist membership after a successful response. A failed or unverifiable write reports sync failure, does not fall back to another destructive replacement, and skips that run's wishlist processing. Manual add, replace and remove endpoints propagate write failures.
- Incremental scans, including no-new-content scans, repair confirmed same-path duplicate IDs. The transaction preserves the live row, fills missing enrichment, redirects manual matches, cached matches and declared track references, then removes the obsolete row. It does not delete audio files. Deep scans perform this repair before stale-row deletion and skip stale deletion when repair fails.
- Repair only fetches an inventory if local duplicate paths exist. An inventory covers the whole account-visible server so changing the selected folder cannot make other live IDs look obsolete.
- Navidrome cover URLs retain the cover ID and use the existing stable local proxy. Library JSON serialization no longer regenerates salted URLs and registers each such cover in the image cache. This fixes a reproducible artwork path; the exact cause of every 170-second request in the user's environment cannot be established from request durations alone.

## Recovery after deployment

Run the normal Navidrome-to-SoulSync database update, then sync the affected playlists. Confirmed duplicate rows are repaired during the update. If identity recovery is ambiguous, the playlist write fails rather than silently removing valid entries; inspect the log and use a verified library scan or a manual match to resolve that case. No database reset is required by this fix.

## Validation and limits

The expanded affected backend suite passed 199 tests before the final healthy-resync optimization; the final focused rerun passed 51 tests, including that optimization. Repository-wide Ruff and diff checks passed. An isolated 1,889-row repair preserved every source ID and merged every obsolete row (0.027 seconds in in-memory SQLite; not a production timing guarantee). Tests use disposable databases and a stateful fake Navidrome server, not the reporter's installation. Guards cover partial inventory failures, scan activity, missing/ambiguous IDs, mismatched metadata, server responses that silently omit songs, reference preservation and avoiding replacement fallback.

No client can make a remote server read and write atomic. Post-write verification detects a server-side change or omission; it does not claim to undo concurrent edits. A very slow full inventory aborts safely rather than allowing an unverified write. Full repository CI and a live Navidrome deployment have not been run here.

Inventory paging follows the [OpenSubsonic empty-query search3 clarification](https://github.com/opensubsonic/open-subsonic-api/discussions/4), which lists Navidrome support.
