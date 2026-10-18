# UI reuse and consistency implementation

Approved 2026-10-18. Baseline: `827f04e30`. This is the implementation ledger for the user's 22-part plan; keep it updated with evidence, limitations, and remaining work. No Operations redesign or matching-policy redesign. Preserve concurrent work and backend approval/signature, readiness, fulfillment, and relocation contracts. Never apply live repair plans merely to test presentation.

## Acceptance checklist

- [ ] 1. Baseline: inspect branch/status/new changes, inventory consumers, capture representative unresolved/ready/release/duplicate/missing/waiting/operation screens.
- [ ] 2. Tokens: replace queue literals with app surfaces/text/borders/accent/status/radius/motion/layers; shared spacing; remove unused audio/menu styles. Accent selects, semantic colors explain states.
- [ ] 3. Badge/notice tones: information independent from accent; shared readable sizing, inspect existing consumers.
- [ ] 4. Controls: shared buttons/inputs/selects/checkboxes/labels/errors, danger variant, pending states, disabled explanations, primary/secondary/tertiary hierarchy; links remain links.
- [ ] 5. Dialogs: shared focus-safe foundation; preview-derived signed apply confirmation with actual effects; fresh preview on invalidation; no confirmation merely to save choices.
- [ ] 6. Menus: extract Library menu and own CSS, portal/position/keyboard/disabled/danger/focus behavior; do not recreate unnecessary More menu; consistent layers.
- [ ] 7. Playback: exact files, duplicate audition, no missing-file fallback, preserve listening queue, current-playing state, errors, explicit catalog preview; no second transport.
- [ ] 8. Record inspector: generic shared component and styles, artist/track wrappers, shared dialog, fields/JSON/filter/copy/download/loading/errors, clearly separated DB vs review snapshots and available timestamps.
- [ ] 9. Metadata comparison: shared Library/Review field-current-planned display with additions/removals/unchanged, scope, arrays/paths/IDs/copy/mobile; readable empty values and changes; independent write workflows.
- [ ] 10. Track pieces: share identity/duration/position/filename/play/format/quality/provider/menu as appropriate; self-contained format styles; do not transplant enhanced table actions.
- [ ] 11. Release metadata: shared neutral format/country/disambiguation badges and details; separate fit counts; worldwide/unknown/historical areas; preserve label/catalog associations and raw qualifiers.
- [ ] 12. Providers: consistent names/monograms/URLs/icons/link hover/focus/IDs/copy; never equate source health with assignment confidence.
- [ ] 13. Artwork: shared thumbnail/fallback/loading, exact candidate provenance; neutral missing candidate; no library-cover substitution.
- [ ] 14. Feedback: task center owns durable operations; scoped inline state/errors; avoid repeated layout-shifting notices/live announcements; outcome/retry/revalidation clear.
- [ ] 15. States: server whole-album readiness/counts/apply, auto-save, waiting/cancelled distinct from unanswered input; explicit album/track/action/field units.
- [ ] 16. Dependencies: standard maintenance conflict explanation + exact review link; distinguish protected choice/dependency/error; no frontend authority logic.
- [ ] 17. Navigation: follow canonical relocated case, preserve context, explicit prev/next labels; distinguish reload/reanalyze/catalog refresh and preserve approvals.
- [ ] 18. Layout: shared page header, three hierarchy levels, fewer nested cards, compact album identity, quieter track rows, attention/outcome/choices/audio/evidence/technical inspector order.
- [ ] 19. Interaction: tabs vs filters vs display toggle vs disclosures vs candidate inspection/selection; keyboard semantics, stable expansion, nested actions safe, compact return-to-plan.
- [ ] 20. Accessibility/mobile: keyboard/focus/dialogs/live regions/text+color/touch/long content/reduced motion; deliberate list-detail return with preserved position; player/task controls do not cover actions.
- [ ] 21. Existing consumers: Library and artist adopt extracted components; no duplicated route CSS or mutation coupling; no Operations replacement.
- [ ] 22. Verification: focused behavioral tests + affected suites, check/build, browser matrix at multiple sizes/accents/states, deploy if useful, fix findings, record exact evidence and limitations.

## Existing functionality to preserve

Global local-file player, track record endpoint, release details/badges, correct candidate artwork fallback, automatic readiness, whole-album batch selection, signed preview, waiting/cancelled states, verified relocation, accepted-decision authority.

## Execution order

Baseline → tokens/controls/dialogs → playback/inspector → comparisons/track/release/provider/artwork → layout/state/navigation → accessibility → cross-consumer and deployed validation.

## Progress

- Baseline code and local instructions reviewed; source clean at start. Infrastructure instructions reviewed. Implementation in feature worktree only.

### Implementation checkpoint

Shared foundation and integrations are implemented: theme tokens, ordinary controls, semantic badges/notices, signed preview dialog, shared Library menu, queue-preserving exact-file audition, generic record inspector, shared metadata comparison, duration/format/artwork/provider/release components, contextual maintenance dependency notices, tab keyboard navigation and mobile detail/back flow. Label/catalog associations now remain paired in catalogue evidence. Existing backend decision authority and matching logic are unchanged.

Verification so far: 1,984 frontend tests passed (116 files); 160 backend tests passed. Local production-asset preview against read-only live data rendered Plan/Release/Tracks and mobile details with no page exceptions or document overflow. Visual review corrected structural-button chrome and overly saturated ordinary text. Baseline deployment is `localhost/soulsync:test-11596193ae4a`, healthy, Flux already suspended for feature review. Remaining: final current-source checks, deployment/browser matrix, any resulting fixes, and final assessment.
