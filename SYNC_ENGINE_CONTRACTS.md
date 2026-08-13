# sync port — engine interface contracts (P1a)

The window.* seams the React sync page calls. Companion to SYNC_PORT_AUDIT.md;
produced by the P1a interface read of downloads.js / core.js /
shared-helpers.js / init.js. Keep until the port ships.

**Script load order** (`webui/index.html` 8363-8410): `fetch-dedupe` → `socket.io` → `setup-wizard` → **core.js** → **shared-helpers.js** → `media-player` → `settings` → `sync-spotify` → **downloads.js** → … → `wishlist-tools` → … → `sync-services` → `sync-listenbrainz` → … → `stats-automations` → … → **init.js**. All are classic scripts, so top-level `function` declarations *are* `window.*` properties, but top-level `let`/`const` (e.g. `activeDownloadProcesses`, `socket`, `playlistTrackCache`, `sequentialSyncManager`, `WishlistModalState`) are **script-scoped and NOT reachable from a module** — those need an explicit bridge.

**Duplicate declarations (later file wins at load time):**
- `escapeHtml` — `shared-helpers.js:3961` then `downloads.js:5624`. Bodies identical; **downloads.js wins**.
- `formatDuration` — `sync-spotify.js:1967`, `wishlist-tools.js:1575`, `sync-services.js:10036`. **sync-services.js wins** (loads last, line 8382).
- `_escAttr` — `downloads.js:4665` then `stats-automations.js:5771`. **stats-automations.js wins** — and the two have *different* semantics (see §3).

---

## 1. `webui/static/downloads.js`

### `openDownloadMissingModalForYouTube`
`webui/static/downloads.js:429`

```js
async function openDownloadMissingModalForYouTube(virtualPlaylistId, playlistName, spotifyTracks, artist = null, album = null)
```

**Params**
- `virtualPlaylistId: string` — used both as the `activeDownloadProcesses` key and as the DOM id suffix. Prefix drives source labelling: `beatport_`→Beatport, `tidal_`→Tidal, `listenbrainz_`→ListenBrainz, `spotify_public_`/`spotify:`→Spotify, `discover_`/`seasonal_`/`spotify_library_`/`build_playlist_`/`decade_`/`build_playlist_custom`→SoulSync, else YouTube.
- `playlistName: string` — displayed in hero; stored as `process.playlist.name`.
- `spotifyTracks: Array<Track>` — fields read directly: `.name`, `.artists` (via `formatArtists` — array of strings or `{name}` objects), `.duration_ms`, `.album.images[0].url` (only for the discover-metadata image fallback).
- `artist: object|null` — fields read: `.name`, `.id` (falls back `.artist_id`), `.source`, `.image_url`. Only consumed when `isDiscoverAlbum` and `album` are both truthy.
- `album: object|null` — fields read: `.name`, `.album_type` (default `'album'`), `.images` (array of `{url}`), `.source`.

**Returns** `Promise<void>`. No value; success/failure is observable only via `activeDownloadProcesses[virtualPlaylistId]` existing.

**Side effects**
- Calls `showLoadingOverlay('Loading YouTube playlist...')` first, `hideLoadingOverlay()` on every exit path.
- **Early-return path** (entry already in `activeDownloadProcesses`): shows `process.modalElement` (`display:'flex'`), toasts `'Showing previous results…'` if `status === 'complete'`, awaits `refreshOrganizePreferenceForDownloadModal(virtualPlaylistId)` if defined, then returns.
- Mutates globals: `playlistTrackCache[virtualPlaylistId] = spotifyTracks`; `currentPlaylistTracks = spotifyTracks`; `currentModalPlaylistId = virtualPlaylistId`; creates `activeDownloadProcesses[virtualPlaylistId]` (see §2 for full shape).
- Appends to `document.body` a `<div id="download-missing-modal-${virtualPlaylistId}" class="download-missing-modal">`, ends with `style.display='flex'`.
- **DOM ids created** (all suffixed `-${virtualPlaylistId}`): `analysis-progress-text-`, `analysis-progress-fill-`, `download-progress-text-`, `download-progress-fill-`, `track-selection-count-`, `select-all-`, `download-tracks-tbody-`, `force-download-all-`, `playlist-folder-mode-` (omitted when `isDiscoverAlbum`), `begin-analysis-btn-`, `add-to-wishlist-btn-`, `cancel-all-btn-`. Per-row: `match-${id}-${index}`, `download-${id}-${index}`, `actions-${id}-${index}`. Hero section (from `generateDownloadModalHeroSection`, `sync-spotify.js:1993`) additionally creates `stat-found-${id}` and `stat-missing-${id}` — **required**, `processModalStatusUpdate` dereferences them unguarded.
- **Classes**: `.download-missing-modal`, `.download-missing-modal-content[data-context="playlist"]`, `.download-missing-modal-header/-body/-footer`, `.download-progress-section`, `.progress-item`, `.progress-bar`, `.progress-fill.analysis`/`.download`, `.download-tracks-table`, `.track-select-cb`, `.track-select-all`, `.force-download-toggle-container`, `.download-control-btn`.
- Inline `onclick`s bind to globals: `toggleAllTrackSelections`, `updateTrackSelectionCount`, `startMissingTracksProcess`, `addModalTracksToWishlist`, `cancelAllOperations`, `exportPlaylistAsM3U`, `closeDownloadMissingModal`, `playDownloadModalTrack`.
- Calls `applyProgressiveTrackRendering(virtualPlaylistId, spotifyTracks.length)` and fire-and-forget `hydrateDownloadModalQualityProfileSelect(virtualPlaylistId)`.
- No network calls of its own (the quality-profile hydrator fetches).

**Preconditions**
- `#loading-overlay` (with a `.loading-message` child) must exist — `showLoadingOverlay` dereferences both unguarded.
- Globals that must exist: `activeDownloadProcesses`, `playlistTrackCache`, `currentPlaylistTracks`, `currentModalPlaylistId`.
- Functions that must exist: `generateDownloadModalHeroSection`, `downloadModalQualityProfileSelectHtml`, `hydrateDownloadModalQualityProfileSelect`, `applyProgressiveTrackRendering`, `renderModalTrackPlayButton`, `escapeHtml`, `formatArtists`, `formatDuration`, `showToast`.

---

### `startMissingTracksProcess`
`downloads.js:1650`

```js
async function startMissingTracksProcess(playlistId)
```

**Params** — `playlistId: string`, must key an existing `activeDownloadProcesses` entry (silent `return` otherwise).

**Reads from the process entry**: `.tracks` (array), `.playlist.name`, `.album` (`.name`), `.artist` (`.name`), `.discoverMetadata` (`.imageUrl`, `.type`).

**Returns** `Promise<void>`.

**Side effects**
- Sets `process.status = 'running'`, then `process.batchId = data.batch_id` on success.
- Calls `updatePlaylistCardUI(playlistId)` and `updateRefreshButtonState()`.
- **DOM** (unguarded, will throw if missing): hides `#begin-analysis-btn-${playlistId}`, shows `#cancel-all-btn-${playlistId}`. Guarded: hides `#add-to-wishlist-btn-${playlistId}`, hides the `.force-download-toggle-container` ancestor of `#force-download-all-${playlistId}`, disables all `.track-select-cb` in `#download-tracks-tbody-${playlistId}` and `#select-all-${playlistId}`.
- Reads checkboxes: `#force-download-all-${playlistId}`, `#skip-acoustid-${playlistId}` (absent ⇒ false), organize state via `isPlaylistOrganizeEnabled(playlistId)` else `#playlist-folder-mode-${playlistId}`.
- **Prefix-dispatched state mutation + backend phase POSTs** (all fire-and-forget except where noted):
  - `artist_album_` → `setAlbumDownloadingStatus(albumId, 0, totalTracks)`.
  - `youtube_` → `updateYouTubeCardPhase(urlHash,'downloading')`, plus `updateMirroredCardPhase` if `urlHash` starts `mirrored_`.
  - `tidal_` → mutates `tidalPlaylistStates[id].phase`, `updateTidalCardPhase`.
  - `beatport_` → mutates `youtubePlaylistStates[urlHash].phase` and `beatportChartStates[chartHash].phase`, `updateBeatportCardPhase`, POST `/api/beatport/charts/update-phase/${chartHash}` (twice: phase, then again with `download_process_id`).
  - `spotify_public_` → mutates `spotifyPublicPlaylistStates[urlHash]` (`.phase`, `.convertedSpotifyPlaylistId`), `updateSpotifyPublicCardPhase`, POST `/api/spotify-public/update_phase/${urlHash}`.
  - `deezer_` → mutates `deezerPlaylistStates[id]`, `updateDeezerCardPhase`, POST `/api/deezer/update_phase/${id}`.
  - `listenbrainz_` → mutates `listenbrainzPlaylistStates[mbid]` (`.phase`, `.download_process_id`, `.convertedSpotifyPlaylistId`), POST `/api/listenbrainz/update-phase/${mbid}` (twice).
- If `process.discoverMetadata` present → `addDiscoverDownload(playlistId, process.playlist.name, type, imageUrl)` (now a React-owned `window.*` global from `src/routes/discover/-discover.download-bar.ts`).
- **Primary network call**: `POST /api/playlists/${playlistId}/start-missing-process`, body:
  ```js
  { tracks, force_download_all, ignore_manual_matches, wing_it, skip_acoustid,
    quality_profile_id, source,                       // source may be undefined
    playlist_name,
    // album/search-track context branch:
    is_album_download, album_context, artist_context,
    // playlist branch:
    playlist_folder_mode,
    // only on blocklist-override retry:
    ignore_blocklist: true }
  ```
  `tracks` are `process.tracks` filtered by checked `.track-select-cb`, each stamped with `_original_index`. `wing_it` comes from `youtubePlaylistStates[playlistId]?.wing_it`. Album-context prefixes: `artist_album_`, `enhanced_search_album_`, `discover_album_`, `seasonal_album_`, `spotify_library_`, `issue_download_`, `library_redownload_`, `beatport_release_`; search-track prefixes: `enhanced_search_track_`, `gsearch_track_`.
- If response `{blocked:true}` → `confirm()` via `confirmBlockedDownload(data)` (reads `.blocked_entity_type`, `.blocked_name`); on decline toasts and returns, on accept re-POSTs with `ignore_blocklist:true`.
- HTTP 429 → thrown as "…Try closing some other download processes first."
- **Starts the poller**: `startModalDownloadPolling(playlistId)`.
- **On error**: toast, `process.status = 'cancelled'`, restores button visibility, then `cleanupDownloadProcess(playlistId)` — which **deletes the entry, removes the modal element from DOM, and POSTs `/api/playlists/cleanup_batch`**.

**Preconditions** — `activeDownloadProcesses[playlistId]` exists with `.tracks` and `.playlist`; `#begin-analysis-btn-${playlistId}` and `#cancel-all-btn-${playlistId}` exist in DOM.

---

### `closeDownloadMissingModal`
`downloads.js:653`

```js
async function closeDownloadMissingModal(playlistId)
```

**Returns** `Promise<void>`.

**Behaviour — three branches**
1. **No process entry**: removes `#download-missing-modal-${playlistId}` from its parent if found, returns.
2. **`process.status === 'running'`**: only hides (`modalElement.style.display='none'`). If `playlistId === 'wishlist'` also calls `WishlistModalState.setUserClosed()`. Nothing else happens — the process, poller and batch survive.
3. **Otherwise (idle/complete/cancelled)**: full teardown —
   - `youtube_` → `updateYouTubeCardPhase(hash,'discovered')` (+ `updateMirroredCardPhase` for `mirrored_`), `await POST /api/youtube/update_phase/${hash}` `{phase:'discovered'}`.
   - `beatport_` → reads `youtubePlaylistStates[hash].is_beatport_playlist`/`.beatport_chart_hash`; if phase ≠ `download_complete`: `updateBeatportCardPhase`, mutates `state.phase` and `beatportChartStates[chartHash].phase`, `await POST /api/beatport/charts/update-phase/${chartHash}`.
   - `tidal_` → preserves `{playlist, discovery_results, spotify_matches, discovery_progress, convertedSpotifyPlaylistId}`, deletes `download_process_id` + `phase`, restores + sets `phase='discovered'`, `updateTidalCardPhase`, `await POST /api/tidal/update_phase/${id}`.
   - `listenbrainz_` → if phase ≠ `download_complete`, deletes `download_process_id` + `convertedSpotifyPlaylistId`, sets `phase='discovered'`, `await POST /api/listenbrainz/update-phase/${mbid}`.
   - `spotify_public_` → same preserve/reset pattern, `updateSpotifyPublicCardPhase`, `await POST /api/spotify-public/update_phase/${hash}`.
   - `deezer_` → same pattern, `updateDeezerCardPhase`, `await POST /api/deezer/update_phase/${id}`.
   - `playlistId === 'wishlist'` → `WishlistModalState.clear()`.
   - `artist_album_` → `cleanupArtistDownload(playlistId)`; `enhanced_search_` → `cleanupSearchDownload`; `beatport_chart_`/`beatport_release_` → `cleanupBeatportDownload`.
   - `discoverDownloads[playlistId]` present → `removeDiscoverDownload(playlistId)`.
   - `await handlePostDownloadAutomation(playlistId, process)`.
   - `cleanupDownloadProcess(playlistId)` — clears `process.poller`, POSTs `/api/playlists/cleanup_batch` `{batch_id}` (202 ⇒ retried after 2s), removes `modalElement` from DOM, `delete activeDownloadProcesses[playlistId]`, `checkAndCleanupGlobalPolling()`, `updatePlaylistCardUI(playlistId)` (skipped for `'wishlist'`), `updateRefreshButtonState()`.

**Preconditions** — the per-source state maps must exist as globals: `youtubePlaylistStates`, `beatportChartStates`, `tidalPlaylistStates`, `listenbrainzPlaylistStates`, `spotifyPublicPlaylistStates`, `deezerPlaylistStates`, `discoverDownloads`, `WishlistModalState`.

---

### `startModalDownloadPolling`
`downloads.js:3442`

```js
function startModalDownloadPolling(playlistId)
```

**Returns** `undefined`. Silent no-op if `!activeDownloadProcesses[playlistId]` **or `!process.batchId`** — the batch id is a hard precondition.

**Side effects**
- Clears any existing `process.poller` interval, sets it `null`.
- Sets `process.status = 'running'`.
- Calls `startGlobalDownloadPolling()` (`downloads.js:2107`) — idempotent; starts a single module-level `globalDownloadStatusPoller` `setInterval` at 2000 ms (`globalPollingBaseInterval`) that:
  - skips while `document.hidden`;
  - collects every `activeDownloadProcesses` entry with `.batchId` and `status` `'running'` **or** `'complete'`;
  - if a visible idle `'wishlist'` modal exists, `GET /api/active-processes` and `rehydrateModal(serverWishlistProcess, false)`;
  - `GET /api/download_status/batch?batch_ids=…&batch_ids=…` and feeds each batch to `processModalStatusUpdate(playlistId, statusData)`;
  - on failure increments `globalPollingFailureCount` and restarts itself with exponential backoff capped at 8000 ms via `startGlobalDownloadPollingWithInterval`.
- Calls `ensureLegacyCompatibility(playlistId)` → `createLegacyPoller` installs a **dummy 5000 ms `setInterval` on `process.poller`** purely so legacy `clearInterval(process.poller)` cleanup paths keep working.

**Also note** — the same batch data arrives over WebSocket via `socket.on('downloads:batch_update', handleDownloadBatchUpdate)` (core.js:1048), which looks up the playlist by `process.batchId` and calls the identical `processModalStatusUpdate`. HTTP polling is deliberately kept running alongside.

**`processModalStatusUpdate(playlistId, data)`** (`downloads.js:2886`) — the payload the poller feeds back:
`{ error?, phase: 'queued'|'analysis'|'album_downloading'|'downloading'|'complete'|'error'|'cancelled', analysis_progress:{processed,total}, analysis_results:[{track_index,found}], album_bundle, tasks:[{task_id,track_index,status,error_message,quarantine_entry_id,…}], auto_initiated?, wishlist_summary? }`.

---

### `startSequentialSync` + `sequentialSyncManager`
`downloads.js:4060` / class at `core.js:1208`, instance global `let sequentialSyncManager = null` at `core.js:388`.

```js
function startSequentialSync()   // no params
```

**Returns** `undefined`.

**Side effects**
- Lazily assigns `sequentialSyncManager = new SequentialSyncManager()`.
- **Toggle semantics**: if `sequentialSyncManager.isRunning` → calls `.cancel()` and returns. (The same button is Start and Cancel.)
- If `selectedPlaylists.size === 0` → `showToast('No playlists selected for sync','error')` and returns.
- Builds the ordered id list by walking `document.querySelectorAll('.playlist-card')` and taking `card.dataset.playlistId` present in `selectedPlaylists` — **DOM order defines run order**.
- `showSyncSidebar()` — sets `.sync-sidebar` `display:''` and `.sync-content-area` `gridTemplateColumns:'2.5fr 0.75fr'`, only when `window.innerWidth > 1300`.
- `sequentialSyncManager.start(orderedPlaylistIds)`.
- `disablePlaylistSelection(true)` — sets `.disabled` on every `.playlist-checkbox`.

**Bound at** `sync-services.js:3971`, inside `initializeSyncPage()`: `document.getElementById('start-sync-btn').addEventListener('click', startSequentialSync)` — same named reference each init, so the browser dedupes (no stacking).

#### `SequentialSyncManager` public surface
| Member | Type | Contract |
|---|---|---|
| `queue` | `string[]` | playlist ids; emptied on `complete()`/`cancel()` |
| `currentIndex` | `number` | 0-based index into `queue`; reset to 0 on stop |
| `isRunning` | `boolean` | the canonical "is a sequential sync live" flag; read by `hasActiveOperations()` and `updateRefreshButtonState()` |
| `startTime` | `number\|null` | `Date.now()` at `start`, `null` when idle |
| `start(playlistIds)` | `void` | warns and returns if already `isRunning`; sets state, calls `updateUI()` then `syncNext()` |
| `syncNext()` | `async void` | `await startPlaylistSync(id)`, then `await waitForSyncCompletion(id)`, `currentIndex++`, `setTimeout(()=>this.syncNext(),1000)`. Looks up display name in the global `spotifyPlaylists` array (`p.id`/`p.name`). Errors are toasted, not thrown. |
| `waitForSyncCompletion(playlistId)` | `Promise<void>` | resolves when `activeSyncPollers[playlistId]` is absent; polls itself every 1000 ms |
| `complete()` | `void` | clears state, `disablePlaylistSelection(false)`, `updateUI()`, `updateRefreshButtonState()`, success toast, `hideSyncSidebar()` |
| `cancel()` | `void` | no-op if `!isRunning`; same teardown as `complete()`, info toast. Does **not** abort the in-flight single sync. |
| `updateUI()` | `void` | see below |

**`updateUI()` DOM contract** — touches exactly two ids:
- `#start-sync-btn`: idle ⇒ `textContent='Start Sync'`, `disabled = (selectedPlaylists.size === 0)`. Running ⇒ `textContent='Cancel Sequential Sync'`, `disabled=false`.
- `#selection-info`: idle ⇒ `'Select playlists to sync'` or `'${n} playlist(s) selected'`. Running ⇒ `` `Syncing ${currentIndex+1}/${queue.length}: ${name}` ``, name resolved from `spotifyPlaylists`, `'Unknown'` fallback.
Both lookups are null-guarded.

**Preconditions** — globals `selectedPlaylists` (`Set<string>`), `spotifyPlaylists` (array), `activeSyncPollers`, functions `startPlaylistSync`, `disablePlaylistSelection`, `updateRefreshButtonState`, `hideSyncSidebar`, `showToast`.

---

### `updateCardToSyncing`
`downloads.js:4139`

```js
function updateCardToSyncing(playlistId, percent, progress = null)
```

**Params**
- `playlistId: string` — matched via `.playlist-card[data-playlist-id="${playlistId}"]`.
- `percent: number` — used only when `progress` is null/has no `total_tracks`.
- `progress: object|null` — fields read: `.matched_tracks`, `.failed_tracks`, `.total_tracks`, `.current_step`, `.current_track`. When `total_tracks > 0`, the displayed percent is **recomputed** as `round((matched+failed)/total*100)`, ignoring `percent` and `progress.progress`.

**Returns** `undefined`. Early-returns if the card is not in the DOM.

**Side effects** — writes only inside `.sync-progress-indicator` of that card: sets `display:'block'` and replaces `innerHTML` with an optional `.playlist-card-sync-status` block (`.sync-stat.total-tracks` / `.matched-tracks` / `.failed-tracks` / `.percentage`, `.sync-separator`) plus `.progress-bar-sync > .progress-fill-sync` (inline `width`) and `.progress-text-sync`. No globals, no network.

**Precondition (unguarded)** — the card **must** contain `.sync-progress-indicator`; `card.querySelector(...)` result is dereferenced without a null check, so a card rendered without it throws `TypeError`.

---

### `startSyncPolling`
`downloads.js:3969`

```js
function startSyncPolling(playlistId)
```

**Returns** `undefined`.

**Side effects**
- Clears any existing `activeSyncPollers[playlistId]` interval.
- **WebSocket branch** (only `if (socketConnected)`): `socket.emit('sync:subscribe', {playlist_ids:[playlistId]})` and registers `_syncProgressCallbacks[playlistId] = (data) => …`. Callback payload: `{status:'syncing'|'finished'|'error'|'cancelled', progress:{…}}`. On `'syncing'` → `updateCardToSyncing(playlistId, data.progress.progress, data.progress)` + `updateModalSyncProgress(playlistId, data.progress)`. On terminal → `stopSyncPolling(playlistId)`, `updateCardToDefault(playlistId, data)`, `closePlaylistDetailsModal()`.
- **HTTP poller runs unconditionally alongside**: `activeSyncPollers[playlistId] = setInterval(…, 2000)` doing `GET /api/sync/status/${playlistId}`. Same handling, plus `closeDeezerArlPlaylistDetailsModal()` on terminal. On fetch error → `stopSyncPolling` + `updateCardToDefault(playlistId, {status:'error', error:'Polling failed'})`.
- Ends with `updateRefreshButtonState()`.

**Companion** `stopSyncPolling(playlistId)` (`downloads.js:4025`): clears + `delete activeSyncPollers[playlistId]`; if a `_syncProgressCallbacks[playlistId]` exists, emits `sync:unsubscribe` (when `socketConnected`) and deletes it; then `updateRefreshButtonState()`.

**Preconditions** — globals `activeSyncPollers`, `socket`, `socketConnected`, `_syncProgressCallbacks`; functions `updateCardToSyncing`, `updateModalSyncProgress`, `updateCardToDefault`, `closePlaylistDetailsModal`, `closeDeezerArlPlaylistDetailsModal`, `updateRefreshButtonState`.

---

### `openDownloadMissingWishlistModal`
`downloads.js:1378`

```js
async function openDownloadMissingWishlistModal(category = null, selectedTrackIds = null)
```

**Params**
- `category: string|null` — appended as `?category=` to the tracks fetch; also stashed on `window.currentWishlistCategory`.
- `selectedTrackIds: Set<string>|null` — when non-empty, tracks are filtered to those whose `.id` **or** `.spotify_track_id` is in the set. Must support `.size` and `.has()`.

**Returns** `Promise<void>`.

**Side effects**
- `showLoadingOverlay('Loading wishlist...')`; `hideLoadingOverlay()` on every exit.
- Fixed process key: the string literal **`'wishlist'`**.
- Early-return if `activeDownloadProcesses['wishlist']` exists: shows the modal, toasts on `status==='complete'`, calls **`WishlistModalState.setVisible()`**.
- Sets `window.currentWishlistCategory = category`.
- **Network**: `GET /api/wishlist/count` (returns `{count}` — 0 ⇒ info toast and abort), then `GET /api/wishlist/tracks[?category=…]` (returns `{tracks:[…]}`). Track fields read: `.name`, `.artists`, `.id`, `.spotify_track_id`.
- Mutates `currentPlaylistTracks`, `currentModalPlaylistId`, and creates `activeDownloadProcesses['wishlist'] = {status:'idle', modalElement, poller:null, batchId:null, playlist:{id:'wishlist',name:'Wishlist'}, tracks}`. **Note: it does NOT write `playlistTrackCache`** (unlike the YouTube/artist-album openers).
- Appends `<div id="download-missing-modal-wishlist" class="download-missing-modal">`.
- **DOM ids created**: `analysis-progress-text-wishlist`, `analysis-progress-fill-wishlist`, `download-progress-text-wishlist`, `download-progress-fill-wishlist`, `download-tracks-tbody-wishlist`, `force-download-all-wishlist`, `begin-analysis-btn-wishlist`, `cancel-all-btn-wishlist`, `cleanup-wishlist-btn-wishlist`, `clear-wishlist-btn-wishlist`, plus per-row `match-wishlist-${i}` / `download-wishlist-${i}` / `actions-wishlist-${i}` and hero `stat-found-wishlist` / `stat-missing-wishlist`. Content wrapper carries `data-context="wishlist"`. **No track-selection checkboxes and no `select-all-` in this modal.**
- Its Begin Analysis button calls **`startWishlistMissingTracksProcess`** (`downloads.js:1559`), *not* `startMissingTracksProcess` — that one POSTs `/api/wishlist/download_missing` `{force_download_all, category, track_ids}`, handles 409 (auto-processing → opens download manager, `delete activeDownloadProcesses[playlistId]`) and 429, then `startModalDownloadPolling('wishlist')`.
- `applyProgressiveTrackRendering('wishlist', tracks.length)`, `modal.style.display='flex'`, `WishlistModalState.setVisible()`.

---

### `_toggleWingItDropdown`
`downloads.js:13`

```js
function _toggleWingItDropdown(btn, urlHash)
```

**Params** — `btn: HTMLElement` (the clicked button; must have an ancestor matching `.wing-it-wrap`), `urlHash: string` (state key).

**Returns** `undefined`. Returns early if a `.wing-it-dropdown.visible` already exists anywhere in the document (closing it — pure toggle), or if `btn.closest('.wing-it-wrap')` is null.

**Side effects**
- Appends `<div class="wing-it-dropdown">` into `.wing-it-wrap`, containing two `.wing-it-dropdown-item` buttons with `data-action="download"` and `data-action="sync"` (each with `.wing-it-dropdown-icon`, `.wing-it-dropdown-label`, `.wing-it-dropdown-hint`).
- Adds `.flip-down` when `btn.getBoundingClientRect().top < 200`; adds `.visible` on the next animation frame; removal is `.visible` off then `remove()` after 150 ms.
- Installs a `document` click handler after a 50 ms `setTimeout` that closes on outside click and removes itself.
- Item click → `_wingItAction(urlHash, 'download'|'sync')` (`downloads.js:69`), which reads `listenbrainzPlaylistStates[urlHash] || youtubePlaylistStates[urlHash]` for `.tracks`/`.rawTracks`/`.playlist.tracks`, `.playlistName`/`.name`/`.playlist.name`, and the `is_tidal_playlist`/`is_qobuz_playlist`/`is_listenbrainz_playlist`/`is_beatport_playlist`/`is_deezer_playlist` flags; `'sync'` calls `_wingItSyncFromModal`, `'download'` removes `#youtube-discovery-modal-${urlHash}` and `#youtube-discovery-overlay-${urlHash}` then calls `wingItDownload(tracks, name, source, null, true)`.

**Call sites** — inline `onclick="_toggleWingItDropdown(this, '${urlHash}')"` in `sync-services.js:9612, 9698, 9863`.

---

### `updateTrackAnalysisResults`
`downloads.js:2003`

```js
function updateTrackAnalysisResults(playlistId, results)
```

**Params** — `playlistId: string`; `results: Array<{track_index: number, found: boolean}>` (only those two fields are read).

**Returns** `undefined`.

**Side effects** — for each result, if `#match-${playlistId}-${result.track_index}` exists: sets `textContent` to `'✅ Found'`/`'❌ Missing'` and `className` to `` `track-match-status ${found ? 'match-found' : 'match-missing'}` `` (**overwrites** the class list, dropping `match-checking`). Null-guarded per element. No globals, no network.

---

### `_ensureErrorTooltipListeners`
`downloads.js:2345`

```js
function _ensureErrorTooltipListeners(statusEl)
```

**Params** — `statusEl: HTMLElement`, expected to be `#download-${playlistId}-${trackIndex}` carrying `dataset.errorMsg`.

**Returns** `undefined`; idempotent via the `statusEl._errorTooltipBound` expando.

**Side effects**
- Sets `statusEl._errorTooltipBound = true`.
- Adds `mouseenter` (reads `this.dataset.errorMsg`, bails if empty or `!this.offsetParent`) and `mouseleave` → `_hideErrorTooltip`.
- On hover it lazily creates a **singleton `<div id="error-tooltip-popup">` appended to `document.body`** (`_getErrorTooltipPopup`, `downloads.js:2330`), sets its `textContent`, adds class `visible`, and positions it with inline `left`/`top` (clamped to 8 px viewport margins, flips below the row when there is no room above).
- Also binds a `scroll` listener (`{passive:true}`) on the nearest `.download-missing-modal-body` ancestor, deduped by `scrollParent._errorTooltipScrollBound`.

**Preconditions** — caller must set `statusEl.dataset.errorMsg` before hover (the render loop at `downloads.js:3122` sets it and adds `.has-error-tooltip` for `failed`/`cancelled`/`not_found` tasks with `error_message`).

---

### `_ensureCandidatesClickListener`
`downloads.js:2375`

```js
function _ensureCandidatesClickListener(statusEl)
```

**Returns** `undefined`; idempotent via `statusEl._candidatesClickBound`.

**Side effects** — adds a `click` listener that `stopPropagation()`s, calls `_hideErrorTooltip()`, reads `this.dataset.taskId` (bails if absent), then **decides at click time**:
- `this.dataset.detailOpen` truthy **and** `typeof openTrackDetail === 'function'` → `openTrackDetail(taskId)`;
- otherwise → `showCandidatesModal(taskId)` (`downloads.js:2394`), which `GET /api/downloads/task/${taskId}/candidates` and calls `_renderCandidatesModal(data)`.

**Preconditions** (set each render at `downloads.js:3126-3143`) — `statusEl.dataset.taskId` must be set; `dataset.detailOpen='1'` for `completed` tasks and for quarantined `not_found`/`failed`/`cancelled` tasks with `task.quarantine_entry_id`; the class `.has-candidates` is the visual affordance. The render loop **clears** `detailOpen`, `quarantineEntryId`, `quarantineReason`, `quarantineTrack` and `.has-candidates` every frame before re-adding, so the listener must stay dataset-driven, never captured.

---

### `updateRefreshButtonState`
`downloads.js:4119`

```js
function updateRefreshButtonState()   // no params
```

**Returns** `undefined`. Early-return if `#spotify-refresh-btn` is absent.

**Side effects** — mutates exactly `#spotify-refresh-btn` (`index.html:2302`, `class="refresh-button"`):
- Busy ⇒ `disabled=true` and `textContent` is `'🔄 Syncing...'` when `Object.keys(activeSyncPollers).length > 0` **or** `sequentialSyncManager?.isRunning`, else `'📥 Downloading...'`.
- Idle ⇒ `disabled=false`, `textContent='🔄 Refresh'`.

**Busy definition** — `hasActiveOperations()` (`downloads.js:4108`): `Object.keys(activeSyncPollers).length > 0` **||** any `activeDownloadProcesses` entry with key ≠ `'wishlist'` and `status === 'running'` **||** `sequentialSyncManager && sequentialSyncManager.isRunning`. Wishlist downloads are deliberately excluded.

**Preconditions** — globals `activeSyncPollers`, `activeDownloadProcesses`, `sequentialSyncManager`.

---

### `checkAndCleanupGlobalPolling`
`downloads.js:3430`

```js
function checkAndCleanupGlobalPolling()   // no params
```

**Returns** `undefined`. **Effectively a no-op today**: it computes `Object.values(activeDownloadProcesses).some(p => p.batchId && p.status === 'running')` and, when false, only `console.debug`s — it deliberately does **not** clear `globalDownloadStatusPoller`. Called from `processModalStatusUpdate` (two completion paths, `downloads.js:3272`, `3425`) and from `cleanupDownloadProcess` (`sync-spotify.js:1785`). Safe to call at any time; no DOM, no network.

---

## 2. `webui/static/core.js`

### `activeDownloadProcesses`
`core.js:44` — `let activeDownloadProcesses = {}` (script-scoped `let`; **not** on `window`; the codebase already bridges it via `window.reopenActiveDownloadModal` / `window.openDownloadBatchModal`, core.js:314/338).

**Key**: the virtual playlist id string (or the literal `'wishlist'`).

**Entry shape — union of every field any writer sets:**

| Field | Type | Set by |
|---|---|---|
| `status` | `'idle'\|'running'\|'complete'\|'cancelled'` (`'view_results'` also compared at shared-helpers.js:2368) | all creators (`'idle'`); mutated by `startMissingTracksProcess`, `startWishlistMissingTracksProcess`, `processModalStatusUpdate`, `startModalDownloadPolling`, `cleanupDownloadProcess`, rehydration paths |
| `modalElement` | `HTMLDivElement` (`#download-missing-modal-${id}`) | all creators |
| `poller` | `number\|null` (interval id) | creators (`null`), `createLegacyPoller` (dummy 5 s interval), cleared by `clearPlaylistDownloadProcess`/`restartPlaylistDownloadMissing`/`cleanupDownloadProcess` |
| `batchId` | `string\|null` | creators (`null`); set from `data.batch_id` after start, or from `existingState.download_process_id` on rehydration |
| `playlist` | `{id, name, track_count?}` — virtual playlist object; wishlist uses `{id:'wishlist', name:'Wishlist'}` | all creators |
| `tracks` | `Array<Track>` | all creators |
| `artist` | `object\|null` (`.name`, `.id`, `.source`, `.image_url`) | `openDownloadMissingModalForYouTube` (downloads.js:475), `openDownloadMissingModalForArtistAlbum` (shared-helpers.js:1815) |
| `album` | `object\|null` (`.name`, `.title`, `.album_type`, `.images`, `.source`) | same two |
| `albumType` | `string` (`album.album_type`) | `openDownloadMissingModalForArtistAlbum` only (shared-helpers.js:1817) |
| `source` | `string\|null` — `artist?.source \|\| album?.source \|\| artistsPageState.artistDiscography?.source` | `openDownloadMissingModalForArtistAlbum` only |
| `discoverMetadata` | `{imageUrl: string\|null, type: 'album'\|'playlist'}` | `openDownloadMissingModalForYouTube` only (downloads.js:507), and only when the source resolves to SoulSync or the id starts `discover_lb_`/`listenbrainz_`/`wing_it_` |
| `modalId` | `string` (element id) | **never written in any of these files** — only *read*, at `core.js:1405-1407` (`openDiscoverDownloadModal` fallback for "sync downloads"). Treat as optional/legacy. |

Entries are **deleted** by: `cleanupDownloadProcess` (sync-spotify.js:1782), `clearPlaylistDownloadProcess` (shared-helpers.js:1553), `restartPlaylistDownloadMissing` (shared-helpers.js:1721), and the 409 branch of `startWishlistMissingTracksProcess` (downloads.js:1613).

The creators are: `downloads.js:469` (YouTube/virtual), `downloads.js:1447` (wishlist), `shared-helpers.js:1807` (artist album / Beatport release), `sync-spotify.js:2283` (Spotify playlist), `sync-services.js:1352` (Tidal/Qobuz/etc.).

---

### `subscribeToDownloadBatch`
`core.js:1074`

```js
function subscribeToDownloadBatch(batchId)
```
- **Param**: `batchId: string`.
- **Returns** `undefined`.
- **Side effect**: `socket.emit('downloads:subscribe', { batch_ids: [batchId] })` — **only if** `socket && socketConnected && batchId`; silently no-ops otherwise (no queueing, no retry).
- **Counterpart** `unsubscribeFromDownloadBatch(batchId)` (core.js:1080) emits `downloads:unsubscribe` under the same guard.
- **Reconnect recovery**: `resubscribeDownloadBatches()` (core.js:1061) runs on `connect` and `reconnect`, gathering every `activeDownloadProcesses` entry whose `.batchId` is set and `status` is `'running'` or `'complete'`, emitting one bulk `downloads:subscribe {batch_ids:[…]}`.
- **Inbound**: `socket.on('downloads:batch_update', handleDownloadBatchUpdate)` → payload `{batch_id, data}`; the handler linearly scans `activeDownloadProcesses` for `process.batchId === batch_id` and calls `processModalStatusUpdate(playlistId, data)` on the first match.

---

### `WishlistModalState`
`core.js:1166` — a plain `const` object literal (script-scoped). Pure `localStorage` wrapper; no DOM, no network. All methods take no args.

| Method | Returns | Effect |
|---|---|---|
| `setVisible()` | `void` | `localStorage['wishlist_modal_visible'] = 'true'` |
| `setHidden()` | `void` | `localStorage['wishlist_modal_visible'] = 'false'` |
| `wasVisible()` | `boolean` | `localStorage['wishlist_modal_visible'] === 'true'` |
| `clear()` | `void` | `removeItem('wishlist_modal_visible')` |
| `setUserClosed()` | `void` | `localStorage['wishlist_modal_user_closed'] = 'true'` |
| `clearUserClosed()` | `void` | `removeItem('wishlist_modal_user_closed')` |
| `wasUserClosed()` | `boolean` | `localStorage['wishlist_modal_user_closed'] === 'true'` |

Callers: `openDownloadMissingWishlistModal` (`setVisible` ×2), `closeDownloadMissingModal` (`setUserClosed` while running, `clear` on full close), `window.openDownloadBatchModal` (`setVisible`).

---

### `currentMusicSourceName`
`core.js:15` — `let currentMusicSourceName = 'Spotify';`

- **Value shape**: a display-cased string. Comment says `'Spotify' | 'iTunes' | 'Deezer'`.
- **What sets it: nothing.** A repo-wide grep for `currentMusicSourceName =` finds only the declaration — despite the "updated from status endpoint" comment, it is **never reassigned**. It is effectively the constant `'Spotify'`.
- The live equivalent is `getActiveMetadataSource()` (`core.js:1376`) → `_lastStatusPayload?.metadata_source?.source || 'spotify'` (lower-case id), and the sidebar label written into `#metadata-source-name` by `updateSidebarServiceStatus` via `getMetadataSourceLabel(statusData.source)` (`shared-helpers.js:4199-4204`).
- **Readers** (all bare reads, so a port must keep the binding or supply the same value): `sync-services.js:9389, 9403, 9404, 9621, 9934, 9936, 9942`; `wishlist-tools.js:225` (`(currentMusicSourceName || 'Spotify').toLowerCase()`); `stats-automations.js:580` (`typeof`-guarded, falls back to `'metadata'`).

---

### `_isSoulsyncStandalone`
`core.js:1373` — **a boolean flag**, not a function: `let _isSoulsyncStandalone = false;`

- **Semantics**: `true` when there is no real media server, i.e. `data.media_server?.type === 'soulsync'`. Meaning: sync-to-server is impossible, so sync controls are hidden and download UI relabels toward playlist-folder downloads.
- **Written in exactly two places**, both service-status handlers: `core.js:974` (`handleServiceStatusUpdate`, the WebSocket `status:update` path) and `shared-helpers.js:4050` (`fetchAndUpdateServiceStatus`, the HTTP fallback path). Both then hide/show every `.sync-to-server-btn, [id$="-sync-btn"], [onclick*="startPlaylistSync"], [onclick*="syncPlaylistToServer"], [onclick*="startDecadeSync"]`, skipping `#stats-sync-btn` and anything with `.soulsync-standalone-action`, using `dataset.hiddenByStandalone` as the restore marker.
- **Readers**: `shared-helpers.js:1692, 1696, 1698, 1934`; `sync-services.js:9635, 9666, 9667, 9670, 9813, 9840, 9841, 9844, 9901, 9902, 9905`.
- Read timing matters: it is only correct after the first `/status` frame has landed.

---

### `socket` / `socketConnected` / `_discoveryProgressCallbacks` / `_syncProgressCallbacks`

**`socket`** — `core.js:708` `let socket = null`. Assigned in `initializeWebSocket()` (core.js:714) to `io({transports:['polling','websocket'], reconnection:true, reconnectionAttempts:Infinity, reconnectionDelay:1000, reconnectionDelayMax:10000, timeout:20000})`. Script-scoped; **no module can subscribe to it**. The established escape hatches are (a) `window.SoulSyncActivitySocket` (`{subscribe, unsubscribe, isConnected}`, core.js:839) and (b) re-broadcast `CustomEvent`s on `window`: `ss:service-status`, `ss:watchlist-count`, `ss:dashboard-stats`, `ss:dashboard-activity`, `ss:dashboard-toast`, `ss:dashboard-db-stats`, `ss:dashboard-wishlist-count`, `ss:discovery-progress`, `ss:watchlist-scan`, `ss:automation-progress`, `ss:media-scan`, `ss:repair-status`.

**`socketConnected`** — `core.js:709` `let socketConnected = false`, mirrored **in lockstep** onto `window._socketConnected` (`core.js:712`, written at 735/736 on `connect` and 761/762 on `disconnect`). `window._socketConnected` is the sanctioned module-readable gate (already declared in `src/platform/shell/globals.d.ts:556`). Every HTTP fallback poller checks it (e.g. `fetchAndUpdateServiceStatus` returns early `if (socketConnected)`), **except** the download-status and sync-status pollers, which run regardless by design.

**`_syncProgressCallbacks`** — `core.js:37` `let _syncProgressCallbacks = {}`.
- **Key**: `playlist_id` (string), matched against the inbound frame's `data.playlist_id`.
- **Registered by**: `startSyncPolling` (`downloads.js:3981`), guarded by `if (socketConnected)`; also many `sync-services.js` sites for source-specific syncs.
- **Dispatch**: `socket.on('sync:progress', …)` → `updateSyncProgressFromData(data)` (`media-player.js:542`) → `_syncProgressCallbacks[data.playlist_id]?.(data)`. **Callback payload** = the raw frame: `{playlist_id, status: 'syncing'|'finished'|'error'|'cancelled', progress: {progress, total_tracks, matched_tracks, failed_tracks, current_step, current_track, wishlist_added_count?, unmatched_tracks?}, error?}`.
- **Deleted by**: `stopSyncPolling` (`downloads.js:4030`, also emits `sync:unsubscribe`), `stopBeatportDiscoveryAndSyncPolling` (`core.js:445-452`, for entries whose `youtubePlaylistStates` entry is a Beatport chart with a matching `syncPlaylistId`), and inline `delete` in the sync-services callbacks on terminal status.
- **Reconnect**: on `connect`, `Object.keys(_syncProgressCallbacks)` is re-emitted as one `sync:subscribe {playlist_ids:[…]}` (`core.js:739-743`).

**`_discoveryProgressCallbacks`** — `core.js:38` `let _discoveryProgressCallbacks = {}`.
- **Key**: the discovery identifier — sometimes a playlist id, sometimes a `urlHash` (`sync-services.js:623` uses `playlistId`; `:4725`, `:6977`, `:8003` use `urlHash`). Matched against the inbound frame's `data.id`.
- **Registered by**: the `startXDiscoveryPolling` functions in `sync-services.js`, each guarded by `if (socketConnected)` and paired with `socket.emit('discovery:subscribe', {ids:[identifier]})`.
- **Dispatch**: `socket.on('discovery:progress', …)` → `qaSignal('sync')` + `updateDiscoveryProgressFromData(data)` (`media-player.js:548`) → `_discoveryProgressCallbacks[data.id]?.(data)`; the same handler *also* dispatches `window` `CustomEvent('ss:discovery-progress', {detail: data})` for React (`core.js:878`). **Payload**: `{id, error?, progress, spotify_matches, spotify_total, complete, phase, results: [{status, status_class, spotify_data, spotify_id, spotify_track, spotify_artist, spotify_album, manual_match, wing_it_fallback, tidal_track?/yt_track?…}]}`.
- **Deleted by**: each callback itself on `data.error` or `data.complete` (paired with `discovery:unsubscribe`), and by `stopBeatportDiscoveryAndSyncPolling` (`core.js:436-443`).
- **Reconnect**: `Object.keys(_discoveryProgressCallbacks)` re-emitted as `discovery:subscribe {ids:[…]}` (`core.js:744-748`).

---

### `cleanupBeatportContent`
`core.js:475`

```js
function cleanupBeatportContent()   // no params
```

**Returns** `undefined`. **Early-returns** unless `beatportContentState.loaded || beatportContentState.loadingPromise` — i.e. cheap to call unconditionally.

**Side effects**
- Aborts the in-flight load: `beatportContentState.abortController.abort()` then `= null` (the signal is threaded into `fetch` by `getBeatportContentSignal()`).
- `stopBeatportDiscoveryAndSyncPolling()` — clears + deletes matching `activeYouTubePollers` intervals; emits `discovery:unsubscribe` and deletes matching `_discoveryProgressCallbacks`; emits `sync:unsubscribe` and deletes matching `_syncProgressCallbacks`.
- Calls `cleanupBeatportRebuildSlider`, `cleanupBeatportReleasesSlider`, `cleanupBeatportHypePicksSlider`, `cleanupBeatportChartsSlider`, `cleanupBeatportDJSlider`.
- `resetBeatportSliderInitFlags()` — sets `dataset.initialized='false'` on `#beatport-rebuild-slider`, `#beatport-releases-slider`, `#beatport-charts-slider`, `#beatport-dj-slider`, and `isInitialized=false` on `beatportReleasesSliderState`, `beatportHypePicksSliderState`, `beatportChartsSliderState`, `beatportDJSliderState`.
- Resets `beatportContentState.loadingPromise = null; beatportContentState.loaded = false`.

**Callers**: `loadPageData` for any `pageId !== 'sync'` (`init.js:3282`), and `initializeSyncPage`'s tab handler when leaving the `beatport` tab (`sync-services.js:3750`).

---

### `openDownloadModalForListenBrainzPlaylist`
`core.js:1765` (rehomed here during the discover port)

```js
async function openDownloadModalForListenBrainzPlaylist(identifier, title)
```

**Params** — `identifier: string` (the LB playlist mbid / cache key), `title: string`.

**Returns** `Promise<void>`.

**Body** (thin wrapper, 7 lines): reads `listenbrainzTracksCache[identifier]`; if falsy or empty → `showToast('No tracks to download','error')` and returns; else `await window.openLbPlaylistDiscovery(identifier, title, tracks)`.

**Precondition** — `listenbrainzTracksCache[identifier]` must already be populated (filled by `sync-listenbrainz.js`; the cache itself is `let listenbrainzTracksCache = {}` at `core.js:48`).

**Downstream side effects** via `window.openLbPlaylistDiscovery` (`core.js:104`, itself a `window.*` bridge): reads `listenbrainzPlaylistStates[identifier]`; if `phase` is `downloading`/`download_complete` **and** both `convertedSpotifyPlaylistId` and `download_process_id` are set, it either re-shows the existing `activeDownloadProcesses[convertedSpotifyPlaylistId].modalElement` or rebuilds it by mapping `existingState.discovery_results[].spotify_data` into `{id,name,artists,album,duration_ms,external_urls}` and calling `openDownloadMissingModalForYouTube(convertedPlaylistId, title, spotifyTracks)`, then sets `process.status`/`process.batchId`, hides `#begin-analysis-btn-…`, shows `#cancel-all-btn-…`, calls `startModalDownloadPolling(convertedPlaylistId)` and `addDiscoverDownload(...)` when `process.discoverMetadata` exists. Otherwise it starts discovery and calls `openYouTubeDiscoveryModal(identifier)`.

---

## 3. `webui/static/shared-helpers.js`

### `openDownloadMissingModalForArtistAlbum`
`shared-helpers.js:1763`

```js
async function openDownloadMissingModalForArtistAlbum(virtualPlaylistId, playlistName, spotifyTracks, album, artist, showLoadingOverlayParam = true, contextType = 'artist_album')
```

**Params**
- `virtualPlaylistId: string` — process key + DOM id suffix.
- `playlistName: string`.
- `spotifyTracks: Array<Track>` — `.name`, `.artists`, `.duration_ms` read.
- `album: object` — **required, dereferenced unguarded** at `.album_type` (line 1817); also `.name`, `.images`, `.source`.
- `artist: object` — `.name`, `.id`, `.source`, `.image_url`.
- `showLoadingOverlayParam: boolean = true` — gates `showLoadingOverlay('Loading album...')` and the early-return `hideLoadingOverlay()`. Note the **final** `hideLoadingOverlay()` on the create path is unconditional.
- `contextType: string = 'artist_album'` — becomes `data-context` on `.download-missing-modal-content`; also `'playlist'` (Beatport charts/compilations), which switches the hero to a playlist hero and **adds** the `#playlist-folder-mode-${id}` checkbox.

**Returns** `Promise<void>`.

**Side effects** — same overall shape as `openDownloadMissingModalForYouTube`, with these differences:
- Early-return path (existing process) additionally awaits `refreshOrganizePreferenceForDownloadModal(virtualPlaylistId)`.
- Process entry additionally carries `albumType` and `source`.
- Extra DOM ids created: `#skip-acoustid-${id}` (always), `#sync-server-btn-${id}` (only when `_isBeatportPlaylistId(virtualPlaylistId)`, hidden inline when `_isSoulsyncStandalone`), and the sync-progress block `#modal-sync-progress-${id}`, `#modal-sync-bar-${id}`, `#modal-sync-step-${id}`, `#modal-sync-matched-${id}`, `#modal-sync-failed-${id}`, `#modal-sync-cancel-${id}` (classes `.modal-sync-progress-area`, `.modal-sync-progress-bar-bg/-fill`, `.modal-sync-progress-info`, `.modal-sync-step`, `.modal-sync-stats`, `.modal-sync-cancel-btn`).
- Writes `playlistTrackCache[virtualPlaylistId]`, `currentPlaylistTracks`, `currentModalPlaylistId`.
- Reads global `artistsPageState.artistDiscography?.source`.
- Ends `modal.style.display='flex'` + `hideLoadingOverlay()`.

**Precondition** — `album` must be a non-null object.

---

### `showBeatportDownloadsSection`
`shared-helpers.js:3430`

```js
function showBeatportDownloadsSection()   // no params
```

**Returns** `undefined`. Early-return if `#beatport-downloads-section` is absent.

**Side effects**
- Reads global `beatportDownloadBubbles` (`core.js:534`, shape `{ [chartKey]: { chart: {name, image}, downloads: [] } }`); "active" = `downloads.length > 0`.
- No active charts ⇒ `display='none'` and return. Otherwise `display='block'` and full `innerHTML` replacement with `.artist-downloads-header > .artist-downloads-title/.artist-downloads-subtitle` and `<div class="artist-bubble-container" id="beatport-bubble-container">` filled by `createBeatportBubbleCard(bubble)` per chart.
- Then, per chart, finds `[data-chart-key="${chartKey}"]` inside the section and attaches a `click` → `openBeatportBubbleModal(chartKey)`, plus `extractImageColors(chartImage, colors => applyDynamicGlow(card, colors))` when `bubble.chart.image` is set.
- Because it re-renders the whole subtree, listeners are re-attached each call — safe to call repeatedly.

**Preconditions** — `#beatport-downloads-section` in DOM; globals `beatportDownloadBubbles`; functions `createBeatportBubbleCard`, `openBeatportBubbleModal`, `extractImageColors`, `applyDynamicGlow`.

---

### `hydrateBeatportBubblesFromSnapshot`
`shared-helpers.js:3657`

```js
async function hydrateBeatportBubblesFromSnapshot()   // no params
```

**Returns** `Promise<void>`. Never throws — `AbortError` is swallowed with a log, other errors are logged.

**Side effects**
- `GET /api/beatport_bubbles/hydrate`, passing `{signal: getBeatportContentSignal()}` when an abort controller is live. Response `{success, error?, bubbles: { [chartKey]: { chart, downloads: [{virtualPlaylistId, status, startTime}] } }}`.
- **Replaces the global wholesale**: `beatportDownloadBubbles = {}` then rebuilds each entry as `{chart: bubbleData.chart, downloads: [{virtualPlaylistId, status, startTime: new Date(d.startTime)}]}` (`startTime` is coerced from the wire string to a `Date`).
- For every download with `status === 'in_progress'` → `monitorBeatportDownload(chartKey, virtualPlaylistId)` (starts a per-download watcher).
- Ends with `updateBeatportDownloadsSection()`.
- Returns early (leaving `beatportDownloadBubbles` untouched) when `!data.success` or when `bubbles` is empty.

**Called from** `ensureBeatportContentLoaded()` (`sync-services.js:3733`) as the first step of the lazy Beatport load.

---

### `escapeHtml` / `_esc` / `_escAttr` — which live where

- **`escapeHtml(text)` — `shared-helpers.js:3961`** (duplicate at `downloads.js:5624`, which wins at load). Body: `const div = document.createElement('div'); div.textContent = text; return div.innerHTML;`. Textarea-style escaping — escapes `& < >` and turns ` ` into `&nbsp;`, but **does not escape `"` or `'`**, so it is unsafe for unquoted/single-quoted attribute interpolation (the codebase nonetheless uses it inside `title="…"`).
- **`_esc(str)` — NOT in shared-helpers.js. Owner: `stats-automations.js:5764`.** Contract: `_esc(str: any): string` — returns `''` for any falsy input, otherwise identical `div.textContent`/`innerHTML` escaping to `escapeHtml`.
- **`_escAttr(str)` — NOT in shared-helpers.js. Two different implementations:**
  - **`stats-automations.js:5771` (wins at load, loaded line 8398)**: `_escAttr(str: any): string` — `''` for falsy; otherwise regex-replaces `& " ' < >` → `&amp; &quot; &#39; &lt; &gt;`. Pure HTML-attribute escaping.
  - `downloads.js:4665` (shadowed): `_escAttr(s) => _escToast(s).replace(/'/g,"\\'").replace(/\n/g,' ').replace(/\r/g,'')` — HTML-escapes, then **backslash-escapes single quotes** and strips newlines. Intended for inline-JS-in-attribute, used by the toast/notification system. Since stats-automations.js loads later, downloads.js's version is **not** the one reachable by name at runtime.
- Related, and the correct helper for `onclick="fn('${v}')"`: **`escapeForInlineJs(str)` — `downloads.js:5638`**: `null`/`undefined` → `''`; escapes `\` and `'` for JS, then `& " < >` for HTML. Used by `renderModalTrackPlayButton`.

---

### Formatting / chrome helpers (signatures only)

| Function | Owner | Signature |
|---|---|---|
| `formatDuration` | **`sync-services.js:10036`** (wins; also `sync-spotify.js:1967`, `wishlist-tools.js:1575`) | `function formatDuration(durationMs)` → `string`; `'0:00'` when falsy, else `M:SS`. (The shadowed wishlist-tools version returns `'--:--'` for `<= 0`.) |
| `formatArtists` | **`downloads.js:5649`** (sole definition) | `function formatArtists(artists)` → `string`; `'Unknown Artist'` when not an array; accepts `string[]` or `{name}[]`; each name passed through `cleanArtistName`; joined with `', '`. |
| `showToast` | **`downloads.js:4610`** | `function showToast(message, type = 'success', helpSection = null)` → `void`. Dedupes identical `${type}:${message}` within 5000 ms. Appends to the toast container, auto-dismisses after 3500 ms (5000 ms when `helpSection`), and pushes an entry into the `_notifState.history` bell panel. |
| `showLoadingOverlay` | **`downloads.js:4321`** | `function showLoadingOverlay(message = 'Loading...')` → `void`. **Unguarded**: `document.getElementById('loading-overlay')` and its `.loading-message` child must both exist. Removes class `hidden`. |
| `hideLoadingOverlay` | **`downloads.js:4328`** | `function hideLoadingOverlay()` → `void`. Adds class `hidden` to `#loading-overlay`; unguarded. |

`#loading-overlay` lives at `webui/index.html:7724`.

---

### Optional-globals protocol members

All of the following **are** in `shared-helpers.js` except where noted.

| Member | Location | Contract |
|---|---|---|
| `playlistTrackCache` | **`core.js:41`** (not shared-helpers) | `let playlistTrackCache = {}` — key: playlist id, value: `Track[]`. Companion `playlistTrackSnapshotCache` (`core.js:42`) holds the upstream `snapshot_id` at cache time. Written by `fetchAndCachePlaylistTracks`, `openDownloadMissingModalForYouTube`, `openDownloadMissingModalForArtistAlbum`. |
| `fetchAndCacheSpotifyPlaylistTracks` | `shared-helpers.js:1660` | `async (playlistId) => fullPlaylist`. Delegates to `fetchAndCachePlaylistTracks(playlistId, '/api/spotify/playlist/'+playlistId, 'spotify', playlistId)`: fetches, **throws** `new Error(fullPlaylist.error)` on an error field, writes `playlistTrackCache[key] = fullPlaylist.tracks` and `playlistTrackSnapshotCache[key] = fullPlaylist.snapshot_id \|\| ''`, calls `mirrorPlaylistTracksForSource` (which POSTs via `mirrorPlaylist` when that function exists), returns the full playlist `{name, description, owner, image_url, snapshot_id, tracks}`. |
| `isPlaylistDownloadProcessStale` | `shared-helpers.js:1569` | `(playlistId, playlistMeta) => boolean`. True if `playlistTrackCacheIsStale(...)`, **or** `playlistMeta.track_count != proc.tracks.length`, **or** `!playlistTrackCache[playlistId] && proc.status === 'complete'`. False when no process exists. Reads `playlistMeta.track_count` only. |
| `playlistTrackCacheIsStale` | `shared-helpers.js:1531` | `(playlistId, playlist) => boolean`. `false` when nothing cached. Compares `String(playlist?.snapshot_id)` against `playlistTrackSnapshotCache[playlistId]`; when either is missing/empty returns `false` (never force-refetches without a reliable snapshot). |
| `restartPlaylistDownloadMissing` | `shared-helpers.js:1713` | `async (playlistId) => void`. Clears `proc.poller`, removes `proc.modalElement` from DOM, `delete activeDownloadProcesses[playlistId]`, calls `closePlaylistDetailsModal()` and `closeDeezerArlPlaylistDetailsModal()` when defined, then `await openDownloadMissingModal(playlistId)`. Does **not** POST any server cleanup. |
| `clearPlaylistDownloadProcess` | `shared-helpers.js:1553` | `(playlistId) => void`. No-op if absent. Clears `proc.poller` (sets `null`), `proc.modalElement.remove()`, `delete activeDownloadProcesses[playlistId]`. No network, no card-UI refresh (contrast `cleanupDownloadProcess`). |
| `invalidatePlaylistTrackCache` | `shared-helpers.js:1590` | `(playlistId = null) => void`. **With an id**: deletes that cache + snapshot entry, resets `currentPlaylistTracks = []` if `currentModalPlaylistId === playlistId`, calls `clearPlaylistDownloadProcess(playlistId)`. **Without**: reassigns `playlistTrackCache = {}` and `playlistTrackSnapshotCache = {}`, `currentPlaylistTracks = []`, then `clearPlaylistDownloadProcess` for every id in `spotifyPlaylists` and every `deezer_arl_`-prefixed id in `deezerArlPlaylists`. Called at the top of `loadSpotifyPlaylists` and `loadDeezerArlPlaylists`. |
| `refreshOrganizePreferenceForDownloadModal` | `shared-helpers.js:1527` | `async (playlistRef, source = null) => void`. Thin alias for `applyMirroredOrganizePreference`. |
| `downloadMissingModalOrganizeCheckboxHtml` | `shared-helpers.js:1301` | `(playlistId) => string` (HTML). Emits `<label class="force-download-toggle">` wrapping `<input type="checkbox" id="playlist-folder-mode-${playlistId}" class="playlist-folder-mode-sync" onchange="onPlaylistOrganizePreferenceChange('${safeId}', this.checked, playlistOrganizeSourceForRef('${safeId}'))">`, plus `playlistQualityProfileSelectHtml(playlistId, playlistOrganizeSourceForRef(playlistId))`. `safeId` = `playlistId` with `'` → `\'`. |
| `applyMirroredOrganizePreference` | `shared-helpers.js:1522` | `async (playlistRef, source = null) => void`. Delegates to `loadPlaylistOrganizePreferenceIntoModal`. |
| `playlistOrganizeToggleHtml` | `shared-helpers.js:1312` | `(playlistRef, source = 'spotify') => string`. `<label class="playlist-modal-organize-toggle">` + checkbox with id `playlistDetailsOrganizeCheckboxId(playlistRef)` and the same `onchange`, + `playlistQualityProfileSelectHtml(playlistRef, source)`. |
| `playlistModalDownloadSyncFooterHtml` | `shared-helpers.js:1682` | `(playlistId, options = {}) => string`. Options destructured: `{hasCompletedProcess = false, isSyncing = false, source = 'spotify', closeBeforeDownload = false}`. Reads `_isSoulsyncStandalone`. Standalone ⇒ download buttons + `📂 Open in Mirrored` (`navigateToMirroredPlaylist`). Otherwise ⇒ download buttons + `<select id="sync-mode-${playlistId}" class="playlist-modal-sync-mode">` (values `''`/`replace`/`reconcile`/`append`) + `<button id="sync-btn-${playlistId}" onclick="startPlaylistSync('${playlistId}')">`. `hasCompletedProcess` swaps the primary download button for `📊 View Last Results` + `🔄 Download Missing (New)`. |
| `loadPlaylistOrganizePreferenceIntoModal` | `shared-helpers.js:1507` | `async (playlistRef, source = null) => void`. Resolves the source, `await fetchMirroredOrganizePreference(...)`, `syncPlaylistOrganizeCheckboxes(playlistRef, enabled)`, `await hydratePlaylistQualityProfileSelects(playlistRef, resolvedSource)`. Network: `GET /api/mirrored-playlists/resolve?ref=…&source=…`. |
| `playlistOrganizeSourceForRef` | `shared-helpers.js:1100` | `(playlistRef, explicitSource = null) => string`. Returns `explicitSource` if truthy, else `knownPlaylistSourceForRef(playlistRef) \|\| 'spotify'`. **The `'spotify'` fallback is fabricated** — never use it as the mirror-resolution `source` hint (use `knownPlaylistSourceForRef`, `shared-helpers.js:1084`, which returns `undefined`/null when unknown). |
| `syncPlaylistOrganizeCheckboxes` | `shared-helpers.js:1325` | `(playlistRef, enabled) => void`. Sets `.checked = !!enabled` on `#${playlistDetailsOrganizeCheckboxId(playlistRef)}` and `#playlist-folder-mode-${playlistRef}`, each null-guarded. Purely DOM. |
| `setMirroredOrganizePreference` | `shared-helpers.js:1479` | `async (playlistRef, enabled, source = null) => boolean`. `GET /api/mirrored-playlists/resolve?ref=…&source=…`; returns `false` if `!data.found \|\| !data.playlist?.id`. Then `PATCH /api/mirrored-playlists/${id}/preferences` with `{organize_by_playlist: !!enabled}`; returns `false` on `!ok` or `patchData.error`. On success calls `syncPlaylistOrganizeCheckboxes(playlistRef, !!enabled)` and returns `true`. Never throws (catches → `false`). |
| `renderModalTrackPlayButton` | **`downloads.js:2032`** (not shared-helpers) | `(playlistId, trackIndex) => string` (HTML). Returns `<button class="modal-track-play-btn" onclick="event.stopPropagation(); playDownloadModalTrack('${escapeForInlineJs(playlistId)}', ${trackIndex})" title="Play track">&#9654;</button>`. `playDownloadModalTrack` (`downloads.js:2079`) resolves the track from `activeDownloadProcesses[playlistId].tracks[trackIndex]` **or** `playlistTrackCache[playlistId][trackIndex]`, then plays via `playLibraryTrack` / `POST /api/stats/resolve-track` / `_gsPlayTrack`. |

Also useful and co-located: `isPlaylistOrganizeEnabled(playlistRef)` (`shared-helpers.js:1332`) — reads the details checkbox first, then `#playlist-folder-mode-${ref}`, `false` if neither exists; this is what `startMissingTracksProcess` prefers over reading the checkbox directly.

---

## 4. `webui/static/init.js` — page-entry seams

### `initializeSyncPage()` / `loadSyncData()`

Two invocation sites, both in `init.js`:

1. **`init.js:2885`, inside `initApp()`** — unconditional, no arguments, part of the fixed component-init sequence:
   `initializeNavigation()` → `initializeMobileNavigation()` → `initializeMediaPlayer()` → `initExpandedPlayer()` → **`initializeSyncPage()`** → `initializeWatchlist()` → `initializeSpotifyAuthCompletionListener()` (typeof-guarded) → `initializeWebSocket()` → `fetchAndUpdateServiceStatus()` + `setInterval(…, 5000)`.
   `initApp()` runs from `_continueAppInit()` (`init.js:2860`) only after `await initProfileSystem()` resolves truthy — if the profile picker is shown, app init (and therefore this call) is deferred until a profile is chosen. `initApp()` also first does `document.body.classList.remove('app-locked')`.
   **Only `initializeSyncPage()` runs here — `loadSyncData()` does not.**

2. **`init.js:3290-3291`, inside `loadPageData(pageId)`, `case 'sync':`** — the only place both run, and the only place `loadSyncData` is called anywhere in the codebase:
   ```js
   case 'sync':
       initializeSyncPage();
       await loadSyncData();
       break;
   ```
   `loadPageData` is described in-file as running "only for legacy-kind pages"; before the `switch` it unconditionally calls `stopDbStatsPolling()`, `stopDbUpdatePolling()`, `stopWishlistCountPolling()`, `stopLogPolling()`, clears `wishlistCountdownInterval`, and calls `cleanupBeatportContent()` **when `pageId !== 'sync'`**.

**Consequence for the port**: `initializeSyncPage()` is invoked once at app start plus once per navigation to `sync`. Named-function handlers (`start-sync-btn` → `startSequentialSync`, the parse buttons) are deduped by the browser (same reference), but the **anonymous ENTER-key `keypress` closures on the four URL inputs stack one per navigation** — N visits ⇒ N parse calls per Enter press.

- `initializeSyncPage` is defined at **`sync-services.js:3696`** — no params, returns `undefined`. It wires `.sync-tab-button` click handlers (activating `#${tabId}-tab-content`, forcing `.sync-sidebar` `display:'none'` and `.sync-content-area` `gridTemplateColumns:'1fr'`), lazy-loads per tab (`deezer` → `GET /api/deezer/arl-status` then `loadDeezerArlPlaylists()`; `mirrored` → `loadMirroredPlaylists()`; `server` → `loadServerPlaylists()` gated by `window._serverPlaylistsLoaded`; `beatport` → `ensureBeatportContentLoaded()`, and `cleanupBeatportContent()` when leaving it; `listenbrainz-sync` → `loadListenBrainzSyncPlaylists()` gated by `window._listenbrainzSyncTabLoaded` + `_startLbSyncCardRefreshLoop()`; `lastfm-sync` → `loadLastfmSyncPlaylists()` gated by `window._lastfmSyncTabLoaded`).
- `loadSyncData` is defined at **`sync-spotify.js:4`** — `async function loadSyncData()`, no params, returns `Promise<void>`. Body: `loadServerPlaylists()` (not awaited) gated once by `window._serverPlaylistsLoaded`; `await loadSpotifyPlaylists()` **only if `!spotifyPlaylistsLoaded`**; `await loadYouTubePlaylistsFromBackend()` (always); `initUrlHistories()`.

### `checkForActiveProcesses()`

**Not called from `init.js` at all.** Definition and both call sites:

- **Definition**: `sync-spotify.js:77` — `async function checkForActiveProcesses()`, no params, `Promise<void>`, never throws (catch → `console.error`).
  - `GET /api/active-processes` → `{active_processes: [{type: 'batch'|'youtube_playlist', playlist_id, playlist_name, batch_id, …}]}`; returns silently if `!response.ok`.
  - Splits on `p.type`. For each `type === 'batch'` entry whose `playlist_id` is **not already** a key of `activeDownloadProcesses`, calls `rehydrateModal(processInfo)` (`sync-spotify.js:535`, default `userRequested = false`) — which rebuilds the download modal, sets `process.status`/`process.batchId`, and `subscribeToDownloadBatch(batch_id)`.
  - `type === 'youtube_playlist'` entries are deliberately skipped (handled by `loadYouTubePlaylistsFromBackend()` inside `loadSyncData`).
- **Call site 1**: `sync-spotify.js:1621`, at the end of the `try` in `loadSpotifyPlaylists()` — after `spotifyPlaylists = await fetch('/api/spotify/playlists')`, `invalidatePlaylistTrackCache()`, `renderSpotifyPlaylists()`, `spotifyPlaylistsLoaded = true`.
- **Call site 2**: `sync-services.js:2461`, at the same point in `loadDeezerArlPlaylists()` — after `deezerArlPlaylists = await fetch('/api/deezer/arl-playlists')`, `invalidatePlaylistTrackCache()`, `renderDeezerArlPlaylists()`, `deezerArlPlaylistsLoaded = true`; it then additionally loops every ARL playlist doing `GET /api/sync/status/deezer_arl_${p.id}` to re-attach sync pollers.

So the effective entry chain the port must reproduce is: **navigate to `sync` → `loadPageData('sync')` → `initializeSyncPage()` + `loadSyncData()` → `loadSpotifyPlaylists()` (first visit only) → `checkForActiveProcesses()` → `rehydrateModal()` per active batch.**
