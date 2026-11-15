# Settings page: port to React

Everything about moving the Settings page from vanilla JS to React. Updated with every change on this branch.

- **Branch:** `settings-react` (worktree `../SoulSync-settings-react`), off dev `52f2190d7`
- **Started:** Nov 14 2026
- **Last updated:** Nov 15 2026 (steps 1 to 5 done: the scaffold, Appearance, Logs, Discovery, Downloads without the chain)

## Goal

Break up the two biggest frontend files by porting Settings to React, **with no visual or behaviour change**.

| File | Lines today | After the port |
|---|---|---|
| `webui/index.html` (whole file) | 12,823 | about 4,300 (the settings markup, lines 1940-10428, is about 8,500 of it) |
| `webui/static/settings.js` | 10,187 | deleted |
| `webui/static/video/video-settings.js` | 2,940 | deleted (ported with the tabs it hooks) |

## Decisions

| Date | Decision | Why |
|---|---|---|
| Nov 14 | Port only, no redesign | Boulder: "maybe we shouldn't redesign it and just port it". A 1:1 port can be checked: any difference is a bug. |
| Nov 14 | Redesign shelved, not dropped | Two mockups exist (v1 rejected, v2 at https://claude.ai/artifact/63tP4jKMYXnHpLqYsbUt3N, no verdict). Ideas to revisit once the page is React: sections that show their current values, service tiles with a side sheet, chips for small enums. |
| Nov 14 | Own branch, merged tab by tab | The old page must keep working the whole time. |
| Nov 14 | Keep the existing CSS classes | So the React page looks identical and `settings-premier.css` / `style.css` keep styling it. No new stylesheet (the CSS budget test forbids one). |

## How the page works today (what has to be preserved)

- **One page, two sides.** Music and video share `#settings-page`. Blocks marked `data-video-only` / `data-music-only` show per side (`body[data-side]`). `video-settings.js` reaches into the same markup and saves video values through `/api/video/*`.
- **Nine tabs**, switched by `switchSettingsTab`. A section belongs to a tab through its `data-stg` attribute, not through where it sits in the file, so sections of one tab are scattered through the markup.
- **One save for everything.** `saveSettings()` (settings.js:6795) reads every control on the page by id into one object and POSTs it to `/api/settings`. The route accepts partial settings (it walks a list of section names and takes the ones present), so a tab can save only its own sections.
- **Also saved in the same call:** the quality profile (`saveQualityProfile`), discovery lookback (`/api/discovery/lookback-period`) and hemisphere (`/api/discovery/hemisphere`).
- **Autosave.** Every input is wired to `debouncedAutoSaveSettings` (2s debounce). Quality profile controls go to `debouncedSaveQualityProfile` (800ms). The switch `settings-autosave-enabled` (System > Saving, added Nov 14, stored as `ui_appearance.settings_autosave`) turns both off so changes wait for the save button.
- **Save status button** `#save-settings` with states idle / pending / saving / saved / error (`setSettingsSaveState`).
- **Guards that must survive:** no save while `window._settingsLoadFailed` (#879), no autosave on the Logs tab (#827), no autosave while the form is being filled (`window._suppressSettingsAutoSave`), video-owned fields skip the music save.
- **Loaded on every visit:** `initializeSettings` → `switchSettingsTab('connections')` → `loadSettingsData` → `loadQualityProfile` → `loadApiKeys` → `loadBlacklistCount` (init.js, case `settings`). (Since step 6 the quality profile loads in the React Quality tab, on the same visit, from `loadSettingsData`'s hydrate.)
- **Inline handlers:** 398 `onclick` / `onchange` / `oninput` attributes in the settings markup call global functions, so those globals must exist until their markup is gone.

## Method

1. **Scaffold** (no tab content): a React host that mounts into slots inside `#settings-page` (same pattern as `webui/src/features/video-library/host.tsx`), a typed settings store, the save status and the autosave rules. The page stays `kind: 'legacy'` in `route-manifest.ts` until the last tab is done.
2. **One tab at a time.** React renders the tab with the same classes and ids. In the same change: delete that tab's markup from `index.html`, its fields from the `saveSettings` collector and `loadSettingsData`, and its helper functions from `settings.js`.
3. **Order:** Appearance, Logs, Discovery, Downloads, Quality, Sources, Library, System, Connections. Video hooks move with each tab; video-only panels last.
4. **Per tab, before it merges:** parity tests against the frozen vanilla, the artefact-diff sweep (classes, ids, globals and strings compared with the old markup), before/after screenshots that match, `npm run build`, the whole `src/test` folder, and the Python tests that read `index.html`.
5. **Last:** flip the route to `kind: 'react'`, delete `settings.js` and `video-settings.js`, remove their `<script>` tags.

## Status

| Step | State | Notes |
|---|---|---|
| Branch and worktree | done | `node_modules` is a Windows junction to the main checkout's. Tests run in the worktree. |
| Inventory of every control | done | Generated from `index.html`; the tables below. Regenerate with the script in `scrap/settingsx` in the main checkout if the markup changes on dev. |
| Scaffold (host, store, save status) | **done** (step 1 below) | The page looks and behaves exactly as before: no tab is registered, so nothing mounts. |
| Tab: Connections | not started | 160 controls and buttons |
| Tab: Sources | not started | 80 controls and buttons |
| Tab: Downloads | **done except the chain** (step 5 below) | 19 of 32 ported: Download Behaviour, Retry Logic, Album Publishing, Filtering. Left: Source Settings (the download chain, 4 controls), which moves with the Sources tab; the video slskd form (9, video only), for the video pass. Checked in a browser: identical apart from one listed quirk. |
| Tab: Quality | **done for music** (step 6 below) | 27 of 67 ported: Music Quality (the ranked target list, presets, upgrade, search, fallback), Quality on Import, Album Releases, Post-Download Conversion, Audio Verification, the Quality Profiles panel and its Manage overview. Left: Video Quality, Custom Formats, YouTube Quality (40, video only), for the video pass. Checked in a browser: identical, 0 pixels differ. |
| Tab: Library | not started | 236 controls and buttons |
| Tab: Discovery | **done for music** (step 4 below) | 11 of 12 controls ported. Left: Import Lists (video only, `video-settings.js`), for the video pass. Checked in a browser: identical, 0 pixels differ. |
| Tab: Appearance | **done** (step 2 below) | 14 of 14 controls ported. Checked in a browser against the old page: pixel-identical. |
| Tab: System | not started | 63 controls and buttons |
| Tab: Logs | **done** (step 3 below) | 10 of 10 controls ported. Checked in a browser against the old page: same states, same requests, screenshots differ by 1 pixel (the help button dot). |
| Video-only panels | not started | counted inside the tabs above |
| Flip route to React, delete the vanilla files | not started | |

**Progress:** 5 of 9 tabs (Discovery and Quality without their video-only sections, Downloads without the chain and its video-only form), 81 of 678 controls, 76 of 333 `settings.js` functions removed, 14 of 83 tests repointed (`settings-regroup-1822`, `test_docs_settings_labels`, `test_settings_sources_tab`, `music-size-limit-settings`, `test_download_chain_widget`, `test_explicit_preference`, `test_preferred_version`, `test_source_search_timeout`, `test_video_download_config`, `test_quality_profile_preview_nudge`, `quality/test_lossless`, `quality-release-import-mode`, `settings-autosave-toggle`, `settings-load-element-drift`).

| File | At the start | Now |
|---|---|---|
| `webui/index.html` | 12,823 | 11,411 |
| `webui/static/settings.js` | 10,187 | 8,226 |
| `webui/static/init.js` | 5,264 | 5,209 |

## Next: step 7, Sources and the download chain (plan, Nov 15 2026)

Read before starting: `settings.js` lines ~1750-3165 (the chain, the tiles, the modal, status and Test all, the legacy hybrid list, `loadHybridSourceOrder`), the per-source functions (~5780-7740: Tidal, Qobuz, Deezer, HiFi instances, Lidarr, Prowlarr, the torrent and usenet clients, slskd auto-detect, yt-dlp), and the Sources markup (`index.html` ~4040-5167: eleven `src-config-panel`s, `#source-config-home`, `#source-config-modal`).

**Why the chain and the Sources tab go together.** They share one set of module state in `settings.js`: `_hybridSourceOrder` / `_hybridSourceEnabled` (the music chain), `_hybridSourceUnready` (from `settings._source_status`), `_hybridSourceStatus` / `_ssLastTestMessage` / `_ssLastTestWarned` (the dots and the last test's words), `_videoChainOrder` (read from `/api/video/downloads/config`). The tiles show where each source sits in every chain and its dot; the chain writes the order the tiles read; Test all (Downloads tab, React since step 5, calls `window.testAllSources`) writes the dots both show. Porting one half would leave React and vanilla writing each other's state.

**Why the modal and every panel go together.** `openSourceModal` MOVES a panel's DOM node into the modal body and `closeSourceModal` moves it back to `#source-config-home`. React cannot share nodes that vanilla code moves, so the modal, the tiles and all eleven panels are rendered by React at once.

**Seams that stay vanilla until their own step**

| What | Owner until | Seam |
|---|---|---|
| The audiobook chain (`_audiobookHybrid`, `#audiobook-download-mode`, `#audiobook-hybrid-rows`) | Library (step 8) | `settings.js` exposes `getAudiobookChain()` / `setAudiobookChain(order)` and dispatches a `soulsync:audiobook-chain` event when its rows change; the React chain's Audiobooks tab reads and writes through them, and the tiles listen. |
| Video ids inside the torrent and usenet panels: `video-redownload-failed`, `video-seed-idx-refresh`, `video-seed-idx-rows`, `video-seed-mode`, `video-seed-remove-data`, `video-usenet-remove-completed` | the video pass | React renders these elements with the same ids and never touches their children or values after mount; `video-settings.js` keeps filling and saving them. |
| `helper.js` popovers keyed by selector | n/a | matched at click time, nothing to do (checked in step 6). |

**Order inside the step** (each a commit, each with parity, behaviour tests and a browser comparison):

1. **7a, the sources model**: a store (`tabs/sources-model.ts`) for the music order and enabled set, unready, status, last messages, the video order; `loadHybridSourceOrder`'s rules (retired sources dropped, the legacy primary/secondary fallback, unready kept enabled); Test all and the per-source probes (`HYBRID_SOURCE_PROBE`, `_ssTestConn`, the Prowlarr pre-probe, the video and audiobook sources, the connections sweep, the toast). `window.testAllSources` and `window.autoTestSourcesOnce` keep working, now calling the model.
2. **7b, the chain** (Downloads > Source Settings): the widget (`#download-chain-widget`: kind tabs, pool groups with filter, steps with ▲ ▼ ×, the end slot, drag between pool and chain), its three kinds, the music save keys (`download_source.mode`, `hybrid_primary`, `hybrid_secondary`, `hybrid_order`: the hidden selects go), Download Behaviour hidden under the video and audiobook chains.
3. **7c, the Sources tab**: the tiles (In use / Available / Indexers), the modal (title, hint, icon, dot, Test with its result, Escape, focus the first field, close lands a pending save), the filesystem-access hint, and the eleven panels with every field's load and save key, auth flows (Tidal device auth, Qobuz login and token, Deezer ARL test, HiFi instance list with drag, Lidarr, Prowlarr indexers, torrent and usenet client tests, slskd auto-detect, YouTube cookies with the Windows App-Bound warning, yt-dlp status and update), the torrent stall keys the collector reads from the torrent panel.

## Completed steps

Each finished step, in order, with exactly what was added, changed and checked.

### Step 1: the scaffold (Nov 14 2026)

**What it is.** The pieces every ported tab will use, with no tab in them yet. The Settings page is unchanged for the person using it.

**The problem it solves.** The page has one save: `saveSettings()` in `settings.js` reads every control on the page by id and posts the lot. A tab rendered by React has no vanilla controls for that function to read, and `loadSettingsData()` fills controls by setting `.value` on DOM nodes, which a React input ignores. So a ported tab needs its own copy of the settings and its own save, and the two saves must not overwrite each other.

**Why two saves are safe.** Checked in `web_server.py` (the `/api/settings` POST): for each known section present in the body it calls `config_manager.set(f'{section}.{key}', value)` per key. Sections that are absent are skipped, and keys that are absent inside a section are left alone. So the React store posts only the keys that changed, and the vanilla save posts what it still owns.

**Files added**

| File | What it does |
|---|---|
| `webui/src/features/settings/settings-store.ts` | The settings a React tab reads and changes. `createSettingsStore()` and the page's one instance, `settingsStore`. |
| `webui/src/features/settings/host.tsx` | `window.SoulSyncSettings`, the door between `settings.js` and the React tabs. Holds the registry `SETTINGS_TABS` (empty for now). |
| `webui/src/features/settings/settings-store.test.ts` | 10 tests for the store. |
| `webui/src/features/settings/host.test.tsx` | 4 tests for the host. |
| `webui/src/test/settings-react-host-hooks.test.ts` | 3 tests for the hooks in `settings.js`. |

**Files changed**

| File | Change |
|---|---|
| `webui/src/app/main.tsx` | Imports and calls `mountSettingsHost()` beside the other hosts. |
| `webui/static/settings.js` | Three hooks, listed below. Nothing removed. |

**The store (`settings-store.ts`)**

- State: `settings` (the server's payload with this session's edits on top), `loaded`, `pending` (changed keys not saved yet, by section), `error`.
- `hydrate(payload)`: takes what `GET /api/settings` returned. An error body is ignored. Edits not saved yet stay on top of the new payload.
- `set(section, key, value)` / `patch(section, values)`: a change by the person. It updates `settings` and `pending`, shows the save button as pending, and queues a save after `AUTOSAVE_DELAY_MS` (2000 ms, the same wait `debouncedAutoSaveSettings` uses).
- The queued save is **not** started when `window._settingsAutosaveOff` is true (the System > Saving switch), when `window._settingsLoadFailed` is true (#879), or while `window._suppressSettingsAutoSave` is true (the form is being filled on open).
- `flush()`: posts `pending` to `/api/settings` now. One save at a time: a change made while a save is in the air goes out in the next one. On failure the keys go back into `pending` (under anything changed since) and the button shows the error state. Returns false without posting when the settings load failed.
- After a good save it merges the saved keys into `window._settingsPayload`, the copy `settings.js` and `shared-helpers.js` read.
- The save status button is still the vanilla one during the port: the store calls `window.setSettingsSaveState(state)`.

**The host (`host.tsx`)**

- A ported tab's markup will be replaced in `index.html` by one empty slot: `<div data-stg="<tab>" data-stg-react="<tab>">`. `switchSettingsTab` already shows and hides every `[data-stg]` block, so the slot follows its tab with no extra code.
- `window.SoulSyncSettings.show(tab)`: if the tab is in `SETTINGS_TABS`, renders it into its slot. One React root per slot for the life of the page, so a tab keeps its state across tab switches. A tab that is not registered, or has no slot, is a no-op.
- `hydrate(settings)`, `flush()`, `hasPending()` pass through to the store. `ported()` lists the registered tabs.
- The React bundle is a deferred module, so `settings.js` can have loaded the settings and opened a tab before it runs. `mountSettingsHost()` catches up: it hydrates from `window._settingsPayload` if present and shows the tab whose button is active.

**The three hooks in `settings.js`**

| Function | Hook | Why |
|---|---|---|
| `switchSettingsTab(tab)` | After the `[data-stg]` show/hide loop: `window.SoulSyncSettings.show(tab)` inside a try/catch. | Opens the React tab. A mount that throws must not break the tab switch. |
| `loadSettingsData()` | Right after `window._settingsPayload = settings`: `window.SoulSyncSettings.hydrate(settings)`. | One fetch feeds both. It sits after the #879 failed-load guard, so an error body never reaches the store. |
| `saveSettings(quiet)` | After `saveQualityProfile()`: `reactSaved = await window.SoulSyncSettings.flush()` in a try/catch. The outcome ladder starts with a new branch: `if (result.success && !reactSaved)` → error state and the toast "Some settings failed to save". | The save button (and the vanilla autosave) reaches the React tabs. This is how a React tab's changes are saved when autosave is off. A React failure must win over "saved". |

**Checked**

| Check | Result |
|---|---|
| New tests (`src/features/settings`, `settings-react-host-hooks.test.ts`) | 20 pass |
| Whole `src/test` folder | 109 files, 1,207 tests pass |
| `tsc --noEmit` | clean |
| `oxlint` on the new and changed files | 0 errors. 2 warnings, both in code that was already in `main.tsx` |
| `oxfmt` on the files touched | applied |
| `npm run build` | builds; `SoulSyncSettings` is in the main bundle |

**Not checked:** in a browser. Nothing visible changed, and no tab uses the scaffold yet; the first real exercise of it is the Appearance tab (step 2).

**What the next step can rely on**

- To port a tab: write its component, add it to `SETTINGS_TABS`, replace its markup with the slot, and delete its fields from `loadSettingsData` and the `saveSettings` collector.
- Read a value with the store's `settings[section][key]`; change it with `store.set(section, key, value)`. Saving, the autosave switch, the load guards and the save button are handled.

### Step 2: the Appearance tab (Nov 15 2026)

**What it is.** The first tab that React renders. Everything on Appearance: Show only what you use, UI Appearance (accent colour, sidebar visualizer, the four effect switches) and the Sidebar Weather tile. It looks and behaves as before.

**Why Appearance first.** It is small (14 controls), its settings are in one section (`ui_appearance`) plus the weather endpoints, and it has the trickiest per-device state on the page (the effect switches), so it tests the scaffold properly.

**Where the markup was.** Two places in `index.html`, because the page lays its blocks out in columns: the two `settings-group` blocks at the top of the third column (89 lines) and the Sidebar Weather tile further down (73 lines). Each run was replaced by one slot:

```html
<div data-stg-react="appearance" data-stg-part="ui" style="display: contents"></div>
<div data-stg-react="appearance" data-stg-part="weather" style="display: contents"></div>
```

`display: contents` means the slot draws no box of its own, so the blocks React puts in it sit in the column exactly as before. The removed markup is kept verbatim as test fixtures: `webui/src/features/settings/tabs/__fixtures__/appearance-ui.vanilla.html` and `appearance-weather.vanilla.html`.

**Files added**

| File | What it does |
|---|---|
| `webui/src/features/settings/tabs/appearance.ts` | The logic, no React: the option lists, `resolveAppearance()` (saved settings to what shows, the way `loadSettingsData` did it), `applyAppearanceOnLoad()`, `effectFlags()`, `savedEffectValue()`. |
| `webui/src/features/settings/tabs/appearance-tab.tsx` | The tab: `AppearanceTab` renders the `ui` or `weather` part. Same tags, ids, classes, labels and help text as the old markup. |
| `webui/src/features/settings/tabs/appearance.test.tsx` | 18 tests, described under Checked. |
| the two fixtures above | The old markup, frozen. |

**Files changed**

| File | Change |
|---|---|
| `webui/index.html` | The two runs of Appearance markup replaced by the two slots (−162 lines). |
| `webui/static/settings.js` | Removed: the UI Appearance block of `loadSettingsData` (70 lines), the appearance keys of the `saveSettings` collector (only `settings_autosave` is left in `ui_appearance`), the whole SIDEBAR WEATHER section (8 functions, 145 lines), the weather load in `switchSettingsTab`, and the `initAccentColorListeners()` call in `initializeSettings`. Moved: the `SoulSyncSettings.show(tab)` hook now runs **before** the show/hide loop. Added: the autosave guard (below). |
| `webui/static/init.js` | Removed `initAccentColorListeners()` (56 lines). The appliers it called (`applyAccentColor`, `applyParticlesSetting`, `applyWorkerOrbsSetting`, `applyReduceEffects`, `applyMaxPerformance`) stay: the dashboard's quick settings strip uses them too. |
| `webui/static/media-player.js` | Added `setSidebarVisualizerType(type)`. `sidebarVisualizerType` is a top-level `let`, which another script cannot assign; settings.js used to assign it directly. |
| `webui/src/shell/weather-preview-settings.ts` | Removed `wireHolidaysToggle()`: the holiday decorations switch is the React tab's now. |
| `webui/src/features/settings/host.tsx` | Extended, see below. |
| `webui/src/test/settings-regroup-1822.test.ts` | Repointed, see below. |
| `webui/src/features/settings/host.test.tsx`, `webui/src/test/settings-react-host-hooks.test.ts` | New tests for the host changes and the autosave guard. |

**Host changes made for this tab**

- **Several slots per tab.** A tab gets one slot per run of its markup, told apart by `data-stg-part`, passed to the component as `part`.
- **Every ported tab is on the page.** `show(tab)` mounts the slots of **all** ported tabs on first call, not just the opened one. The settings search (`filterSettings`) reads the text of every tab's blocks, so a tab that only appeared when opened would vanish from search. A slot that is already mounted re-renders only when its own tab is opened.
- **The first paint is synchronous** (`flushSync`), so the blocks exist before `switchSettingsTab` goes on to hide the other tabs' blocks and any column left empty. That is also why the hook moved before the show/hide loop.
- **`opened` counter.** Each time a tab is opened its count goes up and the tab gets it as a prop. The weather tile reloads on each opening, as the old page did, and not before the first one.
- **Load effects.** `SETTINGS_LOAD_EFFECTS` run on every good `hydrate`, whatever tab is open. Opening Settings has always applied the saved accent, visualizer and effect switches; that code moved out of `loadSettingsData` into `applyAppearanceOnLoad`. One effect throwing does not stop the next.
- **Catch-up.** When the bundle runs after settings.js has opened a tab, it now goes through `window.switchSettingsTab(open)`, so the show/hide pass runs after the mount.

**How each control works now**

| Control | Before | Now |
|---|---|---|
| Accent Color (`#accent-preset`) | `initAccentColorListeners` applied it live; `saveSettings` read the select | `onChange` calls `applyAccentColor` and saves `ui_appearance.accent_preset`. "Custom" shows `#custom-color-group` and applies the custom colour. |
| Custom Color (`#accent-custom-color`) | same | `onChange` applies it and saves `ui_appearance.accent_color` |
| Sidebar Visualizer | `media-player.js` listens for `change` on `document`; `saveSettings` read the select | Saves `ui_appearance.sidebar_visualizer`. React's change event still reaches `media-player.js`'s document listener (tested). |
| Background Particles, Worker Orbs, Reduce Visual Effects, Max Performance | `initAccentColorListeners` called the appliers; `saveSettings` read the window flags | `onChange` calls the same applier, then saves what the flag says (`savedEffectValue`). Checked state is read from the flags, so Max Performance locking the other three (off, disabled, `setting-overridden`) and giving them back works as before. |
| Customize SoulSync | inline `onclick` | `onClick` calls `window.openFeaturePicker` |
| Sidebar Weather: Location, Save, Clear, Show weather, °C | inline handlers calling the SIDEBAR WEATHER functions | Same requests (`/api/weather`, `PUT /location`, `/display`, `/units`), same toasts, same status line, same sidebar re-boot. |
| Holiday decorations | `wireHolidaysToggle` in the shell bundle | Same request (`PUT /api/weather/holidays`), state from the same `GET /api/weather`. |

**Differences from the old page, all deliberate**

| What | Before | Now | Why |
|---|---|---|---|
| Saves per change | One full-page save (every section). | One save with only the keys that changed. | The point of the store. The server stores per key, so the result is the same. |
| A React control and the page autosave | (no React controls) | `debouncedAutoSaveSettings` returns early for any control inside `[data-stg-react]`. | `initializeSettings` wires every input it finds, and the React controls are already on the page then. Without the guard one change sent two saves (found in the browser check). Side effect: typing a weather location no longer sends a full-page save of everything else, which it did before for no reason. |
| When weather loads | When Appearance is opened. | When Appearance is opened. | Same. (A first version loaded it whenever Settings opened; fixed before commit.) |

**Checked**

| Check | Result |
|---|---|
| Parity: the React tab against the frozen old markup | Same elements in the same order, same ids, classes and the attributes the CSS and scripts use (`data-stg`, `type`, `placeholder`, `for`, `role`, `tabindex`), same text. 2 tests. |
| Behaviour tests (`appearance.test.tsx`) | 16 more: resolving saved values (Custom for an unknown colour, the server's orb default, this device's choice winning), the load order with Max Performance last, preset/custom/visualizer/effect changes and what each saves, the Max Performance lock and release, the feature picker, the weather status line in its 5 states, loading only once opened and again on each opening, the tile opening and closing, Save/empty Save/Clear/the three switches and their requests and toasts. |
| Host tests | 7 (3 new: several slots and every tab on the page, load effects with a failing one, catch-up). |
| Autosave guard | 1 new test. **Negative-checked:** with the guard removed the test fails, with it restored it passes, and the restore was confirmed by grep. |
| Repointed test | `src/test/settings-regroup-1822.test.ts`: "Sidebar Weather is on appearance" dropped (the heading is rendered by React now, covered by the parity test); "no tab left empty" counts `[data-stg-react]` slots too; the loaders test now checks Appearance is handed to `SoulSyncSettings.show`. |
| Whole frontend suite (`vitest run src`, 729 files, 12,271 tests) | 12,250 passed, 21 failed, **none in Settings code**. 16 were 15-second timeouts from running every file at once: they pass when their files are rerun. The other 5, all library page tests (`art-picker-modal`, `artist-settings`, `history-modal`, `reorganize-modal`, `retag-modal`), are **pre-existing**: the same 15 files on dev (`52f2190d7`, no port) fail 6, four of them the same tests. The fifth, `art-picker-modal`, passed on dev in that run but passes on the branch run on its own, twice. Not touched here; listed under Known traps. |
| `tsc --noEmit` | clean |
| `oxlint` on the files touched | 0 errors, 0 warnings in new code (2 pre-existing warnings in `settings-regroup-1822.test.ts` for `new Function`) |
| Python tests that read `index.html` or the vanilla scripts (107 files) | 2,544 passed, 1 skipped |
| `npm run build` | builds |
| **Browser, old page vs ported page**, real `index.html` and real bundle in two sandboxes (`scrap/stgsbx/launch.py` in the main checkout, ports 8791 and 8792, stubbed settings and weather, every POST logged) | Appearance closed: the settings area is **pixel-identical** (all differing pixels are in the animated sidebar). Weather tile open: identical except 26 pixels where the floating help button's dot pulses. Same values shown, same accent applied (`139, 92, 246`), the tab's blocks hidden on System, search for "holiday" lists "Appearance — Sidebar Weather" on both. |
| Browser, weather requests | `GET /api/weather` counted on both pages at boot, on opening Settings, on opening Appearance and on reopening it: **identical counts** on the old page and the ported one. |
| Browser, saving | Two changes inside the wait: **one** save, `{"ui_appearance": {"particles_enabled": true, "sidebar_visualizer": "spectrum"}}`. Autosave off: no save after a change, the button read "Save changes", clicking it sent the change. |

**Not checked:** on a real server with real settings. Only in the sandbox.

**Learned for the next tabs**

- Put **every** ported tab on the page at once, or it drops out of the settings search.
- After a tab is ported, look for its controls in `initializeSettings`' wiring and in the shell bundle (`src/shell`), not only in `settings.js`.
- A vanilla `let` can't be set from React: expose a setter on the script that owns it.
- Run the browser before/after check with every POST logged. The double save was invisible to every unit test.

### Step 3: the Logs tab (Nov 15 2026)

**What it is.** The live log viewer: pick a log, filter by level, search, copy, clear, auto-scroll. Nothing on it is a saved setting, so it never touches the store. It looks and behaves as before.

**Where the markup was.** One `settings-group` at the end of the settings pane (47 lines), replaced by `<div data-stg-react="logs" style="display: contents"></div>`. The removed markup is frozen as `webui/src/features/settings/tabs/__fixtures__/logs.vanilla.html`.

**How the viewer worked, and how it works now**

| Behaviour | Before (`settings.js`) | Now (`logs-tab.tsx`) |
|---|---|---|
| Open the tab | `switchSettingsTab` called `_logViewerInit()`: `GET /api/logs/tail?source=…&lines=300` (+ `level`, `search` when set), then `socket.emit('logs:subscribe', {source})` and `socket.on('logs:live', …)` if the socket was connected. A second init while live did nothing. | Same request and same subscription, started when the host says the tab is `active`. A second start while live does nothing. |
| Leave the tab | `switchSettingsTab` called `_logViewerStop()`: `socket.off('logs:live')`, `emit('logs:unsubscribe')`. | Same, when `active` goes false. Also on unmount. |
| Live lines | Dropped if another source, filtered client-side by level and search, appended. | Same (`keepLiveLines`). |
| Lines shown | DOM nodes appended by hand, oldest removed past 2,000, class from `_logClassify`. | A list of rows in state, cut to the last 2,000, same classes (`classifyLogLine`, `logLevelClass`). Each line is a memoised component, so a live push only adds new elements. |
| Level buttons | Moved `active`, reloaded. | Same. |
| Search | 300 ms after typing stops, reloaded. | Same. |
| Source | Stop, "Loading...", start. | Same. |
| Reload | Stop, one "Loading..." line, start. The line count keeps its old value until lines arrive. | Same, including the count. |
| Copy | Clipboard API on a secure page, else a hidden textarea; toast "Logs copied". | Same. |
| Clear | Empties the view, "0 lines". | Same. |
| Auto-scroll | After lines were appended, if ticked. | Same: after lines arrive, not after Loading or Clear. |
| A failed tail | Warning in the console, message left up, still subscribes. | Same. |

**The socket bridge.** `socket` in `core.js` is a script-scoped `let`, which a module can't reach, so `core.js` now lends it through `window.soulsyncLogStream`:

- `subscribe(source, onLive)`: if the socket is connected, emits `logs:subscribe` and listens for `logs:live`; returns false otherwise.
- `unsubscribe(onLive)`: removes the listener and emits `logs:unsubscribe`.

Exactly the four socket calls the vanilla viewer made.

**Host change: `active`.** Every mounted tab is now re-rendered on each tab switch with `active` (is it the open tab?). Before, only the opened tab re-rendered, so a tab had no way to know it had been left. Re-rendering the others is cheap: they get the same props apart from `active`.

**Files added**

| File | What it does |
|---|---|
| `webui/src/features/settings/tabs/logs.ts` | Sources, levels, limits, `classifyLogLine`, `logLevelClass`, `keepLiveLines`, the bridge's type. |
| `webui/src/features/settings/tabs/logs-tab.tsx` | The tab. |
| `webui/src/features/settings/tabs/logs.test.tsx` | 12 tests, below. |
| `webui/src/features/settings/tabs/__fixtures__/logs.vanilla.html` | The old markup, frozen. |
| `webui/src/features/settings/tabs/__fixtures__/shape.ts` | The markup comparison, shared by every tab's parity test (moved out of the Appearance test; it now also compares `data-level` and `title`). |

**Files changed**

| File | Change |
|---|---|
| `webui/index.html` | The Logs block replaced by its slot (−47 lines, +3). |
| `webui/static/settings.js` | Removed the whole Live Log Viewer section: the 4 `_logViewer*` lets, `_LOG_MAX_LINES`, `_logSearchDebounce` and 12 functions (166 lines). Removed the init/stop hook from `switchSettingsTab`. |
| `webui/static/core.js` | Added `window.soulsyncLogStream` beside the `socket` declaration. |
| `webui/src/features/settings/host.tsx` | `active` prop; registers `logs`. |
| `webui/src/features/settings/tabs/appearance.test.tsx` | Uses the shared helper and passes `active`. |
| `webui/src/test/settings-react-host-hooks.test.ts` | 2 tests for the `core.js` bridge. |

**Left as it was:** the page autosave's guard for the Logs tab (`settings.js` line ~157, "never auto-save while the Logs tab is active", #827). It is harmless and goes when the page autosave goes.

**Checked**

| Check | Result |
|---|---|
| Parity with the frozen markup | Same elements, ids, classes, `data-level`, titles, placeholder and text. |
| Behaviour tests (`logs.test.tsx`) | 11 more: level reading, waiting to be opened then tail and live, own-source live lines and stopping on leave (a late frame dropped, no stacked subscription on reopening), level reload and live filtering, search waiting 300 ms, source change, the 2,000-line cap, no socket, a failed tail, Copy and Clear, auto-scroll on and off, unmount stops the stream. |
| The `core.js` bridge | 2 tests with a fake socket: the four calls in order, and nothing without a connected socket. |
| `src/features/settings` + `src/test` | 47 + 1,207 tests pass. |
| `tsc --noEmit` / `oxlint` | clean / 0 warnings, 0 errors |
| `npm run build` | builds |
| Python tests that read the settings markup or the vanilla scripts (67 files) | 1,662 passed |
| **Browser, old page vs ported page** (the sandbox now stubs `/api/logs/tail` and logs each request) | Opened the tab, picked Error, searched "line 3", switched to acoustid.log, cleared, went to Appearance. Every state (lines with their classes, the count, the active level, visibility) and every tail request was **identical** on both pages. Screenshot: 1 pixel differs, the help button's dot. |

**Not checked in a browser:** live lines. The sandbox has no socket server, on either page. Both halves are unit-tested (the tab against a fake bridge, the bridge against a fake socket), and the bridge makes the same four calls the old code made.

### Step 4: the Discovery tab, music side (Nov 15 2026)

**What it is.** The "Discovery & playlists" group, Playlists (M3U export, whole-library M3U, sync mode, wishlist missing tracks, backup copy), Listening Stats, the Discovery dial, and Discovery Pool Settings (lookback, hemisphere, music only). **Import Lists**, the tab's video-only section, is still vanilla: `video-settings.js` owns it and it moves in the video pass.

**Where the markup was.** Two runs: the group and three sections in the second column (180 lines), and Discovery Pool Settings in the third column (49 lines). Replaced by slots `discovery`/`main` and `discovery`/`pool`. Frozen as `__fixtures__/discovery-main.vanilla.html` and `discovery-pool.vanilla.html`.

**New shared pieces (`tabs/parts.tsx`), for every tab from here on.** This tab is the first with the page's two DOM-rewriting mechanisms, and React can't share nodes with code that rewrites them, so the React tab renders the finished result and owns the behaviour:

| Mechanism | What `settings.js` did | What `parts.tsx` does |
|---|---|---|
| Section summaries (`data-stg-summary`) | `refreshLibrarySummaries()` rewrote the header hint with a live summary on every change, falling back to `data-stg-fallback`, and toggled `is-live`. | `Section` renders the summary it is given (or the fallback) and the class. The three summaries this tab had (`library-playlists`, `library-stats`, `library-discovery`) were taken out of `LIBRARY_SUMMARIES`, so `settings.js` never touches React's text. |
| Help lines (#1822) | `initSettingHelpLines()` added a `<small class="stg-help-line">` with the help's first sentence to each form-group, and moved the "i" into it when there was more to read, else hid it. | `helpBits()` renders the same `<small>` and puts the "i" in the same place (`helpFirstLine` is `settingHelpFirstLine`, ported). React icons have no inline `onclick`, so `initSettingHelpLines` (which matches `[onclick*="toggleSettingHelp"]`) never picks them up. |
| The "i" (`toggleSettingHelp`) | Toggled the help body's `hidden` and the icon's `open` class. | `useHelp()`: the body's `hidden` and the icon's `open` follow React state. Enter and Space work as before. |
| Collapsible tiles | inline `onclick` | `toggleSection` (moved here from the Appearance tab). |

Also `useDraft()`: a number field shows what is being typed and saves what it parses to (Poll Interval: emptied, it shows empty and saves 30, as the collector's `parseInt(...) || 30` did).

**Store changes**

- **Values with their own endpoint** (`setExtra(key, value, save)`). Lookback and hemisphere are not `/api/settings` keys; `saveSettings` posted them to `/api/discovery/lookback-period` and `/api/discovery/hemisphere` on every page save. Now a change queues its own saver under the same rules as any other change (the 2 s wait, autosave off, the load guards), and `flush()` runs it. A failed saver stays pending and the save shows the error state. A saver that only warned keeps doing so: a failed lookback still shows "Settings saved, but discovery lookback period failed to save"; a failed hemisphere is still only logged.
- **`loads` counter.** Goes up with each good payload. The Pool part re-reads lookback and hemisphere on each, as `loadSettingsData` did on every visit to Settings.

**Removed from `settings.js`**

| What | Lines |
|---|---|
| The three summaries from `LIBRARY_SUMMARIES` | 17 |
| `loadSettingsData`: playlist sync (3 controls), listening stats and the dial, M3U export and whole-library M3U | 9 + 10 + 2 + 4 |
| `loadSettingsData`: the lookback and hemisphere fetches | 22 |
| Collector: `playlist_sync`, `listening_stats`, `discover`, and three keys of `m3u_export` (`enabled`, `library_enabled`, `library_path`; the other four keys belong to the Library tab and stay) | 18 |
| `saveSettings`: the lookback and hemisphere posts, and the `lookbackSaved` branch of the outcome ladder | 38 |

**Bug found, ported as it was, not fixed.** The Discovery section's summary compares the dial (0 to 1) with 25 and 60, so it always reads "plays it safe". Same on dev (seen in the browser at 0.60 on both pages). A one-line fix (`n <= 0.25 … n <= 0.6`) for Boulder to decide; a 1:1 port does not change it.

**Differences from the old page, all deliberate**

| What | Before | Now |
|---|---|---|
| Saving lookback and hemisphere | Posted with every page save, changed or not. | Posted when changed. |
| What a change saves | Every value on the page. | Only the keys that changed. |

**Checked**

| Check | Result |
|---|---|
| Parity with the frozen markup **after** `settings.js`'s own `initSettingHelpLines` has run on it (the shared helper now runs the real function) | Same elements, ids, classes, `hidden`, `min`/`max`/`step`, `data-stg-summary`/`fallback`, `data-music-only`, help lines and text, both parts. The first try caught two help bodies the markup keeps inside their form-group. |
| Behaviour tests (`discovery.test.tsx`) | 11 more: resolving saved values, summaries (the bug included), fixed hints until load then live, the first-sentence cut, every control saving only its key, the emptied interval, the "i" opening only its own help (click and Enter), the Pool loading with each settings load and not before, the Pool saving through its own endpoints under autosave-off, the lookback failure warning. |
| Repointed | `settings-regroup-1822.test.ts` (four "is on discovery" rows now covered by the parity test; Import Lists stays), `settings-react-host-hooks.test.ts` (the new outcome ladder), and two Python tests: `tests/test_docs_settings_labels.py` (the docs-named labels are looked for in the React tabs too; three had moved there) and `tests/test_settings_sources_tab.py::test_library_headers_report_what_is_set_not_what_is_inside` (counts React summaries, checks each has its summary and the shared fallback, and that none is on the page twice). |
| Python tests that read the settings markup, scripts or these controls (90 files) | First run: 2,403 passed, 4 failed, all four the two tests above reading moved labels and summaries. After repointing them: 151 of 151 in those two files pass. |
| `src/features/settings` + `src/test` | 59 + 1,209 pass |
| `tsc --noEmit` / `oxlint` / build | clean / 0 warnings / builds |
| **Browser, old page vs ported page** (sandbox stubs `/api/discovery/*` and logs posts; settings with Discovery values) | Values, summaries (live), section headers, help lines, two opened helps, search for "hemisphere": **identical**. All four sections open: **0 pixels differ**. Changing four settings: the old page sent a full save, lookback and hemisphere (3 requests); the ported page sent one settings save with exactly the four changed keys, and the lookback (2 requests). Same resulting values. |

### Step 5: the Downloads tab, without the download chain (Nov 15 2026)

**What it is.** Download Behaviour (preview source, concurrency, search timeout, Test all sources), Retry Logic, Album Publishing, and Filtering (explicit content, the size cap, preferred version, the genre whitelist). It looks and behaves as before, with one listed exception.

**What is not in it, and why.**

- **Source Settings (the download chain).** One widget edits three chains (music in `/api/settings`, video in `/api/video/downloads/config`, audiobooks via the Library tab's controls), and its writes feed the Sources tab: the source tiles, the hidden `download-source-mode` select that `updateDownloadSourceUI` reads, the legacy hybrid list whose status probing the tiles use. Porting it alone would have React feeding vanilla code it does not own, so it moves together with the Sources tab, its other half.
- **The video slskd form** (`#video-slskd-container`, 9 fields): video only, for the video pass.

**Where the markup was.** Three runs, replaced by three slots:

| Slot | What | Lines | Fixture |
|---|---|---|---|
| `downloads`/`behaviour` | Download Behaviour. The slot sits **inside** the vanilla `<div id="dlchain-behaviour">` wrapper, which stays: the chain hides it under the video and audiobook chains, and keeps hiding its own node. | 90 | `downloads-behaviour.vanilla.html` |
| `downloads`/`retry` | Retry Logic and Album Publishing | 170 | `downloads-retry.vanilla.html` |
| `downloads`/`filtering` | Filtering | 139 | `downloads-filtering.vanilla.html` |

**How each control works now**

| Control | Saves |
|---|---|
| Stream / Preview Source | `download_source.stream_source` |
| Concurrent Downloads | `download_source.max_concurrent` (`parseInt \|\| 3`, as before) |
| Source search timeout | `download_source.source_search_timeout` (`_cfgInt(…, 0)`: blank saves 0) |
| Test all sources | calls `window.testAllSources()`, the Sources tab's probe (still vanilla) |
| Retry next-best / Exhaustive / Accept version mismatch | `post_processing.retry_next_candidate_on_mismatch` / `retry_exhaustive` / `accept_version_mismatch_fallback` |
| Retries per query / Minimum matching mismatches | `post_processing.retries_per_query` (`max(1, parseInt \|\| 5)`) / `version_mismatch_min_count` (`max(1, parseInt \|\| 2)`). Each row shows only while its switch is on (was `syncRetryConditionalRows`). |
| Publish albums only when complete / Recover interrupted albums | `album_downloads.atomic_publish` / `atomic_recover_orphans` |
| Allow explicit / Prefer explicit / Never download clean | `content_filter.*`. The two children grey out (disabled, label at 0.45 opacity) while explicit is blocked (was `syncPreferExplicitState`). |
| Maximum music download size | `download_source.max_mb_per_minute` (`max(0, _cfgFloat(…, 0))`) |
| Prefer a version | `soulseek.preferred_version` |
| Enable strict genre filtering, the genre list | `genre_whitelist.enabled`, `genre_whitelist.genres`. Enter adds (case-blind, sorted), × removes, typing filters, Reset to Defaults loads `/api/genre-whitelist/defaults` with its toast. Switching it on with an empty list loads the defaults. |

The Filtering header's live summary (`library-filtering`) is rendered by the tab and was taken out of `LIBRARY_SUMMARIES`.

**Shared change (`parts.tsx`).** `helpBits` now takes the help body as markup and reads its first sentence with `textOf`, so the long formatted help texts (bold, italics, code, line breaks) exist once, as the body. Two more help shapes are handled by the components here: the help inside the form-group (`Field`) and the `setting-row` (`Row`).

**Removed from `settings.js`**

| What | Lines |
|---|---|
| `syncPreferExplicitState`, `syncRetryConditionalRows` and their `window` exports | 31 |
| `library-filtering` from `LIBRARY_SUMMARIES` | 8 |
| `loadSettingsData`: preview source, concurrency, search timeout; the five retry lines and their sync call; Album Publishing; the whole Filtering block (explicit, preferred version, size cap, genre whitelist) | 5 + 6 + 6 + 19 |
| Collector: `soulseek.preferred_version`; four `download_source` keys (`mode`, `hybrid_*` stay: the chain); `content_filter`; `genre_whitelist`; the five retry keys of `post_processing` (the rest stay: Library and Quality); `album_downloads` | 32 |
| The whole Genre Whitelist section: the cache, 6 functions, 3 document listeners | 93 |

**Differences from the old page, all deliberate**

| What | Before | Now | Why |
|---|---|---|---|
| **Genre whitelist off at load** | The page never read the saved genres, so its list stayed empty, **and every later full-page save posted `genres: []`**: a saved list was wiped by any unrelated change while the whitelist was off. Turning it on then loaded the defaults. | The saved genres are read always and saved only when they change. Turning it on keeps a saved list, and loads the defaults only when there is none. | A data-loss bug on dev. A save of only the changed keys cannot reproduce it. |
| The chip list after Enter | Stays filtered to what was typed (the box is cleared without a re-render); clearing the box does not bring the rest back until something is typed. | Shows the full list straight away. | A rendering quirk, not a behaviour anyone relies on. |
| Saves | Every value on the page. | Only the keys that changed. | As every tab. |

**Test fix that applies to all tab tests.** The fake `post` in each test pushed into a module-level `posted` array, so a save one test left queued (delay 0) could land in the next test's array. Each store now saves into its own list (`appearance`, `discovery`, `downloads` tests). Found when one test passed alone and failed in its file.

**Checked**

| Check | Result |
|---|---|
| Parity, all three parts, against the frozen markup after `settings.js`'s own help lines | Same elements, ids, classes, attributes and text. Passed first time. |
| Behaviour tests (`downloads.test.tsx`) | 14 more: resolving saved values, the Filtering summary, adding genres, Behaviour's saves and Test all sources, the conditional rows, Retry/Publishing saves with the collector's clamping, a setting-row "i", the explicit greying, Filtering's saves (a negative cap saves 0), the defaults on switching on, a saved list kept on switching on, Enter/filter/×, the empty list's message, a failing Reset. |
| Repointed | `src/test/music-size-limit-settings.test.ts` → `.tsx`: it ran the load line and collector snippet from `settings.js`, now loads and saves 0, 10 and 0.5 through the React tab, plus a negative cap and "not saved until changed". `settings-regroup-1822.test.ts`: the Filtering row moves to the parity test, and "finds a body for every info icon" now asks for more than zero vanilla icons (it asked for more than 90; 104 at the start of the port, 80 now, falling with each tab). |
| `src/features/settings` + `src/test` | 76 (twice, to rule out the leak) + 1,205 pass |
| Python tests that read the markup, scripts or these settings (86 files) | 2,237 passed, 10 failed. **9** pinned the moved code and are repointed (below); after repointing, their 5 files pass (206). **1** is pre-existing: `tests/downloads/test_atomic_album_publish.py::test_publish_order_is_total_even_for_names_that_only_differ_in_case` creates two files whose names differ only in case, which this Windows drive cannot hold; it fails on dev the same way. |
| Repointed Python tests (intent kept, now read the React tab) | `test_download_chain_widget.py` (2: the behaviour group still sits after both chain columns, now as a slot inside the vanilla wrapper, rendered through the shared Section; its ids exist in the tab), `test_explicit_preference.py` (2: the sub-options are disabled with the parent and save their own keys), `test_preferred_version.py` (3: settings.js no longer touches the key, the tab saves it only when changed, "off" is the first option, every option is a value the backend accepts), `test_source_search_timeout.py` (1), `test_video_download_config.py` (1: the Downloads tab's sections are still exactly Source Settings, Download Behaviour, Retry Logic, Album Publishing, Filtering, in that order). |
| `tsc --noEmit` / `oxlint` / build | clean / 0 warnings (4 fixed: number fields could stringify an object) / builds |
| **Browser, old page vs ported page** (sandbox with Downloads values set: hybrid, explicit blocked, a 3-genre whitelist…) | From clean servers: values and disabled states, the conditional rows, the summary, section headers, 14 help lines, the chips, the chain hiding Download Behaviour under the video chain, the four changes' saved values and the number of saves: **identical**. Screenshot with all four sections open: only the help button's dot differs (63 pixels). The one difference: the chip list right after Enter (above). |

**Learned.** Restart the sandboxes before each comparison: a failed run leaves its saves in the server's memory, and the two pages then start from different settings. And never wait on a background run with `pgrep -f <its args>`: the waiting loop's own command line matches, so it never ends (it cost half an hour here).

### Step 6: the Quality tab, music side (Nov 15 2026)

**What it is.** Everything on the music Quality tab, as one piece: the Music Quality tile (Quick Presets, Reset to defaults, the ranked target list with drag, ▲ ▼ and 🗑, "+ Add target", Upgrade until and its cutoff, Search strategy, Rank-based download order, the fallback switch), Quality on Import, Album Releases, Post-Download Conversion, Audio Verification, the Quality Profiles side panel (rows, rename in place, save now, Auto-Import, delete, "+ New profile", the editing banner) and the Manage overview it opens. It looks and behaves as before, with the deliberate differences listed below.

**Why it could not be split.** Every control on the tab but one belongs to the quality profile open in the side panel, not to config: the five tiles are one save (`collectFullQualityBundleFromUI`), and the page-wide save had a special block to keep them from overwriting the live default. Porting a tile without the panel would have split one save across React and vanilla.

**What is not in it, and why.** Video Quality, Custom Formats and YouTube Quality (40 controls, video only, `video-settings.js`): the video pass.

**Where the markup was.** One run of 614 lines (`<div class="qp-layout">`, the tiles and the side panel), replaced by one slot, `quality`/`profiles`. Fixture: `quality.vanilla.html`. Three more fixtures freeze what settings.js **drew** at run time, made by running its own `renderRankedTargets`, `renderUpgradeCutoffOptions`, `renderCustomQualityProfiles`, `qpManagerOverlay` and `renderQualityProfileManager` once over test data before they were deleted: `quality-targets.vanilla.html` (target rows and cutoff options), `quality-profiles.vanilla.html` (two rows, one open, one on Auto-Import), `quality-manager.vanilla.html` (the overview).

**How saving works now**

| What | Before | Now |
|---|---|---|
| An edit to any profile control (the ladder or the eight bundle controls) | `_qpHandleProfileControlChange` routed it to `debouncedSaveQualityProfile`, which captured the profile on screen and the whole bundle, and posted it 800 ms later to `/api/quality-profile/custom/<id>/update` (or `/api/quality-profile` with no profile list) | The same post, same body, same route, through the settings store: `store.setExtra('quality.profile.<id>', {target, bundle}, saveProfile)`. The key has the profile id in it, so an edit still goes to the profile it was made on after another is opened, as before. It follows the store's 2 s autosave, the autosave switch and the save button. |
| Verify FLAC decode | A plain config key in the page-wide save | `store.set('post_processing', 'verify_flac_decode', …)`, only when it changes |
| The page-wide save | Sent `acoustid.require_verified`, `post_processing.audio_completeness_check`, `import.replace_lower_quality` and all of `lossy_copy` from the controls, and while another profile was open swapped in the default's values so the server's config→default mirror could not overwrite the default; then called `saveQualityProfile()` with a warning toast of its own | Sends none of those keys. The server copies the default profile into config itself when the default is saved (`update_custom_quality_profile` → `apply_quality_profile_to_settings`), and its config→default mirror after any other save reads config that already matches. The profile save goes out in `SoulSyncSettings.flush()`; a failed one shows "Some settings failed to save" like any React tab. |
| Load | `loadSettingsData` set the eight bundle controls from config, then `init.js` called `loadQualityProfile()`, which put the default profile over them and loaded the profile list | The tab loads the default profile and the list each time the settings load (the store's `loads` counter), so on every visit as before. Until the profile arrives the eight controls show the config values, as before. |
| Presets on the default | POST `/preset/<name>` (or `/reset`), cleared the queued saves | Same routes. Queued profile saves are dropped (`store.dropExtras`), as before, so an edit of the old list never lands after the switch. |
| Presets on another profile | Loaded `/presets`, kept the search and upgrade choices, saved to that profile | Same. |
| Set default, rename, new, save now, delete, Auto-Import, Manage | The routes and confirms (`showConfirmDialog`) and toasts above | Same routes, bodies, confirms and toast texts. `invalidatePlaylistQualityProfiles()` still runs every time the list changes (R2-11). |

**Store additions** (`settings-store.ts`): `hasExtra(prefix)` and `dropExtras(prefix)`.

**Shared change (`parts.tsx`).** `HelpIcon` is exported (the target list's and the Manage overview's "i" have no help line, as before: `initSettingHelpLines` only gave lines to form-groups on the page). `Section` takes a `badge` (the "Profile" badge between the title and the hint). `textOf` reads a `<br>` as a space: in the html it sat on its own line, so the first-sentence split saw whitespace there; JSX drops it.

**Removed**

| Where | What | Lines (net, about) |
|---|---|---|
| `index.html` | the `qp-layout` block | 614 |
| `index.html` | the AcoustID switch's `onchange` that called `syncAcoustidRequireVerifiedVisibility` (a no-op) | 1 |
| `settings.js` | the whole QUALITY PROFILE FUNCTIONS block: 45 functions, their state and the Manage overview (kept: `_settingHelpBody` and `toggleSettingHelp`, which every vanilla tab still uses) | 1,210 |
| `settings.js` | `syncAcoustidRequireVerifiedVisibility`, `_QP_BUNDLE_CONTROL_IDS`, `_QP_PROFILE_CONTROL_IDS`, `_qpHandleProfileControlChange` and its call in the page autosave, `updateLossyBitrateOptions` | 54 |
| `settings.js` | `loadSettingsData`: the eight bundle controls, verify FLAC, the lossy options' display | 15 |
| `settings.js` | the collector's five quality keys and `lossy_copy`; the default-profile mirror block; `saveQualityProfile` and its "quality profile failed to save" branch in `saveSettings`; the quality timer in `handleManualSaveClick`; the tile gate in `updateDownloadSourceUI` | 52 |
| `init.js` | `await loadQualityProfile()` | 1 |

48 functions in all (the functions table is marked).

**Differences from the old page, all deliberate**

| What | Before | Now | Why |
|---|---|---|---|
| **An edit before the profile has loaded, or after it failed to load** | Saved the page to the profile: with no profile loaded the target list on the page was empty, so this posted `ranked_targets: []` over the real one | Not saved. The control changes on screen and the next load replaces it. | Data loss on dev, small window but real when `/api/quality-profile` errors. |
| A profile edit's wait | 800 ms | 2 s, the store's, like every other setting | One autosave rule for the page. |
| A new visit with an unsaved profile edit (autosave off) | The edit was overwritten on screen by the server's copy and lost | The edit stays on screen and is still saved by the save button; only the list reloads | Losing an edit nobody saved yet is not behaviour to keep. |
| A profile key missing from the server's answer (the legacy single-profile shape) | Its control was set to `undefined`: a tick box went off, a select went blank | The control keeps the config value it was loaded with | Same values in every real case; no blank select in the legacy one. |
| The Save button with nothing changed | Always posted the profile again | Posts nothing for the profile | Only changes are saved, as on every ported tab. |
| The hidden editing banner's name | After going back to the default it kept the last profile's name, hidden | Reads "this profile", hidden | Never on screen either way. |

**Checked**

| Check | Result |
|---|---|
| Parity: the whole tab against the frozen markup after `settings.js`'s own help lines | Same elements, ids, classes, attributes and text. The first run differed only in the help lines of three settings (Upgrade until, Rank-based download order, the fallback switch) whose first sentence ends right before a line break (the `textOf` fix above). |
| Parity of what settings.js drew: target rows + cutoff options, profile rows (one open, one on Auto-Import), the Manage overview | Same. |
| Behaviour tests (`quality.test.tsx`, 29) | values: labels, populate rules (`until_top`, clamped cutoff, a missing cutoff keeps the one on screen), the cutoff kept in range as targets come and go, group adds without duplicates, summaries, config fallback, the bundle. Saves: an edit posts the whole profile to the default and nothing to `/api/settings`; Verify FLAC is a plain setting; an edit goes to the profile it was made on after another is opened (autosave off, then the save button); nothing saved before a profile loads; a revisit reloads unless an edit waits. Controls: Best quality hides rank-based order, the cutoff shows only for "selected target", Opus greys out 320 and falls to 256, add/move/drag/remove, the empty list. Presets: on the default (and a queued edit never lands after), on another profile, reset refused on another profile. Panel: set default only after the confirm, rename (Enter, Escape), new profile, save now, delete (back to the default, the last one cannot be deleted), Auto-Import on and off. All pass twice. |
| Repointed (frontend) | `quality-release-import-mode.test.ts` → `.tsx`: the same five checks through the real tab. `settings-autosave-toggle`: the two quality checks now run the store with the switch off and on. `settings-load-element-drift`: the verify-flac pin reads the tab. `settings-react-host-hooks`: no `saveQualityProfile` in the page save. `settings-regroup-1822`: Album Releases moves to the parity test. |
| Repointed (Python) | `test_quality_profile_preview_nudge.py` (31): every contract kept, now on `quality-tab.tsx`/`quality.ts` (each bundle control saves through `editExtras`, each key is in `collectBundle`, each ladder control through `editLadder`, the target is taken at edit time and keyed per profile, the update route and the cache drop, a visit resets to the default, the named-profile preset path, the banner, the conversion copy), plus the page-wide save sending no profile key. **Negative-checked**: five broken copies of the source (a control unwired, a key dropped, the target taken at save time, the page save sending `lossy_copy`, a ladder control unwired) each fail their guard. `quality/test_lossless.py` (2): the lossless list and dropdown read the tab. `test_settings_sources_tab.py::test_no_two_cards_share_a_name`: card titles now include the React tabs' (it lost Music Quality otherwise). |
| `src/features/settings` + `src/test` | 1,309 pass |
| Python tests that read the markup, scripts or quality settings (27 files) | 928 passed before repointing, 34 failed, all pins on moved code; after repointing all pass. |
| `oxlint --type-check` / ruff / build | 0 warnings (4 fixed: `String()` of unknown values) / clean / builds |
| **Browser, old page vs ported page** (sandbox with two profiles, one on Auto-Import, lossy copy on) | From clean servers: every control's value, the shown/hidden groups, Opus 320, presets, target rows, cutoff options, profile rows, headers and help lines, closed and open; after Best quality on the default; after opening Strict; after editing Strict and going back and adding an Opus target; the Manage overview open and closed: **identical** (the row texts differ only in whitespace between flex items, which does not render). Writes: the same three profile updates with the same bodies to the same profiles; Verify FLAC sent `{post_processing: {verify_flac_decode}}` where the old page posted every setting. Screenshots: **0 pixels** differ by more than the noise threshold (the Manage one: 1 pixel, the help button). |
| `helper.js` popovers on `.preset-button`, `.ranked-targets-editor`, `#lossy-copy-enabled` | matched with `element.matches()` at click time, so they find the React markup |

## Change log

Newest first. One entry per change on the branch.

| Date | Commit | What changed |
|---|---|---|
| Nov 15 | `fe7c38f78` (step 6) | **Quality tab ported (music).** New: `tabs/quality.ts`, `tabs/quality-tab.tsx`, their test, four fixtures (one frozen from the markup, three from what settings.js drew). Store: `hasExtra`, `dropExtras`. `parts.tsx`: `HelpIcon` exported, `Section` badge, `<br>` reads as a space. Markup replaced by one slot; the quality profile block (48 functions), its load, collector keys, default-profile mirror and save branch removed from `settings.js`, the profile load from `init.js`. Details under "Completed steps". |
| Nov 15 | `57ade5bed` (step 5) | **Downloads tab ported, without the chain.** New: `tabs/downloads.ts`, `tabs/downloads-tab.tsx`, their test, three fixtures. `parts.tsx`: help lines read formatted help. Markup replaced by three slots (Behaviour inside the vanilla `#dlchain-behaviour` wrapper); the four sections' helpers, summary, load, save and the genre whitelist removed from `settings.js`. Test stores now save into their own lists. Details under "Completed steps". |
| Nov 15 | `2802c8ed0` (step 4) | **Discovery tab ported (music).** New: `tabs/parts.tsx` (shared sections, summaries, help lines), `tabs/discovery.ts`, `tabs/discovery-tab.tsx`, their test, two fixtures. Store: values with their own endpoints, and a load counter. Markup replaced by two slots; the tab's summaries, load, save, lookback and hemisphere code removed from `settings.js`. Details under "Completed steps". |
| Nov 15 | `f1b9ccf3f` (step 3) | **Logs tab ported.** New: `tabs/logs.ts`, `tabs/logs-tab.tsx`, their test, a frozen fixture, and the shared test helper `tabs/__fixtures__/shape.ts`. Markup replaced by one slot; the 12 `_log*` functions and the tab-switch hook removed from `settings.js`; a socket bridge added to `core.js`. Host: every tab is told which tab is open (`active`). Details under "Completed steps". |
| Nov 15 | `d58589201` (step 2) | **Appearance tab ported.** New: `tabs/appearance.ts`, `tabs/appearance-tab.tsx`, their test and two frozen-markup fixtures. Markup replaced by two slots; its load, save, listener and weather code removed from `settings.js`, `init.js` and `src/shell/weather-preview-settings.ts`. Host extended (several slots per tab, every ported tab on the page, opened counter, load effects). Fixed: the page autosave also fired for React controls. Details under "Completed steps". |
| Nov 14 | `5f5a2717a` (step 1) | **Scaffold.** New: `settings-store.ts`, `host.tsx` and their tests, `settings-react-host-hooks.test.ts`. Changed: `main.tsx` (mounts the host), `settings.js` (three hooks: `switchSettingsTab`, `loadSettingsData`, `saveSettings`). No markup changed, no tab ported. Details under "Completed steps". |
| Nov 14 | `2dbd3fb90` | Tracker created. No code changed. |

## Inventory by tab

Every control and button in the settings markup, grouped by the tab it shows on (`data-stg`), in file order. Side is who sees it. Mark the Ported column as each one moves.

### Connections (160)

**Metadata Source** (music only, 1)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| select | `metadata-fallback-source` | Primary metadata source: {Spotify / Spotify (no auth) / iTunes / Apple Music / Deezer / Discogs / MusicBrainz} | music only | no |

**Service** (both, 1)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| button |  | × | both | no |

**API Configuration Expand All** (music only, 1)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| button |  | Expand All | music only | no |

**Spotify** (music only, 7)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| text | `spotify-client-id` | Client ID: | music only | no |
| password | `spotify-client-secret` | Client Secret: | music only | no |
| text | `spotify-redirect-uri` | Redirect URI: | music only | no |
| button |  | 🔐 Authenticate | music only | no |
| button | `spotify-disconnect-btn` | 🔌 Disconnect | music only | no |
| button |  | Test Connection | music only | no |
| checkbox | `metadata-spotify-free-enrichment` | Use Spotify (no auth) for background enrichment | music only | no |

**iTunes / Apple Music** (music only, 2)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| select | `itunes-country` | Storefront Country: {United States (US) / United Kingdom (GB) / Canada (CA) / Australia (AU) / Germany (DE) /  | music only | no |
| button |  | Test Connection | music only | no |

**Deezer (Favorites & Playlists)** (music only, 6)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| text | `deezer-app-id` | App ID: | music only | no |
| password | `deezer-app-secret` | App Secret: | music only | no |
| text | `deezer-redirect-uri` | Redirect URI: | music only | no |
| button |  | 🔐 Authenticate | music only | no |
| password | `deezer-connection-arl` | ARL Token: | music only | no |
| button |  | Test Connection | music only | no |

**Discogs** (music only, 2)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| password | `discogs-token` | Personal Access Token: | music only | no |
| button |  | Test Connection | music only | no |

**Tidal (Playlists & Metadata)** (music only, 6)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| text | `tidal-client-id` | Client ID: | music only | no |
| password | `tidal-client-secret` | Client Secret: | music only | no |
| text | `tidal-redirect-uri` | Redirect URI: | music only | no |
| button |  | 🔐 Authenticate | music only | no |
| button |  | Disconnect | music only | no |
| button |  | Test Connection | music only | no |

**Qobuz (Metadata & Enrichment)** (music only, 7)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| button |  | Disconnect | music only | no |
| email | `qobuz-connection-email` | Email: | music only | no |
| password | `qobuz-connection-password` | Password: | music only | no |
| button | `qobuz-connection-login-btn` | Connect Qobuz | music only | no |
| password | `qobuz-connection-token` | Auth Token: | music only | no |
| button | `qobuz-token-login-btn` | Connect with Token | music only | no |
| button |  | Test Connection | music only | no |

**Concerts** (music only, 4)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| text | `concerts-ticketmaster-api-key` | Ticketmaster API Key: | music only | no |
| text | `concerts-country` | Concerts near you (country): | music only | no |
| text | `concerts-setlistfm-api-key` | Setlist.fm API Key: | music only | no |
| button |  | Test Connection | music only | no |

**Last.fm** (music only, 6)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| password | `lastfm-api-key` | API Key: | music only | no |
| password | `lastfm-api-secret` | API Secret: | music only | no |
| text | `lastfm-username` | Username: | music only | no |
| checkbox | `lastfm-scrobble-enabled` | Scrobble plays to Last.fm | music only | no |
| button |  | Authorize Scrobbling | music only | no |
| button |  | Test Connection | music only | no |

**Genius** (music only, 2)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| password | `genius-access-token` | Client Access Token: | music only | no |
| button |  | Test Connection | music only | no |

**Pollinations** (music only, 2)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| password | `pollinations-api-key` | API Key (optional): | music only | no |
| button |  | Test Connection | music only | no |

**fanart.tv** (music only, 2)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| password | `artist-fanart-api-key` | API Key (optional): | music only | no |
| button |  | Test Connection | music only | no |

**MusicBrainz** (music only, 4)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| url | `musicbrainz-base-url` | MusicBrainz server URL | music only | no |
| select | `musicbrainz-rate-mode` | MusicBrainz request rate {Fixed limit / No limit (adaptive)} | music only | no |
| number | `musicbrainz-request-rate` | MusicBrainz request rate | music only | no |
| button |  | Test Connection | music only | no |

**AcoustID Verification** (music only, 4)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| checkbox | `acoustid-enabled` | Enable Download Verification | music only | no |
| password | `acoustid-api-key` | Application API Key: | music only | no |
| button |  | Test Connection | music only | no |
| button |  | Clear Quarantine | music only | no |

**ListenBrainz** (music only, 5)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| text | `listenbrainz-base-url` | API Base URL: | music only | no |
| password | `listenbrainz-token` | User Token: | music only | no |
| text | `listenbrainz-username` | Username (optional): | music only | no |
| checkbox | `listenbrainz-scrobble-enabled` | Scrobble plays to ListenBrainz | music only | no |
| button |  | Test Connection | music only | no |

**Hydrabase** (music only, 10)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| text | `hydrabase-url` | WebSocket URL: | music only | no |
| password | `hydrabase-api-key` | API Key: | music only | no |
| checkbox | `hydrabase-auto-connect` | Auto-connect on startup | music only | no |
| button | `hydrabase-connect-btn` | Connect | music only | no |
| button |  | Test Spotify | music only | no |
| button |  | Test Tidal | music only | no |
| button |  | Test ListenBrainz | music only | no |
| button |  | Test AcoustID | music only | no |
| button |  | Test Last.fm | music only | no |
| button |  | Test Genius | music only | no |

**API Configuration Expand All** (video only, 1)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| button |  | Expand All | video only | no |

**TMDB** (video only, 2)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| password | `tmdb-api-key` | API Key: | video only | no |
| button |  | Test TMDB | video only | no |

**TVDB** (video only, 2)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| password | `tvdb-api-key` | API Key: | video only | no |
| button |  | Test TVDB | video only | no |

**OMDb (Ratings)** (video only, 2)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| password | `omdb-api-key` | API Key: | video only | no |
| button |  | Test OMDb | video only | no |

**fanart.tv (Artwork)** (video only, 2)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| password | `fanart-api-key` | API Key: | video only | no |
| button |  | Test fanart.tv | video only | no |

**OpenSubtitles (Subtitles)** (video only, 2)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| password | `opensubtitles-api-key` | API Key: | video only | no |
| button |  | Test OpenSubtitles | video only | no |

**SubDL (Subtitles)** (video only, 2)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| password | `subdl-api-key` | API Key: | video only | no |
| button |  | Test SubDL | video only | no |

**SubSource (Subtitles)** (video only, 2)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| password | `subsource-api-key` | API Key: | video only | no |
| button |  | Test SubSource | video only | no |

**Trakt (Audience Rating)** (video only, 2)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| password | `trakt-api-key` | API Key (Client ID): | video only | no |
| button |  | Test Trakt | video only | no |

**MDBList (List Collections)** (video only, 2)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| password | `mdblist-api-key` | API Key: | video only | no |
| button |  | Test MDBList | video only | no |

**Community Data (No Key)** (video only, 4)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| checkbox | `video-tvmaze-enabled` | TVmaze (TV community rating) | video only | no |
| checkbox | `video-anilist-enabled` | AniList (anime): links your anime to AniList by id and adds their roma | video only | no |
| checkbox | `video-wikidata-enabled` | Wikidata (official website link) | video only | no |
| button |  | Test Connections | video only | no |

**YouTube Extras (No Key)** (video only, 4)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| checkbox | `video-ryd-enabled` | Return YouTube Dislike (like/dislike estimates) | video only | no |
| checkbox | `video-sponsorblock-enabled` | SponsorBlock (crowd-sourced segments) | video only | no |
| checkbox | `video-dearrow-enabled` | DeArrow (crowd-sourced better titles) | video only | no |
| button |  | Test Connections | video only | no |

**Server Connection** (video only, 9)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| text |  | Plex Server URL: | video only | no |
| password |  | Plex Token: | video only | no |
| button |  | Test Connection | video only | no |
| text |  | Jellyfin Server URL: | video only | no |
| password |  | API Key: | video only | no |
| button |  | Test Connection | video only | no |
| select |  | Jellyfin User {Select User} | video only | no |
| select |  | Movies Library: {Loading...} | video only | no |
| select |  | TV Shows Library: {Loading...} | video only | no |

**Server Connections** (both, 38)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| button | `navidrome-toggle` | Navidrome | both | no |
| button | `music_assistant-toggle` | Music Assistant | both | no |
| button | `soulsync-toggle` | Standalone | both | no |
| url | `plex-url` | Plex Server URL: | both | no |
| button | `plex-autodetect-button` | Auto-detect | both | no |
| password | `plex-token` | Plex Token: | both | no |
| button | `plex-connect-button` | Connect | both | no |
| select | `plex-music-library` | Music Library: {Loading...} | both | no |
| button | `plex-config-action-button` | Clear Configuration | both | no |
| button | `plex-relink-button` | Re-link with Plex | both | no |
| button | `plex-link-to-plex-button` | Link to Plex (OAuth) | both | no |
| button | `plex-manual-config-button` | Manually Configure Plex | both | no |
| button | `plex-view-config-button` | View Configuration | both | no |
| button | `plex-pin-refresh-button` | Generate New Code | both | no |
| button |  | Cancel | both | no |
| url | `jellyfin-url` | Jellyfin Server URL: | both | no |
| password | `jellyfin-api-key` | API Key: | both | no |
| button |  | Auto-detect | both | no |
| button |  | Test | both | no |
| number | `jellyfin-timeout` | API Timeout (seconds): | both | no |
| select | `jellyfin-user` | Jellyfin User {Select User} | both | no |
| select | `jellyfin-music-library` | Music Library {Select Library} | both | no |
| url | `navidrome-url` | Navidrome Server URL: | both | no |
| text | `navidrome-username` | Username: | both | no |
| password | `navidrome-password` | Password: | both | no |
| text | `navidrome-playlist-username` | Playlist account (optional): | both | no |
| password | `navidrome-playlist-password` | Playlist account (optional): | both | no |
| button |  | Save playlist account | both | no |
| button | `navidrome-playlist-clear` | Use the account above | both | no |
| select | `navidrome-music-folder` | Music Library {All Libraries} | both | no |
| button |  | Auto-detect | both | no |
| button |  | Test | both | no |
| url | `music_assistant-url` | Music Assistant URL: | both | no |
| button |  | Auto-detect | both | no |
| password | `music_assistant-token` | Long-lived token: | both | no |
| select | `music_assistant-source` | Music source {} | both | no |
| text | `music_assistant-source-folder` | Source folder: | both | no |
| button |  | Load sources | both | no |

**Standalone Mode** (both, 2)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| button |  | Verify Output Folder | both | no |
| button |  | Test Server | both | no |

**MetaSync** (both, 10)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| button |  | Connect | both | no |
| button |  | I've approved it — finish connecting | both | no |
| text | `metasync-node-url` | MetaSync URL: | both | no |
| password | `metasync-local-key` |  | both | no |
| text | `metasync-pool-url` | Pool URL: | both | no |
| password | `metasync-pool-key` | Pool key: | both | no |
| button |  | Send pool key to MetaSync | both | no |
| button |  | Save | both | no |
| button |  | Refresh status | both | no |
| button |  | Disconnect | both | no |

**Notifications** (video only, 2)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| select | `video-download-mode` | Download Source: {Soulseek Only / Torrent Only (via Prowlarr) / Usenet Only (via Prowlarr) / Hybrid (Primary + | video only | no |
| button |  | + Add connection | video only | no |

### Sources (80)

**Download Sources** (both, 39)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| url | `soulseek-url` | slskd URL: | both | no |
| button |  | Auto-detect | both | no |
| password | `soulseek-api-key` | API Key: | both | no |
| number | `soulseek-search-timeout` | Search Timeout (seconds): | both | no |
| number | `soulseek-search-timeout-buffer` | Search Timeout Buffer (seconds): | both | no |
| number | `soulseek-search-min-delay-seconds` | Minimum Delay Between Searches (seconds): | both | no |
| select | `soulseek-min-peer-speed` | Minimum Peer Upload Speed: {Any speed / 1 Mbps / 2 Mbps / 3 Mbps / 4 Mbps / 5 Mbps / 10 Mbps} | both | no |
| select | `soulseek-max-peer-queue` | Max Peer Queue Length: {No limit / 5 / 10 / 20 / 50 / 100} | both | no |
| checkbox | `soulseek-observed-speed-fallback-enabl` | Retry slow Soulseek downloads | both | no |
| number | `soulseek-min-observed-download-speed` | Minimum Observed Download Speed (KB/s): | both | no |
| number | `soulseek-download-timeout` | Download Timeout (minutes): | both | no |
| checkbox | `soulseek-auto-clear-searches` | Auto-clear slskd search history | both | no |
| select | `soulseek-cleanup-scope` | slskd Cleanup Scope: {Only SoulSync's own transfers / Everything in slskd} | both | no |
| button |  | Test Soulseek Connection | both | no |
| button | `tidal-download-auth-btn` | Link Tidal Account | both | no |
| button |  | Disconnect | both | no |
| email | `qobuz-email` | Qobuz Account: | both | no |
| password | `qobuz-password` | Qobuz Account: | both | no |
| button | `qobuz-login-btn` | Connect Qobuz | both | no |
| password | `qobuz-download-token` | Qobuz Account: | both | no |
| button | `qobuz-download-token-btn` | Connect with Token | both | no |
| button | `hifi-test-btn` | Test Connection | both | no |
| url | `hifi-new-instance` | API Instances: | both | no |
| button |  | Add | both | no |
| button | `hifi-instances-check-btn` | Check All Instances | both | no |
| button | `hifi-instances-restore-btn` | Restore Defaults | both | no |
| password | `deezer-download-arl` | Deezer ARL Token: | both | no |
| button | `deezer-download-test-btn` | Test Connection | both | no |
| button | `soundcloud-test-btn` | Test Connection | both | no |
| text | `lidarr-url` | Lidarr URL: | both | no |
| password | `lidarr-api-key` | API Key: | both | no |
| button | `lidarr-test-btn` | Test Connection | both | no |
| select | `youtube-cookies-browser` | YouTube Browser Cookies: {None (No cookies) / Chrome / Firefox / Edge / Brave / Opera / Safari …} | both | no |
| textarea | `youtube-cookies-paste` | Paste cookies.txt: | both | no |
| number | `youtube-download-delay` | YouTube Download Delay: | both | no |
| checkbox | `youtube-album-bundle` | Download whole albums from YouTube Music | both | no |
| checkbox | `youtube-transcode` | Re-encode YouTube audio | both | no |
| select | `youtube-transcode-codec` | Codec: {MP3 / Opus / AAC (M4A)} | both | no |
| select | `youtube-transcode-bitrate` | Bitrate: {320 kbps / 256 kbps / 192 kbps / 128 kbps} | both | no |

**yt-dlp ●** (both, 2)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| select | `ytdlp-channel` | Release channel: {Nightly (recommended) / Stable} | both | no |
| button | `ytdlp-update-btn` | Update yt-dlp | both | no |

**⬢ Prowlarr** (both, 7)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| text | `prowlarr-url` | Prowlarr URL: | both | no |
| password | `prowlarr-api-key` | API Key: | both | no |
| text | `prowlarr-indexer-ids` | Restrict to indexer IDs (optional): | both | no |
| number | `prowlarr-search-min-gap` | Seconds between searches: | both | no |
| number | `prowlarr-max-searches-per-minute` | Searches per minute, at most: | both | no |
| button | `prowlarr-test-btn` | Test Connection | both | no |
| button | `prowlarr-refresh-indexers-btn` | Refresh Indexer List | both | no |

**⬢ Torrent Downloader** (both, 21)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| select | `torrent-client-type` | Client Type: {qBittorrent / Transmission / Deluge 2.x / Aria2 (RPC)} | both | no |
| text | `torrent-client-url` | WebUI URL: | both | no |
| text | `torrent-client-username` | Username: | both | no |
| password | `torrent-client-password` | Password: | both | no |
| text | `torrent-client-category` | Category / Label: | both | no |
| text | `torrent-client-save-path` | Save Path (optional): | both | no |
| checkbox | `torrent-client-start-torrents` | Start the torrents SoulSync adds | both | no |
| text | `torrent-download-path` | Completed Downloads Path — in SoulSync (optional): | both | no |
| number | `torrent-min-seeders` | Minimum seeders: | both | no |
| number | `torrent-stall-timeout` | Stalled torrent timeout (minutes): | both | no |
| select | `torrent-stall-action` | When a torrent stalls: {Abandon — remove it & its partial data, fail the download / Pause — pause it in the cl | both | no |
| number | `music-seed-ratio` | Seeding goals (torrents): | both | no |
| number | `music-seed-hours` | Seeding goals (torrents): | both | no |
| checkbox | `music-seed-remove-data` | also delete the client's copy | both | no |
| select | `music-seed-mode` | Seeding goals (torrents): {SoulSync (watches the client + removes) / Torrent client (writes its own seed limit | both | no |
| button | `torrent-client-test-btn` | Test Connection | both | no |
| number | `video-seed-ratio` | Seeding goals (torrents): | both | no |
| number | `video-seed-hours` | Seeding goals (torrents): | both | no |
| checkbox | `video-seed-remove-data` | also delete the client's copy | both | no |
| select | `video-seed-mode` | Seeding goals (torrents): {SoulSync (watches the client + removes) / Torrent client (writes its own seed limit | both | no |
| button | `video-seed-idx-refresh` | Load indexers | both | no |

**⬢ Usenet Downloader** (both, 11)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| select | `usenet-client-type` | Client Type: {SABnzbd / NZBGet} | both | no |
| text | `usenet-client-url` | WebUI URL: | both | no |
| password | `usenet-client-api-key` | API Key: | both | no |
| text | `usenet-client-username` | Username: | both | no |
| password | `usenet-client-password` | Password: | both | no |
| text | `usenet-client-category` | Category / Label: | both | no |
| text | `usenet-download-path` | Completed Downloads Path — in SoulSync (optional): | both | no |
| button | `usenet-client-test-btn` | Test Connection | both | no |
| select | `music-usenet-remove-completed` | When a usenet download has imported (music): {Leave it in the client / Remove it from the client's history / R | both | no |
| select | `video-usenet-remove-completed` | When a usenet download has imported (video): {Leave it in the client / Remove it from the client's history / R | both | no |
| checkbox | `video-redownload-failed` | When a video download fails, grab the next best release | both | no |

### Downloads (32)

**Soulseek (slskd) Connection** (both, 9)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| text | `video-slskd-url` | slskd URL: | both | no (video pass) |
| password | `video-slskd-api-key` | API Key: | both | no (video pass) |
| number | `video-slskd-search-timeout` | Search Timeout (seconds): | both | no (video pass) |
| number | `video-slskd-search-timeout-buffer` | Search Timeout Buffer (seconds): | both | no (video pass) |
| number | `video-slskd-search-min-delay` | Minimum Delay Between Searches (seconds): | both | no (video pass) |
| number | `video-slskd-min-peer-speed` | Minimum Peer Upload Speed (Mbps): | both | no (video pass) |
| number | `video-slskd-max-peer-queue` | Max Peer Queue Length: | both | no (video pass) |
| number | `video-slskd-download-timeout` | Download Timeout (minutes): | both | no (video pass) |
| checkbox | `video-slskd-auto-clear` | Auto-clear slskd search history | both | no (video pass) |

**Source Settings** (both, 4)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| select | `download-source-mode` | Download Source: {Soulseek Only / YouTube Only / Tidal Only / Qobuz Only / HiFi Only (Free Lossless) / Deezer  | both | no (with Sources) |
| search | `dlchain-filter` |  | both | no (with Sources) |
| select | `hybrid-primary-source` | {Soulseek / YouTube / Tidal / Qobuz / HiFi / Deezer / SoundCloud} | both | no (with Sources) |
| select | `hybrid-secondary-source` | {Soulseek / YouTube / Tidal / Qobuz / HiFi / Deezer / SoundCloud} | both | no (with Sources) |

**Download Behaviour** (both, 4)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| select | `stream-source` | Stream / Preview Source: {YouTube (Instant) / Active Download Source / Deezer 30s Preview (Instant)} | both | yes (step 5) |
| select | `max-concurrent-downloads` | Concurrent Downloads: {1 / 2 / 3 (Default) / 4 / 5 / 6 / 8 …} | both | yes (step 5) |
| number | `source-search-timeout` | Source search timeout (seconds): | both | yes (step 5) |
| button | `test-all-sources-btn` | Test all sources | both | yes (step 5) |

**Retry Logic** (both, 5)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| checkbox | `retry-next-candidate` | Retry next-best candidate on mismatch | both | yes (step 5) |
| checkbox | `retry-exhaustive` | Exhaustive retry (separate budget per source) | both | yes (step 5) |
| number | `retries-per-query` | Retries per query (per source) | both | yes (step 5) |
| checkbox | `accept-version-mismatch-fallback` | Accept best version mismatch as last resort | both | yes (step 5) |
| number | `version-mismatch-min-count` | Minimum matching mismatches before accepting | both | yes (step 5) |

**Album Publishing** (both, 2)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| checkbox | `album-atomic-publish` | Publish albums only when complete | both | yes (step 5) |
| checkbox | `album-atomic-recover` | Recover interrupted albums on startup | both | yes (step 5) |

**Filtering** (both, 8)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| checkbox | `allow-explicit` | Allow explicit content in downloads | both | yes (step 5) |
| checkbox | `prefer-explicit` | Prefer explicit versions when matching | both | yes (step 5) |
| checkbox | `exclude-clean` | Never download clean versions | both | yes (step 5) |
| number | `music-max-mb-per-minute` | Maximum music download size (MB/min) | both | yes (step 5) |
| select | `preferred-version` | Prefer a version when one exists {Off — take what the source asked for / Extended mix / long version / Radio e | both | yes (step 5) |
| checkbox | `genre-whitelist-enabled` | Enable strict genre filtering | both | yes (step 5) |
| text | `genre-whitelist-search` |  | both | yes (step 5) |
| button |  | Reset to Defaults | both | yes (step 5) |

### Quality (67)

**Video Quality** (both, 28)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| select |  | {Default} | both | no |
| text |  |  | both | no |
| button |  | + New profile | both | no |
| button |  | Delete | both | no |
| select |  | New movies use Default {Default} | both | no |
| select |  | New shows use Default {Default} | both | no |
| select | `vq-cutoff` | {4K · 2160p / 1080p / 720p / 480p (SD) / Best available — never stop upgrading} | both | no |
| button |  | Any | both | no |
| button |  | HEVC / x265 | both | no |
| button |  | AV1 | both | no |
| button |  | Don't care | both | no |
| button |  | Prefer | both | no |
| button |  | Require | both | no |
| button |  | Any | both | no |
| button |  | Surround | both | no |
| button |  | Lossless | both | no |
| button |  | Atmos | both | no |
| checkbox | `vq-prefer-repack` | Prefer REPACK / PROPER re-releases | both | no |
| button |  | Don't care | both | no |
| button |  | Prefer | both | no |
| button |  | Require | both | no |
| button |  | Main library | both | no |
| button |  | 4K library | both | no |
| range | `vq-movie-size` |  | both | no |
| range | `vq-episode-size` |  | both | no |
| range | `vq-movie-pref` |  | both | no |
| range | `vq-episode-pref` |  | both | no |
| range | `vq-min-seeders` |  | both | no |

**Custom Formats** (both, 2)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| button |  | + Add format | both | no |
| number | `vq-min-format-score` | Minimum format score (this profile): | both | no |

**YouTube Quality** (both, 10)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| select | `yq-resolution` | {Best available / 8K · 4320p / 4K · 2160p / 1440p / 1080p / 720p / 480p …} | both | no |
| button |  | Best | both | no |
| button |  | AV1 | both | no |
| button |  | VP9 | both | no |
| button |  | H.264 | both | no |
| button |  | MP4 | both | no |
| button |  | MKV | both | no |
| button |  | WebM | both | no |
| checkbox | `yq-60fps` | Prefer 60fps when available | both | no |
| checkbox | `yq-hdr` | Allow HDR (off = always grab SDR) | both | no |

**Music Quality** (both, 15)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| button |  | 🎧 Audiophile | both | yes (step 6) |
| button |  | ⚖️ Balanced | both | yes (step 6) |
| button |  | 💾 Space Saver | both | yes (step 6) |
| button |  | ↺ Reset to defaults | both | yes (step 6) |
| select | `rt-add-format` | {All lossless / FLAC / ALAC / WAV / AIFF / DSD (DSF / DFF) / All lossy / MP3 …} | both | yes (step 6) |
| select | `rt-add-bitdepth` | {Any bit depth / 16-bit / 24-bit} | both | yes (step 6) |
| select | `rt-add-samplerate` | {Any sample rate / ≥ 44.1 kHz / ≥ 48 kHz / ≥ 96 kHz / ≥ 192 kHz} | both | yes (step 6) |
| select | `rt-add-bitrate` | {Any bitrate / ≥ 96 kbps / ≥ 128 kbps / ≥ 192 kbps / ≥ 256 kbps / ≥ 320 kbps / Custom…} | both | yes (step 6) |
| number | `rt-add-bitrate-custom` | ≥ kbps | both | yes (step 6) |
| button |  | + Add target | both | yes (step 6) |
| select | `quality-upgrade-policy` | Upgrade until {No upgrades / Any accepted target / Selected target or better} | both | yes (step 6) |
| select | `quality-upgrade-cutoff` | Upgrade until {} | both | yes (step 6) |
| select | `quality-search-mode` | Search strategy {Source priority — fast (use the first source that has it) / Best quality — thorough (check ev | both | yes (step 6) |
| checkbox | `quality-rank-candidates` | Rank-based download order | both | yes (step 6) |
| checkbox | `quality-fallback-enabled` | Accept off-list quality when nothing in the list is available | both | yes (step 6) |

**Quality on Import** (both, 1)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| checkbox | `import-replace-lower-quality` | Replace lower quality files on import | both | yes (step 6) |

**Album Releases** (both, 1)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| select | `quality-release-import-mode` | Import from album releases {Requested tracks only (default) / Also the album's other tracks that pass} | both | yes (step 6) |

**Post-Download Conversion** (both, 5)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| checkbox | `downsample-hires` | Downsample Hi-Res FLAC to CD quality (16-bit / 44.1kHz) | both | yes (step 6) |
| checkbox | `lossy-copy-enabled` | Create lossy copy of downloaded FLAC files | both | yes (step 6) |
| select | `lossy-copy-codec` | Codec: {MP3 / Opus / AAC (M4A)} | both | yes (step 6) |
| select | `lossy-copy-bitrate` | Bitrate: {320 kbps / 256 kbps / 192 kbps / 128 kbps} | both | yes (step 6) |
| checkbox | `lossy-copy-delete-original` | Keep lossy output only — delete the lossless source after conversion | both | yes (step 6) |

**Audio Verification** (both, 3)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| checkbox | `acoustid-require-verified` | Only allow AcoustID-verified tracks for imports and downloads | both | yes (step 6) |
| checkbox | `audio-completeness-check` | Verify real audio with ffmpeg | both | yes (step 6) |
| checkbox | `verify-flac-decode` | Verify FLAC decode with flac -t | both | yes (step 6) |

**Quality Profiles** (both, 2)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| button |  | Manage | both | yes (step 6) |
| button | `qp-profile-new-btn` | + New profile | both | yes (step 6) |

### Library (236)

**Folders** (both, 4)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| button | `folders-music-tab` | Music | both | no |
| button | `folders-audiobooks-tab` | Audiobooks | both | no |
| button | `folders-podcasts-tab` | Podcasts | both | no |
| button | `folders-video-tab` | Video | both | no |

**Folder Paths** (both, 18)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| text | `download-path` | Download Folder (input): | both | no |
| button |  | Unlock | both | no |
| text | `transfer-path` | Music Library Folder (output): | both | no |
| button |  | Unlock | both | no |
| number | `min-free-disk-gb` | Minimum free disk space (GB): | both | no |
| text | `staging-path` | Import Folder: | both | no |
| button |  | Unlock | both | no |
| text | `music-videos-path` | Music Videos Dir: | both | no |
| button |  | Unlock | both | no |
| text | `playlists-materialize-path` | Playlists Folder: | both | no |
| button |  | Unlock | both | no |
| select | `playlists-materialize-mode` | Playlist Folder Style: {Symlinks — no extra disk, point to your library / Copies — real duplicates (USB sticks | both | no |
| button | `playlists-rebuild-btn` | Rebuild playlist folders now | both | no |
| checkbox | `m3u-relative-paths` | Write M3U entries relative to the playlist file | both | no |
| text | `m3u-entry-base-path` | M3U Entry Base Path: | both | no |
| button |  | Unlock | both | no |
| text | `m3u-rewrite-from` | M3U Path Prefix Rewrite: | both | no |
| text | `m3u-rewrite-to` | M3U Path Prefix Rewrite: | both | no |

**Additional Music Libraries** (both, 1)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| button |  | + Add Path | both | no |

**Sample Studio Folders** (both, 6)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| button |  | + Add Path | both | no |
| text | `audiobooks-path` | Audiobooks Folder (output): | both | no |
| button |  | Unlock | both | no |
| button |  | + Add folder | both | no |
| text | `podcasts-path` | Podcasts Folder (output): | both | no |
| button |  | Unlock | both | no |

**Where downloads arrive** (both, 2)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| text | `video-download-path` | Download folder (input, shared with Music): | both | no |
| button |  | Unlock | both | no |

**Libraries** (both, 8)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| text | `video-movies-path` | 🎬 Movies: | both | no |
| button |  | Unlock | both | no |
| text | `video-tv-path` | 📺 TV shows: | both | no |
| button |  | Unlock | both | no |
| text | `video-anime-path` | 🍥 Anime (optional): | both | no |
| button |  | Unlock | both | no |
| text | `video-youtube-path` | ▶️ YouTube: | both | no |
| button |  | Unlock | both | no |

**4K libraries (optional)** (both, 6)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| checkbox | `video-movies-4k-enabled` | Separate 4K movies folder | both | no |
| text | `video-movies-4k-path` | Separate 4K movies folder | both | no |
| button |  | Unlock | both | no |
| checkbox | `video-tv-4k-enabled` | Separate 4K TV folder | both | no |
| text | `video-tv-4k-path` | Separate 4K TV folder | both | no |
| button |  | Unlock | both | no |

**More libraries** (both, 1)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| button | `video-library-add` | + Add library | both | no |

**Your library's folders** (both, 1)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| button | `video-drive-map-save` | Save folders | both | no |

**Folders your server doesn't list** (both, 2)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| button |  | + Add folder | both | no |
| button |  | + Add folder | both | no |

**Docker volumes** (both, 1)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| button | `video-compose-copy` | Copy lines | both | no |

**Organization** (both, 4)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| button | `organization-music-tab` | Music | both | no |
| button | `organization-audiobooks-tab` | Audiobooks | both | no |
| button | `organization-podcasts-tab` | Podcasts | both | no |
| button | `organization-video-tab` | Video | both | no |

**File Organization** (both, 21)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| checkbox | `file-organization-enabled` | Enable custom file organization templates | both | no |
| checkbox | `auto-disambiguation` | Automatically add disambiguation to folder and filenames | both | no |
| text | `template-album-path` | Album Path Template: | both | no |
| checkbox | `detect-multi-artist-compilations` | Detect multi-artist compilations | both | no |
| text | `template-compilation-path` | Compilation Path Template: | both | no |
| text | `template-single-path` | Single Path Template: | both | no |
| text | `template-playlist-path` | Playlist Path Template: | both | no |
| text | `template-playlist-item` | Playlist File Naming: | both | no |
| text | `template-video-path` | Music Video Path Template: | both | no |
| text | `template-sample-path` | Sample Path Template: | both | no |
| select | `disc-label` | Multi-Disc Folder Label: {Disc (e.g., Disc 1/) / CD (e.g., CD 1/)} | both | no |
| select | `collab-artist-mode` | Collaborative Album Artist: {First Listed Artist (recommended) / All Artists Combined} | both | no |
| checkbox | `artistletter-symbol-fallback` | Group numbers & symbols under "#" | both | no |
| checkbox | `unicode-substitution` | Keep symbols in names with lookalike characters | both | no |
| select | `artist-separator` | Artist Tag Separator: {, (comma) / ; (semicolon) / / (slash) / & (ampersand)} | both | no |
| checkbox | `write-multi-artist` | Write multi-value ARTISTS and GENRE tags | both | no |
| checkbox | `feat-in-title` | Move featured artists to title | both | no |
| checkbox | `allow-duplicate-tracks` | Allow duplicate tracks across albums | both | no |
| checkbox | `wishlist-wing-it-guesses` | Search Wing It guesses as wishlist tracks | both | no |
| number | `wishlist-ignore-ttl` | Removed-track re-try window (days) | both | no |
| button |  | 🔄 Reset to Defaults | both | no |

**Library Organize** (both, 5)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| checkbox | `reorganize-preserve-casing` | Preserve my casing, year & disc layout | both | no |
| checkbox | `write-artist-nfo` | Write artist.nfo (Jellyfin / Kodi / Emby) | both | no |
| text | `template-audiobook-path` | Audiobook Path Template: | both | no |
| checkbox | `audiobook-recycle-deletes` | Recycle deleted audiobooks | both | no |
| number | `audiobook-recycle-keep-days` | Empty The Recycle Bin After: | both | no |

**Audiobook Quality** (both, 12)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| select | `audiobook-format-first` | Preferred Format: {M4B (recommended) / M4A / MP3 / Opus / OGG / FLAC} | both | no |
| checkbox |  | M4B | both | no |
| checkbox |  | M4A | both | no |
| checkbox |  | MP3 | both | no |
| checkbox |  | Opus | both | no |
| checkbox |  | OGG | both | no |
| checkbox |  | FLAC | both | no |
| checkbox |  | Single file (whole book) | both | no |
| checkbox |  | Multiple files (chapters or parts) | both | no |
| number | `audiobook-min-bitrate` | Reject Below: | both | no |
| number | `audiobook-max-bitrate` | Reject Above: | both | no |
| checkbox | `audiobook-allow-dramatized` | Show dramatised adaptations | both | no |

**Downloads & Import** (both, 52)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| select | `audiobook-marketplace` | Audible Marketplace: {United States (audible.com) / United Kingdom (audible.co.uk) / Germany (audible.de) / Fr | both | no |
| select | `audiobook-download-mode` | Audiobook Download Sources: {Soulseek Only / Torrent Only (via Prowlarr) / Usenet Only (via Prowlarr) / Hybrid | both | no |
| text | `audiobook-torrent-category` | Audiobook Downloader Category: | both | no |
| text | `audiobook-prowlarr-categories` | Audiobook Prowlarr Categories: | both | no |
| number | `audiobook-completeness-tolerance` | Import When Runtime Reaches: | both | no |
| number | `audiobook-staging-days` | Give Up On A Short Book After: | both | no |
| checkbox | `audiobook-renumber-chapters` | Renumber chapter files on import | both | no |
| checkbox | `audiobook-merge-m4b` | Merge multi-file books into one M4B on import | both | no |
| checkbox | `audiobook-remove-owned-from-wishlist` | Remove books from the wishlist once they're in the library | both | no |
| checkbox | `audiobook-embed-metadata` | Write tags into audiobook files | both | no |
| checkbox | `audiobook-embed-artwork` | Embed cover art in audiobook files | both | no |
| checkbox | `audiobook-save-artwork` | Save cover.jpg beside audiobooks | both | no |
| checkbox | `audiobook-write-nfo` | Write audiobook metadata sidecars | both | no |
| text | `template-podcast-path` | Podcast Path Template: | both | no |
| select | `podcast-media-format` | Podcast Media Format: {Audio Only (MP3 / M4A) — Default / Video Only (MP4 with static artwork for TV media ser | both | no |
| checkbox | `redownload-externally-deleted` | Re-download files deleted outside SoulSync | both | no |
| text | `vo-movie-template` | Movie Path Template: | both | no |
| text | `vo-episode-template` | Episode Path Template: | both | no |
| select | `vo-colon-replacement` | Colons in titles: {Delete (Dexter Resurrection) / Smart (Dexter - Resurrection, 12:30 becomes 12-30) / Space d | both | no |
| text | `vo-youtube-template` | YouTube Path Template: | both | no |
| number | `vo-yt-follow-count` | YouTube: videos to grab when you follow a channel: | both | no |
| select | `vo-transfer-mode` | When a file finishes: {Copy in, then remove the source (kept for torrents) / Move into the library} | both | no |
| checkbox | `vo-verify` | Verify with ffprobe | both | no |
| checkbox | `vo-replace` | Replace an existing copy when the new one is better | both | no |
| checkbox | `vo-subs` | Carry matching subtitles | both | no |
| checkbox | `vo-artwork` | Save artwork into the folder | both | no |
| checkbox | `vo-nfo` | Write NFO metadata files | both | no |
| checkbox | `vo-recycle` | Recycle deleted files instead of erasing them | both | no |
| number | `vo-recycle-days` | Keep recycled files for (days): | both | no |
| text | `vo-recycle-path` | Recycle folder (optional): | both | no |
| number | `vo-min-free` | Minimum free disk space (GB): | both | no |
| select | `vo-sponsorblock` | YouTube: SponsorBlock {Off / Mark segments as chapters / Cut sponsor segments out} | both | no |
| checkbox | `vo-yt-subs` | YouTube: embed subtitles | both | no |
| button | `vo-reset` | 🔄 Reset to Defaults | both | no |
| checkbox | `vo-subs-dl` | Download subtitles | both | no |
| text | `vo-sub-langs` | Subtitle Languages: | both | no |
| select | `vo-sub-hi-forced` | Hearing-impaired preference: {Off — best match wins / Prefer hearing-impaired} | both | no |
| checkbox | `vo-sub-forced` | Also grab forced subtitles | both | no |
| checkbox | `vo-sub-use-embedded` | Count embedded subtitles | both | no |
| checkbox | `vo-sub-ignore-pgs` | …except PGS (Blu-ray image subs) | both | no |
| checkbox | `vo-sub-ignore-vobsub` | …except VobSub (DVD image subs) | both | no |
| checkbox | `vo-sub-ignore-ass` | …except ASS/SSA (styled subs) | both | no |
| checkbox | `vo-sub-autosync` | Auto-sync subtitles to audio | both | no |
| select | `vo-whisper-model` | Local transcription (Whisper): {tiny / base / small} | both | no |
| button | `vo-whisper-dl` | Download model | both | no |
| checkbox | `vo-whisper-enabled` | Enable local transcription | both | no |
| number | `vo-sub-min-score` | Advanced: | both | no |
| number | `vo-sub-daily-quota` | Advanced: | both | no |
| number | `vo-sub-upgrade-delta` | Advanced: | both | no |
| number | `vo-sub-worker-interval` | Advanced: | both | no |
| number | `vo-sub-worker-batch` | Advanced: | both | no |
| button | `vo-sub-open-job` | Missing Subtitles job → | both | no |

**Post-Processing** (both, 87)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| checkbox | `metadata-enabled` | Enable post-processing on downloaded files | both | no |
| checkbox | `embed-album-art` | Embed album art into file | both | no |
| checkbox | `cover-art-download` | Download cover.jpg to album folder | both | no |
| checkbox | `prefer-caa-art` | Use MusicBrainz Cover Art Archive for album art | both | no |
| checkbox | `single-to-album-enabled` | Match singles to their parent album | both | no |
| checkbox | `lrclib-enabled` | Generate .lrc lyrics files (LRClib) | both | no |
| checkbox | `replaygain-enabled` | Apply ReplayGain tags after download | both | no |
| number | `duration-tolerance-seconds` | Duration tolerance (seconds) | both | no |
| checkbox | `embed-spotify` | Spotify | both | no |
| checkbox |  | Track ID | both | no |
| checkbox |  | Artist ID | both | no |
| checkbox |  | Album ID | both | no |
| checkbox | `embed-itunes` | iTunes | both | no |
| checkbox |  | Track ID | both | no |
| checkbox |  | Artist ID | both | no |
| checkbox |  | Album ID | both | no |
| checkbox | `embed-musicbrainz` | MusicBrainz | both | no |
| checkbox |  | Recording ID | both | no |
| checkbox |  | Artist ID | both | no |
| checkbox |  | Release ID | both | no |
| checkbox |  | Release Comment | both | no |
| checkbox |  | Recording Comment | both | no |
| checkbox |  | Release Group ID | both | no |
| checkbox |  | Album Artist ID | both | no |
| checkbox |  | Release Track ID | both | no |
| checkbox |  | Release Type | both | no |
| checkbox |  | Release Date | both | no |
| checkbox |  | Original Date / Year | both | no |
| checkbox |  | Artist Sort Order | both | no |
| checkbox |  | Album Artist Sort Order | both | no |
| checkbox |  | Artists | both | no |
| checkbox |  | Record Label | both | no |
| checkbox |  | Release Status | both | no |
| checkbox |  | Release Country | both | no |
| checkbox |  | Barcode | both | no |
| checkbox |  | Media | both | no |
| checkbox |  | Total Discs | both | no |
| checkbox |  | Catalog Number | both | no |
| checkbox |  | Script | both | no |
| checkbox |  | ASIN | both | no |
| checkbox |  | ISRC | both | no |
| checkbox |  | Genres | both | no |
| checkbox | `mb-use-original-date-for-date` | Use original release date for DATE | both | no |
| checkbox | `embed-deezer` | Deezer | both | no |
| checkbox |  | Track ID | both | no |
| checkbox |  | Artist ID | both | no |
| checkbox |  | BPM | both | no |
| checkbox |  | ISRC | both | no |
| checkbox | `embed-audiodb` | AudioDB | both | no |
| checkbox |  | Track ID | both | no |
| checkbox |  | Mood | both | no |
| checkbox |  | Style | both | no |
| checkbox |  | Genre | both | no |
| checkbox | `embed-tidal` | Tidal | both | no |
| checkbox |  | Track ID | both | no |
| checkbox |  | Artist ID | both | no |
| checkbox |  | ISRC | both | no |
| checkbox |  | Copyright | both | no |
| checkbox |  | BPM | both | no |
| checkbox | `embed-qobuz` | Qobuz | both | no |
| checkbox |  | Track ID | both | no |
| checkbox |  | Artist ID | both | no |
| checkbox |  | ISRC | both | no |
| checkbox |  | Copyright | both | no |
| checkbox |  | Label | both | no |
| checkbox | `embed-lastfm` | Last.fm | both | no |
| checkbox |  | Genres | both | no |
| checkbox |  | URL | both | no |
| checkbox | `embed-genius` | Genius | both | no |
| checkbox |  | Track ID | both | no |
| checkbox |  | URL | both | no |
| checkbox | `embed-hifi` | HiFi | both | no |
| checkbox |  | Track ID | both | no |
| checkbox |  | Artist ID | both | no |
| checkbox |  | ISRC | both | no |
| checkbox |  | BPM | both | no |
| checkbox |  | Copyright | both | no |
| checkbox | `embed-beatport` | Beatport | both | no |
| checkbox |  | BPM | both | no |
| checkbox |  | Key | both | no |
| checkbox |  | Label | both | no |
| checkbox |  | Catalog number | both | no |
| checkbox |  | Genre | both | no |
| checkbox |  | Track ID | both | no |
| checkbox | `beatport-search-all` | Look up every track, not just electronic ones Slower: one Beatport req | both | no |
| checkbox |  | Quality Audio quality (e.g. FLAC, MP3-320) | both | no |
| checkbox |  | Genre Merging Merge genres from Spotify + enabled sources | both | no |

**Import** (both, 2)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| checkbox | `import-folder-artist-override` | Use the top Staging folder as the artist | both | no |
| checkbox | `import-transfer-permanent` | Music Library is my permanent library — never move files out | both | no |

**Detail Pages** (both, 2)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| checkbox | `video-billboard-autoplay` | Autoplay trailers in the billboard | both | no |
| select | `video-watch-region` | Where-to-watch region United States United Kingdom Canada Australia Ir {United States / United Kingdom / Canad | both | no |

**Monitoring** (both, 1)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| checkbox | `video-monitor-specials` | Monitor Specials (season 0) | both | no |

### Discovery (12)

**Playlists** (both, 6)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| checkbox | `m3u-export-enabled` | Auto-save M3U file when downloading playlists | both | yes (step 4) |
| checkbox | `library-m3u-enabled` | Auto-generate a whole-library M3U (updates on every scan) | both | yes (step 4) |
| text | `library-m3u-path` | Whole-library M3U output folder: | both | yes (step 4) |
| select | `playlist-sync-mode` | Sync mode: {Replace — recreate the playlist each sync / Reconcile — edit in place, keep image & description (d | both | yes (step 4) |
| checkbox | `playlist-sync-wishlist-missing` | Add missing tracks to the wishlist during sync | both | yes (step 4) |
| checkbox | `create-backup` | Keep a "<name> Backup" copy on the server before a Replace sync | both | yes (step 4) |

**Listening Stats** (both, 2)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| checkbox | `listening-stats-enabled` | Enable listening stats collection from media server | both | yes (step 4) |
| number | `listening-stats-interval` | Poll Interval (minutes): | both | yes (step 4) |

**Discovery** (both, 1)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| range | `discover-adventurousness` | Adventurousness | both | yes (step 4) |

**Discovery Pool Settings** (music only, 2)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| select | `discovery-lookback-period` | Lookback Period: {Last 7 days / Last 30 days (Default) / Last 90 days / Last 6 months / Entire discography} | music only | yes (step 4) |
| select | `discovery-hemisphere` | Hemisphere: {Northern Hemisphere / Southern Hemisphere} | music only | yes (step 4) |

**Import Lists** (video only, 1)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| button |  | + Add list | video only | no (video pass) |

### Appearance (14)

**Show only what you use** (both, 1)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| button | `settings-open-feature-picker` | Customize SoulSync | both | yes (step 2) |

**UI Appearance** (both, 7)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| select | `accent-preset` | Accent Color: {Spotify Green / Ocean Blue / Purple / Boulder Purple / Sunset Orange / Rose / Teal …} | both | yes (step 2) |
| color | `accent-custom-color` | Custom Color: | both | yes (step 2) |
| select | `sidebar-visualizer-type` | Sidebar Visualizer: {Bars / Wave / Spectrum / Mirror / Equalizer / None} | both | yes (step 2) |
| checkbox | `particles-enabled` | Background Particles | both | yes (step 2) |
| checkbox | `worker-orbs-enabled` | Worker Orbs | both | yes (step 2) |
| checkbox | `reduce-effects-enabled` | Reduce Visual Effects | both | yes (step 2) |
| checkbox | `max-performance-enabled` | Max Performance | both | yes (step 2) |

**Sidebar Weather** (both, 6)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| text | `sw-location` | Location: | both | yes (step 2) |
| button |  | Save | both | yes (step 2) |
| button |  | Clear | both | yes (step 2) |
| checkbox | `sw-enabled` | Show weather in the sidebar | both | yes (step 2) |
| checkbox | `sw-holidays` | Holiday decorations | both | yes (step 2) |
| checkbox | `sw-celsius` | Use °C instead of °F | both | yes (step 2) |

### System (63)

**Saving** (both, 1)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| checkbox | `settings-autosave-enabled` | Save settings as I change them | both | no |

**🔑 Lock with a PIN simple · one shared PIN** (both, 5)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| password | `security-new-pin` | Step 1 — Set admin PIN: | both | no |
| password | `security-confirm-pin` | Step 1 — Set admin PIN: | both | no |
| button | `security-save-pin-btn` | Save PIN | both | no |
| checkbox | `security-require-pin` | Require PIN to access SoulSync | both | no |
| button |  | Change PIN | both | no |

**👤 User accounts (login) per-person · best for public access** (both, 28)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| password | `security-login-password` | Step 1 — Admin login password: | both | no |
| password | `security-login-password-confirm` | Step 1 — Admin login password: | both | no |
| button |  | Save Password | both | no |
| select | `security-recovery-question` | Step 2 (recommended) — Password recovery question: {— Select a question — / What was the name of your first pe | both | no |
| text | `security-recovery-custom` | Step 2 (recommended) — Password recovery question: | both | no |
| text | `security-recovery-answer` | Step 2 (recommended) — Password recovery question: | both | no |
| button |  | Save Recovery Question | both | no |
| checkbox | `security-require-login` | Step 3 — Require login (username + password) | both | no |
| checkbox | `security-plex-signin` | Sign in with Plex | both | no |
| checkbox | `security-plex-signin-auto-create` | Let new Plex users sign up (off: only people who already have a Plex p | both | no |
| checkbox | `security-plex-signin-can-download` | New Plex profiles can download (off: request-only) | both | no |
| checkbox | `security-oidc-signin` | Sign in with OIDC | both | no |
| text | `security-oidc-issuer` | Issuer URL: | both | no |
| text | `security-oidc-client-id` | Client ID: | both | no |
| password | `security-oidc-client-secret` | Client secret: | both | no |
| text | `security-oidc-button-label` | Button label: | both | no |
| text | `security-oidc-icon-url` | Icon URL (optional) : | both | no |
| text | `security-oidc-required-group` | Required group (optional) : | both | no |
| text | `security-oidc-groups-claim` | Groups claim: | both | no |
| text | `security-oidc-scopes` | Scopes: | both | no |
| text | `security-oidc-redirect-uri` | Callback URL override (optional) : | both | no |
| checkbox | `security-oidc-signin-auto-create` | Let new OIDC users sign up (off: only accounts already connected to a | both | no |
| checkbox | `security-oidc-signin-can-download` | New OIDC profiles can download (off: request-only) | both | no |
| button |  | Connect my admin account | both | no |
| checkbox | `security-oidc-only` | OIDC only: turn off every other way to sign in | both | no |
| select | `security-signup-sides` | New sign-ups start on {Music / Movies & TV / Music and Movies & TV} | both | no |
| number | `security-signup-request-limit` | Request limit for new sign-ups | both | no |
| number | `security-signup-request-limit-days` | Request limit for new sign-ups | both | no |

**🌐 Reverse proxy & remote access** (both, 3)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| checkbox | `security-trust-proxy` | Behind a reverse proxy | both | no |
| text | `security-auth-proxy-header` | ↳ Auth proxy user header (optional) : | both | no |
| textarea | `security-cors-origins` | Allowed WebSocket Origins: | both | no |

**Database Settings** (both, 1)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| select | `max-workers` | Concurrent Workers: {3 / 4 / 5 / 6 / 7 / 8 / 9 …} | both | no |

**Database Maintenance** (both, 2)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| button | `db-vacuum-btn` | Compact Database (VACUUM) | both | no |
| button | `db-incvacuum-btn` | Enable Incremental Vacuum | both | no |

**Logging** (both, 1)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| select | `log-level-select` | Log Level: {DEBUG (Detailed) / INFO (Normal) / WARNING (Minimal) / ERROR (Critical Only)} | both | no |

**REST API Keys** (both, 4)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| text | `api-key-label` | New Key Label: | both | no |
| select | `api-key-scope` | Access: {Read-only (stats and library, for dashboards) / Admin (can change settings and everything else)} | both | no |
| button |  | 🔑 Generate API Key | both | no |
| button |  | Copy | both | no |

**MCP for AI Clients** (both, 1)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| checkbox | `mcp-enabled` | Answer MCP requests at /mcp | both | no |

**Lidarr Compatible API** (both, 2)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| checkbox | `lidarr-compat-enabled` | Answer as Lidarr at /lidarr | both | no |
| select | `lidarr-compat-profile` | Requests go to profile: {Loading...} | both | no |

**Developer Mode** (both, 2)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| password | `dev-mode-password` | Password: | both | no |
| button |  | Activate | both | no |

**Artwork Cache** (both, 4)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| number | `imgcache-max-mb` | Size limit (MB): | both | no |
| checkbox | `imgcache-thumbnails` | Serve resized thumbnails | both | no |
| button | `imgcache-prune-btn` | Reclaim space now | both | no |
| button | `imgcache-clear-btn` | Clear cache | both | no |

**Developer** (both, 7)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| select | `sw-preview-preset` | Weather preview: {Live weather} | both | no |
| select | `sw-preview-holiday` | Weather preview: {Holiday by date / No holiday} | both | no |
| date | `sw-preview-date` | Weather preview: | both | no |
| button | `sw-preview-reset` | Back to live | both | no |
| select | `ob-preview-step` | Setup preview: {Start at Welcome / Your account / What it's for / Media server / Folders / Download sources /  | both | no |
| select | `ob-preview-env` | Setup preview: {Detect for real / Pretend Docker / Pretend bare metal / Pretend nothing found} | both | no |
| button | `ob-preview-open` | Open preview | both | no |

**Experimental** (music only, 2)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| checkbox | `experimental-jiosaavn-enabled` | Enable JioSaavn metadata source | music only | no |
| checkbox | `experimental-bandcamp-enabled` | Enable Bandcamp search & enrichment | music only | no |

### Logs (10)

**(in the file under "Experimental")** (both, 10)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| select | `log-viewer-source` | {app.log / post_processing.log / acoustid.log / source_reuse.log} | both | yes (step 3) |
| button |  | All | both | yes (step 3) |
| button |  | Debug | both | yes (step 3) |
| button |  | Info | both | yes (step 3) |
| button |  | Warn | both | yes (step 3) |
| button |  | Error | both | yes (step 3) |
| text | `log-viewer-search` |  | both | yes (step 3) |
| button |  | Copy | both | yes (step 3) |
| button |  | Clear | both | yes (step 3) |
| checkbox | `log-viewer-autoscroll` | Auto-scroll | both | yes (step 3) |

### No tab (page header and shared pieces) (4)

**Settings** (both, 2)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| text | `stg-search-input` |  | both | no |
| button | `save-settings` | All changes saved | both | no |

**Source** (both, 2)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| button | `src-modal-test` | Test | both | no |
| button |  | × | both | no |

## settings.js functions (333)

Every top-level function, with its line today. A function is removed when the last thing that calls it is gone. Mark it when it goes.

| Function | Line | Removed |
|---|---|---|
| `showSupportModal` | 4 | no |
| `closeSupportModal` | 9 | no |
| `copyAddress` | 14 | no |
| `syncAcoustidRequireVerifiedVisibility` | 46 | yes (step 6) |
| `syncPreferExplicitState` | 55 | yes (step 5) |
| `syncRetryConditionalRows` | 72 | yes (step 5) |
| `syncOidcProviderBlock` | 89 | no |
| `_qpHandleProfileControlChange` | 116 | yes (step 6) |
| `_isVideoOwnedField` | 125 | no |
| `debouncedAutoSaveSettings` | 131 | no |
| `handleSettingsAutosaveToggle` | 167 | no |
| `handleManualSaveClick` | 171 | no |
| `setSettingsSaveState` | 191 | no |
| `syncMetadataSourceSelection` | 214 | no |
| `_isMetadataSourceSelectable` | 222 | no |
| `syncJiosaavnMetadataSourceOption` | 242 | no |
| `showExperimentalEnableDialog` | 271 | no |
| `resolveExperimentalEnableDialog` | 306 | no |
| `syncJiosaavnEnrichmentBubble` | 322 | no |
| `onExperimentalJiosaavnToggle` | 339 | no |
| `onExperimentalBandcampToggle` | 364 | no |
| `_metadataSourceFallback` | 385 | no |
| `focusServiceSettingsSection` | 390 | no |
| `sanitizeMetadataSourceSelection` | 411 | no |
| `handleMetadataSourceChange` | 441 | no |
| `_markCredentialFieldsNoAutofill` | 460 | no |
| `initializeSettings` | 474 | no |
| `resetFileOrganizationTemplates` | 572 | no |
| `validateFileOrganizationTemplates` | 596 | no |
| `_svcSyncKindToSide` | 847 | no |
| `_svcFrames` | 853 | no |
| `_svcMeta` | 864 | no |
| `_svcConfigured` | 890 | no |
| `buildServiceTiles` | 906 | no |
| `switchServiceKind` | 975 | no |
| `openServiceModal` | 983 | no |
| `closeServiceModal` | 1009 | no |
| `refreshLibrarySummaries` | 1123 | no |
| `switchLibraryMediaTab` | 1183 | no |
| `handleLibraryMediaTabKey` | 1194 | no |
| `switchSettingsTab` | 1206 | no |
| `filterSettings` | 1304 | no |
| `settingHelpFirstLine` | 1362 | no |
| `initSettingHelpLines` | 1374 | no |
| `_pathPreviewSlot` | 1441 | no |
| `initPathPreview` | 1455 | no |
| `refreshPathPreview` | 1486 | no |
| `_fetchPathPreview` | 1492 | no |
| `_renderPathPreview` | 1561 | no |
| `applyServiceStatusGradients` | 1627 | no |
| `syncSpotifySettingsAuthState` | 1656 | no |
| `_stgSetCheckingState` | 1681 | no |
| `_stgShowVerifyWarning` | 1712 | no |
| `_stgClearVerifyWarning` | 1730 | no |
| `_stgRefreshAfterSave` | 1737 | no |
| `_stgVerifyServices` | 1757 | no |
| `toggleStgService` | 1793 | no |
| `toggleAllServiceAccordions` | 1808 | no |
| `_dlchainSyncKindToSide` | 1921 | no |
| `_dlchainPoolHtml` | 1932 | no |
| `dlchainFilter` | 1956 | no |
| `_dlchainApplyFilter` | 1962 | no |
| `_dlchainTile` | 1980 | no |
| `_dlchainStep` | 1996 | no |
| `dlchainMove` | 2029 | no |
| `renderDownloadChain` | 2038 | no |
| `switchDownloadChain` | 2096 | no |
| `_dlchainLoad` | 2104 | no |
| `_dlchainCommit` | 2122 | no |
| `dlchainAdd` | 2135 | no |
| `dlchainRemove` | 2141 | no |
| `_dlchainWireDrag` | 2153 | no |
| `renderAudiobookHybrid` | 2253 | no |
| `moveAudiobookSource` | 2274 | no |
| `toggleAudiobookSource` | 2282 | no |
| `_audiobookActiveSources` | 2296 | no |
| `onAudiobookModeChange` | 2305 | no |
| `toggleSourceCard` | 2389 | no |
| `_loadVideoChainForTiles` | 2400 | no |
| `_srcAllChains` | 2412 | no |
| `_srcChainUses` | 2423 | no |
| `_srcTileMarkup` | 2435 | no |
| `buildSourceTiles` | 2478 | no |
| `probeSourceOnOpen` | 2531 | no |
| `testOneSource` | 2547 | no |
| `openSourceModal` | 2581 | no |
| `closeSourceModal` | 2635 | no |
| `toggleHybridSourceConfig` | 2696 | no |
| `openSourceConfig` | 2706 | no |
| `markUnsupportedCookieBrowsers` | 2726 | no |
| `updateCookieBrowserWarning` | 2740 | no |
| `_ssJson` | 2755 | no |
| `_ssTestConn` | 2765 | no |
| `testAllSources` | 2804 | no |
| `autoTestSourcesOnce` | 2909 | no |
| `buildHybridSourceList` | 2915 | no |
| `moveHybridSource` | 3012 | no |
| `_reorderHybridSource` | 3029 | no |
| `toggleHybridSource` | 3045 | no |
| `_syncHybridOrderFromDOM` | 3058 | no |
| `_syncHybridHiddenSelects` | 3072 | no |
| `getHybridOrder` | 3080 | no |
| `buildArtSourceList` | 3096 | no |
| `moveArtSource` | 3133 | no |
| `toggleArtSource` | 3143 | no |
| `getArtOrder` | 3150 | no |
| `loadArtSourceOrder` | 3154 | no |
| `loadHybridSourceOrder` | 3194 | no |
| `updateLossyBitrateOptions` | 3236 | yes (step 6) |
| `updateYoutubeTranscodeBitrateOptions` | 3250 | no |
| `updatePlexConfigurationButtons` | 3263 | no |
| `loadSettingsData` | 3282 | no |
| `_wireRedactedSecrets` | 4212 | no |
| `changeLogLevel` | 4238 | no |
| `updateMediaServerFields` | 4263 | no |
| `showPlexConfiguration` | 4280 | no |
| `showPlexSetup` | 4301 | no |
| `setPlexConfigActionButton` | 4314 | no |
| `startPlexRelink` | 4334 | no |
| `startPlexPinAuth` | 4341 | no |
| `startPlexPinAuthPolling` | 4379 | no |
| `stopPlexPinAuthPolling` | 4386 | no |
| `pollPlexPinAuthStatus` | 4393 | no |
| `cancelPlexPinAuth` | 4458 | no |
| `restartPlexPinAuth` | 4473 | no |
| `clearPlexConfiguration` | 4480 | no |
| `loadMediaServerNames` | 4530 | no |
| `activeServerFromToggles` | 4542 | no |
| `toggleServer` | 4547 | no |
| `updateDownloadSourceUI` | 4581 | no |
| `updateHybridSecondaryOptions` | 4628 | no |
| `_qpDefaultProfileId` | 4673 | yes (step 6) |
| `debouncedSaveQualityProfile` | 4680 | yes (step 6) |
| `loadQualityProfile` | 4700 | yes (step 6) |
| `rtLabel` | 4722 | yes (step 6) |
| `populateQualityProfileUI` | 4733 | yes (step 6) |
| `onSearchModeChange` | 4771 | yes (step 6) |
| `renderUpgradeCutoffOptions` | 4777 | yes (step 6) |
| `onUpgradePolicyChange` | 4794 | yes (step 6) |
| `_settingHelpBody` | 4805 | no |
| `toggleSettingHelp` | 4828 | no |
| `renderRankedTargets` | 4840 | yes (step 6) |
| `reorderRankedTarget` | 4884 | yes (step 6) |
| `moveRankedTarget` | 4891 | yes (step 6) |
| `deleteRankedTarget` | 4899 | yes (step 6) |
| `rtSelectionIsLossless` | 4914 | yes (step 6) |
| `onRtAddFormatChange` | 4918 | yes (step 6) |
| `onRtBitrateChange` | 4928 | yes (step 6) |
| `addRankedTarget` | 4935 | yes (step 6) |
| `applyQualityPreset` | 4973 | yes (step 6) |
| `resetActiveQualityPreset` | 5047 | yes (step 6) |
| `collectQualityProfileFromUI` | 5088 | yes (step 6) |
| `saveQualityProfile` | 5115 | yes (step 6) |
| `_qpSetProfileRows` | 5182 | yes (step 6) |
| `normalizeQualityProfileId` | 5189 | yes (step 6) |
| `loadCustomQualityProfiles` | 5197 | yes (step 6) |
| `qpProfileSummary` | 5220 | yes (step 6) |
| `renderCustomQualityProfiles` | 5251 | yes (step 6) |
| `qpStartRename` | 5394 | yes (step 6) |
| `qpShowNewProfileInput` | 5455 | yes (step 6) |
| `qpResetNewProfileControl` | 5487 | yes (step 6) |
| `collectFullQualityBundleFromUI` | 5498 | yes (step 6) |
| `applyFullQualityBundleToDom` | 5517 | yes (step 6) |
| `saveCurrentAsQualityProfile` | 5537 | yes (step 6) |
| `updateCustomQualityProfile` | 5581 | yes (step 6) |
| `applyCustomQualityProfile` | 5610 | yes (step 6) |
| `confirmSetDefaultQualityProfile` | 5647 | yes (step 6) |
| `confirmSetDefaultQualityProfileById` | 5660 | yes (step 6) |
| `previewQualityProfile` | 5669 | yes (step 6) |
| `qpShowEditingBanner` | 5705 | yes (step 6) |
| `qpHideEditingBanner` | 5720 | yes (step 6) |
| `deleteCustomQualityProfile` | 5725 | yes (step 6) |
| `toggleAutoImportQualityProfile` | 5764 | yes (step 6) |
| `qpManagerOverlay` | 5796 | yes (step 6) |
| `renderQualityProfileManager` | 5847 | yes (step 6) |
| `clearAutoImportQualityProfileFromManager` | 5891 | yes (step 6) |
| `openQualityProfileManager` | 5895 | yes (step 6) |
| `closeQualityProfileManager` | 5907 | yes (step 6) |
| `toggleHydrabaseFromSettings` | 5915 | no |
| `renderMusicPaths` | 5981 | no |
| `addMusicPathRow` | 6000 | no |
| `_removeMusicPathRow` | 6021 | no |
| `addAudiobookExtraPathRow` | 6032 | no |
| `renderAudiobookExtraPaths` | 6069 | no |
| `collectAudiobookExtraPaths` | 6077 | no |
| `collectMusicPaths` | 6087 | no |
| `renderSamplePaths` | 6101 | no |
| `loadAudiobookRoutes` | 6124 | no |
| `audiobookRouteAction` | 6143 | no |
| `addSamplePathRow` | 6184 | no |
| `_removeSamplePathRow` | 6204 | no |
| `collectSamplePaths` | 6210 | no |
| `_genreWhitelistRender` | 6223 | yes (step 5) |
| `_initGenreChipClickHandler` | 6242 | yes (step 5) |
| `_genreWhitelistRemove` | 6254 | yes (step 5) |
| `_genreWhitelistAdd` | 6260 | yes (step 5) |
| `_genreWhitelistReset` | 6270 | yes (step 5) |
| `_collectGenreWhitelist` | 6311 | yes (step 5) |
| `_logClassify` | 6322 | yes (step 3) |
| `_logClassToCSS` | 6336 | yes (step 3) |
| `_logViewerInit` | 6340 | yes (step 3) |
| `_logViewerStop` | 6370 | yes (step 3) |
| `_logViewerOnLive` | 6379 | yes (step 3) |
| `_logViewerAppendLines` | 6395 | yes (step 3) |
| `_logViewerChangeSource` | 6425 | yes (step 3) |
| `_logViewerFilterLevel` | 6433 | yes (step 3) |
| `_logViewerOnSearch` | 6441 | yes (step 3) |
| `_logViewerReload` | 6449 | yes (step 3) |
| `_logViewerCopy` | 6456 | yes (step 3) |
| `_logViewerClear` | 6474 | yes (step 3) |
| `loadDbMaintenanceInfo` | 6482 | no |
| `runDatabaseVacuum` | 6505 | no |
| `enableIncrementalVacuum` | 6532 | no |
| `activateDevMode` | 6560 | no |
| `hydrabaseToggleConnection` | 6590 | no |
| `hydrabaseConnect` | 6598 | no |
| `hydrabaseDisconnect` | 6634 | no |
| `loadHydrabaseComparisons` | 6655 | no |
| `hydrabaseSendRaw` | 6695 | no |
| `toggleTagGroup` | 6743 | no |
| `toggleServiceTags` | 6755 | no |
| `_collectServiceTags` | 6773 | no |
| `_getTagConfig` | 6782 | no |
| `_cfgStr` | 6803 | no |
| `_cfgFloat` | 6812 | no |
| `_cfgInt` | 6822 | no |
| `_cfgBool` | 6829 | no |
| `saveSettings` | 6834 | no |
| `authorizeLastfmScrobbling` | 7537 | no |
| `testConnection` | 7554 | no |
| `testDeezerConnection` | 7599 | no |
| `clearQuarantine` | 7630 | no |
| `loadLidarrCompatProfiles` | 7654 | no |
| `loadApiKeys` | 7674 | no |
| `renderApiKeys` | 7691 | no |
| `_initApiKeyClickHandler` | 7721 | no |
| `generateApiKey` | 7733 | no |
| `copyApiKey` | 7766 | no |
| `revokeApiKey` | 7783 | no |
| `testDashboardConnection` | 7802 | no |
| `autoDetectPlex` | 7834 | no |
| `autoDetectJellyfin` | 7861 | no |
| `autoDetectMusicAssistant` | 7888 | no |
| `loadMusicAssistantSources` | 7907 | no |
| `selectMusicAssistantSource` | 7935 | no |
| `autoDetectNavidrome` | 7951 | no |
| `autoDetectSlskd` | 7978 | no |
| `cancelDetection` | 8005 | no |
| `updateStatusDisplays` | 8011 | no |
| `authenticateSpotify` | 8023 | no |
| `disconnectSpotify` | 8038 | no |
| `handleSpotifyRateLimit` | 8072 | no |
| `showRateLimitModal` | 8102 | no |
| `closeRateLimitModal` | 8133 | no |
| `disconnectSpotifyFromRateLimit` | 8143 | no |
| `formatRateLimitDuration` | 8168 | no |
| `authenticateTidal` | 8178 | no |
| `disconnectTidal` | 8193 | no |
| `authenticateDeezer` | 8219 | no |
| `testHiFiConnection` | 8235 | no |
| `testSoundcloudConnection` | 8257 | no |
| `testLidarrConnection` | 8283 | no |
| `_setIndStatusDot` | 8310 | no |
| `testProwlarrConnection` | 8326 | no |
| `updateUsenetClientUI` | 8356 | no |
| `testUsenetClientConnection` | 8372 | no |
| `testTorrentClientConnection` | 8401 | no |
| `loadProwlarrIndexers` | 8430 | no |
| `loadHiFiInstances` | 8464 | no |
| `_initHiFiDragDrop` | 8493 | no |
| `_initHiFiClickHandlers` | 8536 | no |
| `_saveHiFiInstanceOrder` | 8554 | no |
| `toggleHiFiInstance` | 8566 | no |
| `addHiFiInstance` | 8590 | no |
| `restoreDefaultHiFiInstances` | 8617 | no |
| `removeHiFiInstance` | 8641 | no |
| `checkHiFiInstances` | 8657 | no |
| `testDeezerDownloadConnection` | 8696 | no |
| `checkTidalDownloadAuthStatus` | 8726 | no |
| `startTidalDownloadAuth` | 8748 | no |
| `checkQobuzAuthStatus` | 8819 | no |
| `loginQobuzFromConnections` | 8857 | no |
| `loginQobuzWithToken` | 8900 | no |
| `loginQobuzWithTokenFromDownloads` | 8940 | no |
| `loginQobuz` | 8980 | no |
| `logoutQobuz` | 9025 | no |
| `applyPathsEnvironment` | 9052 | no |
| `togglePathLock` | 9069 | no |
| `rebuildPlaylistFolders` | 9090 | no |
| `loadPlexMusicLibraries` | 9152 | no |
| `selectPlexLibrary` | 9200 | no |
| `loadJellyfinUsers` | 9233 | no |
| `selectJellyfinUser` | 9271 | no |
| `loadJellyfinMusicLibraries` | 9306 | no |
| `selectJellyfinLibrary` | 9344 | no |
| `loadNavidromeMusicFolders` | 9377 | no |
| `_paintNavidromePlaylistLogin` | 9413 | no |
| `loadNavidromePlaylistLogin` | 9428 | no |
| `saveNavidromePlaylistLogin` | 9438 | no |
| `clearNavidromePlaylistLogin` | 9463 | no |
| `selectNavidromeMusicFolder` | 9478 | no |
| `loadYtdlpStatus` | 9512 | no |
| `runYtdlpUpdate` | 9551 | no |
| `_imgCacheBytes` | 9606 | no |
| `_imgCacheStatus` | 9612 | no |
| `onImageCacheSettingChanged` | 9621 | no |
| `loadImageCacheStatus` | 9625 | no |
| `runImageCachePrune` | 9659 | no |
| `runImageCacheClear` | 9681 | no |
| `_swApi` | 9715 | yes (step 2) |
| `_swStatus` | 9724 | yes (step 2) |
| `_swRebootSidebar` | 9729 | yes (step 2) |
| `loadSidebarWeatherSettings` | 9747 | yes (step 2) |
| `saveSidebarWeatherLocation` | 9784 | yes (step 2) |
| `clearSidebarWeatherLocation` | 9805 | yes (step 2) |
| `toggleSidebarWeatherEnabled` | 9822 | yes (step 2) |
| `saveSidebarWeatherUnits` | 9838 | yes (step 2) |
| `_msApi` | 9863 | no |
| `_msStatus` | 9872 | no |
| `_msDetail` | 9877 | no |
| `_msEscape` | 9882 | no |
| `metasyncDetect` | 9893 | no |
| `_msMaybeAutoDetect` | 9910 | no |
| `loadMetaSyncSettings` | 9919 | no |
| `saveMetaSyncSettings` | 9950 | no |
| `metasyncPairRequest` | 9976 | no |
| `metasyncPairClaim` | 10005 | no |
| `metasyncHandoff` | 10021 | no |
| `refreshMetaSyncStatus` | 10060 | no |
| `metasyncDisconnect` | 10118 | no |
| `loadMusicBrainzServerSettings` | 10148 | no |
| `updateMusicBrainzRateControls` | 10157 | no |
| `collectMusicBrainzServerSettings` | 10162 | no |

## Tests that read the settings markup or scripts (85)

These pin `settings.js`, `video-settings.js` or `#settings-page` by source or by DOM. Each needs repointing or replacing when the code it reads moves. Mark it when it is done.

| Test file | Repointed |
|---|---|
| `tests/downloads/test_seeding.py` | no |
| `tests/downloads/test_usenet_remove_completed_music.py` | no |
| `tests/downloads/test_youtube_audio_quality.py` | no |
| `tests/imports/test_artist_letter.py` | no |
| `tests/imports/test_compilation_album_artist.py` | no |
| `tests/imports/test_configured_root_folders.py` | no |
| `tests/imports/test_unicode_substitution_1867.py` | no |
| `tests/quality/test_lossless.py` | yes (step 6) |
| `tests/quality/test_quality_profiles_crud.py` | no |
| `tests/test_amazon_retired.py` | no |
| `tests/test_audiobook_settings.py` | no |
| `tests/test_cleanup_protected_roots.py` | no |
| `tests/test_config_persistence.py` | no |
| `tests/test_dashboard_seam.py` | no |
| `tests/test_docs_settings_labels.py` | yes (step 4) |
| `tests/test_download_chain_widget.py` | yes (step 5) |
| `tests/test_explicit_preference.py` | yes (step 5) |
| `tests/test_hybrid_source_unready_ui.py` | no |
| `tests/test_lastfm_username_setting.py` | no |
| `tests/test_listenbrainz_username_setting.py` | no |
| `tests/test_m3u_prefix_rewrite.py` | no |
| `tests/test_m3u_relative_paths.py` | no |
| `tests/test_music_disk_guard.py` | no |
| `tests/test_navidrome_login_endpoints.py` | no |
| `tests/test_preferred_version.py` | yes (step 5) |
| `tests/test_prowlarr_throttle.py` | no |
| `tests/test_quality_panel_layout.py` | no |
| `tests/test_quality_profile_preview_nudge.py` | yes (step 6) |
| `tests/test_script_split_integrity.py` | no |
| `tests/test_settings_never_wipes.py` | no |
| `tests/test_settings_persist_whitelist.py` | no |
| `tests/test_settings_sources_tab.py` | yes (step 4) |
| `tests/test_settings_subtitles_section.py` | no |
| `tests/test_settings_templates_exposed.py` | no |
| `tests/test_settings_textarea_autosave.py` | no |
| `tests/test_sidebar_icon_parity.py` | no |
| `tests/test_soulseek_observed_speed.py` | no |
| `tests/test_source_search_timeout.py` | yes (step 5) |
| `tests/test_subtitle_whisper.py` | no |
| `tests/test_ui_features_1909.py` | no |
| `tests/test_vanilla_globals_resolve.py` | no |
| `tests/test_video_custom_formats.py` | no |
| `tests/test_video_download_config.py` | yes (step 5) |
| `tests/test_video_import_lists.py` | no |
| `tests/test_video_notifications.py` | no |
| `tests/test_video_quality_profiles.py` | no |
| `tests/test_video_seeding.py` | no |
| `tests/test_video_side_shell.py` | no |
| `tests/test_video_title_profile_choice.py` | no |
| `tests/test_youtube_album_bundle_setting.py` | no |
| `tests/test_youtube_cookie_browser_picker.py` | no |
| `tests/test_youtube_failure_is_reported.py` | no |
| `tests/test_ytdlp_update.py` | no |
| `tests/video/test_colon_replacement.py` | no |
| `tests/video/test_usenet_remove_completed.py` | no |
| `tests/video/test_video_recycle.py` | no |
| `tests/video/test_video_seed_rules.py` | no |
| `tests/video/test_youtube_embed_opts.py` | no |
| `webui/src/test/metasync-settings-status.test.ts` | no |
| `webui/src/test/mobile-css-batch2.test.ts` | no |
| `webui/src/test/music-size-limit-settings.test.ts` | yes (step 5, now `.tsx`) |
| `webui/src/test/musicbrainz-settings.test.ts` | no |
| `webui/src/test/oidc-provider-block-toggle.test.ts` | no |
| `webui/src/test/outside-text-is-escaped.test.ts` | no |
| `webui/src/test/quality-release-import-mode.test.ts` | yes (step 6, now `.tsx`) |
| `webui/src/test/settings-autosave-toggle.test.ts` | yes (step 6) |
| `webui/src/test/settings-library-tabs.test.ts` | no |
| `webui/src/test/settings-load-element-drift.test.ts` | yes (step 6) |
| `webui/src/test/settings-navigation.test.ts` | no |
| `webui/src/test/settings-regroup-1822.test.ts` | yes (steps 2, 4, 5) |
| `webui/src/test/settings-save-status-1909.test.ts` | no |
| `webui/src/test/settings-source-autotest.test.ts` | no |
| `webui/src/test/settings-source-tiles-chains.test.ts` | no |
| `webui/src/test/settings-video-library-load.test.ts` | no |
| `webui/src/test/settings-video-tv-libraries.test.ts` | no |
| `webui/src/test/signup-defaults-settings.test.ts` | no |
| `webui/src/test/soulseek-observed-speed-settings.test.ts` | no |
| `webui/src/test/vanilla-crossfile.test.ts` | no |
| `webui/src/test/vanilla-settings-pickers.test.ts` | no |
| `webui/src/test/video-audio-language-chip.test.ts` | no |
| `webui/src/test/video-libraries-2029.test.ts` | no |
| `webui/src/test/video-release-rank-why.test.ts` | no |
| `webui/src/test/video-settings-conn.test.ts` | no |
| `webui/src/test/video-side-settings-save.test.ts` | no |
| `webui/src/test/video-tools-shared-page.test.ts` | no |

## Known traps

- A tab's sections are scattered through `index.html`; find them by `data-stg`, not by position.
- **The whole frontend suite is not green on dev either.** Run on its own, `vitest run src` takes about 15 minutes, times out up to ~16 tests under load, and has ~5 library page tests (`src/routes/library/-ui/`) that fail intermittently on dev. Compare a failure against dev before blaming the port. Do not put a 580-second `timeout` on the whole suite: it gets killed (exit 143).
- `tests/static/*.mjs` and source-pin tests read vanilla files by path and fail honestly when code moves.
- `test_script_split_integrity.py` keeps registries of the vanilla scripts (`SPLIT_MODULES`, `NON_SPLIT_JS`).
- The CSS budget test: no new `<link>` sheet, no new hex or `rgb()` literals, no new `!important`. Run `src/test` after any CSS change.
- `webui/static/dist` is gitignored; run a full `npm run build` after `src` changes or the served page is stale.
- `helper.js` has tour entries keyed by settings selectors (for example `.save-settings`, `#sidebar-visualizer-type`).
- `docs-content/11-settings.js` and `tests/test_docs_settings_labels.py` tie the docs to the labels; labels must not change in a 1:1 port.
- Some settings are device-scoped through localStorage (reduce effects, max performance) and are read from runtime flags on save, not from the tick boxes.
