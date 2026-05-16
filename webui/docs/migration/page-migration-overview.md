# WebUI Page Migration Overview

Snapshot date: 2026-05-14

## Summary
- The shell route manifest now has 18 page ids.
- `issues` and `stats` are now React-owned routes.
- Since the last snapshot, the biggest changes are:
  - `downloads` was renamed into `search`.
  - The live queue became `active-downloads`.
  - `watchlist` and `wishlist` became full sidebar pages.
  - `tools` was split off from `dashboard`.
  - `artists` is no longer a route id.
- Since the last migration review, `stats` has been fully moved to React, the legacy stats HTML/JS/CSS path has been removed, and the global `Chart.js` import has been dropped in favor of route-local `Recharts`.
- The shell is also more modular now. The old monolithic `script.js` has been split across `core.js`, `init.js`, `shared-helpers.js`, and feature modules such as `search.js`, `api-monitor.js`, `pages-extra.js`, `stats-automations.js`, and `wishlist-tools.js`.
- Current profile compatibility still normalizes old `downloads` and `artists` references to `search`, so the docs and the route ids are not always using the same historical language.

## What Changed Since The Last Snapshot
- `search` is now the canonical route for the old download/search experience.
- `active-downloads` owns the dedicated live queue that used to sit inside the search flow.
- `watchlist` and `wishlist` moved out of dashboard-era chrome and into their own routes.
- `tools` moved off the dashboard into a dedicated sidebar page.
- `dashboard` is a bit narrower now, because several operational surfaces were split out.
- `artist-detail` is still a first-class route, but its permission relationship is now tied to `library` and `search`, not to an `artists` page.
- The contextual help system still contains some historical `downloads` and `artists` wording, so those labels should be treated as legacy text rather than route ids.

## Current Architecture
- `webui/index.html` still hosts the Flask-rendered shell, the sidebar, the media player, the legacy `.page` containers, and the React mount point.
- `webui/static/core.js` now holds a lot of the shared global state that used to live in the old monolith.
- `webui/static/init.js` still owns page activation, permission gating, nav highlighting, legacy routing, and the `window.SoulSyncWebRouter` bridge.
- `webui/static/shell-bridge.js` and the TanStack Router adapter still decide whether a route is handled by the React host or handed back to the legacy shell.
- `issues` remains the reference pattern for interactive React-owned pages, while `stats` now complements it as the reference for data-heavy read-only routes with route-local charts and explicit shell handoffs.
- The legacy shell is now spread across feature modules rather than one giant coordinator file, which makes the migration seams a little clearer than they were a month ago.

### Route and Compatibility Notes
- Manifest page ids: `dashboard`, `sync`, `search`, `discover`, `playlist-explorer`, `watchlist`, `wishlist`, `automations`, `active-downloads`, `library`, `tools`, `artist-detail`, `stats`, `import`, `settings`, `issues`, `help`, `hydrabase`.
- `downloads` and `artists` are no longer manifest ids.
- HTML `.page` containers exist for every legacy page plus `webui-react-root` for React.
- `watchlist`, `wishlist`, and `active-downloads` are now standalone route targets instead of dashboard overlays.
- `tools` is now a dedicated page, so dashboard can be treated as a monitoring hub instead of the one-stop maintenance surface.
- `help` and `issues` remain always-allowed for non-admins.
- `settings` remains admin-only.
- `artist-detail` is allowed when the profile can access `library` or `search`.

## Cross-Cutting Features
- Profile and permission routing still live in the shell bootstrap.
- Shell chrome and nav highlighting are still shared shell responsibilities.
- Media player behavior, queue handling, and global overlays still cut across multiple pages.
- Socket/WebSocket and polling behavior remain the biggest migration risks for live pages.
- The help system, tours, and helper annotations still reference some historical route names, so route-migration work should use the manifest as the source of truth.
- Visual effects such as `particles.js` and `worker-orbs.js` remain shell-global.
- Route migrations should actively scan for emerging shared UI or shell patterns. Do not force abstractions on the first occurrence, but do document overlap and prefer extracting a shared primitive once a second route clearly needs the same behavior.

## Scoring Rubric
Each page is scored from 1 to 5 on five axes:

- Rendering surface size: HTML/UI area and number of distinct render states
- State/coupling complexity: amount of local state plus coupling to other pages or shell-global state
- Async/realtime complexity: fetch fan-out, polling, WebSocket/live progress, streaming, or long-running workflows
- Cross-cutting shell dependency: reliance on shared shell behaviors, globals, overlays, or non-route contracts
- Testability/parity difficulty: how hard it is to prove route parity without regressions

Rollups:

- Migration effort
  - `Low`: total score 9-11
  - `Medium`: total score 12-17
  - `High`: total score 18-21
  - `Very High`: total score 22-25
- Regression risk
  - `Low`: mostly isolated UI with limited async and minimal shell coupling
  - `Medium`: moderate data flow or workflow complexity with bounded blast radius
  - `High`: broad coupling, many async states, or sensitive user workflows

## Summary Matrix

| Page | Owner | Scores (R/S/A/C/T) | Effort | Risk | Recommended Wave |
| --- | --- | --- | --- | --- | --- |
| `issues` | React | 2 / 2 / 2 / 2 / 2 | Low | Low | Completed |
| `stats` | React | 2 / 2 / 2 / 2 / 2 | Low | Low | Completed |
| `help` | Legacy | 3 / 2 / 1 / 1 / 2 | Low | Low | Wave 1 |
| `hydrabase` | Legacy | 2 / 2 / 2 / 2 / 2 | Low | Low | Wave 1 |
| `import` | Legacy | 3 / 3 / 3 / 2 / 3 | Medium | Medium | Wave 1 |
| `search` | Legacy | 4 / 4 / 4 / 3 / 4 | High | High | Wave 2 |
| `watchlist` | Legacy | 4 / 4 / 4 / 3 / 4 | High | High | Wave 3 |
| `wishlist` | Legacy | 4 / 4 / 4 / 3 / 4 | High | High | Wave 3 |
| `active-downloads` | Legacy | 3 / 4 / 4 / 3 / 4 | High | High | Wave 4 |
| `tools` | Legacy | 4 / 4 / 4 / 4 / 4 | High | High | Wave 4 |
| `dashboard` | Legacy | 4 / 4 / 4 / 4 / 4 | High | High | Wave 5 |
| `discover` | Legacy | 5 / 5 / 4 / 4 / 5 | Very High | High | Wave 6 |
| `playlist-explorer` | Legacy | 4 / 4 / 4 / 3 / 4 | High | High | Wave 7 |
| `library` | Legacy | 4 / 5 / 4 / 4 / 5 | Very High | High | Wave 8 |
| `artist-detail` | Legacy | 5 / 5 / 4 / 5 / 5 | Very High | High | Wave 8 |
| `sync` | Legacy | 5 / 5 / 5 / 4 / 5 | Very High | High | Wave 9 |
| `settings` | Legacy | 5 / 5 / 4 / 5 / 5 | Very High | High | Wave 10 |
| `automations` | Legacy | 4 / 5 / 4 / 3 / 4 | High | High | Wave 10 |

## Page Catalog

### Wave 0: Baseline

#### `issues`
- Current owner: React.
- Primary files: `webui/src/routes/issues/*`, `webui/src/platform/shell/*`, `webui/src/app/router.tsx`.
- Main surface: counts cards, filtered issue list, issue-detail modal, mutation flows.
- Key coupling: shell page gating, shell nav badge refresh, bridge-controlled chrome, React Query cache.
- Why it stays first: it is already the canonical React route pattern and the migration baseline.

#### `stats`
- Current owner: React.
- Primary files: `webui/src/routes/stats/*`, `webui/src/platform/shell/*`.
- Main surface: listening stats, charts, ranked lists, disk usage, database storage visualization.
- Key coupling: query invalidation, shell handoffs for playback and artist navigation, route-local chart composition.
- Why it matters now: it is the first completed data-heavy read-only migration and the current reference for route-local charting, explicit shell bridge usage, and legacy cleanup after cutover.

### Wave 1: Safest wins

#### `help`
- Current owner: Legacy.
- Primary files: `webui/index.html`, `webui/static/docs.js`, `webui/static/helper.js`.
- Main surface: docs navigation, long-form sections, screenshots, lightbox behavior.
- Key coupling: mostly shell chrome and docs deep linking.
- Recommendation: still one of the safest early migrations, but keep the helper system shell-owned for now.

#### `hydrabase`
- Current owner: Legacy.
- Primary files: `webui/index.html`, `webui/static/init.js`.
- Main surface: connection state, saved credentials, peer count, comparison list.
- Key coupling: profile gating and a small amount of shell state.
- Recommendation: low-risk route with a narrow surface.

#### `import`
- Current owner: Legacy.
- Primary files: `webui/index.html`, `webui/static/stats-automations.js`, `webui/static/helper.js`.
- Main surface: staging files, album and singles matching, suggestion cards, processing queue.
- Key coupling: settings-derived staging path assumptions and downstream library state.
- Recommendation: still bounded enough for an early wave, though more workflow-heavy than `help` or `hydrabase`.

### Wave 2: Search split

#### `search`
- Current owner: Legacy.
- Primary files: `webui/index.html`, `webui/static/search.js`, `webui/static/downloads.js`, `webui/static/shared-helpers.js`.
- Main surface: basic search, enhanced search, source picker, fallback banner, download launch flow.
- Key coupling: global search widget parity, shared search controller, download modal handoff, legacy DOM ids that still say `downloads`.
- Recommendation: this is the renamed download/search surface, so it should be treated as a distinct migration from the queue view, not as the old monolith.

### Wave 3: Watchlist pair

#### `watchlist`
- Current owner: Legacy.
- Primary files: `webui/index.html`, `webui/static/api-monitor.js`, `webui/static/helper.js`.
- Main surface: watched-artist grid, per-artist config, scan status, global override banner, bulk actions.
- Key coupling: discovery and wishlist generation, scan polling, per-profile access rules.
- Recommendation: good mid-complexity route once the shell bridge and route-local data patterns are stable.

#### `wishlist`
- Current owner: Legacy.
- Primary files: `webui/index.html`, `webui/static/api-monitor.js`, `webui/static/wishlist-tools.js`, `webui/static/helper.js`.
- Main surface: track queue, cycle state, live processing, nebula visualization, countdown timers.
- Key coupling: watchlist scans, playlist sync handoff, download processing, live polling.
- Recommendation: visually distinctive but still bounded enough to follow `watchlist` in the same program wave.

### Wave 4: Operational split

#### `active-downloads`
- Current owner: Legacy.
- Primary files: `webui/index.html`, `webui/static/pages-extra.js`.
- Main surface: centralized live download list, status filters, batch grouping, batch history, cancellation controls.
- Key coupling: polling every few seconds, download batch hydration, nav badge counts, server-side download state.
- Recommendation: the old embedded queue moved here, so this page should be treated as the queue sibling of `search`.

#### `tools`
- Current owner: Legacy.
- Primary files: `webui/index.html`, `webui/static/wishlist-tools.js`, `webui/static/stats-automations.js`, `webui/static/helper.js`.
- Main surface: database updater, metadata updater, quality scan, duplicate clean, retag, backups, maintenance sections.
- Key coupling: lots of operational actions and several background jobs, but less dashboard chrome than before.
- Recommendation: split-off from the dashboard, but still operational enough to stay in a later wave.

### Wave 5: Monitoring hub

#### `dashboard`
- Current owner: Legacy.
- Primary files: `webui/index.html`, `webui/static/init.js`, `webui/static/wishlist-tools.js`, `webui/static/api-monitor.js`, `webui/static/worker-orbs.js`.
- Main surface: service cards, enrichment workers, library status, recent syncs, system stats, activity feed, quick nav.
- Key coupling: almost every global subsystem eventually shows up here.
- Recommendation: narrower than the old snapshot because tools moved out, but still one of the central shell surfaces.

### Wave 6: Broad discovery surface

#### `discover`
- Current owner: Legacy.
- Primary files: `webui/index.html`, `webui/static/discover.js`, `webui/static/helper.js`.
- Main surface: hero carousel, recent releases, genre browser, decade browser, similar artists, seasonal picks.
- Key coupling: watchlist-derived recommendations, discovery pool, download handoffs, many semi-independent sections.
- Recommendation: broad rendering surface and heavy fetch fan-out make this a high-risk migration.

### Wave 7: Visual interaction route

#### `playlist-explorer`
- Current owner: Legacy.
- Primary files: `webui/index.html`, `webui/static/wishlist-tools.js`, `webui/static/helper.js`.
- Main surface: playlist cards, discovery tree, selection model, zoom/pan interactions, wishlist submission.
- Key coupling: document-level pointer handling, discovery workflow, artist navigation.
- Recommendation: interactive enough to wait until the team is comfortable migrating richer stateful views.

### Wave 8: Library stack

#### `library`
- Current owner: Legacy.
- Primary files: `webui/index.html`, `webui/static/library.js`, `webui/static/helper.js`.
- Main surface: searchable artist grid, watchlist filters, pagination, download bubbles, deep links to detail.
- Key coupling: tightly bound to `artist-detail`, watchlist systems, and library-wide expectations.
- Recommendation: should be migrated alongside the detail route, not as an isolated quick win.

#### `artist-detail`
- Current owner: Legacy.
- Primary files: `webui/index.html`, `webui/static/library.js`, `webui/static/downloads.js`, `webui/static/helper.js`.
- Main surface: hero section, discography views, bulk operations, inline editing, tag writing, reorganize, quality actions.
- Key coupling: explicitly coupled to `library`, plus downloads, playback, metadata services, and file-organization settings.
- Recommendation: treat this as part of the library stack and keep it out of early waves.

### Wave 9: Multi-source orchestration

#### `sync`
- Current owner: Legacy.
- Primary files: `webui/index.html`, `webui/static/sync-spotify.js`, `webui/static/sync-services.js`, `webui/static/wishlist-tools.js`.
- Main surface: mirrored playlists, source tabs, discovery, sync history, playlist import flows, M3U export.
- Key coupling: the heaviest async orchestration in the app, with long-running workflows and state rehydration.
- Recommendation: one of the last major migrations.

### Wave 10: Final complex authoring/admin routes

#### `settings`
- Current owner: Legacy.
- Primary files: `webui/index.html`, `webui/static/settings.js`, `webui/static/helper.js`.
- Main surface: service credentials, download config, quality settings, file organization, appearance, advanced settings.
- Key coupling: almost every other page depends on settings-derived behavior or stored configuration.
- Recommendation: late migration because the blast radius is very large.

#### `automations`
- Current owner: Legacy.
- Primary files: `webui/index.html`, `webui/static/stats-automations.js`, `webui/static/helper.js`.
- Main surface: automation list, visual builder, run history, one-click hub groups, config editing.
- Key coupling: nested editable state, polling, run/deploy/toggle flows, and other system-level actions.
- Recommendation: save this for the final wave with the other complex authoring surfaces.

## Platform Unlocks
- `stats` now provides the baseline for route-local charting, explicit shell-bridge interop from React, and the pattern of cleaning out legacy assets once parity is confirmed.
- `search` likely unlocks reusable search-controller and download-launch primitives for the global search widget and other search entry points.
- `watchlist` likely unlocks artist-card, per-artist config, and scan-status primitives for `discover` and `wishlist`.
- `wishlist` likely unlocks queue/cycle visualization, live polling, and retry-state handling for `active-downloads` and sync-driven download flows.
- `active-downloads` likely unlocks batch grouping, queue filtering, and cancellation patterns for other download-related surfaces.
- `tools` likely unlocks maintenance-card and operational-action patterns that can be reused from `dashboard`.
- `library` + `artist-detail` still unlock entity-detail patterns, bulk actions, and file-management workflows.

## Why Earlier Waves Are Safer
- `stats` validated that bounded data pages can move early without needing broad shell rewrites, while also surfacing a healthy reminder to watch for emerging shared primitives during migration work.
- Wave 1 routes are either mostly static or bounded data UIs with limited cross-route side effects.
- Wave 2 adds the renamed search surface without dragging in the full queue history.
- Wave 3 introduces the new watchlist/wishlist split, which is important but still narrower than discovery or library management.
- Wave 4 adds the live queue and tools split once the route-local patterns are already in place.
- Wave 5 keeps the dashboard after its maintenance responsibilities have been peeled away.
- Waves 6-10 defer the broadest, most coupled, or most orchestration-heavy surfaces until the team has the most leverage.

## Final Recommendation
- Keep `issues` and `stats` as the current React reference implementations, and preserve the explicit bridge contract between React routes and legacy shell behavior.
- Treat `search`, `watchlist`, `wishlist`, `active-downloads`, and `tools` as the current route ids, and keep `downloads` and `artists` only as compatibility history.
- Migrate the remaining safe legacy routes first: `help`, `hydrabase`, and `import`.
- During each migration, actively look for small reuse opportunities across route slices and shared UI primitives, but only extract once the overlap is clearly real.
- Use `search` as the next meaningful proving ground now that the download queue has been split out.
- Avoid pulling `settings`, `sync`, `library`, `artist-detail`, or `automations` forward unless there is a separate product priority strong enough to justify the added regression risk.
