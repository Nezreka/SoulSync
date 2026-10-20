# UI reuse and consistency implementation

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
- [ ] T8. Maintain durable Review visual fixtures and behavioral/layout checks,
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
