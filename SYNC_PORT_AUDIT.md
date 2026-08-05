# sync page port — P0 audit

## P0 STATUS: COMPLETE (one carry-over)

Read coverage: index.html sync block 100% · sync-spotify.js 100% ·
sync-services.js 100% (Tidal/YT/shared-core/browse prose; Qobuz/Deezer×2/
SpotifyPublic/iTunes diff-dispatched) · 3 small tab files 100% ·
auto-sync.js structure+pure-core · pages-extra server region ~100% ·
stats-automations sync regions (mirror core verbatim, import/pools
skim-verified w/ endpoints). CARRY-OVER: beatport-ui.js verbatim read rides
the Beatport-tab port phase (structure/owners known); downloads.js/core.js
engine functions get interface reads in P1 (deliberate — they stay vanilla).

## P0 headline outcomes
1. PORT SHAPE DECIDED: page → React; download engine (downloads.js+core.js)
   stays vanilla behind existing window.* seams (six React pages already
   consume them). Discovery modal ports (page-scoped) but keeps engine seams.
2. THE 7-PHASE STATE MACHINE + 4 phase maps = the P1 pure core, with the
   documented per-source drift catalog (~35 items) as parameterization.
3. LIVE BUGS for day-one knowing-fixes: Deezer sync_complete wrong re-sync
   dispatch; dead download_complete case; iTunes/LB opened-mid-sync wrong
   poller; unescaped discovery-table cells (XSS-adjacent); wing-it rendering
   transport-dependent (Tidal+Qobuz).
4. P1 SKETCH: types + pure core (phase maps, trigger/weekly codecs, mirror
   normalizer, import parsers, URL-history, id/prefix ladders, isFound
   union, transforms per source) + differential tests; then source-vertical
   controller (per-source config carrying the drift catalog); then the
   shared discovery modal; tabs in waves (small files first — near-1:1);
   auto-sync modal (most React-shaped); Beatport tab last (with
   beatport-ui.js read); flip + severs + hardening one PR like dashboard.

The 13th and last big vanilla music page. Boulder's warning confirmed: the family
carries core app functionality (THE download modal + the discovery modal + sync
engine), not just page UI.

## scope

- **Markup**: `webui/index.html` 2226–3320 (`#sync-page`, ~1,095 lines).
  Related app-level modals OUTSIDE the block: `#sync-history-overlay` (8160),
  `#matching-modal-overlay` (7902), `#add-to-wishlist-modal-overlay` (8022).
  NO static download/discovery modal markup — both are DYNAMICALLY BUILT.
- **JS family** (17,410 lines, load order per index.html 8372–8399):
  1. `sync-spotify.js` (2,622) — page orchestration (`loadSyncData`), active-process
     rehydration (`rehydrateModal` + artist-album/discover-playlist/enhanced-search
     rehydrators), backend card loaders (YouTube/Beatport/ListenBrainz), Spotify
     vertical, playlist details modal, **openDownloadMissingModal** (2193) +
     hero section + M3U export.
  2. `sync-services.js` (11,482) — NINE per-source verticals, each a ~20-fn clone:
     load/render/card/click/rehydrate/discovery-modal/discovery-poll/state-load/
     apply-state/card-phase/card-progress/sync-start/sync-poll/cancel/
     card-sync-progress/modal-sync-progress/modal-buttons/download-missing.
     Verticals: Tidal (4), Qobuz (1516), DeezerARL (2437), Deezer (2706),
     initializeSyncPage (3696), wishlist/db-update btns (4043–4237),
     Beatport browse subsystem (4238–6610: genres/charts/top10/rebuild — HUGE),
     SpotifyPublic (6611), iTunesLink (7633), URL history helpers (8654),
     YouTube (8806), shared discovery-modal core (9552–10460:
     getModalActionButtons=342 lines, getModalDescription takes 10 source bools),
     YouTube sync tail + reset (10461–10927), ListenBrainz mirror+polling (10928–end).
  3. `sync-listenbrainz.js` (364), `sync-lastfm.js` (131),
     `sync-soulsync-discovery.js` (286) — lazy tab loaders (window._*TabLoaded flags).
  4. `auto-sync.js` (2,525) — Auto-Sync schedule modal + pure cadence helpers
     (invoked from React dashboard quick-actions via window.openAutoSyncScheduleModal).

## cross-page contracts (THE port constraint)

- `openDownloadMissingModal` — called by 7 other vanilla files (beatport-ui, core,
  downloads, shared-helpers, shell-bridge, stats-automations, wishlist-tools) AND
  6 React routes (artist-detail redownload + top-tracks, discover album-open,
  label-detail open-release, search actions). **It is the app-wide download flow.**
  A sync-page port CANNOT delete it in PR 1 — either port it as a shared React
  service first, or the page port adopts it (dashboard adopted-region pattern).
- `rehydrateModal` — called from api-monitor, core, downloads, init, pages-extra,
  wishlist-tools + React dashboard-header. App-wide too.
- `generateDownloadModalHeroSection` — downloads.js + shared-helpers.js call it.
- `formatDuration` — sync-spotify.js:1967 defines a global that DUPLICATES
  shared-helpers'. Load order: shared-helpers loads BEFORE sync-spotify (verify
  which definition survives; the search-port duplicate-global trap).
- `loadSyncData`/`initializeSyncPage` — init.js only (page-entry seam).
- `window.loadBeatportTop10Lists` — sync-services wraps an EXISTING
  window.loadBeatportTop10Lists (from beatport-ui.js? verify) at 4998 —
  monkey-patch pattern, load-order-sensitive.

## script-scoped state (the modal engine's memory)

sync-spotify.js: activeAnalysisTaskId, currentPlaylistTracks, analysisResults,
missingTracks, currentDownloadBatchId, modalDownloadPoller, currentModalPlaylistId,
cancelledTracks (Set), TRACK_RENDER_BATCH_SIZE=100.
sync-services.js: rebuildPageTrackData, spotifyPublicPlaylists/States,
itunesLinkPlaylists/States, URL_HISTORY_MAX/SOURCES. (Tidal/Qobuz/Deezer state —
locate during read; likely `tidalPlaylists`/`tidalPlaylistStates` etc. declared
mid-file.)

## the REAL family (recon complete)

The page's reachable code goes beyond the 6 sync files:
- `pages-extra.js` — the whole Server-tab playlist manager (loadServerPlaylists,
  disambig, comparison editor, exportServerPlaylistM3U). PAGE CONTENT → must port.
- `stats-automations.js` — the Import-file tab (importFileClear/Reparse/Submit)
  AND openDiscoveryPoolModal/openWingItPoolModal. Import tab is PAGE CONTENT →
  must port; the two pool modals are app-level overlays (adoptable).
- `wishlist-tools.js` — openSyncHistoryModal (app-level overlay
  #sync-history-overlay at index.html 8160; adoptable).
- `manual-library-match.js` — openManualLibraryMatchTool (overlay; adoptable).
- `origin-history.js` — openDownloadOriginsModal (overlay; adoptable, shared
  with downloads page).

Port shape emerging (NOT final): the page (15 tabs incl. server manager +
import-file + beatport browse) ports to React; the app-level overlays (sync
history, library match, download origins, pools, download modal, discovery
modal) are window.*-invoked services — each either ports as a shared component
or stays vanilla behind its existing global seam. Download modal is the big
decision (12 call sites, both worlds).

## THE DOWNLOAD-ENGINE ECOSYSTEM MAP (owner-resolved — the port's interop
surface, settled)

- **downloads.js (6,055)** = THE DOWNLOAD ENGINE (survived the ADL port;
  app-wide): openDownloadMissingModalForYouTube (the generic vpid modal),
  startMissingTracksProcess (analysis+download driver),
  closeDownloadMissingModal, startModalDownloadPolling, the SEQUENTIAL SYNC
  ENGINE (startSequentialSync, sequentialSyncManager, updateCardToSyncing,
  the account startSyncPolling), openDownloadMissingWishlistModal,
  _toggleWingItDropdown, updateTrackAnalysisResults,
  _ensureErrorTooltipListeners, updateRefreshButtonState,
  checkAndCleanupGlobalPolling.
- **core.js** = the state substrate: activeDownloadProcesses,
  subscribeToDownloadBatch, WishlistModalState, currentMusicSourceName,
  _isSoulsyncStandalone, socket + _discoveryProgressCallbacks/
  _syncProgressCallbacks registries, cleanupBeatportContent.
- **shared-helpers.js**: openDownloadMissingModalForArtistAlbum,
  mirrorPlaylist, showBeatportDownloadsSection,
  hydrateBeatportBubblesFromSnapshot.
- **stats-automations.js**: loadMirroredPlaylists + the MIRRORED vertical +
  retryFailedMirroredDiscovery (+ import-file tab + pool modals).
- **wishlist-tools.js**: openDiscoveryFixModal + unmatchDiscoveryTrack (the
  manual-match fix flow inside the discovery modal) + sync history modal.
- **api-monitor.js**: initializeLiveLogViewer (the sidebar log).

**PORT SHAPE (settled by this map)**: the sync PAGE (15 tabs, verticals,
discovery modal, beatport browse) ports to React. The DOWNLOAD ENGINE
(downloads.js + core.js substrate) stays vanilla in PR 1 — the React page
calls the same window.* seams every other ported page already uses
(openDownloadMissingModal*, activeDownloadProcesses, mirrorPlaylist,
startSequentialSync...). Engine functions need INTERFACE reads (signatures +
contracts) during P1/P2, not full-file ports. The discovery modal CAN move to
React (it's page-scoped) but must keep calling the engine's seams.

## verbatim read log

(filled in as regions are read; nothing below this line is assumed — only read)

### index.html 2226–3320 — READ (full)

Shape: `.page-shell` > header + `.sync-content-area` (two-column: `.sync-main-panel`
+ `.sync-sidebar`).

**Header** (2229–2243): title w/ sync.png icon + subtitle; 4 buttons, ALL inline
onclick to globals: `openAutoSyncScheduleModal()` (auto-sync.js),
`openManualLibraryMatchTool()`, `openSyncHistoryModal()`,
`openDownloadOriginsModal('playlist')` — owners TBD (outside family?).

**15 tabs** (2249–2296), `data-tab` attrs, link-import tabs carry `data-link="true"`:
server (DEFAULT active), spotify, spotify-public, itunes-link, tidal, qobuz,
deezer (=ARL account), deezer-link (URL import), youtube, beatport,
listenbrainz-sync, lastfm-sync, soulsync-discovery-sync, import-file, mirrored.

**Tab content divs** — two archetypes:
- Account-list tabs (spotify/tidal/deezer-arl/qobuz/soulsync-discovery/lastfm/
  listenbrainz/mirrored/beatport-playlists): `.playlist-header` (h3 + refresh btn
  id `<src>-refresh-btn`) + `.playlist-scroll-container` id `<src>-playlist-container`
  + placeholder div. Quirks: deezer ARL btn id is `deezer-arl-refresh-btn` but
  container `deezer-arl-playlist-container`; deezer-LINK reuses container id
  `deezer-playlist-container` (no 'link' in id!); listenbrainz has 3 sub-tab
  buttons (`data-lb-type`: created_for_user/user_created/collaborative);
  mirrored has 2 extra pool buttons (`openDiscoveryPoolModal()`,
  `openWingItPoolModal()`) + "Update list" btn id `mirrored-refresh-btn`.
- URL-input tabs (deezer-link/youtube/spotify-public/itunes-link):
  `.youtube-input-section` (input id `<src>-url-input` + parse btn id
  `<src>-parse-btn`) + `.url-history-bar` id `<src>-url-history` (display:none)
  + scroll container. NOTE: youtube's parse btn says "Parse Playlist",
  others "Load"/"Load Playlist".

**Beatport tab** (2395–3083) — a page-within-a-page:
- Hidden nested `.beatport-tabs` (3 buttons, rebuild active; tab bar display:none —
  vestigial UI, views are driven by JS).
- `#beatport-browse-content`: `#beatport-main-view` (hero w/ HARDCODED stats
  "39 Genres • Top 100 • Daily Updates"; genre-explorer card `data-action=
  "show-genres"`; main-charts cards `data-chart-type` top-10/top-100 (both
  endpoint /api/beatport/top-100); releases cards releases-top-10/-100/latest
  (endpoints .../homepage/top-10-releases, /top-100-releases, /homepage/
  new-releases); hype cards hype-top-10/-100/hype-picks; DJ charts grid
  `#dj-charts-grid` + loading `#dj-charts-loading-inline`; featured charts grid
  `#featured-charts-grid` + `#featured-charts-loading-inline`).
- `#beatport-genres-view` sub-view: breadcrumb + 12 HARDCODED genre items
  (house/5, tech-house/11, techno/6, deep-house/12, trance/7, drum-and-bass/1,
  dubstep/18, progressive-house/15, melodic-house-and-techno/90, afro-house/89,
  minimal/14, nu-disco/50) — but hero claims 39; the rest come from
  loadBeatportGenres presumably replacing this grid.
- `#beatport-genre-detail-view` sub-view: breadcrumb (`#genre-detail-back`,
  `#genre-detail-breadcrumb`), header (`#genre-detail-title/-description`),
  chart-type cards (top-10/top-100/releases*/staff-picks/hype*) with per-card
  title ids `#genre-<type>-title`, + New Charts section (`#new-charts-content`,
  `#charts-loading-inline`, `#new-charts-grid`).
- `#beatport-genre-charts-list-view` sub-view: breadcrumb (`#genre-charts-list-back`,
  `-breadcrumb`), header ids, `#charts-loading-placeholder`, `#genre-charts-grid`.
- `#beatport-playlists-content`: My Beatport Playlists list + `#beatport-clear-btn`.
- `#beatport-rebuild-content` (ACTIVE default): hero slider (`#beatport-rebuild-
  slider/-track/-prev-btn/-next-btn` + indicators div w/o id), nav buttons
  (`#browse-by-genre-btn`, `#beatport-top100-btn`, `#hype-top100-btn`),
  `#beatport-downloads-section` (class artist-downloads-section, display:none —
  download BUBBLES, likely painted by wishlist-tools/shared bubble system —
  CROSS-PAGE seam like dashboard's), Top10 lists (`#beatport-top10-list/-tracks`,
  `#beatport-hype10-list/-tracks`), Top 10 Releases (`#beatport-releases-top10-
  list`), New Releases slider (`#beatport-releases-slider/-track/-prev/-next/
  -indicators`), Hype Picks slider (`#beatport-hype-picks-*`), Featured Charts
  slider (`#beatport-charts-*`), DJ Charts slider (`#beatport-dj-*`).

**Import-file tab** (3086–3176): upload zone (`#import-file-upload-zone`,
`#import-file-dropzone`, `#import-file-input` accept .csv/.tsv/.txt/.m3u/.m3u8),
preview section (`#import-file-preview-section`): info bar (name/count/clear btn
onclick `importFileClear()`), text-format bar (`#import-file-text-order`
artist-title|title-artist, `#import-file-text-separator` " - "/" — "/|//,
onchange `importFileReparse()`), CSV column mapping (`#import-file-column-mapping`,
`#import-file-mapping-selects`), preview table (`#import-file-preview-table/-tbody`),
action bar (name input `#import-file-playlist-name` maxlength 200 + submit btn
`#import-file-import-btn` onclick `importFileSubmit()` → "Import as Mirrored
Playlist"). Owners of importFile* globals TBD.

**Server tab** (3230–3297, DEFAULT active): `#server-tab-title`,
`#server-refresh-btn` onclick `loadServerPlaylists()`, `#server-playlist-view` >
`#server-playlist-container`; disambiguation modal INSIDE the page
(`#server-disambig-overlay/-modal`, subtitle/list ids, close onclick
`closeServerDisambig()`); full comparison editor `#server-editor` (display:none):
header (back `serverEditorBack()`, `#server-editor-name/-meta/-stats`, refresh
`_serverEditorRefresh()`), `#server-no-source-banner`, filters (`.discog-filter`
all/matched/missing/extra via `_serverEditorFilter(this,'x')` + M3U export
`exportServerPlaylistM3U()`), two compare columns (`#server-col-source/-server`
each: icon/label/count/scroll ids), `#server-editor-footer`. Owner file TBD
(not in the obvious 6? server-playlist functions weren't in the sync-services fn
inventory — grep needed).

**Sidebar** (3301–3315): Sync Actions (`#selection-info`, `#start-sync-btn`
disabled) + Sync Progress (`#sync-progress-bar`, `#sync-progress-text`,
`#sync-log-area` textarea).
### sync-spotify.js — PARTIAL (read 1–700 + 1960–2200; RESUME at 700–1960 and 2200–2622)

**loadSyncData (4–21)** — the page-entry seam (init.js calls it): server playlists
first (guarded by window._serverPlaylistsLoaded, fire-and-forget), then awaited
loadSpotifyPlaylists (guarded by script-scoped spotifyPlaylistsLoaded), then
loadYouTubePlaylistsFromBackend (ALWAYS refreshes), then initUrlHistories().
NOTE: Beatport content is NOT loaded here — lazy via ensureBeatportContentLoaded.

**ensureBeatportContentLoaded (23–75)** — lazy, memoized (beatportContentState
{loaded, loadingPromise, abortController} — declared elsewhere, find it),
abortable between EVERY step: hydrateBeatportBubblesFromSnapshot →
loadBeatportChartsFromBackend → init 5 sliders (Rebuild/Releases/HypePicks/
Charts/DJ — those initializers live in beatport-ui.js presumably) →
Promise.all(loadBeatportTop10Lists, loadBeatportTop10Releases) →
showBeatportDownloadsSection. throwIfBeatportLoadAborted between steps.

**checkForActiveProcesses (77–108)** — GET /api/active-processes; type==='batch'
→ rehydrateModal(p) for each not already in activeDownloadProcesses;
type==='youtube_playlist' deliberately skipped (loadYouTubePlaylistsFromBackend
handles them better). WHO CALLS checkForActiveProcesses? (init/core — verify.)

**The rehydration dispatcher — rehydrateModal(processInfo, userRequested=false)
(535–689)**: switches on playlist_id PREFIX:
- youtube_* / beatport_* → skip (own systems).
- artist_album_* → rehydrateArtistAlbumModal (110–203): parses
  artist_album_[artistId]_[albumId] (albumId may contain underscores — slice(3)
  join), pulls source/artistName from the EXISTING activeDownloadProcesses entry,
  GET /api/album/{albumId}/tracks?name&artist&source, artist name taken from
  tracks[0].artists[0], calls openDownloadMissingModalForArtistAlbum (EXTERNAL —
  shared-helpers/downloads?), then marks process running, sets batchId,
  subscribeToDownloadBatch(batchId), hides begin/wishlist buttons + shows cancel
  (ids begin-analysis-btn-/cancel-all-btn-/add-to-wishlist-btn-<vpid>), HIDES the
  modalElement (background rehydration).
- discover_* → rehydrateDiscoverPlaylistModal (205–382): discover_album_* → GET
  /api/spotify/album/{id} → openDownloadMissingModalForYouTube; else endpoint
  map: discover_release_radar→/api/discover/release-radar, _discovery_weekly,
  _seasonal_playlist, _popular_picks, _hidden_gems, _discovery_shuffle,
  build_playlist_custom→/api/discover/build-playlist; discover_lb_* SKIPPED (no
  LB rehydration). Track normalization: track_data_json verbatim if present,
  else constructed {id: spotify_track_id, name: track_name, artists:[{name}],
  album:{name, images from album_cover_url}, duration_ms}; artists normalized to
  STRING array.
- enhanced_search_album_/track_ → rehydrateEnhancedSearchModal (384–533): finds
  the download in searchDownloadBubbles (EXTERNAL registry, search page),
  album → GET /api/spotify/album/{id}?name&artist (Hydrabase support) →
  openDownloadMissingModalForArtistAlbum(..., false=no loading overlay);
  track → single enrichedTrack → openDownloadMissingModalForYouTube; both then
  batch-subscribe + hide modal + startModalDownloadPolling.
- "wishlist" → special: if modal already open (display==='flex') just update
  batchId + ensure polling; else ONLY create modal if userRequested
  (openDownloadMissingWishlistModal(current_cycle) — EXTERNAL, wishlist-tools),
  show + WishlistModalState.setVisible()/clearUserClosed(); background
  auto-processing NEVER creates the modal.
- deezer_arl_* → SHIM: pushes a fake entry into spotifyPlaylists (from
  deezerArlPlaylists or process info) so the SPOTIFY modal path can serve it.
- default (spotify + shimmed deezer_arl) → openDownloadMissingModal(playlist_id),
  mark running, updatePlaylistCardUI + updateRefreshButtonState,
  startModalDownloadPolling, modal hidden.

**EXTERNAL DEPENDENCIES the rehydration system leans on** (define the modal
ecosystem boundary; owners TBD next session): activeDownloadProcesses,
openDownloadMissingModalForArtistAlbum, openDownloadMissingModalForYouTube,
openDownloadMissingWishlistModal, subscribeToDownloadBatch,
startModalDownloadPolling, searchDownloadBubbles, WishlistModalState,
updatePlaylistCardUI (local), updateRefreshButtonState, spotifyPlaylists,
spotifyPlaylistsLoaded, deezerArlPlaylists, beatportContentState,
hydrateBeatportBubblesFromSnapshot, initializeBeatport*Slider (×5),
loadBeatportTop10Releases, showBeatportDownloadsSection,
isPlaylistDownloadProcessStale, showLoadingOverlay, buildArtistDetailPath,
escapeForInlineJs.

**formatDuration (1967)** — DUPLICATE of shared-helpers global, ms→m:ss.
**Modal state globals (1977–2134)**: activeAnalysisTaskId, currentPlaylistTracks,
analysisResults, missingTracks, currentDownloadBatchId, modalDownloadPoller,
currentModalPlaylistId, cancelledTracks:Set (GUI-parity), TRACK_RENDER_BATCH_SIZE=100.

**generateDownloadModalHeroSection(context) (1993–2127)** — used ALSO by
downloads.js + shared-helpers. context {type, playlist, artist, album,
trackCount, playlistId, source}. type album/artist_album: per-source artist-id
field priority table (spotify/itunes/deezer/discogs/amazon/hydrabase/musicbrainz
→ ordered candidate fields), id==name guard clears it, artistHref via
buildArtistDetailPath, album image → hero bg, artist+album imgs, artist link
onclick closes the modal. type playlist: 🎵 + owner. wishlist: 👁️ "From watched
artists". default: 📥. ALWAYS appends the 4-stat dashboard (ids
stat-total/-found/-missing/-downloaded-<playlistId>) + close × onclick
closeDownloadMissingModal('<playlistId>'||'unknown').

**applyProgressiveTrackRendering (2136–2191)** — >100 rows: hide rest, "Showing
N of M" indicator appended into .download-tracks-title, scroll-bottom<200px
reveals next 100, listener self-removes at end. Modal DOM ids:
download-missing-modal-<pid>, download-tracks-tbody-<pid>, rows tr[data-track-index].

**openDownloadMissingModal(playlistId) (2193–2409) — READ IN FULL.** The
canonical spotify/deezer-arl playlist download modal:
1. showLoadingOverlay. Staleness: isPlaylistDownloadProcessStale(playlistId,
   meta) if defined, else playlistTrackCacheIsStale(playlistId, meta) (both
   OPTIONAL globals — typeof-guarded).
2. Existing non-stale process → close details modal, re-show its modalElement
   (display:flex), toast if status==='complete' ("Showing previous results…
   Download Missing (New) for a fresh run"),
   refreshOrganizePreferenceForDownloadModal if defined, done.
3. Stale + existing → restartPlaylistDownloadMissing if defined (returns), else
   clearPlaylistDownloadProcess, else invalidatePlaylistTrackCache. (A whole
   optional-globals PROTOCOL — these live in wishlist-tools/shared-helpers;
   map owners next.)
4. Track fetch w/ playlistTrackCache: deezer_arl_* → GET
   /api/deezer/arl-playlist/{rawId}; else fetchAndCacheSpotifyPlaylistTracks if
   defined; else GET /api/spotify/playlist/{id} (cache write in both fallbacks).
5. Creates div#download-missing-modal-<pid> class download-missing-modal
   appended to BODY (display:none), registers activeDownloadProcesses[pid] =
   {status:'idle'|running|complete|cancelled, modalElement, poller:null,
   batchId:null, playlist, tracks}, sets currentPlaylistTracks +
   currentModalPlaylistId.
6. innerHTML: .download-missing-modal-content[data-context=playlist] > header
   (hero via generateDownloadModalHeroSection type:'playlist') + body
   (two progress bars: analysis-progress-text/-fill-<pid>,
   download-progress-text/-fill-<pid>; tracks table: select-all checkbox
   onchange toggleAllTrackSelections, per-row checkbox onchange
   updateTrackSelectionCount, #, name w/ renderModalTrackPlayButton(pid,idx),
   artist via formatArtists, duration via formatDuration, match-<pid>-<idx>
   "🔍 Pending", download-<pid>-<idx> "-", actions-<pid>-<idx> "-";
   track-selection-count-<pid> "N / N tracks selected") + footer
   (force-download-all-<pid> checkbox; organize checkbox via OPTIONAL
   downloadMissingModalOrganizeCheckboxHtml else bare playlist-folder-mode-<pid>
   checkbox; Begin Analysis btn → startMissingTracksProcess('<pid>');
   Add to Wishlist (#9333ea) → addModalTracksToWishlist; Cancel All hidden →
   cancelAllOperations; Export as M3U → exportPlaylistAsM3U; Close →
   closeDownloadMissingModal).
7. applyProgressiveTrackRendering, applyMirroredOrganizePreference(pid) (owner
   TBD), display:flex, hideLoadingOverlay.

More external/optional globals from this fn: playlistTrackCache,
fetchAndCacheSpotifyPlaylistTracks, restartPlaylistDownloadMissing,
clearPlaylistDownloadProcess, invalidatePlaylistTrackCache,
refreshOrganizePreferenceForDownloadModal, downloadMissingModalOrganizeCheckboxHtml,
applyMirroredOrganizePreference, renderModalTrackPlayButton, formatArtists,
showToast, showLoadingOverlay/hideLoadingOverlay, startMissingTracksProcess,
addModalTracksToWishlist, cancelAllOperations, toggleAllTrackSelections,
updateTrackSelectionCount, closeDownloadMissingModal.
(startMissingTracksProcess/closeDownloadMissingModal/cancelAllOperations are the
next must-reads — likely in shared-helpers.js or downloads.js, NOT this file.)

**M3U tail (2411–2622) — READ IN FULL.**
- autoSavePlaylistM3U(pid): server-side auto-save after downloads, PLAYLIST
  contexts only — skip prefix list: artist_album_, discover_album_,
  enhanced_search_album_/track_, seasonal_album_, spotify_library_,
  beatport_release_, discover_cache_, issue_download_, library_redownload_,
  redownload_. POST /api/generate-playlist-m3u {playlist_name, tracks via
  _extractM3UTracks, context_type:'playlist', artist/album/year from process,
  save_to_disk:true} — server enforces m3u_export.enabled. Errors non-critical.
- generateM3UContent(pid): CLIENT-side M3U8 builder that READS STATUS FROM THE
  DOM (match-/download-<pid>-<idx> textContent includes 'Found'/'Completed'/
  'Missing') → #EXTINF + #STATUS lines + sanitized "Artist - Track.mp3" paths
  (missing → commented "# NOT AVAILABLE") + #SUMMARY block. NOTE: apparently
  VESTIGIAL for export (exportPlaylistAsM3U now uses the API) — verify callers;
  DOM-as-state is a port hazard if still live.
- exportPlaylistAsM3U(pid): API-backed (context_type album vs playlist via
  album-prefix list — note this list LACKS issue_download_/library_redownload_/
  redownload_ vs autoSave's), force:true + save_to_disk:true, then browser blob
  download `<name>.m3u`, toast w/ found+downloaded vs missing stats.
- _extractM3UTracks: {name, artist (first of artists array|string|artist obj),
  duration_ms} — tolerant artist normalization.

**695–1960 — READ (file now COMPLETE).**

Backend card hydration (the sync page's persistence layer):
- loadYouTubePlaylistsFromBackend (695–843): GET /api/youtube/playlists; per
  playlist: if card exists in youtubePlaylistStates AND still in DOM → update
  state fields (phase/discoveryProgress/spotifyMatches/convertedSpotifyPlaylistId)
  + lazily fetch /api/youtube/state/<hash> for discovery results when phase not
  fresh/discovering; else createYouTubeCardFromBackendState + same state fetch.
  SECOND PASS: for phases downloading/download_complete w/ converted id +
  download_process_id and no activeDownloadProcesses entry → rebuild the
  download modal (openDownloadMissingModalForYouTube with spotify_data from
  discoveryResults), mark running, batch id, poll, HIDE modal.
- loadBeatportChartsFromBackend (845–1052): same shape via /api/beatport/charts
  + /api/beatport/charts/status/<hash>, ABORTABLE (getBeatportContentSignal +
  throwIfBeatportLoadAborted; AbortError rethrown/tolerated at each layer).
  CRITICAL DESIGN FACT: **Beatport stores its modal state INSIDE
  youtubePlaylistStates[chartHash]** ({is_beatport_playlist: true,
  beatport_chart_type/hash...}) — the "YouTube discovery modal" is the shared
  discovery modal for all url-hash sources. Transforms backend results to the
  modal row shape (yt_track/yt_artist/status '✅ Found'/status_class/
  spotify_track/artist/album) — DUAL snake+camel keys stored side by side
  (discovery_results AND discoveryResults etc.). Rehydrates download modals
  like YouTube (status 'complete' when download_complete). Auto-resumes
  discovery polling for 'discovering' charts; updateBeatportClearButtonState.
- loadListenBrainzPlaylistsFromBackend (1054–1159): /api/listenbrainz/playlists
  + /state/<mbid>; NO cards created (the LB tab renders elsewhere) — restores
  listenbrainzPlaylistStates[mbid] w/ dual-key naming + transformed rows;
  resumes discovery polling; shows sync btn by id
  `discover-lb-playlist-<mbid>-sync-btn`; listenbrainzPlaylistsLoaded set even
  on error (no retries).
- createBeatportCardFromBackendState (1161)/createYouTubeCardFromBackendState
  (1344): insertAdjacentHTML cards (.youtube-playlist-card, id beatport-card-/
  youtube-card-<hash>), progress line "♪ total / ✓ matched / ✗ failed (N%)",
  YouTube card uses INLINE onclick=handleYouTubeCardClick, Beatport binds
  addEventListener. beatportChartStates[chartHash] = {phase, chart, cardElement}.
- **The 7-phase lifecycle** (the sync page's core state machine): fresh →
  discovering → discovered → syncing → sync_complete → downloading →
  download_complete. Pure maps: getActionButtonText (Discover/View Progress/
  View Results/View Sync/Download/View Downloads/Complete/default Open),
  getPhaseText, getPhaseColor (#999 / #ffa500 in-progress / accent for
  completed), getProgressWidth (matches/total %, fresh→0, total 0→0).
  PRIME differential-test targets.
- rehydrateYouTubePlaylist (1435–1539): ensures card (fetching full state if
  missing), restores results, resumes discovery/sync polling by phase,
  userRequested → opens discovery modal (discovering..sync_complete) or
  download modal (downloading/download_complete via converted id).
- removeYouTubePlaylistFromBackend (1541): DELETE /api/youtube/delete/<hash>,
  removes card+state+poller (activeYouTubePollers)+modal, restores placeholder.

Spotify vertical (1598–1965):
- loadSpotifyPlaylists: /api/spotify/playlists → spotifyPlaylists;
  invalidatePlaylistTrackCache (optional global, else playlistTrackCache={});
  renderSpotifyPlaylists; spotifyPlaylistsLoaded=true; THEN
  checkForActiveProcesses() (rehydration entry!).
- renderSpotifyPlaylists: cards w/ sync_status classes (status-synced /
  status-needs-sync for 'Needs Sync' or 'Last Sync…' / status-never-synced),
  inline onclick togglePlaylistSelection + openPlaylistDetailsModal +
  handleViewProgressClick (progress-btn hidden by default), per-card
  #progress-<id> indicator div.
- Selection model: selectedPlaylists Set + updateSyncActionsUI (defers to
  sequentialSyncManager.updateUI() when running — the sidebar Start Sync
  engine lives ELSEWHERE, find sequentialSyncManager owner).
- updatePlaylistCardUI: 3 states — running (View Progress, action '📥
  Downloading...' disabled), complete ('📋 View Results' green #28a745,
  '✅ Ready for Review', card.download-complete class), else reset.
- cleanupDownloadProcess (1723–1792): stop poller, status→complete, POST
  /api/playlists/cleanup_batch {batch_id}; 202 = wishlist processing → RETRY
  once after 2s; remove modal from DOM, delete process,
  checkAndCleanupGlobalPolling, card UI restore (not for 'wishlist'),
  updateRefreshButtonState.
- openPlaylistDetailsModal: cache-or-fetch (playlistTrackCacheIsStale /
  fetchAndCacheSpotifyPlaylistTracks optional-global protocol again).
- showPlaylistDetailsModal (1878–1958): singleton #playlist-details-modal
  appended to body; sync-status stat spans (modal-total/-matched/-failed/
  -percentage-<id>, hidden by default); tracks list; footer hooks ALL
  typeof-guarded optional globals: playlistOrganizeToggleHtml(id,'spotify'),
  playlistModalDownloadSyncFooterHtml(id,{hasCompletedProcess,isSyncing,
  source}) else bare Download Missing button,
  loadPlaylistOrganizePreferenceIntoModal. isSyncing = activeSyncPollers[id].

NEW externals surfaced (owners TBD): youtubePlaylistStates, beatportChartStates,
listenbrainzPlaylistStates, listenbrainzPlaylistsLoaded, activeYouTubePollers,
activeSyncPollers, selectedPlaylists, sequentialSyncManager,
checkAndCleanupGlobalPolling, updateRefreshButtonState, getBeatportContentSignal,
throwIfBeatportLoadAborted, beatportContentState, playlistOrganizeToggleHtml,
playlistModalDownloadSyncFooterHtml, loadPlaylistOrganizePreferenceIntoModal,
playlistTrackCache, spotifyPlaylistsLoaded, sequential sync engine.

ORIGINAL remaining-notes (region now read): 700–1960 (YouTube/Beatport/LB backend hydration loaders,
createBeatportCardFromBackendState, rehydrateBeatportChart/YouTubePlaylist,
phase helpers getActionButtonText/getPhaseText/getPhaseColor/getProgressWidth,
Spotify vertical loadSpotifyPlaylists→renderSpotifyPlaylists→cards, selection
togglePlaylistSelection/updateSyncActionsUI, playlist details modal,
cleanupDownloadProcess) and 2411–2622 (autoSavePlaylistM3U, generateM3UContent,
exportPlaylistAsM3U, _extractM3UTracks).
### sync-services.js verticals — IN PROGRESS (read 1–480; RESUME at 480)

**Tidal 230–479:**
- rehydrateTidalDownloadModal (230–331): lazily fetches /api/tidal/state/<id>
  when discovery_results missing; spotify tracks = results w/ spotify_data;
  builds via openDownloadMissingModalForTidal (LOCAL to this file, 1312);
  download_process_id → status running|complete by phase; downloading → hide
  begin/show cancel + startModalDownloadPolling; download_complete → hide both
  + ONE-SHOT fetch /api/playlists/<batch>/download_status →
  updateCompletedModalResults (only when data.phase==='complete' && tasks).
- updateCompletedModalResults (333–426): final paint of a completed download
  modal. Analysis bar → 100%; updateTrackAnalysisResults(analysis_results)
  (EXTERNAL) + stat-found/-missing counts; per task row (selector
  #download-missing-modal-<CSS.escape(pid)> tr[data-track-index]): full status
  text map — pending ⏸️/searching 🔍/downloading ⏬ N%/post_processing ⌛
  (verification workflow)/completed ✅/not_found 🔇/failed ❌/cancelled 🚫/
  default ⚪ <raw>; failed|cancelled|not_found + error_message →
  .has-error-tooltip + dataset.errorMsg + _ensureErrorTooltipListeners
  (EXTERNAL); not_found + has_candidates → .has-candidates + dataset.taskId +
  _ensureCandidatesClickListener (EXTERNAL — the manual-match candidates flow);
  actions cell wiped '-'; download bar = finished/missing % (100 if none),
  text "N/M completed (P%)", stat-downloaded = completedCount.
- updateTidalCardPhase (428–472): state.phase set; card re-render via
  outerHTML (createTidalCard) + RE-ATTACH click handler; SELF-VERIFYING debug
  (compares rendered button text vs getActionButtonText, logs error on
  mismatch); syncing/sync_complete + state.lastSyncProgress → setTimeout(0)
  updateTidalCardSyncProgress.
- **openTidalDiscoveryModal (474+): fake urlHash `tidal_<playlistId>` — Tidal
  ALSO piggybacks the YouTube modal system.** Pattern confirmed for 3 sources
  now (Beatport chartHash, LB mbid, Tidal fake hash); expect the same for
  Qobuz/Deezer/SpotifyPublic/iTunesLink verticals.

More externals: updateTrackAnalysisResults, _ensureErrorTooltipListeners,
_ensureCandidatesClickListener (owners TBD — shared-helpers/downloads.js).

**Tidal 480–773 (modal open tail + discovery polling):**
- openTidalDiscoveryModal tail: builds transformedResults from stored
  discovery_results (isFound = status 'found'|'✅ Found'|status_class 'found'|
  spotify_data present|spotify_track present — LENIENT), actualMatches counted
  from that; fake youtubePlaylistStates['tidal_<id>'] w/ is_tidal_playlist +
  tidal_playlist_id + dual keys; NOT-discovered path: phase FORCED 'discovering'
  BEFORE open (renders the non-clickable "Discovering matches…" footer, #867),
  openYouTubeDiscoveryModal first, .modal-description text swapped ('Loading
  playlist from Tidal…' → 'Discovering tracks…'/'Could not start discovery.'),
  POST /api/tidal/discovery/start/<id>, then updateTidalCardPhase +
  startTidalDiscoveryPolling, EARLY RETURN. Resume paths: discovering →
  re-poll; syncing → startTidalSyncPolling; else use existing results; shared
  open at bottom.
- startTidalDiscoveryPolling (612–773): **DUAL TRANSPORT**. (a) if
  socketConnected: socket.emit('discovery:subscribe',{ids:[playlistId]}) +
  _discoveryProgressCallbacks[playlistId] (EXTERNALS: socket, socketConnected,
  _discoveryProgressCallbacks — core.js); callback transform HANDLES WING IT
  (wing_it_fallback || status_class 'wing-it' → '🎯 Wing It'/'wing-it'), updates
  both fake + tidal states (dual keys) + modal + card, unsubscribes on
  complete/error. (b) ALWAYS a 1s HTTP poll of /api/tidal/discovery/status/<id>
  regardless of socket ("Always poll — no dedicated WebSocket events" — comment
  contradicts the subscribe above!). **DRIFT FOUND: the HTTP poll's transform
  has NO wing-it handling** — a wing-it row renders '✅ Found' via polling but
  '🎯 Wing It' via socket. Real transport-dependent rendering bug to preserve
  or fix knowingly. Poller stored in activeYouTubePollers[fakeUrlHash]; both
  transports clear it on complete.

**Tidal 775–1319 (state hydration + sync machinery + download handoff):**
- loadTidalPlaylistStatesFromBackend (775–862): /api/tidal/playlists/states →
  applyTidalPlaylistState per state; SECOND PASS rehydrates download modals
  (same shape as YouTube/Beatport: phase downloading|download_complete +
  converted id + process id → openDownloadMissingModalForTidal, mark running,
  poll, but NOTE: does NOT hide the modal — unlike YouTube's which sets
  display:none. Verify on port: Tidal state-hydration may flash modals?
  Actually check openDownloadMissingModalForTidal's default display).
- applyTidalPlaylistState (864–949): merges backend state (camel
  convertedSpotifyPlaylistId + snake download_process_id mixed on the SAME
  object); non-fresh/non-discovering → fetch /api/tidal/state/<id> full
  results; updateTidalCardPhase; 'discovered' → updateTidalCardProgress with
  spotify_total from track_count||tracks.length; resumes discovery/sync
  polling with fake hash `tidal_<id>`.
- updateTidalCardProgress (951): paints "♪ T / ✓ M / ✗ F / P%" text into
  .playlist-card-progress + unhides. (Discovery format: slash-separated TEXT;
  sync format below is HTML spans — two different progress renderings on the
  same element.)
- **Sync machinery (976–1157)**: startTidalPlaylistSync → POST
  /api/tidal/sync/start/<id> → syncPlaylistId captured on the fake state →
  card+modal to 'syncing' → startTidalSyncPolling. startTidalSyncPolling:
  socket 'sync:subscribe' {playlist_ids:[syncPlaylistId]} +
  _syncProgressCallbacks[syncId] (EXTERNAL registry, core.js); status
  finished → sync_complete both states + toast; error|cancelled → REVERT to
  'discovered'. HTTP fallback poll (1s) **SKIPS when socketConnected**
  (`if (socketConnected) return;`) — ASYMMETRIC with discovery polling which
  always polls. Immediate first poll only when no socket. Poll complete →
  sync_complete; sync_status 'error' → discovered.
- cancelTidalSync (1112): POST /api/tidal/sync/cancel/<id>, stop poller,
  unsubscribe WS, revert card+modal to 'discovered'.
- updateTidalCardSyncProgress (1159): saves state.lastSyncProgress (used by
  the phase re-render restore); HTML span block (♪/✓/✗/(P%)) only when
  total_tracks>0 — empty progress preserves discovery text.
- updateTidalModalSyncProgress (1200): updates ids tidal-sync-status-<hash>,
  tidal-total/-matched/-failed/-percentage-<hash> (modal sync counters).
- updateTidalModalButtons (1230): delegates to setDiscoveryModalFooterActions
  (the SHARED footer state machine at 9569).
- startTidalDownloadMissing (1237–1310): builds spotifyTracks from
  discoveryResults||discovery_results; TWO row formats: spotify_data verbatim
  OR reconstructed from flat fields (spotify_track + status_class 'found' →
  {id: spotify_id||'unknown', name, artists:[spotify_artist], album coerced to
  OBJECT for wishlist compat, duration_ms: 0}); virtual playlist id
  `tidal_<tidal_playlist_id>` stored as convertedSpotifyPlaylistId; hides
  discovery modal (classList 'hidden'); opens
  openDownloadMissingModalForTidal. Phase flips to 'downloading' only when
  user clicks Begin Analysis.
- **openDownloadMissingModalForTidal (1312–1503) — READ IN FULL. Misnamed:
  it is THE GENERIC virtual-playlist download modal.** Source-name ladder
  (1362–75) maps prefix → hero 'owner': beatport_→Beatport, tidal_→Tidal,
  qobuz_→Qobuz, listenbrainz_→ListenBrainz, spotify_public_→Spotify,
  itunes_link_→iTunes, spotify:→Spotify, discover_/seasonal_/spotify_library_/
  build_playlist_/decade_/build_playlist_custom→SoulSync, else YouTube.
  Deltas vs the spotify-account modal (sync-spotify.js 2193): seeds
  playlistTrackCache[vpid]=tracks directly (no fetch); NO staleness protocol;
  NO Export-as-M3U button in footer; organize-checkbox fallback INCLUDES the
  full label ("Organize by Playlist (Downloads/Playlist/Artist - Track.ext)")
  vs bare input; extra options.forcePlaylistFolder →
  syncPlaylistOrganizeCheckboxes(vpid,true) + setMirroredOrganizePreference;
  orgSource via playlistOrganizeSourceForRef(vpid) else 'spotify'. Toast text
  differs too ('Close this modal to start a new analysis' vs 'Use "Download
  Missing (New)" for a fresh run'). Everything else (hero/progress/table/
  buttons/progressive rendering) byte-similar.
- **TIDAL VERTICAL COMPLETE (1–1515).**

**Qobuz vertical (1516–2436): DIFF-READ COMPLETE vs Tidal** (normalized diff,
scratchpad qobuz_diff.txt). Header comment: "mirrors Tidal — #677, /api/qobuz/*
mirrors /api/tidal/* one-for-one". Card key `qobuz-card-<id>` (no colon — CSS
pseudo-class constraint). VERDICT: behaviorally identical EXCEPT:
1. **Qobuz NEVER GOT THE #867 open-modal-immediately UX.** Fresh card click
   BLOCKS: showLoadingOverlay → await /api/qobuz/playlist/<id> tracks fetch
   (mapped {id,name,artists,album,duration_ms,track_number}) → error toast +
   return if no tracks → THEN opens the discovery modal.
   openQobuzDiscoveryModal correspondingly LACKS the whole open-first block
   (no forced 'discovering' phase, no early open + .modal-description swap,
   no early return). Port decision: give Qobuz the #867 UX (parameterize) or
   preserve the blocking flow. RECOMMEND unify to #867 as a knowing change.
2. **Extra status literal**: Qobuz isFound checks add `status === 'Found'`
   (capital, no emoji) in openDiscoveryModal + BOTH startDiscoveryPolling
   transforms (socket + poll). The qobuz backend presumably emits 'Found'.
   The unified controller's isFound must accept the union of all formats.
3. updateQobuzCardPhase DROPS Tidal's self-verifying debug (no behavior).
4. updateQobuzModalButtons guards `.modal-footer-left` exists before
   setDiscoveryModalFooterActions (Tidal calls unconditionally) — behavioral
   only if footer absent.
Everything else = comment/log stripping + statement compaction; rehydrate
keeps the download_status/updateCompletedModalResults one-shot (verified);
sync polling keeps the socket-skip HTTP fallback; wing-it drift (poll
transform lacks wing-it) EXISTS IN QOBUZ TOO (inherited from the clone).

**DeezerARL vertical (2437–2700): PROSE-READ COMPLETE.** Clones the
SPOTIFY-ACCOUNT archetype (not Tidal): /api/deezer/arl-playlists →
deezerArlPlaylists → cards (playlist-card w/ arlId `deezer_arl_<id>`, inline
onclick handlers, sync-status classes but NO 'Needs Sync' branch — only
Synced/Never), details modal #deezer-arl-playlist-details-modal (mirrors the
spotify one; footer hook source 'deezer' + closeBeforeDownload:true; fallback
btn closes modal BEFORE openDownloadMissingModal). THE SHIM: pushes an
arl-playlist entry into spotifyPlaylists (both at modal open AND sync
rehydration) so openDownloadMissingModal + the sync engine serve it.
Sync rehydration on load: per playlist GET /api/sync/status/deezer_arl_<id>;
'syncing' → shim + updateCardToSyncing + startSyncPolling (EXTERNALS — the
ACCOUNT sync engine, owner TBD, likely wishlist-tools/shared). Cache via
playlistTrackCache[arlId] + fetchAndCacheDeezerArlPlaylistTracks (optional
global). updateDeezerArlPlaylistCardUI = literal clone of updatePlaylistCardUI
w/ prefix. NOTE deezerArlPlaylistsLoaded flag.

**Deezer-LINK vertical (2706–3695): DIFF-READ COMPLETE vs Tidal.** URL-parse
head (2706–2833): regex deezer.com/[{locale}/]playlist/{id} OR raw numeric;
dedupe by String(id); GET /api/deezer/playlist/<id> → deezerPlaylists;
AUTO-MIRRORS w/ description = THE RAW URL; saveUrlHistory('deezer', url,
name); render seeds deezerPlaylistStates fresh; shared phase maps.
Divergences vs Tidal (rest is comment/compaction noise):
1. NO #867 open-first UX (same as Qobuz — only Tidal has it).
2. rehydrateDeezerDownloadModal is a STUB by comparison (35L vs 102L): no
   backend state-fetch fallback, no download_complete one-shot
   /download_status + updateCompletedModalResults paint — reopening a
   completed Deezer download shows an UNPAINTED modal. Uses
   openDownloadMissingModalForTidal directly w/ vpid fallback `deezer_<id>`.
3. **Deezer's discovery HTTP poll SKIPS when socketConnected** (`if
   (socketConnected) return;`) — Tidal/Qobuz always-poll. Third transport
   variant.
4. updateDeezerCardProgress renders a DIFFERENT visual: HTML spans "✓ M / ♪ T"
   (no failed count, no percent) vs Tidal's text "♪/✓/✗/%".
5. String(id) coercion in all find()s (numeric Deezer ids).
6. Drops the self-verify debug; footer-left guard like Qobuz; no 'Found'
   literal (Tidal-style status set).
Deezer has NO openDownloadMissingModalForDeezer — calls the (generic)
...ForTidal everywhere.

**initializeSyncPage (3696–4036): READ IN FULL — the page wiring hub.**
- Tab switching: .sync-tab-button active classes + `${tabId}-tab-content`
  active; SIDEBAR FORCED HIDDEN on every switch (display none +
  gridTemplateColumns '1fr' — sidebar only reappears when a sync runs,
  elsewhere; isMobile var computed but UNUSED — vestigial).
- Per-tab lazy loads (each guarded once): deezer → GET /api/deezer/arl-status
  first: authenticated → loadDeezerArlPlaylists, else 'ARL not configured'
  placeholder; mirrored → loadMirroredPlaylists (mirroredPlaylistsLoaded);
  server → loadServerPlaylists (window._serverPlaylistsLoaded); beatport →
  ensureBeatportContentLoaded AND leaving beatport → cleanupBeatportContent()
  (the abort seam!); listenbrainz-sync → loadListenBrainzSyncPlaylists
  (window._listenbrainzSyncTabLoaded) + _startLbSyncCardRefreshLoop (500ms
  card refresh that auto-stops when tab loses active — parity w/ Tidal live
  updates); lastfm-sync → same machinery (SHARES listenbrainzPlaylistStates);
  soulsync-discovery-sync → loadSoulsyncDiscoverySyncPlaylists. Comment
  explains listenbrainz-sync tab id avoids colliding with the (old) Discover
  page's listenbrainz-tab-content.
- If beatport tab already active at init → ensureBeatportContentLoaded.
- Refresh buttons (spotify/tidal/deezer-arl/qobuz) wired with
  removeEventListener-then-add (re-init safety); parse buttons + ENTER-key on
  all 4 URL inputs (deezer/youtube/spotify-public/itunes-link); mirrored
  refresh; _initImportFileTab() (stats-automations); beatport clear +
  updateBeatportClearButtonState; Beatport nested tabs (rebuild →
  ensureBeatportContentLoaded), genre-explorer card, homepage chart handlers,
  breadcrumb backs (3 targets by id), chart/genre item handlers, top10
  container clicks, hero-slider handlers set in populateBeatportSlider;
  Start Sync btn → startSequentialSync (the sidebar engine — EXTERNAL);
  beatport-top100/hype-top100 btns; initializeLiveLogViewer() (EXTERNAL).
New externals: loadMirroredPlaylists, mirroredPlaylistsLoaded,
cleanupBeatportContent, loadListenBrainzSyncPlaylists,
_startLbSyncCardRefreshLoop, loadLastfmSyncPlaylists,
loadSoulsyncDiscoverySyncPlaylists, startSequentialSync,
initializeLiveLogViewer, handleBeatportTop100Click, handleHypeTop100Click,
_initImportFileTab, populateBeatportSlider.

**Buttons region (4043–4282): READ.**
- handleDbUpdateButtonClick (4043–4111): toggle by button TEXT ('Update
  Database' vs anything else = stop branch). full → confirm dialog; deep →
  confirm dialog (adds/removes/preserves copy). #859 hardening: button stays
  ENABLED during 'Starting...' (doubles as cancel affordance, second click
  falls to stop branch); POST /api/database/update {deep_scan:true} XOR
  {full_refresh:bool} (deep takes precedence server-side, send only one);
  checks response.ok AND data.success!==false; then checkAndUpdateDbProgress()
  + armDbUpdateSafetyPoll() (EXTERNALS — socket-independent recovery, #859).
  Stop → POST /api/database/update/stop.
  NOTE: #db-update-button/#db-refresh-type live on the DASHBOARD (React now!)
  — verify who calls this post-dashboard-flip; may be sync-page's own copy of
  ids or dead code. CHECK REACHABILITY.
- cleanupWishlist (4114): confirm → POST /api/wishlist/cleanup → toast w/
  removed/processed counts; >0 removed → reopen
  openDownloadMissingWishlistModal after 500ms + updateWishlistCount
  (EXTERNAL). Buttons cleanup-wishlist-btn-<pid> text states.
- clearWishlist (4176): destructive confirm ('Clear All') → POST
  /api/wishlist/clear → closeDownloadMissingModal(pid) + updateWishlistCount.
- updateBeatportClearButtonState (4238): 3-state clear button (no charts →
  disabled '🗑️ Clear' 0.5; active charts (discovering/syncing/downloading) →
  '🚫 Clear Blocked' + title lists active names; else enabled).

**SHARED DISCOVERY MODAL CORE — openYouTubeDiscoveryModal (9302–9550): READ.**
- State lookup ORDER: listenbrainzPlaylistStates[urlHash] FIRST, else
  youtubePlaylistStates[urlHash] (LB keys are mbids — collision-safe only by
  luck of id shapes).
- Existing modal → classList.remove('hidden') + resume polling by phase:
  discovering → startYouTubeDiscoveryPolling (ALWAYS the YT one — per-source
  discovery pollers only started from their own verticals); syncing → NINE-WAY
  source dispatch (is_tidal/is_qobuz/is_deezer/is_spotify_public/
  is_itunes_link/is_beatport/is_listenbrainz/else YT).
- New modal: source booleans from state flags + isLastfmRadio detected by
  URL-HASH PREFIX 'lastfm_radio_' (not a state flag — inconsistent);
  isMirrored w/ state.mirrored_source label. Title + sourceLabel ladders (10
  variants; LB label 'LB', YT 'YT'). Table headers use currentMusicSourceName
  (EXTERNAL — active metadata source display name) for the match columns.
  Body: progress bar ids youtube-discovery-progress[-text]-<hash>, table
  youtube-discovery-table-<hash> seeded by generateTableRowsFromState.
  Footer: buildDiscoveryModalFooterLeftHtml + Close.
- **NESTED 'Fix Track Match' modal** (#discovery-fix-modal-overlay, one per
  discovery modal, STATIC ids — only one can exist at a time!): source track
  display, track+artist search (searchDiscoveryFix), **MBID escape hatch**
  (paste MB recording URL/UUID → lookupDiscoveryFixByMbid), results div,
  cancel. The manual-match flow lives INSIDE the discovery modal.
- Progress seeding on open: discoveryProgress||computed from
  results.length/tracks.length; matches fallback counts status_class 'found'.
- **DRIFT: the opened-in-syncing immediate-polling dispatch (9525–9540) OMITS
  is_itunes_link AND is_listenbrainz** — an iTunes-Link or LB playlist whose
  modal opens mid-sync falls into startYouTubeSyncPolling (wrong endpoints).
  The resume-on-reopen dispatch above has all 8. Real inconsistency.
- spotify_public: organize preference loaded into modal
  (ref spotify_public_<id>, source 'spotify_public').
- discoveryModalOrganizeFooterHtml (9552): organize toggle only for
  spotify_public states.

**THE FOOTER STATE MACHINE — getModalActionButtons (9588–9928) + generators
(9930–10095): READ IN FULL.** The port's single most drift-dense region:
- buildDiscoveryModalFooterLeftHtml = organize-footer (spotify_public only) +
  action buttons. setDiscoveryModalFooterActions re-renders footer-left +
  reloads spotify_public organize pref.
- Phase switch:
  * fresh → Start Discovery (LB variant startListenBrainzDiscovery, else
    startYouTubeDiscovery — ALL sources' discovery start goes through the YT
    fn?? no — per-source verticals auto-start; this button only serves YT/LB/
    mirrored flows) + ⚡ Wing It (_toggleWingItDropdown EXTERNAL dropdown).
  * discovering → info text only.
  * 'discovered'+'downloading'+'download_complete' (ONE case group):
    Sync button 8-way dispatch, gated hasSpotifyMatches &&
    !_isSoulsyncStandalone (EXTERNAL standalone-mode flag — no media server:
    sync hidden everywhere); Download button 8-way — **LB's download uses
    startYouTubeDownloadMissing** (no LB-specific fn); spotify_public in
    standalone becomes '📁 Download to Playlist Folder' + forcePlaylistFolder
    arg + extra class; Mirrored → Retry Failed (N) via
    retryFailedMirroredDiscovery when failedCount>0; Rediscover ONLY for
    Beatport (resetBeatportChart) + YT/mirrored (resetYouTubePlaylist) —
    others lack reset endpoints (documented); Wing It always; no-buttons →
    'No Spotify matches found' info prefix.
  * syncing → per-source Cancel + sync-status span row. **QOBUZ DELIBERATELY
    REUSES THE tidal-* ID NAMESPACE** (comment: updateQobuzModalSyncProgress
    targets those exact IDs, markup byte-identical to skip a CSS pass).
    **LB's cancel is cancelYouTubeSync** (no LB cancel fn).
  * sync_complete → sync + download 8-way again BUT **THE SYNC DISPATCH OMITS
    isDeezer** — a Deezer-link playlist in sync_complete renders the YOUTUBE
    re-sync button (startYouTubePlaylistSync → wrong endpoints). REAL BUG.
  * **DEAD CODE: a second `case 'download_complete':` (9867–9923) after the
    first case group already claims it** — unreachable (first match wins);
    the dead branch differs (has isDeezer, lacks Wing It + standalone gate on
    sync). Port: delete knowingly; its existence explains the sync_complete
    omission (someone edited the dead twin).
- getModalDescription/getInitialProgressText (9930–9959): the 10 boolean
  params are VESTIGIAL except the source-name ladder; description only varies
  fresh/discovering vs completed; uses currentMusicSourceName.
- generateTableRowsFromState (9961): platform ladder (9 values incl
  'mirrored'); dual-key results; source track/artist field fallbacks
  lb_*→yt_*→track_name; **#863 fix: 'Unknown Artist' falls back to the
  MATCHED spotify_artist**; row ids discovery-row-<hash>-<result.index>
  (state rows use result.index; initial rows use POSITION index —
  subtle mismatch if backend indexes differ). **NO escapeHtml ON ANY TABLE
  CELL** (trackName/artistName/spotify_*) — HTML injection via track names;
  React port fixes by construction (JSX) — note as knowing improvement.
- generateInitialTableRows: LB tracks use track_name/artist_name; others
  name/artists (array join). Status '🔍 Pending...'.
- formatDuration (10036): THIRD duplicate definition (this file +
  sync-spotify + shared-helpers).
- generateDiscoveryActionButton (10046): status classes → actions: not-found/
  error → 🔧 Fix (openDiscoveryFixModal(platform, identifier, index));
  wing-it → 🔧 Fix; found → ↻ re-match + red ✕ unmatch
  (unmatchDiscoveryTrack); else '-'. Status literals accepted: 'not_found'/
  'not-found'/'❌ Not Found'/'Not Found', 'error'/'❌ Error', wing_it_fallback/
  'wing-it', 'found'/'✅ Found'.

**Modal update + close (10097–10455): READ IN FULL.**
- updateYouTubeDiscoveryModal (10097–10205): guards all 3 element ids;
  progress bar + "M / T tracks matched (P%)" text; per-result row update BY
  result.index id — **#867 fix: missing rows are CREATED on the fly** (the
  pre-rendered initial rows can be SHORTER than authoritative backend results
  — rate-limited partial track fetches — old code silently dropped the
  excess); textContent everywhere (SAFE — unlike the innerHTML row
  generators); #863 live source-artist fallback repeated; actions cell
  re-rendered w/ the same 9-way platform ladder (4th copy of the ladder);
  status.complete + phase 'discovering' → phase='discovered' + footer
  re-render + description swap; phase already 'discovered' + footer shows
  .modal-info → footer re-render (rehydration repair).
- refreshYouTubeDiscoveryModalTable (10207): full tbody re-render from state +
  progress re-derive (progress||100).
- closeYouTubeDiscoveryModal (10237–10455): HIDES (classList 'hidden'), never
  removes — state + modal preserved, polling continues in background. Then IF
  phase is sync_complete|download_complete: **SEVEN near-identical per-source
  reset blocks** (spotify_public/itunes_link/deezer/tidal/qobuz/beatport/
  else-youtube): preserve {playlist, discovery_results, spotify_matches,
  discovery_progress, convertedSpotifyPlaylistId}, delete download_process_id
  + phase, restore, phase='discovered', update source card, POST per-source
  update_phase endpoint — **Beatport's is `/api/beatport/charts/update-phase/`
  (HYPHEN) vs everyone else's `update_phase` (UNDERSCORE)**; beatport block
  doesn't preserve/restore (just sets phase). Then fake-state phase =
  'discovered' too. Prime unification target (one reset fn + per-source
  config: stateRegistry, cardUpdater, endpoint).

**YouTube sync tail + resets (10461–10904): READ IN FULL.** Tidal-shaped sync
machinery (start → sync_playlist_id → socket sync:subscribe + skip-when-
connected HTTP fallback → finished→sync_complete / error|cancelled→discovered).
- **updateYouTubeModalSyncProgress (10658) is the GENERIC painter: it
  PREFIX-SCANS all 7 sync-status id namespaces** (youtube/listenbrainz/tidal/
  deezer/spotify-public/itunes-link/beatport) and paints whichever exists —
  this is why Qobuz's tidal-* reuse and LB's borrowed YT functions still
  paint correctly. De-facto the shared painter.
- startYouTubeDownloadMissing (10706): serves YT **AND LB** (checks both
  registries); vpid prefix ladder listenbrainz_/deezer_/beatport_/tidal_/
  qobuz_/youtube_<hash>; same two-format track builder; calls
  openDownloadMissingModalForYouTube (EXTERNAL — find owner; the OTHER
  generic download-modal builder).
- resetYouTubePlaylist (10785): POST /api/youtube/reset/<hash>, zero all
  state fields, card fresh, close modal. resetBeatportChart (10837): POST
  charts/update-phase {phase:'fresh', reset:true}, zeros BOTH key styles,
  chartState fresh.

**ListenBrainz region (10906–11479): READ IN FULL — file tail COMPLETE.**
- _mirrorListenBrainzAfterDiscovery (10928–11020): mirrors ONLY matched
  tracks (spotify_data.id required) w/ extra_data JSON {discovered, provider,
  confidence, matched_data}; 'Last.fm Radio:' title prefix → mirrorSource
  'lastfm' (Auto-Sync grouping + cascade-delete targeting); **rotating-series
  collapse**: GET /api/listenbrainz/series-detect?title= → matched →
  synthetic series_id + canonical name (Weekly Jams etc. roll into ONE
  mirror row via UPSERT). Idempotent.
- startListenBrainzDiscoveryPolling (11022): socket + ALWAYS-POLL (Tidal
  variant); complete ALSO when phase==='discovered'; completion → POST
  /api/listenbrainz/update-phase/ (**HYPHEN — so hyphen endpoints = Beatport
  + LB; underscore = youtube/tidal/qobuz/deezer/spotify-public/itunes**),
  footer re-render, description swap, listing sync-btn reveal, MIRROR call,
  toast. Both socket + poll paths duplicate the completion block.
- startListenBrainzDiscovery (11261): sets phase locally FIRST, POST
  discovery/start with the playlist BODY (LB sends the whole playlist —
  others send nothing), error → revert to fresh.
- startListenBrainzPlaylistSync (11313): DUAL UI — detects listing context
  by presence of #discover-lb-playlist-<mbid>-sync-status; listing → own
  polling fn w/ ids -sync-total/-matched/-failed/-percentage, status hidden
  after 3s, button re-enabled; modal → standard polling + footer.
  **LB listing percentage = matched/total (NOT (matched+failed)/total like
  every other painter)** — another formula drift.

**SpotifyPublic vertical (6611–7632): DIFF-READ COMPLETE vs Deezer-link.**
parseSpotifyPublicUrl READ IN FULL: validates open.spotify.com/playlist|album
+ spotify:playlist|album: URIs; _isUrlAlreadyLoaded guard; POST
/api/spotify/parse-public {url}; result has url_hash + type
(playlist|album) + subtitle; AUTO-MIRRORS (source 'spotify_public', owner =
subtitle, description = URL); saveUrlHistory('spotify-public'). Divergences
vs Deezer-link (rest = rename + toast wording):
1. Keyed by url_hash everywhere (not numeric id).
2. **ALBUM SUPPORT**: type badge 💿 Album (#b3b3b3) vs 🎵 Playlist (#1DB954
   Spotify green) on cards; createCard tolerates missing state (phase
   'fresh' default).
3. TWO id prefixes: modal fake hash `spotifypublic_<hash>` but download vpid
   `spotify_public_<hash>` (underscore) — inconsistent pair, both live.
4. startSpotifyPublicDownloadMissing(urlHash, forcePlaylistFolder=false) —
   the standalone-mode folder-download entry; writes convertedSpotifyPlaylistId
   back onto the REAL per-source state; passes {forcePlaylistFolder} into the
   generic download modal.
5. modalSyncProg/modalButtons IDENTICAL to Deezer's (0 diff).

**iTunesLink vertical (7633–8653): DIFF-READ COMPLETE vs SpotifyPublic —
NEAR-PERFECT CLONE (14/18 functions byte-identical modulo rename).** Only:
1. parse validation: itunes.apple.com/music.apple.com (case-insensitive) + 6
   URI schemes (itunes:/applemusic: × album/track/playlist); POST
   /api/itunes-link/parse; artist mapper hardened (object-or-string).
2. Card: adds 'Track' type (♫ icon), Apple pink #fa586a badge for
   playlist/track vs album grey.
3. NO forcePlaylistFolder (standalone folder-download not offered).
4. Placeholder copy.

**Beatport browse 4283–5210: READ.**
- clearBeatportPlaylists (4275–4361): re-checks active charts (toast block),
  removes modals + youtubePlaylistStates twins + download processes, wipes
  beatportChartStates, DELETE /api/beatport/charts/delete/<hash> per chart,
  placeholder restore.
- loadBeatportGenres (4377): fast fetch /api/beatport/genres (no images) →
  dynamic genre cards REPLACING the 12 hardcoded markup items; retry buttons;
  >10 genres → loadGenreImagesProgressively: 2-worker queue, 500ms/worker
  delay, per-genre /api/beatport/genre-image/<slug>/<id>, icon→bg-image fade.
- setupHomepageChartTypeHandlers: clone-node listener reset (dedupe pattern);
  homepage chart click → handleHomepageChartTypeClick: chartTypeMap (8 types →
  endpoint+limit; top-10 = top-100 endpoint limit 10 etc.) → fetch →
  openBeatportChartAsDownloadModal(tracks, name, null) — NOT the discovery
  flow! Homepage charts go STRAIGHT to a download modal (find
  openBeatportChartAsDownloadModal — later in file or beatport-ui.js).
- openBeatportDiscoveryModal (4648): creates the fake YT state, phase
  'discovering' card immediately, POST /api/beatport/discovery/start/<hash>
  {chart_data} (sends the whole chart), error → revert 'fresh', then opens
  shared modal. startBeatportDiscoveryPolling (4714): socket + ALWAYS-POLL at
  **2000ms** (comment says 'like Tidal' — Tidal is 1000ms; wrong comment,
  real cadence drift); no wing-it in EITHER transform (unlike Tidal socket);
  completion on phase discovered|error.
- Rebuild Top10 flow: _beatportModalOpening debounce (2s); getRebuildPageTrackData
  — **DOM-EXTRACTION as data source** (reads .beatport-top10-card title/artist/
  label/rank/data-url from the rendered cards; cached in rebuildPageTrackData)
  → _enrichTracksWithProgress (find owner) → openBeatportChartAsDownloadModal.
- window.loadBeatportTop10Lists monkey-patch (4998): wrapper is a NO-OP
  (calls original, returns) — vestigial, delete knowingly.
- createBeatportCard/addBeatportCardToContainer (5014): standard card;
  AUTO-MIRRORS chart on card add (source 'beatport', t.name||t.title,
  artists[0]||t.artist). handleBeatportCardClick (5088): Tidal-shaped phase
  dispatch; state split across beatportChartStates + youtubePlaylistStates
  with on-demand backend re-hydration when the YT twin is missing
  (/api/beatport/charts/status/<hash> → restore playlist/results/progress).

**Beatport 5211–6610: READ/SKIM-VERIFIED COMPLETE.**
- rehydrateBeatportDownloadModal (5211): fallback-only (main path is backend
  loading); state fetch, artist normalization, openDownloadMissingModalForYouTube,
  running-state setup. NOTE: unlike Tidal's rehydrate, NO download_complete
  one-shot results paint here either (the backend-loading path has it).
- updateBeatportCardPhase: outerHTML re-render, NO self-verify debug;
  updateBeatportCardProgress: text format like Tidal's.
- Beatport sync (5371–5557): Tidal-shaped; QUIRKS: response key `sync_id ||
  sync_playlist_id`; poll cadence **2000ms** (Tidal 1000); sync completion via
  HTTP captures status.converted_spotify_playlist_id;
  **updateBeatportModalSyncProgress % = matched/total** (the LB formula, NOT
  processed/total) — Beatport + LB share the alternate formula.
- startBeatportDownloadMissing (5592): DEV-LEFTOVER logs (JSON.stringify of
  first two results!); same two-format track builder + double album-object
  coercion; vpid beatport_<hash>; **PERSISTS converted id to backend keeping
  current phase** (update-phase w/ converted_spotify_playlist_id — only
  Beatport does this); modal via ...ForYouTube.
- Genre browse tail (5731–6610): genre detail view (title rewrites ×9 ids,
  dataset-stored genre context, clone-node handler resets), THREE near-identical
  inline chart loaders (new-charts per genre /api/beatport/genre/<slug>/<id>/
  new-charts?limit=20|50, DJ /api/beatport/dj-charts-improved?limit=20,
  featured /api/beatport/homepage/featured-charts?limit=20 — note DJ returns
  data.charts, others data.tracks!), all chart-item clicks → POST
  /api/beatport/chart/extract {chart_url, chart_name, limit:100, enrich:false}
  → _enrichTracksWithProgress → openBeatportChartAsDownloadModal;
  handleGenreChartTypeClick = endpoint map like homepage. UNESCAPED
  interpolation of chart names/artists into innerHTML again.

**SCOPE EXPANSION CONFIRMED: beatport-ui.js (3,913 lines) is sync-page
content** — owns the 5 sliders, loadBeatportTop10Lists/Releases (1597),
_enrichTracksWithProgress (1927), openBeatportChartAsDownloadModal (1988),
_beatportModalOpening; plus shared-helpers.js owns
showBeatportDownloadsSection (3430) + hydrateBeatportBubblesFromSnapshot
(3657); core.js owns cleanupBeatportContent (475). beatport-ui.js needs its
own read pass (add to remaining).

**URL-history helpers (8654–8800): READ IN FULL.** localStorage per source
(keys soulsync-url-history-<source>, max 10, dedupe-by-url, unshift+cap),
URL_HISTORY_SOURCES config {key, icon, inputId, containerId, loadFn} for
youtube/deezer/spotify-public/itunes-link; pill bar (PROPERLY escaped — the
one region that escapes!), pill click → already-loaded check → fill input +
loadFn; X removes. _isUrlAlreadyLoaded: per-source id-extraction
(YouTube = DOM data-url scan!, Deezer regex, Spotify open.spotify.com
playlist|album id, iTunes via extractITunesLinkId: itunes:/applemusic: URIs +
?i= track param + /song|album|playlist path ids incl pl.* playlists).

**YouTube vertical head (8806–9301): READ IN FULL — sync-services.js is now
100% COVERED.**
- parseYouTubePlaylist: validates youtube.com/playlist|music.youtube.com;
  **creates a TEMP card first** (tempHash = btoa(url).slice(0,8), 'Parsing…'
  disabled button) → POST /api/youtube/parse → error removes card; success
  saves history, **re-keys the temp state/card to the real url_hash**
  (updateYouTubeCardData finds by url, deletes temp key, re-ids the element),
  auto-mirrors (description = url, album_name always ''), stays 'fresh'.
- updateYouTubeCardPhase (8971): IN-PLACE mutation (no outerHTML re-render —
  unlike Tidal/Beatport) via its OWN 7-case switch, NOT the shared
  getActionButtonText map — **drift: YT 'discovered' button says 'View
  Details' (shared map says 'View Results'); YT 'download_complete' says
  'View Results' (map says 'Complete')**.
- handleYouTubeCardClick: fresh → phase 'discovering' IMMEDIATELY (before
  backend ack) + startYouTubeDiscovery + open modal; downloading/complete →
  builds tracks from discoveryResults (ONLY spotify_data — no flat-field
  fallback here) → openDownloadMissingModalForYouTube; loads results from
  /api/youtube/state/<hash> if missing (promise chain, not await).
- startYouTubeDiscovery: POST discovery/start; state lookup checks LB registry
  FIRST (copy-paste artifact); modal buttons 'discovering'; polling; opens
  modal AGAIN (double-open guard = modal exists → unhide).
- **#815 retry toast** (_discoveryCompleteToast): if state._retryDiscovery
  (stamped by retryFailedMirroredDiscovery) → "Retry complete: N of M newly
  found[, K still not found]" computed vs matchesBefore baseline; else
  generic. Baseline deleted after use.
- startYouTubeDiscoveryPolling: socket + ALWAYS-POLL 1s; stores results under
  BOTH key styles; completion → phase 'discovered' set on state directly
  (comment: cardPhase may skip if no cardElement) + card + footer + toast.
- stopYouTubeDiscoveryPolling: plain clear.

P0 REMAINING: beatport-ui.js (3,913 — sliders/top10/enrich/chart-as-modal),
auto-sync.js (2,525), sync-listenbrainz.js (364) + sync-lastfm.js (131) +
sync-soulsync-discovery.js (286), server-tab region in pages-extra.js,
import-tab region in stats-automations.js, cross-file touchpoints
(init.js page-entry, core.js socket registries + cleanupBeatportContent,
shared-helpers.js download-modal ecosystem: openDownloadMissingModalForYouTube/
ForArtistAlbum, startMissingTracksProcess, closeDownloadMissingModal,
startModalDownloadPolling, mirrorPlaylist, sequential sync engine,
loadMirroredPlaylists, WishlistModalState, initializeLiveLogViewer).
then SpotifyPublic (6611)/iTunesLink (7633)/YouTube (8806) verticals — diff
candidates vs Tidal/each other — URL-history helpers (8654), the SHARED
DISCOVERY MODAL CORE 9302–10460 (PROSE-READ MANDATORY), YouTube sync tail +
reset (10461–10927), ListenBrainz mirror+polling (10928–end).

More externals: playlistOrganizeSourceForRef, syncPlaylistOrganizeCheckboxes,
setMirroredOrganizePreference, _syncProgressCallbacks.

**Tidal vertical head (1–228):**
- loadTidalPlaylists (4–71): /api/tidal/playlists → tidalPlaylists → render →
  tidalPlaylistsLoaded. Then AUTO-MIRROR EVERY playlist: tracks present →
  mirrorPlaylist('tidal', id, name, mapped tracks {track_name, artist_name
  (first of array), album_name (string-only), duration_ms, source_track_id},
  {owner,image_url,description}); else SEQUENTIALLY awaits
  /api/tidal/playlist/<id> per playlist (slow-fetch: backend paginates w/ 1s
  sleep/page), updates card count in-DOM, then mirrors. So the Mirrored tab is
  FED as a side effect of loading the Tidal tab. mirrorPlaylist owner TBD.
  Finally loadTidalPlaylistStatesFromBackend().
- renderTidalPlaylists: seeds tidalPlaylistStates[p.id]={phase:'fresh',
  playlist} on first render; cards via createTidalCard (uses the SHARED
  getActionButtonText/getPhaseText/getPhaseColor phase maps from
  sync-spotify.js — cross-file dependency); addEventListener click (not inline).
- handleTidalCardClick (128–228): defensive state validation (missing state/
  playlist → toast; missing phase → default fresh). Phase dispatch:
  * fresh → openTidalDiscoveryModal IMMEDIATELY (#867 UX: modal must not
    block on the ~10s track fetch; discovery poll fills rows; ensures
    state.playlist.tracks is an array).
  * discovering/discovered/syncing/sync_complete → reopen discovery modal;
    'discovered' with EMPTY results → backend fallback fetch
    /api/tidal/state/<id> merge before opening.
  * downloading/download_complete → show existing download modal
    (activeDownloadProcesses[convertedSpotifyPlaylistId].modalElement) or
    rehydrateTidalDownloadModal; no converted id → fallback to discovery
    modal if results exist else error toast.
### small tab files — ALL THREE READ IN FULL (the newest, cleanest code in
the family — Phase 1c Discover-to-Sync unification; properly escaped,
typeof-guarded, easiest ports)

**sync-listenbrainz.js (364)**: 3 parallel category fetches (created-for /
user-playlists / collaborative via /api/discover/listenbrainz/*), JSPF
unwrap (p.playlist.identifier URL → mbid; track_count from 3 fallbacks),
auth-failure surfaced ('not authenticated' → connect prompt), sub-tabs by
_lbSyncCurrentType, cards read shared listenbrainzPlaylistStates + phase
maps; click → fills listenbrainzTracksCache on demand
(/api/discover/listenbrainz/playlist/<mbid>) → hands off to
**openDownloadModalForListenBrainzPlaylist — REHOMED TO core.js:1765 during
the discover port** (reachability VERIFIED alive; the 'discover.js may be
missing' comment is stale). THE 500ms CARD REFRESH LOOP
(_startLbSyncCardRefreshLoop): idempotent, self-stops when neither LB nor
Last.fm tab active, per-card in-place phase/button/progress updates reading
the shared state (sync phase uses state.lastSyncProgress, matched/total %).
Refresh btn POSTs /api/discover/listenbrainz/refresh (best-effort) then
reloads. DOMContentLoaded bootstraps sub-tabs + refresh.

**sync-lastfm.js (131)**: intentionally thin — Last.fm radios live in the
SAME listenbrainz_playlists table (playlist_type='lastfm_radio'), same
discovery flow; list /api/discover/listenbrainz/lastfm-radio, cards 📻,
click → handleListenBrainzSyncCardClick (byte-identical flow), shares the
LB refresh loop. Generation happens on Discover; this tab lists+syncs only.

**sync-soulsync-discovery.js (286)**: DIFFERENT shape — personalized tracks
already carry provider IDs (no discovery hop). Load = /api/personalized/
playlists + best-effort /api/personalized/kinds (SEPARATE guard — a kinds
failure must not sink the tab); synthesizes never-generated singleton kinds
as clickable rows (_never_generated, 'Tap "Refresh & Mirror" to generate');
variant kinds only shown if already generated. Synthetic mirror id
`ssd_<kind>[_<variant>]` (UPSERT stability). Click = Refresh & Mirror: POST
/api/personalized/playlist/<kind>[/<variant>]/refresh → project tracks into
the mirror contract (extra_data {discovered, provider spotify|itunes|deezer
by id presence, confidence 1.0, matched_data{...}}) → INLINE POST
/api/mirror-playlist (NOT mirrorPlaylist() — needs the returned playlist_id)
→ update in-memory record → openMirroredPlaylistModal(mirroredId) (EXTERNAL,
stats-automations — the Mirrored tab's detail modal). 0-track warning toast.
### auto-sync.js (2,525) — STRUCTURE + PURE CORE READ (recent, well-commented
code "extracted from stats-automations.js (Cin review feedback)"; header
DECLARES its externals: _esc/_escAttr/_autoParseUTC/_autoFormatTrigger/
showToast/showConfirmDialog/loadMirroredPlaylists/updateMirroredCardPhase/
openMirroredPlaylistModal/closeMirroredModal/youtubePlaylistStates — all in
stats-automations.js or earlier)

**State**: mirroredPipelinePollers, AUTO_SYNC_BUCKETS [1,2,4,8,12,16,24,48,
72,168], _autoSyncScheduleState {playlists, automations, playlistSchedules,
weeklySchedules, automationPipelines, runHistory(+Total)}, active tab /
sidebar filter / expanded kinds / history filter+limit(50) / weekly-editor
draft (controlled popover, discard-on-outside-click).

**PURE CORE (35–470, read verbatim — P1 differential-test gold)**:
- getMirroredSourceRef: source_ref || (spotify_public|youtube + http desc →
  desc) || source_playlist_id.
- Trigger codecs: autoSyncTriggerForHours (≥24 & %24==0 → days),
  autoSyncHoursFromTrigger (minutes→ceil-ish hours, days*24, weeks*168),
  bucket/interval/lane labels (168='Weekly'/'Every week', 1='Hourly',
  12='Twice a day', 24='Daily').
- Weekly codec: AUTO_SYNC_WEEKDAYS mon..sun lowercase (engine payload
  convention); autoSyncWeeklyTrigger DEFENSIVE (regex-validated HH:MM else
  09:00, day whitelist, tz else detectBrowserTimezone→UTC);
  autoSyncWeeklyFromTrigger (empty/invalid days → ALL 7 = "every day" per
  next_run_at convention); autoSyncWeeklyLabel (7 days → 'Daily @ T',
  canonical Mon-Sun ordering so text doesn't shuffle).
- Source labels (12 sources incl. file='File Imports') + logo map (same brand
  URLs as dashboard; missing logos → img onerror display:none).
- **autoSyncCanSchedulePlaylist: file/beatport/lastfm EXCLUDED** (documented:
  no external refresh hook; lastfm radios are seed-specific snapshots that
  never change upstream).
- Automation linkage: playlist_pipeline action_type; playlist id from
  action_config (all:true → null); **schedule ownership = owned_by==='auto_sync'
  flag || legacy group 'Playlist Auto-Sync' || name 'Auto-Sync:' prefix**.
- **Personalized rows trick: synthetic schedulable rows with NEGATIVE ids**
  (never collide with real mirrored ids, flow through every parseInt
  unchanged so drag/drop/bulk/weekly work as-is); kind label from
  name_template split at '{variant}'; autoSyncEnrichDiscoveryRows tags real
  ssd_* rows with kind/variant + DROPS rows whose kind is unregistered
  (fails open with no kinds metadata).

**Render/interaction half (471–2525, inventory-mapped)**: modal open/refresh/
render + 4 tabs (schedule lanes w/ drag-drop to buckets, weekly board w/ day
columns + controlled editor popover + weekly drag, pipeline monitor w/
pollers, automation panel, history panel w/ filter/load-more + rich per-entry
detail: stat cards, before/after snapshots, delta chips, logs, DOM-built —
createElement not innerHTML for entries); bulk schedule/unschedule per
source (+custom-hours prompt); organize-by-playlist toggle per playlist
(setAutoSyncOrganizeByPlaylist); scheduled-card health + next-run labels;
saveAutoSyncPlaylistSchedule[Silent] / unschedule / weekly save+unschedule.
PORT NOTE: this is the most React-shaped vanilla code in the family
(controlled editor state, pure helpers, DOM-built lists) — port maps ~1:1.

### pages-extra.js server-tab region (6–1240, ~entire file) — READ (verbatim
1–595, skim-verified tail). Modern well-built code, escapes via _esc.

- **loadServerPlaylists (12–156)**: skeleton loaders (6 animated cards);
  PARALLEL fetch /api/server/playlists + /api/mirrored-playlists +
  /api/sync/history/names; splits SYNCED (name ∈ mirrored∪history names,
  case-insensitive-trimmed) vs 'Other'; title 'Server Playlists (<Type>)';
  per-server SVG icons (plex/jellyfin/navidrome); hue-rotated cards
  (i*37+200 %360), inline onclick openServerPlaylistEditor.
- **openServerPlaylistEditor**: mirrored lookup BY NAME; 1 match → compare,
  0 → server-only view (banner), >1 → disambiguation modal (source emoji,
  timeAgo, Escape+backdrop close w/ window._disambigEsc handler cleanup).
- **_openServerCompareView (247–354)**: /api/server/playlist/<id>/tracks
  ?name[&mirrored_playlist_id]; state {tracks, serverType, orderStatus,
  serverOrder}; columns render in SOURCE order — **order_status flags
  same-tracks-different-order drift; '⚠ out of order' badge → _showServerOrder
  read-only modal of the ACTUAL server order + ALIGN actions** ('Mirror
  source' removes extras / 'Keep extras' appends them; navidrome+plex+
  jellyfin only; POST /api/server/playlist/<id>/align {playlist_name,
  matched_ids in source order, keep_extras}; missing tracks deliberately NOT
  added — 'run a normal sync for those'). #1005 fix: re-applies active
  filter pill after reload.
- _updateCompareStats: matched/missing/extra counts → stat tiles + filter
  pill labels + footer text.
- **_renderCompareColumns (490–583)**: paired rows by data-pair-id; source
  col (position, art, title/artist, duration, status dot; extra → 'No source
  track' slot); server col (index, art, **confidence badge** exact≥100/
  high≥90/fuzzy w/ title-similarity tooltip, swap btn (matched only) →
  serverSearchReplace(i,'replace'), remove btn → _serverRemoveTrack w/
  destructive confirm; missing → clickable 'Find & add' slot →
  serverSearchReplace(i,'add') w/ artist—name hint).
- _setupScrollLinking: linked column scrolling w/ **AbortController listener
  reset** (window._serverScrollAC).
- Tail (744–1240, skim-verified): serverSearchReplace modal (library search
  /api/library/search-tracks?q&limit=20, result list, _serverSelectTrack →
  POST replace-track|add-track), _serverRemoveTrack → POST remove-track,
  _readdSyncWishlist (/api/sync/history/<id>/track/<i>/wishlist POST,
  '✓ Re-added'/'✓ On wishlist'), **openSyncDetailModal
  (/api/sync/history/<id> → rich detail overlay + _syncDetailFilter)** — the
  sync-history DETAIL modal lives HERE (the history LIST modal is
  wishlist-tools'). exportServerPlaylistM3U + filters read earlier.

### stats-automations.js sync-page regions (13–~2400) — READ (mirror core
verbatim, import/pools skim-verified). **The file is misnamed: its first
third is sync-page content.**

- **_initImportFileTab + parser suite (13–468)**: pure CLIENT-SIDE parsing —
  file read, delimiter detection, CSV/TSV parser w/ quoted-field handling,
  M3U/#EXTINF parser (artist-title split + pending flush), header
  auto-mapping by pattern lists, preview render, column-mapping dropdowns,
  reparse/clear/submit (importFileSubmit → mirror as source 'file'). Pure
  functions = P1 differential-test material.
- **mirrorPlaylist (469–495)**: THE mirror contract normalizer — tolerant
  field extraction (track_name||name, artist from array-of-obj|array|string,
  album from obj|string, image from album.images[0], source_track_id||id||
  spotify_track_id, extra_data passthrough) → POST /api/mirror-playlist
  {source, source_playlist_id: String, name, tracks, description, owner,
  image_url}. FIRE-AND-FORGET (why soulsync-discovery bypasses it).
- **loadMirroredPlaylists + renderMirroredCard (500–656)**: list →
  renderMirroredCard per row + hydrateMirroredDiscoveryStates. THE RICHEST
  CARD: pipeline phases (pipeline_running/complete/error from
  p.pipeline_state) LAYERED OVER the 7-phase machine (fake state synthesized
  from pipeline_state when no youtubePlaylistStates[`mirrored_<id>`]);
  phase-colored status spans; custom_name w/ original-name subline;
  discovery ratio 'N/M discovered on <currentMusicSourceName>'; 7 ACTIONS:
  quality-profile select (playlistQualityProfileSelectHtml optional global +
  hydrate), ↺ clear discovery, **Auto-Sync btn → runMirroredPlaylistPipeline**
  (per-card pipeline w/ mirroredPipelinePollers + pollMirroredPipelineStatus
  auto-resume for running pipelines), ✏️ rename (custom_name), 🔗 edit
  source ref, 📤 export (#903: LB push or .jspf download via MBID-resolving
  background job w/ live card status), ✕ delete. Click dispatch: non-fresh
  state|active poller|open modal → discovery/download modal path (mirrors
  Tidal pattern incl. rehydrateMirroredDownloadModal); else
  openMirroredPlaylistModal(id) (the tracks detail).
- **openMirroredPlaylistModal (1066–1198)**: /api/mirrored-playlists/<id> →
  detail modal (the one soulsync-discovery opens post-mirror);
  closeMirroredModal; clearMirroredDiscovery POST .../clear-discovery.
- **Discovery Pool modal (1141–1372 approx)**: /api/discovery-pool[?playlist],
  stats, matched-art mosaic, category → list views, per-playlist filter,
  rematch flow (openPoolRematchModal). **Wing It Pool (1373–2150)**:
  same shape over the wing-it endpoints (categories/list/filter/refresh,
  matched-name helper, header counts, rematch).
- retryFailedMirroredDiscovery (2155+): stamps _retryDiscovery baseline
  (matchesBefore/retryCount) then re-runs discovery (the #815 toast pair).

### beatport-ui.js — structure known (sliders/top10/enrich/chart-modal);
interface-level read during P1 (it stays tightly coupled to the page port's
Beatport tab; full verbatim read scheduled with that P-phase).
### init.js/core.js touchpoints — interface reads during P1 (owners + roles
all mapped in the ecosystem section).
### beatport-ui.js — TODO (structure known: sliders/top10/enrich/chart-modal)
### init.js/core.js touchpoints — TODO

## P1 findings (added as the port phases run)

- **P1a COMPLETE — engine interface contracts banked in
  `SYNC_ENGINE_CONTRACTS.md`** (repo root): full signatures/param shapes/side
  effects/preconditions for the downloads.js engine, the core.js substrate,
  shared-helpers' optional-globals protocol, and the init.js entry chain.
  Headline port constraints found there:
  - Top-level `let`/`const` (activeDownloadProcesses, socket,
    playlistTrackCache, sequentialSyncManager, WishlistModalState) are
    SCRIPT-SCOPED — not on window — so the React page needs explicit bridges
    (or the existing ones: window.reopenActiveDownloadModal,
    window._socketConnected, the ss:* CustomEvents, SoulSyncActivitySocket).
  - `_escAttr` has TWO different implementations (downloads.js inline-JS
    flavour vs stats-automations pure-attribute flavour); stats-automations
    wins the load order, so downloads.js callers get the WRONG escaper today.
  - `currentMusicSourceName` is NEVER reassigned (declared 'Spotify',
    "updated from status endpoint" comment is false) — the discovery modal's
    match-column headers always say Spotify even when metadata source is
    Deezer/iTunes. LIVE BUG #5 (cosmetic); the real signal is
    getActiveMetadataSource().
  - The anonymous ENTER-key keypress closures on the 4 URL inputs STACK one
    per sync-page navigation (named-function handlers dedupe, closures
    don't) — N visits ⇒ N parse calls per Enter. LIVE BUG #6 (mild).
  - `checkAndCleanupGlobalPolling` is a documented no-op; the global 2s batch
    poller runs forever once started, alongside the socket path, by design.

- **formatDuration is a THREE-WAY drift, not a two-way duplicate** (found by
  the P1b differential): sync-spotify.js 1967 has NO falsy guard (undefined →
  'NaN:NaN'), wishlist-tools.js 1575 guards to '--:--', sync-services.js 10036
  guards to '0:00'. All plain global declarations; load order (sync-spotify →
  wishlist-tools → sync-services) means the sync-services copy is the ONE
  every runtime caller gets — including wishlist-tools' own callers, which
  never see their '--:--' fallback. Port matches the winner; pinned in
  routes/sync/-sync.core.test.ts.
- P1b modules landed: routes/sync/-sync.types.ts + -sync.core.ts (+27 tests,
  differential where executable): 4 phase maps, hero-source ladder, vpid flag
  ladder, isFound lenient/Qobuz + wing-it predicates, discovery actions-cell
  classification, download-task status map + final-progress formula, both M3U
  prefix lists (the autoSave/export drift preserved).

- P2 landed: `-sync.sources.ts` — the drift catalog as a typed config table
  (endpoints incl. hyphen/underscore drift + LB's borrowed youtube cancel,
  id prefixes incl. the spotifypublic_/spotify_public_ pair, transports:
  always-poll vs skip-when-connected vs beatport's 2000ms, wing-it per
  transform, percent formulas, #867 flag, found variants) + `-sync.api.ts` —
  config-driven vertical calls + the page-level endpoints, every path pinned
  by a captured-fetch test. Two controller normalizations DECLARED (not yet
  built): wing-it handled on every transport, discovery via
  ss:discovery-progress + per-source HTTP backstop.

- DRIFT CATALOG CORRECTION (P3a re-read): ListenBrainz has NO wing-it
  handling in EITHER transform (the only case-insensitive 'wing' in its
  region is the word "Showing" in a log line) — the audit's earlier guess
  that LB followed the Tidal socket pattern was wrong; config fixed. Also
  found: LB's spotify_artist shows only the FIRST artist (11049) where every
  other mapper joins the list; and the actualMatches counters disagree with
  the socket mappers about wing-it rows (491 counts a wing-it row with
  spotify_data as a match; 635 excludes it).
- P3a landed: `-sync.transform.ts` — ONE mapper replacing the fourteen
  socket/poll result transforms, per-source only for the genuine payload
  dialects (tidal_track/qobuz_track/deezer_track/spotify_public_track/
  itunes_link_track objects, beatport_track title+artist strings, LB
  lb_*||track_name flats + authoritative indexes + duration, YT/mirrored
  passthrough). THREE knowing unifications documented in the module header:
  wing-it on every source+transport, the error arm everywhere, joined
  Spotify artists (LB's first-only dropped).

- VERIFICATION PASS (Boulder-requested) findings, fixed before commit:
  the sync HTTP poll signals completion with a BOOLEAN `status.complete`
  (tidal 1078) while the socket frame says status 'finished' (1030) — the
  P3b reducer only handled the string; applySyncStatus now accepts both.
  The poll's error arm checks `sync_status === 'error'` only (no
  'cancelled'); the reducer's acceptance of cancelled via either field is a
  harmless superset. All config transport facts re-verified against the
  pollers themselves (deezer skip@1000, LB always@1000, beatport 2000 both,
  beatport sync skip).
- INDEPENDENT REVIEW (Boulder-requested verify pass) — verdict: endpoints,
  transports, id spaces, core ladders, urls/import/autosync modules all
  verified clean; five findings, all fixed:
  1. applySyncStatus missed the HTTP poll's boolean `complete` (caught by
     both the self-review and the reviewer; fixed + pinned per transport).
  2. spotify_public + itunes_link card progress is CHECK-NOTE-SPANS (their
     painters clone deezer's, 7283/8309) — config had slash-text, and the
     pinning test agreed with the wrong table. Both fixed.
  3. LB percentFormula: matched/total is its LISTING only (11393); its MODAL
     uses the shared processed painter (10684). Config now carries
     percentFormula 'processed' + listingPercentFormula 'matched'.
  4. slash-text doc string had parentheses the card painter doesn't (966).
  5. Authoritative result indexes are beatport+LB ONLY — the object-track
     mappers are positional unconditionally, and the Fix modal posts the
     index back; transform now splits exactly.
  Plus wire-exactness: buildMirrorPayload's artist `|| ''` now binds to the
  non-array branch only (empty-array artists drop the key, proven by a
  JSON round-trip differential), image_url passes undefined through.
- P3c landed: `-sync.use-vertical.ts` — useSourceVertical(config), ONE hook
  for the nine verticals' polling/lifecycle plumbing: ss:discovery-progress
  frames + HTTP backstop at the config cadence, optimistic discovery start
  with the beatport/LB failure revert, sync poll to both terminal shapes,
  cancel with local revert, the gated close-reset writing phase through the
  config's drift endpoint, seed/hydrate for tab loads. Tested with fake
  timers + dispatched CustomEvents + captured fetch.

- P4a landed: the shared discovery modal (-sync.modal-core.ts,
  -sync.use-standalone.ts, -ui/discovery-modal.tsx). Independent review ran
  BEFORE commit; its big finding changed the architecture: **the
  wishlist-tools Fix Track Match flow CANNOT be adopted** — openDiscoveryFix
  Modal/unmatchDiscoveryTrack read the script-scoped listenbrainzPlaylist
  States/youtubePlaylistStates registries (wishlist-tools.js 22-58) that a
  React page can never populate, so every call would toast "Track data not
  found". The modal therefore surfaces onFixTrack/onUnmatchTrack INTENTS and
  the fix modal gets ported React-native in P4b (it is page-scoped anyway).
  All other review findings fixed pre-commit: vanilla cell classes + the
  modal-footer-actions wrapper restored (the repaint/CSS contract), the
  spotify_public organize toggle noted as a P4b item, the wing-it DROPDOWN
  (Download closes the modal; Sync to Server via _wingItSyncFromModal keeps
  it open — both engine fns take explicit tracks, no registry dependency),
  Retry-Failed counts every non-found row (9684), Download stays available
  via convertedSpotifyPlaylistId with counter-only hasMatches (9603-9605),
  the ⚠️ no-results branch (9628), the styled ♪/✓/✗ syncing status row,
  initial 🔍 Pending rows (10001), the vanilla button copy (Sync This
  Playlist / ❌ Cancel Sync / ℹ️ No X matches found.), inline unmatch/mbid
  styling, soulsync-standalone-action class.
  P4b TODO: React-native fix modal (search + MBID lookup + apply/unmatch
  endpoints), spotify_public organize toggle in the footer.

- PERFECTION PASS (third independent review, whole branch): ten findings,
  all fixed + tested; production build verified for the first time (passes;
  routeTree untouched). The fixes: mirrored is a ''-prefix source (the
  'mirrored_' marker is PART of the id — fakeHash===sourceId, no doubling);
  cancelSync only reverts on a SUCCESSFUL cancel (the vanilla returns
  without reverting on error, 1129-1132); discovery frames are filtered by
  PLATFORM as well as id (rooms are not platform-namespaced; mirrored frames
  arrive as platform 'youtube') and the transport doc now states honestly
  that the poll is the only guaranteed path; the React modal uses its OWN
  container/row-id namespace (sync-discovery-*) so live vanilla writers
  (updateYouTubeModalButtons from wing-it sync) no-op instead of
  innerHTML-clobbering React DOM; the progress line uses the payload's
  authoritative spotify_total; pendingSourceRows matches
  generateInitialTableRows exactly ('Unknown Track'/'Unknown Artist', plain
  join); the "No matches" info line is REMOVED (the vanilla's prepend is
  unreachable dead code — wing-it wrap defeats its startsWith guard);
  discovery polling stops on phase 'error' (backends park failures there
  with complete=false — the vanilla HTTP polls spin forever, ours doesn't);
  startSync/fetchAndHydrateState catch network failures; sync polls run an
  immediate first tick on start/resume (vanilla 1105); the beatport
  converted-id doc corrected (no status endpoint returns it — the ||-keep is
  defensive).

- P4b landed (built → line-by-line reviewed → findings fixed, per the loop):
  the React-NATIVE Fix Track Match flow (-sync.fix.ts + -ui/fix-modal.tsx —
  the search cascade active-source-first with bug-#5 actually working, the
  MBID escape hatch with the vanilla's two distinct not-found messages, the
  confirm-through-SoulSync-dialog → update_match POST, applyFixedMatch /
  applyUnmatched reducers with the vanilla's exact counter/progress/album-
  object semantics) + the spotify_public organize toggle (-ui/organize-
  toggle.tsx — resolve ref is the BARE hash; the review caught the port
  sending the prefixed ref, which the vanilla normalizes away and the
  backend would never match). The vanilla's identifier ladder dissolved:
  React states are keyed by the source's own id, which IS the backend
  identifier everywhere. LIVE BUG #8 catalogued by the reviewer: the
  vanilla fix modal has no qobuz arm in its state ladder (22-43), so qobuz
  rows could never be fixed at all — the React port makes them fixable
  (update_match endpoint exists); qobuz UNMATCH still falls to /api/youtube
  (bug #7, transcribed bug-compatible). Remaining P5 wiring items recorded:
  render FixModal with key={row.index}, run postUnmatch+applyUnmatched with
  the vanilla toasts, and the quality-profile select in the organize footer.

- P5a landed (built → line-by-line reviewed → 8 findings fixed, per the loop):
  the first tab wave — -sync.lb-tabs.ts (JSPF unwrap with the parameterized
  inner.name fallback LB has and lastfm doesn't, auth detect off the
  created-for response only, the ♪/✓/✗ card progress line with the exact
  matched_tracks||spotify_matches fallback chains, SSD synthetic-singleton
  filter + staleness incl. the curly quotes + mirror projection with the
  spotify>itunes>deezer provider ladder and JSON-string extra_data),
  -ui/source-card.tsx (the shared card archetype; null hides the progress
  line, '' renders it visible-and-empty — the vanilla's
  unhidden-while-refreshing state), -ui/source-modals.tsx (the wiring host
  every tab shares: DiscoveryModal + key'd FixModal + unmatch toasts +
  the download hand-off), and the three tabs (LB sub-tabs, Last.fm Radio,
  SoulSync Discovery Refresh & Mirror). The vanilla's 500ms card refresh
  loop dies here — cards re-render off state. Review findings fixed: the
  hand-off must close() the React modal first (engine modal is z-index 9000
  vs the overlay's 10000 — it would open buried), and must patchState
  convertedSpotifyPlaylistId (the vanilla stores it at 10765; the hasConverted
  footer gate + rehydration key off it); the LB {playlist} start body is now
  DERIVED inside SourceModals from config.discovery.startBody so page wiring
  can't drop it (the endpoint 400s without it); the two distinct download
  guards (no-results vs no-matches) + the engine-error toast; lastfm unwrap
  lost the inner.name fallback; SSD shows the empty progress bar while
  refreshing and writes '♪ N / ✓ N / mirrored' after (236-238), busy state is
  a per-card Set. Declared improvements: SSD card re-renders with new
  count+Ready after mirror (vanilla left the DOM stale); React guards the
  whole card against re-entry (vanilla's card body was re-entrant). Declared
  divergence: card click seeds the React vertical + DiscoveryModal instead of
  openDownloadModalForListenBrainzPlaylist (script-scoped registries,
  unreachable). patchState added to useSourceVertical (exposes the stable
  patch; missing-id falls to freshSourceState where reducers no-op safely).

- P5b landed (built → line-by-line reviewed → 6 findings fixed, per the loop):
  the four URL-import tabs — -sync.url-tabs.ts (validation toasts verbatim
  incl. the 'a iTunes' typo, spotify checks case-SENSITIVE vs itunes
  lowercased, the four mirror-track projections field-by-field with youtube's
  unconditional mirror + always-'' album, badges/icons/colors, YouTube's OWN
  7-case button map — fresh 'Start Discovery', discovered+sync_complete
  'View Details', download_complete 'View Results' — and its
  progress-only-while-running visibility, slash-text + check-note progress
  formats, url-history localStorage glue), -ui/url-history-bar.tsx (Recent
  pills, truncate 28+'...', × remove), -ui/url-import-tab.tsx (LinkTabShell
  + DeezerLinkTab + the PublicLinkTab clone pair + YouTubeTab with the temp
  'Parsing…' card flow and the sync-spotify 695 backend restore; ENTER key
  on all inputs; useUrlCardOpen with the discovered-empty-results refetch;
  useYouTubeCardOpen preserving YT's fresh-click-starts-discovery drift).
  fetchDeezerLinkPlaylist now throws the backend error on !ok (the vanilla's
  2746 throw); fetchYouTubePlaylists added. SourceCard grew typeBadge /
  iconClassName / optional owner / ReactNode progressLine (additive).
  Review findings fixed: states-list rows hydrated in discovering/syncing now
  RESUME their pollers (the vanilla's startXPolling tail, 3320-3326 — the
  port would have frozen mid-flight cards), the YouTube mount restore resumes
  the same way (replacing checkForActiveProcesses → rehydrate), url-variant
  re-paste of a restored YT playlist can't append a duplicate url_hash card
  (vanilla leaked an orphan card here), the silent-seed divergence declared
  (the 'state not found' toasts are unreachable by construction), the
  restored-card button-map inconsistency documented, and coverage added for
  post-load dedupe + pill guard + url_hash dedupe + the refetch + resume.
  Declared divergences (P5a pattern): card clicks open the React
  DiscoveryModal in every phase; engine-modal page-load rehydration dropped
  (script-scoped registry); progress line derives from state each render;
  YT already-loaded check is array-backed, not a DOM data-url scan.

- P5c landed (built → line-by-line reviewed → findings fixed, per the loop):
  the Tidal + Qobuz account verticals in -ui/account-tab.tsx — one
  AccountVerticalTab parameterized by the pair's only real drift. Refresh
  loads the account list (fetchSourcePlaylists now THROWS the backend error
  on !ok, the vanilla's 14-17/1526-1529 throw → ❌ placeholder + toast),
  cards render instantly from metadata, then a sequential background loop
  fetches each playlist's tracks (new fetchAccountPlaylist), updates the card
  count and auto-mirrors with {owner, image_url, description} — the mirror
  mapper is byte-identical to Deezer's, so deezerMirrorTracks is reused.
  Saved states hydrate afterwards with the P5b resume-on-in-flight fix.
  The fresh-click drift: TIDAL opens the modal immediately with tracks ?? []
  (#867 — the backend discovery fetch is the source of truth, 152-166);
  QOBUZ fetches the track list behind the loading overlay, projects it
  (1657-1661), and refuses to open with 'Could not load tracks for this
  playlist'. Review findings fixed: hydrateStatesForLoaded takes an optional
  staleness guard so an abandoned Refresh can't hydrate/resume over a newer
  one (belt-and-braces — the Refresh button is disabled for the whole load,
  which the tests now pin), plus coverage for the tracks-came-with-the-list
  mirror branch, the settled discovered-empty refetch, and the guard itself.
  DEFERRED: deezer-arl is NOT an account vertical of this shape — it's a
  Spotify-style tab (details modal + sequential sync engine +
  playlistTrackCache) and rides the Spotify wave.

- P5c CORRECTED + P5d landed (an Opus re-review of P5c found what the first
  pass missed — the re-review is now the standard for every wave):
  * **The cards had no SYNC progress line at all.** The vanilla paints this
    element from TWO writers: updateXCardProgress (the discovery line) and
    updateXCardSyncProgress (tidal 1159-1197, qobuz 2315-2348), whose
    percentage is (matched+failed)/total, not matched/total, and which only
    paints when total_tracks > 0 — otherwise the discovery line stays. The
    port rendered discovery numbers during and after a sync, wave-wide (P5b
    and P5c both). Now ONE renderer, -ui/card-progress.tsx, drives every
    vertical, and it reads the discovery format from the config table's
    ux.cardProgressFormat — which until now had no production consumer at
    all. fromBackendState also never mapped the rows' sync_progress
    (endpoints.py 239) onto lastSyncProgress, so the line could not have lit
    up from hydration even once it existed; it does now.
  * The tidal-vs-qobuz fresh-click drift now reads config.ux.
    openModalImmediately instead of a base-name string — the second dead
    config field, and the second source of truth, both gone.
  * Saved states hydrate BEFORE the background track loop, not after it: the
    loop is sequential over every playlist and runs for minutes on a real
    account, and a states response landing after the user started a
    discovery rolled their card back (hydrate replaces the whole state).
  * A track fetch outliving a Refresh no longer resurrects the cleared list
    into [] (which flashed 'No <source> playlists found.' mid-load), and the
    states loop regained the vanilla's PER-ROW try/catch (867, 946-948) so
    one malformed row can't drop the rest.
  * Three P5c tests were proven vacuous by mutation and rewritten: the Qobuz
    projection was deletable with the suite still green, the card-count
    assertion passed off the fixture, and the in-loop staleness check was
    never reached. The #867 test now proves the payoff (cached tracks seed
    instantly) instead of a tautology.
  * P5d: the import-file tab (-ui/import-file-tab.tsx). All parsing is the P1
    pure core; the component owns the file read, the DOM and the submit.
    onImported is REQUIRED so a mount site cannot silently drop the vanilla's
    post-import tab switch. Declared divergence: the name pre-fill runs once
    per read (the vanilla re-ran it on every reparse, refilling a field the
    user had cleared); its 'only if empty' guard is moot here because the
    input does not exist before the first read and clear() empties it.

- FULL-PR REVIEW (six slices; three completed: transform/state/hooks, modal
  layer, cards/tabs). Every finding below was verified against BOTH sides
  before acting; unverifiable suspicions were dropped. Fixed:
  * **The fix modal rendered where nobody could see it.** The vanilla nests
    the fix overlay INSIDE the discovery modal ('Discovery Fix Modal (nested
    inside)', 9426) and .discovery-fix-modal-overlay is z-index 1000 "Above
    parent modal content" (style.css 33628-33640). The port rendered it as a
    SIBLING of the z-index-10000 .modal-overlay, so it painted under an
    opaque backdrop — the Fix/unmatch feature was unusable. DiscoveryModal
    now takes children and SourceModals nests the FixModal; pinned by a test
    asserting overlay.contains(fix).
  * **Every source handed downloads to the wrong engine entry.** The vanilla
    routes tidal/qobuz/deezer/spotify_public/itunes_link through the generic
    (misnamed) openDownloadMissingModalForTidal — which alone takes options
    and hydrates the organize preference (1494) — and reserves
    openDownloadMissingModalForYouTube (downloads.js 429, no options) for
    youtube/beatport/LB/mirrored via startYouTubeDownloadMissing. The port
    used ForYouTube for all nine. Now a ux.downloadEntry config field, and
    ForTidal is declared in globals.d.ts.
  * **'📁 Download to Playlist Folder' did nothing special**: the modal sent
    {forcePlaylistFolder:true} but the handler took no parameters, so TS
    accepted it and the flag was dropped. It now reaches ForTidal's options.
  * **The hand-off ran the close-RESET.** close() → closeModalReset patches
    the phase back to 'discovered' and POSTs update_phase; the vanilla only
    HIDES on hand-off (10768-10772), reserving the reset for the Close button
    (10253-10455). Split into hideWithoutReset; pinned by a test asserting no
    update_phase POST.
  * Invented wing-it dropdown copy (📥/'Grab the source tracks directly'/
    'Match against the server library') replaced with the engine's own
    ⬇️/'Raw names'/'Best-effort' (downloads.js 23-33) — the same class of bug
    as the organize tooltip.
  * fromBackendState dropped Beatport's playlist payload: its endpoints name
    it chart_data (web_server.py 37340, 38297) and the vanilla renames it at
    5123-5127. Added to the pick list (latent — no beatport tab yet).
  * The card progress line wrongly generalised the check-note writers'
    total>0 gate to the slash-text sources; tidal/qobuz/youtube paint
    unconditionally (961-967), so a 0-track card must read
    '♪ 0 / ✓ 0 / ✗ 0 / 0%'. Two of my own tests had pinned the bug.
  * The three small tabs: h3 titles restored to the vanilla's ('Your
    ListenBrainz Playlists' etc.), the LB sub-tabs moved back INSIDE
    .playlist-header (they are inline-flex with margin-left:16px, style.css
    14864 — as a sibling they dropped to their own line), and the refresh
    buttons regained their modifier class + id.
  Verified clean and NOT changed: all modal titles/labels/descriptions, the
  footer gating ladder, buildDownloadTracks, the wing-it engine calls, the
  whole fix cascade + confirm dialog + update_match body, the unmatch ladder,
  XSS (no dangerouslySetInnerHTML anywhere), the id namespace (no collision
  with any live vanilla selector), both sync-completion signals, every
  discovery stop condition, the socket platform filter, syncPercent, and
  applyFixedMatch/applyUnmatched field-by-field against wishlist-tools.js.

## open questions for the port design (collect, don't decide yet)

- Download modal: port-first-as-shared-component vs adopt? (12 call sites across
  both worlds; modal is dynamically built so React could own it wholesale and
  publish the same window.openDownloadMissingModal seam.)
- The 9 verticals: is a parameterized controller honest, or do verticals hide
  incompatible state machines? (Answer comes from the full read.)
- Beatport browse subsystem interplay with beatport-ui.js (3,913 lines, NOT in
  the sync family — what's the boundary?)
- sync-history-overlay + matching-modal live OUTSIDE the page block — shared?
