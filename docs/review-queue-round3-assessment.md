# Review queue: third feedback round

Scope: screenshot findings 1–23. The Operations redesign (24) is deferred.

## Decisions and application

Numbering and metadata choices are drafts. Quarantine, replacement and duplicate-keeper choices are also saved in the album plan. Choosing them does not edit audio files. The user can revise a choice, apply the album, or save a resolved plan for a later explicit batch. Saved approvals are bound to the current evidence and selected file action; changed evidence invalidates them. An applied “keep numbering” decision survives unrelated metadata repairs while the release, fingerprint and exact numbering proposal remain the same. Reopening it clears that choice.

The current global ready action includes fully decided album plans, including explicitly approved physical file decisions. One unresolved required decision excludes the entire album. Confirmation describes metadata edits, file decisions and acquisition requests; the exact confirmed snapshot is revalidated before execution. Existing file identity, release, fingerprint and playlist guards still run when applying it.

Release pins, completeness intent and assignments update the plan and trigger reanalysis. Explicit wishlist requests still start acquisition. Reopening a historical decision is reconsideration, not reversal of an already completed file edit.

## Interface changes

- Review and proposed-change groups collapse consistently.
- Listening links use the existing outlined-control style. Recording IDs are compact, muted, underlined links with hover/focus color; release-reference buttons remain intact.
- Playback reuses the Library play icon and shared audio-format badge without replacing the review layout.
- Extra-file coverage and actual review state are separate, so a reviewed extra still counts as an extra. Wrong audio retains its warning color and evidence.
- Valid previous decisions are reflected in release previews; obsolete scan findings are not presented as user acceptance. Historical decisions can be reopened.
- Ready and review-required quantities are distinguished, with one apply panel rather than duplicate summaries.
- Album operations report progress in the album pane; global operations report at the top. Background apply operations use the existing task/notification callbacks and persist recoverable operation status.
- Plan changes have stable saved/updating feedback. They do not imply that file tags have already been applied.
- Wishlist panels link to the wishlist. Requested and published-but-unverified tracks remain in the queue, and resolving another action keeps the selected waiting album open.

## Findings confirmed from the supplied examples

- Queen's extra file tagged “Who Wants to Live Forever” has fingerprint evidence for “Teo Torriatte (HD mix)”. The conflict is not simply its ten-second duration difference. Extra-file snapshots previously kept abbreviated audio summaries; they now retain full candidates and local tags. An obsolete finding had incorrectly been presented as a reviewed decision; obsolete results now remain reviewable and current evidence is retained.
- Continuous numbering was being interpreted as an outlying local position on multi-disc releases. This explains the false review reason on “Living on My Own”, “Girl”, and “You've Got to Hide Your Love Away”. The matcher now recognizes continuous release-slot positions without weakening version checks.
- New Light's single local track belonged to a playlist. That silently implied a partial album. It now prompts for completeness, using partial ownership only as a suggestion.
- Shaman's “The Game of Love” already had `Santana, Michelle Branch` in both its local track-artist tag and database credit. The evidence view showed the narrower artist field. It now displays the track credit when available and preserves richer credits. All 11 Shaman tracks with featured performers already contain the guest in their local track-artist credit; the proposals preserve those values.
- Faith's reviewed duration discrepancy remains inspectable and can be reopened. Matching a recording identifier does not mean all duration discrepancies should be silently accepted.

## Other-tools overlap audit

Read-only baseline captured 2026-10-17. Joined current findings to queue cases using physical path aliases, local album IDs and file membership. These are overlaps, not proof that every proposed value conflicts.

| Other finding type | Pending | Findings overlapping queue cases | Concern |
| --- | ---: | ---: | --- |
| Recording MBID mismatch | 824 | 731 | May overwrite the queue's recording decision |
| Album MBID mismatch | 3 | 3 | May replace the selected release identity |
| AcoustID mismatch | 133 | 93 | Same audio-identity decision, different presentation |
| Track numbering | 35 | 35 | Can contradict saved numbering intent |
| Canonical version | 266 | 128 | Edition/provider selection can differ |
| Incomplete album | 127 | 71 | May duplicate or contradict completeness and wishlist intent |
| Duplicate tracks | 89 | 66 | May discard intentional repeated release slots |
| Album tag inconsistency | 9 | 8 | May compete with planned album metadata |
| BPM | 500 | 197 | Independent field, subject to normal stale-file guards |
| Genre | 159 | 39 | Independent field |
| Cover art | 11 | 6 | Usually independent; artwork edition still matters |
| Lyrics | 63 | 23 | Independent if recording identity remains correct |
| ReplayGain | 9 | 7 | Independent analysis, subject to file identity |
| Dead file / orphan / empty folder | 8 / 16 / 1 | 0 / 0 / 0 | No active-case overlap in this snapshot |

Of 128 overlapping canonical-version proposals, 97 specify a different provider/release key. Eleven use the same provider but propose a different release ID. Cross-provider differences alone do not prove different editions. Of 66 overlapping duplicate findings, 49 contain files mapped to distinct release slots: repeated recordings on compilations cannot be treated as disposable copies solely because their audio matches.

This round centralizes missing-track decisions in the queue while reusing the wishlist/download machinery. It does not remove the legacy tools or claim that their findings have been reconciled with queue decisions. Shared ownership of edition, acquisition and file decisions is work for the deferred Operations discussion.

## Runtime and acquisition

Applying changes also waits for reconciliation access, verifies fingerprints and release/file identity, checks playlist references, writes tags/database state, may rename files, persists progress, and reanalyzes the album. A read-only five-file FLAC fingerprint benchmark took 0.642–0.968 seconds per file, median 0.878 seconds. That alone is roughly 23 seconds for 26 files; it is not a measured total apply time. Progress reports now expose waiting, fingerprinting and application timing.

Wishlist publication is recorded before removing the wishlist entry. Publication schedules a coalesced album discovery refresh. A request remains pending until the correct release slot is actually assigned; a completed transfer alone does not establish a match. Existing media-server library-scan automation remains in place. Historical wishlist entries without a current request or publication record are not assumed to still be downloading.

## Verification

File-changing workflows are exercised against temporary test libraries, not by applying the user's pending library decisions. Deployed browser checks inspect real queue data and block mutation requests. Final deployment and reconciliation results are recorded below after both cycles complete.

### First completed assessment

- Image `74eebd3f1f9a`; run 829 completed in 1,896 seconds, 13,830 files / 1,600 folders, zero errors, zero automatic fixes.
- Queue: 642 → 890 albums. Completeness findings: 25 → 227. Obsolete results are no longer interpreted as accepted reviews.
- Queen uncertainty: 7 → 1; the separate wrong-audio file still has three AcoustID candidates and one missing counterpart slot.
- Beatles 1962–1966 uncertainty: 4 → 0.
- New Light now has an explicit completeness question.
- Prequelle has one current audio decision for Faith; motionblur has five audio decisions and complete 26-slot coverage.
- Follow-up fixes include deferred physical file actions, consistent readiness counts, retryable failed choices, exact provider identity for acquisitions, legacy request migration, preserved numbering choices, and corrected link/button styling.

### Follow-up deployment and checks

- Deployed image `3c784a60a606`, healthy after rollout.
- 200 focused backend tests passed, including the apply/rescan/reopen lifecycle and actual background application of staged file actions against test data.
- 32 review UI tests passed. Frontend check: zero errors (548 existing warnings); production build passed.
- Final deployed browser pass: 54 album/tab/viewport combinations at 1800, 840 and 390 pixels; no horizontal overflow or JavaScript errors.
- Direct interaction checks verified collapsing, full wrong-audio evidence, outlined listening links, and recording IDs matching their surrounding muted text with underline and hover color.
- Live readiness probe: 812 advertised actions = 812 actual ready metadata IDs.
- Second full reconciliation: run 831 completed in 1,612 seconds, 13,830 files / 1,600 folders, zero errors, zero automatic fixes, and zero new findings.

### Final reassessment

- Runs 829 and 831 produced the same final 890 album cases and 2,555 pending reconciliation findings. No pending IDs were added or retired in the second run; all supplied example albums retained the same assignment counts.
- Final modes: 776 albums need input, 110 ready albums, 4 file-action albums. The global metadata action advertises 812 changes, matching the actual selector.
- Live image `localhost/soulsync:test-3c784a60a606` is Ready (1/1). The feature deployment remains available for review with Flux suspended as in the existing test workflow.
- The second deployment's runtime logs contained MusicBrainz release-group 404 lookups, but no other errors, tracebacks or reconciliation persistence failures.
- The live wishlist and transfer list were empty. Publication-to-refresh behavior was verified through integration tests rather than a fabricated live download. No pending user album repair plan was bulk-applied for validation.
- The audit of other Operations findings is documented above. Their independent actions still exist; replacing or reorganizing Operations remains deferred under item 24.


## PR readiness follow-up (2026-10-18)

This follow-up completes compatibility and correctness within the existing feature. It does not replace the Operations page or retire its specialized jobs. Earlier deployment measurements above describe their historical builds, not the current global batch contract.

### Ownership and ordering

- The selected release, completeness intent, manual assignment and numbering convention govern dependent edits. Queue choices reuse the existing canonical pin, intent, assignment and finding/choice journals.
- Legacy maintenance checks these decisions before mutations. Conflicting changes remain unapplied and return an exact album-review link. Independent fields such as BPM, genre and ReplayGain remain with their existing tools.
- Active album plans protect file membership from legacy duplicate cleanup, replacement and organization. Queued organization checks again at execution. Maintenance mutation units share a process lock; scans and UI reads do not hold it. This is protection within SoulSync's single maintenance process, not a distributed lock against external file editors.
- Accepted field values remain authoritative after application. Reopening clears the decision; a changed proposal cannot reuse approval for older evidence. Unchanged queue resolutions do not expire under the generic seven-day recurrence rule.
- Verified unique relocation preserves manual assignments and saved choices. Changed audio, changed size, ambiguous copies or an old path that still exists do not qualify as a verified relocation. Exact acquisition targets follow case redirects within the same profile and release.

### Ready, acquisition and recovery contracts

- Ready is an album classification. All required decisions must be resolved before any of that album's actions enter the global batch. A previously suspect action becomes eligible after explicit approval; harmless supported metadata can become eligible automatically.
- Download requests already waiting for fulfillment are not unanswered decisions. Their presence does not authorize unrelated unresolved changes. Published files remain pending until reconciliation verifies their exact release-slot assignment.
- Cancellation records acquisition state without changing complete/partial intent or wanted slots. Requesting again remains an explicit action. Profile, provider and release identities are preserved.
- If one action fails, remaining dependent actions in that album stop. Other approved albums can continue. Restarted operations report interruption and require revalidation/retry; they do not automatically replay file actions. Reopening a decision does not undo a prior file write.
- File edits and quarantine tests use disposable libraries. Live validation does not apply the user's waiting plans.

### Why ordering matters in the observed cases

| Observed case | What independent tools can get wrong | What the queue establishes first |
| --- | --- | --- |
| Queen, “Who Wants to Live Forever” | Correcting the number/title would make the wrong recording look more convincing; the fingerprint actually supported “Teo Torriatte (HD mix)”. | Resolve audio identity and replacement before metadata or filenames. |
| *Rated R* and *Songs for the Deaf* | Retagging to one source can conceal split/combined tracks or a mixture of editions. | Select the fitting edition, then calculate assignment, missing tracks and dependent edits. |
| Joep Beving, *Trilogy* | Per-track repair can repeatedly restore disc-relative numbers against an intentional continuous sequence. | Choose one numbering convention for all 46 tracks and preserve it. |
| Chelsea Wolfe, *The Dark* | Independent duplicate cleanup can choose a single copy without considering the album slot or playlist use. | Compare the competing copies, select the keeper, then apply the approved plan. |
| Gidge, *New Light* | Pinning an edition or observing playlist membership can be mistaken for a deliberate partial-album choice. | Ask separately whether the selected nine-track edition is intended to be complete. |
| Compilation repeats | Matching fingerprints alone can make two legitimate release slots look like disposable duplicates. | Establish one-to-one release assignments before deciding which copies are surplus. |

These are documented failure modes and observations, not a claim that every historical file change can be attributed to a particular legacy job run.

### Readiness and performance audit

The pre-deployment snapshot contained 884 albums: 758 needing input, 124 ready, two needing file decisions and none solely waiting. The ready selector contained 567 actions: 566 metadata actions and one explicitly approved replacement; 34 actions had explicit approval and 533 were automatic. The automatic entries had clear assignments and no recorded audio/version conflict.

The audit identified Isis's “Weight”: a release-ID rewrite remained automatic despite fingerprint decoding failure. That now requires review and excludes its whole nine-action album from the batch. The two other unsuccessful fingerprint lookups proposed only library album-display cleanup, so remain eligible. The resulting unchanged-snapshot selector has 123 ready albums and 558 actions. Supported version text remains automatic only with exact recording support, close duration and no competing explicit version; explicit approvals remain effective.

A compact policy query reads only classification inputs for list/count endpoints. Its results match the full evidence selector across all 884 captured albums; execution still uses complete evidence. Five interleaved local replays after warming both paths measured median classification times of 0.339 seconds with full evidence and 0.164 seconds with compact inputs. This is an isolated query measurement, not a whole-page speed claim.

The UI loads the album list independently of global counts, fetches the selected album without waiting for both, avoids refetching its detail solely because a mode changed, and avoids whole-queue recounts on each running-album poll. Detail reads load only that case's saved choices. Deployed read-only checks measured the following. The endpoint figures are medians of three requests through the same local tunnel; background provider activity was not controlled, so they are observations rather than a throughput guarantee.

| Measurement | Before | After |
| --- | ---: | ---: |
| Queue summary response | 0.917 s | 0.344 s |
| Queue list response | 1.435 s | 0.478 s |
| First selected-album request begins after navigation | 5.6 s | 2.3 s |

Under concurrent browser loading and reconciliation, some requests still exceeded one second. The shared application bundle and large technical-evidence payloads remain costs; this pass avoids new caching or changing the evidence/execution contract.

### Completed regression and deployed UI checks

- 1,146 backend tests passed across reconciliation, repair jobs, queue/API, legacy retagging, organization, requests, canonical resolution and pin guards.
- 670 Review/Tools UI tests passed. Frontend checks completed with zero errors and 551 warnings; production builds succeeded.
- 27 representative album/tab/viewport combinations passed at 1800, 840 and 390 pixels, with no JavaScript errors or horizontal document overflow. These covered Oceanic, Queen's Platinum Collection and Magnolia. The intentional mobile inspector was closed before changing tabs.
- All 558 eligible actions had present source files, matching recorded size/mtime and no occupied or duplicate rename destinations. All 34 pending saved approvals still matched their evidence during the scan. The live confirmation agreed on 123 albums, 558 actions and one explicitly approved replacement.
- The global batch was inspected without applying it. Mutating integration tests used temporary libraries. Wishlist cancellation preserves intent, and cancelled requests remain visible as wanted missing tracks without inflating the waiting count.
- The legacy automatic canonical resolver and API-based organization now check accepted queue fields as well as generic finding actions. Tag-based organization can retain the reviewed metadata.
- During rollout preparation, real downloads encountered slskd ENOSPC errors and a stale completed-download mount. No storage changes or wishlist cancellations were made for this verification. Later ordinary wishlist downloads completed successfully, so the errors were not a persistent total outage. The exact queue-request-to-verified-slot lifecycle, publication and cancellation were exercised in integration tests; no artificial live request was created.

### Full reconciliation and final reassessment

- Run 841 completed all 13,827 files / 1,600 folders in 1,921.68 seconds, with zero errors, zero automatic fixes and zero new findings.
- The ready batch retained exactly the same 558 action IDs across 123 albums, including one approved replacement. All 34 pending approvals still matched their evidence; a repeated filesystem audit found no changed sources or conflicting rename targets.
- All 1,567 logical album plans remained present. Comparing assignments and proposed changes, excluding inventory bookkeeping and expiring preview URLs, found changes only in Bastille's *Bad Blood X* and *motionblur*: existing manual assignments were restored. Motionblur's five accepted assignments no longer produced unresolved findings. Final totals were 883 albums: 758 needing input, 123 ready and two file-decision albums.
- This comparison exposed a read-model inconsistency: a verified manual assignment could still display as uncertain. A follow-up correction treats it as settled and allows harmless dependent metadata through readiness checks, while retaining independent title/version, numbering and audio-conflict decisions. All 674 affected backend regression tests passed.
- The final correction was deployed as `localhost/soulsync:test-7f6a28a1a6a4`, healthy 1/1. Targeted reconciliation completed for both affected albums without errors: Bastille has zero uncertain assignments; Motionblur has 26 confirmed tracks and zero uncertain assignments. The ready batch remains exactly 123 albums / 558 actions.
- An additional 12 deployed browser combinations (both affected albums, three tabs, desktop/mobile) passed with no JavaScript errors or horizontal overflow. Final rollout logs showed no errors or tracebacks, and all 34 pending approvals remained valid.
- Automatic music downloads were restored to their original enabled state after active transfers finished and the rollout completed. Flux remains suspended only for the SoulSync feature-review image. No live user plan has been applied.
- The implementation is ready for focused PR review. Remaining follow-ups are the intermittent download-storage issue and further performance work on the shared app bundle / large evidence payloads; the Operations redesign remains outside this PR.


## Adversarial review corrections (2026-10-18)

A subsequent review found four gaps in the preceding readiness assessment. These
are corrected in the current source:

- Full provider enrichment now checks accepted identity fields at the tag-write
  boundary, including recording/release IDs and dates that were absent from the
  original retag proposal. Independent BPM and genre enrichment remains available.
  Legacy release-date and track-count field aliases use the same ownership rules.
- Queue choices, pins, reopening and legacy mutations share the maintenance lock.
  Each batch write revalidates the remaining whole-album plan while holding that
  lock. A new unanswered decision stops that album, and unrelated albums continue.
- A verified unique assignment relocation updates the stored assignment path,
  so reset, slot ownership and legacy protection all refer to the same file.
  Concurrently removed or changed decisions are not recreated by stale scans.
  Authorized tag edits/renames also refresh assignment file evidence; stale
  assignments are not silently refreshed.
- Fulfillment relocation merges conflicting destination records by update time,
  with an explicit progress rule for equal timestamps. A newer publication is
  retained, as are newer cancellations and deliberate re-requests. Profile and
  selected-release boundaries still apply; completion intent is unchanged.

Regression coverage includes real FLAC writes preserving accepted recording ID,
release ID and date while adding genre/BPM; reset and legacy protection after a
move; a concurrent release-pin change; a new unresolved decision between batch
writes; five fulfillment-state collisions; and assignment survival after an
approved tag edit and rename. Mutating tests continue to use disposable data.

### Requirements recheck

| Requirement | Validation in this correction round |
| --- | --- |
| Decisions are saved before file changes; album-wide choices precede dependent edits | Queue/API and real-file lifecycle suites pass; mid-batch invalidation now has a regression test. |
| Accepted queue decisions govern affected fields across legacy jobs | Provider write-boundary tests, canonical/assignment guards, shared-lock concurrency test and date/count alias tests pass. |
| Global apply contains fully decided albums, including approved file actions | Whole-album selector and background-batch tests pass; deployed preview retains 123 albums / 558 actions, including one approved replacement. No plans were applied in production. |
| Harmless metadata stays eligible, while identity conflicts require approval | Readiness suites pass; the earlier Oceanic fingerprint-failure exclusion remains present. |
| Cancellation does not change completion intent | Fulfillment cancellation and exact profile/release tests pass; relocation collisions preserve the newer acquisition state. |
| Decisions and acquisitions survive moves, and can be reconsidered | Persisted relocation/reset tests, real tag-write/rename assignment test and fulfillment merge tests pass. Changed audio/evidence is not silently approved. |
| Failure and restart do not replay destructive actions | Background failure isolation and interrupted-operation tests pass. |
| Consistent UI, playback, evidence and notifications; responsive queue access | 670 Review/Tools tests and 39 deployed album/tab/viewport checks pass. During reconciliation, three-request medians were 0.205 s for summary and 0.363 s for list; these are observations, not a throughput guarantee. |
| Focused PR, with existing maintenance tools retained | No Operations redesign or removal of specialized jobs is included. |

The final selected backend run passed 2,204 tests (165 existing deprecation
warnings). One unrelated BPM audio-analysis test module was excluded because
the local environment lacks NumPy. A bulk-reopen test fixture was made portable
across SQLite builds; its optional `UPDATE ... LIMIT` syntax had prevented the
test from reaching application code. No frontend source changed in this round.

### Deployed correction-round result

- Code commit/image `3173e9e7e6e9` is healthy (1/1). Automatic music downloads
  were restored; SoulSync alone remains in the existing Flux-suspended feature
  test workflow. No user repair plan was applied.
- Full reconciliation run 842 completed in 1,562.56 seconds: 13,835 files across
  1,602 folders, zero errors and zero automatic fixes. One new unresolved-release
  finding concerns Sawyer Hill's “Folsom Prison Blues”; the other new folder is
  a fully matched seven-track *Heartbreak Hysteria*. These account for eight new
  files and the increase from 883 to 884 queue cases.
- Final modes: 759 need input, 123 ready, two file-decision albums, zero waiting.
  The ready batch retains exactly the same 558 IDs, including one approved file
  action. All 34 pending approvals remain valid. All selected source stat checks
  pass, with no occupied or duplicate rename destinations.
- All five relocated motionblur assignments now have persisted current paths and
  matching file evidence. Seven of eight stored assignments match their files;
  the remaining pre-existing changed-file assignment was correctly not refreshed
  without evidence. No stored assignment points to a missing path.
- No previous logical album plan disappeared. The only changed existing semantic
  plan was Queen's clear-match counter, 49 → 50, now including its manual Headlong
  assignment. Changing only that counter back reproduces the exact old plan hash;
  assignments, proposed edits and coverage otherwise remain unchanged.
- All 39 deployed desktop/mobile screen checks passed. An interrupted check was
  traced to a Kubernetes port-forward stream timeout; direct pod requests remained
  responsive, and the check passed through a replacement tunnel. Provider 404s
  remain visible in logs; the reconciliation job reported no errors.
- Runtime evidence strengthens the proposed performance work: 13,801 fingerprint
  hits, 13,801 AcoustID hits and 13,835 tag-cache hits; only 34 fingerprints and
  eight lookups were computed. The scan considered 9,949 releases. Inventory,
  fingerprint and lookup timers reported 8.5, 7.4 and 1.7 seconds respectively;
  most of the 1,561.6-second scanner time remains unattributed between catalogue,
  pacing, matching and persistence. It is not valid to assign that remainder to
  any single stage without the proposed instrumentation.

## Proposed performance implementation plan — awaiting review

Performance is part of feature readiness. Implement the first three milestones
as a focused performance series before proposing the feature upstream, with each
milestone independently testable and revertible. Whole-album result reuse is a
fourth, evidence-dependent milestone. This plan is not implemented by the
correctness commit above.

### 1. Attribute scan time and establish a reproducible baseline

**Locations:** `core/repair_jobs/library_reconciliation.py`,
`core/library/reconciliation_evidence.py`, `core/musicbrainz_client.py`, and the
existing repair run/progress reporting contract.

- Add a run-scoped metrics collector for catalogue lookup, rate-limit waits,
  retry backoff, candidate matching, assignment preparation, finding persistence,
  plan persistence and total wall time. Retain the existing inventory/audio/tag
  measurements. Distinguish inclusive from exclusive timers so nested calls do
  not make the totals appear additive when they are not.
- Count requests by provider and operation; record memory/persistent hits,
  misses, expired entries, confirmed negatives, transient failures and shared
  in-flight requests. Keep a bounded list of the slowest album stages; avoid
  track IDs, paths and unbounded labels in aggregate metrics.
- Instrument MusicBrainz pacing and transport separately at `_get` and
  `_wait_for_musicbrainz_slot`. Pass an optional observer/context through the
  reconciliation call chain; do not charge other background jobs to this run.
- Persist one compact summary with the completed run and expose it through
  existing run details/progress. Include partial metrics on cancellation and
  provider failure. No new Operations dashboard is needed.
- Capture an unchanged-library baseline: full scan and targeted Bastille,
  motionblur, Oceanic, Queen and a multi-edition case. Run targeted cases at
  least three times; record warm/cold cache state, provider/server configuration,
  concurrent jobs, request counts and browser/API latency.

**Gate:** deterministic tests account for simulated network, pacing, retry and
matching time without double counting; metrics survive early exit. The baseline
identifies the dominant stages. Instrumentation adds less than 5% to a local
replay benchmark and does not change findings or readiness.

### 2. Reuse one catalogue snapshot during each analysis

**Locations:** `core/metadata/album_tracks.py`, `core/musicbrainz_search.py`,
`core/repair_jobs/library_reconciliation.py`; add a small run-scoped catalogue
access object rather than another global mutable dictionary.

- Introduce an internal release snapshot containing metadata, complete normalized
  tracks and release-group identity, with provenance and response completeness.
  Adapt the existing tracklist/metadata functions to consume that snapshot while
  retaining their public return shapes for other callers.
- For MusicBrainz, request the standard rich release response once (recordings,
  artist credits, release groups and labels). Derive both consumers from it;
  edition expansion reuses its group ID. Fetch group editions separately only
  when needed. A partial response cannot satisfy a richer request.
- Keep known release and release-group identifiers typed through the adapter so
  an exact release does not first probe the release-group endpoint. Preserve
  existing bounded discovery, pagination and video-track filtering.
- Add per-key in-flight request sharing. Release the key on failure/cancellation;
  never hold a database transaction or global maintenance lock during catalogue
  fetches. Different albums can use different keys independently.
- Hold the selected response stable for the duration of one analysis, even if
  its persistent TTL expires midway. Consumers receive immutable views or copies
  so normalizing tracks cannot alter metadata used elsewhere.

**Gate:** a release used for scoring, evidence and edition expansion produces one
rich release fetch per run, including concurrent consumers. Tests cover differing
include sets, exact release versus group, paginated editions, provider fallback,
partial/malformed responses and mutation isolation. Existing semantic plans,
proposal values, readiness and approval tokens remain equivalent on frozen input.

### 3. Persist bounded catalogue responses across runs and deployments

**Locations:** a dedicated catalogue-response module/table, database schema
migration, the catalogue adapter from milestone 2, `core/musicbrainz_client.py`
for structured outcomes, and `core/repair_jobs/cache_evictor.py` for maintenance.

The existing `core/metadata/cache.py` is a useful lifecycle reference, but its
entity key lacks server and requested-field identity, its TTL is day-based and
it writes access counters on each hit. Do not overload that key or make every
reconciliation cache hit a SQLite write.

- Proposed key: cache schema version, provider, normalized configured endpoint,
  entity/operation type, identifier or normalized query, and canonical request
  parameters (sorted includes, pagination, market/language where relevant).
  Never store credentials in keys or logs. Include account/profile scope only
  where provider output actually depends on it; public MusicBrainz responses can
  be shared. A mirror change must produce a cache miss.
- Store validated JSON, fetched/expiry times, outcome, payload hash, byte size and
  last-access bucket. Use UTC epoch timestamps and second-resolution TTL checks.
  Add response-schema/parser versions so incompatible deployments cannot reuse
  incorrectly shaped entries. Normalized snapshots have their own version.
- Initial reviewable defaults: successful release/recording details 7 days;
  searches and edition listings 24 hours; confirmed 404 or valid empty search
  1 hour. Limit storage to 25,000 responses and 256 MiB, with a 2 MiB per-response
  ceiling; oversized responses remain usable but are not persisted. These are
  starting limits to validate against the measured working set (the current run
  considered 9,949 releases before counting discovery/group responses).
- Evict expired entries first, then least-recently-used entries in bounded
  batches. Coalesce access touches rather than writing on every hit. Use short
  transactions, and make corrupt entries/cache write failures ordinary misses.
  A cache problem must not prevent analysis or leave repair writes half-applied.
- Distinguish `success`, `not_found`, `empty_success`, `transient_error` and
  `invalid_response` before the current client wrappers collapse them to `None`.
  Never persist timeout, connection failure, 429, 5xx or malformed data as a
  negative result. Retain existing retry/backoff behavior. A failed refresh
  cannot overwrite a previously successful cache entry with an empty response.
- Expired data may remain stored for diagnosis, but does not silently become new
  ready evidence. If refresh fails, preserve the prior plan and mark analysis
  incomplete; do not retire its findings or manufacture missing tracks from the
  failure. This also needs coverage at the existing scanner finalization boundary.
- Normal “Reanalyze album” rereads current files and choices while reusing valid
  catalogue evidence. Offer “Refresh catalogue evidence” as an explicit force
  refresh for that album's release/discovery dependencies; do not clear the
  whole library cache or discard pins, assignments or completion intent.
- Initially enable persistent responses for the MusicBrainz path identified by
  the measurements. Other provider adapters retain their current behavior until
  their identity, pagination and account-dependent cache rules have tests.

**Gate:** tests cover restart persistence, TTL boundaries, server/market/include
isolation, 404 versus transient failure, failed refresh preserving plans,
corrupt JSON, eviction/byte caps, concurrent fetches and cache-write failure.
An unchanged warm scan makes zero upstream requests for eligible unexpired keys;
request sharing prevents duplicate misses. Force refresh changes only the intended
catalogue dependencies. A changed proposal invalidates its old approval.

### 4. Consider skipping unchanged album matching only after measurement

Implement only if matching/persistence remains a substantial measured cost after
milestones 1–3. Start with reusing the pure matching result, while still checking
current inventory and deriving current plan state; do not initially skip the
entire album lifecycle.

- Build a versioned signature from profile and physical album membership,
  file stat/audio/tag evidence, relevant library metadata and locks, selected
  provider/release and catalogue hashes, manual assignments, pins, numbering and
  completion intent, wanted slots, saved/reopened decisions, playlist constraints,
  fulfillment state and publication/discovery revision, relevant settings, and
  matching-policy version. Include absent-slot dependencies so newly published
  files invalidate a previously incomplete album.
- Moves, split/merged folders, deleted files, reanalysis after a download,
  cancellation, changed provider data and upgraded matching policy must recompute
  their affected results. Cancellation still leaves completion intent unchanged.
- Retain live projection of transient job/wishlist status and approval validity.
  Do not replay old finding writes or use cached “ready” flags as authority.
- Gate on signature-parity/invalidation tests for each dependency and a fallback
  to ordinary analysis on any uncertainty. Exclude this milestone if measured
  benefit is small compared with its invalidation complexity.

### Performance and correctness acceptance / rollout

- Run cold and warm analyses against the same captured inventory/catalogue
  fixtures and compare normalized plans, pending finding IDs/values, ready modes,
  accepted decisions and fulfillment states. Ignore only documented bookkeeping
  timestamps and expiring media URLs. Explain every live-data difference.
- Report full-scan wall time, targeted p50/p95, catalogue calls and wait time,
  matching/persistence time, cache bytes and peak memory. Proposed targets:
  at least 90% fewer eligible catalogue calls on warm scans; at least 50% less
  warm full-scan wall time where catalogue work dominates; targeted warm album
  reanalysis p95 under 3 seconds for ordinary single albums. Confirm or revise
  those latency targets against milestone 1's measured non-catalogue floor.
- Queue list/summary latency must not regress by more than 10% in matched replay;
  request-count gates and semantic equivalence are deterministic CI checks.
  Live latency targets are deployment measurements, not flaky unit assertions.
- Keep all existing apply-time file, fingerprint, pin, evidence, playlist and
  destination checks. The analysis cache must not bypass them. Revalidate the
  accepted plan before mutation and do not substitute refreshed proposal values.
- Commit and test each milestone; deploy the combined approved performance pass,
  run a cold/full then warm/full reconciliation and targeted cases, verify all
  saved approvals and queue totals, and rerun deployed desktop/mobile checks.
  Do not apply live user plans to benchmark mutations. Retain a cache-disable
  escape hatch and backwards-compatible schema for rollback.
- Review decisions requested: approve milestones 1–3 as the next focused pass;
  confirm the proposed TTL/storage defaults and force-refresh control; decide
  whether milestone 4 is in scope only after the measured results are available.
