# Wishlist track dropouts — root cause analysis and proposed fix

Companion to `wishlist-track-dropout-handoff.md`. Code read at `dev` (`5e2e673e`).
Every line reference below was verified against that checkout.

## Summary

The handoff's executive finding is correct, and the code confirms it with a
sharper statement than "a race":

> **Atomic album publishing (#999) moved the moment a track becomes visible in the
> library from per-track post-processing to batch completion, but left wishlist
> removal at per-track post-processing. The two halves of "the request is
> satisfied" are now separated by the entire remaining runtime of the batch.**

Before #999, the ordering was crash-safe by accident: post-processing moved the
file to its final library path and *then* deleted the wishlist row
(`core/imports/pipeline.py:1156` → `:1440`). A crash between those two points
left a live file plus a stale wishlist row — the harmless direction, healed by
the already-in-library cleanup.

With `album_downloads.atomic_publish` on, `_maybe_stage_album_track`
(`core/imports/pipeline.py:330`, called at `:1154`) rewrites `final_path` to a
path under `<transfer>/.soulsync_atomic_staging/<batch-id>/`, and
`context['_final_processed_path']` becomes that staged path
(`core/imports/pipeline.py:1156`). The wishlist removal at
`core/imports/pipeline.py:1440` — and the duplicate one at
`core/downloads/lifecycle.py:959` — still fire, unchanged, with no idea the file
is not in the library. The actual publish happens much later, at batch
completion, in `_publish_atomic_album` (`core/downloads/lifecycle.py:87`, called
from `:1049` and `:1187`).

So the window is not milliseconds. It is **from the first track finishing to the
last track of the album finishing** — minutes to hours on a slow Soulseek album,
and unbounded if the batch never completes. The module's own docstring asserts
the invariant the wiring breaks:

> `core/downloads/atomic_album_publish.py:8` — "If the batch never completes, the
> staged files stay out of the library (quarantine) and the failed tracks stay
> retryable in the wishlist."

Failed tracks do. **Succeeded-but-unpublished tracks do not.** They are exactly
the ones whose rows have already been deleted.

The Hoobastank evidence in the handoff is this path, logged end to end: stage at
18:51:14.709, `DELETE` at 18:51:15.867, master shutdown at 18:51:18, three MP3s
still in staging afterwards, zero files in the published album folder.

## Why it never recovers

Three independent reasons, all confirmed in code:

1. **Batch state is in-memory only.** `download_batches` / `download_tasks` are
   process-local dicts. `_atomic_active`, `_atomic_staging_root` and
   `_atomic_transfer_dir` live only there (`core/imports/pipeline.py:395-397`).
   A restart erases the only record that maps a staging directory to a batch.

2. **Nothing ever looks at an orphaned staging tree.** There is no startup
   reconciliation anywhere — the only references to `_STAGING_DIRNAME` outside
   the publish module are two *skip* rules: the SoulSync scanner
   (`core/soulsync_client.py:279`) and the repair jobs
   (`core/repair_jobs/base.py:78`) both prune it deliberately. The dot prefix
   hides it from Navidrome. So the audio exists, is fully post-processed and
   tagged, and is invisible to every consumer forever.

3. **The wishlist row is gone, so nothing re-requests it.**
   `process_failed_to_wishlist` re-adds *failed* tracks at batch completion.
   Tracks marked successful were never captured. `update_wishlist_retry`
   (`database/music_database.py:14405`) hard-`DELETE`s on success; there is no
   tombstone, no pending state, nothing to reconcile against.

## The second, worse variant: the audio is deleted too

The restart case at least leaves recoverable bytes. This one does not, and it
needs no crash.

`discard_staging_root` (`core/downloads/atomic_album_publish.py:293`) `rmtree`s
a batch's staging root. It is called from two places:

* `web_server.py:1989` — the 30s healing loop's stale-batch cleanup, which fires
  for any batch in phase `complete`/`error`/`cancelled`/`failed` five minutes
  after `completion_time` (`web_server.py:1836-1848`).
* `web_server.py:16432` — the batch cleanup/cancel endpoint.

The only guard is `_preserve_failed_publish` (`web_server.py:1984`), which
requires `_atomic_publish_attempts > 0`. That covers the lifecycle's
retry-exhaustion path, and nothing else. In particular:

* The **600s stuck-heal** at `web_server.py:1955-1971` forces `phase = 'error'`
  on a batch whose tasks wedged *before* any publish was attempted, so
  `_atomic_publish_attempts` is `0`. Five minutes later, the staging tree — with
  every already-downloaded, already-de-wishlisted track in it — is `rmtree`d.
* A **user cancel** of a partially-downloaded album does the same.

Net result: the wishlist row was deleted at stage time, the file is deleted at
cleanup time, and on top of that a cancel writes a TTL'd ignore entry
(`core/wishlist/ignore.py`) that suppresses the automatic re-add paths. The
track is gone with no trace in any of the four states the handoff says to check.

Given 9 restarts in ~4 hours during a multi-thousand-track ingest, with several
concurrent album batches each holding 10-20 tracks, these two paths alone
comfortably account for the "57 missing across 17 of 90 albums" shape of the
symptom — partial albums, not whole ones, because tracks still *downloading* at
the cut keep their rows and do get retried.

## Contributing paths (independent of atomic publish)

Ranked by confidence. These explain dropouts in deployments with
`atomic_publish` off, and add to the count when it is on.

### A. "No matched context" is reported as success — high confidence

`core/downloads/post_processing.py:619-633`: a file found in the **downloads
folder** with no entry in `matched_downloads_context` is marked
`mark_task_completed(...)` and then `deps.on_download_completed(batch_id,
task_id, True)`. That success flows to `core/downloads/lifecycle.py:959`, which
removes the track from the wishlist.

Nothing imported the file. It is still in `/app/downloads`, untagged and
unrenamed. The library never gets it, the wishlist no longer wants it. This is a
complete dropout with no crash and no atomic publishing involved.

### B. Success removal is unconditioned on a real library path — high confidence

`check_and_remove_from_wishlist` (`core/wishlist/resolution.py:43`) takes a
context, digs out a track id, and deletes. It never receives, checks, or logs a
final path. The lifecycle caller synthesizes a context out of `track_info` alone
(`core/downloads/lifecycle.py:955-959`) — there is no path in it at all. This is
what lets both (A) and the staging bug through: nothing in the removal path can
tell "published to the library" from "staged", "still in downloads", or "never
moved".

### C. All-profile `DELETE` — confirmed, scope question for the maintainer

`database/music_database.py:14413`:

```sql
DELETE FROM wishlist_tracks WHERE spotify_track_id = ?
```

no `profile_id`, while the failure branch three lines down *is* profile-scoped,
and migration `database/music_database.py:4978-5013` deliberately rebuilt the
table for **profile-scoped uniqueness**. The comment says "track is now in
shared library", which is true for one shared library and false for own-library
profiles (`core/library_scope`, #1199). One profile's download silently empties
every other profile's request for that track.

### D. Fuzzy removal by title + first artist — medium confidence

`core/wishlist/resolution.py:88-99` falls back to matching every profile's
wishlist by lowercased title and lowercased first artist when no source id is
available, then deletes whatever it hits. Combined with the handoff's
observation that `Miss Disarray.mp3` was imported as
`03 - Gin Blossoms - Mrs. Rita.mp3`, a misidentified download can delete the
wishlist row of a *different* requested track. Same fallback again in
`check_and_remove_track_from_wishlist_by_metadata` (`:127-163`).

### E. `confidence_threshold=0.7` already-in-library cleanup — medium confidence

`core/wishlist/processing.py:658-672` removes a wishlist row whenever
`check_track_exists(..., confidence_threshold=0.7, album=...)` returns a match
for *any* of the track's artists. At 0.7, covers, live versions, remixes and
compilation duplicates match. This is the most plausible explanation for the
handoff's standalone dropouts with generic titles — `Laid`, `Meet Virginia`,
`Makes Me Wonder`, `Found Out About You`, `Slide`, `Follow Me` — none of which
need a crash or an album batch to disappear. It is also unauditable today: the
log line names the wishlist track but not the DB row that matched it.

## Proposed fix

The invariant to enforce, stated as a precondition on a single function:

> A wishlist row may only be deleted when the caller can name a file that
> (a) exists, (b) is under the profile's library root, and (c) is **not** under
> `.soulsync_atomic_staging` — or when the removal is an explicit,
> user-initiated action.

### 1. Make the precondition real (fixes B, A, and most of the staging bug)

Give `check_and_remove_from_wishlist` a required published-path argument and a
removal reason:

```python
def check_and_remove_from_wishlist(context, *, published_path=None,
                                   reason="download_complete", ...):
    if reason == "download_complete" and not _is_published(published_path, context):
        logger.warning("[Wishlist] Refusing removal for %s — no published library "
                       "path (path=%r, staged=%s)", track_id, published_path, ...)
        return
```

where `_is_published` checks existence, library-root containment, and
`not is_staged_path(path, transfer_dir)` — `is_staged_path` already exists at
`core/downloads/atomic_album_publish.py:68`.

Wiring, three call sites:

* `core/imports/pipeline.py:1440` — pass `context['_final_processed_path']`. In
  atomic mode that is the staged path, so the guard defers the removal. Correct
  by construction, no flag to keep in sync.
* `core/downloads/lifecycle.py:959` — pass
  `download_tasks[task_id].get('final_file_path')` (already recorded at
  `core/imports/pipeline.py:1455`). For the no-context path (A) that key is
  absent, so the removal is refused and the track stays retryable.
* `core/imports/pipeline.py:952` (simple download) — passes `destination`, which
  is a real library path. Unchanged behavior.

This one change closes A, B and the per-track half of the staging bug, and it
fails safe for any future caller that forgets.

### 2. Remove after publish, from the publish itself

When a track's removal is deferred, record it on the batch:

```python
batch.setdefault('_wishlist_pending', []).append(
    {'source_ids': ..., 'profile_id': ..., 'staged_path': ..., 'final_path': ...})
```

In `_publish_atomic_album` (`core/downloads/lifecycle.py:87`), after
`result['success']` and the `pubmap` remap, walk `_wishlist_pending`, look each
entry's `staged_path` up in `pubmap`, confirm the final path exists, and only
then remove. A crash after publish but before removal now lands on the *safe*
side: a live file and a stale row, healed by the already-in-library cleanup.

Note the handoff is right that this alone is not sufficient — it shrinks the
window rather than closing it. Step 3 closes it.

### 3. Durable manifest + startup reconciliation

Write `<staging_root>/.soulsync_batch.json` when the batch is marked
`_atomic_active` (`core/imports/pipeline.py:395`), and append to it as each
track stages: `{batch_id, transfer_dir, profile_id, tracks: [{staged, final,
source, source_ids}]}`. It costs one small write per track and it is the only
thing that survives the process.

On startup, scan `<transfer>/.soulsync_atomic_staging/*` for roots with no live
batch and, per root:

* manifest present and files map cleanly → **publish it** (the files are already
  post-processed and tagged), then run the manifest's wishlist removals;
* manifest present, publish fails → leave staged, log loudly, surface it in the
  UI, and re-add the manifest's `source_ids` to the wishlist;
* no manifest → move the audio back to the downloads folder for reimport rather
  than deleting it.

Deleting must never be the default for a tree that holds finished audio.

### 4. Stop `rmtree`-ing recoverable audio

Replace the `_preserve_failed_publish` test at `web_server.py:1984` with:
discard only when the batch was **explicitly cancelled by the user** *and* no
wishlist removals were consumed for it. Everything else — force-errored, stale,
publish-failed — keeps its staging and gets reconciled by step 3. (With step 1
in place the wishlist rows survive anyway, so this is about not re-downloading
gigabytes; without it, this is the difference between recoverable and lost.)

### 5. Scope the delete to the profile

`database/music_database.py:14413` → `DELETE FROM wishlist_tracks WHERE
spotify_track_id = ? AND profile_id = ?`, with the all-profile sweep kept only
behind an explicit `all_profiles=True` used where the library really is shared.
Callers in `core/wishlist/resolution.py:101/127/160` currently pass no
`profile_id`; resolve it from the context/batch (`batch.get('profile_id')` is
already threaded for `library_root_for_profile` at
`core/imports/pipeline.py:370`).

### 6. Tighten the heuristic removals

* `core/wishlist/resolution.py:88-99`: only run the title/artist fallback when
  there is no source id *and* the batch/task cannot supply one; require album
  agreement too; log at warning with both sides of the match.
* `core/wishlist/processing.py:658-672`: raise the threshold, require the album
  to agree when the wishlist entry has one, and log the matched DB row's
  `file_path` so a false positive is auditable after the fact.

### 7. Observability — an audit trail, not just a log line

One structured line at every removal (`source_id`, `profile_id`, `batch_id`,
`reason`, `final_path`), plus a small `wishlist_removals` table keyed by
`spotify_track_id`. That turns the handoff's "why did this vanish" forensics —
currently done by correlating adjacent timestamps — into a single query, and it
is what would have made this investigation ten minutes instead of a day.

## Regression tests

1. Atomic batch, one track staged, process killed before publish → row still in
   `wishlist_tracks`; on restart, staging is reconciled and the file lands in the
   library.
2. Atomic publish fails all attempts → rows retained, staging preserved, batch
   errors without blocking wishlist processing.
3. Atomic batch force-errored by the stuck healer with `_atomic_publish_attempts
   == 0` → staging **not** discarded.
4. No-matched-context completion (`core/downloads/post_processing.py:619`) →
   task completes, wishlist row retained.
5. Two profiles wishlist the same id, one downloads → the other's row survives.
6. False-positive library match at 0.7 → row retained (or removal logged with
   the matched path).
7. Happy path: atomic album publishes → all rows removed exactly once, after the
   final files exist.

Tests 1 and 3 are the ones that would have caught this; both are expressible
against the existing `tests/test_atomic_staging_unwritable.py` harness.

## Ordering

1 and 4 are small and stop the bleeding. 2 and 3 are the real fix. 5, 6 and 7
are independent and can ship separately. 3 also needs a one-off recovery pass for
whatever is sitting in `.soulsync_atomic_staging` on the live deployment right
now — those files are recoverable audio, not temp data.

---

# What shipped

All seven, on `fix/wishlist-dropout-atomic-publish` off `dev`.

| # | Change | Where |
| --- | --- | --- |
| 1 | `may_remove(published_path, reason)` — a wishlist row may only be deleted for a completed download when the caller names a file that exists and is not staged. Ungated for user actions and already-owned cleanups. | `core/wishlist/removal_guard.py` (new), wired at `core/imports/pipeline.py`, `core/downloads/lifecycle.py`, `core/wishlist/processing.py` |
| 2 | Staged tracks record a deferred removal on the batch instead of deleting; `_publish_atomic_album` settles them after the album is live, each checked against a file that exists at its final path. | `core/imports/pipeline.py::_settle_wishlist_for_completed_track`, `core/downloads/lifecycle.py::_settle_deferred_wishlist`, `core/wishlist/resolution.py::remove_published_wishlist_entries` |
| 3 | A JSON manifest inside each staging root (batch id, library, per-track source ids) plus a startup pass that publishes abandoned trees and clears their wishlist rows. A staged file whose final path is already taken is set aside into the Downloads recycle bin instead of overwriting the library copy. Gated by `album_downloads.atomic_recover_orphans` (default on). | `core/downloads/atomic_manifest.py`, `core/downloads/atomic_recovery.py` (both new), startup hook in `web_server.py` |
| 4 | `should_discard_staging(batch)` — only an explicit user cancel deletes staged audio. Errored, force-errored, publish-failed and stale batches keep theirs. | `core/downloads/atomic_album_publish.py`, both `web_server.py` discard sites |
| 5 | The success `DELETE` takes `profile_ids`; the resolver computes them from which profiles' library roots actually contain the published file. The unscoped sweep stays the default for callers that cannot tell. | `database/music_database.py::update_wishlist_retry`, `core/wishlist/resolution.py::_profiles_owning_path` |
| 6 | The metadata fallback requires album agreement and refuses an ambiguous match rather than guessing; the already-owned check ignores a library row still pointing into staging. | `core/wishlist/resolution.py::_match_by_metadata`, `core/wishlist/library_match.py` (new) |
| 7 | A `wishlist_removals` audit table (track, profiles, reason, final path, batch, timestamp) and one structured log line per decision, including refusals. | `database/music_database.py`, `core/wishlist/resolution.py::_log_removal` |

A fourth unguarded removal path turned up during the work and is fixed the same
way: `remove_completed_tracks_from_wishlist` (`core/wishlist/processing.py:81`)
swept every `completed` task at batch completion with no path at all.

## What this does not change

#999's guarantee is intact. The staging redirect, the all-or-nothing publish and
the rollback are untouched, and a live batch still publishes only when the album
is complete. `tests/downloads/test_atomic_publish_wiring.py` — including the
flag-off pass-through and the Lil-Uzi-Chimp rowcount case — passes unmodified.

The one judgement call is startup recovery publishing an album the interrupted
batch had not finished, which can make an incomplete album visible. #999 is about
what the media server sees *while an album is downloading*; here the download is
already over, and the choice is between an album that fills in and an album that
never appears. It also composes: the tracks the batch never got to are still on
the wishlist, and because the album folder is no longer fresh they arrive through
the ordinary per-track publish, so nothing is re-downloaded. Users who prefer
strict quarantine can turn `atomic_recover_orphans` off; nothing is ever deleted
either way.

## Recovery never overwrites

Worth stating separately, because it is the one place recovery could have done
harm. A **live** atomic batch cannot collide with the library:
`album_folder_is_fresh` only lets a batch stage into an album folder that holds
no audio. A tree recovered weeks later has no such guarantee — the stranded
tracks have very likely been re-requested and some have landed — and
`safe_move_file` publishes with `os.replace`, which overwrites.

So before publishing a recovered tree, any staged file whose final path already
exists is moved into the Downloads recycle bin (`.deleted/atomic_superseded/`,
restorable like every other quarantined file) and the rest of the album
publishes normally. The tracks that really are missing land; the tracks the
library already has are untouched. If a set-aside fails, the whole tree stays
staged rather than risking an overwrite.

## Tests

`tests/downloads/test_atomic_wishlist_dropout.py` — 34 cases covering all seven
acceptance criteria. The two that would have caught the original bug are
`test_a_staged_track_defers_its_wishlist_removal` and
`test_only_an_explicit_cancel_discards_staged_audio[error-0-False]`.
