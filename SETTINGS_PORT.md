# Settings page: port to React

Everything about moving the Settings page from vanilla JS to React. Updated with every change on this branch.

- **Branch:** `settings-react` (worktree `../SoulSync-settings-react`), off dev `52f2190d7`
- **Started:** Nov 14 2026
- **Last updated:** Nov 14 2026 (step 1, the scaffold, is done; no tab is ported yet)

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
- **Loaded on every visit:** `initializeSettings` → `switchSettingsTab('connections')` → `loadSettingsData` → `loadQualityProfile` → `loadApiKeys` → `loadBlacklistCount` (init.js, case `settings`).
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
| Tab: Downloads | not started | 32 controls and buttons |
| Tab: Quality | not started | 67 controls and buttons |
| Tab: Library | not started | 236 controls and buttons |
| Tab: Discovery | not started | 12 controls and buttons |
| Tab: Appearance | not started | 14 controls and buttons |
| Tab: System | not started | 63 controls and buttons |
| Tab: Logs | not started | 10 controls and buttons |
| Video-only panels | not started | counted inside the tabs above |
| Flip route to React, delete the vanilla files | not started | |

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

## Change log

Newest first. One entry per change on the branch.

| Date | Commit | What changed |
|---|---|---|
| Nov 14 | step 1 | **Scaffold.** New: `settings-store.ts`, `host.tsx` and their tests, `settings-react-host-hooks.test.ts`. Changed: `main.tsx` (mounts the host), `settings.js` (three hooks: `switchSettingsTab`, `loadSettingsData`, `saveSettings`). No markup changed, no tab ported. Details under "Completed steps". |
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
| text | `video-slskd-url` | slskd URL: | both | no |
| password | `video-slskd-api-key` | API Key: | both | no |
| number | `video-slskd-search-timeout` | Search Timeout (seconds): | both | no |
| number | `video-slskd-search-timeout-buffer` | Search Timeout Buffer (seconds): | both | no |
| number | `video-slskd-search-min-delay` | Minimum Delay Between Searches (seconds): | both | no |
| number | `video-slskd-min-peer-speed` | Minimum Peer Upload Speed (Mbps): | both | no |
| number | `video-slskd-max-peer-queue` | Max Peer Queue Length: | both | no |
| number | `video-slskd-download-timeout` | Download Timeout (minutes): | both | no |
| checkbox | `video-slskd-auto-clear` | Auto-clear slskd search history | both | no |

**Source Settings** (both, 4)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| select | `download-source-mode` | Download Source: {Soulseek Only / YouTube Only / Tidal Only / Qobuz Only / HiFi Only (Free Lossless) / Deezer  | both | no |
| search | `dlchain-filter` |  | both | no |
| select | `hybrid-primary-source` | {Soulseek / YouTube / Tidal / Qobuz / HiFi / Deezer / SoundCloud} | both | no |
| select | `hybrid-secondary-source` | {Soulseek / YouTube / Tidal / Qobuz / HiFi / Deezer / SoundCloud} | both | no |

**Download Behaviour** (both, 4)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| select | `stream-source` | Stream / Preview Source: {YouTube (Instant) / Active Download Source / Deezer 30s Preview (Instant)} | both | no |
| select | `max-concurrent-downloads` | Concurrent Downloads: {1 / 2 / 3 (Default) / 4 / 5 / 6 / 8 …} | both | no |
| number | `source-search-timeout` | Source search timeout (seconds): | both | no |
| button | `test-all-sources-btn` | Test all sources | both | no |

**Retry Logic** (both, 5)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| checkbox | `retry-next-candidate` | Retry next-best candidate on mismatch | both | no |
| checkbox | `retry-exhaustive` | Exhaustive retry (separate budget per source) | both | no |
| number | `retries-per-query` | Retries per query (per source) | both | no |
| checkbox | `accept-version-mismatch-fallback` | Accept best version mismatch as last resort | both | no |
| number | `version-mismatch-min-count` | Minimum matching mismatches before accepting | both | no |

**Album Publishing** (both, 2)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| checkbox | `album-atomic-publish` | Publish albums only when complete | both | no |
| checkbox | `album-atomic-recover` | Recover interrupted albums on startup | both | no |

**Filtering** (both, 8)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| checkbox | `allow-explicit` | Allow explicit content in downloads | both | no |
| checkbox | `prefer-explicit` | Prefer explicit versions when matching | both | no |
| checkbox | `exclude-clean` | Never download clean versions | both | no |
| number | `music-max-mb-per-minute` | Maximum music download size (MB/min) | both | no |
| select | `preferred-version` | Prefer a version when one exists {Off — take what the source asked for / Extended mix / long version / Radio e | both | no |
| checkbox | `genre-whitelist-enabled` | Enable strict genre filtering | both | no |
| text | `genre-whitelist-search` |  | both | no |
| button |  | Reset to Defaults | both | no |

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
| button |  | 🎧 Audiophile | both | no |
| button |  | ⚖️ Balanced | both | no |
| button |  | 💾 Space Saver | both | no |
| button |  | ↺ Reset to defaults | both | no |
| select | `rt-add-format` | {All lossless / FLAC / ALAC / WAV / AIFF / DSD (DSF / DFF) / All lossy / MP3 …} | both | no |
| select | `rt-add-bitdepth` | {Any bit depth / 16-bit / 24-bit} | both | no |
| select | `rt-add-samplerate` | {Any sample rate / ≥ 44.1 kHz / ≥ 48 kHz / ≥ 96 kHz / ≥ 192 kHz} | both | no |
| select | `rt-add-bitrate` | {Any bitrate / ≥ 96 kbps / ≥ 128 kbps / ≥ 192 kbps / ≥ 256 kbps / ≥ 320 kbps / Custom…} | both | no |
| number | `rt-add-bitrate-custom` | ≥ kbps | both | no |
| button |  | + Add target | both | no |
| select | `quality-upgrade-policy` | Upgrade until {No upgrades / Any accepted target / Selected target or better} | both | no |
| select | `quality-upgrade-cutoff` | Upgrade until {} | both | no |
| select | `quality-search-mode` | Search strategy {Source priority — fast (use the first source that has it) / Best quality — thorough (check ev | both | no |
| checkbox | `quality-rank-candidates` | Rank-based download order | both | no |
| checkbox | `quality-fallback-enabled` | Accept off-list quality when nothing in the list is available | both | no |

**Quality on Import** (both, 1)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| checkbox | `import-replace-lower-quality` | Replace lower quality files on import | both | no |

**Album Releases** (both, 1)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| select | `quality-release-import-mode` | Import from album releases {Requested tracks only (default) / Also the album's other tracks that pass} | both | no |

**Post-Download Conversion** (both, 5)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| checkbox | `downsample-hires` | Downsample Hi-Res FLAC to CD quality (16-bit / 44.1kHz) | both | no |
| checkbox | `lossy-copy-enabled` | Create lossy copy of downloaded FLAC files | both | no |
| select | `lossy-copy-codec` | Codec: {MP3 / Opus / AAC (M4A)} | both | no |
| select | `lossy-copy-bitrate` | Bitrate: {320 kbps / 256 kbps / 192 kbps / 128 kbps} | both | no |
| checkbox | `lossy-copy-delete-original` | Keep lossy output only — delete the lossless source after conversion | both | no |

**Audio Verification** (both, 3)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| checkbox | `acoustid-require-verified` | Only allow AcoustID-verified tracks for imports and downloads | both | no |
| checkbox | `audio-completeness-check` | Verify real audio with ffmpeg | both | no |
| checkbox | `verify-flac-decode` | Verify FLAC decode with flac -t | both | no |

**Quality Profiles** (both, 2)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| button |  | Manage | both | no |
| button | `qp-profile-new-btn` | + New profile | both | no |

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
| checkbox | `m3u-export-enabled` | Auto-save M3U file when downloading playlists | both | no |
| checkbox | `library-m3u-enabled` | Auto-generate a whole-library M3U (updates on every scan) | both | no |
| text | `library-m3u-path` | Whole-library M3U output folder: | both | no |
| select | `playlist-sync-mode` | Sync mode: {Replace — recreate the playlist each sync / Reconcile — edit in place, keep image & description (d | both | no |
| checkbox | `playlist-sync-wishlist-missing` | Add missing tracks to the wishlist during sync | both | no |
| checkbox | `create-backup` | Keep a "<name> Backup" copy on the server before a Replace sync | both | no |

**Listening Stats** (both, 2)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| checkbox | `listening-stats-enabled` | Enable listening stats collection from media server | both | no |
| number | `listening-stats-interval` | Poll Interval (minutes): | both | no |

**Discovery** (both, 1)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| range | `discover-adventurousness` | Adventurousness | both | no |

**Discovery Pool Settings** (music only, 2)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| select | `discovery-lookback-period` | Lookback Period: {Last 7 days / Last 30 days (Default) / Last 90 days / Last 6 months / Entire discography} | music only | no |
| select | `discovery-hemisphere` | Hemisphere: {Northern Hemisphere / Southern Hemisphere} | music only | no |

**Import Lists** (video only, 1)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| button |  | + Add list | video only | no |

### Appearance (14)

**Show only what you use** (both, 1)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| button | `settings-open-feature-picker` | Customize SoulSync | both | no |

**UI Appearance** (both, 7)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| select | `accent-preset` | Accent Color: {Spotify Green / Ocean Blue / Purple / Boulder Purple / Sunset Orange / Rose / Teal …} | both | no |
| color | `accent-custom-color` | Custom Color: | both | no |
| select | `sidebar-visualizer-type` | Sidebar Visualizer: {Bars / Wave / Spectrum / Mirror / Equalizer / None} | both | no |
| checkbox | `particles-enabled` | Background Particles | both | no |
| checkbox | `worker-orbs-enabled` | Worker Orbs | both | no |
| checkbox | `reduce-effects-enabled` | Reduce Visual Effects | both | no |
| checkbox | `max-performance-enabled` | Max Performance | both | no |

**Sidebar Weather** (both, 6)

| Kind | id | Label | Side | Ported |
|---|---|---|---|---|
| text | `sw-location` | Location: | both | no |
| button |  | Save | both | no |
| button |  | Clear | both | no |
| checkbox | `sw-enabled` | Show weather in the sidebar | both | no |
| checkbox | `sw-holidays` | Holiday decorations | both | no |
| checkbox | `sw-celsius` | Use °C instead of °F | both | no |

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
| select | `log-viewer-source` | {app.log / post_processing.log / acoustid.log / source_reuse.log} | both | no |
| button |  | All | both | no |
| button |  | Debug | both | no |
| button |  | Info | both | no |
| button |  | Warn | both | no |
| button |  | Error | both | no |
| text | `log-viewer-search` |  | both | no |
| button |  | Copy | both | no |
| button |  | Clear | both | no |
| checkbox | `log-viewer-autoscroll` | Auto-scroll | both | no |

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
| `syncAcoustidRequireVerifiedVisibility` | 46 | no |
| `syncPreferExplicitState` | 55 | no |
| `syncRetryConditionalRows` | 72 | no |
| `syncOidcProviderBlock` | 89 | no |
| `_qpHandleProfileControlChange` | 116 | no |
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
| `updateLossyBitrateOptions` | 3236 | no |
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
| `_qpDefaultProfileId` | 4673 | no |
| `debouncedSaveQualityProfile` | 4680 | no |
| `loadQualityProfile` | 4700 | no |
| `rtLabel` | 4722 | no |
| `populateQualityProfileUI` | 4733 | no |
| `onSearchModeChange` | 4771 | no |
| `renderUpgradeCutoffOptions` | 4777 | no |
| `onUpgradePolicyChange` | 4794 | no |
| `_settingHelpBody` | 4805 | no |
| `toggleSettingHelp` | 4828 | no |
| `renderRankedTargets` | 4840 | no |
| `reorderRankedTarget` | 4884 | no |
| `moveRankedTarget` | 4891 | no |
| `deleteRankedTarget` | 4899 | no |
| `rtSelectionIsLossless` | 4914 | no |
| `onRtAddFormatChange` | 4918 | no |
| `onRtBitrateChange` | 4928 | no |
| `addRankedTarget` | 4935 | no |
| `applyQualityPreset` | 4973 | no |
| `resetActiveQualityPreset` | 5047 | no |
| `collectQualityProfileFromUI` | 5088 | no |
| `saveQualityProfile` | 5115 | no |
| `_qpSetProfileRows` | 5182 | no |
| `normalizeQualityProfileId` | 5189 | no |
| `loadCustomQualityProfiles` | 5197 | no |
| `qpProfileSummary` | 5220 | no |
| `renderCustomQualityProfiles` | 5251 | no |
| `qpStartRename` | 5394 | no |
| `qpShowNewProfileInput` | 5455 | no |
| `qpResetNewProfileControl` | 5487 | no |
| `collectFullQualityBundleFromUI` | 5498 | no |
| `applyFullQualityBundleToDom` | 5517 | no |
| `saveCurrentAsQualityProfile` | 5537 | no |
| `updateCustomQualityProfile` | 5581 | no |
| `applyCustomQualityProfile` | 5610 | no |
| `confirmSetDefaultQualityProfile` | 5647 | no |
| `confirmSetDefaultQualityProfileById` | 5660 | no |
| `previewQualityProfile` | 5669 | no |
| `qpShowEditingBanner` | 5705 | no |
| `qpHideEditingBanner` | 5720 | no |
| `deleteCustomQualityProfile` | 5725 | no |
| `toggleAutoImportQualityProfile` | 5764 | no |
| `qpManagerOverlay` | 5796 | no |
| `renderQualityProfileManager` | 5847 | no |
| `clearAutoImportQualityProfileFromManager` | 5891 | no |
| `openQualityProfileManager` | 5895 | no |
| `closeQualityProfileManager` | 5907 | no |
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
| `_genreWhitelistRender` | 6223 | no |
| `_initGenreChipClickHandler` | 6242 | no |
| `_genreWhitelistRemove` | 6254 | no |
| `_genreWhitelistAdd` | 6260 | no |
| `_genreWhitelistReset` | 6270 | no |
| `_collectGenreWhitelist` | 6311 | no |
| `_logClassify` | 6322 | no |
| `_logClassToCSS` | 6336 | no |
| `_logViewerInit` | 6340 | no |
| `_logViewerStop` | 6370 | no |
| `_logViewerOnLive` | 6379 | no |
| `_logViewerAppendLines` | 6395 | no |
| `_logViewerChangeSource` | 6425 | no |
| `_logViewerFilterLevel` | 6433 | no |
| `_logViewerOnSearch` | 6441 | no |
| `_logViewerReload` | 6449 | no |
| `_logViewerCopy` | 6456 | no |
| `_logViewerClear` | 6474 | no |
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
| `_swApi` | 9715 | no |
| `_swStatus` | 9724 | no |
| `_swRebootSidebar` | 9729 | no |
| `loadSidebarWeatherSettings` | 9747 | no |
| `saveSidebarWeatherLocation` | 9784 | no |
| `clearSidebarWeatherLocation` | 9805 | no |
| `toggleSidebarWeatherEnabled` | 9822 | no |
| `saveSidebarWeatherUnits` | 9838 | no |
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
| `tests/quality/test_lossless.py` | no |
| `tests/quality/test_quality_profiles_crud.py` | no |
| `tests/test_amazon_retired.py` | no |
| `tests/test_audiobook_settings.py` | no |
| `tests/test_cleanup_protected_roots.py` | no |
| `tests/test_config_persistence.py` | no |
| `tests/test_dashboard_seam.py` | no |
| `tests/test_docs_settings_labels.py` | no |
| `tests/test_download_chain_widget.py` | no |
| `tests/test_explicit_preference.py` | no |
| `tests/test_hybrid_source_unready_ui.py` | no |
| `tests/test_lastfm_username_setting.py` | no |
| `tests/test_listenbrainz_username_setting.py` | no |
| `tests/test_m3u_prefix_rewrite.py` | no |
| `tests/test_m3u_relative_paths.py` | no |
| `tests/test_music_disk_guard.py` | no |
| `tests/test_navidrome_login_endpoints.py` | no |
| `tests/test_preferred_version.py` | no |
| `tests/test_prowlarr_throttle.py` | no |
| `tests/test_quality_panel_layout.py` | no |
| `tests/test_quality_profile_preview_nudge.py` | no |
| `tests/test_script_split_integrity.py` | no |
| `tests/test_settings_never_wipes.py` | no |
| `tests/test_settings_persist_whitelist.py` | no |
| `tests/test_settings_sources_tab.py` | no |
| `tests/test_settings_subtitles_section.py` | no |
| `tests/test_settings_templates_exposed.py` | no |
| `tests/test_settings_textarea_autosave.py` | no |
| `tests/test_sidebar_icon_parity.py` | no |
| `tests/test_soulseek_observed_speed.py` | no |
| `tests/test_source_search_timeout.py` | no |
| `tests/test_subtitle_whisper.py` | no |
| `tests/test_ui_features_1909.py` | no |
| `tests/test_vanilla_globals_resolve.py` | no |
| `tests/test_video_custom_formats.py` | no |
| `tests/test_video_download_config.py` | no |
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
| `webui/src/test/music-size-limit-settings.test.ts` | no |
| `webui/src/test/musicbrainz-settings.test.ts` | no |
| `webui/src/test/oidc-provider-block-toggle.test.ts` | no |
| `webui/src/test/outside-text-is-escaped.test.ts` | no |
| `webui/src/test/quality-release-import-mode.test.ts` | no |
| `webui/src/test/settings-autosave-toggle.test.ts` | no |
| `webui/src/test/settings-library-tabs.test.ts` | no |
| `webui/src/test/settings-load-element-drift.test.ts` | no |
| `webui/src/test/settings-navigation.test.ts` | no |
| `webui/src/test/settings-regroup-1822.test.ts` | no |
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
- `tests/static/*.mjs` and source-pin tests read vanilla files by path and fail honestly when code moves.
- `test_script_split_integrity.py` keeps registries of the vanilla scripts (`SPLIT_MODULES`, `NON_SPLIT_JS`).
- The CSS budget test: no new `<link>` sheet, no new hex or `rgb()` literals, no new `!important`. Run `src/test` after any CSS change.
- `webui/static/dist` is gitignored; run a full `npm run build` after `src` changes or the served page is stale.
- `helper.js` has tour entries keyed by settings selectors (for example `.save-settings`, `#sidebar-visualizer-type`).
- `docs-content/11-settings.js` and `tests/test_docs_settings_labels.py` tie the docs to the labels; labels must not change in a 1:1 port.
- Some settings are device-scoped through localStorage (reduce effects, max performance) and are read from runtime flags on save, not from the tick boxes.
