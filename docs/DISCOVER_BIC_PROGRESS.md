# Discover best-in-class: implementation record

Companion to [DISCOVER_BEST_IN_CLASS_REVIEW.md](DISCOVER_BEST_IN_CLASS_REVIEW.md).
One entry per task id, in the review's own completion format (§12.1). Batches
run in the order §12 sets out; nothing here is marked complete on "it compiles".

---

## Batch 1 — Restore basic trust

Scope per §12: M01 collisions, M02 preview/play truth, M03 modal behaviour,
M04 native controls, M05 accessible input, V01 indicator fix, V02 race guard,
V03 save rollback, V16 keyboard scope, S01 resize.

Everything else in M01/M03/M04/M05/V01 (the recomposition, the visual dialog,
the card system, the copy rewrite, the hero hierarchy) belongs to batches 3
and 4 and is NOT done here.

### Live audit

`webui/tests/live/discover-batch1-audit.spec.ts` runs against a running
SoulSync and is the source of the after-shots in
`webui/test-results/discover-batch1/`.

    cd webui && SOULSYNC_PROFILE_PIN=<pin> npx playwright test --config=playwright.live.config.ts

12/12 passing on the live install, Sep 4 2026: hero collisions at 1440x900,
1024x768 and 390x844 on music and 1440x900 / 390x844 on video; the mix dialog's
role/aria/focus/scroll-lock/Escape/focus-restore; the row Play actually
reaching `/api/discover/resolve-playable`; the dial responding to arrow keys;
an arrow key on a focused poster leaving the video hero alone; and both halves
of the sidebar breakpoint behaviour.

Two honest caveats on that run:

* The install's hero had no recommendations for the audited profile, so it
  rendered its empty/loading state and the rotation indicators were absent.
  The music collision tests therefore did not exercise the dots live; the
  six-artist case is only covered by the offline probe.
* The dev server restarted itself twice mid-run (ECONNRESET / ECONNREFUSED).
  The spec now waits for it to come back and the config retries twice.
  `dev.py`'s watcher restarts the backend on any `.html` change outside
  `.git`, `logs`, `node_modules` and `static/dist`, which includes
  `webui/test-results` and `webui/playwright-report`.

### How the layout claims are checked

jsdom cannot lay anything out, and every collision in this batch is one
rectangle sitting on another. `webui/playwright.offline.config.ts` runs
browser probes that need no server: they render the real component markup
(regenerated from `src/test/layout-fixtures.test.tsx` before every run) and the
page's real `renderHero`/`paintHeroBody` under the real stylesheets, then
measure boxes and hit-test centres at 1440×900, 1024×768, 768×1024, 390×844
and 320×740.

    cd webui && npx playwright test --config=playwright.offline.config.ts

Every guard below was negative-checked: the fix was reverted and the probe
watched to fail.

---

### M01 — hero collisions [P0 part only]
- Status: complete (collisions only; the opening recomposition is batch 3)
- Current problem reproduced: reverting the CSS to the two absolutely
  positioned boxes fails `hero controls never intersect` at all five widths
  and `hittable at its centre` at four of them. Matches
  `discover-review-evidence/music-desktop.jpg` and `discover-mobile-fresh.jpg`.
- Behaviour implemented: `.discover-hero-indicators` and
  `.discover-hero-bottom-actions` are cells of one `.discover-hero-controls`
  flex row at the foot of the hero. The hero is a flex column, so the strip is
  a real row the content sits above rather than an overlay. An indicator is a
  36×36 target with an 8px `.hero-indicator-dot` span inside it, with a focus
  ring. On a phone the mobile hero is `min-height`, not `height`: a fixed
  318px clipped the hero's own View Discography / Add to Watchlist buttons off
  the bottom as soon as a title wrapped.
- Files changed: `webui/src/routes/discover/-ui/discover-hero.tsx`,
  `webui/static/style.css`.
- API/data contract changes: none.
- Validation: `tests/layout/discover-hero-geometry.spec.ts` (13 browser
  probes); `src/test/discover-shell-trust.test.ts` (declaration fences);
  existing `discover-hero.test.tsx` unchanged and green.
- Failure states checked: no artist image (placeholder renders, no collision);
  68-character title at 390 and 320 (all actions stay inside the hero).
- Accessibility/mobile checked: 320/390/768/1024/1440 widths; indicator target
  size; keyboard focus ring on indicators; reduced motion removes the dot
  transition.
- Remaining limitations: the command panel, the duplicated portrait, the
  intent navigation and the Watch All removal are batch 3. The hero is still
  550px on desktop.

### M02 — listening is the first, truthful action [P0]
- Status: complete
- Current problem reproduced: `onPreviewTrack={() => {}}` in `discover-page.tsx`
  and a `.mix-card-play` div whose only handler opened the modal. `playMixNow`
  called `window.playTrackList?.()` without awaiting and toasted
  "Playing all N tracks" regardless.
- Behaviour implemented: `-discover.playable.ts` has five distinguishable
  outcomes — `unsupported` (no player bridge: no network call, no success
  toast), `failed` (resolution failed, or the player rejected), `empty` (no
  playable metadata), `played` (only after the hand-off resolves). The
  hand-off is awaited. `playTrackNow` plays one row and says whether it is
  playing it or fetching it first. Mix cards gained a real Play button that
  loads a lazy tracklist first (`useMixModal.loadTracks`); the modal's row
  button is now a named "Play <track>". Both guard against a second
  activation while resolving and show a busy state.
- Files changed: `-discover.playable.ts`, `-discover.use-mix-modal.ts`,
  `-ui/mix-shelf.tsx`, `-ui/mix-modal.tsx`, `-ui/discover-page.tsx`.
- API/data contract changes: none. `PlayOutcome` gained `'unsupported'`.
- Validation: `-discover.playable.test.ts` (13 tests, incl. absent bridge,
  rejected hand-off, empty resolution, "waits for the player before saying
  anything"); `mix-shelf.test.tsx`; `mix-modal.test.tsx`.
- Failure states checked: missing bridge, rejected queue, empty resolution,
  partial resolution, double tap.
- Accessibility/mobile checked: play buttons are `<button>` with a per-item
  accessible name; the mix card's play is visible at rest rather than on
  hover, so a touch device can reach it.
- Remaining limitations: "Playing" is claimed once `playTrackList` resolves,
  which is when the first queue item reaches the audio element. A true
  playback-started lifecycle event does not exist in `media-player.js` yet.
  Audible playback is a manual check (§11.4) and has NOT been run.

### M03 — mix dialog behaviour [P0 part only]
- Status: complete (behaviour only; the visual redesign is batch 3)
- Current problem reproduced: runtime `escapeClosed: false` in the review
  evidence; the modal root was a plain div with no dialog semantics.
- Behaviour implemented: new shared `useAccessibleModal` hook in
  `webui/src/components/dialog` — initial focus, Tab/Shift+Tab containment
  that also recovers focus that escaped the dialog, Escape (capture phase, so
  a page-level handler underneath cannot answer first), body scroll lock with
  a nesting count, and focus restored to the exact opener. The mix modal
  carries `role="dialog"`, `aria-modal` and `aria-labelledby` pointing at its
  own title.
- Files changed: `webui/src/components/dialog/use-accessible-modal.ts`,
  `webui/src/components/dialog/index.ts`, `-ui/mix-modal.tsx`.
- API/data contract changes: none.
- Validation: five new tests in `mix-modal.test.tsx`. Negative-checked by
  removing the Escape branch.
- Failure states checked: no focusable content (focus falls back to the
  dialog); opener removed from the page before close.
- Accessibility/mobile checked: keyboard-only open/close, focus restore, tab
  loop. Screen reader itself NOT checked (§11.4 manual).
- Remaining limitations: the header hierarchy (Play primary, Sync demoted),
  selection mode, row heights, retry-inside-the-dialog and the mobile sheet
  are all batch 3. The dialog is not portalled, so background `inert` is not
  set; the scroll lock and focus trap carry it for now.

### M04 — native card controls [P0 part only]
- Status: complete (native activation only; the card family is batch 3)
- Current problem reproduced: `DiscoverMixCard` and `DiscoverAlbumCard` were
  `<div onClick>`, unreachable by keyboard, and the mix card's ▶ was a
  decorative div that opened the modal.
- Behaviour implemented: the mix card is a `.mix-card-open` button (its
  `::after` covers the whole card) plus a sibling `.mix-card-play` button on
  top — two real controls, neither nested in the other. The solid-cover markup
  keeps its nodes as direct flex children through a `display: contents`
  wrapper. The album card is itself a `<button>` with an accessible name that
  names album and artist.
- Files changed: `-ui/mix-shelf.tsx`, `-ui/album-shelves.tsx`,
  `webui/static/style.css`.
- API/data contract changes: none. `MixShelf` gained optional `onPlayMix` /
  `playingKey`.
- Validation: `mix-shelf.test.tsx` (open and play are separate real buttons,
  play does not open, a resolving card cannot be fired again, no handler means
  no button); `discover-shell-trust.test.ts` fences the clickable-div shape
  out.
- Failure states checked: no play handler (no dead button rendered); broken
  cover art (album card already keeps its box).
- Accessibility/mobile checked: both cards keyboard-operable; play is no
  longer hover-only.
- Remaining limitations: artist/track/station/BYLT cards are untouched; the
  shared card primitives, overlaid-text policy and thumbnail payload sizing
  are batch 3.

### M05 — accessible taste control [P0 part only]
- Status: complete (input and motion; the copy rewrite is batch 3)
- Current problem reproduced: `onMouseDown` + window mousemove only, no range
  semantics, and a rAF loop gated on `offsetParent` (which says the page is
  displayed, not that the dial is on screen).
- Behaviour implemented: a native `<input type="range">` stretched
  transparently over the wave, with `aria-labelledby`, `aria-valuetext`
  carrying the band name and percent, and `aria-describedby` pointing at a new
  line that says what the dial actually does. Keyboard, touch and pointer all
  work because the browser owns them. Live value on every input event, one
  save 320 ms after the gesture settles, so a held arrow key saves once with
  the last value. The frame loop is skipped entirely under reduced motion and
  suspended by an IntersectionObserver when the wave is off screen.
- Files changed: `-ui/adventurousness-dial.tsx`, `webui/static/style.css`,
  `-ui/artefact-parity.test.ts` (two new deliberate ids).
- API/data contract changes: none.
- Validation: `controls.test.tsx` — range semantics, live-then-settle, held
  key saves once with the last value, reduced motion means no frame loop,
  off-screen means no redraw and scrolling back in resumes.
- Failure states checked: repeated changes keep only the last commit.
- Accessibility/mobile checked: arrows/Home/End/touch via the native control;
  focus ring on the card via `:focus-within`; reduced motion.
- Remaining limitations: the visible name is still "Adventurousness" and the
  two pole labels still promise deep cuts — renaming it means moving the
  Settings copy too, which is batch 3. Failed-save rollback and the
  "which shelves this affects" note are not done.

### V01 — hero indicators [P0 part only]
- Status: complete (indicators only; hero hierarchy is batch 4)
- Current problem reproduced: restoring the old `.vdsc-dots`/`.vdsc-dot` rules
  fails five of the video probes, including `.vdsc-hero-cta over .vdsc-dot` at
  390 and 320. mobile.css's blanket `button { min-height: 38px }` was
  stretching each 9px dot.
- Behaviour implemented: `.vdsc-dots` is a reserved full-width strip across the
  foot of the hero, `pointer-events: none` except on its buttons. A dot is a
  40×40 button with the visible 9px dot as a `::before`, so no generic
  min-height can distort it. The hero body clears the strip at every width.
  Keyboard focus inside the hero pauses the rotation, in CSS and in JS.
- Files changed: `webui/static/video/video-side.css`,
  `webui/static/video/video-discover.js`.
- API/data contract changes: none.
- Validation: `tests/layout/video-hero-geometry.spec.ts` (12 probes, running
  the page's own `renderHero`/`paintHeroBody`), plus a settled-layout probe
  with the entry animation actually running.
- Failure states checked: five slides; long wrapping title; 320px width.
- Accessibility/mobile checked: 40px targets at every width; focus-visible
  ring already present; reduced motion already stops the fill animation.
- Remaining limitations: hero height, the capability-driven primary action,
  the "In your library" duplication, the wordmark rules and default rotation
  are batch 4.

### V02 — race-safe browsing [P0 part only]
- Status: complete (race guard and error-vs-empty; URL/restore is batch 4)
- Current problem reproduced: `loadGrid` closed over the old `state.cat` and
  inserted into the shared grid with no active-query check. Stubbing the guard
  to `return true` fails three of the new grid tests.
- Behaviour implemented: every query carries a generation (`state.catGen`).
  A response paints only when both the cat object and its generation still
  match, so a slow filter cannot append into a newer grid, a page 2 in flight
  cannot land in the next query, and closing the category discards what is
  still coming. A null/failed response is no longer rendered as "Nothing
  here": a new `[data-vdsc-grid-error]` block appears, the cursor does not
  advance past the failed page, and the button becomes Try again.
- Files changed: `webui/static/video/video-discover.js`, `webui/index.html`.
- API/data contract changes: none.
- Validation: `src/test/video-discover-trust.test.ts` runs the real `loadGrid`
  with its closure bound to stubs — deterministic out-of-order A/B, stale page
  2, close-during-flight, failure vs empty, failed page 2 keeps its rows.
- Failure states checked: non-OK response, thrown fetch, failed page 2.
- Accessibility/mobile checked: n/a for this item.
- Remaining limitations: no AbortController yet; query state is still not in
  the URL, Back from a category does not return to the shelf, and reload does
  not restore filters. Those are V02's batch-4 half. Cross-page dedupe of
  entity keys is not done.

### V03 — transactional Not interested [P0 part only]
- Status: complete (the hide action; preference-cache re-keying is batch 5)
- Current problem reproduced: `wireNotInterested` fired `postIgnore` without
  awaiting it and removed the card regardless. Forcing the acknowledged path
  fails the new rollback test.
- Behaviour implemented: clicking hides EVERY tile for that `(kind, tmdb_id)`
  — taking the Top-10 rank numeral with it — by adding a class, keeping the
  nodes in the DOM. The button disables so a second tap cannot double-post. On
  a rejected or failed save the tiles come straight back where they were,
  focus returns to the button, and it says "Couldn't hide <title>. Try again."
  On success an Undo bar appears for nine seconds; Undo un-hides and posts the
  removal, and only when the window closes are the tiles really dropped.
  `postIgnore` now resolves null for a non-OK response, a `success: false`
  body or a network failure, and the ignore modal's own add/remove report
  failure too.
- Files changed: `webui/static/video/video-discover.js`,
  `webui/static/video/video-side.css`.
- API/data contract changes: none.
- Validation: `src/test/video-discover-trust.test.ts` — every occurrence
  hidden, rank numeral included, rejected save restores and reports, Undo
  un-hides and reverses, cards only really go once the window closes, cannot
  fire twice.
- Failure states checked: non-OK save, `success: false`, double click.
- Accessibility/mobile checked: the Undo bar is `role="status"` with a real
  button; focus returns to the hide button on failure.
- Remaining limitations: the scope question (is the ignore list per profile or
  global?) is untouched, so points 1, 3 and 4 of V03 — "Hide this
  recommendation" vs "Don't recommend this title", and renaming Ignore List to
  Hidden titles — are not done. Cache re-keying on language/provider change
  (V03.7) is batch 5.

### V16 — keyboard scope [P0 part only]
- Status: complete
- Current problem reproduced: a `document`-level ArrowLeft/ArrowRight handler
  that ran whenever the Discover page was visible, exempting only INPUT,
  TEXTAREA and SELECT.
- Behaviour implemented: the handler moved onto the hero element, so arrows in
  a rail, a chip group or a slider keep their own meaning.
  `isContentEditable` is respected. `focusin`/`focusout` on the hero pause and
  resume the rotation.
- Files changed: `webui/static/video/video-discover.js`.
- Validation: `src/test/video-discover-trust.test.ts` asserts the document
  handler is gone and the hero one exists, plus the focus pause in both JS and
  CSS.
- Remaining limitations: the rail focus model, focus-ring clipping, and
  identity-preserving hydration (the rest of V16) are batch 4.

### S01 — sidebar breakpoint [P0 part only]
- Status: complete for the drawer state machine
- Current problem reproduced: `music-mobile.jpg` / `video-mobile.jpg` show the
  drawer over the page after a desktop→mobile resize, while a fresh mobile
  context puts it at x=-280. The drawer had no breakpoint handling at all: it
  kept whatever class it had and animated its 0.3s slide across the layout
  change.
- Behaviour implemented: a `matchMedia('(max-width: 768px)')` listener closes
  the drawer on every crossing and suppresses its transition for one frame, so
  the flip cannot paint a half-open panel over the content. The desktop
  collapse preference is untouched (it lives in `html[data-sidebar]`). Escape
  closes the drawer and returns focus to the opener; opening moves focus into
  it; the hamburger carries `aria-expanded` and `aria-controls`, and its label
  changes between Open and Close navigation.
- Files changed: `webui/static/init.js`, `webui/static/mobile.css`,
  `webui/index.html`.
- API/data contract changes: none.
- Validation: `src/test/discover-shell-trust.test.ts`.
- Failure states checked: n/a.
- Accessibility/mobile checked: labelled opener with state, Escape, focus in
  and back out.
- Remaining limitations: NOT verified in a real browser resize yet — see
  "Outstanding" below. Focus containment inside the open drawer, the utility
  orbs above modals, and the persistent-player height reservation (the rest of
  S01) are not done.

---

## Outstanding for batch 1

These are the parts of the batch that cannot be closed from here:

1. **Audible playback (§11.4).** M02's acceptance says "Play mix reaches
   audible playback or an honest supported-device handoff". The contract, its
   five outcomes, and the live click reaching `resolve-playable` are all
   covered; whether sound comes out is not.
2. **The hero rotation indicators, live.** See the caveat above: the audited
   profile had no hero recommendations, so the live run never rendered a dot.
3. **Screen reader pass (§11.4).**
4. **Does it look right.** The after-shots are in
   `webui/test-results/discover-batch1/`; the judgement is Boulder's.
