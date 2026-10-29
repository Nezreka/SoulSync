# Reconciliation implementation history

Historical plans, measurements, and reviews are retained here for provenance. Commands, filenames, deployment images, and unfinished items below describe their original dates. Use [architecture](architecture.md), [edge cases](edge-cases.md), and [testing](testing.md) for current guidance.


---

## Original report: library-reconciliation-review-queue-2026-10-05.md

# Library reconciliation and review queue design (2026-10-05)

Status: implemented as an opt-in first working slice on `feat/library-reconciliation-review-queue`. The reuse analysis below was checked against `upstream/dev` at `4d45b9ed4` (2026-10-04). The sections headed “Proposed” record the design target; see the implementation notes at the end for the shipped boundary.

## Goal

Give a collector a reliable way to return an existing library to a clean baseline. Scan each directory's actual audio, compare it with the intended release and wanted tracks, propose repairs with the supporting evidence, then review and apply those repairs album by album. Tags, filenames, and folder names are clues, not proof. AcoustID is stronger evidence when it has a confident, unambiguous result, but it is not a guaranteed recording or release identifier.

The intended review experience is a full-width queue. It presents one actionable issue at a time with its album context, shows why the recommendation was made, lets the user apply or change it, and advances through that album before moving to the next. The existing maintenance job controls remain available for scanning and configuration.

## Reconciliation pipeline

1. **Inventory once.** Walk every shared and personal library root; join physical files, DB tracks, playlist membership, manually tagged or locked metadata, and known acquisition intent in bulk. Include files missing from the DB and DB rows missing from disk. Keep library owner/root in every identity key.
2. **Collect evidence.** Read audio duration and embedded tags/IDs, filename, folder, and stored DB metadata. Reuse a cached fingerprint and AcoustID candidate set when the audio is unchanged. Preserve `ok`, `no_match`, `unsupported`, and error states separately. Do not treat a missing result as evidence that the tags are correct.
3. **Generate bounded release candidates.** Start with explicit album/release IDs, acquisition intent, validated IDs from files, and the dominant recording candidates in the folder. Fetch exact release tracklists once per candidate. Expand to alternate editions only if fit is poor or ambiguous. Mixed compilations, singles, bonus discs, live sets, and folders containing more than one release need explicit handling; a simple majority cannot establish the release alone.
4. **Score the folder against each candidate release.** Compare recording candidates, title and artist (including meaningful version qualifiers), duration, disc/track position, count, and intended album. Keep conflicting evidence and alternate explanations visible. Select a release only when the evidence distinguishes it; otherwise request review.
5. **Assign files to release slots jointly.** Build candidate file/slot edges, reject implausible edges, then choose a one-to-one assignment for the album. An AcoustID recording can appear on multiple releases, so release choice and slot choice must be evaluated together. Do not let an early duration or track-number match consume a slot that another file clearly fits. Uncertain assignments remain uncertain.
6. **Compare actual, intended, and missing.** A missing release slot is not automatically a wanted download: distinguish a complete-album goal from playlist-only acquisitions, partial editions, and tracks already present elsewhere. A file outside the inferred release can be a valid standalone acquisition, a misplaced file, or unwanted audio. Check global copies and playlist references before proposing duplicate removal.
7. **Create a reviewable plan.** Show tag and filename edits, moves, recoverable removals, wishlist additions, and intentional exceptions as distinct actions with exact targets and preconditions. Reuse existing repair writers and quarantine handling only after the plan is approved. Recheck changed files, locks, destination collisions, and related findings at apply time; record each result and refresh the affected album.

Never silently delete a file, infer a wanted album solely from a folder majority, or retag a wrong recording into a second copy of a song the library already has.

## Existing code to reuse

| Concern | Existing implementation | Use and boundary |
| --- | --- | --- |
| Library traversal | `core/repair_jobs/base.py` (`all_library_roots`, `walk_library`), `core/library/path_resolver.py` | Covers shared and own roots and ignores SoulSync staging/quarantine. Build one inventory rather than running several scanners. |
| Folder/tag parsing | `core/auto_import_worker.py` (`FolderCandidate`, tag and directory parsing) | Reuse parsing helpers. Its current first-success identification and three-file AcoustID fallback are import heuristics, not reconciliation proof. |
| AcoustID | `core/acoustid_client.py::lookup_with_status`, `core/acoustid_verification.py`, `core/matching/audio_verification.py` | Reuse structured outcomes, candidate comparison, ambiguity and version logic. Factor evidence retrieval from expected-title verification so a file is fingerprinted once. |
| Release discovery | `core/metadata/canonical_resolver.py`, `core/metadata/album_tracks.py`, `core/imports/exact_id_discovery.py` | Reuse candidate discovery and tracklist fetching. Keep exact release/recording IDs in the normalized tracklist; the current default resolver output drops some IDs. Do not persist a canonical pin before review. |
| File/slot scoring | `core/imports/album_matching.py`, `core/repair_jobs/album_completeness.py`, `core/text/`, `core/matching/` | Reuse normalization, duration and identifier scoring, and missing-track payloads. Replace the import matcher's early position dedup and greedy final assignment for this use case. The separate canonical title/duration assignment work may supply the global matcher after it lands. |
| Duplicates and intent | `core/library/duplicate_rules.py`, `core/library/duplicate_keep.py`, `core/library/track_identity.py`, playlist membership in `JobContext` | Reuse version/number safeguards and keeper preferences. Bulk-load all relevant copies; `find_library_track_by_external_id` returns only one row, and metadata buckets alone cannot prove duplicate audio. |
| Repairs | `core/library/retag_planner.py`, `core/repair_jobs/library_retag.py`, `core/library_reorganize.py`, `core/repair_worker.py`, `core/library/deleted_quarantine.py` | Reuse per-field tag diffs, path preview, job lifecycle, finding status, and recoverable removal. Do not call whole-library jobs again for each album. |

Important boundaries: the persistent album MBID cache in `core/metadata/album_mbid_cache.py` removes edition qualifiers from its key, so it cannot be an exact-edition authority. `core/imports/file_ops.py::safe_move_file` uses `os.replace` and can overwrite a destination; reconciliation needs collision checks and a no-overwrite move. The import matcher deduplicates by position before proving audio identity, then uses descending greedy matches. The album completeness matcher also relies on potentially wrong DB tags/IDs. Reuse their components, not their final decisions.

## Performance contract

- **One scan, one joined inventory.** Load DB rows and playlist/intent data in batches. Index by physical path, owner, album, recording ID, and fingerprint candidates in memory. Do not issue one global duplicate query or one album query per file.
- **Cache audio evidence.** Separate fingerprint generation from AcoustID lookup without changing the fingerprint algorithm. Retain the fingerprint and structured lookup candidates with provenance/version. File size and modification time provide a cheap skip hint; verify identity again before a destructive or path-changing action. A move or tag-only edit should not force another AcoustID request. Retry errors with backoff; never cache them as permanent no-matches.
- **Bound network work.** Cache exact release tracklists by source and release ID; deduplicate requests within a run. Cap alternate-release expansion, honor configured metadata source/mirror and provider throttling, and send unresolved cases to review instead of searching indefinitely.
- **Match locally.** Build sparse candidate edges from strong IDs, reasonable duration windows, and title/artist matches. Global assignment is cheap at album scale relative to fingerprinting and network calls; measure before optimizing it. Preserve ambiguity rather than resolving ties by input order.
- **Resume and refresh narrowly.** Checkpoint per folder, persist report inputs/version, and recompute only affected folders and cross-album/global-copy relationships after applying a plan. Page queue summaries and load full evidence for the active album only.
- **Instrument.** Count files walked, fingerprints computed/reused, AcoustID calls/errors, release fetches/cache hits, candidates scored, match time, and apply failures. Use those numbers to tune concurrency; bound local fingerprint workers separately from rate-limited remote calls.

## Why the current results UI needs a different read model

The current simple screen in `webui/src/routes/tools/-ui/operations-studio.tsx` shows an album grid and opens `album-inspection-tray.tsx`. Advanced mode in `maintenance-hero.tsx` mounts `findings-surface.tsx`, with grouping by type, album, or artist. This is useful for surveying the backlog and operating individual jobs, but it does not present one decision with all its evidence and a clear next step.

The existing `/api/repair/findings/albums` in `api/repair.py` groups by artist and album **text** extracted from finding details, orders largely by audio quality, and omits rows without both names. This can merge different editions or owners and can miss untagged/wrongly tagged files. The tray searches findings by text with `limit: 100`, filters client-side, and falls back to the unfiltered results when none match. Thus it cannot be the authoritative album membership query. Its album-wide fix loop catches individual failures and can still announce success. The new queue must use stable identities, exact server-side membership, and actual apply results.

## Proposed review surface

Use a dedicated full-width **Review Queue** page reachable from the maintenance overview and from a finding/album deep link. Keep simple/advanced as ways to inspect and run maintenance jobs; let both lead to the same queue. The unit of navigation is an **album case** (or an explicit unassigned-files case). Inside it, advance through **decisions**, which may combine several raw findings when they describe the same problem: two swapped titles, an extra wrong-audio file plus a missing wanted slot, or duplicate candidates. Do not present those as independent fixes that can contradict each other.

Suggested layout:

```text
Queue: 17 albums need review · 3 blocked · 42 ready     [filters] [search]
← Previous album       Artist — Album [release/edition]       Next album →
Album context: intended release, 10/12 present, 2 missing, 1 extra; source/pin

Decision 2 of 4: Track 7 appears to be a different recording
Recommendation: Move this file aside and request the intended track
Why: AcoustID candidates, duration, filename, tags, expected release slot
Other plausible explanation / uncertainty; full evidence and provenance

Current file  |  Intended slot  |  Other copies / playlist use
path, tags,   |  title, IDs,     |  where found, quality,
length, IDs   |  length, edition |  references

[Preview plan] [Apply recommendation] [Choose another action] [Defer]
Result / error / undo or recovery link                         Next decision →
Album tracklist: matched · uncertain · missing · extra (expandable)
```

The recommendation is a typed, evidence-backed plan, not a free-text command or a blanket “resolve album” button. Display a short reason, confidence *category* (clear, likely, ambiguous, unavailable), conflicting signals, and precise consequences. Show AcoustID alternatives and scores, recording/release IDs, current tags, filename/folder, duration deltas, intended playlist/album origin, and other library copies when relevant. A title variation may be harmless; version annotations, combined tracks, bonus tracks and live recordings can be material. Let the user inspect the complete tracklist without losing their place. Playback can be offered where an existing playable file endpoint supports it.

Actions should include apply recommendation, select another supported resolution, mark as acceptable/ignore with a reason, defer, and revisit/undo where supported. A successful action advances automatically within the album. Errors keep the decision in place with the actual failure. When an action changes other decisions, refresh their evidence and mark stale recommendations for reconsideration. Dismissal must not be mistaken for a successful repair. Keep the prior decision reachable through history.

Queue ordering should favor blockers and high-confidence fixes before ambiguous cases, but never hide ambiguous or unnamed files. Allow filters by album, issue type, severity, confidence, status, and owner. Persist progress across sessions; direct links should reopen the same album/decision. Support keyboard navigation with explicit focus and screen-reader labels; do not bind destructive actions to a single accidental keypress. Small screens can stack the evidence panels, while desktop gets the large comparison view.

## API and state proposal

Keep `repair_findings` and existing fix/dismiss endpoints for legacy jobs. Add a queue read model that assembles related raw findings and new reconciliation evidence without rewriting every scanner. A case needs a stable ID based on library owner plus a release ID where known and physical folder/cluster identity where not; artist/album strings are display fields. Each decision should carry source finding IDs, affected file IDs/paths, evidence version, supported actions, recommended action, dependencies, status, and last apply result. Exact membership must be queried on the server and paged as needed.

Possible endpoints: `GET /api/repair/review-queue` for paged case summaries, `GET /api/repair/review-queue/<case_id>` for full album evidence, `POST /api/repair/review-queue/<case_id>/decisions/<id>/preview`, and a corresponding `apply` endpoint carrying an idempotency key and evidence version. These names are provisional. Legacy findings can initially offer the current per-finding actions through an adapter; multi-file reconciliation actions need their own planner/apply path. Return partial failures by action, never a generic success for an incomplete album. Apply should validate current file state, locks, owner, and destination; journal completed operations and provide quarantine/restore references.

## Implementation sequence

1. Build the stable queue case/detail API over existing findings, including exact album membership, unassigned findings, paging, statuses, and provenance. Fix the tray's false-success path as part of moving actions into a shared typed action layer.
2. Build the full-width queue UI using existing finding detail renderers, prompts, API calls, and job progress where they fit. Launch from both simple and advanced screens. Verify navigation, failure handling, and persistence with realistic albums containing multiple issue types.
3. Add the inventory/evidence/cache pipeline and release/slot planner above. Feed its cases into the same queue. Start read-only; validate ambiguous editions, swapped tags, mixed folders, duplicates, combined tracks, and missing intent before enabling apply.
4. Add guarded, idempotent multi-action apply and targeted refresh. Roll out low-risk tag/path corrections first, then recoverable removals and wishlist requests after end-to-end checks.

Open design choice for implementation: where to persist *intent* that an album must be complete versus a playlist track only. Existing tags and folder membership cannot answer this reliably; use acquisition history where available and ask once when it is absent. Also decide whether accepted exceptions expire when audio or release evidence changes; an exception tied only to title text is too broad.

## Implementation notes

The `library_reconciliation` job is opt-in and creates findings without editing files. It walks all library roots, merges conventional disc subfolders, and keeps path/stat fingerprints and successful AcoustID lookups in SQLite. An exhausted lookup skips only its folder, so a file-specific AcoustID error cannot block the rest of the library. Three consecutive exhausted failures open a circuit for the run: cached evidence remains usable, folders that need a new lookup are preserved, and stale findings elsewhere can still retire without hammering an unavailable service. It compares at most six exact release candidates per folder with a one-to-one assignment. A strong fingerprint contradicting both tags and filename blocks that slot. Uncertain pairs, mixed folders, ambiguous releases, and unavailable identities remain review findings. An interrupted or otherwise partial scan leaves existing proposals intact.

The `/review` page lists exact folder/album cases from existing and new repair findings, shows evidence, and applies one finding at a time through the existing handlers. Stale evidence tokens, changed audio, changed pinned releases, locks, newly arrived files, and filename collisions reject actions. The page can queue a reconciliation scan and refresh the queue. The job's new actions cover a selected release, complete-versus-partial album intent, audio-backed tag/MBID, album-title, and filename corrections, exact-ID wishlist requests, and recoverable quarantine for a reviewed unassigned file. Filename changes preserve familiar track-number prefixes and update the library row; harmless title flavor is left alone. Grouped bulk fix excludes these review-only actions.

The current implementation deliberately leaves uncertain or mixed folders for manual review. It does not automatically identify an intended edition without a usable source/release ID or MusicBrainz recording releases, infer complete-album intent from folder completeness, or move an unassigned song into another release. A lone track referenced by a current media-server playlist is treated as a partial acquisition for that scan; this does not persist an album-wide intent. Audio-proven competing local copies are combined into one explicit keeper decision and removed copies use the existing recoverable duplicate quarantine. Other duplicate, reorganizer, and playback decisions remain with their existing tools. Accepted reconciliation exceptions are reopened when their audio fingerprint, release ID, or folder evidence signature changes. This boundary avoids guessing a wanted track or removing a unique recording from insufficient evidence.

The post-rebase review added guards for a locked release that scores worse than another edition, tied AcoustID identities on unassigned files, competing copies for one release slot, and MusicBrainz release-group requests that need the concrete release ID. The review queue also changes its active case when search or pagination changes.

AcoustID recording links tied at the same score are evaluated in album context rather than displayed or trusted in API order. A tied candidate matching the assigned release slot can be corroborated by the measured duration, embedded ID/title, and filename; conflicting links remain visible as alternatives without making an otherwise exact assignment ambiguous. Conversely, when a file's measured duration is incompatible with its labelled slot and fits another fingerprint-supported slot, stale metadata cannot consume the labelled slot. Joint assignment then reports the extra copy and the genuinely missing track. This preserves the evidence hierarchy without treating AcoustID's unordered MusicBrainz links as a ranking.

The deployed library can expose one file through different container mount paths, such as a stored `/music/...` path and a scanned `/app/Music/library/...` path. Inventory joins now retain both aliases only when the operating system proves they are the same physical file. This restores album IDs, artists, canonical releases, and playlist membership without joining coincidentally identical relative paths from separate profile libraries. When several release candidates produce the same track mapping, the stored album release wins an equal fit; candidates with indistinguishable mappings no longer manufacture a manual release decision. The review evidence collapses repeated MusicBrainz recording IDs with the same normalized title and artist while preserving genuinely different titles and version qualifiers.

The first full-library run exposed five additional boundaries. Reconciliation excludes `.watchlist-removal-*` recovery snapshots from the active inventory. Tied AcoustID identities no longer force review when the selected release slot, tag title, filename and close duration agree and the competing identity fits no other release slot. The precise remaining ambiguity reasons are stored with the finding. MusicBrainz recording disambiguation from #1539 and #1541 participates in file/slot version matching, release-candidate shape, duplicate compatibility, metadata repair and review evidence; vocal, instrumental, live, acoustic and other marked versions cannot become one keeper decision merely because they share a bad external ID. Completion intent is deferred while an album still has ambiguous assignments or extra files, so a bad edition mapping is resolved before asking whether its apparent missing tracks are wanted.

The 2026-10-07 tuning pass follows Picard's conservative clustering pattern without copying its broader release guessing. Folders already attached to one library album are audited as one logical album. A small unlinked soundtrack fragment may join a single large, source-linked soundtrack only when owner and normalized title agree, the anchor's album title, record type, or one of its existing folder names explicitly identifies a soundtrack, and every fragment position is unused; two possible anchors, a linked fragment, an overlapping position, or a non-soundtrack anchor prevents the merge. The exact release tracklist and one-to-one audio assignment still decide each track. A clear reviewed fragment repair can reassign its library album row, while physical file moves remain outside this action.

Albums without a usable release candidate get one bounded MusicBrainz title-and-artist search: four results, an exact normalized title, and a search score of at least 90. Tracklist fit still selects the release, and a tie still requires review. AcoustID ties can now be resolved by the full assignment when the competing slot has another local file with top-score audio support, two independent naming/ID signals, a close duration, and at least as much pair weight. This keeps a real unowned alternative ambiguous. Visible version text is preserved when MusicBrainz recording disambiguation confirms it, complete per-track artist credits are retained, and track artist is repaired separately from album artist. A recording-disambiguation-only database change no longer creates a review card.

Embedded tags now have the same size-and-mtime cache boundary as fingerprints. Run metrics report tag reads/hits and wall time spent in inventory, fingerprinting, AcoustID lookup and tag parsing. A local decoder/fingerprint failure leaves that file uncertain and no longer hides its siblings; an AcoustID service failure still preserves the affected logical album for a later run.

The next queue pass adds an album-level action plan over the raw findings. Every pending item is classified as a supported metadata repair, a manual choice, a recoverable file action, or blocked by a release choice. Release choices block dependent metadata, disc/track-number changes always remain manual, and only the supported subset can enter the new background bulk action. The queue shows these groups and blockers before individual evidence, offers an inspectable per-album action and an all-ready action, and can enqueue the existing tag-based organizer after a fully unblocked album applies successfully. Existing evidence tokens and apply-time file, release, lock, and collision checks still guard each write.

Release comparison now expands a bounded MusicBrainz release group when the current edition has missing/extra audio, systematic numbering drift, or multiple discs under the default digital numbering mode. An otherwise equal continuous single-medium `1..N` tracklist is preferred; `numbering_mode=release` retains linked release/per-disc numbering. Alternate tracklists remain capped and cached. Equally fitting editions with materially different tracklists or positions produce a release choice even when one unlocked edition was previously stored. Conventional `Disc N` descendants split into a separate library album row join their physical parent when titles agree and release identities do not conflict; a release anchor permits damaged overlapping positions so the joint audio/release plan can expose duplicates and repair assignments instead of presenting two false albums.

The 2026-10-08 review pass persists one complete, versioned album plan after every successful comparison. Pending findings remain the write model, while the plan keeps confirmed assignments, selected release, playlist membership, intentionally uncollected partial-album slots, missing slots, extras, duplicates, and the exact proposed changes together for review. A completed scan retires stale plans; an interrupted scan preserves them. This restores the album context that was lost when the queue contained only exceptional findings.

Title drift is now classified as typography, harmless display flavor, preserved version detail, or a potentially material title/version conflict. Harmless flavor remains untouched as before, and the track matrix says so; material or conflicting version text cannot enter the safe bulk action. MusicBrainz edition discovery now runs proactively when the selected candidate uses noncontinuous physical-disc numbering in digital mode, even when the initial winner came from another provider. A MusicBrainz release or release-group seed can expand to a bounded, cached set of editions, but audio identity, assignment quality, missing tracks, and extras continue to outrank format preference.

The queue UI now treats repeated raw findings as album decisions. Metadata, title-convention, and numbering actions can be accepted or retained once per album through the existing guarded handlers. Category and risk filters operate server-side while returning the full matching album context. The detail view leads with decision cards and an issue-first track matrix; confirmed and intentionally uncollected slots remain available in a collapsed settled section. It identifies the selected release, numbering shape, acquisition intent, playlist membership, exact change fields, and successful assignments before the lower-level finding evidence.


---

## Original report: library-reconciliation-progress-review-2026-10-08.md

# Library reconciliation progress review (2026-10-08)

This review compares the current implementation with the original design in
`library-reconciliation-review-queue-2026-10-05.md`. It records intentional
departures, remaining gaps, and the visual hierarchy used by the review queue.

## Goal assessment

| Original goal                                                                                     | Current state                                                                                                                                                            | Assessment                                                                                      |
| ------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------- |
| Scan the physical library once and reason from folders, files, tags, database rows, and playlists | The reconciliation job inventories library roots by physical folder, merges conventional disc folders, includes playlist membership, and persists successful assignments | Strong                                                                                          |
| Cache audio identity and bound remote work                                                        | Fingerprints, structured AcoustID results, tag reads, release tracklists, release searches, and release-group expansion are cached or bounded                            | Strong; runtime remains about 11–12 minutes for 13,801 files and should continue to be measured |
| Select releases from album-wide evidence                                                          | MusicBrainz, Apple, and Deezer candidates are scored against the folder; exact IDs, disambiguation, format, track coverage, and digital numbering influence selection    | Strong, with deliberately unresolved catalogue-equivalent editions                              |
| Assign files jointly rather than greedily                                                         | Sparse file/slot candidates are solved one-to-one and successful assignments are persisted for review context                                                            | Strong                                                                                          |
| Distinguish missing tracks from acquisition intent                                                | Complete, partial, playlist-only, and unknown intent are separated; unknown intent creates one Album completeness decision                                               | Strong                                                                                          |
| Keep duplicate and destructive work explicit and recoverable                                      | Duplicate-copy decisions include playlist evidence and use quarantine-backed repair paths                                                                                | Strong                                                                                          |
| Review and apply album by album                                                                   | Stable folder/release cases, grouped decisions, full track assignment, guarded apply, stale-evidence checks, and album-scoped safe apply are present                     | Strong                                                                                          |
| Show one clear recommendation with supporting evidence                                            | The default Plan view states each outcome directly; Tracks and Evidence are separate disclosure layers, and selected-row detail stays beside its row                     | Strong                                                                                          |
| Refresh only affected folders after apply                                                         | The apply path refreshes the case, but the scanner still primarily operates as a full-library job                                                                        | Partial                                                                                         |
| Provide complete action history, preview, recovery, and undo in the queue                         | Finding state and quarantine recovery exist; a unified album-plan preview/history/undo surface does not                                                                  | Partial                                                                                         |
| Handle mixed folders and relocate unique recordings into the correct release automatically        | The scanner detects conflicts and avoids unsafe retagging, but it usually leaves cross-release relocation for review or another tool                                     | Partial by design                                                                               |
| Offer playback where useful                                                                       | Not integrated into the reconciliation queue                                                                                                                             | Not started                                                                                     |

## Intentional deviations

### Album overview before a single-finding workflow

The original sketch centered one decision at a time. Real albums showed that a
release choice can change numbering, missing slots, titles, and every per-track
action. The UI therefore opens on a Plan view with the album's ordered decisions
and supported bulk actions. The complete assignment matrix and technical
evidence are available through separate Tracks and Evidence views. Selecting a
track still reveals compact detail directly beneath that row.

This deviation is useful. It preserves the original queue and action model while
preventing users from accepting several apparently independent findings before
discovering the controlling edition conflict at the end.

### Safe bulk actions remain visible

The original design emphasized advancing after each action. The implemented
planner additionally separates evidence-supported changes from choices that
need judgment. Users can apply the safe subset for one album or the whole queue,
while still reviewing everything it would change.

This matches the practical goal of returning a large library to a clean baseline.
Safe actions must remain visually secondary whenever the current album has a
release, completeness, numbering, audio, or destructive decision.

### Ready albums stay in the queue

The queue contains both fully ready albums and albums needing decisions. This
allows inspection before bulk apply and explains exactly what organization would
change. Risk filters provide the fast path. Calling every row a case “needing
attention” was misleading, so the UI now calls them albums in the queue and
labels fully ready rows explicitly.

### Digital numbering became an explicit preference

The original design did not prescribe physical or digital numbering. Testing
multidisc releases established continuous digital numbering as the preferred
default when an equally correct digital release exists. Release numbering remains
an explicit album-level choice when the selected release and local convention
differ.

## Review-page attention hierarchy

The page should answer these questions in order:

1. **What kind of work am I doing?** Queue modes separate Needs input, Ready to apply, File actions, and All, with album counts.
2. **Which album am I reviewing?** Album and artist, queue position, and album navigation.
3. **What requires my judgment?** The Plan view orders release, completeness, numbering, audio, title, duplicate, and file decisions. Each card states the requested outcome in plain language.
4. **What can SoulSync safely do now?** Independent safe actions follow blocking decisions with a clear album-level apply button. On a fully ready album this is the primary panel.
5. **How does the album map track by track?** The Tracks view compares local file and release slot in one row, groups routine changes, and places exceptions first.
6. **Why is one row classified this way?** Selecting the row inserts compact detail immediately beneath it; exact changes and deeper evidence are disclosed on demand.
7. **What are the raw technical details?** The Evidence view holds source release, scope, numbering, candidate comparison, IDs, tags, paths, scores, and provenance.

Color communicates state rather than selection: green means supported and safe,
amber means judgment is required, red means a file or destructive consequence,
and purple is reserved for navigation and focus. Large tinted surfaces are kept
subtle so a busy album does not become a wall of competing status colors.
On narrow screens, track rows become stacked comparisons instead of preserving a
desktop-width table with horizontal scrolling.

## Remaining priorities

1. Add targeted post-apply rescans so a repaired album does not depend on a full run.
2. Add a unified plan preview and album action history, including recovery links.
3. Continue reducing false AcoustID ambiguity when embedded recording IDs and album evidence conclusively resolve unordered catalogue links.
4. Add explicit relocation plans for unique recordings found in the wrong album folder.
5. Expose scan cache and candidate metrics in operational diagnostics.
6. Consider playback only after the decision and evidence hierarchy is stable.


---

## Original report: review-queue-round3-assessment.md

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

## Performance implementation plan — milestones 1–3 approved

Performance is part of feature readiness. Implement the first three milestones
as a focused performance series before proposing the feature upstream, with each
milestone independently testable and revertible. Whole-album result reuse is a
fourth, evidence-dependent milestone. This plan is not implemented by the
correctness commit above.

### 1. Attribute scan time and establish a reproducible baseline

**Locations:** `core/repair_jobs/library_reconciliation.py`,
`core/reconciliation/evidence.py`, `core/musicbrainz_client.py`, and the
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


## Performance implementation: milestones 1–3

Implemented run-scoped catalogue timings/counters, one rich MusicBrainz release
response shared by analysis consumers, and a persistent response cache keyed by
server, entity and complete request parameters. Response storage uses 7-day
entity TTLs, 24-hour listing/search TTLs and 1-hour confirmed negatives, with
25,000-row / 256 MiB capacity limits and a 2 MiB individual response ceiling.
Maintenance is amortized during writes and enforced when the scan finishes.

Ordinary reanalysis reuses valid evidence; “Refresh catalogue evidence” bypasses
persistent entries only for requests made by that album. The setting
`repair.jobs.library_reconciliation.settings.catalogue_cache_enabled=false`
disables catalogue response reuse for diagnosis. Other providers and apply-time
guards retain their existing behavior. Whole-album matching reuse (#4) is deferred.

Album finding/plan publication is staged until its catalogue reads finish.
Transient or malformed responses do not become cached negatives, and an
incomplete catalogue read retains that album's prior plan and findings. Other
albums may complete; global stale-result retirement is withheld for an incomplete
scan. Metrics are exposed in run history and targeted refresh status, including
partial runs, and distinguish inclusive catalogue time from nested network,
pacing and retry timers. Cache hits do not write SQLite access counters per hit.

Initial verification: 995 backend tests, 670 Review/Tools tests, 110 focused
follow-up tests, frontend check with zero errors (551 existing warnings), and
production build passed. Deployed cold/warm measurements follow after rollout.

### Deployment measurements and validation (2026-10-18)

The comparison used the same 13,835 files across 1,602 folders, the configured
MusicBrainz mirror, and no concurrent repair job. Music automation was restored
after each deployment. These are live measurements rather than a frozen-server
benchmark. No user album plan was applied to produce them.

The previous full scan (842) took 1,562.56 seconds. The new cold scan (843) took
1,136.49 seconds, a 27.3% reduction. It made 8,800 upstream requests and reused
6,667 responses within the scan. Its inclusive catalogue time was 782.12 seconds,
including 679.08 seconds of network time and 8.43 seconds of rate-limit waiting.
Candidate matching took 255.42 seconds. Nested timers must not be added to their
inclusive parent timers.

The cold cache finished with 8,799 entries / 133,058,550 payload bytes (126.9 MiB).
One response exceeded the 2 MiB per-entry limit and was deliberately not stored;
it remains usable during analysis. Run-local raw response strings occupied
129.7 MiB, and the application worker's observed memory high-water mark was
about 1,013 MiB. The latter includes the application's existing caches and
parsed candidate data; it is not the cache module's isolated memory cost, and
no comparable pre-change memory measurement was captured.

An intermediate warm scan (844) exposed an overly strict completeness check:
MusicBrainz omits `tracks` for media explicitly declaring `track-count: 0`,
including unindexed vinyl editions and bonus DVDs. The scan retained prior
plans and marked itself incomplete. The validator now accepts that explicit
zero while rejecting unexplained omissions. Regression tests cover both shapes.
This incomplete run is not used as the final successful benchmark.

Three sequential targeted reanalyses per album, measured from API request to
completed refresh (including up to 300 ms polling granularity):

| Album | Previous median | Warm median | Warm maximum |
| --- | ---: | ---: | ---: |
| Bastille | 3.287 s | 1.652 s | 2.691 s |
| motionblur | 1.514 s | 1.121 s | 1.125 s |
| Oceanic | 1.079 s | 0.734 s | 0.748 s |
| Queen | 18.668 s | 14.828 s | 15.029 s |
| Magnolia | 2.110 s | 0.780 s | 0.797 s |

All 15 warm targeted runs made zero upstream catalogue requests. These samples
are too small to make a population p95 claim. Queen's last sample spent 12.23
seconds matching candidates and 0.46 seconds in catalogue access; it remains
above the proposed ordinary-album latency target. An explicit Oceanic catalogue
refresh fetched its three dependencies, reported `force_refresh: true`, and
left the shared cache's entry count and payload size unchanged.

Queue API medians during the cold scan were 329 ms (summary), 753 ms (list), and
1,077 ms (global apply preview). After scanning they were 229, 479, and 668 ms,
respectively (five requests each, inside the pod to exclude tunnel artifacts).
No matched pre-change API benchmark was captured, so these measurements do not
establish a percentage regression bound. A local alternating 12-track matching
replay measured 1.511 seconds without the timer versus 1.509 with it: no measurable
stage-timer overhead within sampling noise, with identical matching results.

Validation passed: 1,026 tests in the wider backend suites, 181 final focused
backend tests after the real-catalogue correction, 670 UI tests, frontend check
with zero errors (existing warnings remain), production frontend build, and 39
deployed album/tab/viewport checks without JavaScript errors or horizontal
overflow. Local Kubernetes port-forward resets required browser retries; direct
pod checks and restart counts confirmed the app remained healthy.

### Final successful warm scan and scope decision

After deploying the validator correction, run **845** completed in **400.30
seconds (6m 40s)**, with zero errors, zero new findings, and
`catalogue_incomplete: false`. This is **74.4% less wall time than run 842** and
**64.8% less than the cold run**. Its 15,467 catalogue accesses consisted of:

- 8,799 persistent hits;
- 6,667 run-local hits;
- one upstream release request for the deliberately uncached oversized response.

Thus all eligible unexpired keys hit the persistent cache after a deployment,
and upstream requests fell from 8,800 to one (99.99%). Storage remained 8,799
entries / 126.9 MiB. Inclusive catalogue time fell to 43.74 seconds, of which
1.18 seconds was network time. Candidate matching took 245.47 seconds (61.3% of
wall time), finding persistence 11.24 seconds, and plan persistence 12.75 seconds.
Plan publication/preparation timers are inclusive of assignment preparation and
persistence; their values are not additional independent costs.

Cold-to-final comparison found **no changes** in normalized plans, any of the
14,714 reconciliation findings, or any of the 59 saved choices. Normalization
excludes inventory bookkeeping, candidate presentation, and expiring preview
URLs; selected assignments and proposal values remain compared. All 34 pending
approvals still match their evidence. The global-ready batch remains exactly
123 albums / 558 actions, including the same one approved file action. All 558
action paths passed the read-only source/destination audit with no conflicts or
duplicate destinations.

Relative to the pre-cold baseline, the only changes were external library-index
progress: Heartbreak Hysteria gained a library album identity while preserving
its seven confirmed assignments, and Folsom Prison Blues gained a confirmed
one-track plan, retiring its earlier unresolved finding. This accounts for the
queue moving from 884 to 883 albums; ready membership did not change. Final album
modes reconcile: 758 needing input + 123 ready + 2 file decisions = 883.

**Milestones 1–3 are complete; milestone 4 is not implemented.** Measurements now
support prioritizing reuse of unchanged candidate-matching results. Matching is
the dominant warm-scan cost and explains Queen's remaining delay. Eliminating
all measured matching time would leave roughly 155 seconds of this run, an
upper-bound estimate rather than a promise: current inventory, decisions,
approval validity, fulfillment, and plan projection must still be processed.
A versioned input signature and the invalidation rules in milestone 4 remain
necessary before implementing that optimization.

Final deployed code: `7d2a4691c81b`, healthy 1/1. Music and video automation are
both enabled as before. Only the SoulSync test Kustomization remains suspended
under the established feature-review workflow. The subsequent documentation
commit does not require another application rollout.

## Matching-result reuse (milestone 4, 2026-10-18)

The optimization caches only the pure file-to-release matching result. It does
not skip album analysis. Every scan still inventories files, obtains current
catalogue evidence, discovers and ranks candidates, validates manual assignments,
and projects current pins, numbering, metadata, locks, playlist membership,
completion intent, wishlist fulfillment, findings, and ready actions. Existing
apply-time file, release, approval, and destination checks are unchanged.

The persistent key includes the ordered file and slot sequences, paths, sizes,
modification times, complete tag and audio evidence, the database identity fields
used by matching, profile/provider/release identity, validated forced pairs, and
a policy digest covering the matcher and its semantic helpers plus Python and
Unicode versions. Reordering files or slots cannot reuse stale numeric indices.
Changes to AcoustID candidates or scores invalidate results even when the audio
fingerprint is unchanged. Unrelated database timestamps and presentation URLs do
not invalidate matching; current values are still used by downstream projection.

There is no additional full-file byte hashing. The existing evidence layer's
file-change boundary remains path/size/mtime, with its existing apply guards.
This cache cannot detect a byte change deliberately hidden from that upstream
boundary. Nor does it freeze catalogue discovery: a refreshed tracklist produces
a different key, while an unchanged refreshed response can safely reuse matching.

`repair.jobs.library_reconciliation.settings.matching_cache_mode` supports:

- `off`: calculate every match and report timing, without cache access;
- `shadow`: calculate every match, populate misses, and compare hits against the
  complete fresh result, including confidence and explanations;
- `reuse`: return validated cached results and calculate misses normally.

A shadow discrepancy returns the fresh result, records an error diagnostic and
`shadow_mismatches`, and disables caching for the rest of that run. It is a
release-validation failure, not permission to publish the cached result. Invalid
payloads/checksums fall back to fresh matching and are replaced. Database failures
and unsupported input serialization also fall back; transient audio failures and
albums with failed catalogue evidence bypass caching.

Storage is disposable and bounded to 25,000 results / 128 MiB, 1 MiB per result,
with 30-day idle expiry and a 32 MiB run-local payload cache. Maintenance runs
periodically during writes, at scan completion, and in the existing cache
maintenance job. Cache output contains matching indices and explanations, not
approval/readiness state. JSON decoding restores integer competitor keys and
checks one-to-one assignments, complete file/slot partitions, and manual-pair
confidence. Consumers receive independent decoded objects.

Diagnostics distinguish automatic/manual matching, signature creation, cache
reads/writes, persistent/run-local hits, misses, bypasses, shadow comparisons,
corruption/errors, and estimated computation time avoided. The estimate comes
from the original calculation and is not a substitute for measured wall time.

Local verification passed 1,115 wider backend tests and 197 final focused tests.
Every existing real-world matcher regression also runs through persisted reuse.
The completion-intent/wishlist/release-pin lifecycle runs in all three modes and
asserts actual warm hits while downstream decisions change. Focused tests cover
manual constraints, duplicate competitors, input ordering, evidence changes,
corruption recovery, expiry/limits, isolation, and fresh fallback.

The initial live shadow run (846) took 523.86 seconds with zero errors/new
findings. Its 268 audio lookups consumed 71.30 seconds (the earlier baseline
needed 44 / 11.22 seconds), so wall time alone overstated new cache overhead.
The new stage timers nevertheless exposed 31.54 seconds reading and 44.20 seconds
writing matching entries through a fresh SQLite connection per candidate.
A scan-local connection now avoids that repeated setup. Reads retain no
transaction, writes commit before returning to matching, and scan completion
closes the connection. A regression test verifies another connection can write
while this scan connection remains open. The corrected focused suites pass
222 tests.

Run 846's comparison found no changes across all 1,569 stored plans, 14,714
findings, 59 choices, or the complete 558-action ready preview, including its
signature. This stronger plan comparison retains candidate presentation and
inventory membership; only directory-observation bookkeeping and expiring
preview URLs are excluded. The initial cache held 10,053 results / 25.9 MiB;
31 requests bypassed caching because their audio evidence was not a successful
or confirmed-no-match outcome. Corrected deployment measurements follow below.

### Corrected cold and full shadow validation

Corrected cold run **847** completed in **403.73 seconds**, with zero errors,
zero new findings, and complete catalogue evidence. Cache reads fell from
31.54 seconds to **0.61 seconds**, and serialization/writes from 44.20 to
**10.76 seconds**. Signature creation took 4.26 seconds. The approximately
15.6 seconds spent in these new cache stages is more informative than attributing
the entire wall-time difference to caching: audio lookups fell from 268 in run
846 to zero, and the earlier 400.30-second baseline still spent 11.22 seconds
on audio lookups. Every one of the 10,053 eligible results was freshly computed
and persisted under the corrected policy identity.

Warm shadow run **848** completed in **380.94 seconds**. It read and independently
recomputed all **10,053** persisted results, with **zero mismatches, cache errors,
or signature errors**. All 31 ineligible requests were calculated afresh. Both
automatic and validated manual matching use this path. Matching itself took
237.43 seconds; signature/read/serialization stages totaled 7.19 seconds.

Both runs retained the exact baseline plans, findings, choices, queue totals,
and complete ready preview. The release gate explicitly required successful
shadow comparisons, complete catalogue evidence, and unchanged outputs before
deploying reuse. The final default-reuse backend run passed **1,118 tests**.

The cache budget is a payload budget, not the size of the shared SQLite file.
Eviction runs every 64 writes and at completion/maintenance, so a running batch
can temporarily exceed the target by up to 63 entries. The two policy generations
from this validation occupy 20,106 entries / 51.8 MiB; the active generation is
10,053 entries / 25.9 MiB. Older generations remain eligible for LRU eviction
and idle expiry. Run-local matching payloads occupy 25.9 MiB, below the 32 MiB
limit; these figures exclude Python object overhead and other application caches.

### Final reuse benchmark and parity audit

After deployment/restart, run **849** completed in **133.62 seconds (2m 14s)**,
with zero errors/new findings and complete catalogue evidence. This is **66.6%
less wall time (about 3× faster)** than the previous 400.30-second baseline,
and 64.9% less than the immediately preceding full shadow calculation run.
These are live measurements with the same 13,835 files / 1,602 folders, not an
isolated hardware benchmark; audio lookup expiry and normal timing variation
are recorded above rather than attributed to matching reuse.

All **10,053 eligible requests hit persistent storage after restart**. The 31
ineligible requests were computed normally. Matching computation fell from
237.43 seconds in shadow mode to **0.58 seconds**; key generation and cache
reads took 3.65 and 1.43 seconds. The stored-cost estimate reports 248.95 seconds
avoided, separately from these measured timings. No cached approval or readiness
state was used. The largest remaining measured stages were catalogue handling
(33.53 seconds), finding persistence (10.72), plan persistence (11.76), and
inventory (10.52); nested preparation/publication timers are not additive.

Three sequential targeted reanalyses per album, using the same endpoint and
300 ms completion polling as the earlier benchmark:

| Album | Previous median | Reuse median | Reuse maximum |
| --- | ---: | ---: | ---: |
| Bastille | 1.652 s | 0.760 s | 1.059 s |
| motionblur | 1.121 s | 0.701 s | 1.031 s |
| Oceanic | 0.734 s | 0.698 s | 1.023 s |
| Queen | 14.828 s | 1.486 s | 1.700 s |
| Magnolia | 0.780 s | 0.720 s | 0.746 s |

All 15 completed without errors or upstream catalogue requests. Oceanic's four
matching requests per analysis deliberately bypassed reuse because its evidence
was ineligible; its small timing change is not a matching-cache speedup. The
other sampled albums reused their eligible results. These samples establish
observed medians/maxima, not population p95 guarantees.

Five idle requests per queue endpoint gave medians of 219.93 ms (summary),
479.16 ms (list), and 747.94 ms (global ready preview). Earlier idle measurements
were 229, 479, and 668 ms. This does not establish an idle UI/API speedup; the
measured benefit is much shorter analysis, especially expensive albums.

The full reuse scan and all subsequent targeted reanalyses preserved:

- all 1,569 stored plans, including candidate presentation, assignments,
  explanations, proposed edits, and inventory membership;
- all 14,714 reconciliation findings and 59 saved decisions;
- the exact global-ready preview and its signature: 123 albums / 558 actions,
  including the same approved file action;
- all 34 pending approvals, each still bound to its current evidence;
- queue totals: 758 needing input + 123 ready + 2 file decisions = 883 albums.

The read-only audit of all 558 ready-action paths found no missing/changed source
files, occupied conflicting destinations, or duplicate targets. Benchmarks did
not apply album plans. Normalization excluded only directory-observation
bookkeeping and expiring preview URLs; comparisons did not omit candidate lists
or inventory membership.

The observed worker memory high-water mark was approximately 972 MiB, with
736 MiB resident at the audit. These include the rest of the app and are not an
isolated cache-memory comparison. Matching payload storage remained 51.8 MiB
across two policy generations, with 25.9 MiB active/run-local payloads; the
largest individual matching result was 16,615 bytes.

Final deployed code: **`11596193ae4a`**, with reuse enabled by default and
`off`/`shadow` retained for diagnostics. Music/video automation is restored to
enabled. SoulSync remains under the established feature-review deployment with
only its Flux Kustomization suspended. The final documentation commit requires
no additional application rollout. **Milestone 4 is complete.**

## PR integration verification — 2026-10-19

Integrated upstream `dev` at **`55fee15bb2610`**. The only textual merge
conflict was in shared CSS; both the review modal behavior and upstream playlist
status styles were retained. Fixed all four Ruff findings, including explicit
binding of the review case identity in the conflict helper.

Integration review also found two newly added upstream paths that needed the
existing review protections:

- Automatic album completion now checks review decisions and album intent for
  every related album in the validated ownership group.
- Missing download tags now check the actual proposed tag writes against saved
  review decisions before modifying the file.

Regression tests cover protected and unrelated albums, compatible and conflicting
tag writes, unchanged file bytes on rejection, and caller-owned database
connections. Full-suite failures also exposed test fixtures that assumed native
host paths or reused a connection after callers closed it. The fixtures now
exercise the production connection contract and explicit environment defaults.
Repeated parallel scans reproduced exhaustion of the container's 1,024-descriptor
soft limit; the Python CI step now explicitly sets it to 65,536.

Verification of application code at **`b7e6f6e26`**:

| Check | Result |
| --- | --- |
| Full Python suite, Python 3.11.17, eight workers | **26,151 passed, 61 skipped**, 31 warnings |
| Ruff, dependency consistency, Python compilation | Passed |
| Full frontend suite, Node 24 | **10,687 passed** across 584 files |
| Frontend check | Zero errors; 492 existing warnings |
| Frontend production builds | Passed |
| Git whitespace check | Passed |

The optional visual job's committed baselines have upstream drift: the initial
run reported 22 mismatches. To isolate branch effects, all 25 scenarios were
captured from clean upstream and compared with the branch in the same pinned
Playwright container. **24 matched exactly**. The remaining modal difference
was the intended hiding of floating application buttons while the modal is
open. After inspecting the images, only that baseline was updated; the modal
test then passed twice. Unrelated upstream baselines were not rewritten, so
the optional job still needs its upstream baseline refresh.

These checks used disposable source exports and test data. This integration
verification did not deploy an image or apply live album plans.


---

## Original report: review-queue-optimization-review.md

# Review queue optimization and equivalence review

## Changes

- Central policy module owns finding categories, review tiers and SQL queue predicates. Common metadata-review and position predicates are shared. Per-finding readiness, whole-album buckets and the existing global ready-action count remain separate contracts; this refactor does not silently reinterpret historical counts or authorize additional edits.
- A count-only endpoint returns mode totals in one read transaction without hydrating album pages or artwork. Both Tools and Review consume it. Refresh overlays no longer count an album twice merely because its normal entry is on a later page.
- One candidate analysis owns cached title comparisons, version markers, duration differences and recording-support facts. Position indexes and per-file close-alternative sets remove repeated whole-slot scans. The global maximum-weight assignment solver and all scoring thresholds remain unchanged. Confidence checks share an explicit owner-support predicate, retaining top-score versus 0.80 policies.
- Logical-album grouping caches folder facts and indexes sibling candidates by parent, normalized title, artist and owner. It preserves ambiguity and candidate iteration order; mutable accumulated position sets are copied.
- Scan scope is explicit: known logical album, album discovery, or full library. Known scope requires saved roots, member files, database membership and directory observations to agree. Discovery includes siblings and related database album rows. Old plans, changed roots and unverifiable/cross-parent discovery fall back to the full inventory. Only the target logical album's member folders are retired after a targeted scan.
- Wishlist acknowledgement remains immediate. Completed download batches request discovery for affected existing albums, including pending sibling-folder requests. Edition selection, manual reanalysis, bulk edits and edits to grouping metadata also request discovery.
- Typed candidate results and shared pair display facts reduce positional tuple access and serializer duplication. Scan and manual-preview evidence loaders share mechanics while retaining their different evidence inputs.
- A shared worker context factory supplies dependencies without merging pause/cancellation/progress policies. Review reuses Tools status transport and polling with explicit strict parsing and idle-observation options; existing Tools defaults are unchanged.
- A memoized UI album model owns assignment groups, decision membership and selection maps. Maps preserve first-match behavior when one finding owns several rows.

## Preserved baseline

Before implementation, a consistent SQLite read transaction copied scan results, library metadata, provider cache and fingerprint/tag evidence into a local comparison dataset. Configuration and credentials were excluded. The dataset is deliberately outside the feature checkout and is not committed.

- Source checkout: `7e313f3eaea5d441d48f5d0bc9375ab472cb4f78`.
- Live application at capture: `localhost/soulsync:test-01334243e8f6`.
- Latest completed reconciliation: run 796, 13,810 files, zero errors, 1,966 seconds.
- Captured plans: 1,567; pending reconciliation findings: 1,180.
- Captured queue: 562 albums, comprising 484 needs-input, 75 ready and 3 file-action albums; historical ready-action count 392.
- Snapshot SHA-256: `46ef5a03122396721674d50b1708d15aea7e50a0a678e2dd46193972951cb653`.

## Verification before deployment

- All page outputs and summary counts matched the original implementation on the saved database across ten categories and four modes.
- Complete matcher/grouping outputs matched the original implementation across all 113 existing planner/end-to-end tests, using a differential test wrapper.
- All 1,567 saved album plans produced identical old/new matcher results when reconstructed into identical inputs. Matching took 43.70 seconds before and 21.83 seconds after on that local replay. This is a matcher-only measurement, not a full-scan speed claim: older plans do not retain every raw provider field, and filesystem/provider work was excluded.
- All 600 seeded randomized matcher cases also produced identical outputs, including duplicate positions, version variants, missing durations and forced assignments.
- All nine Mermaid diagrams parsed successfully.
- Broad Python regression: 1,106 tests passed. Subsequent targeted checks cover scope escalation, changed roots, sibling downloads and refresh overlays.
- Shared Review/Tools UI regression: 662 tests passed. Frontend checks: zero errors, 548 existing warnings. Production frontend build succeeded.
- Browser checks exercised Plan, Release, Tracks and Evidence at 1800, 1100, 840 and 390 pixels: no document overflow or JavaScript errors. The mobile track inspector remained within the viewport and scrollable.

## Deployed verification

Test image `localhost/soulsync:test-67889b3a80a5` is running on the Kubernetes cluster. The documented test-deployment script completed successfully; the deployment is Ready and SoulSync's Flux reconciliation remains suspended for feature review. No upstream push was made. Imports also passed inside the built Python 3.11 image.

A fresh full-library scan (run 799) completed with 13,810 files, zero errors and no automatic file fixes. It took 1,869.53 seconds (31m 10s), compared with 1,966.11 seconds (32m 46s) for the previous scan. These are observations under different cache/provider conditions, not a controlled whole-scan benchmark. The new scan's inventory took 11.3 seconds; it reused 13,780 fingerprints and 13,806 tag reads.

The before/after comparison covers all 1,567 plans:

- No logical album was added or removed.
- 1,561 plans were identical after excluding the new operational inventory record.
- Five plans differed only in expiring Deezer preview URL parameters; their media paths were unchanged.
- One plan freshly observed ISRC tags on four Paramore files. Its assignments, confidence and proposed edits were unchanged.
- All assignment decisions and proposed metadata edits matched the baseline.

Pending findings increased from 1,180 to 1,467, and queue albums from 562 to 602 (518 needs-input, 80 ready, four file-action albums; ready-action count 494). Every one of the 287 newly pending findings had unchanged evidence and a prior resolved finding whose existing seven-day recurrence grace expired between the two scans. This is existing time-dependent behavior, not a changed classification or matching threshold. Historical resolution times were October 8 at 05:13:29, 05:56:19 and 18:25:04 UTC.

A live scope check on **North (Deluxe Edition)** selected 28 known album rows in 0.014 seconds. Discovery selected 90 related rows under its artist directory; the normal album reanalysis completed in 3.876 seconds with an identical plan. No metadata application, quarantine or download was performed for verification.

All 16 live viewport/tab combinations passed without document overflow or JavaScript errors. A stalled local Kubernetes port-forward was replaced before completing the mobile checks.

Local comparison artifacts are under `test-data/review-optimization-baseline-20261015/` and `test-data/review-optimization-after-20261015/` in the main workspace. Each contains a compressed SQLite snapshot and manifest; the after directory also contains validation logs and the explanation of every observed difference. These datasets are not committed.

## Complexity and boundaries

Pair scoring no longer repeats a full slot search for each applicable edge. Its repeated alternative-search work moves toward `O(F × S × R)` from `O(F × S² × R)`, where F is files, S slots and R recording candidates. Assignment remains `O(F² × max(F, S))`. Per-analysis caches use additional memory proportional to the facts actually evaluated.

Indexed grouping avoids comparing every fragment with every unrelated group; worst-case same-key buckets can still be quadratic. Queue summaries reduce repeated requests and processing rather than claiming a different database asymptotic bound. A known-album refresh avoids whole-library file resolution and walking, while checking nearby directory membership conservatively. Broad discovery and whole-library scans remain available.

Older retag matching, download verification and quality-probe outputs are not interchangeable with reconciliation policies. Their differing acceptance rules, bitrate units and fields remain intact; existing guarded mutation handlers continue to authorize writes.

See [the updated end-to-end decision flow](review-queue-decision-flow.md) for the detailed algorithms and thresholds.


---

## Original report: ui-consistency-implementation.md

# UI reuse and consistency implementation

## Dev refresh and configurable-defaults assessment — 2026-10-22

Fetched and merged `upstream/dev` at `d66f604c1` (170 incoming commits) into the
feature branch from `96ceafec9`. Resolved two shared-component conflicts:
`DialogFrame` retains the shared-modal marker alongside upstream's custom
viewport, accessible label and popup ID support; badges retain their deliberate
semantic contrast/icon spacing alongside upstream's new loading/empty-state
primitives. Fixed one incoming wishlist type declaration's formatting. No new
preferences were implemented or deployed in this refresh.

Verification: **764 backend tests**, **112 focused UI tests**, and **69 pinned
browser/visual checks** passed. Screenshot baselines were not updated. UI check
finished with zero errors and 488 warnings; production build and the local
conflict-resolution whitespace check passed. This is integration coverage of
Review and the affected shared code, not a full test of every incoming feature.

The initial configurable-defaults proposal below is superseded by the queue-only
correction scope agreed on 2026-10-22. It is retained only as historical context;
no shared policy resolver, override framework, or new settings are authorized.

- Separate release/audio identity from preferred metadata representation,
  filename rendering, folder layout and acquisition intent. A formatting
  preference must not manufacture evidence for a different recording/edition.
- Existing settings already cover reconciliation numbering/source/album titles,
  path templates, automatic disambiguation, artist credits, original dates and
  preservation. Resolve these through one shared policy instead of introducing
  competing queue-only settings. The current digital numbering setting also
  influences release ranking; separate those responsibilities.
- Preserve canonical source disc/track positions and derive an absolute position
  from the full selected tracklist, including missing slots. Let tag numbering,
  filename numbering and disc folders be chosen independently. Custom export of
  an absolute position must not be presented as universally supported by players.
- Preserve supported release/remaster detail in metadata while independently
  controlling whether it appears in title tags, filenames and album folders.
  Distinguish edition descriptions from live/remix/acoustic recording identity.
- Resolve library defaults with album overrides and explicit saved field choices;
  snapshot relevant policy inputs into proposals/signed apply. Preference changes
  regenerate affected proposals, preserve valid explicit decisions and never
  silently apply files. Reuse raw catalogue/audio evidence for presentation-only
  changes. Review, imports, tag writing and organization must converge on the same
  policy so repeat scans do not undo one another.
- A supported convention covered by an explicit default can become ready without
  repeated per-album confirmation. Whole-album eligibility remains required.
  Numbering approval must still disclose its actual scope: preferences do not
  excuse approving unrelated artist/identifier edits under a numbering label.
- Start with numbering, title/filename qualifier handling and release preferences;
  integrate existing date, credit and preservation controls. Validate mixed edits,
  partial/multidisc albums, conflicting versions, stale plans, codec round trips,
  naming collisions and apply-then-rescan stability before broadening defaults.

## Queue numbering correction and recovery — 2026-10-22 (completed)

The user authorized implementation, tests, deployment, a full reconciliation run,
and repeated correction/deployment/run review as needed. The host reboot for a
vulnerability fix completed; subsequent rollouts and live runs were healthy.

- [x] Keep digital format as an evidence-subordinate release tie breaker;
  separate it from numbering. Respect selected releases and recording versions.
- [x] Preserve the selected release's real disc/track positions, including audio
  media after excluded videos. Retire the queue's flattening behavior/settings.
- [x] Reassess completed albums and recover previously flattened metadata using
  selected-release evidence. Preserve valid decisions; changed inverse proposals
  require fresh review. Do not infer that an actual single-disc edition is wrong.
- [x] Numbering approval covers numbering only; unrelated metadata remains
  classified on its evidence and explicitly reviewable where required.
- [x] Preserve compatible filename conventions and title/remaster behavior;
  verify partial albums, repeated titles, collisions and apply/rescan stability.
- [x] Remove low-value queue disclosures and excessive component nesting,
  especially release candidates; inspect desktop/mobile before and after.
- [x] Run affected backend/UI/browser suites, check/build; deploy via maintained
  script, run reconciliation, inspect actual proposals/readiness/recovery, fix
  findings and repeat. Report measured results and remaining limitations.

Scope excludes new preferences, downloads/Soulseek, shared organizer behavior,
date policy, completeness defaults, and duplicate handling changes. Live review
does not authorize applying unrelated music-file changes as test data.

Implementation checks: 783 backend scan/matching/authority/queue tests passed.
The final album-context correction passed all 137 scan end-to-end tests,
including three new cases for format/country ordering and explicit pins.
Recovery tests write real FLAC fixtures, verify disc/track and per-disc totals,
retain compatible absolute filenames, and rescan to an empty queue. Additional
coverage includes unordered or incomplete source positions, explicit release
pins, digital format ties, and separate numbering/metadata choice lifecycles.
Frontend review tests passed 78 tests; check reported zero errors and 488 existing
warnings, and production builds passed. Desktop/tablet/phone screenshots were
compared; the redundant release/manual-lookup frames, single-action album menu,
small saved-choice/source disclosures and one-identifier disclosure were removed.
Multiple identifier additions retain a disclosure to protect phone readability.
All 69 pinned browser checks passed against the reviewed snapshots. An initial
update pass overlapped a production build and lost its manifest in three tests;
the complete run against the finished build passed without snapshot updates.
Live baseline: 895 albums, 131 eligible albums / 526 ready changes; captured 137
release/numbering cases before rollout. Final live results are recorded below.

First live run (`aa75e21a2`, run 900): all 13,835 files examined, 306 findings
created, no file actions applied. It correctly reopened 20 Bad Blood X tracks,
40 ABBA Gold tracks and 15 Fame Monster tracks for fresh numbering review.
The run ended as stopped/incomplete because a merged MusicBrainz release ID
returned a same-server 301. The conservative incomplete-run path retained old
proposals, so its queue totals are not a final result. Fixed the existing client
redirect handling to allow bounded, same-server/same-API-root/same-entity release
and release-group merges as well as recording merges; 114 provider/catalogue
tests passed, including local HTTP tests and rejected unsafe/looping redirects.

Comparing all newly reopened cases exposed an ineffective digital tie breaker:
format ordered candidates but physical numbering variants still forced release
review. Corrected it to resolve identical audio/tracklist ties across formats,
while preserving explicit pins, material version/tracklist differences and
competing digital layouts. No source positions are synthesized. Follow-up tests
cover physical-vs-digital layouts, pins and two genuinely different digital
layouts. A packed DDTT filename overflow guard also preserves prefixes that
cannot represent the source position.

Run 901 on `4a5129417048` completed all 13,835 files with zero errors, zero
automatic file changes and complete catalogue evidence. The persisted review
contained 857 numbering proposals, all matching their selected source positions;
no pending legacy digital-mode proposals remained. A read-only batch preview
contained exactly the 128 fully eligible albums / 519 changes. All 27 live
desktop/phone views passed without page errors, overflow or repair mutations.

Comparing changed release recommendations then caught a further ordering issue:
without the old numbering bias, an unrelated compilation could precede a lone
track's known album on equal audio fit. These cases remained manual, but the
recommendation was worse (for example Piano Solos and Between Us). The tie order
now considers the existing single-file album context before format/country,
without overriding better audio, explicit pins or genuine edition ambiguity.
The recommendation-order regression failed before the correction.

### Final live verification

- Deployed `localhost/soulsync:test-f96a3235331e` through the maintained test
  script. Run **902** completed in **114.7 seconds**: **13,835 files**, **12 new
  findings**, **zero errors**, **zero provider failures**, complete catalogue
  evidence, and **zero automatic file changes**.
- Queue: **886 albums = 753 needs input + 128 ready + 5 file actions + 0 waiting**.
  The read-only signed batch preview contained exactly the 128 fully eligible
  albums / **520 changes**, with no file actions or unresolved albums. The extra
  ready change since run 901 is a newly available, uniquely audio-supported
  recording-ID repair for Kenny Loggins' “The Real Thing”, inspected separately.
- Audited every pending numbering proposal: **857 tracks**, **zero disagreements
  with the selected source's disc/track positions**, and **zero legacy digital
  mode proposals**. Old approvals cannot silently authorize inverse repairs.
- **101 historically changed tracks** have fresh numbering review proposals:
  Bad Blood X 20, ABBA Gold 40, The Fame Monster 15, Unreal Unearth: Unending 26.
  Recovery follows current selected-release evidence rather than blindly
  reversing old edits. Actual single-disc editions retain their source layout.
  No live recovery plan was applied; the apply/rescan cycle was verified with
  disposable real-tag fixtures.
- The final context fix improved the leading recommendation on **16 sparse
  albums**, including Piano Solos and Between Us. All retain their unresolved
  edition decision where multiple material tracklists fit. A digital bias does
  not override the known album context, stronger audio evidence or a user pin.
- **27 live desktop/phone views** passed on the preceding image, followed by
  **12 targeted recovery/context views** on the final image. Reviewed actual
  artwork, per-disc previews, filename differences, semantic colors and mobile
  wrapping. The automated checks found no document overflow, page errors or
  repair mutations. The 69 fixture-based browser/visual checks also passed.

Remaining behavior is explicit: materially different releases still need a
choice; source numbering can imply filename changes when an old absolute prefix
does not match the selected edition's order. Title/remaster policy was preserved,
not redesigned: six pre-existing source-title proposals still put remaster text
in filenames (five Deftones Covers tracks and “All It Takes”). Their before/after
proposals are identical. This round adds no settings and changes no downloader,
Soulseek, date, folder-organization or duplicate policy. The test image remains
available for user review with the existing SoulSync-only Flux suspension.

## Reverification against the original assessment — 2026-10-20

Baseline: `e80e4a1da`; deployed application `336c7cf68c1e`. Recovered the full
historical assessment from the conversation and checked its numbered findings
against source, production-generated acceptance fixtures, current live cases and
the Stage 1–3 record below. The earlier completion statements were too broad:
this pass found three connected omissions in album-conflict presentation.

### Additional corrections

1. Plan still described contradictory album tags as generic metadata changes
   with ready/manual totals. It now names the conflicting album title and/or
   release ID, counts the affected tracks, and discloses other edits in the same
   group. Saved decisions and unrelated identifier edits cannot revive this
   question. The original regression test failed before the correction.
2. Album conflicts sharing a finding with numbering were hidden behind a
   numbering-only explanation and approval label. The decision now explains
   both effects before approval; its acceptance button names numbering and album
   metadata. Stored Matchbox Twenty findings exposed this presentation gap;
   the fresh run below removed their obsolete conflict reason. The Essential
   Cheap Trick provides the post-scan live example of genuine combined conflicts.
3. After **Keep current numbering**, the backend correctly retained conflicting
   metadata as manual, but Tracks treated any saved choice as settled. Attention
   grouping, amber styling and the status now follow the remaining action state:
   **Review metadata · numbering kept**. The remaining proposal is reachable;
   numbering is preserved, files remain unchanged, and the album stays ineligible
   for apply until its other decisions are resolved.

These are explanation/state-projection corrections. Matching, readiness policy,
group membership, saved-choice authority and file-writing behavior are unchanged.

### Original findings crosswalk

Numbers below refer to the recovered historical assessment, not the later
acceptance checklist.

| Original finding | Current assessment and evidence |
| --- | --- |
| 3.1 Compatible named-performance detail rejected | Corrected in Stage 1. Planner/end-to-end tests retain compatible dates/location detail and reject distinct dates, mixes, venues and conflicting audio; acceptance renders both positive and negative cases. |
| 3.2 Missing metadata treated as contradictory; coherent IDs removed | Corrected in Stage 1. Blank and contradictory tag fixtures have independently asserted exact edits. Authority tests retain coherent identities and explicit release choices. |
| 3.3 Unmatched companion disappears | Corrected in Stage 1. Inventory/queue tests and both companion acceptance states account for every file without consuming extra release slots or manufacturing duplicate removals. |
| 3.4 Add/replace/remove summaries; album-wide conflict explanation | Identifier operations were corrected in Stage 1. This pass adds missing album-conflict explanations, including mixed numbering decisions. The fresh live run below exposed a broader remaining gap: numbering approval can also approve artist/identifier edits when there is no album-conflict reason. |
| 3.5 Repeated original/deluxe occurrences remain ambiguous | **Open behavior, not established as a new solver regression.** Current motionblur still has 18 ambiguous and eight clear assignments. Acceptance protects all intended occurrences and files; it does not solve the repeated-occurrence ambiguity. Historical/current catalogue evidence differs. |
| 3.6 Additive guest credits treated as artist replacement | **Open inherited policy gap.** A changed nonempty credit still requires manual review, including a guest addition. A current direct policy probe confirmed this. Stage 1–3 did not redesign artist-credit equivalence or broaden automatic approval. |
| 4.1 Reason after controls/audio | Stage 2 places the issue and proposed effects before approval and playback. Browser checks cover conflicts, ambiguity, duplicates and missing-track intent; this pass adds the album-level mixed-decision case. |
| 4.2 Consequential identity changes collapsed | Replacements/removals remain expanded with actual old/new values and operation labels. Only explicit additions can be secondary. Review and the shared Library comparison are checked. |
| 4.3 Excessive comparison stacking | Compact side-by-side current/planned presentation retains strikethrough and direction, with wrapping for long values. Desktop, tablet, phone and 320px checks pass. |
| 4.4 Selection/success and matching/readiness conflated | Distinct selection, severity, evidence and decision labels are present. The partial saved-numbering exception was missed and is fixed in this pass; real worker output now feeds its browser test. |
| 4.5 Readiness overclaims audio verification | Aggregate readiness describes plan decisions; audio, metadata and user-supported assignments keep distinct evidence labels. Positive and fallback cases are covered by presentation/backend tests. |
| 4.6 Internal mobile header clipping | Existing checks measure header tools, not only document overflow, at 320/390px and wider viewports. All layout checks pass. |
| 4.7 Excessive generic/navigation structure | Stage 2 consolidates the ready apply/field summary and uses a compact album browser on narrow screens. Stage 3 also corrects the optional organization button squeezing the ready explanation. |
| 4.8 Capability reachability | Tests retain selective missing-track requests, saved choice editing, duplicates, assignment alternatives, navigation, cancellation and stale/relocated cases. Read-only live checks supplement disposable mutation tests. |
| 5 Preserve architectural/shared-component improvements | Authority, signed apply, whole-album eligibility, relocation, cache, fulfillment, player, inspector, artwork provenance and shared-control suites pass. No authority or matching contracts are broadened by this pass. |
| 6 Layered CSS and incomplete presentation semantics | Conflict semantics are now carried into Plan and partial-choice state. **CSS organization remains debt**: Review still has layered/responsive selectors. This pass does not claim a complete stylesheet consolidation. |
| 7 Durable acceptance was absent/narrow | Backend-generated fixtures assert recommendations independently; pinned-browser checks cover meaning, workflows and pixels. This pass exposes why inspecting Tracks alone missed a Plan requirement and adds both views and the transition between them. |

The earlier screenshot feedback is also represented: shared exact-file playback
and record inspection; no local player for absent audio; release artwork
provenance and compact edition/label details; semantic track colors; opaque
completion guidance; selective acquisition; secondary saved-choice controls;
whole-album apply eligibility; no-op organization suppression; and staged file
choices followed by a consequential apply confirmation. Existing shared UI and
Review tests were rerun, not inferred from component reuse alone.

### Verification in this pass

- **668 backend tests passed** across planner, end-to-end, authority, cache,
  inventory, catalogue, fulfillment, Tools, acceptance, queue/group, finding
  lifecycle, recording-ID repair and fetch behavior.
- **263 Review/shared UI/Library tests and 60 additional shared player/control
  tests passed.** Type/format check: zero errors and 485 existing warnings;
  production build passed.
- **69 pinned-browser comparisons/workflow checks and 14 layout/navigation
  checks passed.** Changed/new screenshots were inspected; all final comparisons
  ran without baseline updates. The corpus now has 13 initial scenarios and
  eight derived states, 21 backend tests and 52 acceptance screenshots.
- The combined-numbering fixture saves a choice through the real worker and
  checks remaining exact edits, manual state and unchanged file bytes. Its
  browser transition checks the exact request, amber row, unavailable apply and
  reachable metadata decision on desktop and phone.

The open inherited policy/ambiguity findings and CSS debt above are not closed
by a passing finite corpus. The historical Evanescence AOL-session missing/extra
pair is likewise not claimed resolved by this pass; it predates the reassessed
sessions and is outside the seven-album live sample.

### Deployment status and live baseline

The corrections are committed as `3e68dd2c3`. The maintained deployment script
built and loaded `localhost/soulsync:test-3e68dd2c3f50`. The initial idle check
held the rollout because Lyrics Filler was running. The user subsequently
explicitly authorized deployment regardless of running jobs. The maintained
script then deployed the image successfully: **Ready 1/1, zero pod restarts**.
Flux remains in its prior feature-review suspension; GitOps manifests and
automation settings are unchanged.

Read-only live inspection before this rollout completed 21 Plan/Tracks/
Release views: seven albums on desktop, three of those on phone, and Stone Sour's
release comparison. No browser exceptions, failed static assets or measured
header/comparison overflows occurred. Artwork requests were allowed to settle;
available exact-release covers and neutral fallbacks remain distinct. The
Matchbox Twenty Plan confirmed the missing combined-conflict explanation on
desktop and phone. This baseline preceded both the rollout and the fresh scan.

The before/after-deployment API sample covers seven albums and 250 assignments.
Complete assignment, proposed-edit, saved-choice, readiness and signature
payloads are unchanged. Only the intended decision explanations differ:
Split Chain's five-action metadata group identifies the two tracks with album
conflicts and three other edits; Matchbox Twenty's 53-track numbering group now
discloses the 40 tracks with conflicting release IDs and 13 other tracks.

### Completed reconciliation run and results

Full-library run **886** completed on 2026-10-20 local time (2026-10-21
02:02:41–02:14:11 UTC), after capturing all 894 queue albums and their decision
plans. It checked **13,835 files across 1,602 album folders in 11 minutes
30 seconds**, created 16 finding rows, reported **zero errors**, and did not
report incomplete catalogue evidence. **Zero automatic fixes** occurred. No
live review choice, repair apply or wishlist request was submitted. This scan
updated reconciliation evidence/findings without applying music-file edits.

| Queue measure | Before scan | After scan |
| --- | ---: | ---: |
| All albums | 894 | 895 |
| Needs input | 760 | 761 |
| Ready albums | 131 | 131 |
| File-action albums | 3 | 3 |
| Waiting albums | 0 | 0 |
| Actions in fully ready albums | 526 | 526 |

Across the entire queue, **889 existing album decision summaries were
unchanged**, five changed, one album appeared, and none disappeared. No existing
album crossed the ready/manual eligibility boundary, and no case reported a
last error. Queue modes sum correctly. The signed batch preview contains
**exactly the same 131 eligible case IDs and 526 actions**, with **zero file
actions** and no album with unresolved decisions. This checks summary and batch
eligibility across the whole queue; it does not claim an exact field comparison
for all 895 albums.

The deeper seven-album comparison covers 250 track rows. All file/slot
relationships and assignment confidence classifications were preserved. Stone
Sour, Split Chain, Joep Beving, Jeff Buckley and Bastille retained their exact
proposals, choices and signatures. Queen and Matchbox changed as described below.

| Changed/new case | Result and interpretation |
| --- | --- |
| Matchbox Twenty collection | The old conflict consisted of 40 blank release IDs and 17 copies of one nonempty ID. The scan correctly removed this false conflict; actual proposed edits and assignments stayed unchanged. This revealed the broader approval-scope gap below. |
| Queen — Platinum Collection | Two local live titles, “The Show Must Go On (live)” and “Somebody to Love (live),” now retain useful local wording instead of being expanded to long catalogue descriptions. Their title/filename proposals were removed; remaining album/disc edits persist. Because the actual proposals changed, those two prior approvals reopened: 33 saved choices became 31. The other 31, including the “Who Wants to Live Forever” replacement, were retained. Ready/manual actions changed from 50/1 to 48/3; the album was already ineligible for bulk apply. |
| The Truman Show | Another false blank-ID conflict disappeared. Ready/manual actions changed from 10/3 to 11/2. One uncertain assignment and a proposed additive orchestra/conductor credit still require review. The latter confirms the inherited artist-credit policy gap. |
| Cream — Wheels of Fire | Three old actions became one manual recording-ID removal and one completeness decision. The selected Apple/iTunes release has 10 local files for 14 slots. “Passing the Time” carries an ID resolving to Cream’s “Sitting on Top of the World”; the proposed removal explicitly exposes that disagreement and stays manual. The full old selected-release payload was not captured, so this is not proof of the cause of the provider-selection change. |
| Soul Asylum — Grave Dancers Union | An old release-choice finding became ten manual title proposals plus completeness for two absent tracks. The selected MusicBrainz edition explicitly says “2022 Remaster,” yet the title proposals remove that qualifier. This is a remaining recommendation-quality gap, detailed below. |
| ABBA — Gold, 40th anniversary edition | Four new manual disc-tag corrections account for the one new queue album. Read-only inspection of the actual FLAC tags confirmed disc values 2, 2, 2 and 3 while the library rows say 1. These are real remaining file/library differences. Earlier resolved numbering findings exist, but the available history does not establish whether an old apply left the tags behind or something later rewrote them. |

The previous full run was on October 19, before the recovery stages. Consequently
the fresh scan exercises the broader Stage 1–3 corrections as well as the latest
presentation fix. Deployment alone preserved all sampled recommendation data;
it is important not to attribute every subsequent scan difference to `3e68dd2c3`.

The prior full scan took 8 minutes 16 seconds versus 11 minutes 30 seconds now.
This is not a controlled performance comparison: this run performed 1,014
catalogue cache misses, 314 audio lookups and 54 fingerprints, and used a new
matching-policy cache signature. Catalogue time rose from 52.3 to 195.4 seconds,
while candidate matching fell from 304.7 to 221.3 seconds. Fresh external work
and changed cache eligibility preclude a simple speed-regression conclusion.

### Remaining gaps exposed by this run

1. **Numbering approval still understates its full scope when other edits are
   attached without an album-conflict reason.** Matchbox now says “Plan continuous
   numbering for 53 tracks.” Forty of those proposals also change the artist
   from Various Artists to Matchbox Twenty and include library album/identifier
   edits. A local projection of the captured real proposal, using the production
   choice projector, confirmed that accepting numbering sets `plan_approved`
   for the entire proposal and leaves all those edits included. No live choice
   was saved. The latest correction addresses explicit album conflicts, not
   every mixed proposal. A follow-up must either disclose the complete approval
   scope before submission or separate approval by field, with matching tests
   for accepting and keeping numbering.
2. **Edition-supported remaster wording is still proposed for removal.** Soul
   Asylum’s selected release `f580b3a0-6bb4-40dd-a989-aa7f62137b5c` is itself
   disambiguated as “2022 Remaster,” but ten title/filename proposals strip
   “(2022 Remaster)” and describe a potential recording-version difference.
   Keeping these manual prevents unattended changes but does not make the
   recommendation useful. Follow-up should preserve supported local edition
   detail while retaining negative controls for conflicting remasters/versions.
   The historical release preview included a 25-slot deluxe alternative absent
   from the current six displayed candidates; those bounded snapshots do not
   establish why the release-choice finding disappeared. Discovery/ranking and
   refreshed evidence must be traced before calling that a solver regression.

These are open findings, not claims of completed recovery. The repeated original/
deluxe ambiguity, additive-credit policy, historical Evanescence case and CSS
debt recorded above also remain outside the completed corrections.

### Post-scan browser verification and retained evidence

The final live browser pass completed **33 desktop/mobile views** across twelve
albums: desktop Plan/Tracks for each, Stone Sour Release, and Plan/Tracks for
four of them at 390px. It recorded zero JavaScript exceptions, zero failed static
assets, and no measured header/comparison overflow. Changed and representative
screenshots were visually inspected, including genuine conflicts, remaster
proposals, wrong-ID removal, Queen, ABBA and the phone numbering decision.

The Essential Cheap Trick demonstrates the corrected genuine conflict after
the fresh scan: numbering names 18 affected tracks, title conventions name
three, and both approval labels disclose album metadata alongside their primary
operation. The explanation is visible before approval on desktop and phone.
Track severity colors, current/planned direction, local evidence and shared
playback remain visible in the inspected states.

An earlier browser attempt during the active scan stopped when its stale
Matchbox-conflict expectation ceased to match the refreshed data. The final pass
used post-scan evidence and completed all views. Browser guards blocked an
incidental notification-log POST; no repair mutation was attempted.

Raw local evidence is retained under `/tmp/soulsync-postdeploy-run/` (before/after
queue, seven detailed cases, six changed/new cases, signed preview, historical
findings and file tags), `/tmp/soulsync-reverify-live/` (33 screenshots and browser
report), and `/tmp/soulsync-reverify-parity-verified.json` (deployment-only parity).
These temporary artifacts are not committed; this ledger preserves the findings
and limits. Final deployment status remains **Ready 1/1** on the requested test
image, with the existing feature-review Flux suspension preserved. Only this
documentation changed after the rollout; no additional runtime changes were
made during the run analysis.

## Stage 3 recovery — completed 2026-10-20

Baseline: `0235284ad`; deployed application `1283b55b6502`.
The original assessment defines this stage as **durable acceptance evidence**,
not another redesign or matching-policy expansion. Preserve Stage 1 and 2.

### Acceptance checklist

- [x] A1. Fixed, fully supported album: correct assignment, supported edits,
  readiness and truthful evidence labels.
- [x] A2. Metadata-only album: exact proposed effects, no invented audio proof.
- [x] A3. Genuine recording conflict: explicit reason and manual decision;
  matching tags cannot silently authorize wrong audio.
- [x] A4. Harmless qualifier/detail difference: useful wording preserved, with
  negative controls for genuinely different performances.
- [x] A5. Repeated recordings in original/deluxe sections: each intended slot
  remains represented; repeated occurrences do not become disposable duplicates.
- [x] A6. Missing versus contradictory metadata: blanks do not erase coherent
  identities; genuine disagreements remain visible and require review.
- [x] A7. Duplicates and intentional companions: complete file inventory,
  distinct roles, keeper evidence and no premature file mutation.
- [x] A8. Partial albums and selective missing-track requests: only intended
  slots requested, with exact provider/release identity and cancellation state.
- [x] A9. Saved choices, relocation and stale evidence: retain valid decisions,
  reopen materially changed evidence, reject stale submissions, preserve
  navigation and accurately render each state.
- [x] A10. Link backend acceptance output to browser fixtures; verify exact
  effects/explanations/inventory and desktop/mobile rendering. Keep repeatable
  commands, reviewed baselines and coverage limitations in the repository.

Completion requires semantic assertions as well as rendered comparisons. Fixture
updates must be explicit; test runs must not silently bless new recommendations.
All repair mutations used for verification operate on disposable files/databases.

### Acceptance evidence

`tests/library/test_review_acceptance.py` builds tagged files and a real SQLite
library, runs the reconciliation job, persists choices through the worker, and
captures the full case, queue, summary, ready preview and finding capabilities.
Its 19 tests cover 12 initial scenarios, seven derived lifecycle states, and four
actual metadata-apply cases. Catalogue/fingerprint responses and tiny-file
durations are controlled; matching, inventory, proposals, persistence, wishlist
identity and queue projection are production code.

| Requirements | Independent assertions and rendered evidence |
| --- | --- |
| A1–A2 | Exact proposed field dictionaries; whole-album readiness; all resulting tags after apply; resolved albums leave the queue. Metadata-only evidence never claims independent audio confirmation. |
| A3–A4 | Wrong-recording identity and missing/extra inventory remain explicit and manual. Equivalent live detail preserves the local wording; a different performance date remains a conflict despite the shared recording ID. |
| A5–A6 | Repeated occurrences occupy distinct release slots and cannot become duplicate removals. Blank fields are filled; contradictory album/release IDs remain manual and their exact replacements are visible. |
| A7 | Every physical local file appears exactly once, including intentional and unmatched lossy companions. Duplicate keeper selection leaves file bytes unchanged; Plan and apply confirmation name the same keeper and quarantine paths. |
| A8 | Partial intent selects Open Water and excludes Afterglow. Wishlist contains only the requested recording and exact release. Cancelling the request retains partial scope, wanted status and the uncollected slot. Browser tests check exact request bodies and cancellation before submission. |
| A9 | Saved decisions survive unchanged rescans and a real directory move; the old case resolves to the new case. Material tag changes reopen review and reject old evidence tokens and signed apply. Browser navigation and refresh follow these states on desktop and phone. |
| A10 | Nineteen generated JSON states feed 38 desktop/phone screenshots at 1800/390px; three more protect 320px collisions. Three organization-preview screenshots at 1800/1280/390px bring the total to 44. Six browser workflows cover save/move/stale apply, selective request/cancellation, and staged duplicate choices on desktop and phone. |

Snapshots normalize only temporary paths, timestamps and opaque tokens; semantic
expectations run before an explicit update. Raw token changes and stale rejection
are separately asserted. The normal Python CI job checks generated output, and
the new blocking `review-acceptance` job checks rendered meaning and pixels in
the pinned Playwright image. Commands and update rules are documented in
`webui/tests/visual/README.md`.

### Defects exposed and corrected

- Explicit queue/detail/summary/apply-preview reads now bypass the shell's short
  GET deduplication cache. Immediate Reload after an external cancellation or
  stale response must actually reach the server. Shared fetch tests cover all
  three explicit refresh cache modes and Request objects.
- Ready plans now list saved file decisions as well as metadata fields. The
  backend shares the exact action description with the signed apply preview,
  and the summary links back to the relevant decision. File-only plans do not
  instruct the user to select a nonexistent metadata field.
- Intentional companions retain their role label when they have pending edits
  or do not fit a release slot. Unassigned-track detail explains the actual
  mismatch and uses the local title for service searches.
- Duplicate facts and keeper buttons wrap without colliding. Waiting badges,
  explanations and Wishlist links remain separated. The mobile completion
  banner clears the fixed shell navigation control. Singular evidence and
  version-conflict labels were corrected.
- Live inspection after the organization preview arrived exposed a second apply
  button squeezing the ready-plan explanation into a narrow column. The action
  column now takes at most half the card's available width; controls wrap while
  the explanation remains readable. Three dedicated visual checks protect this
  state, which was absent from the original no-moves preview fixture.

These fixes do not change matcher thresholds, readiness policy, saved-choice
authority or file-writing policy.

### Local verification

- **632 backend tests passed** across planner, end-to-end, authority, cache,
  inventory, catalogue, fulfillment, Tools, queue/group, fetch and acceptance
  suites. The **19 acceptance tests also passed under Python 3.11** in the cached
  application image with isolated temporary data and no network.
- **95 focused UI tests passed** across Review, metadata comparison and fetch
  deduplication. `npm run check`: **zero errors / 485 existing warnings**;
  production build, affected Python lint and whitespace checks passed.
- **61 pinned-browser checks passed without updating baselines**: 50 new
  acceptance checks and all 11 existing Review/Library visual comparisons.
  Every new screenshot was visually inspected, including the final corrected
  phone layouts. Existing baselines were unchanged.
- **14 existing layout/navigation checks passed**, covering 1800/1280/768/390/320px,
  paging, focus, identity-edit visibility and acquisition status. A missing local
  browser path was corrected in the temporary runner; no app change was needed.

### Final deployed verification

Application commits: `b846eafe2` (acceptance corpus and exposed defects) and
`336c7cf68` (organization-card correction and coverage). The final code image is
`localhost/soulsync:test-336c7cf68c1e`, deployed through the maintained test-rollout
script and **Ready 1/1**. The subsequent documentation commit changes no runtime
code. Music automation was paused after an idle check and restored to its prior
enabled state; video automation remained enabled. SoulSync Flux remains suspended
for the existing feature-review deployment. GitOps manifests were unchanged.

- Compared six real albums before and after deployment: Stone Sour, Split Chain,
  Joep Beving, Queen, Jeff Buckley and Bastille. All **193 assignment rows**,
  proposed changes, decisions, readiness and plan signatures were unchanged.
  The sole intentional addition was Queen finding 51777's `planned_file_action`,
  describing its existing saved replacement choice. No unexpected differences.
- Repeated Plan/Tracks checks for all six albums at 1800px, Stone Sour's Release
  view, and Plan/Tracks for Stone Sour, Split Chain and Bastille at 390px. The
  completed matrix produced 19 screenshots with no JavaScript exceptions,
  header overflow or checked comparison-table overflow. Screenshots were
  inspected, including the final desktop/phone organization layout. The ready
  explanation measured 272px on desktop and 260px on the phone with both buttons
  present.
- A separate settled-artwork check confirmed four exact release covers loaded;
  two image-proxy requests returned 502 and displayed neutral fallback artwork.
  This check waited for every candidate request to finish instead of treating
  loading placeholders as unavailable covers.
- All 44 new acceptance baselines were inspected. The final three organization
  captures were centered away from fixed shell controls and compared again
  without updates. Existing Review/Library baselines were not regenerated.

Initial host-browser attempts over `kubectl port-forward` stalled while loading
static assets. Completed live checks used the pinned Playwright browser over an
SSH tunnel; this pass does not establish the cause of the earlier runner/transport
failure. Remote image failures, deliberately blocked notification writes and
navigation-aborted background requests are distinct from JavaScript exceptions.
No live repair plan, wishlist request, scan or music-file mutation was executed
as a test. Mutating acceptance checks used disposable libraries and databases.

All A1–A10 requirements have implementation and repeatable acceptance evidence.
No known Stage 3 implementation task remains open within the scope below.

### Coverage boundaries

This is a fixed, finite acceptance corpus, not proof of universal matching
accuracy. Tiny tagged fixtures do not exercise fingerprint extraction from real
audio or remote catalogue availability. Browser mutation responses are fixtures;
the corresponding persistence, rejection and exact effects are exercised by the
Python tests. Live verification is read-only. Broader negative-control and
fulfillment tests remain necessary alongside this corpus.

## Stage 2 recovery — authorized 2026-10-20

Baseline: `c836973fe` (application code deployed as `6f42d601ba6e`).
Stage 1 is complete; the user now authorizes Stage 2. Preserve its matching,
inventory, saved-choice authority, readiness, and signed-apply behavior.

### Acceptance checklist

- [x] T1. Explain the actual unresolved issue before acceptance controls and
  playback. Show the proposed result before technical evidence. Cover ambiguous
  audio, conflicting versions, metadata, duplicates, blocked previews and
  missing tracks without inventing stronger evidence.
- [x] T2. Keep current/planned values comparable at ordinary inspector widths;
  preserve strikethrough, direction, add/remove labels, full values and copy
  controls. Verify the shared component's Library consumer as well as Review.
- [x] T3. Ready albums have one primary apply area and one field summary.
  Completed prerequisites and supporting context do not overshadow outstanding
  choices. Preserve editing saved choices and access to individual changes.
- [x] T4. Reduce header/control overhead and fix internal clipping. Phone and
  tablet users can browse/filter/page the queue without a permanently expanded
  album browser above their task. Preserve keyboard focus, navigation, filter
  state, and returning from track details.
- [x] T5. Selection remains recognizable independently of green success and
  amber/red severity. Improve heading/body hierarchy and useful information
  density using existing controls/tokens, retaining semantic track colors.
- [x] T6. Album-wide release/completeness/numbering choices explain the proposed
  outcome and dependencies. Missing-track selection, duplicates, pending file
  actions, waiting/cancelled requests and manual assignment remain reachable.
- [x] T7. Selected-release evidence uses the exact matching source/release ID,
  title and edition metadata. Local identity stays local. Missing catalogue
  metadata is honestly absent, never borrowed from a different candidate.
- [x] T8. Maintain durable Review visual fixtures and behavioral/layout checks,
  compare identical data before/after at desktop/tablet/phone widths, inspect
  screenshots, run affected tests/check/build, deploy and inspect the live UI.
  No live repairs are applied merely for testing.

Measurements and results will be recorded here as each item is verified.
Known baseline issues include a 520px comparison breakpoint that stacks field,
current, arrow and planned into four rows, giant default inspector headings,
duplicate ready/field summaries, green selection borders, and a 390px header
whose nowrap count can clip inside the shared header's overflow boundary.

### Implementation and verification

- T1: ambiguity, competing identities/files and missing-track explanations now
  precede proposed edits, decisions and playback. Duplicate keeper evidence
  precedes the generic fallback action. The existing duration warning remains
  first and is no longer repeated in a second card. The no-named-fingerprint
  warning retains its original applicability; no evidence threshold changed.
- T2: comparisons keep current → planned columns together, with the field label
  above at narrow widths. Strikethrough, additions/removals, unchanged values,
  full text and copy controls remain. On identical captured data, the two-field
  comparison at an 1800px viewport shrank from 286px to 183px high; at 390px,
  from 331px to 239px. The Library tag-preview consumer is covered at 1440/390px.
- T3/T6: ready plans have one apply surface and one field summary. Removed the
  duplicate completed timeline and ready-summary panel; supporting context still
  shows release, coverage, scope and numbering. Acquisition status and editing
  saved choices remain available below the primary task. Group decision headings
  link to their exact affected tracks, with an explicit inspection cue. Release
  dependencies are named. A stale ready flag cannot hide visible open decisions.
- T4/T5: narrower outer gutters/sidebar, consistent 20px inspector headings,
  compact facts, wrapped header counts, two-column phone actions and a collapsible
  phone/tablet album browser. Browsing preserves filtering and pagination and
  restores focus when an album is selected. Mobile track details clear the shell
  navigation button. Selection uses a neutral outline/inset marker on tracks,
  albums and release candidates; amber/red/blue/green retain their issue meanings.
- T7: catalogue title/edition come from the candidate with the exact source AND
  release ID. Local display titles and embedded album tags remain local; a stale
  or inspected candidate cannot supply another edition's metadata. The Plan
  context reuses release badges. Missing catalogue fields stay absent.
- T8 local gates: 167 focused UI tests; 94 backend queue/group tests; 14 browser
  workflow/layout checks including 1800/1280/768/390/320px; production build and
  check with zero errors and the same 485 existing warnings. Ten identical-data
  before/after capture scenarios cover Stone Sour, Split Chain, SOUR and Trilogy.
  Eleven pinned-container visual baselines cover Review and Library tag previews.
  Final screenshot comparison and deployment verification are recorded below.

Corrections during verification: the first ready-card draft inherited unwanted
blue button chrome, and hiding the completed decision board would have hidden
acquisition status on a ready album. Both were corrected before deployment.
Repeated numbering summaries and green candidate-selection backgrounds were
also caught in visual inspection and removed. Settled/saved assignments do not
revive an old ambiguity as a new review warning. Matcher, persistence and signed
apply code remain unchanged.

### Final deployment verification

- Application commits: `b5166befaca2` and `1283b55b6502`. Deployed
  `localhost/soulsync:test-1283b55b6502`; the maintained deployment script confirms
  1/1 ready and available. Flux remains suspended for this authorized feature
  review. No active transfers or repair jobs preceded the rollout.
- Final browser coverage also checks that selecting an album from the compact
  browser returns focus to its toggle, while Previous/Next album navigation
  retains its own focus. The paging fixture has an explicit summary response,
  avoiding an unrelated case handler swallowing that request.
- All eleven pinned-container screenshot comparisons passed without updating
  their baselines. The fourteen workflow/layout checks passed; the paging check
  passed again after the test-only fixture correction.
- Live inspection covered Plan and track details for Stone Sour, Split Chain,
  Joep Beving, Queen, Jeff Buckley and Bastille; Stone Sour's release candidates;
  and three phone cases. No browser exceptions, overflowing phone header tools,
  or clipped comparison tables were observed. Available catalogue artwork loaded;
  unavailable covers retained their fallback. The browser allowed the read-only
  organization preview but prohibited repair/apply requests.
- Before/after API snapshots for those six albums, covering 193 assignment rows,
  had no changes to album identity, folder, full matching plan, decisions, saved
  album plan, findings or plan signature. No live repair was applied.
- Stage 2 is complete within this acceptance scope. The checks establish the
  preserved behavior of these fixtures and live cases, not an exhaustive claim
  about every library recording or future catalogue response.

## Recovery assessment and Stage 1 — 2026-10-20

The user accepted the historical reassessment and authorized **Stage 1 only**.
Earlier statements that every original requirement was complete are superseded
by this assessment. Do not infer completion from component reuse, passing
existing tests, or absence of document overflow.

### Historical findings to preserve

- Both named sessions forked at `9c7465cc8`; `814ea7557` precedes architecture
  implementation and `827f04e30` precedes UI standardization. The initial UI
  rewrite is `23e3f0ddc`. Later recommendation policy changes start at
  `3d1d52244`; they are distinct from the UI rewrite and cache work.
- The assessment inspected source at `119abe4db`, deployed review code at
  `1a5406d9d9d0`, session logs, original edge cases, identical captured responses
  rendered through historical/current frontends, and local matching probes.
- Named-performance matching rejects compatible added location detail and
  equivalent textual/numeric dates, even with matching recording ID/duration.
  Historical evidence contained five distinct tracks affected by that new
  rule; this is not a claim that all five are currently unmatched.
- Album consistency conflates absent and contradictory metadata. A local
  integration probe with one stored release ID and one blank proposed removing
  the stored ID under a non-MusicBrainz release. It required review, but lacked
  evidence that the ID was wrong. Missing album titles can similarly disable
  preservation of useful nonempty titles.
- Unmatched intentional companions disappear: `_match_release` removes them
  from extras even when `_review_pairs` cannot attach them to an assigned source.
- New identifier removal/replacement operations outgrew UI summaries that
  unconditionally say “Add”. Consequential identity edits can be collapsed
  merely because another visible field also changes.
- Assignment confidence and required user input are distinct. Manual metadata
  proposals can be grouped and colored as ordinary changes; ready summaries
  still claim audio verification for metadata-supported/user-approved plans.
- Current motionblur ambiguity is not proven to be a new solver regression:
  both solver versions produce the same classifications on its current input.
  The selected provider/evidence also changed. The Evanescence AOL-session
  missing/extra case and overly broad artist-credit replacement policy predate
  these sessions.
- Preserve the improved decision authority, whole-album eligibility, signed
  apply/revalidation, relocation handling, validated pure-match caching,
  exact-file player, shared record inspector, artwork provenance and accessible
  controls. Cache parity does not prove recommendation correctness.
- Deferred Stage 2 findings: specific ambiguity explanations occur below
  controls/audio; comparison stacking is too tall at ordinary inspector widths;
  selection/success hues and muted hierarchy need improvement; accumulated
  header/control space reduces useful content; mobile header clips internally
  at 390px; album-wide concerns still need clearer decision presentation.
- Durable visual route baselines omit Review. Existing backend tests passed
  258/258 during assessment despite the reproduced failures. Later acceptance
  must cover exact effects, complete inventory and semantic presentation, not
  just successful rendering or DOM presence.

### Stage 1 scope and acceptance

- [x] S1. Compatible named-performance detail and equivalent dates remain
  assignable and preserve useful local wording. Distinct mixes, venues, dates,
  versions, song numbers and independent conflicting evidence stay protected.
  Check tag, filename and recording-disambiguation paths, not only one helper.
- [x] S2. Missing, conflicting and invalid album metadata are distinguished.
  Blanks cannot justify erasing a coherent ID or replacing useful titles.
  Existing explicit release selection and evidence-supported repairs remain
  available. Keep unsupported removal manual and explain actual evidence.
- [x] S3. Every inventory file remains represented after companion processing,
  including unmatched sources, explicit assignments and incomplete evidence.
  Matched companions must not consume slots or become false duplicates.
- [x] S4. Group/track summaries truthfully name additions, replacements,
  removals and mixed operations. File/library copies do not double-count tracks.
- [x] S5. Consequential identity edits are visible before approval even when
  title/filename fields also change. Raw identifiers may be secondary; actual
  effects may not be hidden.
- [x] S6. Manual decision state controls attention grouping/labels independently
  of matching confidence. Preserve saved choices, release-blocked previews,
  numbering dependencies, missing/extra relationships and whole-album readiness.
- [x] S7. Readiness and evidence copy never implies audio verification for
  metadata-only, missing-audio or user-confirmed evidence. Cover aggregates,
  findings, candidate previews and fallback paths.

Verification will include new regression cases with negative controls, affected
and wider planner/worker suites, focused frontend behavior tests, check/build,
historical evidence replay where available, and rendered desktop/mobile checks
of the Stage 1 presentation changes. Live repair/file mutations are not a
presentation test. Record actual results and remaining limitations below before
marking any item complete. Stage 2 layout work remains deferred.

### Stage 1 implementation and verification

- **S1:** Named qualifier facts now separate venue/mix names from dates. Date
  comparisons use shared precision (year, month, day), allowing English textual,
  ISO, day/month/year, unambiguous month/day/year, and abbreviated-year forms.
  Dates are excluded from numeric-title comparison only inside recognized
  qualifiers; main-title numbers remain material differences. Compatible
  performance-name detail can be a subset; distinct named remixes remain
  protected. Local tags, filenames and database titles compare with the release
  title; local recording comments compare with remote recording comments.
  Version markers still combine all layers. Event titles and venue comments
  are not interchangeable facts (see the live follow-up below). Sparse audio
  can still lower confidence: compatibility does not manufacture independent
  verification.
- **S2:** Blank values no longer create album consistency conflicts. A coherent
  release ID is retained when a sibling lacks the tag. Missing MusicBrainz IDs
  can be filled when the selected identity agrees; an explicit pin still permits
  replacing the identity. Non-MusicBrainz sources cannot erase the coherent ID
  because of a blank sibling. Missing album titles inherit a compatible,
  consistent local title under the preserve policy. Genuine disagreements and
  catalog-proven recording-ID conflicts retain manual review.
- **S3:** Companions leave the extra-file list only when their source has an
  assignment. Explicitly assigned companions are not projected a second time.
  Unmatched sources/companions remain in persisted review, including previews
  governed by an album release question. Matched companions consume no new slot.
- **S4–S5:** Backend decision summaries and frontend field summaries distinguish
  add/replace/remove/mixed effects. File/library copies count once per track;
  mixed effects on the same track count as one mixed track. Whitespace cleanup
  remains an actual edit. Identifier replacements/removals stay expanded beside
  filename/title changes, with explicit operation labels and both values.
  Identifier additions may be collapsed under an explicit “Add …” summary.
- **S6–S7:** Manual metadata decisions use attention grouping, amber row/group
  styling, a review label and a separate aggregate decision count. Matching
  confidence is unchanged. Saved choices and release/numbering previews retain
  their states. Readiness copy describes completed decisions without claiming
  universal audio support. User-confirmed assignments have distinct finding copy.

Verification:

- **643 Python tests passed** across reconciliation plan/end-to-end/authority/
  cache/inventory/catalogue/fulfillment/Tools suites, review queue/inbox, finding
  lifecycle, and recording-ID repair. New cases include real temporary-file
  scan/apply checks, sparse tags, explicit pins, genuine conflicts and companion
  inventory. Matcher regressions run both fresh and through persisted cache.
- **69 frontend tests passed** in the Review route. Check completed with
  **zero errors** (485 existing warnings); production app and shell builds passed.
- The browser spec `webui/tests/review-recommendations.spec.ts` uses production
  bundles with local API fixtures and blocked external requests. It covers
  manual and saved metadata plans at **1800, 1280 and 390 pixels**, grouped and
  release-order rows, ID effects visible before approval, evidence-neutral
  readiness, and screenshots. Inspection found an inherited fixed minimum width
  overflowing the mobile ready-summary card; the grid now respects its container,
  with a browser bounds assertion. This is a local summary fix, not Stage 2.
- Historical replay examined **13,497 paired rows**, checking tags and filenames
  against release titles. All **10 false-conflict comparisons on five tracks**
  identified in the assessment became compatible, preserving local display
  wording. No new named-title conflicts appeared in that paired-row corpus.
  This is not a full catalogue rescan or proof about every unmatched file;
  synthetic negative controls cover genuinely different performances.
- Local evidence logs/screenshots are under `/tmp/soulsync-stage1-*`; they are
  diagnostic artifacts, not committed application data. Findings, acceptance
  criteria, tests and these results are the durable record.

Live follow-up and correction:

- The first deployment (`7c60c9719eab`) refreshed Britney Spears, Evanescence,
  Jeff Buckley and Keane without applying repairs. Two previously assigned
  recordings became extra/missing. This failed acceptance despite the passing
  local suite; the build was not treated as the completed Stage 1 result.
- The cause was a new cross-comparison between title qualifiers and recording
  comments. Catalogue comments can describe the venue while titles describe
  the event, and can use different abbreviations/date precision. A read-only
  comparison of **12,693 paired rows** against cached catalogue recordings
  identified **17 additional newly incompatible comparisons** in existing plans.
  Examples include Roadburn Redux versus its recording venue, Fox Theater
  versus Theatre, and Unit 24 country wording. The already-unassigned two
  refreshed tracks were separately reproduced with their exact evidence.
- The correction restores comparison between corresponding fields, retaining
  existing version-marker boundaries and independent title/filename/DB-title
  conflict checks. Compatible recording comments can differ in detail; genuine
  conflicts between them remain blocked. Unicode-hyphen ISO dates and
  year/month precision have dedicated coverage. We did not add a venue alias
  catalogue or treat unfamiliar event/venue wording as proof of wrong audio.
- Repeating those **12,693 comparisons** with the correction found **zero newly
  incompatible comparisons** and two compatible-detail improvements. This is
  a compatibility audit of existing pairs, not a full solver replay or proof of
  every candidate's correctness. New tests also exercise complete assignment
  and cold/warm-cache paths for the two live failures and representative
  event/venue cases. The wider backend suite was rerun: **643 passed**.
- Desktop and phone checks against the running application confirmed visible
  ID replacement effects before approval, amber manual-metadata rows and
  evidence-neutral readiness text, with no browser exceptions. The narrow
  phone inspector and repeated ready summaries remain Stage 2 work.

Final deployed verification:

- Corrected code commit/image: **`6f42d601ba6e`** /
  **`localhost/soulsync:test-6f42d601ba6e`**. The maintained rollout script reports
  **1/1 Ready**; Flux remains suspended for the existing feature test.
- All four targeted refreshes completed without errors or incomplete catalogue
  evidence. Their **108 local files** retained the same inventory and embedded
  tags in the before/after API snapshots. No live repair plans were applied.

  | Album | Before: extra / missing | Final: extra / missing |
  | --- | --- | --- |
  | The Essential Britney Spears | 0 / 0 | 0 / 0 |
  | Fallen (20th anniversary) | 1 / 1 | 1 / 1 |
  | Grace (Legacy Edition) | 1 / 2 | 0 / 0 |
  | Hopes and Fears (Deluxe Edition) | 5 / 9 | 3 / 7 |

- Six targeted recordings (the original five plus Keane's Mill St Brewery
  performance) are assigned with no proposed title rewrite. Newly recovered
  Keane Forum/Airwaves and Jeff Buckley “The Other Woman” assignments remain
  uncertain/likely as their evidence warrants; compatibility does not promote
  them to automatic acceptance. All four albums remain ineligible for whole-
  album apply because decisions remain open.
- Fallen's leading candidate changes from two-disc vinyl to digital: both now
  have 20 assigned / 18 clear, so the existing digital-numbering preference
  breaks the tie. The seven disc-number previews remain gated on release
  selection. The inherited AOL-session version question remains unresolved.
  Grace's leading candidate now covers 22/22 instead of 21/23. These release
  ranking changes are recorded, rather than presenting identical queue counts
  as the success criterion.
- A final audit using the **deployed Python runtime and matching module**
  compared **12,697 paired rows**, with zero missing cached recordings and
  **zero newly incompatible comparisons** against the pre-Stage-1 policy.
- Final browser inspection on this image repeated desktop/mobile identity-edit
  and readiness checks and inspected the restored Evanescence/Keane rows.
  No browser exceptions occurred. Screenshots and exact API snapshots remain
  under `/tmp/soulsync-stage1-*`.
- Additional Stage 2 evidence-presentation finding: `AssignmentEvidence`
  populates the release-track album label from `album.album`. When a candidate
  differs from the local edition, that label can show local edition wording
  beside the selected candidate's ID (observed with Fallen's Target-exclusive
  wording). Stage 2 should distinguish current album identity from selected
  release metadata; this was recorded without expanding Stage 1.

**Stage 2 is still open.** The desktop inspector still stacks comparisons too
early, the plan repeats summaries, mobile/header space remains inefficient, and
selection/success hierarchy and broader decision ordering still need the
separate Stage 2 pass. Stage 1 completion must not be described as completion of
the entire original UI recovery plan. No live repair plans were applied for
verification.

Approved 2026-10-18. Baseline: `827f04e30`. This is the implementation ledger for the user's 22-part plan; keep it updated with evidence, limitations, and remaining work. No Operations redesign or matching-policy redesign. Preserve concurrent work and backend approval/signature, readiness, fulfillment, and relocation contracts. Never apply live repair plans merely to test presentation.

## Acceptance checklist

- [x] 1. Baseline: inspect branch/status/new changes, inventory consumers, capture representative unresolved/ready/release/duplicate/missing/waiting/operation screens.
- [x] 2. Tokens: replace queue literals with app surfaces/text/borders/accent/status/radius/motion/layers; shared spacing; remove unused audio/menu styles. Accent selects, semantic colors explain states.
- [x] 3. Badge/notice tones: information independent from accent; shared readable sizing, inspect existing consumers.
- [x] 4. Controls: shared buttons/inputs/selects/checkboxes/labels/errors, danger variant, pending states, disabled explanations, primary/secondary/tertiary hierarchy; links remain links.
- [x] 5. Dialogs: shared focus-safe foundation; preview-derived signed apply confirmation with actual effects; fresh preview on invalidation; no confirmation merely to save choices.
- [x] 6. Menus: extract Library menu and own CSS, portal/position/keyboard/disabled/danger/focus behavior; do not recreate unnecessary More menu; consistent layers.
- [x] 7. Playback: exact files, duplicate audition, no missing-file fallback, preserve listening queue, current-playing state, errors, explicit catalog preview; no second transport.
- [x] 8. Record inspector: generic shared component and styles, artist/track wrappers, shared accessible modal hook, fields/JSON/filter/copy/download/loading/errors, clearly separated DB vs review snapshots and available timestamps.
- [x] 9. Metadata comparison: shared Library/Review field-current-planned display with additions/removals/unchanged, scope, arrays/paths/IDs/copy/mobile; readable empty values and changes; independent write workflows.
- [x] 10. Track pieces: share identity/duration/position/filename/play/format/quality/provider/menu as appropriate; self-contained format styles; do not transplant enhanced table actions.
- [x] 11. Release metadata: shared neutral format/country/disambiguation badges and details; separate fit counts; worldwide/unknown/historical areas; preserve label/catalog associations and raw qualifiers.
- [x] 12. Providers: consistent names/monograms/URLs/icons/link hover/focus/IDs/copy; never equate source health with assignment confidence.
- [x] 13. Artwork: shared thumbnail/fallback/loading, exact candidate provenance; neutral missing candidate; no library-cover substitution.
- [x] 14. Feedback: task center owns durable operations; scoped inline state/errors; avoid repeated layout-shifting notices/live announcements; outcome/retry/revalidation clear.
- [x] 15. States: server whole-album readiness/counts/apply, auto-save, waiting/cancelled distinct from unanswered input; explicit album/track/action/field units.
- [x] 16. Dependencies: standard maintenance conflict explanation + exact review link; distinguish protected choice/dependency/error; no frontend authority logic.
- [x] 17. Navigation: follow canonical relocated case, preserve context, explicit prev/next labels; distinguish reload/reanalyze/catalog refresh and preserve approvals.
- [x] 18. Layout: shared page header, three hierarchy levels, fewer nested cards, compact album identity, quieter track rows, attention/outcome/choices/audio/evidence/technical inspector order.
- [x] 19. Interaction: tabs vs filters vs display toggle vs disclosures vs candidate inspection/selection; keyboard semantics, stable expansion, nested actions safe, compact return-to-plan.
- [x] 20. Accessibility/mobile: keyboard/focus/dialogs/live regions/text+color/touch/long content/reduced motion; deliberate list-detail return with preserved position; player/task controls do not cover actions.
- [x] 21. Existing consumers: Library and artist adopt extracted components; no duplicated route CSS or mutation coupling; no Operations replacement.
- [x] 22. Verification: focused behavioral tests + affected suites, check/build, browser matrix at multiple sizes/accents/states, deploy if useful, fix findings, record exact evidence and limitations.

## Existing functionality to preserve

Global local-file player, track record endpoint, release details/badges, correct candidate artwork fallback, automatic readiness, whole-album batch selection, signed preview, waiting/cancelled states, verified relocation, accepted-decision authority.

## Execution order

Baseline → tokens/controls/dialogs → playback/inspector → comparisons/track/release/provider/artwork → layout/state/navigation → accessibility → cross-consumer and deployed validation.

## Progress

- Baseline code and local instructions reviewed; source clean at start. Infrastructure instructions reviewed. Implementation in feature worktree only.

### Implementation checkpoint

Shared foundation and integrations are implemented: theme tokens, ordinary controls, semantic badges/notices, signed preview dialog, shared Library menu, queue-preserving exact-file audition, generic record inspector, shared metadata comparison, duration/format/artwork/provider/release components, contextual maintenance dependency notices, tab keyboard navigation and mobile detail/back flow. Label/catalog associations now remain paired in catalogue evidence. Existing backend decision authority and matching logic are unchanged.

Verification so far: 1,984 frontend tests passed (116 files); 160 backend tests passed. Local production-asset preview against read-only live data rendered Plan/Release/Tracks and mobile details with no page exceptions or document overflow. Visual review corrected structural-button chrome and overly saturated ordinary text. Baseline deployment is `localhost/soulsync:test-11596193ae4a`, healthy, Flux already suspended for feature review. Subsequent deployment checks and corrections are recorded below.


## Task evidence and implementation decisions

| Tasks | Implementation and verification |
| --- | --- |
| 1 | Reviewed the current feature worktree and backend contracts before changes. Captured live unresolved, ready, edition, missing-track and long-album views. Waiting/cancelled/error states use browser response fixtures because no live albums were waiting during inspection. |
| 2–4 | App surfaces, spacing, radii, accent, semantic states, focus and reduced-motion tokens now drive the queue. Shared Button, Select, TextInput and Checkbox serve ordinary controls. Structural candidate/assignment controls retain their specialized layout. Information tones no longer inherit the chosen accent. |
| 5 | Shared confirmation hook uses DialogFrame and server-signed previews. Every album/action remains inspectable, file-action counts are explicit, organization destinations are shown when applicable, and open decisions remain excluded by the server. Preview cancellation was exercised in the deployed browser; no plans were applied. |
| 6 | Library's ActionMenu is a shared component with its own styles, portal, positioning and keyboard behavior. Queue catalog refresh uses it; routine scan/reload actions are directly available. Library and Discover consumer tests cover the shared extraction. |
| 7 | Review calls the existing global player with an exact-file audition option. It reuses or inserts a queue entry without clearing the listening queue. Missing files cannot enter metadata acquisition, and failed auditions do not skip into unrelated music. Tests execute the actual legacy player functions, including empty queues and duplicate entries. Both comparison buttons share pending state. |
| 8 | Artist and track inspection share RecordInspector. Fields, JSON, filtering, copy and download remain available. It owns its styles and uses the shared accessible modal hook. Current fetched database values and review-time embedded tags/audio evidence are explicitly distinguished; timestamps present in records remain visible as fields. Browser checks cover artist and track consumers plus mobile JSON and focus return. |
| 9 | Library tag previews and review proposals use MetadataComparison. Empty values, zeroes, arrays, long strings, copy feedback and literal markup are handled consistently. Current and planned values stack within narrow containers. Library writing and queue planning retain their separate server workflows. |
| 10 | Reused duration formatting, format badges, artwork, provider references, menus and global playback. The review assignment table remains purpose-built; Library download/delete/enrichment actions were not imported into it. |
| 11–13 | Release format/country/qualifier badges stay separate from fit/confidence counts. Label/catalog pairs survive serialization, with compatibility for older flattened evidence. Provider references share naming, URLs and copying; audio IDs remain links. Shared artwork resets errors on URL changes and uses neutral fallback art without substituting the library cover for a release candidate. |
| 14 | Durable work continues through the existing operation/task system. Album errors stay scoped to their album, global apply/scan errors stay at queue scope, and loading counts are labelled as loading instead of briefly reporting zero. Tests cover operation restoration, global failure scope and navigation races. |
| 15–17 | Preserved backend whole-album readiness, automatic staging, exact-case navigation, relocation and accepted decisions. Waiting requests are distinct from cancelled requests; cancelled evidence no longer claims a download is pending. Tools dependency notices share a standard component with exact review links. Reload, reanalysis and catalog refresh remain distinct actions. |
| 18–19 | Shared page header and quieter surfaces/rows reduce visual competition. Proposed result and choices precede audio/evidence. Album tabs expose tab semantics and arrow/Home/End navigation; filters, disclosures and candidate inspection retain their own interaction meaning. |
| 20 | Mobile track inspection uses list/detail navigation rather than a second fixed overlay. Returning restores row focus and saved scroll position. Browser checks at 390, 900 and 1800 pixels found no document overflow; keyboard tab/menu/inspector return checks passed. Extra page-bottom space keeps final actions above floating player/task controls. |
| 21 | Library/artist wrappers consume the extracted components; shared styles do not rely on the queue stylesheet. Operations remains a specialized maintenance area. No authority or matching-policy redesign was introduced. |

### Review findings corrected

- Removed excessive shared-button chrome from structural navigation and reduced saturation on ordinary text.
- Added spacing and usable disclosure targets to large apply previews; fixed singular action labels.
- Displayed explicit loading state while ready counts/albums are fetched.
- Matched pending playback behavior for both local copies.
- Corrected cancelled-request evidence wording.
- Kept failures of global operations at queue scope even while an album is selected.
- Extended existing modal suppression of floating player/help/activity controls to shared dialogs and the record inspector, keeping mobile footer actions unobstructed.

### Verification boundaries

Browser tests block repair/file/library mutations. Preview dialogs are opened and cancelled; fixtures simulate absent waiting/cancelled/failure states without changing persisted decisions. Actual player orchestration is covered by executable tests; verification does not download replacements or apply user plans. The full reconciliation job is unnecessary for these presentation changes: existing plans remain readable, and paired catalog labels are populated when catalog evidence is next analyzed/refreshed. Existing flattened label data remains supported.


## Final verification and handoff — 2026-10-18

All 22 tasks are complete. The final code image is `localhost/soulsync:test-bd93669aeb4b`, deployed and Ready 1/1. The final documentation commit only updates this ledger. The feature deployment remains available for user review; SoulSync Flux was already suspended when this work began and remains suspended. GitOps manifests were not changed.

- Backend: **160 passed** across `tests/library/test_reconciliation_end_to_end.py` and `tests/test_repair_review_queue.py`.
- Broad frontend regression: **1,984 passed in 116 files**, covering Review, Library, artist detail, Tools, shared components and actual player contracts.
- Subsequent affected suites passed after corrections: **72 tests** for review/components/player/Discover menu; **37 review tests** after the global-error scope fix; **71 shared-modal/record/review tests**; **75 existing mobile CSS/modal tests**. These overlap the broad run and are not additive totals.
- `npm run check`: **0 errors**, with **551 existing warnings**. Production builds succeeded, including the final image build.
- Browser validation covered Plan/Release/Tracks at **390, 900 and 1800 pixels**, alternate accent styling, row focus restoration, tab keyboard navigation, menu Escape/focus, signed apply-preview cancellation, track database inspection, Library rendering, artist inspection and Library tag comparisons. No page exceptions or document overflow were found in the completed checks.
- Separate browser response fixtures verified waiting, cancelled requests, restored running operations and failed case reads. They did not alter live decisions or wishlist entries.
- Final deployed modal checks: **7/7 passed**, with no page exceptions. These verify normal Right-arrow tab navigation, immediate suppression/restoration of floating controls, unobstructed Cancel and Apply hit targets on mobile, and record-inspector restoration. The final run used served assets with no injected CSS.
- The browser harness initially forced focus onto a tab outside the normal roving-tab sequence; keyboard behavior was rechecked using normal selection/focus. Slow background resource loads were separated from explicit rendered-content waits. Neither required an application behavior change.

### Final assessment

The queue now shares the application's visual primitives and core interaction components while retaining its album-decision workflow. Library and artist consumers use the same extracted components. Whole-album readiness, signed execution previews, decision authority and relocation contracts remain server-owned. The only backend addition preserves label/catalog-number associations in release evidence.

The final visual pass corrected mobile modal overlap from both floating controls and transient toasts. Global toasts remain in notification history and sit behind shared dialogs; dialog feedback stays local. No live repair plans, replacement requests or file writes were executed as a test. No implementation tasks remain open in this plan.

## Reopened acceptance review — 2026-10-18

The prior completion claim is superseded by the user's visual and workflow audit. Shared infrastructure is implemented, but acceptance is reopened for semantic state colors, comparison cues, evidence spacing, sticky opacity, artwork delivery/loading, individual missing-track choices, count scopes, and cross-consumer parity. The next pass must verify capability by album state and inspect rendered before/after views, including scrolled content, rather than treating rendering and overflow checks as visual acceptance.

- [x] Restore semantic rows/group headers, selected-state independence, comparison arrows/strikethrough/unchanged cues.
- [x] Fix evidence spacing, compact release facts, quiet scan history, opaque sticky completion notices.
- [x] Verify exact candidate artwork and shared loading/failure behavior in Review and Library.
- [x] Provide individual missing-track choices and scope editing in all applicable album states.
- [x] Make filtered versus global ready counts coherent without changing server apply authority.
- [x] Recheck all original tasks, shared consumers, desktop/mobile, focus, data states, and prior-version parity; record actual evidence and remaining limitations.


## Regression acceptance and original-plan recheck — 2026-10-18

This section supersedes the earlier handoff assessment. The initial unification implemented the shared components but did not preserve every visual cue or make every workflow reachable. The corrected implementation restores those capabilities while retaining shared components. Baseline comparisons used the pre-unification source (`827f04e30`), the prior unified image (`bd93669aeb4b`), the user’s screenshots, and rendered corrected views.

### Corrections and plan coverage

| Original tasks | Recheck and correction |
| --- | --- |
| 1, 22: baseline and acceptance | Revisited the actual rendered states, including scrolled content. Rendering without overflow alone was insufficient: the first followup browser pass still exposed a translucent sticky surface, which was corrected and checked against the actual scrolling ancestor. |
| 2–4: tokens, semantic tones, controls | Restored blue proposed changes, amber uncertainty, red missing/extra states and green confirmation, including group headings and status pills. Selection uses a separate outline. Removed ordinary-button rules that overrode structural controls. Semantic colors remain independent of the selected app accent. |
| 5–8: dialogs, menus, player, records | Existing shared integrations retained. Apply previews remain server-authorized and cancellable; missing tracks have no local player. Track record inspection, artist record inspection, keyboard/focus return and mobile dialogs were rechecked. Choice saving remains separate from execution. |
| 9–10, 21: comparisons and shared consumers | Shared MetadataComparison now strikes replaced/removed values, uses arrows for changes and checks for unchanged values, and preserves these cues when stacked on mobile. Library tag previews receive the same correction. Artwork loading placeholders and loaded/error resets are restored for Library as well as Review. |
| 11–13: release information and artwork | Compact facts no longer dominate release identity and assignment counts. Readable neutral qualifier badges remain. Missing MusicBrainz image URLs resolve to that exact release’s Cover Art Archive URL in both candidate serialization and the UI; unavailable covers stay neutral rather than borrowing library art. Real release images loaded successfully in the browser. |
| 14–17: feedback, readiness, dependencies, navigation | Completed reconciliation is quiet header context; running and failed operations retain visible feedback. Global ready-album and ready-action totals now share the same global scope even under queue filters. Server whole-album eligibility and signed execution remain unchanged. Individual missing-track selection now works for already-complete albums as well as unresolved scope decisions; the endpoint validates the current eligible subset. Existing navigation/relocation and decision contracts remain intact. |
| 18–20: layout and interaction | Removed compounded evidence margins and empty action space. Completion notice is opaque and clickable when scrolled. The Tracks checklist supports individual/all selections; a missing-track inspector offers a direct request and a focusable route to the checklist. Mobile Back to tracks restores the actual scroll-container positions and row focus rather than only window scroll. |

### Workflow parity

- Release inspection/selection, numbering choices, current-value choices, assignment changes, album scope, duplicate/file decisions, record inspection, playback and signed apply previews remain available through their existing contracts.
- Missing-track requests support both all actionable tracks and an explicit subset. Unselected tracks remain wanted when the album is complete; choosing a partial album is still a separate scope decision. Already requested or fulfilled tracks cannot be resubmitted through subset IDs.
- Global apply remains limited to fully decided eligible albums. The count correction does not broaden execution authority or apply decisions from albums with outstanding input.
- Waiting, cancelled, operation-in-progress and failed-case presentations were checked with browser response fixtures. No fixtures were persisted to the live database.

### Verification for this regression pass

- Backend: **161 passed** across review-queue and reconciliation end-to-end suites; **9 passed** in API parameter tests, including malformed and subset request cases.
- Broad frontend: **1,980 passed in 115 files**, covering Review, Library, artist detail, Tools, shared components and player contracts. A subsequent affected-consumer run passed **652 tests in 46 files**, including Discover. These runs overlap and are not additive totals.
- Final followup: **43 passed in 11 files**. `npm run check` reported **0 errors / 551 existing warnings**; production builds succeeded.
- Deployed main regression browser matrix: **72 checks**, no page exceptions or failed assertions. Covered Plan/Release/Tracks at 390, 900 and 2048 pixels, unresolved/ready cases, real candidate images, missing-track selection, record inspection, preview cancellation, and waiting/cancelled/running/failed fixtures.
- Followup browser checks cover direct single-track request cancellation, checklist focus, actual ancestor scroll restoration, opaque sticky completion, accent-independent semantic colors and keyboard tabs. Screenshots were manually inspected in addition to assertions.

### Verification boundaries

Browser validation deliberately blocks repair, library and file mutations; request/apply dialogs are cancelled. Backend and component tests exercise the mutation contracts. No user plans, requests or music files were changed as test data. Artwork depends on remote availability: an exact edition with no accessible cover displays a neutral fallback. The passing matrix supports the reviewed workflows and representative widths/states; it is not a claim to have exhaustively tested every possible catalog response or device.

### Final deployed verification

Code image `localhost/soulsync:test-afb0333c369d` is deployed and **Ready 1/1**. The six followup browser checks passed against served assets with no local CSS/asset injection and no page exceptions. Library artist-record filtering/JSON/focus return and desktop/mobile tag previews were repeated successfully on the final deployment; mobile screenshots show unobstructed dialog actions and no horizontal document overflow. Network logs include deliberately blocked mutation/background requests and unavailable remote resources; these are not reported as an entirely error-free network trace.

Music automation was paused for the rollout and restored to its prior enabled state immediately afterward; video automation was unchanged. The feature image remains available for user review, with SoulSync Flux still suspended as it was before this work. GitOps manifests were not changed.

All original checklist items have been rechecked within the documented scope, and the reopened regression items are resolved. No known implementation item remains open. The previous acceptance mistake was treating component reuse and successful rendering as sufficient; this pass additionally checked preserved visual meaning, reachable per-track actions and actual scrolling behavior.

## Adversarial followup — 2026-10-18

The next audit exposed gaps beyond the earlier representative-state checks. Fixed queue refresh responses and totals with one shared request-version/scope guard, including stale errors; made global ready-batch navigation clear search/category filters; kept routine queue and album success feedback non-sticky; and made missing-track instructions respect partial-album intent.

The subsequent pass also found that reopening an already visible ready batch could clear the selected album without triggering a fetch. The button now preserves selection when the scope is already correct, including while results are loading. Refreshing an initially empty selection also selects the first returned album, and superseded album-refresh failures cannot replace newer feedback.

Verification: 43 Review tests passed, including delayed manual refresh responses, global batch filters, repeated ready-batch navigation and partial-album instructions. Type checks reported zero errors with the existing 551 warnings. Browser inspection reproduced the original notice collision (both notices at y=8); corrected feedback scrolls offscreen while the completion notice remains opaque at y=8. Browser checks also verified cleared batch search and partial-album copy using a response fixture; no live scope choices or requests were changed. Final deployment verification follows below.

Final followup deployment: `localhost/soulsync:test-b07510dd39bb`, Ready 1/1. Served-asset checks passed for notice separation, clearing ready-batch search, repeated ready navigation and partial-album wording. The six existing missing-track/mobile-scroll/focus/sticky/accent/keyboard checks also passed with no page exceptions. Screenshots were inspected. An initial parallel browser run was interrupted by a Kubernetes port-forward stream timeout; sequential reruns through a fresh connection completed successfully. Music automation was restored to its prior enabled state; Flux remains suspended for the feature-review image. No live repair plans, wishlist requests or file edits were executed as test actions.

## Readability and information hierarchy pass — 2026-10-18

Reviewed the queue alongside the existing shared page header, form controls, badges/notices, provider references, metadata comparison and native disclosure patterns used in Tools. This pass keeps semantic state colors and the specialized album workflow while reducing competition from secondary information.

- Plan presents unresolved choices before already settled release/numbering summaries, preserving dependency order among unanswered decisions.
- Track detail leads with the track question; decision navigation is quieter. Accept and keep-current actions are together immediately after the proposed changes. Alternative assignment/reset controls remain reachable in an explicitly labelled disclosure.
- Mixed proposals show readable values first and list the count of identifier changes in an expandable section. Identifier-only proposals show their exact values immediately. All changes still use the shared comparison component, including strikethroughs, arrows, copy controls and mobile stacking.
- Track header counts form a compact row; source IDs are available under selected-release details. Plan context leads with the release title instead of an internal provider identifier.
- Missing-track requests stay compact while reviewing unrelated tracks. Selecting a missing row or the Missing filter opens the chooser; the direct Choose missing tracks action also opens and focuses it. Unresolved completeness choices remain visible.

Verification before rollout: 46 Review tests passed; type/format checks passed with zero errors and the existing 551 warnings; production build passed. Eight browser checks passed using production assets and read-only live data, including primary-choice placement, reachable alternative assignments, identifier expansion, automatic missing-track expansion, desktop/mobile layouts and release navigation. Screenshots were visually compared with the prior deployed layout. Primary choices now fit in the mobile inspector before optional reassignment and evidence. No plans or requests were executed during browser inspection.

Hierarchy rollout verified: `localhost/soulsync:test-bd5157e0e1c1`, Ready 1/1. All eight hierarchy browser checks passed on served assets, and the six existing missing-request/focus/mobile-scroll/sticky/accent/keyboard checks passed with no page exceptions. Deployed desktop/mobile screenshots were inspected. Music automation was restored to its prior enabled state. The feature image remains available with Flux suspended; manifests and live repair decisions were unchanged.

## Adversarial state and responsive-layout pass — 2026-10-18

Concrete findings and corrections:

- The prominent suggested-assignment action could contradict wrong-audio/version evidence and selected the first matching recording ID even when that recording occupied multiple release positions. It now requires the exact recording/disc/track slot and is absent for conflicting audio, duplicates, waiting downloads, missing files, and numbering previews. Manual reassignment remains available for reviewable local files.
- Alternative-assignment drafts survived release/options updates. The editor now resets with its finding, file, release, and available slots; busy editors cannot change selection. Album-completeness selections also reset when their release or missing-slot set changes and are disabled while saving.
- A Plan decision could open Tracks with the mobile inspector hidden. Conversely, field-summary links could retain an old inspector instead of showing the requested list. Dedicated navigation helpers now open/focus the requested detail or list, and pointer/keyboard tab entry behaves consistently. Filtered lists select a matching row, and Previous/Next decision follows those visible assignments rather than unrelated album-level findings.
- Late manual-release lookup and pin responses could populate a subsequently selected album; editing the lookup input could also leave an obsolete result. Responses now respect the selected album and lookup version, and album transitions clear the lookup input.
- Visual inspection found a later `flex: 1 1 400px` override defeating the narrow-screen header rule and leaving hundreds of blank pixels above navigation. Consolidated the header rules so the responsive basis applies. Long titles/buttons still wrap naturally at 320 pixels.
- Fixed empty display-number fallbacks and singular group/checklist labels; materialized filtered row groups once for their counts and rendering.

Verification: **59 review tests passed in 12 files**; production build succeeded; static checks reported **0 errors / 551 existing warnings**. The browser matrix covers 320, 390, 768, 1024, 1450 and 2048 pixel widths, requested-decision visibility, filtered row selection, keyboard tab entry, natural header spacing, and horizontal overflow. Phone, tablet and desktop screenshots were inspected, including the header before/after. Existing disclosure/primary-action checks passed. A cancel/reopen apply-dialog test was also rerun successfully after a timeout in the first highly concurrent run. Live browser repair/file mutations were blocked; lookup races and saved-choice guards were tested with mocked responses.

Deployment verification: `localhost/soulsync:test-6fb41dd0a41b` is ready 1/1. All **48 checks across six widths** passed again on the deployed image, followed by **6 passing workflow checks** for direct missing requests (cancelled), checklist focus, mobile scroll restoration, opaque scrolled completion, accent-independent status colors, and keyboard tabs. Both browser runs reported no page exceptions. Music automation was restored to its prior enabled state; video automation remained enabled. Flux remains suspended for the existing feature-review deployment. Worktree source is committed; no live repair decisions or music files were modified by the tests.

## Branch cleanup — 2026-10-18

Scope is this branch's additions relative to `upstream/dev` (`e70549362792`, also the merge base). Local `dev` is older and would include unrelated upstream changes. Removed unused frontend saved-plan helpers, provider re-export files, a backend selector used only by tests, 12 unused CSS classes, 34 superseded declarations, and references to an undefined button class. Review now uses its existing apply API helper, one track-group renderer, and album-model helpers for metadata summaries. The shared record inspector calls the portal directly and uses a generic payload type while preserving the artist adapter's type export. Provider tests live beside the shared component; readiness tests exercise the production preview selector.

Verification: **103 frontend tests**, **84 backend tests**, format/type checks (**0 errors / 550 existing warnings**) and production builds passed. Both the saved pre-cleanup build and cleaned build passed the same **48 browser checks** at 320, 390, 768, 1024, 1450 and 2048 pixels. Screenshots were compared and visually inspected; no layout or semantic-color regression was found in these samples. The **6 workflow checks** and **3 Library consumer checks** also passed without page exceptions, including the mobile record inspector and focus return. Remote resource failures and deliberately blocked background writes are separate from those assertions. Browser checks cancelled mutation dialogs; no live plans or music files were changed. No service rollout was needed for this behavior-preserving cleanup; the previously deployed feature image remains in place.
