# WebUI Stats Migration Plan

Snapshot date: 2026-05-14

## Status

- Completed on 2026-05-14.
- `stats` is now React-owned in the shell route manifest.
- The legacy stats HTML, JS, and CSS path has been removed.
- The global `Chart.js` import was removed and replaced with route-local `Recharts`.
- Legacy playback and artist-detail handoffs now go through the explicit shell bridge.
- A local seed script exists for realistic UI testing without production listening history: `tools/seed_stats_ui_scenarios.py`.

## Goal

- Migrate `stats` from the legacy shell to the React route host.
- Replace the global `Chart.js` CDN script with route-local React chart components.
- Use the `issues` route slice as the structural reference, but add a few stronger conventions for data-heavy read-only pages.

## Why `stats` Is The Right Next Route

- The route is shell-local today.
- The activation path is narrow.
- The page has real async data loading and interaction.
- The page is complex enough to validate query conventions, search-param state, and route-local chart components.
- The page does not currently drive broad shell-global workflows.

This route has now validated those assumptions successfully.

## Current Legacy Shape

Page surface in `webui/index.html`:

- Header
  - time range buttons
  - last synced label
  - manual sync action
- Overview cards
- Left column
  - listening activity chart
  - genre breakdown chart
  - recently played list
- Right column
  - top artists
  - top albums
  - top tracks
- Full-width sections
  - library health
  - library disk usage
  - database storage
- Empty state

Legacy JS responsibilities in `webui/static/stats-automations.js`:

- page initialization
- range switch handling
- data fetch orchestration
- formatting helpers
- chart instantiation and teardown
- ranked list rendering
- cross-page deep links into library / artist detail
- playback handoff for recent and top tracks

Backend endpoints already split cleanly:

- `GET /api/stats/cached`
- `GET /api/stats/db-storage`
- `GET /api/stats/library-disk-usage`
- `POST /api/listening-stats/sync`
- `GET /api/listening-stats/status`

There are also narrower stats endpoints in the backend, but the current page already gets most of its main payload from the cached route.

## Library Choice

Recommended charting library:

- `recharts`

Reasoning:

- React-native component model
- good fit for bar + doughnut-style dashboards
- easy to split into small route-local components
- easier to theme from CSS variables than raw imperative chart setup
- easier to test than a canvas-first imperative path

Not recommended for this migration:

- `react-chartjs-2`
  - better for parity-only migration
  - still keeps the mental model close to Chart.js
- `visx`
  - stronger for bespoke visualization systems
  - more work than this page needs

## Proposed Route Slice

```text
webui/src/routes/stats/
  route.tsx
  -stats.types.ts
  -stats.api.ts
  -stats.helpers.ts
  -stats.api.test.ts
  -stats.helpers.test.ts
  -ui/
    stats-page.tsx
    stats-page.module.css
    stats-header.tsx
    stats-overview-cards.tsx
    stats-empty-state.tsx
    stats-ranked-list.tsx
    stats-recent-plays.tsx
    stats-library-health.tsx
    stats-disk-usage.tsx
    stats-activity-chart.tsx
    stats-genre-chart.tsx
    stats-db-storage-chart.tsx
```

## Proposed Route Responsibilities

`route.tsx`

- declare `/stats`
- validate search params
- gate route through `bridge.isPageAllowed('stats')`
- preload the shell context
- load the main cached stats payload plus listening-status payload
- optionally preload disk usage and db storage if we want zero-layout-shift first render

`-stats.types.ts`

- search param schema
- response payload types
- normalized display shapes
- chart row types

`-stats.api.ts`

- query keys
- fetchers for:
  - cached stats
  - listening status
  - db storage
  - library disk usage
- mutation helper for manual sync
- invalidation helpers

`-stats.helpers.ts`

- range labels
- numeric and duration formatters
- disk size formatters
- chart data shaping
- legend shaping
- safe fallbacks for empty server responses

`-ui/stats-page.tsx`

- page composition
- search-param driven range selection
- section layout
- empty-state branching

## Search Params

Use search params for state that should survive reloads and linking:

- `range`

Recommended values:

- `7d`
- `30d`
- `12m`
- `all`

This is the one clear page-state value worth encoding in the URL. Everything else can remain derived from server data.

## Query Model

Recommended split:

- primary query:
  - `statsCachedQueryOptions(profileId, range)`
- secondary queries:
  - `statsListeningStatusQueryOptions(profileId)`
  - `statsDbStorageQueryOptions(profileId)`
  - `statsLibraryDiskUsageQueryOptions(profileId)`

Why this split:

- `cached` is the real page backbone
- `db-storage` and `library-disk-usage` are already separate in the backend
- they can render as progressively enhanced cards without blocking the whole route
- `listening-stats/status` updates the sync label and complements the sync mutation

Recommended route-loader behavior:

- always ensure:
  - cached stats
  - listening status
- optional:
  - db storage
  - disk usage

If we want a snappier first migration, we should keep the last two as client-side `useQuery` calls rather than route-loader requirements.

## Component Sketch

`StatsPage`

- calls `useReactPageShell('stats')`
- reads `range` from route search
- renders:
  - `StatsHeader`
  - `StatsOverviewCards`
  - `StatsEmptyState` or main sections

`StatsHeader`

- range segmented control
- last synced text
- sync button mutation

`StatsOverviewCards`

- five summary cards

`StatsActivityChart`

- Recharts `BarChart`
- responsive container
- route-local tooltip
- accepts already-shaped rows

`StatsGenreChart`

- Recharts `PieChart`
- legend rendered in React markup beside the chart
- top-10 clipping stays in helpers

`StatsDbStorageChart`

- Recharts `PieChart`
- custom center label rendered in React
- legend list rendered beside chart

`StatsRankedList`

- shared component for artists / albums / tracks
- variant props for:
  - artwork
  - subtitle/meta
  - count label
  - optional play action
  - optional artist-detail deep link

`StatsRecentPlays`

- simple list component
- play action

`StatsLibraryHealth`

- overview metrics
- format breakdown bar
- enrichment coverage rows

`StatsDiskUsage`

- total bytes row
- pending/deep-scan message
- per-format horizontal bars

## Recharts Mapping

Legacy Chart.js to React mapping:

- listening activity
  - from imperative `new Chart(... type: 'bar')`
  - to `ResponsiveContainer` + `BarChart` + `Bar` + `XAxis` + `YAxis` + `Tooltip`
- genre breakdown
  - from doughnut chart
  - to `PieChart` + `Pie` + custom legend
- database storage
  - from doughnut chart with center total overlay
  - to `PieChart` + `Pie` + React-rendered center label

Suggested chart convention:

- keep all chart data shaping outside the chart components
- chart components should receive already-normalized rows and colors
- never read directly from raw server payloads inside Recharts markup

## CSS Strategy

Recommended first pass:

- create `stats-page.module.css`
- port stats-specific selectors from `webui/static/style.css`
- keep class names semantically similar to reduce migration risk

Suggested approach:

- move only the selectors needed by the React route
- leave legacy stats selectors in place until the route flip is complete
- after the React route owns `stats`, remove unused legacy selectors in a cleanup pass

Do not try to redesign the page during the migration.

## Shell And Routing Changes

When the route is ready:

1. Add `webui/src/routes/stats/route.tsx`
2. Regenerate the TanStack route tree if needed
3. Change `stats` from `legacy` to `react` in `webui/src/platform/shell/route-manifest.ts`
4. Keep the legacy `stats-page` DOM in `webui/index.html` during the initial cutover if that reduces risk
5. Remove legacy activation from `webui/static/init.js` once React ownership is confirmed
6. Remove the global Chart.js script from `webui/index.html`

## Incremental Migration Order

Recommended order:

1. Add types, API layer, and helpers
2. Build the React route with plain markup and no charts yet
3. Port overview, ranked lists, recent plays, and empty state
4. Port library health and disk usage
5. Port Recharts activity, genre, and db storage charts
6. Flip route ownership from legacy to React
7. Remove global Chart.js import
8. Delete or shrink legacy `stats` logic from `stats-automations.js`

This order gives us a working React page before charting becomes the critical path.

## Testing Sketch

Unit tests:

- `-stats.helpers.test.ts`
  - range formatting
  - duration formatting
  - db storage grouping into `Other`
  - genre top-10 shaping
  - disk usage empty-state shaping

API tests:

- `-stats.api.test.ts`
  - cached stats success / error
  - listening status success / error
  - db storage success / error
  - disk usage success / error
  - sync mutation success / error

Route / component tests:

- initial render for default `range=7d`
- changing range updates the URL and query key
- empty state renders when `overview.total_plays === 0`
- ranked artist click deep-links to library / artist detail
- track play action triggers the expected handoff
- sync action shows pending state and invalidates relevant queries

Playwright is optional for the first pass.

## Decisions To Keep Simple

- Keep the existing page structure.
- Keep the current backend endpoint split.
- Keep the current time-range set.
- Reuse the existing shell deep-link behavior for library and playback.
- Use Recharts only inside `stats` first.

## Follow-Up Opportunities

- Extract shared chart colors into route-local constants or a small shared viz helper.
- Consider a tiny `components/charts/` layer only after a second React page needs charts.
- Revisit whether `stats/cached` should remain the primary page payload or whether the route should fan out to narrower endpoints later.
- Keep watching for overlap between route-local controls and shared UI primitives. The stats range selector is a good example of a pattern that should stay local for now, but should be reconsidered if another migrated route needs the same segmented-control behavior.

## Recommendation

The first implementation should optimize for:

- parity
- clear route-local boundaries
- removal of global `Chart.js`
- reusable data/query conventions

It should not optimize for:

- visual redesign
- a cross-app chart abstraction
- backend reshaping

## Outcome

- The route now serves as the reference for data-heavy read-only React pages.
- The migration proved out route-local charts, route-search state, explicit shell-bridge interop, and post-cutover legacy cleanup.
- The work also reinforced a migration guideline for future routes:
  - prefer local implementation on first use
  - actively note overlap with shared primitives
  - extract only once the second clear consumer appears
