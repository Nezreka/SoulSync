# Help sheet

What the ? button opens. Plan: `scrap/help/PLAN.md`. Look: `scrap/help/mockup.html`.

## How it mounts

`app/main.tsx` calls `mountHelpHost()` (host.tsx). That adds `#soulsync-help-root` to the end of `<body>`, sets `window.SoulSyncHelp` and the global keys, and fires `ss:help-ready`.

The ? button is still the vanilla `#helper-float-btn` in `index.html`. Its `onclick="toggleHelperMode()"` is a shim in `static/helper.js` that calls `window.SoulSyncHelp.toggle()`, or opens on `ss:help-ready` if the bundle hasn't run yet. The host mirrors the store onto the button: `aria-expanded`, and `data-help-alert` (the amber dot, admins with a Health error only; style in `static/style.css`).

```js
window.SoulSyncHelp.open();                    // Home
window.SoulSyncHelp.open('report');            // straight to a view, Home under it
window.SoulSyncHelp.open('article', { id: 'gs-docker' });
window.SoulSyncHelp.openSearch();              // caret in the search field
window.SoulSyncHelp.close() / .toggle() / .isOpen()
```

Keys (host.tsx `onGlobalKey`): `?` (not while typing) and `F1` toggle, `Ctrl/Cmd K` opens at the search field, `Esc` closes from anywhere unless another modal is open.

## Files

| File | Owner | What |
|---|---|---|
| `host.tsx` | P2 | root, `window.SoulSyncHelp`, keys, button state, status poll, page tracking |
| `sheet.tsx` | P2 | the dialog: header (back, title, close, search), body, footer, focus, Esc, phone trap |
| `store.ts` | P2 | open, view stack, query, page, isAdmin, status, version |
| `views.ts` | P2 | view ids, titles, which are wide, which are admin only |
| `outlet.tsx` | P2 | view id to component |
| `home.tsx` | P2 | Home: status card, On this page, Read more, More tiles, Get help |
| `ui.tsx` | P2 | shared pieces: `Label`, `List`, `Row`, `Tile`, `Button`, `Actions`, `Kbd`, `Placeholder` |
| `api.ts` | P2 (P4 adds) | typed client for `/api/help/*` and `/api/update-check` |
| `page.ts`, `links.ts` | P2 | which page, who is looking; where links go |
| `pages/index.ts` | P2 | the On this page registry and its types |
| `pages/base.ts` | P2 | every page's title, summary and articles |
| `pages/register.ts` | P2 (P5 adds lines) | imports base.ts and each page file |
| `pages/<page>.ts` | **P5** | live answers per page |
| `search/results.tsx` | **P3** | the inline results view (placeholder now) |
| `search/{index,rank,actions}.ts`, `search/palette.tsx` | **P3** | the index and the Ctrl K palette |
| `article.tsx` | **P3** | a docs article read in the sheet (placeholder now) |
| `health.tsx`, `report.tsx`, `logs.tsx` | **P4** | admin views (placeholders now) |
| `whats-new.tsx`, `shortcuts.tsx` | **P6** | (placeholders now), plus `release-notes.ts`, `shortcuts.ts`, `tours/*` |

## Extension points

**A view (P3, P4, P6).** Replace the placeholder file; keep its export name (`HealthView`, ...). It gets `ViewProps` (`view-props.ts`): the stack entry with its params, the query, isAdmin, the page. Push another view with `useHelpStore.getState().push({ view: 'logs' })`. A new view id goes in `views.ts` (title, `wide`, `admin`) and `outlet.tsx`. An `admin: true` view is never pushed for a member, from any path.

**Search (P3).** The header field writes `query`; at 2 characters the results view shows over the stack and Back clears it. When the palette lands, `onGlobalKey`'s Ctrl K branch should open it instead, and `SoulSyncHelp.openSearch()` can stay as the inline field.

**On this page (P5).** One file per page that calls `registerPageHelp({ id, useAnswers })`, plus one import line in `pages/register.ts`. The entry merges over the base one. `useAnswers` is a hook, mounted keyed by page id; return `PageAnswer[]` built from data the page already fetched, `[]` when there is nothing true to say. `tone: 'warn'` tints it and says Needs you, `live: true` says Live, `open: true` expands it. Page ids are the sidebar's: `active-downloads`, `settings`, `video-library`, ...

**Tours (P6).** A page entry's `tour` shows a "Tour: ..." row under Read more. Clicking closes the sheet and fires `ss:help-start-tour` with `{ id }`; the tour engine listens for it.

**Status (P1, P4).** The dot polls `/api/help/status` (`{errors, checked_at, refreshing}`, errors only) every 2 minutes and again 10 s later while the first run is going. Opening the sheet reads `/api/help/health` (cached 30 s server side) so Home can name the findings and count warnings; `mergeStatus` keeps those titles while the poll agrees. Admins only: a member never calls either, even though `/status` would answer `{}`.

## Styling

`help.module.css`, tokens only (`static/tokens.css` has `--help-sheet` and `--help-on-accent`). No hex, rgb literals or `!important` (`src/test/css-budget.test.ts`), radii and z-index from tokens (stylelint). Tone and kind go through data attributes, not dynamic class names, so `css-module-classes` can see every class. The app has no light theme, so neither does the sheet.

## Tests

`sheet.test.tsx` (open/close from button and keys, focus in and back, Esc, phone trap, back stack, admin vs member, the dot) and `src/test/helper-menu-opens.test.ts` (the real helper.js shim).
