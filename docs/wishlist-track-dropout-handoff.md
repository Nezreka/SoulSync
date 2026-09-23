# SoulSync wishlist track dropouts during playlist/album ingestion

Investigation handoff, 2026-09-22. Intended destination: `~/dev/SoulSync/docs/`.

## Executive finding

There is a demonstrable application-level loss-of-intent window in the current wishlist/atomic-album flow. A per-track task is declared successful and its Spotify ID is **deleted from `wishlist_tracks` while its audio file is still under `.soulsync_atomic_staging`**. The album is published only later, when the batch completes. If the process stops or publication fails during that gap, SoulSync has no wishlist row to retry and Navidrome has no published file to index. A live Sep 22 restart left exactly such a batch in staging. This does not prove that every historical dropout had this cause; the historical audits also include matching ambiguity, indexing lag, and some genuine download failures. It does establish a concrete, high-priority bug path that can explain a substantial class of the observed behavior.

The most decisive sample is Hoobastank, *The Reason*, batch `6cf3b636-cfe4-4637-b705-032c750a8592`: `Never There` (`257U69MNzHAYAaQQpsfksD`) was removed from the wishlist at 18:51:15 UTC, the Gunicorn master shut down at 18:51:18, and the three MP3s from that batch still existed only in `.soulsync_atomic_staging` when checked in the replacement pod. The corresponding published album directory contained zero regular files. The previous container exited `0` with reason `Completed`; this example is a graceful shutdown/race, **not evidence of an OOM or NFS write error**.

## Environment and scope

- Live deployment on Sep 22: `soulsync` image `boulderbadgedad/soulsync:3.4.5`, one replica; pod `soulsync-64c64b4f5b-zsgtz`, 9 restarts over roughly four hours when observed. Latest terminated container: reason `Completed`, exit code `0`, finished `2026-09-22T18:51:18Z`. Local checkout was `f0030ef5` when this report was prepared; verify that this checkout corresponds to the deployed 3.4.5 image before patching.
- `/app/config`, `/app/data`, and `/app/logs` use `local-path-retain` PVCs. `/app/downloads`, `/app/Staging`, and `/app/Music` use NFS-backed PVs on `192.168.1.231` (respectively Soulseek completed downloads, music ingest staging, and music library). NFS may lengthen moves or expose additional failure cases, but it is not needed to explain the ordering bug.
- `slskd` was separately observed `1/2 CreateContainerError`; current SoulSync logs also showed a 120-second timeout on `POST .../searches`. Those are download availability issues, not direct evidence for deletion of an already accepted wishlist row. Investigate separately.
- Navidrome is the external library/playlist view. An absent Navidrome match alone cannot establish that bytes are lost: publication, scan, metadata, and matching may each lag or disagree.

## Evidence: actual delete-before-publish sequence

These are abbreviated, time-ordered lines from `kubectl -n soulsync logs soulsync-64c64b4f5b-zsgtz --previous --timestamps` (timestamps are UTC; internal app timestamps are America/New_York). The previous log ended immediately after this:

```text
2026-09-22T18:51:14.709Z soulsync.imports.pipeline - Moving 'Never There.mp3' to '/app/Music/library/.soulsync_atomic_staging/6cf3b636-cfe4-4637-b705-032c750a8592/Hoobastank/Album Hoobastank - The Reason/05 - Never There.mp3'
2026-09-22T18:51:14.710Z Cross-device move, using atomic copy+rename: [Errno 18] Invalid cross-device link: '/app/downloads/Never There.mp3' -> '/app/Music/library/.soulsync_atomic_staging/.../05 - Never There.mp3'
2026-09-22T18:51:15.826Z soulsync.imports.pipeline - Post-processing complete for: /app/Music/library/.soulsync_atomic_staging/6cf3b636-cfe4-4637-b705-032c750a8592/Hoobastank/Album Hoobastank - The Reason/05 - Never There.mp3
2026-09-22T18:51:15.864Z soulsync.wishlist.resolution - [Wishlist] Found Source track ID from source_ids: 257U69MNzHAYAaQQpsfksD
2026-09-22T18:51:15.867Z soulsync.wishlist.resolution - [Wishlist] Successfully removed track from wishlist: 257U69MNzHAYAaQQpsfksD
2026-09-22T18:51:18.004Z gunicorn.error - Shutting down: Master
```

Read-only check in the replacement pod, later the same day:

```text
$ find /app/Music/library/.soulsync_atomic_staging/6cf3b636-cfe4-4637-b705-032c750a8592 -type f -name '*.mp3' | wc -l
3
$ find '/app/Music/library/Hoobastank/Album Hoobastank - The Reason' -maxdepth 1 -type f | wc -l
0
```

Staged MP3s were `Same Direction`, `Disappear`, and `Never There`. This demonstrates the file was not physically lost, but it was invisible to normal library indexing while its wishlist retry record had been deleted. The `EXDEV`/`Errno 18` line is the expected cross-filesystem branch and explicitly says SoulSync used copy+rename; do not interpret that line as a failed move without a subsequent error. No atomic-publish success for this batch appeared in the sampled prior-log tail, and the persisted staging files confirm non-publication at inspection time.

The same ordering is routine in successful batches, not unique to the restart. For example, on Sep 22 `Soul Asylum / Grave Dancers Union (2022 Remaster)` staged `Growing Into You` at `19:36:12.320Z`, removed Spotify ID `054A1KitR5I6N1A3YzESAy` at `19:36:12.338Z`, and only published batch `a723779b-839e-4b21-bcb1-aafabd2a78a6` (17 files) at `19:36:24.115Z`. `Plain White T's / Every Second Counts` likewise removed several IDs at 19:40–19:44 before batch `9badf857-bbcd-442a-80fc-b6fc823c2996` published 21 files at `19:44:26.181Z`. Those successful examples prove the normal event order; they do not represent dropouts themselves.

## Code path explaining the observation

1. `core/imports/pipeline.py` redirects fresh full-album output into `/app/Music/library/.soulsync_atomic_staging/<batch-id>/...` when atomic album publishing is enabled (`_atomic_active`). This directory is intentionally hidden from media-server scans.
2. `core/downloads/post_processing.py` calls `deps.on_download_completed(batch_id, task_id, True)` after per-track import/verification, including the path where a file in the downloads folder has **no matched context** and is nevertheless marked completed merely because the file exists. Review that latter success path as another possible premature-removal route.
3. `core/downloads/lifecycle.py`, inside `on_download_completed`, invokes `deps.check_and_remove_from_wishlist(context)` for a successful task (around line 959). This happens **before** the batch-level `_publish_atomic_album` call (around lines 1049/1187).
4. `core/wishlist/resolution.py` extracts the source track ID and calls `wishlist_service.mark_track_download_result(track_id, success=True)` with no final published-path or media-server ownership check. Its fallback can also remove by title/first-artist match when no ID is available; profile handling merits separate review.
5. `core/wishlist/service.py` delegates that to `database.music_database.update_wishlist_retry`. In `database/music_database.py`, `success=True` executes `DELETE FROM wishlist_tracks WHERE spotify_track_id = ?` across **all profiles**, commits, and returns whether a row was deleted. Deletion is not transactional with a filesystem publish and does not retain a durable pending state.
6. `_publish_atomic_album` operates after tracks complete, at batch completion. It can fail and retry up to three times, then force the batch into `error` while leaving files staged. The code comment says this unblocks wishlist processing, but tracks previously marked successful have already had their rows removed. In-memory batch state is also lost on pod restart; the observed staged Hoobastank batch remained after restart.

Other wishlist deletion paths should be audited before attributing all losses to the primary path: `core/wishlist/processing.py:remove_tracks_already_in_library` removes on a manual match or `check_track_exists(... confidence_threshold=0.7, album=...)`; `core/downloads/cleanup.py` and other callers also call `mark_track_download_result(success=True)`. False-positive ownership matches could create a separate dropout mode. The Hoobastank case above does not depend on those paths: a direct source-ID removal is logged immediately after staging.

## Ingestion history and size of the symptom

These figures are snapshots from the playlist-import scripts and earlier audits, **not a single current total**. Requested full albums intentionally expanded the import set beyond the source playlists, so distinguish requested-album tracks from source-playlist tracks. A `201` from POST `/api/v1/wishlist` proves request acceptance then, not eventual audio publication.

| Campaign / check | Observed result |
| --- | --- |
| Initial 88 Keys to Peace + Fluid Focus | 437 wishlist requests accepted. |
| Betamax | 77 wishlist requests accepted. |
| Metal Mix | 472 accepted, 0 request errors; 49 full-album choices, 53 missing source tracks. |
| Reviewed Rock Classics | 532 accepted, 0 errors; 32 full-album choices and 38 reviewed track choices. |
| Reviewed 90s & 00s Radio Rock | 1,409 accepted, 0 errors; 93 album choices and 142 distinct missing source IDs (143 review decisions). |
| Earlier Sep 21 album audit | 57 missing tracks across 17 of 90 requested albums while SoulSync wishlist appeared empty. Four standalone tracks were also missing (`Some` — Nils Frahm; `Always - Monkey Safari Remix` — RÜFÜS DU SOL; `Choral` — Stefan Obermaier; `Jaw Breaker` — VHS Glitch); `Reminiscence` needed explicit artist-sensitive treatment. 62 were queued including Reminiscence. |
| Repeat Sep 21 audit | Still 57 album tracks missing, only 14 queued, 43 absent from wishlist (35 Metal Mix, 22 Rock Classics in the 57). The standalone items also appeared dropped. Requeue: 48 added, 14 refreshed, 0 errors. |
| After 90s import | Wishlist 1,347; 90s source tracks: 1 present, 117 missing, 24 weak matches. Eight missing source tracks were absent from both the matched library and wishlist, including `Slide`, `The Freshmen`, `Follow Me`, and `So Far Away`. |
| After restart/update and large requeue | Initial expanded-plan audit found 1,429 unresolved; one POST failed with `RemoteDisconnected`. A later pass found 1,420 unresolved and ultimately requeued 1,420 (104 added, 1,316 refreshed, 0 errors) after replacing a failed port-forward. This shows the helper can repair the visible symptom but does not establish the root cause. |
| Subsequent source-track check | Wishlist 735; 90s source tracks 72/142 present, 58 missing, 12 weak. Five absent from both matched library and wishlist were `Makes Me Wonder`, `Meet Virginia`, `Laid`, `Found Out About You`, and `Meant to Live`; they were re-added. |

Historical audits used `report_playlist_import_progress.py` and `requeue_unresolved_music.py` in sibling `../spotify-quick-scripts/`, with persisted `playlist_album_completion_audit_current.json`, `soulsync_album_audit.json`, and `soulsync_album_audit_verified.json`. The playlist report uses Navidrome `search3` and title/artist matching, sometimes normalizing version text; “orphaned” means “not matched in Navidrome and not returned in the wishlist API,” **not** “confirmed absent on disk.” This can produce false positives or negatives with alternate editions, compilations, artist credits, scan delays, or incorrect tags. `Reminiscence` was a concrete artist-mismatch exception. The large expanded-plan requeue includes album tracks not necessarily present on the source playlist.

## Additional signals and confounders

- The latest current-container log sample showed many successful atomic publishes (e.g. the 17-file Soul Asylum batch). A search of sampled publish-outcome lines did not surface a `NOT published` line. This does **not** refute the restart case; no publish was attempted for that in-memory batch before termination.
- In the same logs, a source file named `Miss Disarray.mp3` was moved to a staged destination named `03 - Gin Blossoms - Mrs. Rita.mp3` (Sep 22, around `19:38:20Z`). This is a separate identity/metadata concern worth checking; it could make title-based library audits appear missing or cause wrong-content matches. It is not by itself proof that either song was absent or that a wishlist row was wrongly deleted.
- `slskd` timeouts/availability, Soulseek candidate scarcity, and failed downloads can leave tracks genuinely pending. A properly retained wishlist row should survive those failures; distinguish “could not download” from “wishlist intent disappeared.”
- Album publish/scan latency can temporarily create an “absent from Navidrome” result. For conclusive classification, inspect **all four** states per Spotify ID: wishlist DB/API, active task/batch, staged file, and published file/Navidrome record. Beware snapshot timing while active downloads continue.
- The repeated manual requeues mean current wishlist counts are not a clean measure of original failure rate. A prior agent or user also restarted/updated SoulSync during the 90s campaign, so snapshots cross software and pod lifetimes.

## Suggested engineering fix and acceptance criteria

The invariant should be: **a requested track must remain durably retryable until a correctly identified playable file is published in the final library (or an explicitly accepted existing-library match is verified).** A per-track staged file is not completion for this purpose.

Suggested design direction, for the PR author to evaluate:

1. Represent “downloaded but awaiting album publication” as a durable intermediate state keyed by profile/source ID/batch, or simply retain the wishlist row through staging. Do not call `mark_track_download_result(success=True)` from per-track completion when `_atomic_active` is true.
2. After successful atomic publish, verify final paths exist (and ideally media identity), then remove only the intended wishlist IDs. If publish fails or the process exits, retain/recover the request. The final publish and wishlist transition need crash-consistent reconciliation; merely moving the existing delete call a few lines later still leaves a crash window unless startup can reconcile it.
3. On startup, discover old `.soulsync_atomic_staging/<batch-id>` directories and either resume/publish safely, quarantine with a visible retry state, or requeue the source IDs. Do not silently strand data. Preserve enough manifest/context on disk or in DB to map files to requested IDs after in-memory batch state disappears.
4. Ensure all success paths (including no matched context, duplicate detection, library cleanup, and profile-scoped deletion) satisfy the invariant. A `success=True` call should have an auditable reason and final file path. Consider whether the all-profile `DELETE` is intentional in non-shared libraries.
5. Add structured logs with source ID, wishlist row/profile ID, batch ID, staged path, final path, publish result, and removal reason. The current log gives a Spotify ID at deletion, but correlating it to a batch relies on adjacent timestamps.

Regression tests should cover: a staged track followed by process termination; album publish failure/retry exhaustion; two profiles; a false-positive existing-library match; a no-context file; and a successful album publish. In each failure case, assert the requested ID remains in a durable retry/recovery state and is absent from Navidrome only until publication. In the success case, assert that deletion occurs only after final-path existence and restart-safe reconciliation. A focused integration test can kill/restart between the two log events demonstrated above.

## Commands to reproduce/extend the investigation (read-only)

```sh
kubectl -n soulsync get pods -o wide
kubectl -n soulsync get pod <pod> -o jsonpath='{range .status.containerStatuses[*]}{.name}{" restarts="}{.restartCount}{" reason="}{.lastState.terminated.reason}{" finished="}{.lastState.terminated.finishedAt}{"\n"}{end}'
kubectl -n soulsync logs <pod> --previous --timestamps | rg 'soulsync_atomic_staging|Successfully removed track from wishlist|\[Atomic Publish\]|Shutting down'
kubectl -n soulsync exec <pod> -- find /app/Music/library/.soulsync_atomic_staging -type f -name '*.mp3'
kubectl -n soulsync get pvc -o wide
kubectl get pv soulsync-downloads soulsync-staging soulsync-transfer -o yaml
```

The live pod/log names will change after deployment. Do not run cleanup/recovery moves until the requested IDs, staging files, and published files are correlated and backed up. The staging folder contains recoverable audio, not disposable temp data in this failure mode.

## Key source references

- `core/imports/pipeline.py` — opt-in atomic staging redirect.
- `core/downloads/post_processing.py` — per-track success callback, including no-context branch.
- `core/downloads/lifecycle.py` — wishlist removal before `_publish_atomic_album`, publish retries and forced error.
- `core/wishlist/resolution.py` — source-ID and fuzzy fallback removal.
- `core/wishlist/service.py` / `database/music_database.py` — `success=True` deletes wishlist rows.
- `core/downloads/atomic_album_publish.py` — staging and publish implementation.
- `core/wishlist/processing.py` — separate already-in-library cleanup path.
- `../spotify-quick-scripts/report_playlist_import_progress.py`, `requeue_unresolved_music.py`, `playlist_album_completion_audit_current.json` — historical audit/requeue methodology and saved results.
