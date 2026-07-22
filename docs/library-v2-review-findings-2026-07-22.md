# Library V2 — Review-Findings (2026-07-22)

The branch contains multiple production-blocking integrity and availability defects: V2 file state can diverge from disk, acquisition imports can execute twice, bootstrap can lock or exhaust the host, and Enrich can persist incorrect identities. Alias-scope gaps, silent outbox failures, and request-amplifying queries further make the migration unsafe for production.

## Remediation status

This document is also the implementation log for the review. Each finding is
kept in its original order; completed rows name the dedicated commit that
contains the fix and its regression coverage.

| # | Finding | Status | Commit |
|---:|---|---|---|
| 1 | Update only the file that reorganize actually moved | Done | `4622f624` |
| 2 | Serialize each acquisition import before dispatch | Done | `d6d37eb2` |
| 3 | Synchronize automatic expiry deletes with Library V2 | Done | `804538c7` |
| 4 | Break the bootstrap into bounded transactions | Done | `c2d99eda` |
| 5 | Stream legacy rows during bootstrap | Done | `e9730afe` |
| 6 | Reject arbitrary artwork fetch targets | Done | `80b5af95` |
| 7 | Require artist context when matching Enrich results | Done | `280716d9` |
| 8 | Bound artist-list aggregation to the requested page | Done | `6c827c33` |
| 9 | Preserve non-Latin Enrich titles | Done | `abfa27a7` |
| 10 | Keep native Enrich's metadata-update contract | Done | `87b990bb` |
| 11 | Fail the monitor mutation when outbox enqueue fails | Done | `088e1dc7` |
| 12 | Fold alias rows into artist-list search and totals | Done | `ce7b4516` |
| 13 | Resolve alias groups for every artist-wide action | Pending | — |
| 14 | Rebuild album artist credits during re-import | Pending | — |
| 15 | Poll queue status once per artist page | Pending | — |
| 16 | Verify existing acquisition working copies by content | Pending | — |
| 17 | Make Refresh & Scan reportable and asynchronous | Pending | — |

Last updated: 2026-07-22 (implementation in progress).

## P1 — Update only the file that reorganize actually moved

Location: `core/reorganize_runner.py:87`

When a legacy-backed V2 track has multiple file rows, the `t.legacy_track_id=?` branch selects every file attached to that track, including secondary/native files whose own `legacy_track_id` is null. The subsequent update rewrites all of those rows to the moved legacy file's path, collapsing distinct files onto one path and corrupting the V2 catalog; resolve the exact moved file by its legacy ID or previous path instead.

## P1 — Serialize each acquisition import before dispatch

Location: `core/acquisition/import_pipeline.py:185-190`

When the periodic monitor overlaps an admin Resume request, both callers can read the same `importing` record and enter the dispatcher because there is no per-import claim or lock. Both then stage and process the same matches, overwrite the same runtime task IDs, and race read-modify-write completion callbacks, potentially moving a file twice or losing processed entries; atomically claim each import before dispatch and release or lease that claim afterward.

## P1 — Synchronize automatic expiry deletes with Library V2

Location: `core/repair_jobs/expired_download_cleaner.py:150-153`

When Expired Download Cleaner runs with `dry_run=false`, this direct helper path bypasses RepairWorker's `sync_repair_change` boundary. It physically deletes the file and legacy track while leaving `lib2_track_files` active and wanted state unrecomputed, so Library V2 still reports the file as owned and may suppress replacement downloads; run automatic deletions through the V2 file lifecycle and wanted/outbox synchronization before counting them as fixed.

## P1 — Break the bootstrap into bounded transactions

Location: `core/library2/importer.py:1350`

For a nontrivial bootstrap, artist, album, track, file, reconciliation, and wanted writes all remain in one SQLite transaction until this sole commit. Since the server is already accepting traffic and SQLite permits only one writer, a large import can make unrelated API and background writes exceed the 30-second busy timeout; connection-aware heartbeat updates are also uncommitted and invisible during this period. Commit restart-safe batches and perform final reconciliation separately.

## P1 — Stream legacy rows during bootstrap

Location: `core/library2/importer.py:1154-1155`

On large libraries, `SELECT *` followed by `fetchall()` retains every legacy track row—including potentially large lyrics and enrichment fields—while the importer also holds album, track, and file maps. The documented 320k-track deployment can therefore consume hundreds of megabytes or more and be killed during the mandatory first startup; select only required columns and iterate in bounded batches.

## P1 — Reject arbitrary artwork fetch targets

Location: `api/library_v2.py:2135-2136`

When an admin or API client submits a crafted artwork URL, it is passed to `requests.get` with redirects enabled, without scheme, destination-IP, or private-network validation, and the response is read through unbounded `resp.content`. This permits requests to loopback/private/cloud-metadata endpoints and memory exhaustion from oversized image bodies; accept server-issued candidate identifiers or validate every redirect and stream into strict byte and image-dimension limits.

## P1 — Require artist context when matching Enrich results

Location: `core/library2/native_enrich.py:302-307`

When an album or track lacks a provider ID and has a common title such as "Home", "Intro", or "Greatest Hits", ranking compares only the entity title. Search candidates already expose artist and album context in `extra`, but it is ignored, so a same-title result by another artist receives a perfect score and its provider ID is persisted automatically; require artist agreement, album context for tracks, and an ambiguity margin before writing identity.

## P1 — Bound artist-list aggregation to the requested page

Location: `core/library2/queries.py:108-110`

On every artist-list page or search request, these CTEs aggregate and de-duplicate the entire track/file catalog before the outer `LIMIT` and `OFFSET` are applied. With hundreds of thousands of tracks, opening the page or typing in search repeatedly performs full-library joins, distinct counts, and a window sort; constrain aggregation to filtered artists where sorting permits it and materialize indexed counters for aggregate-based sorts.

## P2 — Preserve non-Latin Enrich titles

Location: `core/library2/native_enrich.py:293-295`

When an album or track title consists entirely of CJK or other non-Latin characters, this ASCII-only normalizer reduces it to an empty string. The ranking loop then skips every candidate because `wanted` is false, so such entities can never acquire a missing provider ID through Enrich; use the Unicode-aware normalization already used for artist matching.

## P2 — Keep native Enrich's metadata-update contract

Location: `core/library2/native_enrich.py:325-334`

When Enrich is invoked for an already matched entity, `hit` remains empty and the native path only refreshes artist/album artwork or track duration; it no longer re-queries and writes the documented genres, year, label, UPC, style, mood, summary, lyrics, and other provider fields. The endpoint nevertheless reports success, making a formerly functional metadata action a silent no-op for most entities; port provider-specific descriptive enrichment to native rows rather than replacing it with ID/artwork resolution.

## P2 — Fail the monitor mutation when outbox enqueue fails

Location: `core/library2/mirror_outbox.py:54-58`

If `track_wishlist_payload` raises while processing a monitor change, the exception is logged at debug level and skipped, after which the caller can still commit the V2 state. Because no outbox row exists, the failure is absent from outbox status and cannot be retried, leaving the authoritative monitor state permanently divergent from the execution wishlist; propagate the error to roll back or persist a failed retryable outbox operation.

## P2 — Fold alias rows into artist-list search and totals

Location: `core/library2/queries.py:94-97`

After an artist is linked as an alias, the alias row is hidden here, but search still examines only the canonical name and the statistics CTEs remain grouped and joined by each raw artist ID. Consequently alias-owned albums, tracks, and bytes disappear from the canonical card, count-based sorting is wrong, and searching by the alias name returns nothing even though the detail page merges that alias; resolve each member to its canonical ID in both filtering and aggregation.

## P2 — Resolve alias groups for every artist-wide action

Location: `api/library_v2.py:4277-4279`

For a canonical artist whose linked alias owns releases, the detail page displays those releases but Refresh & Scan selects only exact `artist_id` rows. The same narrower scope remains in `retag.artist_track_ids`, `enqueue_artist_reorganize_all`, bulk release monitoring, and the duplicates query, so several artist-level controls silently skip visible releases; use the shared alias-group resolver for all artist-wide action scopes.

## P2 — Rebuild album artist credits during re-import

Location: `core/library2/importer.py:1273-1280`

When legacy metadata changes or removes a featured artist—or changes the primary artist—the track-level junction is reset, but album-level credits are only inserted with `INSERT OR IGNORE`. Old `lib2_album_artists` rows therefore survive indefinitely, causing ghost releases and incorrect artist counts/action scopes after a metadata correction; rebuild derived album credits for each imported album after processing its tracks.

## P2 — Poll queue status once per artist page

Location: `webui/src/routes/library-v2/-ui/library-v2-page.tsx:5555`

On an artist page with N releases, every collapsed `AlbumBlock` is still mounted and creates a distinct queue-status query that refetches every three seconds. Each request opens the database and scans all runtime task/context maps, so 100 releases generate roughly 33 requests per second while the page is idle; poll one artist-scoped status map and distribute its album entries, or limit polling to expanded/visible rows.

## P2 — Verify existing acquisition working copies by content

Location: `core/acquisition/main_pipeline_bridge.py:150-154`

After an interrupted import, rescan, or reassignment, a deterministic staging destination can already contain different content with the same byte size, especially when source files share a basename across disc folders. This branch treats that stale copy as valid and sends it through the pipeline for the new match, potentially importing the wrong recording; compare a content hash or atomically replace the working copy under the import claim.

## P2 — Make Refresh & Scan reportable and asynchronous

Location: `api/library_v2.py:4291-4296`

For a large artist or slow network-mounted library, this request synchronously probes every file and can exceed browser or reverse-proxy timeouts. A top-level scan failure is then caught and still returned as `success: true` with empty stats, so the UI can claim completion despite no scan; enqueue an observable background job and surface its terminal error, while retaining per-file error tolerance inside `rescan_files`.
