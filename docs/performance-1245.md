# Performance investigation: issue 1245

Reviewed baseline: `73e1f9abc`. No live media services, production database,
reporter machine, or actual music files were used by these reproductions.

## Reproduced before fixing

| Path | Before | After |
| --- | --- | --- |
| Re-register 75 known artwork URLs | SQLite trace recorded 75 INSERT/upsert transactions | Zero repeated registrations within a 60-second coalescing window |
| Stuck-download status recovery with a blocked filesystem finder | Another thread could not acquire the global task lock within 200 ms | Lock remains available and the status response returns while the finder is still blocked |
| Manual import with a blocked processor | HTTP response could not complete while processing was blocked | Opt-in asynchronous POST returns 202 and a job ID while processing continues |

The regression tests were run against the original behavior and failed on those
specific assertions before their corresponding production fixes were applied.
They use event-controlled I/O, rather than assuming a particular disk speed.
These are proofs of blocking/extra work, not benchmarks of the reporter's system.

## Changes and safeguards

- File recovery has two background workers and at most two outstanding scans.
  Repeated polls cannot duplicate a probe. Results are applied only if the same
  task/attempt is still current; cancellation, retries, removal, and completion
  supersede the scan. Failed post-processing submission restores a retryable
  state instead of leaving a task stuck in Processing with no worker.
- Artwork registration uses a bounded 4096-entry memo with a 60-second refresh.
  Eviction and clear invalidate the memo; image serving still updates access
  timestamps. First-time URL registration and actual image fetching retain
  their existing behavior.
- The browser opts into manual import jobs with `Prefer: respond-async` and an
  idempotency key. Jobs are profile-owned, have two workers and a 16-job active
  capacity, and retain results for one hour (maximum 1024 retained jobs).
  Duplicate keys return the existing job; conflicting payloads are rejected.
  Polling preserves existing HTTP errors, including the disconnected-server
  signal that stops a batch. Synchronous API clients remain supported.
- The frontend preserves the old long timeout for compatibility with old
  servers. It does not automatically resubmit an uncertain POST to an old
  server that may ignore idempotency keys. Its key generation works on HTTP LAN
  origins, and polling retries transient network failures without resubmitting.

## Scope and limitations

These changes remove avoidable blocking and repeated writes. They do not make
metadata providers, audio verification, or network storage intrinsically faster,
and do not prove the exact cause of the reporter's twenty-minute processing wait.

The separate hypothesis that cold artwork requests exhaust Gunicorn's request
threads has not been reproduced with the real browser/server workload. No
artwork-fetch admission, placeholder, or retry behavior was changed speculatively.

Import jobs are in-process, matching the current single-worker deployment.
A server restart loses job records; an unknown job returns an explicit error
asking the caller to check imported files before resubmitting. This is not a
persistent or restart-resumable job system. A stopped browser does not cancel an
already accepted import.

## Verification

Deterministic reproductions and race/lifecycle coverage are in
`tests/test_performance_1245.py`. Existing status tests now account for background
recovery. The frontend import API tests cover job polling and failure delivery.

A pre-existing failure was observed before any production edits:
`tests/downloads/test_downloads_status.py::test_external_audiobook_progress_survives_music_timeout_and_serializes`
expects `downloading` but receives `queued`. It remains outside this change.

Final validation: the main affected backend run had 125 passes and the known
baseline failure above; an adjacent status/post-processing/image-normalization
run had 65 passes. These runs overlap in regression coverage and should not be
added as a unique test count. All 18 frontend import tests passed. Ruff, targeted
TypeScript lint/type checking, and git whitespace validation passed.
