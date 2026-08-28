# downloads page, full visual + ux review

date: aug 26. method: booted a review server on :8010 against a copy of the live db, seeded 22 tasks across 3 batches covering every state (downloading, searching, processing, queued, completed, failed with error text, cancelled, retry chip, verified/unverified/quarantined), then screenshotted every tab, sub-view and viewport (1920 / 1366 / 390) with playwright. screenshots were working artifacts and are not kept in the repo — the rig that makes them is described above and in the downloads-page memory note. code read: all of `webui/src/routes/active-downloads/` plus the `.adl-*` blocks in `style.css` (~65100-66200) and `mobile.css` (~3607-3646).

verdict up front: the bones are genuinely good. batch color rails, phase narration, the review pipeline, per-row audit, the clients hub, none of that needs functional touch. what's wrong is that the page has no visual hierarchy, no progress feedback where it matters, three generations of pill bars stacked on top of each other, and it literally breaks at 1366px. every fix below is presentation, the engine underneath stays.

---

## 1. what's actually good (keep all of this)

- batch color rail on rows matching the batch card in the panel. the one piece of visual language that works.
- the batch cards themselves: thumb, phase line with spinner, "now downloading" line, segmented progress bar, stat chips. densest and best-designed element on the page.
- status dot + spinner semantics, quality chips, retry chip with the acoustid shield.
- honest empty-state copy ("no transfers right now, all quiet", "the bin is empty"). the words are right, the presentation is bare.
- row click to expand live narration (#1156), whole-row audit in review view.
- the review pipeline ux concept: unverified / quarantine / deleted with per-state bulk actions and explainer text.
- clients hub concept: three clients, downloads/uploads split, filter + sort, magnet add.

## 2. hard bugs (fix regardless of redesign)

**2.1 layout collision at 1366px.** `laptop-01/03` shots: the count text "4 active / 7 queued / 51 total" runs UNDER the Cancel All button, and the batches summary strip overlaps Cancel All / Clear Completed. mechanism: `.adl-controls` is `justify-content: space-between` with no wrap, the count and both buttons are `white-space: nowrap`, and the batch panel is a fixed 366px. at 1366 minus the 240px sidebar the main column is ~750px and the controls just bleed right into the sticky panel. 1366 is the most common laptop width on earth. severity: broken, not ugly.

**2.2 count disagreement on the review pill.** top pill says "Unverified/Quarantine 3" (badge from `/api/review-queue/summary`), the sub-view pill says "Unverified (4)" (rows actually rendered). two sources of truth on screen at once, one of them wrong. `desktop-03` shot.

**2.3 "Batches (2)" header over three cards.** the header counts active batches, the list renders active + recently done. reads as a bug even when it isn't.

**2.4 mobile review rows lose the track title entirely.** `mobile-03`: the acoustid badge + quality chip + five icon buttons squeeze the title to zero width. rows are artwork + badges with no name. unusable for the "which track is this" decision the view exists for.

**2.5 mobile clips right-edge controls.** "Clear Completed" is half offscreen (`mobile-01`), the "slskd connected" chip clips to "slske" (`mobile-06`), the sub-view pill row overflows with no scroll affordance (`mobile-03`, "Deleted" cut, "Approve all" wrapped mid-word).

**2.6 the setup-help toast squats on content.** "New here? Click for setup help!" floats over list rows on every viewport, worst on mobile where it covers a full row. needs a dismissal or to yield on this page.

## 3. hierarchy and layout

**3.1 the flat list fights the batch panel.** the main list is a flat status-ordered dump while the batch story lives only in the right panel. result at 1920 (`desktop-01`): twelve nearly identical rows saying "Melanie Martinez · HIT ME HARD AND SOFT" and "Liked Songs · Track N of 12 · Soulseek" twelve times each, same artwork twelve times. the page's core structure (batches) is invisible in the page's main column. this is THE structural decision to revisit: group the main list by batch, collapse the repetition into one group header, and the panel stops being a second competing rendering of the same data.

**3.2 no page summary.** the only aggregate is a 0.78rem 30%-white string top right. a downloads page wants a glanceable hero: N active with combined speed, N queued, N failed, N waiting review. right now that information is scattered across four corners.

**3.3 downloading rows show no progress.** the list's active rows say "Downloading" with a spinner. no bar, no percent, no speed, no eta. the segmented bar exists only inside batch cards, and per-track progress only inside an expanded batch card's track rows. progress is the single most important signal on a downloads page and the default view has none of it.

**3.4 queued and completed rows cost as much as active ones.** every row is ~90px. seven queued rows and thirty-seven completed rows get the same visual weight as the four rows doing work. active deserves the height; queued wants ~48px compact; completed wants compact + collapsed beyond the first handful (a "37 completed, show all" fold).

**3.5 section headers vanish.** "ACTIVE (4)" / "QUEUED (7)" / "COMPLETED (37)" are tiny all-caps at low contrast, easy to scroll past without registering. they should carry the grouping weight the rows currently duplicate.

**3.6 dead column at 1920.** `.adl-layout` maxes at 1440 but the header strip and content column left-align inside a wider viewport with a void to the right of the batch panel (`desktop-01`). either center the 1440 block or let the list breathe wider.

**3.7 the h2 costs 60px for the word the sidebar already shows.** "Downloads" with an icon, then 24px margin. prime top-of-page space spent restating navigation. the summary hero from 3.2 should live there instead.

## 4. the three stacked pill bars problem

top bar: All / Active / Queued / Completed / Failed / ⚠ Unverified-Quarantine / ⛓ Clients. these are not the same kind of thing. five are status filters on one list, two are entire different pages. clicking Clients replaces the whole main column with a different product, but it's styled as sibling number seven of a filter row.

then the review view adds a SECOND pill bar (Unverified / Quarantine / Deleted) in the same visual style directly underneath, and clients adds a THIRD (Soulseek / Torrents / Usenet), plus a fourth row of downloads/uploads pills, plus a fifth row of filter + sort controls (`desktop-06`: five stacked control rows before content).

fix: separate the two levels of navigation.
- level 1, view switcher: **Downloads | Review (3) | Clients** as a real segmented control or underlined tabs, visually distinct from filters.
- level 2, contextual: status filter chips inside the Downloads view, sub-view chips inside Review, client chips inside Clients. different visual treatment (smaller, ghost style) so the eye never confuses the levels.

## 5. row anatomy

- title 0.9rem is fine; meta and batch lines are two separate low-contrast lines making rows tall. one meta line: `artist · album · track 5 of 12` and let the batch group header carry the batch name and source.
- status is words only, right-aligned, colored dot the size of a period. give status a real chip (colored bg pill) and give active rows an inline progress bar under the meta line with percent + speed.
- error text (`desktop-02-filter-failed`) is dark red on near-black, roughly 3:1, and wraps under the title making failed rows the tallest with the least-readable text. errors deserve a readable chip or a background tint on the row.
- expandable rows have zero affordance. nothing signals "click me" except a title tooltip. add a chevron that rotates on open.
- the per-row cancel is a red filled circle that appears on hover, heavier than any other row element. ghost icon-button that reddens on hover is enough.
- `2/3` retry chip is cryptic until the tooltip. "retry 2/3" costs nothing.

## 6. batch panel

- best element, but everything in it is one size too small: 13px icon buttons, 0.6rem stat chips, 4px progress bar. bump it a notch across the board.
- stat chips (✓3 / ✗3 / ↓1 / 1 queued) are outline pills in four colors with no legend; the ✗ red pill next to the ✓ green pill at that size reads as noise. consider a single line: "3 done · 3 failed · 1 active · 1 queued".
- segmented progress bar colors are never explained anywhere. tooltip or legend.
- "SYNC" / "ARTIST-DETAIL" / "WISHLIST" source tags: all-caps micro text in a bordered box, visually as loud as the batch name. lowercase ghost text is enough.
- "Recent History" label + "DOWNLOAD HISTORY" all-caps bordered button + chevron on one line: three different treatments for one control. make it a plain section header that expands.
- the collapse chevron top right of the panel is unlabeled and 30% white; nobody will find the panel collapses.

## 7. review view

- the explainer paragraph is right in spirit, but it's a permanent wall of 0.85rem text in a box pushing content down on every visit. show once, collapse to an (i) after.
- "ACOUSTID UNCONFIRMED" amber outline pill repeats full-width-caps on every row. the sub-view already says these are unverified. per-row it can shrink to an amber dot + tooltip, or just the reason on the expanded row.
- five icon-only 24px buttons per row (play, compare, audit, approve, delete), meaning invisible until hover, targets below 44px. keep icons but add labels on the two decisions that matter (Approve, Delete) and push play/compare/audit into the expanded row or a kebab.
- no multi-select. bulk is all-or-one ("Approve all" / "Delete all"). checkboxes + "approve selected" is the actual workflow for a mixed queue.
- Quarantine (0) and Deleted (0) render the full banner + empty text with bulk buttons that do nothing on zero items. hide bulk actions when the view is empty.
- the "keep forever" retention dropdown floats alone bottom-left of the Deleted view, no label, no context (`desktop-05`). caption it ("keep deleted files:") and anchor it to the banner.

## 8. clients view

- five control rows before any content (sub-tabs, dl/ul pills, filter+sort, magnet input on torrents). collapse to two: client tabs with health dots, then one toolbar.
- connection status is plain text ("connected", "not configured") with no color. green/gray/red dot on the client tab itself says it faster (`desktop-06/07`, `usenet` shot).
- the magnet input is a full-width bare input visually identical to the filter input above it, two lookalike text boxes stacked (`desktop-07-clients-torrent`). make add-torrent a button that reveals the input.
- empty states here are the barest on the page: one 0.85rem line, left-aligned in a void. centered icon + line + optional cta ("configure in settings") matches what the copy already says.
- "0 shown · ↓ 0 B/s · ↑ 0 B/s" stat line renders even when a client is empty-but-connected; fine, but at 390px it clips (`mobile-06`).

## 9. mobile (390px)

the stack order is right (list, then batches) and pills wrap, but:

- rows keep desktop anatomy: art + two text lines + right status column, so titles truncate to ~10 chars ("WILDFLOW...", `mobile-01`). mobile row wants: art, title full-width, status as a small chip under it, no right column.
- review rows: see 2.4, titles gone entirely.
- sub-view and client pill rows overflow with no scroll hint (2.5). horizontal scroll chips with edge fade is the house pattern elsewhere.
- the batch panel stacks below EVERYTHING, so on a busy page it's ~3000px down. on mobile the batch summary is arguably the more useful view; consider batches as a collapsible strip at top, or a bottom sheet.
- count strip + Cancel All + Clear Completed become three stacked full-width rows before content. fold cancel/clear into an overflow menu next to the summary.

## 10. contrast and accessibility

- muted text at 0.3 alpha (count strip, section headers, batch meta) is ~2.2:1 on this background, well under wcag 4.5:1. 0.55 alpha is the floor for anything that carries information.
- interactive targets: panel icon buttons 13px, review actions 24px, mobile needs 44px.
- status is communicated by color dot + word, ok, but the segmented progress bar is color-only. patterns or a legend for the color-blind.
- aria: pills carry aria-pressed (good), but the icon-only buttons rely on title attrs; the review actions want aria-labels (some have them via title only).

## 11. redesign direction (proposal, not started)

one line: keep the engine, rebuild the shell around batch grouping, a real view switcher, and visible progress.

- **header**: summary hero replaces the h2. big numbers: active (with live combined speed), queued, failed, review count. cancel all / clear completed as quiet buttons on the hero's right.
- **navigation**: segmented view switcher Downloads | Review | Clients. status chips (All/Active/Queued/Completed/Failed) live inside Downloads only, ghost style.
- **main list**: grouped by batch. group header = batch identity (thumb, name, source tag, phase, segmented progress, eta, cancel/filter), i.e. the current batch card promoted into the list. tracks under it as 48px compact rows with inline progress on the active one. completed groups collapsed by default. the standalone/history spill stays a flat "earlier" group.
- **right panel**: with batches in the main list, the panel either dies (preferred, one rendering of the truth, frees 366px and kills the 1366 bug at the root) or becomes a slim activity/history rail. my vote: kill it, move recent history into the Downloads view under an "earlier" fold.
- **rows**: chip status + inline progress + chevron affordance, per 5.
- **review**: banner chips + labeled approve/delete + multi-select, per 7.
- **clients**: tabs with health dots + one toolbar, per 8.
- **mobile**: dedicated row anatomy, scrollable chip rows, batches strip on top, per 9.
- **palette**: unchanged (dark + accent), just amplified: status colors carried by chips and rails, muted text lifted to readable alphas, one pill language per navigation level.

everything functional maps 1:1: filter-to-batch becomes the group header filter icon, batch modal opens from the group header name, audit/expand/cancel/retry chips all keep their seats. nothing is lost, it just stops being flat.

## 12. shot index (regenerate with the rig — see the method line at the top)

- `desktop-01-default-all` full page, the repetition problem
- `desktop-02-filter-*` per-status filters, failed shows error contrast
- `desktop-03/04/05` review sub-views incl count mismatch and orphan dropdown
- `desktop-06/07` clients soulseek/torrent/usenet
- `desktop-08` expanded row narration
- `laptop-01/03` THE 1366 overlap
- `mobile-01/03/06` truncation, lost titles, clipped chips
