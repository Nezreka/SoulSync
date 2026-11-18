# Help sheet

What the ? button opens. Plan: `scrap/help/PLAN.md`. Look: `scrap/help/mockup.html`. Backend: `core/help/` and `api/help.py` (`/api/help/status`, `/health`, `/bundle`, `/logs`).

## How it mounts

`app/main.tsx` calls `mountHelpHost()` (host.tsx). That adds `#soulsync-help-root` to the end of `<body>`, then, in order:

1. `installReleaseNotes()` (release-notes.ts): `window.SoulSyncReleaseNotes`, for the version modal (downloads.js `showVersionInfo`)
2. `installTours()` (tours/index.tsx): the `ss:help-start-tour` listener, `window.SoulSyncTours`, and each tour offered on its pages
3. `installHelpApi()`: `window.SoulSyncHelp`
4. `installHelpKeys()`: the global keys
5. renders `<HelpHost />` and fires `ss:help-ready`

Nothing here installs itself on import.

The ? button is the vanilla `#helper-float-btn` in `index.html`. Its `onclick="toggleHelperMode()"` is the shim in `static/helper.js` (92 lines: the shim, `_helperPageId`, the idle glow, a first launch pointer that opens the sheet). It calls `window.SoulSyncHelp.toggle()`, or opens on `ss:help-ready` if the bundle hasn't run yet. The host mirrors the store onto the button: `aria-expanded`, and `data-help-alert` (the amber dot, admins with a Health error only; style in `static/style.css`).

```js
window.SoulSyncHelp.open();                    // Home
window.SoulSyncHelp.open('report');            // straight to a view, Home under it
window.SoulSyncHelp.open('article', { id: 'gs-docker' });
window.SoulSyncHelp.openSearch();              // caret in the search field
window.SoulSyncHelp.close() / .toggle() / .isOpen()
window.SoulSyncTours.start('getting-around') / .list() / .running() / .end()
```

Keys (host.tsx `onGlobalKey`): `?` (not while typing) and `F1` toggle, `Ctrl/Cmd K` opens the palette from any page (with the sheet open, its search field), `Esc` closes from anywhere unless another modal is open. Ctrl/Cmd K has one owner: `src/test/helper-menu-opens.test.ts` fails if any other script binds it. Settings' own search is on `/` (features/settings/shell.ts `onSettingsSearchKey`). Every key SoulSync has is in `shortcut-registry.ts`.

## Files

Owner is the phase that built it; anyone may change it now, keep its tests green.

| File | Owner | What |
|---|---|---|
| `host.tsx` | P2 | root, install order, `window.SoulSyncHelp`, keys, button state, status poll, page tracking |
| `sheet.tsx` | P2 | the dialog: header (back, title, close, search), body, footer, focus, Esc, phone trap |
| `store.ts` | P2 | open, view stack, query, page, isAdmin, status, version |
| `views.ts`, `view-props.ts` | P2 | view ids, titles, which are wide, which are admin only; what a view gets |
| `outlet.tsx` | P2 | view id to component |
| `home.tsx` | P2 | Home: status card, On this page (heading, answers), Read more, More tiles, Get help |
| `ui.tsx` | P2 | shared pieces: `Label`, `List`, `Row`, `Tile`, `Button`, `Actions`, `Kbd`, `Placeholder` |
| `icons.tsx` | P2 | the sheet's icon set |
| `help.module.css` | P2 | the sheet, Home and the shared pieces |
| `api.ts` | P2, P4 | typed client for `/api/help/*` and `/api/update-check`; `postFix` posts `/api/` routes only |
| `page.ts` | P2 | which page (`pageIdFrom`), which side, who is looking |
| `links.ts` | P2 | where links go: pages, `openArticleInSheet`, `openArticleInDocs`, Discord, GitHub |
| `pages/index.ts` | P2 | the On this page registry and its types (`PageHelp`, `PageAnswer`, `UseAnswers`, `UseHeading`) |
| `pages/base.ts` | P2 | every page's title, summary and articles |
| `pages/register.ts` | P2, P5 | imports base.ts and each page file |
| `pages/kit.tsx` | P5 | what page files build from: `useLive`, `useFeedOrPoll`, `useHealthFindings`, words (`plural`, `spoken`, `ago`), pieces (`Why`, `WhyRow`, `Act`, ...), `runFix` and `FindingBody` for Health findings in an answer |
| `pages/answers.module.css` | P5 | the answers' rows, bars and actions |
| `pages/{active-downloads,video-downloads,requests,wishlist,import,settings,library,search}.tsx` | P5 | live answers per page; settings also names its tab (`useHeading`) |
| `pages/answers.test-utils.tsx` | P5 | renders answers the way Home does, for the page tests |
| `search/index.ts` | P3 | the one index: actions, settings (`features/settings/search-entries.ts`), pages, articles; member filtering; recents |
| `search/rank.ts` | P3 | matching and ranking, snippets |
| `search/actions.ts` | P3 | the things search can do, each with its permission |
| `search/docs.ts` | P3 | the docs articles, from docs.js's registry (`window.SoulSyncDocs.sections()`) |
| `search/docs.test-utils.ts` | P8 | runs the real docs.js and docs-content files for tests |
| `search/markdown.tsx` | P3 | the docs' markdown as React, for the article view |
| `search/results.tsx` | P3 | the inline results view |
| `search/palette.tsx` | P3 | the Ctrl/Cmd K palette (combobox, groups, "Search music for ...") |
| `search/search.module.css` | P3 | results and palette |
| `article.tsx`, `article.module.css` | P3 | a docs article read in the sheet, "Open in Help & Docs" |
| `health.tsx` | P4 | Health: summary, Needs you, Worth a look, Finish setting up, Running fine, the fixes |
| `fixes.ts` | P4 | the one fix runner (`runFix`) and openers (`openSettings`, `openPage`), used by Health and the page answers |
| `report.tsx` | P4 | Report a problem: what happened, the switchable parts with their exact text, GitHub, Discord, file |
| `logs.tsx` | P4 | Logs: every log and its backups, one file, one log or all as a zip, Remove secrets |
| `format.ts` | P4 | sizes and times in plain words |
| `glyphs.tsx` | P4 | the extra icons the admin views use |
| `switch.tsx` | P4 | the on/off switch Report and Logs use |
| `admin.module.css` | P4 | Health, Report and Logs |
| `release-notes.ts` | P6 | the release notes, the one file to edit at release time; `installReleaseNotes` |
| `whats-new.tsx`, `whats-new.module.css` | P6 | What's new, with Show me |
| `shortcut-registry.ts` | P6 | every keyboard shortcut, each pointing at its handler |
| `shortcuts.tsx`, `shortcuts.module.css` | P6 | the Shortcuts view |
| `tours/tours.ts` | P6 | the three tours: Getting around, Your first download, Your first video request |
| `tours/anchors.ts` | P6 | every `data-tour` anchor and the component that writes it |
| `tours/engine.ts` | P6 | the running tour: steps, skip a missing anchor, spotlight |
| `tours/layer.tsx`, `tours/tour.module.css` | P6 | the scrim and the step card |
| `tours/navigate.ts`, `tours/place.ts` | P6 | getting to a step's page and finding its anchor; where the card goes |
| `tours/index.tsx` | P6 | `installTours`, `window.SoulSyncTours`, the tour root |

Outside this folder: `routes/active-downloads/-dl.feed.ts` (the Downloads page's poll, shared with the answers), `features/settings/search-entries.ts` (what search finds in Settings), `features/settings/page-tabs.ts` (the Settings tab you're on).

## Extension points

**A view.** A view id goes in `views.ts` (title, `wide`, `admin`) and `outlet.tsx`; the component gets `ViewProps`. Push it with `useHelpStore.getState().push({ view: 'logs' })`. An `admin: true` view is never pushed for a member, from any path.

**A page's help.** Its title, summary and articles go in `pages/base.ts`. Live answers: one file that calls `registerPageHelp({ id, useAnswers })`, plus one import line in `pages/register.ts`; the entry merges over the base one. `useAnswers` is a hook, mounted keyed by page id; return `PageAnswer[]` from data the page already fetched, `[]` when there is nothing true to say. `tone: 'warn'` says Needs you, `live: true` says Live, `open: true` expands it. A heading that follows the page's state (Settings, Downloads) is `useHeading`. Page ids are the sidebar's: `active-downloads`, `settings`, `video-library`, ...

**Data a page already polls.** Don't poll it again: publish it to a feed like `routes/active-downloads/-dl.feed.ts` from the page's hook (`attach` on mount, `publish` each good poll) and read it with `useFeedOrPoll`, which polls on its own only while the page isn't mounted.

**A Health fix kind.** `HealthFix` in `api.ts` and a case in `fixes.ts` `runFix`; both Health and the answers pick it up.

**Search.** A setting: an entry in `features/settings/search-entries.ts` (its anchor is checked by `search-entries.test.ts`). An action: `search/actions.ts`, with `admin` and `when`. A docs article: write it in `static/docs-content/`, it is indexed from docs.js's registry.

**A shortcut.** Bind it where it lives and add it to `shortcut-registry.ts` with the file and a string from its handler; `shortcut-registry.test.tsx` fails if the handler goes.

**A tour.** Steps in `tours/tours.ts` naming anchors from `tours/anchors.ts`; the component that owns the element writes `data-tour="..."`. `tours/anchors.test.tsx` fails on a missing or unused anchor. A tour is offered under Read more on each page in its `pages`.

**A release.** Edit `release-notes.ts` only (its header says how).

## Styling

CSS modules, tokens only (`static/tokens.css` has `--help-sheet` and `--help-on-accent`). No hex, rgb literals or `!important` (`src/test/css-budget.test.ts`), radii and z-index from tokens (stylelint). Tone and kind go through data attributes, not dynamic class names, so `css-module-classes` can see every class. The app has no light theme, so neither does the sheet. The only legacy rules left are the ? button's and the first launch pointer's in `static/style.css` and `mobile.css`.

## Tests

`sheet.test.tsx` (open/close from button and keys, focus in and back, Esc, phone trap, back stack, admin vs member, the dot, Read more in the sheet, the Settings heading), `src/test/helper-menu-opens.test.ts` (the real helper.js shim, the first launch pointer, one owner for Ctrl K), `features/settings/settings-keys.test.ts` (Ctrl K and `/` on Settings), and a test file beside every view, page file, search piece and tour file.
