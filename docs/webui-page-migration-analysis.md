# WebUI Page Migration Analysis

Snapshot date: 2026-04-06

## Summary
- This document inventories the current hybrid WebUI shell and recommends an incremental migration order from legacy vanilla-JS rendering to the Vite + React + TanStack Router app.
- The canonical shell route list currently contains 15 page IDs in `webui/src/platform/shell/route-manifest.ts`.
- Only `issues` is React-owned today. Every other route is still rendered by the legacy shell, primarily through `webui/static/script.js`, `webui/index.html`, and the global stylesheet.
- The recommended order optimizes for low-risk wins first while still calling out a few high-value platform unlocks for later waves.

## Current Architecture
- `webui/index.html` is still the Flask-served shell. It owns the sidebar, media player, global overlays, and legacy `.page` containers for every non-React page.
- `#webui-react-root` is the single React mount point. It is treated like a shell page and becomes active when the current route belongs to React.
- `webui/static/script.js` is still the main rendering coordinator:
  - route activation and page switching
  - shell bridge exposure via `window.SoulSyncWebShellBridge`
  - `loadPageData()` dispatch for nearly every legacy route
  - page-local state, global shell state, polling, and many modal/workflow implementations
- TanStack Router delegates legacy pages back to the shell through `webui/src/routes/$.tsx` and `LegacyRouteController`, while React routes use `useReactPageShell()` to set shell chrome and show the React host.
- `issues` is the reference migration pattern:
  - canonical route ownership lives in `route-manifest.ts`
  - route UI lives under `webui/src/routes/issues/`
  - React owns route rendering, data loading, and detail modal behavior
  - the shell still owns route gating, nav chrome, and the page host

### Inventory Notes
- Manifest page IDs: `dashboard`, `sync`, `downloads`, `discover`, `playlist-explorer`, `artists`, `automations`, `library`, `artist-detail`, `stats`, `import`, `settings`, `issues`, `help`, `hydrabase`
- HTML `.page` containers exist for every shell page except `issues`.
- `issues` is the only route that resolves through the React host instead of a legacy `.page` container.
- `artist-detail` is a first-class route in the manifest and HTML, but it behaves like an extension of `library`, not a truly independent feature area.

## Cross-Cutting Features
These features are not owned by one page, but they affect migration scope, shell contracts, and ordering.

- Profile and permission routing
  - Owned in `script.js` profile initialization and `isPageAllowed()` logic.
  - Controls route gating, home-page redirects, admin-only settings, and the special `artist-detail -> library` permission relationship.
- Sidebar and shell chrome
  - Nav highlighting, page activation, global search visibility, discover sidebar visibility, and route-path synchronization all stay shell-owned today.
  - Any route migration must preserve these shell behaviors through the bridge rather than re-implementing them per route.
- Media player and queue
  - The sidebar player, expanded player, queue, streaming preview, and media-session integration live outside page boundaries.
  - Several pages launch playback or update queue state, so migration work has to preserve these entry points.
- WebSocket and polling infrastructure
  - Socket.IO initialization is global.
  - Many pages depend on active-process polling, sync/discovery polling, worker status polling, wishlist/watchlist polling, or progress refresh loops.
  - Pages with heavy polling or live progress are materially riskier to migrate.
- Helper and docs systems
  - `webui/static/docs.js` owns the Help page content.
  - `webui/static/helper.js` owns contextual help, tours, setup flows, and page-specific selector metadata across the app.
  - The Help page itself is easy to migrate, but the helper system is cross-cutting and should remain shell-owned until route migrations are further along.
- Visual shell effects
  - `particles.js` and `worker-orbs.js` are shell/global effects.
  - `worker-orbs.js` is dashboard-specific but mounted globally.
  - These are low priority to migrate and should be treated as shell integrations unless a page migration explicitly needs to absorb them.

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
| `issues` | React | 2 / 2 / 2 / 2 / 2 | Low | Low | Baseline only |
| `help` | Legacy | 3 / 2 / 1 / 1 / 2 | Low | Low | Wave 1 |
| `hydrabase` | Legacy | 2 / 2 / 2 / 2 / 2 | Low | Low | Wave 1 |
| `stats` | Legacy | 2 / 2 / 2 / 2 / 2 | Low | Low | Wave 1 |
| `import` | Legacy | 3 / 3 / 3 / 2 / 3 | Medium | Medium | Wave 1 |
| `artists` | Legacy | 3 / 4 / 3 / 3 / 3 | Medium | Medium | Wave 2 |
| `downloads` | Legacy | 4 / 4 / 4 / 3 / 4 | High | High | Wave 3 |
| `dashboard` | Legacy | 4 / 4 / 4 / 4 / 4 | High | High | Wave 3 |
| `discover` | Legacy | 5 / 5 / 4 / 4 / 5 | Very High | High | Wave 4 |
| `library` | Legacy | 4 / 5 / 4 / 4 / 5 | Very High | High | Wave 5 |
| `artist-detail` | Legacy | 5 / 5 / 4 / 5 / 5 | Very High | High | Wave 5 |
| `playlist-explorer` | Legacy | 4 / 4 / 4 / 3 / 4 | High | High | Wave 6 |
| `sync` | Legacy | 5 / 5 / 5 / 4 / 5 | Very High | High | Wave 7 |
| `settings` | Legacy | 5 / 5 / 4 / 5 / 5 | Very High | High | Wave 8 |
| `automations` | Legacy | 4 / 5 / 4 / 3 / 4 | High | High | Wave 8 |

## Page Catalog

### `issues`
- Current owner: React
- Primary files: `webui/src/routes/issues/*`, `webui/src/platform/shell/*`, `plans/webui-issues-migration-plan.md`
- Main surface: counts cards, filtered issue list, issue-detail modal, mutation flows
- Async behavior: route loader prefetch, React Query list/detail/count queries, mutation-driven refresh
- Coupling: shell page gating, shell nav badge refresh, bridge-controlled chrome
- Blockers or prerequisites: none; this is the migration baseline
- Scores: `2 / 2 / 2 / 2 / 2`
- Rationale: already proves the route-manifest + bridge + React-host pattern with limited shell leakage

### `help`
- Current owner: Legacy
- Primary files: `webui/index.html`, `webui/static/docs.js`, `webui/static/script.js`
- Main surface: documentation navigation, long-form content sections, screenshot lightbox, docs deep linking
- Async behavior: effectively none beyond image loading
- Coupling: shell nav and page activation only; contextual help metadata lives elsewhere but does not need to migrate with the page
- Blockers or prerequisites: keep `helper.js` shell-owned for now; migrate the Help route only, not the whole helper system
- Scores: `3 / 2 / 1 / 1 / 2`
- Rationale: large content surface, but low runtime complexity and limited cross-page coordination
- Recommendation: best low-risk route after `issues`

### `hydrabase`
- Current owner: Legacy
- Primary files: `webui/index.html`, `webui/static/script.js`
- Main surface: connection state, saved credentials, peer count, comparison list
- Async behavior: status fetch, connect/disconnect flows, comparisons load
- Coupling: dev-mode visibility, settings linkage, shell route gating
- Blockers or prerequisites: keep the Hydrabase shell toggle behavior outside the route initially
- Scores: `2 / 2 / 2 / 2 / 2`
- Rationale: bounded feature area with limited UI states and no broad reusable legacy entanglement
- Recommendation: safe early migration

### `stats`
- Current owner: Legacy
- Primary files: `webui/index.html`, `webui/static/script.js`
- Main surface: ranked lists, charts, db storage visualization, listening-stats refresh
- Async behavior: cached stats fetches, chart rendering, one-off sync actions
- Coupling: shell page activation, some library deep links back to artist/library routes
- Blockers or prerequisites: preserve chart library loading and route-to-library deep links
- Scores: `2 / 2 / 2 / 2 / 2`
- Rationale: read-heavy page with mostly isolated rendering and modest mutation behavior
- Recommendation: early migration candidate with good parity-test potential

### `import`
- Current owner: Legacy
- Primary files: `webui/index.html`, `webui/static/script.js`
- Main surface: staging files, grouped import candidates, album/track search, suggestions, match and process flows
- Async behavior: multiple search/match/process endpoints, modal-like step transitions inside the page
- Coupling: settings-derived staging path assumptions, downstream library state
- Blockers or prerequisites: likely benefits from shared album-search and suggestion primitives, but does not require them
- Scores: `3 / 3 / 3 / 2 / 3`
- Rationale: moderate workflow complexity, but still much more self-contained than discovery, sync, or library management
- Recommendation: last route in the initial low-risk wave

### `artists`
- Current owner: Legacy
- Primary files: `webui/index.html`, `webui/static/script.js`, `webui/static/helper.js`
- Main surface: search/results/detail view switching inside one route, artist caching, discography snippets, similar artists, watchlist interactions, download bubbles
- Async behavior: search debouncing, cancellation via abort controllers, detail/discography fetches, similar-artist loading
- Coupling: shares artist concepts with `library`, `artist-detail`, `discover`, and watchlist workflows
- Blockers or prerequisites: decide whether route migration should keep its internal view-state approach or split search/detail sub-routes later
- Scores: `3 / 4 / 3 / 3 / 3`
- Rationale: not tiny, but still a manageable route for establishing artist-focused React patterns before touching library detail
- Recommendation: first medium-complexity route after the initial safe wave

### `downloads`
- Current owner: Legacy
- Primary files: `webui/index.html`, `webui/static/script.js`, `webui/static/helper.js`
- Main surface: enhanced search, basic search, source tabs, filters, result cards, preview playback, candidate selection, download manager queues
- Async behavior: many search endpoints, batch downloads, active-process rehydration, polling, streaming preview
- Coupling: media player, queue, settings-derived source configuration, modal reuse across other pages
- Blockers or prerequisites: shared result-card, album-detail, and mutation-state primitives would help, but they do not have to exist before migration starts
- Scores: `4 / 4 / 4 / 3 / 4`
- Rationale: feature-rich and mutation-heavy, but valuable once the app already has a few safer route wins
- Recommendation: migrate only after help/hydrabase/stats/import/artists establish a stable pattern

### `dashboard`
- Current owner: Legacy
- Primary files: `webui/index.html`, `webui/static/script.js`, `webui/static/helper.js`, `webui/static/worker-orbs.js`
- Main surface: activity feed, service cards, worker buttons, backup manager, metadata cache, history modals, repair dashboard, recent sync history
- Async behavior: high polling density, worker status updates, activity feed refresh, backup and maintenance actions
- Coupling: almost every cross-cutting system eventually surfaces here
- Blockers or prerequisites: keep worker-orb visuals and global helper affordances shell-owned; route migration should focus on dashboard content first
- Scores: `4 / 4 / 4 / 4 / 4`
- Rationale: central page with broad read/write coverage and high shell entanglement
- Recommendation: treat as a mid-program migration, not an opening move

### `discover`
- Current owner: Legacy
- Primary files: `webui/index.html`, `webui/static/script.js`, `webui/static/helper.js`
- Main surface: hero carousel, your artists, Spotify library, recent releases, seasonal content, personalized shelves, ListenBrainz tabs, decade browser, genre browser, discovery blacklist
- Async behavior: very large parallel fetch fan-out, sync-status polling, watchlist integration, persistent playlist state hydration
- Coupling: shared album cards, watchlist/wishlist flows, sync actions, discovery-download sidebars, artist navigation
- Blockers or prerequisites: benefits from reusable album/artist card primitives, but the main risk is breadth rather than missing infrastructure
- Scores: `5 / 5 / 4 / 4 / 5`
- Rationale: one of the broadest pages in the app, with many semi-independent sections that still share global behaviors
- Recommendation: move after the team has already migrated a few medium-complexity, data-heavy routes

### `library`
- Current owner: Legacy
- Primary files: `webui/index.html`, `webui/static/script.js`, `webui/static/helper.js`
- Main surface: searchable artist grid, alphabet navigation, watchlist filters, pagination, download bubbles, deep links into `artist-detail`
- Async behavior: paginated fetches, watchlist mutations, shell-to-detail navigation
- Coupling: tightly bound to `artist-detail`, watchlist systems, active metadata source, and library-wide expectations
- Blockers or prerequisites: should not be migrated independently from the `artist-detail` strategy
- Scores: `4 / 5 / 4 / 4 / 5`
- Rationale: modest-looking page, but it is the entry point into the most complex library-management workflows
- Recommendation: migrate in the same program wave as `artist-detail`, not as an isolated quick win

### `artist-detail`
- Current owner: Legacy
- Primary files: `webui/index.html`, `webui/static/script.js`, `webui/static/helper.js`
- Main surface: hero section, standard discography, enhanced view, filters, selection/bulk operations, inline editing, manual match, tag preview/write, reorganize, quality enhancement
- Async behavior: detail fetches, streaming library-completion checks, multiple mutation workflows, modal stacks, playback actions
- Coupling: explicitly coupled to `library`; also touches watchlist settings, downloads, playback, metadata services, and file-organization settings
- Blockers or prerequisites: requires a clear React strategy for complex table/grid state, inline editing, and bulk actions before migration begins
- Scores: `5 / 5 / 4 / 5 / 5`
- Rationale: highest combined shell and workflow complexity outside `sync` and `settings`
- Recommendation: treat as a paired migration with `library`, and keep it out of early waves

### `playlist-explorer`
- Current owner: Legacy
- Primary files: `webui/index.html`, `webui/static/script.js`
- Main surface: visual discovery tree, artist tiers, album expansion, selection model, wishlist submission flow, zoom/pan interactions
- Async behavior: tree build, album-track fetches, wishlist processing, drag-like navigation, global document listeners for pointer/wheel events
- Coupling: artist navigation, wishlist flows, page-level document event handlers
- Blockers or prerequisites: needs a deliberate React strategy for viewport interactions and document-level listeners before migration starts
- Scores: `4 / 4 / 4 / 3 / 4`
- Rationale: narrower than `sync`, but still interaction-heavy and easy to regress
- Recommendation: late-mid program route after the team is comfortable migrating complex visual state

### `sync`
- Current owner: Legacy
- Primary files: `webui/index.html`, `webui/static/script.js`, `webui/static/helper.js`
- Main surface: Spotify/Tidal/Deezer/YouTube/Beatport/ListenBrainz/public Spotify playlists, mirrored playlists, URL histories, server playlist manager, discovery and sync phases, modal rehydration
- Async behavior: the heaviest page in the app; many endpoints, long-running workflows, state rehydration, polling, and live progress
- Coupling: download manager, server integrations, discovery workflows, active-process hydration, and multiple global state buckets
- Blockers or prerequisites: likely needs reusable route-local infrastructure for task state, progress polling, and source-specific adapters
- Scores: `5 / 5 / 5 / 4 / 5`
- Rationale: broadest operational surface and highest parity burden
- Recommendation: one of the last major migrations

### `settings`
- Current owner: Legacy
- Primary files: `webui/index.html`, `webui/static/script.js`, `webui/static/helper.js`, `webui/static/docs.js`
- Main surface: five-tab admin settings area, API/service credentials, media server setup, download source and quality config, file organization, appearance, advanced settings, profile/security integration, API keys
- Async behavior: large form hydration, auto-save, many service auth/test flows, dynamic source-specific form sections, media-library selectors
- Coupling: almost every other page depends on settings-derived behavior or stored configuration
- Blockers or prerequisites: route migration should be delayed until the app has settled conventions for large forms, auth/test actions, and configuration write flows
- Scores: `5 / 5 / 4 / 5 / 5`
- Rationale: the biggest shell container in `index.html` and one of the most globally coupled feature areas
- Recommendation: late migration despite the lack of constant polling, because the blast radius is large

### `automations`
- Current owner: Legacy
- Primary files: `webui/index.html`, `webui/static/script.js`, `webui/static/helper.js`
- Main surface: automation list, filters, execution status, run history modal, one-click hub groups, visual builder, block placement and config editing
- Async behavior: list loading, progress polling, run/deploy/toggle/history actions, builder save/load flows
- Coupling: uses many domain concepts but is less shell-dependent than settings or sync
- Blockers or prerequisites: migrating the builder safely requires a strong React approach for nested editable state and drag-like canvas interactions
- Scores: `4 / 5 / 4 / 3 / 4`
- Rationale: smaller shell footprint than settings or sync, but high internal interaction complexity
- Recommendation: save for the final wave with other complex authoring surfaces

## Platform Unlocks
These are not the primary ordering rule, but they are useful to recognize because they can lower later migration cost.

- `artists`
  - Likely unlocks reusable artist search, discography preview, and watchlist primitives for `discover`, `library`, and `artist-detail`.
- `downloads`
  - Likely unlocks reusable album/track result cards, playback-launch patterns, and download mutation handling for `discover`, `library`, and issue-detail admin actions.
- `library` + `artist-detail`
  - Likely unlock reusable entity-detail patterns, richer table state, batch actions, and file-management workflows.
- `settings`
  - Likely unlock shared admin form patterns and service-auth/test primitives, but the route is risky enough that it should not be used as an early proving ground.

## Recommended Migration Waves

### Wave 0: Reference baseline
- `issues`
- Goal: keep using the current Issues route as the canonical example for route ownership, shell bridge usage, and React-host activation.

### Wave 1: Safest wins
- `help`
- `hydrabase`
- `stats`
- `import`
- Why: these routes offer the best ratio of migration confidence to regression risk. They let the app add more React-owned routes without immediately entangling the team in global shell state or long-running workflow recovery.

### Wave 2: First medium-complexity route
- `artists`
- Why: good proving ground for async search, route-local cache/state, artist cards, and watchlist interactions before touching the full library-management stack.

### Wave 3: High-value operational routes
- `downloads`
- `dashboard`
- Why: both are important and complex, but by this point the team should already have route-local data-loading, mutation, and shell-bridge patterns that reduce migration risk.

### Wave 4: Broad discovery surface
- `discover`
- Why: very large rendering surface with many semi-independent sections. It should follow several prior migrations so the shared UI and query patterns are already mature.

### Wave 5: Library stack
- `library`
- `artist-detail`
- Why: these routes are tightly coupled and should be planned together. `artist-detail` is not an independent easy win and should not be pulled forward ahead of `library`.

### Wave 6: Visual interaction route
- `playlist-explorer`
- Why: highly interactive canvas/tree behaviors are easier to migrate once the broader React route architecture is already established.

### Wave 7: Multi-source orchestration route
- `sync`
- Why: this route has the deepest async orchestration, state rehydration, and external-service surface. It should be migrated only after the team has already de-risked several other route families.

### Wave 8: Final complex authoring/admin routes
- `settings`
- `automations`
- Why: these are high-blast-radius authoring surfaces with large state trees. They should land after the route architecture, shared UI patterns, and shell contracts are already stable.

## Why Earlier Waves Are Safer
- Wave 1 routes are either mostly static or bounded data UIs with limited cross-route side effects.
- Wave 2 adds moderate route-local state without forcing the app to solve shell-global task orchestration yet.
- Waves 3-4 add high-value routes once the migration pattern is established, rather than trying to learn the pattern inside the most coupled pages.
- Wave 5 intentionally treats the library stack as one problem space instead of creating a half-migrated split between `library` and `artist-detail`.
- Waves 6-8 defer the most interaction-heavy, orchestration-heavy, or configuration-heavy surfaces until the team has the most leverage.

## Final Recommendation
- Keep `issues` as the reference implementation and preserve the existing bridge contract.
- Migrate the low-risk routes first: `help`, `hydrabase`, `stats`, and `import`.
- Use `artists` as the first medium-complexity proving ground.
- Avoid pulling `settings`, `sync`, `library`, `artist-detail`, or `automations` forward unless there is a separate product priority strong enough to justify the added regression risk.
