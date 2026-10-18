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
- During rollout preparation, real downloads encountered slskd ENOSPC errors and a stale completed-download mount. No storage changes or wishlist cancellations were made for this verification. Successful live acquisition could not be demonstrated; publication, cancellation and exact assignment were exercised in integration tests.

### Full reconciliation and final reassessment

- Run 841 completed all 13,827 files / 1,600 folders in 1,921.68 seconds, with zero errors, zero automatic fixes and zero new findings.
- The ready batch retained exactly the same 558 action IDs across 123 albums, including one approved replacement. All 34 pending approvals still matched their evidence; a repeated filesystem audit found no changed sources or conflicting rename targets.
- All 1,567 logical album plans remained present. Comparing assignments and proposed changes, excluding inventory bookkeeping and expiring preview URLs, found changes only in Bastille's *Bad Blood X* and *motionblur*: existing manual assignments were restored. Motionblur's five accepted assignments no longer produced unresolved findings. Final totals were 883 albums: 758 needing input, 123 ready and two file-decision albums.
- This comparison exposed a read-model inconsistency: a verified manual assignment could still display as uncertain. A follow-up correction treats it as settled and allows harmless dependent metadata through readiness checks, while retaining independent title/version, numbering and audio-conflict decisions. All 674 affected backend regression tests passed.
- The final correction is being verified through targeted deployed reanalysis of the affected albums. No live user plan has been applied.

