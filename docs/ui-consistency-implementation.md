# UI reuse and consistency implementation

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
