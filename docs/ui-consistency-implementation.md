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
