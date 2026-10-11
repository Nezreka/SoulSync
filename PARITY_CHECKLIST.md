# Unified Downloads Page — Feature Parity Checklist

ONE page at `/active-downloads` with a Music | Video switcher (`?media=music|video`).
Backend endpoints untouched. Video UI rewritten in React reusing the shared
`.adl-*` markup vocabulary + existing CSS unchanged. Old `/video-downloads`
vanilla subpage + its 3 JS files deleted; the video shell nav redirects to the
unified page's video view.

Work through every item as it is built; each must end SURVIVED.

## A. Page frame (new)

- [x] A1. Music | Video segmented switcher above the page, deep-linkable via `?media=`. (tests: 5 switcher tests pass; screenshot (a)/(b))
- [x] A2. `?media=video` (from `/video-downloads` redirect) lands on the video view. (test; redirect wired in video-side.js — live click not possible as subagent)
- [x] A3. Module-level view store: filter / expanded drawers / collapsed season-pack groups / review sub-tab survive Music|Video switches and unmount/remount, restoring the old page's `_filter`/`_expanded`/`_gcollapse`/`_sub` parity. (regression tests in -vdl.use-downloads.test.tsx)
- [x] A4. The music view renders the SAME `ActiveDownloadsPage` component, untouched. route.tsx only adds validateSearch + wrapper. Screenshot (a) confirms identical layout.

## B. Video: Downloads view (was video-downloads-page.js)

### B1. Header / hero
- [x] B1a. stat hero implemented; screenshot (b) shows 5/2/2/9. Stat hero: active / done / failed / total counts.
- [x] B1b. combined speed under active stat, hidden when 0. Combined live speed under the active stat (hidden when 0).
- [x] B1c. sub-line implemented; screenshot (b) shows '5 active · 2 done · 2 failed'. Sub-line "N active · N done · N failed".
- [x] B1d. adl-stat-live / adl-stat-bad classes applied by count. `adl-stat-live` on active>0, `adl-stat-bad` on failed>0.
- [x] B1e. history button + live badge; screenshot shows History 128. History button + live history count badge (`data-vdh-count`).
- [x] B1f. visible only when ≥2 retryable; wired to vdlRetryFailedAll. Retry Failed button, visible only when ≥2 retryable failures.
- [x] B1g. visible only when active>0; toast with count; confirm not required (matches old). Cancel All button, visible only when active work; toast with count.
- [x] B1h. visible only when completed+failed>0; toast 'Cleared N finished downloads'. Clear Finished button, visible only when completed+failed>0; toast.
- [x] B1i. hook polls on-page; VideoDownloadsBadgePoller in __root keeps it live off-page. Video sidebar badge `[data-video-downloads-badge]` stays live
      (on-page poll + background poll when on video side, tab visible).
- [x] B1j. window._vdpgAnyActive re-exposed by the hook. `window._vdpgAnyActive()` still answers (video-wishlist.js badge poll).

### B2. Filter pills
- [x] B2a. All / Active / Completed / Failed; vdlMatchesFilter unit-tested. Pills: All / Active / Completed / Failed (same labels, same matching).
- [x] B2b. active/fail sets ported verbatim; tests cover. Active = downloading/queued/searching/importing; failed = failed/cancelled/import_failed.

### B3. Season-pack grouping
- [x] B3a. vdlGroupKey + lone-pack rule ported; screenshot shows Bear S3 group. Same show+season rows (≥2, or a lone pack) collapse into one parent card.
- [x] B3b. pack not counted as episode; tally shows '1/2 imported'. Pack is the batch head: not counted as an episode in the tally.
- [x] B3c. tally text rules ported verbatim. Tally "N/M imported · F failed"; pack-only "season pack downloading…" / "season pack".
- [x] B3d. group progress: pack % while active, else episode average. Group progress bar: pack's own % while pack active, else episode average.
- [x] B3e. group status text rules ported verbatim. Group status text: downloading/importing / "N active" / "✓ done" / "N failed".
- [x] B3f. group poster art; caret collapse persists in groupOpen set. Group poster art; caret collapse/expand persists.
- [x] B3g. group cancel includes the pack id in batch cancel. Group cancel button cancels the whole batch incl. the pack id.
- [x] B3h. server order preserved; group sits at first member's index. Server order preserved; group sits where its first member appears.

### B4. Cards (collapsed)
- [x] B4a. poster tile + emoji/kind fallback + type badge corner; screenshot (b). Poster art tile, kind emoji fallback, type badge corner (🎬/📺/▶️).
- [x] B4b. title (+ SxxExx for episodes), quality chip; screenshot. Title (+ SxxExx disambiguation for episodes), quality chip.
- [x] B4c. ⇪ upgrade-watch chip when completed && upgrade_watch (fixture row 601 covers). ⇪ upgrade-watch chip when completed && upgrade_watch.
- [x] B4d. YouTube channel name on collapsed row. YouTube channel name on the collapsed row.
- [x] B4e. meta context line variants ported; screenshot shows all four. Meta context line: release / size·speed·eta·user·pct / import-phase text /
      dest path for completed / "Trying another release…" for searching.
- [x] B4f. error line on failed rows; screenshot shows Oppenheimer's. Error line on failed rows.
- [x] B4g. determinate %; indeterminate shimmer for queued/searching/merging; screenshot shows shimmer. Progress bar: determinate %; indeterminate shimmer for queued/searching/merging.
- [x] B4h. merging (no %) vs moving (real % + elapsed timer). Import sub-phases: merging (no %) vs moving (real % + elapsed timer).
- [x] B4i. status dot + label + '· Nx' attempts; screenshot. Status dot + label (+ "· Nx" attempts).
- [x] B4j. Cancel/Retry/Block/Manual Import/Open gating ported verbatim. Row buttons: Cancel (active), Retry (failed), Block release (failed/import_failed
      with username+filename, non-YouTube), Manual Import (import_failed + admin),
      Open (media detail / channel / YouTube link).
- [x] B4k. Manual Import → video Import page; admin gate via useProfile. Manual Import goes to the video Import page (admin gate kept).
- [x] B4l. failed: block + next:true; import_failed: block only. Block on failed = block + auto-retry a DIFFERENT release (next:true);
      on import_failed = block only (file may still be manually importable).
- [x] B4m. toast copy matches old strings verbatim. Toasts match old copy ("Release blocked — searching for another release", etc).

### B5. Cards (expanded drawer) — Broque's hard requirement: cast info + images
- [x] B5a. backdrop/title/year/rating/runtime/studio/status/tagline; screenshot (c). Backdrop image; TMDB logo or title; year · ⭐ rating · runtime · network/studio · status; tagline.
- [x] B5b. episode block (still, SxxE, air date, title, synopsis) — code-ported, not screenshotted. Episode-specific block: still + SxxE · air date + episode title + its synopsis.
- [x] B5c. overview / 'Loading…' / 'No synopsis available.'. Overview/synopsis, or "Loading…" first paint, or "No synopsis available."
- [x] B5d. genres row; screenshot (c). Genres row.
- [x] B5e. trailer button + Watch-on providers; screenshot (c). Trailer button; "Watch on" provider logos.
- [x] B5f. CAST up to 8 with photo/initial fallback + name + character; screenshot (c) shows 5. CAST: up to 8, photo (or initial fallback), name, character.
- [x] B5g. YouTube variant: thumbnail, channel·duration·views·upload date, description. YouTube variant: big thumbnail, channel · duration · views · upload date, description.
- [x] B5h. download facts grid ported field-by-field; screenshot (c). Download facts: Status / Director|Creator / Quality target / Release /
      Format (resolution·source·codec) / Source (👤 user) / Availability snapshot /
      Size / Attempts / Upgrade note / Path + copy button / Error.
- [x] B5i. drawer actions mirror collapsed gating. Action buttons: Open in library / Open channel / Open on YouTube /
      Cancel / Manual Import / Retry (same gating as B4j).
- [x] B5j. lazy fetch once per card; library skips; code-ported verbatim URL scheme. Meta lazily fetched once per card: `/meta/<movie|show>/<tmdb_id>?season&episode`
      or `/yt-meta/<video_id>`; `media_source==='library'` skips (owned re-grab).
- [x] B5k. row click toggles; controls/links stopPropagation; drawer survives re-patches via metaCache. Click row toggles drawer; clicks on controls/links don't; drawer content
      survives re-patches while open.
- [x] B5l. openDetail/openChannel/openYouTube ported via window.SoulSyncVideo guards. Open buttons use `SoulSyncVideo.openDetail` (works from the music shell too).

### B6. List behavior
- [x] B6a. skeleton rows on first load; no empty-flash. Skeleton shimmer rows on first load (no "no downloads" flash).
- [x] B6b. three empty states ported. Empty states: no downloads / nothing here / nothing failed (🎉).
- [x] B6c. 2s active / 6s idle poll; hidden-tab pause; unmount cleanup. Poll 2s when active, 6s when idle; paused when tab hidden; stops on unmount.
- [x] B6d. reads never throw — failed fetch keeps the last list. Poll never blanks the list on a failed fetch.

## C. Video: Review view (was video-downloads-tabs.js — review half)

- [x] C1. Sub-tabs: 🗑 Recycle bin (N) / 🚫 Blocklist (N). (vdl-review.tsx; badge on Review tab visible in screenshot (b))
- [x] C2. Recycle explainer ported verbatim incl. recycling-OFF note and keep_days copy.
- [x] C3. Recycle rows: name (timestamp prefix stripped), original path (or unknown note), reason, size, age; Restore / Delete buttons.
- [x] C4. Restore all / Empty bin (confirm) when non-empty.
- [x] C5. Delete asks for confirmation (permanent).
- [x] C6. Review badge `[data-vdpg-review-n]` = bin count; refreshed on page open (cheap counts call, no poll).
- [x] C7. Blocklist explainer; rows: release_title/filename/username, title·username, reason, "whole uploader" chip; Unblock button.
- [x] C8. Blocklist Clear all (confirm).
- [x] C9. Toasts match old copy ("Restored to …", "Deleted", …).

## D. Video: Clients view (was video-downloads-tabs.js — clients half)

- [x] D1. Reuses the shared `AdlClientsTab` (same `/api/clients/*` blueprint the music side uses) — superset of the old torrent/usenet/slskd panes.
- [x] D2. Preserved via the shared component (same component the music side uses, unchanged).
- [x] D3. 10s poll while the pane is open; stops on unmount (shared component behavior, unchanged).

## E. Video: History modal (was video-download-history.js)

- [x] E1. Opens from the History button; overlay+Escape close; body scroll lock. (vdl-history.tsx)
- [x] E2. Tabs All / Movies / TV / YouTube with counts; subtitle "N grabs · M movies · …".
- [x] E3. Search (debounced 250ms); "Load more" pagination (40/page).
- [x] E4. Day-grouped timeline rows ported: poster/emoji, title+SxxE, sub line, outcome chips, when, chevron; expand in place.
- [x] E5. Expanded row: Release/Source/Codec/Size/Grabbed/Finished/Path/Error + Re-download (row removed, toast), Block release (gated as old), Block uploader.
- [x] E6. Clear (confirm; toast "Cleared N from history").
- [x] E7. History badge refreshed on page open + after clear.

## F. Per-release evaluate/grab flow (from the downloads page)

- [x] F1. Failed row → block + retry-next grabs a DIFFERENT release (block + next:true on failed; vdlRetryFailedAll ported). Never the blocked one.
- [x] F2. Toast copy ported verbatim: "Release blocked — retrying with another release" vs "Release blocked — searching for another release".
- [x] F3. video-download-view.js and the get-modal untouched — never part of the downloads page. (git status: no changes)

## G. Old route + shell nav

- [x] G1. `/video-downloads` deep link / reload redirects to `/active-downloads?media=video` (replace, no back-button trap). Wired in video-side.js; live redirect not clickable as subagent.
- [x] G2. Sidebar nav 'video-downloads' routes through navigate() → gotoUnifiedDownloads (in-shell). Verified by code path.
- [x] G3. Both dispatch `soulsync:video-navigate` → navigate('video-downloads') → same intercept. Verified by code path.
- [x] G4. Old subpage section + 3 script includes removed from index.html; 3 JS files deleted via git rm; grep confirms no live references (`_vdpgAnyActive` re-exposed by the hook for video-wishlist.js).
- [x] G5. gotoUnifiedDownloads flips via window._switchAppSide('music') then routes in-app; location.assign fallback. Live shell flip not clickable as subagent.
- [x] G6. No visual redesign. git status: style.css and video-side.css UNCHANGED. Screenshots show the same look as the old pages.

## H. Quality gates

- [x] H1. `oxfmt --check src` clean (oxfmt 0.47.0, matches package.json pin).
- [x] H2. `oxlint --type-check src`: 0 errors (535 warnings, all pre-existing in untouched files; the 2 new test files are warning-free). One collateral Link fixed with explicit search param.
- [x] H3. Full webui suite green: 544 files / 10204 tests pass (incl. 7 new vdl regression tests).
- [x] H4. New: -vdl.api.test.ts (msw, 20), -vdl.helpers.test.ts (35 `it` blocks), unified-downloads-page.test.tsx (5 tests), vdl-fixtures.test.tsx (3 screenshot fixtures). 63 new tests, all pass.
- [x] H5. REAL screenshots under ~/workspace/unified-downloads-shots/: (a) unified-downloads-music.png, (b) unified-downloads-video.png, (c) unified-downloads-video-expanded.png (cast + images visible). Rendered from the real components under the real stylesheets in real Chromium (TMDB art replaced with labeled SVG placeholders).
- [x] H6. Committed locally as 81b9b42fe on fix/unified-downloads-page (review findings round: back-button, first-paint, history race, state store, badge poller, toasts, lints, 7 new regression tests). git diff --stat + full git diff reviewed: every hunk on-topic. NOT pushed. Never main. Never merged.
