# download live status plan (#1156, wishx)

> **STATUS: P1–P4 shipped `366ac953e` (Aug 19 2026) and LIVE-VERIFIED the
> same day** — boulder smoke-tested the pop-in, the expandable cards and an
> album-release batch: "it all works". P5 (the artist/album/studio dossier)
> still deferred. One arc was NOT in this plan and got built anyway — see
> "album releases" at the bottom.


the ask: stop making Pending → Searching → Downloading → Processing a mystery.
show where we're searching, what we found, who we're pulling from — live — on
both the downloads page cards and the download-missing-tracks modal.

the finding that shapes everything: **the engine already knows all of it.**
the task dict carries the query ladder position, the winning peer/file, the
per-source candidate counts, the raw slskd state, the retry clocks — and the
status builders drop every one of them on the floor. this feature is mostly
serialization + two UI affordances, not new machinery.

## what exists today (mapped, file:line)

- task dict fields never exposed: `current_query_index`/`query_count`/
  `searched_queries` (core/downloads/task_worker.py:366-372, 472, 522),
  winning `username`/`filename`/`download_id` (core/downloads/candidates.py:579-581),
  `current_candidate_index` (candidates.py:288), `exhausted_download_sources`
  (monitor.py:212), stall clocks (`queued_start_time` monitor.py:814,
  `downloading_start_time` monitor.py:904).
- best-quality mode already builds a per-source contributions list
  ("soulseek=12, youtube=0, tidal=excluded") at
  core/download_engine/engine.py:453-499 — the exact narration wishx wants —
  and logs it instead of shipping it.
- slskd's live search has a `progress_callback` hook that fires on every poll
  tick with candidate/peer counts (core/soulseek_client.py:649-651) — plumbed
  through the whole plugin chain and **dead on the download path** because
  task_worker never passes one (task_worker.py:564-566).
- the raw slskd transfer state ("Queued, Remotely" vs "InProgress") is
  collapsed to the single word 'queued' at core/downloads/status.py:494 — the
  UI literally cannot distinguish "about to start" from "behind 40 people".
  the video side already classifies this string (core/video/slskd_download.py:77).
- a LIVE completed row never shows its source: "YouTube"/"Tidal" comes from
  library_history.download_source and only appears once the row ages into the
  history branch (status.py:708-709 vs :813). the username→source map that
  produces it exists at core/imports/side_effects.py:186-212.
- both transports already tick every 2s: `downloads:batch_update` socket room
  (web_server.py:42062-42084) + HTTP fallback poll (downloads.js:2107), both
  landing in one updater, processModalStatusUpdate (downloads.js:2886). the
  downloads page polls /api/downloads/all separately (2s, -adl.use-downloads.ts).
- the modal table has FIVE parallel builders (shared-helpers.js:1763,
  sync-spotify.js:2193, sync-services.js:1325, downloads.js:429, :1378) but
  identical cell ids — the updater is shared, so popover work touches only
  downloads.js, never the builders. beware the second thin updater
  updateCompletedModalResults (sync-services.js:346) — any status-label change
  must land in both or they diverge.
- anchored popover template ready to steal: source-info-popover.tsx:44 (react)
  and the error-tooltip pattern (downloads.js:2330-2373) (vanilla).
- status-cell clicks already open detail for completed/quarantined rows
  (downloads.js:3129-3143 → track-detail.js:55) and a candidates modal for
  failed (downloads.js:2395). the in-flight states are the only dead clicks —
  exactly the gap in the report.

## p1 — ride what's known onto the payloads (backend only)

new per-task `live_detail` dict, mirrored into the task the same way the
album-bundle narration mirrors plugin state into the batch
(core/downloads/album_bundle_dispatch.py:158-167 — the structural template):

    live_detail: {
      source,            # provider currently being tried / bucket that won
      username,          # winning peer (soulseek) or service name
      filename,          # basename of the file being pulled
      query,             # the query string currently searching
      query_index, query_count,
      candidate_index, candidate_count,
      slskd_state,       # RAW state string: "Queued, Remotely", "InProgress"
      speed, size,       # from the live transfer row (monitor.py:594-603)
    }

- set `task['current_source']` at the orchestrator seams (engine.py:418
  hybrid, :453-499 best-quality contributions, download_orchestrator.py:334
  single-source); winner fields already exist (candidates.py:579-581).
- serialize in BOTH builders: build_batch_status_data (status.py:342-362,
  feeds the modal via socket AND poll for free) and
  build_unified_downloads_response (status.py:793-824, feeds the page).
  only attach for non-terminal statuses to keep the 300-row payload lean.
- fix the live-completed source gap while here: run task['username'] through
  the side_effects source map so a live completed row shows "YouTube"/"Tidal"
  immediately instead of after history persistence.
- stop collapsing the slskd state: ship the raw string alongside 'queued' so
  "queued remotely" is renderable (music side catches up to video side).
- tests: pure builder tests (task dict in → payload out), no network.

## p2 — downloads page: expandable cards (react)

- rows become clickable, expanding inline — same interaction as the
  unverified-review rows already have (adl-review.tsx:140-165), so the page
  stays one visual language. no new popover on this surface.
- expanded while in flight: source being searched, query ladder position
  ("query 2/4"), candidates found, peer + file + speed once downloading, raw
  queue state, retry info (already in the payload today, barely rendered).
- expanded when terminal: reuse GET /api/downloads/task/<id>/detail
  (track_detail.py:57-106 — source, quality, acoustid, file path,
  expected-vs-downloaded) fetched on expand. failed rows link the existing
  candidates modal.
- rebuild the bundle (npm run build) — react change.

## p3 — the "Searching…" live pop-in (modal, vanilla)

- extend the status-cell click decision tree (downloads.js:3129-3143) so the
  in-flight states open a small anchored pop-in (error-tooltip positioning
  pattern), not a full modal — wishx's own suggestion.
- it renders from the SAME batch frames processModalStatusUpdate already
  receives every 2s; the updater refreshes any open pop-in and closes it when
  the task leaves in-flight states. no new transport, no new endpoint.
- wire the dead slskd progress_callback (task_worker.py:564-566 →
  soulseek_client.py:649-651) into `task['live_detail']` so the pop-in can
  tick "12 candidates from 5 peers" DURING the search, between frames.

## p4 — search narration depth (optional polish)

- mirror the best-quality contributions list per source into live_detail so
  the pop-in can say "soulseek 12 · youtube 3 · tidal skipped (not configured)".
- surface `exhausted_download_sources` + quarantine budget in the pop-in for
  long-suffering wishlist tracks.

## p5 — deferred, decide later

- the "every bit of info about artist/album/track, studio" card dossier: real
  metadata surface, new lookups, its own design pass. the expanded card links
  to the artist page instead for now. not in this arc unless you want it.

## risks / traps

- five modal builders, one updater: popover work stays in downloads.js. any
  status-LABEL change must also hit updateCompletedModalResults
  (sync-services.js:393-403).
- builders read under tasks_lock — live_detail must be assembled inside the
  existing lock pass, no second acquisition (plain non-reentrant lock).
- payload growth: gate live_detail on non-terminal status; the emit loop
  already skips with no connected clients.
- the socket handler handleDownloadBatchUpdate (core.js:1113) is the one with
  no ss: re-broadcast seam. p2/p3 don't need it (page polls its own endpoint,
  modal is vanilla) — add the seam only if react ever consumes batch frames.
- progress_callback fires on slskd poll ticks inside the search thread — the
  callback must only write dict keys (no locks, no io), same rule as the
  deezer narration closure (deezer_download_client.py:536-553).

## album releases (not in the original plan — found by boulder)

"album level downloads never show up in the track list as 'searching...' —
happens in background and then suddenly we start getting a download
percentage for the album pack. then straight to processing."

correct, and for a structural reason: when the bundle gate engages
(torrent/usenet/soulseek release grabs) there ARE no per-track tasks yet.
the batch goes to phase `album_downloading`, the release downloads as one
unit, and per-track rows only exist afterward — by which point the files are
already staged, so they skip to processing. no per-track narration could ever
have applied.

two real gaps under that:

- the plugins have emitted `query` on their `searching` payload since day
  one, but `_MIRRORED_KEYS` in album_bundle_dispatch never mirrored it — so
  the entire prowlarr search rendered as dead air. now mirrored.
- the pick-a-release step (candidate filtering, seeder gate, profile veto)
  emitted NOTHING between 'searching' and 'queued'. both plugins now emit a
  `selecting` state carrying the release count.

UI: the progress line now walks searching → choosing a release (with query +
n releases) → queued → downloading → staging → matching tracks, and every
"Waiting for release" row plus the progress line opens the same pop-in,
rendered from the batch's album_bundle frame instead of a task frame
(stage, query, releases found, chosen release, seeders/grabs, speed, size).

## pop-in depth, second pass

first cut showed everything that was serialized; a second look found more
sitting unused on the task dict. added: the picked candidate's PEER stats
(free slots, queue length, avg upload speed), the monitor's own
`queued_start_time` clock as "45s in the remote queue", `used_sources`
count as "tried N peer/file pairs so far", and `exhausted_download_sources`.
together those turn "why is this stuck" into a self-answering question.

deliberately still excluded: full searched_queries history (the x/y counter
covers it), prior attempts' error text (retry chip + terminal detail cover
it), and anything needing I/O at build time — the pop-in stays a pure read
of what the engine already holds in memory, which is what makes it free.
