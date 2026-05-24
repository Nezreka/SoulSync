# WebUI Import Migration Plan

Snapshot date: 2026-05-24

## Status

- Initial implementation completed on 2026-05-15.
- `import` is now React-owned in the shell route manifest.
- The legacy import page DOM has been removed from `webui/index.html`.
- Legacy import activation has been removed from `webui/static/init.js`.
- A React route subtree now owns import rendering, nested route state, album matching, singles matching, auto-import controls, and the client-side processing queue.
- The old `tab=` URL contract has been replaced by `/import/album`, `/import/singles`, and `/import/auto`, with `/import` redirecting to `/import/album`.
- Route-local workflow state lives in `webui/src/routes/import/-import.store.ts`, which keeps draft matching, selection, and queue state alive while navigating within the import route.
- The old import-page-specific functions have already been removed from `webui/static/stats-automations.js`; any remaining `import` references there belong to the broader automation feature set, not this page migration.
- Backend routes are already grouped around `/api/import/*` and `/api/auto-import/*`.

## Goal

- Migrate `import` into a React-owned route without changing the user workflow.
- Preserve manual album matching, singles matching, auto-import review, and the processing queue.
- Keep staging-folder and import-processing behavior backed by the existing API routes.
- Use the completed `issues` and `stats` routes as the structural reference for route slices, API helpers, shell gating, and tests.

## Why `import` Was The Right Next Route

- It is the safest remaining route after excluding `help` and `hydrabase`.
- It has real workflows, so it gives the migration program more signal than another mostly-static page.
- The backend API boundary is already clearer than the broad dashboard, library, discover, sync, or settings surfaces.
- The page is important enough to validate mutation, polling, and route-local reducer patterns before larger operational pages.
- It does not need a visual redesign or a new shell abstraction to migrate cleanly.

## Current Legacy Shape

Page surface in `webui/index.html`:

- Header
  - import folder path
  - file count and total size
  - refresh action
- Processing queue
  - per-job progress
  - partial error display
  - clear-finished action
- Tabs
  - auto-import
  - albums
  - singles
- Auto-import tab
  - enable toggle
  - status text
  - confidence and interval settings
  - scan-now action
  - live scan progress
  - result filters
  - approve, reject, approve-all, and clear-completed actions
- Albums tab
  - auto-group suggestions from staging
  - album search
  - album result cards
  - track matching view
  - drag/drop and tap-to-assign overrides
  - unmatched file pool
  - process album action
- Singles tab
  - staging file list
  - select all / per-file selection
  - per-file track search
  - manual match selection
  - process selected action

Legacy JS responsibilities in `webui/static/stats-automations.js`:

- `initializeImportPage`
- staging fetch and refresh
- tab switching
- auto-import polling
- auto-import mutations
- staging group and suggestion rendering
- album search and match POSTs
- drag/drop assignment state
- singles search and selection state
- client-side processing queue
- sequential album/singles processing requests

Backend endpoints already available:

- `GET /api/import/staging/files`
- `GET /api/import/staging/groups`
- `GET /api/import/staging/hints`
- `GET /api/import/staging/suggestions`
- `GET /api/import/search/albums`
- `POST /api/import/album/match`
- `POST /api/import/album/process`
- `GET /api/import/search/tracks`
- `POST /api/import/singles/process`
- `GET /api/auto-import/status`
- `POST /api/auto-import/toggle`
- `GET /api/auto-import/settings`
- `POST /api/auto-import/settings`
- `GET /api/auto-import/results`
- `POST /api/auto-import/approve/:id`
- `POST /api/auto-import/reject/:id`
- `POST /api/auto-import/scan-now`
- `POST /api/auto-import/approve-all`
- `POST /api/auto-import/clear-completed`

## Implemented Route Slice

```text
webui/src/routes/import/
  route.tsx
  index.tsx
  album.tsx
  auto.tsx
  singles.tsx
  -import.types.ts
  -import.api.ts
  -import.helpers.ts
  -import.store.ts
  -route.test.tsx
  -ui/
    import-page.tsx
    album-import-tab.tsx
    auto-import-tab.tsx
    singles-import-tab.tsx
    import-shared.tsx
```

## Implemented Route Responsibilities

`route.tsx`

- declare `/import`
- gate route through `bridge.isPageAllowed('import')`
- preload shell context
- prefetch the staging-files query without blocking on transient fetch failure

`index.tsx`

- redirect `/import` to `/import/album`

`album.tsx`

- prefetch album staging groups and suggestions

`auto.tsx`

- validate the `autoFilter` search param
- keep route-driven filter changes in the URL while leaving the rest of the workflow state local

`singles.tsx`

- mount the singles import tab without extra route-level loader work

`-import.types.ts`

- search param schema
- API response types
- staging file, staging group, album result, track result, match, auto-import result, and queue item types

`-import.api.ts`

- query options for staging, groups, suggestions, auto-import status, auto-import settings, auto-import results, album search, and track search
- mutation helpers for album match, album process, singles process, auto-import actions, and settings writes
- invalidation helpers for broad route refreshes, staging-only refreshes, and auto-import-only refreshes

`-import.helpers.ts`

- byte-size formatting
- album and track display labels
- confidence class/label mapping
- staging match normalization
- auto-import result filtering and counters

`-import.store.ts`

- album search state, selected album, auto-group file paths, and match overrides
- selected single-file state and manual matches
- queue job state and queue entry updates
- single-search draft state
- draft state survival across nested route remounts

`-ui/import-page.tsx`

- page chrome and nested route navigation
- queue summary and queue-item rendering
- nested route outlet for album, singles, and auto views

## Search Params

Use nested route paths for durable, shareable tab state:

- `/import/album`
  - default landing route
- `/import/singles`
- `/import/auto`
- `autoFilter`
  - values: `all`, `pending`, `imported`, `failed`
  - default: `all`

Keep these local to React state:

- album search text
- track search text
- selected album
- match overrides
- selected singles
- processing queue jobs

Reasoning:

- The tab choice belongs in the path, which keeps deep links simple and avoids an extra `tab=` query param.
- The auto-import filter is still useful after reloads.
- The matching workflow is ephemeral and should not create fragile URLs with file indexes or local staging paths.

## Query Model

Critical route-loader data:

- `importStagingFilesQueryOptions()`

Useful prefetch data:

- `importStagingGroupsQueryOptions()`
- `importStagingSuggestionsQueryOptions()`

Nested-route data:

- `autoImportStatusQueryOptions()`
- `autoImportSettingsQueryOptions()`
- `autoImportResultsQueryOptions(autoFilter)`

Lazy search data:

- `importAlbumSearchQueryOptions(query)`
- `importTrackSearchQueryOptions(query)`

Mutation-style actions:

- album match draft
- process one album track
- process one single file
- toggle auto-import
- save auto-import settings
- scan now
- approve/reject auto-import result
- approve all
- clear completed

Invalidation rules:

- Processing album or singles files invalidates staging files, staging groups, staging suggestions, auto-import results, and any route-local queue completion summary.
- Auto-import actions invalidate auto-import status and results.
- Auto-import settings writes invalidate settings and status.
- Refresh invalidates staging files, groups, and suggestions.

## Incremental Migration Order

Recommended order:

1. Add route slice, types, API helpers, reducer, and helper tests.
2. Build the React route shell with header, tabs, and staging summary.
3. Port the Albums tab search and suggestions, but keep processing disabled until match rendering is covered.
4. Port the album matching view, including drag/drop and tap assignment.
5. Port the processing queue and album/singles process mutations.
6. Port the Singles tab.
7. Port the Auto tab and polling behavior.
8. Flip `import` from `legacy` to `react` in the shell route manifest.
9. Remove the legacy `import-page` DOM from `webui/index.html`.
10. Remove import-specific legacy functions from `webui/static/stats-automations.js`.

This order gives us a visible React page early while delaying the highest-risk file-processing actions until the state model is tested.

The implemented route keeps the same overall migration shape, but the final URL contract uses nested route paths instead of a `tab=` search param.

## Testing Sketch

Unit tests:

- route path and filter defaults
- staging summary formatting
- auto-import counters
- confidence labels
- reducer assignment behavior
- reducer queue transitions

API tests:

- staging files success and error
- staging groups success and error
- album search success and empty result
- track search success and empty result
- album match success and failure
- album process success and partial error
- singles process success and partial error
- auto-import status/results/settings/actions

Route / component tests:

- unauthorized users redirect to profile home
- default route redirects to `/import/album`
- `/import/singles` renders the Singles tab
- `/import/auto?autoFilter=pending` renders pending auto-import results
- refresh invalidates staging queries
- album selection opens the match view
- drag/drop and tap assignment update track matches
- processing queue advances and refreshes staging on completion
- client workflow drafts survive page remounts

Playwright can wait until after route ownership flips.

## Risks

- The processing queue is client-side and long-running.
- Auto-import polling must stop when leaving the auto subroute or route.
- File indexes can become stale after staging refreshes.
- Album matching depends on preserving source, album name, and album artist from search results.
- The page currently shares a large legacy module with stats and automations code, so cleanup should be careful and incremental.

## Decisions To Keep Simple

- Keep the current visual language.
- Keep the existing backend endpoints.
- Keep the processing queue client-side for the first migration.
- Keep file matching state local to the route.
- Do not extract shared workflow primitives until a second migrated route needs them.

## Outcome

- The route now serves as the first React-owned workflow migration.
- The implementation uses nested route paths plus a validated `autoFilter` search param.
- The route uses TanStack Query for staging data, suggestions, auto-import polling, mutations, and invalidation.
- Tests cover shell ownership, nested route state, album match payload preservation, and auto-import rendering.

## Recommendation

Treat remaining work as cleanup and hardening rather than route selection.

Follow-up work should optimize for:

- shrinking `stats-automations.js` after cutover
- adding E2E coverage around full album and singles processing
- considering route-level code splitting once more large React routes land

It should not optimize for:

- redesign
- backend reshaping
- shared queue abstractions
- migrating `automations` at the same time
