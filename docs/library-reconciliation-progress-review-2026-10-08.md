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
