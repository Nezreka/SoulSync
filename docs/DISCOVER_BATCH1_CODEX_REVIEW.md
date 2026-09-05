# Codex review of Discover Batch 1

Reviewed 2026-09-04. Implementation commit: `e0c3e56cf`; checkout reviewed: `df07401ec` (including the layout-probe follow-up). Scope: committed Discover changes, `DISCOVER_BEST_IN_CLASS_REVIEW.md`, and `DISCOVER_BIC_PROGRESS.md`. This is an independent review, not an implementation patch.

## Verdict

**Request changes. Batch 1 is materially better, but is not ready for correctness sign-off.** Six actionable issues remain in the changed flows. Passing tests establish useful coverage, but do not establish the player contract, first-page retry, failed Undo, composed slider persistence, overlapping playback intent, or real ten-item mobile hero layout.

This batch was a corrective pass, not the visual redesign. It should not be judged as completing the best-in-class objective. The original batches 2–8 remain necessary. Conversely, deferring redesign does not justify marking incorrect Batch 1 behavior complete.

## Evidence and verification

- All **127 focused Vitest tests in six files passed**.
- All **25 offline browser geometry tests passed**, after the two fixture-generation tests. Widths covered by that suite: 1440, 1024, 768, 390, and 320 pixels.
- Independently opened the authenticated running application in a fresh headless Edge context, inspected desktop/mobile screenshots, and exercised the real rendered controls.
- Live checks confirmed mix-dialog focus enters the dialog, Escape closes it, and the closed sidebar ends at x=0 after a desktop-to-mobile resize.
- Live failure injection confirmed first-page Retry requests page 2.
- Live intercepted persistence confirmed ten ArrowRight presses, separated by 100 ms, produce four adventurousness POSTs rather than one settled write. POSTs were intercepted and did not change the saved preference.
- Live mobile geometry confirmed the real ten-artist indicator row extends beyond both viewport edges at 390 pixels.
- Playback contract and concurrency findings below are source-proven paths; actual audio start and the reported approximately 60-second delay were **not independently timed**. No acquisition or real playback was initiated during these probes.

Evidence:

- [Probe results](discover-batch1-review-evidence/live-probes.json)
- [Mobile indicator bounds](discover-batch1-review-evidence/mobile-indicators.json)
- [Music desktop](discover-batch1-review-evidence/music-desktop.jpg)
- [Music mobile](discover-batch1-review-evidence/music-mobile.jpg)
- [Video mobile](discover-batch1-review-evidence/video-mobile.jpg)

## R1 — P1: resolved player promise does not mean playback started

**Locations:** `webui/src/routes/discover/-discover.playable.ts:107`; `webui/static/media-player.js:2617`; `webui/static/media-player.js:3459`. Also inspect `startAudioPlayback` and its catch around line 848.

**Problem:** `resolveAndPlay` awaits `window.playTrackList`, then emits its success message and returns `played`. Its explanatory comment asserts the promise resolves once the first queue item reaches the audio element. The actual player does not uphold that contract.

`playTrackList` awaits `playQueueItem(0)`. `playQueueItem` catches queue/playback errors, displays “Skipping track”, optionally schedules a later item, and returns normally. For a one-item queue, this can finish with no playback and a fulfilled promise. `startAudioPlayback` also handles errors internally without rethrowing. There are additional early-return paths. Consequently Discover can emit “Playing all 1 tracks”, report `played`, and close a modal despite playback not starting. A promise-rejection test using a mock bridge cannot catch a real bridge that swallows failures.

**User consequence:** the central trust defect M02 was intended to fix remains. The interface can tell a user that music is playing while it is silent. Waiting longer before displaying that false success does not fix the contract.

**Required implementation:**

1. Define an explicit player result shared by Discover and the real bridge. Distinguish acknowledged playback, queued/preparing, failure, cancellation/supersession, and unusable metadata as appropriate to the existing player.
2. Return a structured acknowledgment or reject consistently through the actual failure paths. Preserve intentional auto-skip behavior, but do not describe a scheduled skip as confirmed playback.
3. Emit “Playing” only from an actual playback acknowledgment tied to the current request. Use truthful preparing/queued language before that point.
4. Close a dialog only on its own still-current accepted outcome; retain a useful error/retry state on failure.
5. Do not solve this solely by changing the bridge type declaration or mocking a different return value in Discover tests.

**Acceptance tests:** execute the real bridge contract with a one-track library-play failure; a rejected `audio.play`; an audio readiness timeout; empty usable metadata; a failed first track followed by a successful next track; and successful playback. Assert there is no `played` result or “Playing” success for unsuccessful playback. Assert queued/preparing and acknowledged playback are observably distinct.

## R2 — P1: concurrent play requests can replace newer intent with older intent

**Location:** `webui/src/routes/discover/-ui/discover-page.tsx:370` (and the modal Play action around line 527).

**Problem:** the change from blocking all mixes to blocking only `playingMixKey === key` enables concurrent operations without sequencing them. A single key cannot represent multiple in-flight operations. Each completion also unconditionally clears the same shared key.

**Deterministic sequence:**

1. Click mix A; delay its tracks or resolution.
2. Click mix B; return its result first.
3. B starts through `playMixNow`.
4. Return A's older result. A still calls `playMixNow`; the bridge clears/replaces the queue, overriding B.

While B is pending, A no longer matches the busy key, so A can also be submitted again. A's `finally` can remove B's busy indicator while B still runs. This is especially consequential with the reported long resolution delay.

The track-row handler has the adjacent opposite inconsistency: it returns while **any** track index is pending, although only the pending row is shown as busy. Other apparent Play controls can silently do nothing. Inspect this as part of the same playback-intent design. A pending modal Play result must also not close a subsequently opened different modal.

**Required implementation:** establish a shared playback intent coordinator for card, modal mix, and track actions. The latest deliberate Play action should own the right to replace the queue. Invalidate stale operations before they call the bridge; cancelling network work alone is insufficient. Give each request an identity, and let only its owning request clear its busy state. Define whether other controls cancel/replace the pending action or are explicitly disabled with an explanation; do not silently ignore an enabled control.

**Acceptance tests:** defer A and B independently; resolve B then A; assert only the current intent reaches the player. Resolve A's failure while B is pending and assert B remains busy. Double-click a pending item; reopen a different modal before completion; initiate a track action while a card action is pending. Confirm no obsolete completion changes the queue, closes the wrong dialog, or clears another request's state.

## R3 — P2: first-page Retry skips page 1

**Locations:** `webui/static/video/video-discover.js:781`, `:843`, and `:1141`.

**Live reproduced:** fail the initial Browse request. Click “Try again”. The actual next request is:

```text
?kind=movie&sort=popularity.desc&lang=any&page=2
```

**Cause:** category state begins with `nextPage: 2`; initial loading selects page 1 through `reset`; the shared button always calls `loadGrid(false)`. After initial failure, Retry therefore uses the preinitialized next-page cursor rather than retrying the failed page.

**User consequence:** a recovered browse session can silently omit the first and highest-ranked page. The error now looks recoverable but recovery returns different data.

**Required implementation:** track the failed request's page explicitly, or initialize/advance the cursor only after a successful accepted response. Retry must repeat the failed page under the same current filters and generation. Keep existing cards on next-page failure and retain stale-response guards.

**Acceptance tests:** initial page 1 fails, click the rendered Retry button, assert another page-1 request; then succeed and assert the next Load more requests page 2. Separately fail page 2 and assert retry remains page 2 without duplicating page 1. Change filters during an outstanding retry and assert the old response cannot populate the new category.

## R4 — P2: failed Undo leaves restored cards inconsistent with the saved exclusion

**Location:** `webui/static/video/video-discover.js:287`.

**Problem:** Undo immediately removes `vdsc-card-hiding` from every occurrence and re-enables the button, then posts the removal. If removal fails, the handler only shows “Couldn't un-hide”. The restored cards remain visible although the backend still excludes the title. Reload then makes the title disappear again.

The initial hide-save rollback is a good fix. Its inverse operation needs the same integrity. Current hide tests do not establish this: the test harness gives the `remove` action a successful result.

**Required implementation:** either restore cards after acknowledgment, with a pending Undo state, or optimistically restore and re-hide on failed acknowledgment. Preserve a retryable Undo opportunity after failure; do not consume the user's only recovery control. Keep all current occurrences synchronized and avoid double submissions while an Undo is pending.

**Acceptance tests:** add succeeds; Undo remove returns HTTP failure, unsuccessful JSON, and network rejection. Assert visible state matches persisted state, failure is announced, and the user can retry. Assert retry success restores all occurrences without duplication. Include repeated hide/Undo interactions and expired Undo.

## R5 — P2: slider debounce does not stop intermediate persistence/refetches

**Locations:** `webui/src/routes/discover/-ui/adventurousness-dial.tsx:78`; `webui/src/routes/discover/-discover.use-recommended.ts:150` and `:170`.

**Live reproduced:** ten ArrowRight presses approximately 100 ms apart caused four POSTs carrying 0.31, 0.34, 0.38, and 0.40. See the recorded probe results. These were intercepted requests, not saved preference changes.

**Cause:** the dial schedules a debounced `onCommit`, but calls `onChange` for every value change. The actual `useAdventurousness.change` still performs its existing 450 ms throttled `save`. Each save also refetches two recommendation queries. The component-level test can observe one `onCommit` while missing all the network writes performed by its parent callback.

**User consequence:** the newly documented “one save after the gesture settles” is not the composed application's behavior. Recommendation refreshes can still happen during an ongoing adjustment, and multiple saves are in flight.

**Required implementation:** separate immediate local value feedback from persistence. Have one clearly owned settled commit path; remove the old live persistence from the callback supplied to the dial, or explicitly redesign the controller contract. Ensure stale persistence/refresh responses cannot override the latest committed preference. Keep the already-deferred error rollback work clearly marked if it remains outside this patch.

**Acceptance tests:** render the dial with its real controller and intercepted fetch/query layer. Ten continuous keyboard changes must produce local value updates and exactly one final persisted value after settling. Repeat a continuous pointer drag, Home/End, unmount with a pending timer, and a new gesture while an older save is pending. Assert no intermediate shelf refetches during the gesture.

## R6 — P2: real ten-item music hero indicators overflow mobile width

**Location:** `webui/static/style.css:36951`.

**Live reproduced at 390 px:** ten 36 px buttons have bounds starting at `left: -3` and ending at `right: 393`. The first and last hit targets therefore extend outside the viewport; the surrounding hero clips the indicator presentation further. Centers remained hittable in this probe, so this is **partial clipping**, not a claim that every edge control is unusable.

The new dedicated control strip fixes the CTA overlap, but the indicator row itself remains a single non-wrapping flex row. A clean six-item fixture does not establish capacity for the actual ten-item data.

**Required implementation:** define a mobile pagination presentation that fits the actual supported item count. Options include a compact bounded indicator window plus previous/next controls, or another intentional responsive treatment. Preserve accessible names, active state, keyboard reachability, and adequate touch targets. Do not simply shrink the button hit targets until they fit.

**Acceptance tests:** use 0, 1, 6, 10, and the supported maximum artist counts at 320, 390, and tablet widths. Assert every interactive control's full hit rectangle is inside the visible container, not merely that its center can be clicked. Verify focus rings and first/last states are not clipped. Include long text and text zoom.

## Playback delay remains an explicit release gate

The user's quoted approximately 60-second silent period is not verified as fixed by this review. It also is not clearly recorded as that measured symptom in the progress document inspected, which instead says audible playback was not tested. Reconcile those accounts with the actual latest measurement.

Instrument timestamps for click, lazy tracklist completion, playable resolution, acquisition if necessary, library play response, audio readiness, and the actual `playing` event. Separate an already-local track from a missing track requiring acquisition. Record cold and warm cases, successful and failing cases, and the number of samples. Never present a toast or a fulfilled bridge promise as measured audio onset.

For already-local content, compare the actual result with the original review's provisional ≤2-second playback target. If a missing item requires a long preparation period, immediately explain the stage, expose useful progress where available, and support cancellation or replacement by another deliberate Play action. The pending control must remain honest throughout.

## Visual assessment of this batch

The video hero's compact dots and separated CTA are a clear improvement. Music's controls are also separated from its hero text and actions. The resize check supports the sidebar fix. These are worthwhile corrections to visible defects.

The current music page still gives substantial first-screen space to the hero and configuration panel before the mix shelves. The large repeated artist portrait and dense control panel remain dominant. The video page still needs the original information-hierarchy, browsing, and decision-support work. Those are expected later-batch concerns rather than evidence that this corrective patch introduced them.

Do not call the present result best in class. A defensible claim requires the later visual implementation, real content at multiple sizes, reliable playback, and task-based user validation. The original review remains the specification for that work.

## Required corrections to the two handoff documents

1. Change Batch 1 from **DONE** to **implemented; review fixes required** until R1–R6 are addressed and verified.
2. Reopen M02 for playback acknowledgment and sequencing; reopen the relevant V02/V03 correctness slices for first-page retry and failed Undo. Keep M01's responsive control fit open for real ten-item data.
3. Keep M05 partial and explicitly record that the composed persistence path still writes during the gesture. Its current settled-save claim is too strong.
4. The release-blocker table inspected has **seven complete rows and two partial rows**, not “eight of nine fully closed”. Distinguish task IDs from table rows when reporting totals.
5. Describe the exported `PlayOutcome` accurately: it has four literal values. Partial library matching is message/result detail, not a fifth literal outcome. More importantly, revise the claim that `played` currently proves audio began.
6. Reconcile the S01 live resize verification with the later statement that real-browser verification remains undone. Keep actual remaining screen-reader and accessibility limitations separate from completed checks.
7. Record the approximately 60-second symptom with measurement context, current investigation status, and eventual verification. A caveat relayed in chat should be in the implementation handoff.
8. Preserve the distinction between tests passed, live behavior exercised, source-inferred failure paths, and unmeasured outcomes. Add the composed integration tests described above rather than increasing confidence through more isolated mocks.

## Recommended next patch order

1. R1 and R2 together: truthful, ordered playback intent and acknowledgment.
2. R3 and R4: recoverable video request failures with consistent persistence.
3. R5: one settled preference commit through the real controller.
4. R6: mobile indicator capacity using real data counts.
5. Re-run the existing tests plus these new integration cases; repeat live screenshots and audible timing; then reconcile both status documents.

No application code was changed in this review. The independent screenshots, probe results, and this report are the review deliverables.
