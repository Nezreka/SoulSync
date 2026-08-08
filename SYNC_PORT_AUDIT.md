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

- FULL-PR REVIEW COMPLETE — all six slices. The three later ones:
  * **pure core: ZERO parity divergences.** Not just read — the reviewer
    lifted the real vanilla bodies and executed both sides over 432
    discoveryRowAction rows, all 32 vpid flag combinations, 84 download-status
    combos, 27 hero ids, 25 formatDuration inputs and the full import matrix.
    All agreed. A scan of all 238 string literals found NO invented
    user-visible string. The formatDuration load-order claim was verified
    against the real <script> order in index.html (8372/8376/8382).
  * **config/api found a REAL transcription bug**: itunes_link's
    fakeHashPrefix was 'itunes_link_'; the vanilla spells the fake hash
    `ituneslink_` (sync-services.js 7885, 8273, 8276) while the vpid is
    `itunes_link_` (7859, 8626). So iTunes is a SECOND mismatched hash/vpid
    pair — the table's comment had called spotify_public's "the" inconsistent
    pair. Fixed, and both pairs are now anchored to the live sources so
    neither can drift silently. Also fixed: mirrored's stateFlag was '' but
    is_mirrored_playlist is real and read four times by the shared modal
    (9354/9682/9969/10172); the wishlist response types named a field the
    backend never sends (`processed` vs `processed_count`) and omitted
    `success`, which the vanilla branches on; and three doc claims were wrong
    (heroLabel is a display name, NOT heroSourceLabel's output — the vanilla
    ladder has no deezer_ arm and never sees a mirrored vpid, so both resolve
    to 'YouTube' there; only QOBUZ actually blocks on a fetch, not four
    sources; the YouTube download entry serves four sources, not two).
  * **cross-cutting: clean on behaviour.** No import cycles, no
    window.confirm/alert, no dangerouslySetInnerHTML, no console noise, no
    TODOs, no `any`/@ts-ignore; 284 test blocks with zero assertion-free
    tests and no .skip/.only; order-independent across two runs plus a
    shuffled one; nothing outside routes/sync imports it and none of it
    reaches the bundle. It also re-verified the dossier's own claims and
    found none the code contradicts. Four documentation statements had gone
    false and were corrected (card-progress is NOT the one renderer — LB uses
    lbCardProgressLine with a matched/total sync percentage, which is real
    parity; the organize-toggle prop doc still described the prefixed ref the
    P4b review had fixed; source-modals named only one engine entry after the
    routing split; the ForTidal declaration had orphaned the YouTube
    docblock).
  Test debt closed from the survivor lists: the minute-interval clamp (a
  sub-30-minute schedule would have read as unscheduled), the all:'true'
  string arm, the synthetic kind+variant match, the Apple `pl.` playlist
  anchor, the CSV blank-line filter, the empty-#EXTINF guard (and the
  filename-derivation branch it sits next to), the >10000 ms/seconds band,
  the four history-pill icons, and the whole endpoint table.
  KNOWN, NOT FIXED: the export-coverage gate is mention-only and matches only
  `export function|const`, so 47 type exports are invisible to it — verified
  NOT currently gamed (0 mention-only exports); routes/sync is not yet in the
  weak-assertion gate (adding it needs 3 pin-ok annotations); sourceForFakeHash
  /sourceForVpid and 11 api functions have no production caller yet (staged
  ahead of the page shell); _AUTO_SYNC_SOURCE_LOGOS is the one pure auto-sync
  constant with no ported home, due with the board phase.

- Review backlog CLEARED — the four LOW-severity behavioural divergences the
  modal review logged are now fixed, not just recorded:
  * **the #863 artist fallback could never fire.** The vanilla tests the RAW
    field, where an absent artist is '' (9984-9985). The port's transform
    renders absent as 'Unknown' (-sync.transform.ts 107/113/123), which is
    truthy and is not the literal 'Unknown Artist' the check looked for — so
    the port's own normalisation defeated its own fallback and the column
    showed 'Unknown' where the vanilla showed the matched artist. 'Unknown'
    now counts as absent.
  * `sync_complete` no longer renders the no-results warning or Retry Failed:
    both belong to the vanilla's DISCOVERED arm only (9603-9605, 9686); its
    separate sync_complete case (9809-9903) has neither.
  * fix-modal errors regain the vanilla's red .error-message class
    (style.css 33945-33956) instead of rendering every status as .loading.
  * the first download guard tests ABSENCE like the vanilla, not emptiness.
    Verified defensive in both worlds — the footer early-returns on
    !hasResults — so this is expression fidelity, not a user-visible change.

- P5e-ii landed (built → line-by-line reviewed → findings fixed → THEN
  committed; P5e-i had gone in unreviewed, which is how its own overclaim
  slipped through). The review found three real breaks in the component:
  * **discovery-state hydration could never match a row.** The shared
    hydrateStatesForLoaded resolves identity from row.playlist_id, but the
    mirrored endpoint sends playlist_id as a BARE INT and the real key as
    url_hash (web_server.py 38508-38509) — so 'mirrored_3' vs '3' never
    matched, nothing hydrated, and no in-flight discovery resumed after a
    refresh. Worse, my own test fixture used playlist_id:'mirrored_3', a
    shape the server never emits, which is exactly what hid it. The helper
    now takes a row-key extractor; mirrored passes url_hash; the fixture is
    the real server shape.
  * **clear left a 'cancelled' state the vanilla deletes.** 1184-1187 writes
    the cancel signal GUARDED by the entry existing and then DELETES it.
    patchState alone skipped the guard (it materialises a state) and left the
    entry behind, so the next card click read it as non-fresh, skipped the
    seed, and opened the shared modal with no playlist at all. Added
    dropState to the hook — patchState cannot express a delete.
  * the quality-profile select and the render-time pipeline poller resume
    were dropped SILENTLY; both are now declared deferrals (profiles need an
    api the port has nowhere yet; the poller belongs with P5g's pipeline
    button), and the rename failure toast regained its 'Error: ' prefix.
  Mutation-checked after fixing: the two survivors were invisible because the
  clear test ran on a card that had never been clicked, so no state existed —
  a seeded-then-clear test kills them. The vanilla's existence guard is
  genuinely unobservable in React (dropState follows unconditionally) and is
  annotated as such so nobody hunts for a test that cannot exist.

- P5f landed, then FAILED RECONCILIATION — see the correction entry below. The
  two pool modals (-sync.pools.ts + -ui/pool-modal.tsx, pool-fix-modal.tsx,
  discovery-pool-modal.tsx, wingit-pool-modal.tsx). Built to the usual
  standard — `_wingItMatchedName` and `_buildPoolMatchedMosaic` both run
  DIFFERENTIALLY against the real vanilla bodies (the mosaic compared element
  for element in jsdom, including the four-cover threshold, doubled tiles,
  three-image row offset and staggered speeds); 39 mutants, 36 killed, the
  three survivors proven equivalent. The UI mutation pass caught three REAL
  test gaps: the matched list's filter was never exercised, and NEITHER row →
  fix-modal hand-off was driven end to end, so an off-by-one in which row's id
  gets passed was invisible — which matters because "Re-match" means two
  different things (a Wing It row carries a TRACK id to /discovery-pool/fix, a
  Discovery matched row a CACHE id to /discovery-pool/rematch). Also recorded:
  the vanilla computes `matchedArtists` at 1706 and never renders it.
  **The work is sound and the target was wrong** — see below.

- P5g landed (export #903 + the Auto-Sync pipeline button + the 🔗 source-ref
  edit + P5e's deferred render-time poller resume). `applyMirroredPipelineState`
  and `parseMirroredPipelineResponse` both proved liftable, so the pipeline core
  is differential over 36 state×prior-state combinations and 9 response cases.
  38 mutants across the two halves, all killed. Findings worth keeping:
  * a test I wrote was WRONG and the differential said so — a
    `{status:'error', error:'...'}` status can never reach the tick's error
    arm, because parseMirroredPipelineResponse rejects ANY body carrying an
    `error` key first (2372). The poller's catch reports "Pipeline status
    error: ..." and stops WITHOUT reloading. That arm only fires for `skipped`
    or a message-less `error`, so its `|| 'Pipeline stopped for ...'` fallback
    is the line users actually see.
  * an EMPTY response body is not an error (it parses to {} and passes both
    guards); a non-empty non-JSON body is, and a 404 gets its own
    "restart the server" message rather than a parse complaint.
  * the 🔗 editor is a modal, not window.prompt (the repo rule).
  **Sequencing problem found later** — see below.

## RECONCILIATION AGAINST THIS DOSSIER (Boulder-prompted) — and a reversal

The trigger: P5h was built on a decision that contradicted headline outcome #1.
Asked whether the guide had been consulted, it had not been — for P5e, P5f, P5g
or P5h. This dossier was treated as background instead of as the worklist. What
that cost, all verified rather than assumed:

1. **P5h REVERTED (3 commits).** Headline outcome #1 says the download engine
   "stays vanilla behind existing window.* seams". P5h added two dispatches to
   downloads.js instead, on the strength of a fork presented to Boulder as
   open — he had actually argued for the adopt option, which was the recorded
   plan. Reverted to 968cc25dd; downloads.js is byte-clean.
2. **P5f violates a contract this port wrote ITSELF.** This dossier calls the
   pool modals "app-level overlays (adoptable)", and
   src/platform/shell/globals.d.ts (written during the TOOLS phase) is
   explicit: "modals that stay VANILLA and are opened, not reimplemented —
   openDiscoveryPoolModal is in stats-automations.js and is also opened from
   the sync page". routes/tools/-ui/launcher-cards.tsx calls
   window.openDiscoveryPoolModal(). P5f reimplemented both pools as sync-route
   components; at the flip the Tools button would break, and
   loadDiscoveryPoolStats (called from wishlist-tools.js 7090, painting the
   Tools counters) with it.
3. **P5e left three named functions unported and unrecorded**:
   openMirroredPlaylistModal (1066 — FIVE external callers: auto-sync.js ×3,
   shared-helpers.js, sync-soulsync-discovery.js), discoverMirroredPlaylist
   (2043), retryFailedMirroredDiscovery (2155, the #815 toast pair). All three
   are named in this dossier's stats-automations section.
4. **P5g ported three functions that live in auto-sync.js**
   (runMirroredPlaylistPipeline, editMirroredSourceRef,
   editMirroredCustomName) — a file whose schedule board is its own later
   phase. After the flip the board would still call the vanilla pipeline,
   giving two implementations, two pollers, and neither aware of the other.
5. **Selection is not self-contained** and P5h had claimed it ported.
   `selectedPlaylists` is script-scoped in core.js:34; startSequentialSync
   (downloads.js 4060) reads it directly AND derives order from
   querySelectorAll('.playlist-card'); updateSyncActionsUI writes
   #selection-info / #start-sync-btn in the .sync-sidebar, outside every tab.
6. **This log stopped at P5e-ii.** P5f and P5g were never written up until now —
   the record went quiet exactly where the work started drifting.

STANDING RULE ADDED: open this dossier at the START of each phase and
reconcile against it — the named functions, the cross-page contracts section,
and globals.d.ts — before writing any code. Every miss above was already
written down.

### P5e-fix P0 READ — the mirrored pieces P5e left behind (READ, no code yet)

Read line by line: stats-automations.js 1066-1197 (openMirroredPlaylistModal,
closeMirroredModal, clearMirroredDiscovery), 2043-2149
(discoverMirroredPlaylist), 2155-2194 (retryFailedMirroredDiscovery);
sync-services.js 9189-9205 (_discoveryCompleteToast); and ALL FIVE external
call sites. Six findings, every one verified by grep, not inferred:

1. **openMirroredPlaylistModal must be ADOPTED, not ported.** Five callers,
   three of them in auto-sync.js whose board is a later phase: the schedule
   board's Details button (1180), and the close-and-REOPEN tails of
   editMirroredCustomName (2403) and editMirroredSourceRef (2437); plus
   shared-helpers.js 1756 (the mirrorPlaylist flow) and
   sync-soulsync-discovery.js 266 ("Same flow the Mirrored tab uses"). It
   stays a global; the React tab opens it. No fork — the evidence is one-sided.
2. **`/api/mirrored-playlists/<id>/prepare-discovery` is UNPORTED** (grep:
   the only occurrence in the repo is stats-automations.js 2062). It REGISTERS
   the mirrored playlist with the backend so the YouTube discovery pipeline can
   find it, and the vanilla POSTs it before every fresh mirrored discovery.
   P5e's card click seeds React state and opens the modal, whose Start
   Discovery goes straight to the youtube discovery-start endpoint with no
   registration. This is a probable functional break in P5e, not a gap.
3. **The React modal's Retry Failed button can never render.** discovery-modal
   .tsx 386 gates it on `config.id === 'mirrored' && failedCount > 0 &&
   onRetryFailed`, and NOTHING in the port supplies onRetryFailed —
   source-modals.tsx does not pass it. Silently dropped, not deferred.
4. **The discovery-complete toast is unported for ALL NINE verticals** — a P3c
   gap, wider than this phase. The vanilla calls _discoveryCompleteToast from
   both completion paths (9233 socket, 9281 poll); useSourceVertical has no
   showToast at all. It also carries #815: when retryFailedMirroredDiscovery
   stamped `_retryDiscovery {matchesBefore, retryCount}`, the toast reports
   `Retry complete: N of M newly found` (+ `, K still not found`), type success
   when N>0 else info, and DELETES the baseline; otherwise plain
   'Discovery complete!'.
5. **clearMirroredDiscovery's stale-modal removal was omitted** (1188-1189:
   `document.getElementById('youtube-discovery-modal-' + hash)` removed after
   the delete). Probably a correct no-op given the React modal's own
   sync-discovery-* namespace (perfection-pass finding), but P5e neither
   ported nor declared it.
6. **The detail modal's source maps differ from the card's.** 1086-1087 carries
   SEVEN entries (spotify, spotify_public, tidal, youtube, beatport, deezer,
   qobuz) with a 📋 fallback and a parallel label map; the card's map at 571 has
   six and includes `file`. Do not share one table between them.

Also noted for the auto-sync board phase: findings 1's callers 2 and 3 mean the
vanilla rename/source-ref tails REOPEN the detail modal, while P5g's React
ports of those two actions do not — so a React rename would leave an adopted
detail modal stale on screen.

### THE ENUMERATION'S THREE GAPS — ALL CLOSED, AND TWO OF MY FINDINGS WERE WRONG

Every gap the enumeration named is now fixed. Recording what the fixing proved,
including where the enumeration itself was inaccurate — two of its three
findings were partly wrong, and only reading the vanilla again showed it.

1. **The LB mirror — real, and the biggest of the three.** Ported as
   buildLbMirrorTracks + resolveLbMirrorTarget + mirrorLbAfterDiscovery, run
   DIFFERENTIALLY against the real vanilla body over 9 mirror cases and 4 skip
   cases. useListenBrainzVertical exists so it cannot be dropped again: there is
   no way to construct that vertical without the mirror.

2. **Rediscover — real.** resetDiscovery on the vertical hook, supplied by
   SourceModals itself. Two genuine drifts went into the config: youtube and
   mirrored send a BARE reset POST while beatport must send
   {phase:'fresh', reset:true} (10851), and the failure toast says 'playlist'
   at 10833 but 'chart' at 10902.

3. **"The source-ref success toast never fires" — WRONG.** It fires, from an
   inline literal; sourceRefUpdatedToast was simply an unused duplicate of that
   string. What WAS missing sat next to it: the vanilla closes and REOPENS the
   detail modal after a successful source-ref edit (2434-2438). That is now
   ported, with the origin remembered at the two open sites rather than probed
   for at commit time.

4. **"The discovery-complete toast is unported for ALL NINE verticals" —
   WRONG ON SCOPE.** Only youtube, mirrored (which rides youtube's poller) and
   listenbrainz toast at all; the other six complete with a console.log. Porting
   it to nine would have invented user-visible behaviour in six verticals. The
   config table now carries the exact per-source text, null included.

5. **Minor: the card's source-icon map has FIVE keys, not six** (spotify,
   tidal, youtube, beatport, file — 571). The detail modal's has seven. The
   enumeration said six. They still must not share a table.

Two fidelity bugs the per-phase line-by-line review caught in my own work, both
invisible to green tests:
- The completion announcement read state BEFORE the patch that triggered it
  committed, so the #815 retry message reported the pre-completion match count.
  The vanilla writes its counters then toasts (9224 then 9233).
- Edit Source seeded the editor from the LIST row where the vanilla uses the
  DETAIL payload (1084-1085) — a stale list would pre-fill the wrong value.

STANDING SWEEPS (both currently empty for routes/sync; run at the end of every
wave and again before the flip):
- Declared-but-never-supplied callback props. NOTE the sweep must count
  object-literal supply (`onX: ...`) as well as JSX (`onX={...}`) or it reports
  hook options as false positives.
- Config fields and exports with no production consumer.
These catch the failure mode green tests structurally cannot: code that exists
and nothing calls.

### CORRECTION to the P5e-fix read, finding 1 — and an OPEN DECISION

The P5e-fix read concluded "openMirroredPlaylistModal must be ADOPTED, not
ported… No fork — the evidence is one-sided." That read examined the modal's
CALLERS. It never read the modal's own BUTTONS. Having now read them
(stats-automations.js 1139-1153), the evidence is not one-sided:

    Delete Mirror  → deleteMirroredPlaylist      (the port has its own)
    Edit Source    → editMirroredSourceRef       (P5g ported it)
    Auto-Sync      → runMirroredPlaylistPipeline (P5g ported it)
    Discover       → discoverMirroredPlaylist    (UNPORTED — the prepare gap)
    Close          → closeMirroredModal

So adopting the detail modal means every action inside it runs the VANILLA
implementation — including opening the VANILLA discovery modal — while the
React tab carries its own React versions of three of the five. Two live
implementations of the same actions, which is the P5g interop problem again,
one level deeper.

It is already half-live: the React SoulSync-Discovery tab calls
window.openMirroredPlaylistModal today (soulsync-discovery-tab.tsx 124).

Why this is NOT the pools' situation: the pools are depended on by the TOOLS
page, an already-completed port that must keep working. This modal's five
callers are auto-sync.js ×3 (a LATER PHASE OF THIS PORT), shared-helpers.js,
and a tab that is already React. Almost the whole dependent set is inside this
port's own remaining scope, which argues the other way — port it, and keep the
vanilla alive only until the auto-sync board phase retires its callers.

DECISION NOT TAKEN. Recording it rather than choosing, because the last
unilateral fork call cost the P5h wave. Independent of it, and safe to do
first: the Rediscover/Retry-Failed wiring and the prepare-discovery fix live in
the DISCOVERY modal, which is already React.

### SPOTIFY TAB P0 READ — PART 1 (READ, no code). Option A is now concrete.

Read line by line: sync-spotify.js 1598-1721 (load/render/card/view-progress/
card-UI) and 1794-1893 (selection + details-modal open). NOT yet read:
showPlaylistDetailsModal's body (1893-1958) and the whole Deezer-ARL region
(sync-services.js 2437-2705). Do those before any code.

**THE OPTION A CONTRACT — the card markup React must reproduce exactly**
(renderSpotifyPlaylists, 1645-1664). The vanilla engine finds these nodes by
selector and paints them, so every id and class is load-bearing:

    div.playlist-card[data-playlist-id=<id>]      ← click toggles selection
      div.playlist-card-main
        div.playlist-card-content
          div.playlist-card-name                  ← escapeHtml(p.name)
          div.playlist-card-info
            span                                  ← `${p.track_count} tracks`
            (the separator is a literal " • " with a TRAILING SPACE, 1651)
            span.playlist-card-status.<statusClass>  ← p.sync_status
          div.sync-progress-indicator#progress-<id> ← THE ENGINE PAINTS HERE
        div.playlist-card-actions
          button#action-btn-<id>                  ← 'Sync / Download'
          button#progress-btn-<id>.view-progress-btn.hidden ← 'View Progress'

**Status class (1640-1642) — three SEQUENTIAL ifs, not else-if; last write
wins.** Default 'status-never-synced'; startsWith('Synced') →
'status-synced'; === 'Needs Sync' OR startsWith('Last Sync') →
'status-needs-sync'. Note 'Synced' and 'Last Sync' are disjoint prefixes, so
the sequence is observably equivalent to a ladder — but transcribe the order,
not an interpretation of it.

**What the ENGINE owns and React must never re-render over:**
- `#progress-<id>` innerHTML — updateCardToSyncing (downloads.js 4139)
- `.playlist-card-status` text AND class — updateCardToDefault (4202)
- `#action-btn-<id>` / `#progress-btn-<id>` text, disabled, inline
  backgroundColor+color, and the card's `download-complete` class —
  updatePlaylistCardUI (1679-1721), whose three arms are: running
  ('📥 Downloading...', disabled, progress btn shown), complete
  ('✅ Ready for Review' + '📋 View Results' at #28a745 on white, card gains
  download-complete), else reset (inline styles CLEARED, not just changed).

The vanilla already wipes and rehydrates this exact way — renderSpotifyPlaylists
rebuilds innerHTML wholesale and checkForActiveProcesses re-applies (1618-1621)
— which is precisely why React re-rendering the skeleton is safe, and why
Option A is faithful rather than a compromise.

**loadSpotifyPlaylists (1598-1630):** placeholder '🔄 Loading playlists...';
refresh button disabled + '🔄 Loading...' then restored to '🔄 Refresh' in a
FINALLY (so an error still re-enables it); !ok throws error.error ||
'Failed to fetch playlists'; error paints '❌ Error: <msg>' into the container
AND toasts 'Error loading playlists: <msg>'; empty list renders
'No Spotify playlists found.'; cache invalidated through the optional-global
protocol (invalidatePlaylistTrackCache else playlistTrackCache = {}); then
checkForActiveProcesses() — the rehydration entry.

**Selection (1794-1830) — the shell-wave problem, now precise:**
- togglePlaylistSelection returns early when `event.target.tagName === 'BUTTON'`
  so the two action buttons never toggle the card.
- It writes the SCRIPT-SCOPED `selectedPlaylists` (core.js 34) — unreachable
  from React.
- updateSyncActionsUI defers entirely to sequentialSyncManager.updateUI() while
  a sync is running, and otherwise writes `#selection-info`
  ('Select playlists to sync' / `N playlist[s] selected`, pluralised at count>1)
  and toggles `#start-sync-btn.disabled` — both in `.sync-sidebar`, OUTSIDE
  every tab.
So selection spans tab + shell + engine, exactly as the reverted P5h found.

**handleViewProgressClick (1668-1677):** reads activeDownloadProcesses and
re-shows the existing modalElement (display:flex). Pure engine adoption — there
is nothing to port.

**openPlaylistDetailsModal (1832-1876):** the optional-globals cache protocol
again — playlistTrackCacheIsStale / invalidatePlaylistTrackCache /
fetchAndCacheSpotifyPlaylistTracks each typeof-guarded with an inline fallback
(GET /api/spotify/playlist/<id>, writing playlistTrackCache[id] = data.tracks).
Cache HIT spreads the cached tracks onto the list row rather than refetching.

### KNOWN FLAKE, NOT MINE, NOT FIXED

`src/routes/search/-route.test.tsx` > "closes on an outside click and reopens on
the next search" failed once in a full-suite run and passed both in isolation
(18/18) and on the very next full run. My change in that round was one added
assertion inside a SYNC test file, which cannot reach the search dropdown except
through ordering or timing. Recorded rather than dismissed: if it resurfaces,
the suspects are an outside-click listener that outlives its test, or a timing
assumption in the dropdown state machine. Not in scope for the sync port.

### SERVER MANAGER TAB — P0 READ COMPLETE (pages-extra.js, all 1,240 lines)

The original P0 read this file as "verbatim 1-595, skim-verified tail". The tail
is now read verbatim too, so the whole tab is covered. 28 top-level functions,
no other page touches this file.

**Shape.** Two views inside one tab, swapped by display:
`#server-playlist-container` (the card list) and `#server-editor` (the compare
editor). serverEditorBack (698) just flips them back.

**The card list (loadServerPlaylists, 12-156).** Six skeleton cards while
loading; three PARALLEL fetches (/api/server/playlists, /api/mirrored-playlists,
/api/sync/history/names); splits SYNCED (name ∈ mirrored ∪ history, matched
case-insensitively and trimmed) from 'Other'; per-server SVG icons
(plex/jellyfin/navidrome); cards hue-rotated by index (`i*37+200 % 360`).

**Opening one (openServerPlaylistEditor, 158).** Mirrored lookup BY NAME: one
match → compare view; none → server-only view with a banner; several →
disambiguation modal (_showServerDisambig, 185) whose Escape handler is parked
on `window._disambigEsc` and removed on close.

**The compare editor (_openServerCompareView, 247-354).** GET
/api/server/playlist/<id>/tracks?name[&mirrored_playlist_id]. State lives in the
script-scoped `_serverEditorState` {tracks, playlistId, playlistName,
serverType, orderStatus, serverOrder, mirroredPlaylist, searchArtist,
_searchResults}. Columns render in SOURCE order; `order_status` flags
same-tracks-different-order and lights a '⚠ out of order' badge opening
_showServerOrder (387) — a read-only view of the ACTUAL server order with two
align actions (_alignPlaylist, 451: 'Mirror source' drops extras, 'Keep extras'
appends them; POST .../align with matched_ids in source order; missing tracks
are deliberately NOT added).

**Filters + re-render.** `.discog-filter` pills (all/matched/missing/extra)
call _serverEditorFilter (705) → _applyServerEditorFilter (715), which shows or
hides `.server-track-item` rows by `dataset.status`. #1005: a re-render must
RE-APPLY the active filter — _rerenderCompare (732) does stats + columns +
filter and RESTORES both columns' scrollTop, because an in-place patch must not
throw the user to the top of a 2,000-track list.

**Search / Replace (746-980) — the richest part.**
- serverSearchReplace(trackIndex, mode) builds `#server-search-overlay`, seeds
  the input with the SOURCE track name only (a title alone searches better than
  an "artist title" blob), stashes the artist on `_serverEditorState.searchArtist`
  as a relevance hint, and closes on backdrop click OR Escape — the Escape
  listener is torn down by a MutationObserver watching for the overlay's removal.
- _serverSearchExecute (818): GET /api/library/search-tracks?q&limit=20
  [&artist=hint]; results keep `_searchResults` so a pick can patch in place;
  the format badge maps M4A→AAC and only shows for a known extension list.
- _serverSelectTrack (886): 'replace' POSTs .../replace-track; 'add' POSTs
  .../add-track with a computed `position` (count of server tracks BEFORE this
  index) plus source_track_id/title/artist/source, which the backend stores as a
  durable manual match (#787). `new_playlist_id` is honoured because PLEX
  DELETES AND RECREATES the playlist. Then it PATCHES the pair in place rather
  than reloading (#1005), and — the subtle bit — if the picked track already sat
  in the list as an 'extra' row, that row is SPLICED OUT, because the backend
  links rather than duplicates.
- _serverRemoveTrack (982): confirm dialog, POST .../remove-track; a matched
  pair loses its server side and becomes 'missing', an extra row is spliced.

**M3U export (642).** Server tracks only, POST /api/generate-playlist-m3u with
save_to_disk+force, then a browser blob download; the filename strips
`/\?%*:|"<>`; the toast reports `found/total in library` when the server could
not resolve every path.

**Sync detail modal (openSyncDetailModal, 1058-1213).** Its own
`.discog-modal-overlay#sync-detail-overlay`, opened from a synced card. Rows
carry status ✅/❌, a confidence badge banded 80/50, and a download column whose
icons are ✅/❌/🔇/🚫. The wishlist arm is the interesting one: a
`download_status === 'wishlist'` row renders a CLICKABLE '→ Wishlist' button
(_readdSyncWishlist, 1029 → POST /api/sync/history/<id>/track/<i>/wishlist)
UNLESS its source_track_id starts with `wing_it_`, in which case it renders the
plain non-clickable 'Unmatched' label — those stubs never had real metadata and
were never wishlisted. Old entries with no track_results fall back to
tracks_json behind a 'Per-track match data not available' notice.

**Port notes.** No engine coupling at all — this region never touches
downloads.js, spotifyPlaylists or the discovery machinery, so nothing here needs
a bridge. `_serverEditorState` is script-scoped but ONLY this file reads it, so
it becomes React state cleanly. Escaping is already correct throughout (`_esc`),
and the two hand-rolled listener teardowns (the disambig Escape handler and the
search overlay's MutationObserver) simply become effects.

**Build slices, in order:** (A) card list + disambiguation; (B) compare editor —
columns, stats, filter pills, scroll linking; (C) search/replace + remove +
the in-place patch rules; (D) order view + align; (E) sync detail modal + M3U
export.

### THE SEEDING BLOCKER — RESOLVED (Boulder chose the bridge)

Found while building the two account tabs, by reading rather than assuming.

**The fact.** `openDownloadMissingModal` looks the playlist up in
`spotifyPlaylists` and HARD-FAILS when it is absent:

    const playlist = spotifyPlaylists.find(p => p.id === playlistId);   // 2235
    if (!playlist) { showToast('Could not find playlist data.', 'error'); return; }

`spotifyPlaylists` is `let spotifyPlaylists = []` at **core.js:33** — a
top-level binding in a classic script, so no module can reach it. Today it is
filled by the vanilla `loadSpotifyPlaylists` (1612), by the ARL shim at 2471 /
2646, and by the rehydration shim at 642-657.

**Why it blocks BOTH tabs, not just ARL.** Once React owns
`#spotify-playlist-container`, the vanilla loader no longer runs, so the array
stays empty and EVERY Spotify download fails with that toast. The ARL shim is
the same problem one level down. This is the identical class of trap as
`selectedPlaylists` (core.js:34) — and it is why the P0 read's
"Option A is faithful" conclusion is right about PAINTING and silent about
SEEDING. The two are different problems and I had merged them.

**There is an exact precedent.** `window.startDiscoverVirtualSync`
(core.js:75-81) was added during the DISCOVER port to solve this same thing,
and its docblock generalises the reasoning:

    `spotifyPlaylists` are top-level `let`s in this script's lexical scope, so
    a module cannot seed them itself — the same reason the function below
    exists.

It seeds `playlistTrackCache` AND pushes the row — but then calls
`startPlaylistSync`, so it cannot be reused for a download.

**The options.**
(a) Add a narrow seeding bridge to core.js beside the existing one. It is the
    established pattern, it is additive, and it touches no behaviour — but it
    edits an engine file this port has deliberately left alone.
(b) Route both tabs' downloads through `openDownloadMissingModalForTidal`
    instead, which takes tracks EXPLICITLY and reads no registry (1312). Costs
    real behaviour: the account modal's staleness protocol, its Export-as-M3U
    button, and its different toast copy all belong to the 2193 entry.
(c) Keep the vanilla loaders alive alongside React so they keep filling the
    array. They also write the container React owns — direct conflict.

**RESOLVED: (a).** `window.registerSyncAccountPlaylist` now sits in core.js
directly beneath startDiscoverVirtualSync, in the same shape minus the sync
kickoff. Six lines, idempotent by id, no behaviour changed for any vanilla
caller. Spotify seeds the whole loaded list (the vanilla ASSIGNS the array at
1612); ARL seeds one shim row at hand-off with the FETCHED track count, which
is what 2646-2654 builds.

This is the SECOND core.js bridge of this kind. See the bridge tally below —
the count is the real signal about the engine's future, and it belongs in the
post-music-side plan rather than in any page phase.

### SPOTIFY + DEEZER-ARL P0 READ — PART 2 (READ COMPLETE, no code)

Read line by line: sync-spotify.js 1878-1971 (the details modal + close +
the formatDuration twin) and sync-services.js 2437-2699 (the WHOLE Deezer-ARL
region). With part 1 above, both tabs are now fully read.

**showPlaylistDetailsModal (1878-1958)** — a SINGLETON `#playlist-details-modal`
.modal-overlay appended to body; closing only sets display:none, it is never
removed, so the next open overwrites innerHTML. Structure:

    div.modal-container.playlist-modal
      div.playlist-modal-header
        div.playlist-header-content
          h2                                   ← escaped name
          div.playlist-quick-info
            span.playlist-track-count          ← `${track_count} tracks`
            span.playlist-owner                ← `by ${owner}`
          div.playlist-modal-sync-status#modal-sync-status-<id> [display:none]
            ♪ #modal-total-<id> / ✓ #modal-matched-<id>
            / ✗ #modal-failed-<id> (#modal-percentage-<id>%)
        span.playlist-modal-close              ← &times;
      div.playlist-modal-body
        div.playlist-description               ← ONLY when description is truthy
        .playlist-tracks-container > .playlist-tracks-list
          .playlist-track-item × N: span.playlist-track-number (index+1),
            .playlist-track-info > .playlist-track-name + .playlist-track-artists
            (formatArtists), .playlist-track-duration (formatDuration)
      div.playlist-modal-footer
        .playlist-modal-footer-left  ← playlistOrganizeToggleHtml(id,'spotify')
        .playlist-modal-footer-right ← Close + playlistModalDownloadSyncFooterHtml

The four-stat sync row is hidden markup the SYNC engine unhides and fills — the
same adopt-the-region rule as the card.

Every footer hook is a typeof-guarded OPTIONAL global with an inline fallback:
playlistOrganizeToggleHtml, playlistModalDownloadSyncFooterHtml (fallback =
one tertiary '📥 Download Missing Tracks' button calling openDownloadMissingModal),
and loadPlaylistOrganizePreferenceIntoModal fired after display:flex.
hasCompletedProcess = activeDownloadProcesses[id].status === 'complete';
isSyncing = !!activeSyncPollers[id].

**DEEZER-ARL — the complete drift list vs Spotify.** It clones the Spotify
archetype, so only the differences matter:

1. **Its cards are NOT selectable.** No `onclick=togglePlaylistSelection` at
   2503, where Spotify has one at 1646. Confirmed rather than assumed.
2. Extra class `.deezer-arl-playlist-card` alongside `.playlist-card`.
3. Every id is prefixed `deezer_arl_<id>`, but the two button onclicks are
   handed the RAW id (2514-2515) and the handlers re-prefix (2527, 2540, 2668).
4. **Only TWO status states** — 'status-never-synced' and, for
   startsWith('Synced'), 'status-synced'. No 'Needs Sync' arm. It also GUARDS
   `p.sync_status &&` and falls back to the literal 'Never Synced' (2500, 2509);
   Spotify has NO guard, so an absent sync_status would throw at 1641.
5. **It rehydrates syncs itself** (2462-2479): after checkForActiveProcesses it
   loops EVERY playlist sequentially, GETs /api/sync/status/deezer_arl_<id>,
   and on status 'syncing' pushes a shim row into spotifyPlaylists then calls
   updateCardToSyncing + startSyncPolling. N awaited requests per tab load.
6. **THE SHIM, twice, with different track counts.** The load-time shim (2471)
   takes `p.track_count || 0`; the modal-time shim (2646-2654) takes
   `playlist.tracks.length`. Both exist so openDownloadMissingModal — which
   only knows spotifyPlaylists — can serve an ARL playlist.
7. Its details modal is a SECOND singleton (`#deezer-arl-playlist-details-modal`),
   byte-identical to Spotify's except: footer source 'deezer',
   `closeBeforeDownload: true`, and the fallback button closes the modal BEFORE
   opening the download modal (2639).
8. openDeezerArlPlaylistDetailsModal coerces with String() on both sides of the
   find (2537), builds a playlistMeta whose track_count is
   `track_count ?? tracks?.length` for the staleness check, keys the cache by
   the PREFIXED id, and calls fetchAndCacheDeezerArlPlaylistTracks(arlId,
   playlistId) — the only optional-global taking BOTH ids.
9. updateDeezerArlPlaylistCardUI (2667-2699) is a literal clone of
   updatePlaylistCardUI, prefix aside — same three arms, same '#28a745', same
   inline-style clearing.

**PORT SHAPE for this wave (Option A, confirmed against the read):** React
renders both card lists and both details modals with these exact ids and
classes. It does NOT own the sync-progress indicator, the status span, the two
action buttons, or the four-stat modal row — those stay the engine's, reached
by selector. The download hand-off stays openDownloadMissingModal. Selection is
Spotify-only and belongs to the SHELL wave, not this one.

## THE FULL FUNCTION ENUMERATION (Boulder-prompted: "im sure there is much more
## still that hasn't been ported")

The reconciliation above checked the port against this dossier's DECISIONS. It
never checked it against this dossier's FUNCTIONS. This pass does that, and it
enumerates the vanilla files rather than the prose above — the prose is a
summary, the files are ground truth.

Surface: 371 top-level functions across the six core sync files (sync-spotify
37, sync-services 197, sync-listenbrainz 10, sync-lastfm 2,
sync-soulsync-discovery 4, auto-sync 121), plus the 53-function
stats-automations sync region and the pages-extra server region.

Method: split the inventory mechanically into the per-source ARCHETYPE (a name
shape shared by ≥4 verticals — `start<S>SyncPolling`, `update<S>CardPhase` and
16 more, 120 functions) which the config table + useSourceVertical cover by
construction, and the 114 NON-archetype one-offs, which were classified
individually. Then two sweeps for the failure mode the phase reviews cannot
see — code that exists but nothing calls: every optional callback prop declared
in the -ui components, and every export in routes/sync, checked for a
production caller.

### THREE NEW GAPS, all inside phases marked COMPLETE

1. **`_mirrorListenBrainzAfterDiscovery` (sync-services.js 10928-11020) is
   entirely unported — P5a.** Both LB discovery-completion paths call it (11075
   socket, 11170 poll). It mirrors the MATCHED tracks (spotify_data.id required)
   into the Mirrored tab with extra_data {discovered, provider, confidence,
   matched_data}, routes a 'Last.fm Radio:' title prefix to source 'lastfm', and
   collapses rotating series via GET /api/listenbrainz/series-detect so per-week
   duplicates (Weekly Jams etc.) UPSERT onto one row. Without it, ListenBrainz
   and Last.fm Radio discoveries never reach the Mirrored tab at all — and
   listenbrainz mirrors are schedulable in Auto-Sync (autoSyncCanSchedulePlaylist
   excludes only file/beatport/lastfm, 205-216), so the schedule board loses them
   too. Half-built and never wired: `detectLbSeries` sits in -sync.api.ts:226
   with no caller, which is what made the omission invisible.

2. **🔄 Rediscover can never render — P5b (youtube) and P5e (mirrored).**
   discovery-modal.tsx:391 gates the button on `config.api.reset &&
   onRediscover`; NOTHING supplies onRediscover. The vanilla renders it in
   THREE phase arms (9690-9695 discovered, 9855-9859 sync_complete, 9916-9920
   the dead download_complete twin) for beatport (resetBeatportChart, 10837) and
   for everything not in the exclusion list at 9693 — which is youtube AND
   mirrored (resetYouTubePlaylist, 10785). The config table already has the
   right endpoints for all three; only the wiring is absent. Identical class to
   the known Retry Failed gap, and the sweep proves the pair is exhaustive:
   of 4 optional callback props declared in the -ui layer, exactly these 2 have
   no non-test supplier.

3. **The source-ref success toast never fires — P5g.** The vanilla's
   editMirroredSourceRef toasts `Updated source for <name>` (auto-sync.js 2432).
   `sourceRefUpdatedToast` was built (-sync.pipeline.ts:202) and never called;
   source-ref-modal.tsx fires only the error toast. Small, but the same
   built-then-not-wired shape as 1 and 2.

### VERIFIED NOT GAPS (recorded so nobody re-checks them)

- `startListenBrainzListingSyncPolling` (11373) and the whole LB listing dual-UI
  drive `discover-lb-playlist-<mbid>-sync-*` ids, which belong to the DISCOVER
  page — already React, contract declared at globals.d.ts:126. Not sync-page
  content, despite living in sync-services.js.
- `removeYouTubePlaylistFromBackend` (sync-spotify.js 1541) is **dead in the
  vanilla**: no caller in any static/*.js, in index.html, or in React. The port
  carries the endpoint (deleteYouTubePlaylist) and needs no UI for it.
- Covered as claimed: removeYouTubeCard, updateYouTubeCardData, timeAgo,
  deleteMirroredPlaylist, the URL-history helpers, the three parse* entries, the
  four phase maps, both formatDuration copies, the shared discovery-modal core
  (9302-10460), and all 120 archetype clones.
- The six config fields with no production consumer (heroLabel,
  listingPercentFormula, pollPolicy, stateFlag, wingItInSocket, wingItInPoll)
  are deliberate RECORDS of vanilla drift, each carrying a doc comment naming
  the vanilla lines; the port's unifications are declared. Not dead config —
  do not "clean up".

### SHELL-WAVE WIRING OBLIGATIONS (unwired because their phase has not run)

These exports have no caller only because the page shell does not exist yet.
Listing them so the shell phase cannot silently drop them the way onRediscover
was dropped: the 13 tab components (TidalTab, QobuzTab, DeezerLinkTab,
SpotifyPublicTab, ITunesLinkTab, YouTubeTab, ListenBrainzSyncTab, LastfmSyncTab,
SoulsyncDiscoveryTab, ImportFileTab, MirroredTab, SourceModals), `useStandalone`
(the modal's required `standalone` prop — the _isSoulsyncStandalone gate),
`useUrlCardOpen`/`useYouTubeCardOpen`/`useLbCardOpen` (each tab takes `onOpen`
as a prop; these hooks build it), and the auto-sync pure core (32 exports, due
with the board phase).

Remaining unenumerated: the Beatport browse subsystem (~35 fns) and
beatport-ui.js still ride their own phase's P0 read; auto-sync's render half and
the pages-extra server region likewise.

## open questions for the port design (collect, don't decide yet)

- Download modal: port-first-as-shared-component vs adopt? (12 call sites across
  both worlds; modal is dynamically built so React could own it wholesale and
  publish the same window.openDownloadMissingModal seam.)
- The 9 verticals: is a parameterized controller honest, or do verticals hide
  incompatible state machines? (Answer comes from the full read.)
- Beatport browse subsystem interplay with beatport-ui.js (3,913 lines, NOT in
  the sync family — what's the boundary?)
- sync-history-overlay + matching-modal live OUTSIDE the page block — shared?

### SERVER TAB SLICE C — search / replace / remove (pages-extra.js 746-1020)

Built: the library-search overlay (`-ui/server-search-overlay.tsx`), the three
write calls and their patch rules (`-sync.server.ts`), wired into the compare
editor. 40 mutants, 40 killed. Full suite 277 files / 6027 tests.

**CORRECTION to slice B — three props deleted.** Slice B declared `onSwap`,
`onFindAndAdd` and `onRemove` as optional props on the guess that a parent would
own them. The read says otherwise: all three mutate `_serverEditorState.tracks`
and re-render this view and nothing else. The editor owns them now and the props
are gone. Keeping them would have been exactly the declared-but-never-supplied
defect the standing sweep exists to catch — the guess was the error, not the
deletion.

**A slice-B verification miss, found and fixed.** The pair-click test looked
green while its behaviour never ran: jsdom has no `scrollIntoView`, so the
handler threw, and React rethrows out of an event handler ASYNCHRONOUSLY — the
assertions had already passed. Vitest reported it only as an unhandled error
beneath the green counts. `scrollIntoView` is now defined per test and deleted
afterwards, and the scroll-the-other-column call is asserted. **Standing lesson:
an "Errors: N" line under a passing suite is a failure, not noise.**

**The pick is resolved AFTER the write, not at click time (946).** My first cut
passed the clicked row straight through, which can never miss — and that would
have silently deleted the vanilla's full-reload fallback at 968-971. The vanilla
looks the id up in `_searchResults` once the write returns, so a second search
landing while the write is in flight really does miss. The seam therefore passes
a RESOLVER, not a value, and the race is tested directly.

**Declared divergences.**
- The vanilla builds the overlay with a sixth results body (magnifier +
  'Searching...', 789-792) that no user ever sees — `_serverSearchExecute`
  overwrites it synchronously in the same task, before paint. Modelling it as a
  React state would add a one-frame flicker the vanilla does not have, so the
  first phase is computed to be whatever the immediate search sets.
- `popover.dataset.trackIndex/mode` (807-808) are plumbing for rebuilding each
  row's onclick string. Both are closure state here, so the attributes carry no
  information and are not emitted.
- The patch helpers return new arrays where the vanilla mutates in place. Same
  resulting list; React re-renders off identity.
- `_rerenderCompare`'s scroll save/restore and filter re-application (732-742)
  need no code: it rebuilds columns with innerHTML, React reconciles them.

**Transcription details worth keeping.** The seed query is the TITLE ALONE and
the artist rides separately as a ranking hint (752, 756-758) — the backend ranks
with it and does not filter. The add-track `position` counts SERVER tracks before
the row, not rows. The confirm names the SERVER track's title. `new_playlist_id`
is honoured on both writes because Plex deletes and recreates. The link case at
960-966 splices the extra row the backend linked rather than duplicated.

**Remaining server slices:** (D) order view + align; (E) sync detail modal + M3U
export.

### SERVER TAB SLICE D — order view + align (pages-extra.js 385-482)

Built: `-ui/server-order-modal.tsx` plus the align core in `-sync.server.ts`,
wired into the compare editor. 21 mutants, 21 killed. Full suite 278 files /
6053 tests.

**`onShowOrder` deleted, for slice C's reason.** _showServerOrder reads
serverOrder/serverType and _alignPlaylist reads playlistId/playlistName/tracks
and then calls _serverEditorRefresh — all editor state. The editor owns both.

**Verified rather than assumed:** the frontend gates align on
navidrome/plex/jellyfin (412) and the backend gates on the SAME three
(web_server.py 22014). Its docstring still says "Navidrome only for now" and is
simply stale — there is no button offered for a server that would reject it.

**Why this one really does reload.** Every other write in this tab patches in
place (#1005). Align does not: a reorder invalidates order_status, the server
column's numbering and the server_order list itself, so there is nothing to
patch — the vanilla calls _serverEditorRefresh (475) and so does this.

**Transcription details.** The align payload carries the MATCHED ids only, in
SOURCE order — missing rows have no server track to name and extras are governed
by keep_extras instead. The id guard is `!= null`, so an id of 0 or '' survives
where `id &&` would drop it. 'Nothing to align' is a WARNING, not an error —
nothing failed, there is simply nothing an order-only rewrite could act on. A
failed align leaves the modal OPEN so the user can retry. The artwork falls back
to a ♫ placeholder both when absent and when it fails to load.

**Two mutation-pass notes, both process rather than product.**
- One survivor was a MALFORMED MUTANT, not a gap: it inserted a dead
  `if (false) void loadCompare()` while leaving the real call intact, so it
  mutated nothing. Re-anchored onto the real call; killed. A mutant that cannot
  change behaviour is not evidence of coverage.
- `playlist_name: playlistName || ''` looked like an equivalent mutant because
  the type says the name is a string. It is not: the name comes from untyped
  wire data, and JSON.stringify OMITS an undefined value rather than sending it,
  so the guard is the difference between the backend seeing `''` (400, as
  designed) and seeing no key at all. Tested through a cast.

**Remaining server slice:** (E) sync detail modal + M3U export.

### SERVER TAB SLICE E — M3U export, and a SCOPE CORRECTION

**The audit had slice E wrong, and the read caught it.** It listed slice E as
"sync detail modal + M3U export". The sync detail modal is NOT sync-page content
and must not be ported here.

**openSyncDetailModal is the DASHBOARD's seam.** Searched the whole repo: its
only caller is `-ui/syncs-card.tsx:109`, `window.openSyncDetailModal?.(view.id)`,
and it is a declared P7 dashboard seam at globals.d.ts:610-630 with a docblock
saying so. Nothing on the sync page reaches it — a synced server card calls
`openServerPlaylistEditor` (99), not this. The P0's phrase "opened from a synced
card" meant the DASHBOARD's Recent Syncs card, and I had read it as the server
tab's.

Porting it into the sync route would have been inventing a caller: no sync-page
code would call it, and the dashboard would still need the window seam.

**FLIP-TIME CARVE-OUT — do not delete this region.** When the sync flip deletes
pages-extra.js's server region, `openSyncDetailModal` (1058-1213),
`_syncDetailFilter` (1214-1232) and `_readdSyncWishlist` (1029-1056) MUST SURVIVE
along with the CSS they use. Deleting them breaks the dashboard's Recent Syncs
card SILENTLY — the call is optional-chained, so a click would simply do nothing.
Whether React eventually owns that modal and publishes the seam is a DASHBOARD
follow-up, not a sync-page phase.

**What slice E actually is: M3U export (632-696).** 23 mutants, 23 killed. Full
suite 278 files / 6071 tests.

- The file describes what is physically ON the server: matched + extra. A
  missing row has no server track and no path to write.
- `force:true` bypasses the auto-save `m3u_export.enabled` gate (manual export);
  `save_to_disk:true` also writes it server-side for media servers.
- The success check is `data.success === false`, so a response OMITTING success
  is treated as a success. Transcribed, not tidied.
- `found` uses `!= null`, so a server that resolved NONE of the tracks reads
  '(0/2 in library)' rather than a cheerful '(2 tracks)'.
- The toast names the playlist WITHOUT the 'Playlist' fallback the body and the
  filename both use — a nameless playlist really does read 'Exported M3U: '.
- NOT routed through routes/library's downloadExport: that one names the file
  its own way, picks its own mime, toasts on its own and revokes the object URL
  a second later. Different function, separate transcription.

`onExportM3u` deleted, for slices C and D's reason. **The server tab is now
COMPLETE** — all five slices built, and ServerCompareEditor has no leftover
callback props.

### THE FLIP-TIME CARVE-OUTS ARE NOW ENFORCED, NOT JUST WRITTEN DOWN

The slice-E note above ("do not delete openSyncDetailModal") was only a
document, and a document does not fail a build. `src/test/vanilla-seams.test.ts`
now asserts that all TEN vanilla definitions React reaches through `window`
still exist in their files.

**Why these specifically.** Every one of these call sites is optional-chained —
`window.openSyncDetailModal?.(id)` — which is correct at runtime, since a
missing global must not throw. The consequence is that deleting the definition
produces no error, no toast and no failing test: the button just stops working,
usually on a DIFFERENT page from the one whose region was deleted. Silence is
the whole danger.

Covered: openSyncDetailModal / _syncDetailFilter / _readdSyncWishlist
(pages-extra.js), openAutoSyncScheduleModal (auto-sync.js),
checkForActiveProcesses + openDownloadMissingModal (sync-spotify.js),
updateCardToSyncing + startSyncPolling (downloads.js), and both core.js bridges
this migration added.

**The guard was proven, not assumed.** Renaming openSyncDetailModal in
pages-extra.js was simulated: the guard failed with the intended message while
the other nine stayed green, and the file was restored under a sha256 check.

A failure here means the seam MOVED, which is fine — re-home it or publish it
from React and update the row. It must never be made green by deleting the row.

## BEATPORT WAVE — P0 SCOPING (the boundary question is ANSWERED)

The dossier has carried this open question since the original P0: *"Beatport
browse subsystem interplay with beatport-ui.js (3,913 lines, NOT in the sync
family — what's the boundary?)"*. Settled by evidence, not by assumption.

**Surface.** beatport-ui.js is 3,913 lines / 113 top-level functions, plus 29
Beatport functions in sync-services.js (442 mentions), plus the markup block at
index.html 2278-2600+. This is the LARGEST remaining wave — bigger than any
server-tab slice, and comparable to the Artist Map + Artist Web viz read.

**The boundary: beatport-ui.js is entirely the sync page's Beatport tab.**
Four checks, all negative for cross-page reach:

1. Every container it looks up by id is `beatport-*` or `genre-*`, and all of
   them live inside `#beatport-tab-content`, which is inside the sync page's own
   tab set (index.html 2395, `data-tab="beatport"`).
2. It references no page machinery at all — no setPage, no currentPage, no
   pageId, and no dashboard/library/watchlist container.
3. It publishes NO `window.*` globals, so nothing can call into it.
4. Nothing outside it names it — grep for callers across static/*.js and
   index.html returns nothing.

The ONE element it touches outside its own markup is
`#loading-overlay .loading-message` — the shared shell overlay, which every page
uses. That is a shell touch, not a cross-page contract.

**What that means for the port.** No seams to preserve and no bridges to add:
unlike the account tabs (which needed the core.js seeding bridge) and unlike
openSyncDetailModal (which the dashboard calls), this subsystem is self-contained.
It is wired purely by listeners it binds to its own markup, so once React owns
`#beatport-tab-content` those listeners have nothing to bind to and the file
severs cleanly and completely.

**Consequence for sequencing.** Because it is self-contained, it carries the
LOWEST interop risk of anything left — but the HIGHEST volume. It does not
block the shell wave and the shell wave does not block it.

**Still to do: the verbatim read.** This entry establishes scope and boundary
only. The line-by-line read of the 3,913 lines + the 29 sync-services functions
has NOT been done, and no Beatport code should be written until it has.

### CORRECTION — the Beatport boundary entry above is WRONG, and how it happened

The entry above claims *"beatport-ui.js is entirely the sync page's Beatport
tab"* with "four checks, all negative". That claim is false and the commit that
carried it (521cd50ac) should be read with this correction attached.

**The fact.** beatport-ui.js lines 3627-3912 (~285 lines, 8 functions) are not
Beatport at all. They are the SETTINGS page's media-server configuration:

    loadPlexMusicLibraries / selectPlexLibrary
    loadJellyfinUsers / selectJellyfinUser
    loadJellyfinMusicLibraries / selectJellyfinLibrary
    loadNavidromeMusicFolders / selectNavidromeMusicFolder

Called from settings.js (three call sites each) and from inline `onchange`
handlers in the SETTINGS markup (index.html 4392-4492). Nothing on the sync page
touches them.

**How the wrong conclusion was reached.** The container-id check was run through
`head -40`, and the conclusion "every container it looks up is beatport-* or
genre-*" was drawn from that truncated list. There are 48 distinct ids, and all
NINE settings ids (plex-music-library, jellyfin-user, navidrome-music-folder and
the three selector containers among them) sat past the cut. The evidence was
truncated and the conclusion was not.

**The lesson, which generalises past this file:** a `head`/`tail` on the evidence
makes a NEGATIVE claim ("nothing outside X") unsound, because the counter-example
is exactly what gets cut. Enumerate in full, or count first and compare the
count. The other three checks (no page machinery, no window globals, no external
callers) were sound and remain true.

**Corrected boundary.** beatport-ui.js is TWO tenants in one file:
- 1-3626 — the sync page's Beatport tab. Self-contained, no seams, severs
  cleanly. Everything the entry above says about interop holds for this part.
- 3627-3912 — the Settings page's media-server pickers. NOT sync content, must
  be re-homed before the file is deleted, and must not be ported into the sync
  route.

**Now enforced.** `vanilla-seams.test.ts` gained a second block, FOREIGN_TENANTS,
asserting all eight settings functions still exist in beatport-ui.js. Unlike the
window seams these fail LOUDLY when deleted (an unqualified call to a missing
function is a ReferenceError) — but loud-on-interaction still catches nobody,
since no one exercises a Settings dropdown while porting the Beatport tab.

### THE CROSS-FILE EDGE INVENTORY — 46 calls that snap at sever time

Verifying the correction above raised the obvious next question: if
beatport-ui.js had a foreign tenant nobody had noticed, what about the other six
files this port deletes from? Swept it properly. The answer is 46.

**Method.** 481 top-level functions are defined across the seven port files
(sync-services, sync-spotify, sync-listenbrainz, sync-lastfm,
sync-soulsync-discovery, auto-sync, beatport-ui). Cross-referenced against every
call in the 36 static/*.js files that SURVIVE the port. 46 of those 481 are
called from a surviving file. Each is an edge that breaks when its definition
goes — an unqualified global call, so it throws at the caller's next
interaction, on a page nobody is exercising while porting the sync page.

**The shape of it.**
- **downloads.js → sync-services/sync-spotify (17 edges).** The engine calling
  back into the page: the per-source card-phase updaters
  (updateTidalCardPhase, updateYouTubeCardPhase, updateBeatportCardPhase,
  updateDeezerCardPhase, updateSpotifyPublicCardPhase), updatePlaylistCardUI,
  the two sync pollers, the modal helpers, cleanupWishlist/clearWishlist. This
  is the Option A adopted-region contract seen from the other side — the port
  has always known the engine paints these, but the CALL EDGES were never
  enumerated.
- **shared-helpers.js → sync-spotify (6).** The download-modal helpers.
- **wishlist-tools.js → sync-services (4).** Three card-progress updaters plus
  generateDiscoveryActionButton.
- **stats-automations.js → auto-sync (5) + sync-services (3).** The P5g mirrored
  interop the audit already flagged, now with exact names:
  editMirroredCustomName, editMirroredSourceRef, getMirroredSourceRef,
  runMirroredPlaylistPipeline, pollMirroredPipelineStatus.
- **core.js → beatport-ui (5).** THE SECOND FLAW IN THE BOUNDARY ENTRY: core.js
  calls all five Beatport slider cleanups on page-leave (core.js 507-511). The
  earlier check grepped for a `beatportUI` NAMESPACE, which does not exist —
  wrong probe entirely for a classic script whose functions are bare globals.
- **settings.js → beatport-ui (4).** The tenant already covered.
- **init.js → sync-services/sync-spotify (2).** initializeSyncPage, loadSyncData.
- **api-monitor.js + core.js + init.js → rehydrateModal (5 callers).**
- **formatDuration** — called from four surviving files. Already known as the
  duplicate-global trap; now confirmed to have four external consumers.

**So beatport-ui.js has THREE tenants, not one:** the Beatport tab, the Settings
pickers, and a teardown contract with core.js.

**Enforced, and computed rather than listed.** `vanilla-crossfile.test.ts`
recomputes the whole inventory from the files on every run and compares it to a
recorded snapshot. Hand-listing is what produced the last two errors; a computed
list cannot miss an edge it was never told about. The FOREIGN_TENANTS block in
vanilla-seams.test.ts is deleted — it caught 8 of these 46 and asserted nothing
about the rest. vanilla-seams.test.ts now covers REACT -> vanilla only.

**Proven, not assumed:** simulated deleting a Settings function AND a core.js
teardown hook from beatport-ui.js; the guard failed on both, and the file was
restored under a sha256 check.

**This is the flip wave's real worklist.** Each of the 46 needs a decision —
re-home the definition, publish it from React, or delete the caller with it —
and none of them can now be missed silently.

### THE OTHER DIRECTION — 119 edges, and what they mean (different risk)

The 46-edge inventory covers SURVIVOR -> PORT calls: things that break when the
port deletes a definition. Reading Beatport's very first function exposed the
missing half — `loadBeatportHeroTracks` calls `getBeatportContentSignal()`,
which lives in **core.js**, not beatport-ui.js.

Swept that direction too: **119 PORT -> SURVIVOR edges.**

**These are not breakage risk.** The callee survives; the caller is the thing
being deleted. They are the CAPABILITY LIST — everything React must obtain (via
a window seam) or reimplement when it takes the page over. Useful as a checklist,
not as a guard, which is why they are recorded here and not asserted in a test.

**beatport-ui.js needs exactly six things** — a short list for a 3,900-line file:
`getBeatportContentSignal` (core.js), `showLoadingOverlay` / `hideLoadingOverlay`
/ `showToast` (downloads.js), `openDownloadMissingModalForArtistAlbum` and
`registerBeatportDownload` (shared-helpers.js).

**CAVEAT, stated rather than glossed:** this resolution is naive about duplicate
globals. `formatDuration` is defined in BOTH sync-services.js and
wishlist-tools.js, so "sync-spotify calls formatDuration<-wishlist-tools.js" may
be wrong about which copy wins — script load order decides that, not the
analysis. Treat any name in the duplicate set as unresolved until read.

### BEATPORT VERBATIM READ — SLIDER 1 (rebuild/hero, lines 1-322)

Read line by line. This is the CLONE BASELINE: the other four sliders (releases
662, hype picks 1002, charts 1298, DJ) share this shape, so they will be
diff-read against it rather than re-read from scratch.

**The shape.** Module state object {currentSlide, totalSlides, autoPlayInterval,
autoPlayDelay: 5000}; init -> fetch -> populate -> wire nav + indicators +
autoplay + hover-pause; a cleanup that clears the interval.

**Details that would be wrong if assumed:**
- **Re-entrancy guard (24-28).** `slider.dataset.initialized === 'true'` skips
  the whole re-init and JUST RESTARTS AUTOPLAY. Re-entering the tab must not
  re-bind listeners, and must not re-fetch.
- **The API-failure path keeps the STATIC markup (163-168).** On failure it
  calls setupBeatportSliderFunctionality() against the placeholder slides
  already in index.html — it does not render an error. So the markup carries
  real placeholder slides, and the port must decide to reproduce or drop them.
- **totalSlides starts at 4** and is overwritten by tracks.length (81). The 4 is
  the count of the static placeholders, which is why the failure path works.
- **Wrap-around both ways (239-243)**, and every slide gets exactly one of
  active/prev/next (252-262) — the CSS transition depends on prev vs next, so
  this is not just an 'active' toggle.
- **Nav and indicator clicks call preventDefault + stopPropagation** (197-198),
  because the slide itself is click-to-open; without it, paging would open the
  release.
- `resetAutoPlay` is an alias for `startAutoPlay`, which clears first (277-283).
- **AbortError is swallowed silently (58)** — a page-leave mid-fetch is not an
  error, which is what getBeatportContentSignal exists for.

**Declared divergence (an improvement, stated):** the slide HTML interpolates
`track.title`, `track.artist`, `track.url` and `track.image_url` into innerHTML
UNESCAPED (86-103), where the rest of this file uses `_esc`. The data is
third-party (Beatport). React escapes by default, so the port closes this
without trying — recorded so the difference is deliberate and not mistaken for
a transcription error later.

**Read status: 322 of ~3,600 in-scope lines.** Remaining: sliders 2-5
(diff-read), the top-10 lists, the click handlers + download-modal bridge, and
the genre browser (~1,400 lines, the largest single region).

### BEATPORT READ — SLIDER 2 (new releases, 323-661)

Read line by line and diffed against slider 1. **They are not clones.** The
comments say "copied from hero slider" on nearly every function, and nearly
every one has since drifted. Assuming shared behaviour would have been wrong
about all of the following:

| | slider 1 (hero) | slider 2 (releases) |
|---|---|---|
| autoplay delay | 5000 | **8000** |
| slide contents | one track | **grid of 10 cards** |
| slide count | tracks.length | **ceil(len/10)** |
| init order | mark initialised, THEN fetch | **fetch, wire up ONLY on success** |
| re-entry | restarts autoplay | **returns, restarts nothing** |
| API failure | silently keeps static markup | **renders an error block** |
| nav buttons | addEventListener | **cloneNode to strip old listeners** |
| indicator click | preventDefault + stopPropagation | **neither** |
| click → data | by INDEX | **by URL match (first wins)** |

**A real vanilla bug, recorded not fixed.** cleanupBeatportReleasesSlider clears
the interval on page-leave (655-659), and initializeBeatportReleasesSlider
returns early on re-entry because `dataset.initialized` is still 'true'
(349-352) — without restarting autoplay. So the releases slider auto-advances
once per app session: leave the sync page and come back and it is frozen, while
the hero slider revives because its guard explicitly restarts (26). Nav buttons
and indicators still work, so it looks alive.

The port should NOT silently inherit this. It is not a transcription question —
React's effect lifecycle would naturally restart it, so reproducing the freeze
takes deliberate code. FLAGGED FOR BOULDER as a probable bug to fix rather than
port.

**Other details worth keeping.** The final slide is padded to 10 with static
'More Releases / Coming Soon / Beatport' placeholder cards, which are excluded
from click handling by `:not(.beatport-release-placeholder)`. The click handler
resolves its release by matching `url` against the source array rather than by
position, so two releases sharing a URL would both open the first. Slides are
appended with `innerHTML +=` inside the loop; handlers are attached afterwards,
so nothing is lost, but every append re-parses the whole track.

Read status: 661 of ~3,600 in-scope lines.

### FIXED IN THE VANILLA: three of five Beatport sliders froze after a page-leave

Found by the read, fixed with Boulder's approval rather than ported.

**The bug.** Leaving the sync page runs core.js 507-511, which calls each
slider's cleanup, and each cleanup clears its autoplay interval. Coming back
re-runs the initialisers, which see their already-initialised guard and return —
without restarting the interval they no longer have. The slider then never
auto-advances again for the rest of the session. Arrows and dots still work, so
it reads as intentional.

**Which ones.** Checked all five individually rather than assuming the pattern:
- hero (14) — CORRECT, its guard already called startAutoPlay
- new releases (339) — BUG, fixed
- hype picks (683) — CORRECT, and it guards on a state flag rather than
  `dataset.initialized`, which is a second inconsistency in the family
- featured charts (1018) — BUG, fixed
- DJ charts (1314) — BUG, fixed

Three lines added, each calling the slider's own existing startAutoPlay, which
already clears any live interval before setting a new one — so a double call is
safe and no other path changes.

Harmless on the failure path too: with totalSlides 0 the autoplay tick wraps to
0 and finds no slides to touch.

**beatport-ui.js added to the vanilla-syntax parse gate.** That test covers the
classic scripts this migration edits, and this file is now one of them; nothing
else parse-checks it, since no bundler or typechecker sees it.

Full suite 280 files / 6085 tests.

### BEATPORT READ — SLIDER 3 (hype picks, 667-1005)

Read line by line. Diverges from BOTH earlier sliders, so the family is three
different designs, not two.

- **autoplay 4000** (hero 5000, releases 8000). Three sliders, three delays.
- **Re-entry guards on a STATE FLAG (`isInitialized`), not `dataset.initialized`**,
  and restarts autoplay — correct, but by a different mechanism than either
  neighbour. It never sets `dataset.initialized` at all. NOTE: the flag lives in
  module state, so it survives DOM replacement; if the markup were ever rebuilt
  underneath it, init would wrongly no-op. Not reachable today.
- **Click data is read back OUT OF THE DOM** (961-972): textContent of the
  rendered title/artist/label plus `img.src`. That is a third approach — hero
  closes over the source object, releases looks it up by URL, hype picks
  re-parses its own markup.
  - **Consequence worth stating:** createBeatportHypePickCard defaults an
    untitled release to the literal 'Unknown Title' / 'Unknown Artist' /
    'Hype Pick' (814-816), and the click handler reads those strings back as
    DATA. So a release missing a title sends "Unknown Title" into the download
    flow as its title. Reproducing that in React would take deliberate effort —
    holding the real object is the natural shape — so it becomes a decision, not
    a transcription.
  - `img.src` is the RESOLVED absolute URL, not the original attribute.
- **Placeholder cards are an icon only** (🔥), where the releases slider's
  placeholders carry 'More Releases / Coming Soon / Beatport' text.
- Shares with releases: cloneNode nav de-duplication, plain indicator clicks,
  an error block on failure. Shares with hero: hover-pause leaves the interval
  handle set rather than nulling it.

Read status: 1,005 of ~3,600 in-scope lines.

### BEATPORT READ — SLIDERS 4 + 5, AND THE COMPLETE FIVE-SLIDER TABLE

All five sliders now read line by line (1-1603). Slider 5 (DJ) really IS a clone
of slider 4 (charts) — the only true clone pair in the family — differing only
in delay, cards-per-slide, class prefix, CSS custom property and which click
handler it calls.

| | hero | releases | hype picks | charts | DJ |
|---|---|---|---|---|---|
| autoplay ms | 5000 | 8000 | 4000 | 10000 | 12000 |
| cards / slide | 1 | 10 | 10 | 10 | 3 |
| re-entry guard | dataset | dataset | **state flag** | dataset | dataset |
| sets dataset | before load | after load | **never** | after load | after load |
| sets state flag | **no** | yes | yes | yes | yes |
| API failure | keeps static markup | error block | error block | **nothing** | **nothing** |
| pads last slide | n/a | text cards | icon cards | **no** | **no** |
| click → data | closure | URL match | **DOM re-read** | URL match | URL match |
| nav de-dup | none | cloneNode | cloneNode | cloneNode | cloneNode |
| indicator click | stops propagation | plain | plain | plain | plain |
| DOM insert | insertAdjacentHTML | innerHTML += | insertAdjacentHTML | innerHTML += | innerHTML += |

**Five sliders, five configurations.** Not one of the twelve rows is uniform.
Every function in sliders 2-5 is commented "copied from" its predecessor and
every one has drifted since. A shared React `<BeatportSlider>` is clearly the
right shape, but its props must carry ALL of the above — writing one component
and assuming the differences are cosmetic would silently change five behaviours.

**Charts and DJ fail silently.** Unlike releases and hype picks, a failed load
calls no error renderer at all: loadBeatportFeaturedCharts / loadBeatportDJCharts
just return false, the init `.then(success => ...)` skips everything, and the
slider keeps whatever markup was already in the DOM. `dataset.initialized` is
never set, so a later re-entry retries the fetch — the only self-healing arm in
the family. Worth keeping deliberately.

Read status: **1,603 of ~3,600 in-scope lines (45%).** The whole slider family
is done. Remaining: top-10 lists, the click handlers + download-modal bridge,
and the genre browser.

### BEATPORT READ — THE TOP-10 LISTS (1605-1853)

Three lists: Beatport Top 10 and Hype Top 10 (tracks, from one endpoint), and
Top 10 Releases (separate endpoint).

**`cleanTrackText` (1638-1649) is the one piece of genuinely portable pure logic
in this file**, and it must be transcribed exactly rather than approximated. Four
substitutions in order:

    /([a-z$!@#%&*])([A-Z])/g   -> '$1 $2'    space between lower/symbol and upper
    /([a-zA-Z]),([a-zA-Z])/g   -> '$1, $2'   space after a comma
    /([a-zA-Z])(Mix|Remix|Extended|Version)\b/g -> '$1 $2'
    /\s+/g -> ' ', then trim

It exists because the scraped Beatport strings arrive concatenated. Note the
first rule is blunt: any internal capital gets split, so 'McCartney' becomes
'Mc Cartney' and 'MoBlack' becomes 'Mo Black'. That is the vanilla's accepted
cost, not a bug to fix mid-port — but it IS why the port must not "improve" it.
Also note it returns its argument UNCHANGED when falsy, so `cleanTrackText(0)`
is 0, not ''.

**Applied inconsistently.** The two TRACK lists clean title/artist/label. The
RELEASES list does not clean anything (1807-1809). Same file, same shape of data.

**Two details in the releases list that are easy to miss:**
- **Beatport CDN upscaling (1824):** the card background rewrites the artwork
  path `/image_size/95x95/` -> `/image_size/500x500/`. The thumbnail stays 95px;
  only the blurred background is upscaled. A plain string replace, so a URL
  without that exact segment passes through untouched.
- The background is applied as an INLINE style with a baked gradient
  (`linear-gradient(rgba(0,0,0,0.7), rgba(0,0,0,0.8)), url(...)`), not a CSS
  class or custom property — unlike every slider, which uses a custom property.

**A fourth click-resolution style.** These cards index straight back into the
source array (`releases[index]`, 1834). Across the file that now makes four:
closure over the item, lookup by URL, re-reading the rendered DOM, and index
alignment.

**Both list renderers bail silently on an empty array** (1656, 1700, 1789), so a
successful-but-empty response leaves whatever was in the container. Only an
explicit failure renders the error block — and showTop10ListsError writes the
SAME block into BOTH track containers.

Read status: 1,853 of ~3,600 in-scope lines (51%).

### BEATPORT READ COMPLETE — 3,626 in-scope lines

The whole of beatport-ui.js is now read line by line, minus the Settings tenant
at the tail. Regions: 5 sliders (1-1603), 3 top-10 lists (1605-1853), the click
handlers + download bridge (1855-2228), the genre browser (2230-3626).

**FIXED: the genre page's Top 100 button downloaded the WRONG genre.**
`showGenrePageView` builds `.genre-page-content` once and reuses it for every
genre thereafter (2704). The Top 100 listener was attached inside that
build-once block, closing over the FIRST genre's slug/id/name, and was never
rebound — while the element's dataset WAS refreshed on every open (2766-2768).
So: open Tech House, go back, open Techno, press Beatport Top 100 → it scraped
and queued **Tech House's** Top 100. Wrong tracks actually downloaded, silently,
with the right genre's name on screen. Now reads the dataset.

The two genre Top 10 handlers were never affected: they re-read the genre from
`.genre-page-title` textContent at click time (3295, 3308).

**The download bridge (the region that matters most).**
- Releases open as an ALBUM via `/api/beatport/release-metadata` ->
  `openDownloadMissingModalForArtistAlbum` with the real artist.
- Charts open as a COMPILATION ('Various Artists'), each track searched
  independently. Title gets `mix_name` appended unless it is 'original mix'
  (case-insensitive); the artist string is split on commas into a real array so
  the folder structure comes out right; durations parse 'm:ss' or bare seconds.
- `_enrichTracksWithProgress` batches, then **polls every 800ms in an
  unbounded `while (true)`** with no timeout, no abort signal and no failure cap
  — a poll that keeps throwing loops forever, and leaving the page does not stop
  it (it is the one fetch here that ignores getBeatportContentSignal). Its
  docblock claims WebSocket progress; the code polls. Its `chartName` parameter
  is unused.
- The double-click latch `_beatportModalOpening` is applied FIVE different ways:
  set-and-clear-on-every-exit (release clicks), set + a blind 2s `setTimeout`
  (Top 100, Hype 100, genre Top 100, genre charts), cleared unconditionally by
  `openBeatportChartAsDownloadModal`, and **not used at all** by the featured-
  chart and DJ-chart card clicks. A scrape slower than 2s reopens the gate mid
  flight.
- **Genre release downloads never register the progress bubble.**
  `handleBeatportReleaseCardClick` calls `registerBeatportDownload` (1911);
  its genre twin `handleGenreReleaseCardClick` is otherwise identical and does
  not. Left as-is — recorded for the port to decide, since it is a UX gap rather
  than a wrong result.

**Five ways of answering "what did the user click".** Closure over the item
(hero), lookup by URL (releases/charts/DJ), re-reading rendered text (hype
picks), index alignment (top-10 releases), and full DOM scraping
(`getGenrePageTrackData`, 3348). The last two re-read defaulted strings, so
'Unknown Title' can travel into a download as real metadata.

**Genre browser specifics.** Module-level cache survives modal closes and is
never invalidated (`lastLoaded` is recorded but unused). Image loading uses two
cooperative workers polling a shared queue, paused by flipping
`imageLoadingActive` on close. Nine genre names are hard-filtered by an
inline list. Two identical card renderers exist (fresh vs cached). Retry
buttons use inline `onclick` global calls. The search filter sets
`display:'block'`, overwriting whatever the CSS chose. `genreHeroSliderState` is
the file's only WINDOW-scoped state. The genre view reaches into the main hero
slider twice to stop its autoplay and restart it on the way back — but touches
none of the other four.

Read status: **COMPLETE.** Next: the React port itself.

### BEATPORT P1 — the pure core (-beatport.core.ts)

32 mutants, 32 killed. Full suite 281 files / 6126 tests. Build clean.

Transcribed, with the read's findings encoded as tests rather than left in prose:
cleanTrackText's four ordered rules, parseBeatportDuration, the CDN 95x95→500x500
upscale and its baked gradient, the five-slider config table, slide paging and
wrap-around, the nine-name genre filter, and the whole chart→download-modal
bridge (compilation album, mix-name suffix, comma-split artists, per-track
release metadata).

**A mutation survivor that was a genuine test defect, not an equivalence.**
Dropping cleanTrackText's THIRD rule entirely — the Mix/Remix/Extended/Version
spacer — did not fail a single test. Nor did removing its `\b`.

The reason is worth recording: every case I had written ('SongExtended',
'SongRemix', …) is already split by rule ONE, which fires on the lower→upper
transition. Rule three only does work where rule one CANNOT fire — after an
uppercase letter. So the tests were exercising rule three's output while
actually measuring rule one.

Closed with all-caps fixtures, where only rule three is in play:
'SUMMERMix' → 'SUMMER Mix', and 'SUMMERMixed' → unchanged, which is what the
word boundary is for. Both mutants now die.

**The general lesson:** in a pipeline of ordered rewrites, a test that passes
through the whole pipeline does not prove any particular stage runs. Each stage
needs an input the earlier stages leave alone. Mutation testing is what surfaced
it; the tests looked thorough and were not.

Next: the API layer, then the shared slider component driven by the config table.

### BEATPORT P1 REVIEW — two contract details the first pass missed

Re-read the committed core against the vanilla. The transcriptions were right;
two things around the download hand-off were not recorded at all.

**1. Charts and releases open the download modal in DIFFERENT MODES.**
`openDownloadMissingModalForArtistAlbum` is declared
`(…, showLoadingOverlayParam = true, contextType = 'artist_album')`
(shared-helpers.js 1763). Charts pass `'playlist'` explicitly as a seventh
argument (2059); releases pass only SIX (1900-1907) and so take the default.

The difference is an argument that ISN'T THERE, which is exactly why it slipped
past — the release call reads as complete. Now `beatportDownloadContext()`, with
both arms asserted. Both call sites pass `false` for the loading overlay, since
Beatport shows its own.

**2. The release bubble's image has a two-step fallback (1910).** The album art
from `/api/beatport/release-metadata` wins, then the clicked card's own
thumbnail, then ''. Charts skip this entirely — they hand the chart image
straight to registerBeatportDownload. Now `releaseBubbleImage()`.

**Still open, deliberately, for the UI slice:** the vanilla generates
`virtualPlaylistId` as `beatport_chart_${Date.now()}_${random}` /
`beatport_release_…` at the call site. Impure, so it stays out of the core and
belongs to the wire layer — recorded here so it is not forgotten, since the
download bubble is keyed off it.

**Also still open:** hype-pick cards re-read their own rendered text, so a
release with no title sends the literal 'Unknown Title' / 'Unknown Artist' /
'Hype Pick' into the download. React holds the real object, so the port
naturally sends the true values. That is a BEHAVIOUR CHANGE and needs a
decision at the UI slice, not a silent improvement.

44 tests. Full suite 281 files / 6129 tests.

### BEATPORT P2 — the wire layer (-beatport.api.ts)

All 19 endpoints, typed. 23 mutants, 23 killed. Full suite 282 files / 6151.

**Abort is not uniform in the vanilla, and the split is meaningful.** The SEVEN
homepage content loads pass core.js's getBeatportContentSignal so leaving the
page cancels them; the other twelve pass nothing. Reproduced exactly, including
`signal ? { signal } : undefined` — an absent signal means a bare `fetch(url)`,
not `fetch(url, { signal: undefined })`, and there is a test for that.

**THE ONE DELIBERATE DIVERGENCE — the enrichment poll.** beatport-ui.js
1955-1980 polls inside `while (true)` with no attempt cap, no abort (it is the
only fetch in that file to ignore the page-leave signal) and a `catch` INSIDE
the loop that swallows a throwing poll and continues. A progress endpoint that
keeps failing therefore spins every 800ms for the life of the tab, and
navigating away does not stop it.

The port adds three exits: an AbortSignal, a consecutive-failure cap, and an
overall attempt ceiling (~30 min at the vanilla's 800ms). All three return the
ORIGINAL tracks — which is exactly what the vanilla does on every failure that
does not hang — so the only behaviour that changes is the hang. Every exit is
tested, including that a single blip resets the failure run rather than ending
the poll.

**A test of mine was wrong, and the code was right.** I asserted the progress
callback fires once per non-final poll. It fires on the FINAL poll too: 1963-1969
paints the overlay BEFORE testing `done`, so the last update carries undefined
counts. The vanilla briefly shows '(undefined/undefined)'. Transcribed, and the
test now says so — whatever renders this must tolerate the blank.

**A mutation survivor that was NOT equivalent.** Deleting the abort check BEFORE
the sleep left the one after it, so the poll still exited — but only after
waiting out a full 800ms tick. The test aborted before calling and never looked
at the sleep. Now asserts `sleep` is not called at all when already aborted,
plus a second case aborting DURING the interval.

Next: the shared slider component, driven by the config table.

### BEATPORT P2 REVIEW

Cross-checked every endpoint string in -beatport.api.ts against every one in
beatport-ui.js, mechanically rather than by eye: 17 exact matches, and the two
remaining (the Beatport and Hype Top 100s) are produced by the one variant
function whose output both tests assert as literals. Nothing missing, nothing
invented.

**The gap the review found was in the DOCS, not the code.** The header said "the
seven homepage loads" abort without naming them, which leaves the UI slice to
guess — and guessing wrong here is invisible, because passing a signal where the
vanilla passes none silently cancels a scrape mid-download.

Now named, with the reasoning stated: the twelve that DON'T abort are exactly
the ones that lead to a download, so cancelling them on a tab change would be a
behaviour change rather than a fix. `VANILLA_ABORTED_ENDPOINTS` is the same list
as data, pinned by a test that drives all seven loaders and compares.

Full suite 282 files / 6153 tests.

### BEATPORT P3 — the one slider component (-ui/beatport-slider.tsx)

22 mutants, 22 killed. Full suite 283 files / 6172. Build clean.

Five vanilla sliders become one component, and it is only safe because every
difference the read found is a PROP: autoplay delay, cards per slide, whether
the slide wraps a grid, whether the last slide is padded, and the CSS slug.

**The slug is checked against the stylesheet, not trusted.** Class names are
derived (`beatport-<slug>-slide` and friends), and a wrong slug is SILENT — a
missing class renders an unstyled slider, never an error. So the test reads
static/style.css and asserts every derived name exists, for all five sliders,
and additionally that the hero has NO grid class. That also pins the quirk that
the hero's slug is 'rebuild', not 'hero': the markup predates the name.

**What React removes, and why none of it is a divergence.** The
`dataset.initialized` / `isInitialized` guards and the `cloneNode` nav
de-duplication both existed to stop duplicate listeners on re-rendered DOM;
effects handle that. The autoplay-frozen-on-return bug those guards caused was
fixed in the VANILLA first (44f60b3fc), so the two sides agree today rather than
the port quietly being better.

**One declared divergence, and it took a timer-count assertion to pin.** With a
single slide the port schedules no interval; the vanilla schedules one and lets
every tick wrap 0 → 0. The rendered output is identical, so the mutation
survived a render-based test. `vi.getTimerCount()` is the only thing that can
see it — now asserted at 0 for one slide and 0 while hovered.

**The other survivor was a plain test gap:** the stopPropagation test clicked
'next' and an indicator but never 'prev', so the prev button's guard was
unverified. All three controls now.

Next: the five section components that feed this slider, then the genre browser.

### BEATPORT P3 REVIEW — a missing wrapper the CSS test could not see

**The bug: the nav buttons were rendered bare.** index.html 2832-2837 wraps both
in `<div class="beatport-<slug>-slider-nav">`, which is what positions them; the
class exists for all five sliders. The component emitted the buttons as direct
children of the container, so they would have landed wherever the flow put them.

**Why the existing test missed it, which is the part worth keeping.** The CSS
test checked a HAND-PICKED list of five class names against style.css. The
component emitted seven. A test that verifies a subset it was told about cannot
notice a class it was never told about — the same shape of error as the
`head -40` truncation earlier in this port: a check whose scope is narrower than
the thing it is supposed to cover, reporting success.

Fixed at the source rather than by adding one more name: every class the
component emits is now derived in ONE place (`beatportSliderClasses`), and the
test iterates that object rather than a list beside it. Adding an eighth class
to the component now automatically demands it exist in the stylesheet.

Also verified while here, rather than assumed: the ‹ › glyphs and the
`nav-btn` / `prev-btn` / `next-btn` class pairs match the markup exactly. The
`prev-btn`/`next-btn` classes are STYLED for only two of the five, but the
markup applies them to all five, so the port emits them all — markup parity, not
stylesheet parity.

Full suite 283 files / 6173 tests. Build clean.

### BEATPORT P4 — the shared section load lifecycle (-beatport.use-section.ts)

15 mutants, 15 killed. Full suite 284 files / 6187. Build clean.

**A behaviour the read caught and the config now carries: WHEN a slider is
marked loaded, which decides whether returning to the tab re-fetches.**

The hero marks itself before it even fetches (31-34), so a failed hero load is
never retried for the rest of the session — the section just keeps its static
placeholders. The other four mark themselves only on success, so a FAILURE
retries next visit and a SUCCESS does not. `marksLoadedBeforeFetch` in the
config table, with both arms tested.

**Why a module-level cache, and why it is not laziness.** The vanilla holds its
loaded state in the DOM, which survives tab switches because the sync page's
markup is hidden rather than removed. A React section that unmounts would
re-fetch every visit — and these endpoints SCRAPE BEATPORT, slowly and
rate-limited. Re-fetching per visit is not a neutral refactor; it is a
behaviour change with a cost on someone else's server.

**One genuinely equivalent mutant, resolved by deleting the branch.** Marking a
section loaded on success was guarded by `if (!marksLoadedBeforeFetch)`, which a
pre-marked section makes a no-op anyway (Set membership). Rather than annotate
an equivalence, the guard is gone and the add is unconditional.

**Two survivors that were one real untested scenario:** a load that settles
LATE, after `reload()` has already aborted it and started another. Both abort
guards exist for exactly that, and nothing exercised it.

Closing it took three attempts, and the failures are the lesson:
1. The first test used a synchronous `act()`. A promise rejection lands in a
   microtask, so the assertion ran before the `catch` did. `await act(async …)`.
2. The second still let the stale-SUCCESS mutant live, because the test
   rejected the stale promise and then tried to RESOLVE THE SAME ONE. A promise
   settles once, so the resolve was a no-op and that path was never entered.
   Stale-success now has its own test with its own promise.

Both were tests that passed while measuring nothing — the same failure mode as
the cleanTrackText rule-three gap earlier in this port. Mutation testing is the
only thing that has caught any of them.

### BEATPORT P4 REVIEW — the cache remembered the flag, not the data

**A real bug, and the tests said it was fine.** The session cache recorded THAT
a section had loaded, never WHAT it loaded. So the second mount — every return
to the tab — produced `status: 'ready'` with an empty item list. An empty
slider, permanently, for the rest of the session.

The vanilla does not have this because its "cache" IS the rendered DOM: the sync
page is hidden rather than removed, so the cards are simply still there. Storing
only the flag reproduced the guard and threw away the thing the guard protects.

The test that should have caught it asserted `status === 'ready'` and
`load` called once, and never looked at `items` — which is the entire point of
not re-fetching. Now stores the items, hydrates them on the FIRST render rather
than in an effect (an effect would flash an empty slider every visit), and
asserts the items on return.

The hero is a deliberate exception: it caches an EMPTY list, because it claims
its slot before fetching and has nothing to show — its static placeholders are
the intended fallback. Tested separately.

**Second finding, a latent footgun rather than a live bug.** The effect depended
on the `config` OBJECT. Production passes the stable BEATPORT_SLIDERS entries,
so nothing misbehaves — but an inline `config={{…}}` would give a new identity
every render, re-run an effect that calls setStatus, and loop forever. Now
depends on the two primitive fields it actually reads.

Full suite 284 files / 6189 tests.

### BEATPORT P5 — the section wrapper (-ui/beatport-section.tsx)

13 mutants, 11 killed, 2 DECLARED EQUIVALENT with the reasoning below. Full
suite 284 files / 6199. Build clean.

Joins the loader to the slider and renders the section's own failure arm. The
three arms are the point: the vanilla's five sections disagree about what a
failed load looks like, and the difference only shows when Beatport is down.
- releases / hype picks: an error block, with per-section copy
- charts / DJ: NOTHING — those loaders have no error renderer at all
- hero: nothing either, but for a different reason — its placeholder slides are
  already in the page markup and setupBeatportSliderWithPlaceholders just wires
  them up

**I justified a change with a scenario that cannot happen, and the mutation
pass caught it.** I switched the render guard from `status !== 'ready'` to
`items.length === 0` and wrote a comment claiming it keeps items on screen
during a reload. A mutant reverting the guard survived — because
BeatportSection does not expose reload, so that path is unreachable through the
component. The test I had written to defend the change reached around the
component and drove the HOOK directly, which is why it passed while proving
nothing about the section.

Corrected rather than papered over: the misplaced test is deleted (the hook
already has a real one), and the comment now says plainly that all three
variants — content guard, status guard, and no guard at all, since
BeatportSlider already returns null for an empty list — are equivalent in every
reachable state.

**The lesson, which is the same one as the earlier gaps:** a test that reaches
past the unit it names can pass without touching it. Mutation testing is what
distinguishes "my justification is right" from "my justification is untested",
and here it was the latter.

### BEATPORT P5 REVIEW — two structural notes, both checked not assumed

**1. The error block sits somewhere different, and that is safe — verified.**
The vanilla writes the error INTO the slider track (`sliderTrack.innerHTML =
…`), leaving the nav buttons and the indicator container in place, because both
are static siblings in index.html. The port renders the error INSTEAD of the
whole slider, so neither appears.

Checked rather than assumed: `.beatport-releases-loading` is a standalone class
selector with its own min-height, background, radius and dashed border, and
there is no descendant selector anywhere in the family. It styles itself
wherever it sits.

The visible difference is that the vanilla keeps dead arrows on screen during an
error — they respond, wrap to slide 0 and find nothing. The port shows the error
box alone. Declared as an improvement rather than smuggled in.

**2. A FLIP-WAVE CONSEQUENCE that needs recording now.** The port's slider
renders the track, the nav wrapper, both buttons and the indicator container
ITSELF. All five of those currently exist as static markup in index.html
(2395-2600+ for the Beatport tab). At flip time that block must be DELETED, not
merely hidden, or every slider will render two sets of arrows and two indicator
rows — a duplicate-id situation as well, since the port reuses the vanilla ids.

This is the same class as the duplicate-id trap the discover port hit
(f6369f914, "the duplicate-id guard was right"). Recorded against the flip
rather than left to be discovered.

### A BREAK THE REVIEW CAUGHT BEFORE THE HERO CARD WAS EVEN WRITTEN

Starting the hero slide renderer, I checked what actually paints its artwork
instead of assuming the markup could go anywhere. style.css 17056:

    .beatport-rebuild-slide[data-image]::before { background-image: var(--slide-bg-image); … }

That is an ATTRIBUTE SELECTOR on the slide element, reading a custom property
that has to inherit from it. The vanilla puts both on the slide (86-91).

**The port could not have.** BeatportSlider owns the slide element and the card
renderer returns its children, so `data-image` and `--slide-bg-image` would have
landed on an inner div. The selector would never match, and the hero would
render with no artwork at all — no error, no warning, just a flat panel where
the album art should be. Exactly the failure mode the slug check was built for,
one level deeper.

Fixed with a `slideAttributes` prop that merges onto the slide itself, applied
only for one-card-per-slide layouts (the hero's shape; grid sliders put their
attributes on the cards). Tested for the merge, for the slider's own class and
data-slide surviving it, and for it NOT being consulted on a ten-card slide.

**The general rule this is the third instance of:** when the vanilla puts an
attribute on an element, check whether a SELECTOR depends on it being there
before deciding where the React version can put it. `[data-image]`,
`.beatport-<slug>-slider-nav` and the slug-derived class names have all now been
verified against style.css rather than by eye — and two of the three were wrong
before checking.

### BEATPORT P6 — the five card shapes (-ui/beatport-cards.tsx)

Full suite 285 files / 6219. Build clean. All 32 class names checked against
style.css rather than by eye.

They look interchangeable and are not. The differences, each transcribed and
each asserted:

- **The background custom property is emitted differently.** The RELEASE card
  sets `--card-bg-image` unconditionally, so an artless release gets `url('')`
  (439). Hype picks and both chart cards OMIT the style entirely in that case
  (805, 1121, 1420). Same-looking cards, opposite rules.
- **Only hype picks default their text**, and its label default is 'Hype Pick',
  not 'Unknown Label'. The release card defaults nothing. This is not cosmetic:
  the vanilla's hype-pick click handler reads the rendered text back out as the
  track's metadata, so those literals can reach the download engine.
- **The two placeholders differ in kind.** Releases pad with a captioned card
  ('More Releases / Coming Soon / Beatport'); hype picks pad with a bare 🔥 and
  no info block at all.
- **Chart and DJ cards use DIFFERENT custom properties** — `--chart-bg-image`
  vs `--dj-bg-image` — so the shared component's variant decides the property,
  not just the class prefix. Asserted both ways round: setting one must leave
  the other empty.
- **`data-url` is emitted as `''` rather than omitted** on the chart cards,
  because the vanilla's click wiring tests against `''` and a missing attribute
  reads as null.

The hero's slide attributes are emitted UNCONDITIONALLY, matching the vanilla:
an artless track still gets `data-image=""`, so `[data-image]::before` still
matches and paints nothing. Emitting them conditionally would be a different
behaviour, not a tidier one.

### BEATPORT P6 REVIEW — attribute PRESENCE, a JSX-vs-template difference

Reviewing the cards raised a difference that is invisible in the code and
visible in the DOM: **template interpolation and JSX disagree about missing
values.**

`data-url="${release.url}"` with no url writes the literal string
`data-url="undefined"`. The JSX equivalent `data-url={release.url}` DROPS the
attribute entirely. So a straight transcription silently changes whether the
attribute is present — and the vanilla's own card wiring queries
`.beatport-release-card[data-url]` (490), which matches the first and not the
second.

Checked before deciding, rather than assuming: the ONLY two attribute selectors
in the entire Beatport stylesheet are
`.beatport-tab-button[data-beatport-tab="browse"]` (16934) and
`.beatport-rebuild-slide[data-image]` (17056). No card styling depends on
`data-url`, and the port attaches click handlers directly rather than by
selector, so nothing breaks either way.

Normalised anyway, and declared: every missing value becomes `''`, so the
attribute stays PRESENT as in the vanilla, without the bogus 'undefined' text.
The hype-pick and chart cards already did this (`|| ''`); the release card was
the odd one out, which is the sort of inconsistency that only shows up when
someone later writes a selector against it.

Full suite 285 files / 6220 tests.

### BEATPORT P7 — the download bridge (-beatport.downloads.ts)

The region where a click becomes files on disk (beatport-ui.js 1855-2228), read
again line by line before any of it was written.

**Four card types, ONE release handler.** The hero slide (149), the releases
slider (502), the hype picks (974) and the top-10 release cards (1834) all call
handleBeatportReleaseCardClick. Only the "which release was clicked" question
differs between them, and that is settled in the components. So the bridge is
three flows, not seven: release, chart card, Top 100.

**A release is an ALBUM; a chart is a COMPILATION.** Different endpoint,
different contextType, different bubble image, different artist. The two call
sites look alike, and the difference in the release one is an argument that
ISN'T THERE — it passes six arguments and takes shared-helpers.js 1763's
`contextType = 'artist_album'` default. Verified that default rather than
assumed it, and the port passes it explicitly so the difference is visible.

**The latch is applied five ways, all reproduced.** Release clicks hold it for
the whole of the work; the two Top 100 buttons release it on a blind 2s timer
(so a slower scrape reopens the gate mid-flight); openBeatportChartAsDownloadModal
clears it unconditionally whether or not it set it; the featured-chart and
DJ-chart card clicks never touch it at all. Unifying them would change which
clicks are swallowed on the path that queues 100 downloads, so they are
transcribed and each one is asserted.

**ONE declared divergence, and it is tiny.** The vanilla has no `data.album`
check and instead throws a TypeError one line later when it logs
`data.album.name`. Both land in the same catch and show a toast; only the
wording differs, and a response with tracks but no album is malformed either
way. The port checks it explicitly.

**Anchored on the vanilla, verified not assumed:**
- the toast copy differs per variant in four places ('No chart URL available'
  vs 'No DJ chart URL available', 'Featured Chart: ' vs 'DJ Chart: ', and the
  two empty-chart messages),
- the chart's DISPLAY name (`name - creator`) and the name sent to the SCRAPER
  (prefixed) are deliberately different strings,
- the chart's album id (2005) has NO random suffix where the virtual playlist id
  (2047) does, and the enrichment id (1939) uses a 6-char suffix where the
  playlist ids use 9,
- neither Top 100 button shows a 'Loading…' toast, where every other flow does,
- enrichment progress is written STRAIGHT into `#loading-overlay .loading-message`
  and null-guarded (1964-1966). Kept that way rather than re-calling
  showLoadingOverlay, because downloads.js 4321 does NOT null-guard the overlay
  element — routing through it would turn a missing overlay from a silent no-op
  into a throw inside the download path.

**A FLIP-WAVE CARVE-OUT, found by following registerBeatportDownload.** It lives
in shared-helpers.js 3390 and writes into `beatportDownloadBubbles`, a top-level
`let` in core.js 555 that no module can reach — so React must call it, and
showBeatportDownloadsSection (3430) renders into `#beatport-downloads-section`
(index.html 2865). That div is an ADOPTED REGION: the React Beatport tab must
render it and never touch its contents, or every download bubble silently stops
appearing. Added to vanilla-seams.test.ts so deleting the function fails loudly.

**Mutation pass: 31 mutants, 31 killed.** Four rounds — two survivors were real
test gaps and two were my own bad anchors:
- SURVIVED: leaking the latch on the release no-url exit. Real gap: the test
  asserted the toast and the absent fetch but not the latch, and a leak there
  would silently swallow every release click for the rest of the session.
- SURVIVED: shortening the release id suffix from 9 to 6. Real gap: the test
  matched the id's prefix only, and the downloads test pinned `random` to 0.5,
  whose token is one character at either length.
- ANCHOR MISS (2x): `copy.noTracks` appears in both the chart and Top 100
  guards. Re-anchored each on the fetch line above it — and the second guard,
  which the first anchor had been hiding, got its own mutant.
- ANCHOR MISS (0x): oxfmt had reflowed the token expression across lines.
Neither anchor miss was counted as a pass.

Full suite 287 files / 6254 tests. Build clean. Lint clean.

**Beatport remaining:** wiring the five sections to these handlers, the three
top-10 lists, and the genre browser (~1,400 lines).

### CORRECTION — the hype-pick "decision" was smaller than I recorded

The P0 read, and the P6 card docblock that carried it forward, said the hype
picks slider's DOM re-read meant "a release missing a title is DOWNLOADED as
'Unknown Title'", and filed it as an open decision for Boulder.

Traced end to end during the P7 review and that is WRONG. The re-read is real
(961-972), but handleBeatportReleaseCardClick uses `release.title` in exactly
two places — the 'Loading …' toast (1871) and the overlay caption (1872). The
download's name is `data.album.name` from /api/beatport/release-metadata (1897),
and the tracks come from that endpoint too. Nothing scraped off the card reaches
the download engine except `image_url`, and only as the bubble-image FALLBACK
when the endpoint returned no album art (1910).

So the port holding the real object instead of re-reading rendered text changes:
the toast copy for an untitled release, and — in the artless case — a raw URL
where the vanilla passed `img.src`'s resolved absolute form. Both cosmetic.
NOT an open decision. Corrected in beatport-cards.tsx and its test.

The general warning still stands for the OTHER two DOM-scraping call sites:
`getGenrePageTrackData` (3348) feeds openBeatportChartAsDownloadModal directly,
so defaulted strings there really do become track metadata. That one is still
ahead of us, in the genre-browser wave.

### THE FIVE LOADERS' ERROR COPY — re-read before P8, and it is not uniform

Each error-block section has TWO distinct messages, which the P4 hook's single
`defaultErrorMessage` would have flattened:

| | API said no | fetch threw |
|---|---|---|
| releases (398-410) | `data.error \|\| 'No releases available'` | `'Failed to load releases'` |
| hype picks (742-754) | `data.error \|\| 'No hype picks available'` | `'Failed to load hype picks'` |
| charts / DJ | nothing rendered | nothing rendered |
| hero | keeps its static markup | keeps its static markup |

Note the console line for hype picks says 'No hype picks found' while the
DISPLAYED string is 'No hype picks available' — the two are not the same, and
copying the wrong one is the obvious mistake.

So the P8 loaders THROW with the exact message rather than returning empty: the
hook already renders `error.message` for an error-block section, which makes the
backend's own `data.error` reach the user, as the vanilla does.

### BEATPORT P8 — the five sections wired end to end

`-beatport.loaders.ts` (the five load functions) + `-ui/beatport-sections.tsx`
(the five components). A stubbed response now goes in one end and a download
modal comes out the other, for all five.

**A BREAK THE WIRING CAUGHT.** `BeatportSection` did not forward
`slideAttributes` to the slider. P3 added the prop, P6 built
`heroSlideAttributes` to fill it, and the section in between silently dropped
it — so the hero would have rendered with no artwork at all, and nothing would
have failed. Neither slice was wrong on its own; the gap only existed at the
join, which is what this phase is. Forwarded, and mutation-tested by deleting
the forward again.

**What the wiring alone decides, and is therefore tested here:**
- WHICH handler a card gets. All four release-ish sections hand off to
  openBeatportRelease; the two chart sections hand off to openBeatportChartCard
  with their own variant. A hype pick wired to the chart handler still draws a
  hype pick and scrapes the wrong endpoint on click.
- WHETHER a card is clickable. The hero, releases and hype picks all refuse to
  bind when the url is missing or '#' (128, 500-501, 959). The two chart
  sections bind UNCONDITIONALLY (1158, 1457) and let the handler toast. Same
  markup, opposite behaviour, and only visible when the url is missing.
- The cache key per section, and the slider config per section.

**Mutation pass: 29 mutants, 29 killed** — after three rounds. Two survivors
were my own bad mutants and three were real test gaps:
- BAD MUTANT: `release={release as never}` is a type-only change with no runtime
  effect. Re-anchored onto the actual handler call.
- BAD MUTANT: an injected `onClick={undefined}` placed BEFORE the real prop —
  the later JSX prop wins, so it mutated nothing. Re-anchored onto the real one.
- GAP: nothing asserted the hype-picks SLUG, so borrowing the releases config
  passed. That would render hype picks under `beatport-releases-*` classes —
  unstyled where the two differ, and silent.
- GAP (the interesting one): two sections sharing a cache key survived. On a
  FIRST mount it is invisible — both start loading before either has cached
  anything, so both fetch and both render. The damage only appears on the tab
  switch BACK, when the second section hydrates from the first's items and never
  re-fetches to correct itself. The test now unmounts and remounts.
- GAP: the chart sections' bind-unconditionally behaviour was asserted in a
  comment and nowhere else.

Full suite 289 files / 6296 tests. Build clean. Lint clean.

**Beatport remaining:** the three top-10 lists (1605-1853) and the genre
browser (2230-3626, ~1,400 lines).

### BEATPORT P9 — the three top-10 lists, and a SIXTH download flow found by reading

`-ui/beatport-top10.tsx` + two loaders + `openBeatportTop10List`.

**THE FINDING: the two top-10 track lists are clickable, and the handler is not
in beatport-ui.js.** Reading 1605-1853 alone, those cards look inert — they carry
a `data-url` and no listener. The listener is in **sync-services.js 3948-3963**,
bound to the whole CONTAINER, so clicking anywhere in the list (including its
header) queues all ten tracks via handleRebuildChartClick (4909-4936). Porting
beatport-ui.js faithfully and stopping there would have shipped two dead panels.

That handler then SCRAPES the rendered cards for the track data
(getRebuildPageTrackData, 4937-4992). The port passes the loaded objects
instead, and that substitution was checked rather than assumed:
- the scrape reads text cleanTrackText has ALREADY been applied to at render
  time (1669-1671), and buildChartTracks applies it again downstream —
  idempotent for these inputs;
- the scrape's per-field 'Unknown …' defaults are the renderer's own, and
  buildChartTrackName defaults identically;
- the only field the scrape drops (artwork_url) is one buildChartTracks never
  reads.
So the download metadata is unchanged. Declared divergence: the vanilla's
empty-list failure can show the user a SELECTOR STRING
('No track cards found in #beatport-top10-list'). There is no container to name
in the port, so it uses the other message the same flow already produces.

**The three lists are not three copies of one list.**
- The two TRACK lists come from ONE endpoint, load together and fail together —
  showTop10ListsError writes the SAME block into BOTH containers, replacing the
  list headers along with the content.
- The track lists clean their text; the RELEASES list cleans nothing
  (1807-1809). Same file, same shape of data.
- The releases list is the mirror image on clicks: per-card handlers, no
  container handler — and it is the ONLY one of the four release-card call sites
  with no url test (1834), which makes it the only place the handler's own
  'No release URL available' toast is reachable.
- A successful-but-EMPTY response is NOT a failure for any of the three: they
  test `data.success` alone (1615, 1767), where every slider also tests length.
  The populate call then bails and the static 'Loading …' markup stays forever.
- The hype list's subtitle CHANGES when the data lands: index.html says
  "Editor's hottest trending picks", 1706 says "Editor's trending picks". The
  port renders the loaded string.

`useBeatportOnce` added alongside `useBeatportSection` — same session cache, no
slider config, since all three of these render an error block.

**Mutation pass: 28 mutants, 28 killed** after two rounds. All five first-round
survivors were real gaps, no bad mutants this time:
- the releases loader's thrown-fetch copy was never asserted;
- nothing tested that a second container click is swallowed;
- the enrichment stub ECHOED its input, so "skipped enrichment entirely"
  passed — the stub now returns a distinguishable payload;
- the hype subtitle was never asserted;
- `useBeatportOnce`'s first-render hydration could not be seen by reading the
  final state, because `render` flushes effects inside act(). Asserted now by
  recording every render and checking the FIRST one.

**The full suite caught what the scoped run could not:** the export-coverage
gate failed on `openBeatportTop10List`, exercised only through the component and
named by no test. Given direct tests, which also cover its empty-list arm.

Full suite 290 files / 6329 tests. Build clean. Lint clean.

**Beatport remaining:** the genre browser (2230-3626, ~1,400 lines) — the last
piece.

### BEATPORT P10a — the genre browser's data layer (-beatport.genres.ts)

The genre list is cheap; the genre IMAGES are one scraped request each for ~40
genres, so the vanilla does something more careful than it first looks
(2526-2625), and all four parts survive the port:

1. render the list immediately with emoji placeholders,
2. fill images in the background with TWO cooperative workers on a shared
   queue, 100ms apart,
3. cache each resolved url so reopening the modal only queues what is missing,
4. PAUSE on modal close — not cancel — and resume from the same point.

**The one change:** the vanilla caches by mutating `genre.imageUrl` in place,
which React cannot observe. The urls live in a Map keyed by SLUG AND ID — the
pair, because the vanilla's card selector matches on both attributes at once
(2511-2513), so a slug alone would let two genres share an image.

**Transcribed, not corrected:** images load only when there are MORE THAN five
genres (2433, a strict `>`), so a list of exactly five keeps its emoji. The real
list is ~40, so this only bites when the scrape has mostly failed — and not
firing five more requests at a struggling backend is defensible.

Also recorded: `/api/beatport/genres` is the only Beatport fetch that checks
`response.ok`, and it puts the status line into the message the user reads. And
two of the vanilla cache's five fields are dead — `lastLoaded` is written and
never read, `imageWorkers` is declared and never assigned — so neither is
ported.

**Mutation pass: 24 mutants, 22 killed, 2 DECLARED EQUIVALENT WITH PROOF.**

Four of the six first-round survivors were real gaps, and all four were tests
that passed for the wrong reason — the recurring failure mode, caught again:
- 'clears imagesLoaded' asserted false against a cache that was ALREADY false
  from the reset. Now sets it true first.
- 'success but no url' could not tell the two clauses apart, because the stub
  omitted the url whenever it reported failure. Now tests both directions.
- 'empty queue is complete' passed on the PREVIOUS call's flag. Now runs the
  empty case first.
- 'does not search the slug' used a fixture with no slug in it.

The remaining two are the pause flag's `while`-head test and its post-await
`break`. The vanilla has both (2588, 2597-2600) and they are redundant:
control reaches the break and then the loop head, which re-tests the same flag,
and the flag is set true immediately before the workers start, so the loop
cannot be entered with it false. Deleting EITHER changes nothing. That was not
left as an assertion — deleting BOTH was run, and is killed. Kept in the code so
a reader diffing against the vanilla finds the same shape.

Full suite 291 files / 6352 tests. Build clean. Lint clean.

**Beatport remaining:** the genre browser MODAL (2243-2340, 2643-2681) and the
genre detail page (2683-3646).

### REVIEW OF P10a — the vanilla has TWO empty states, and I had collapsed them

Caught reviewing the committed loader against 2369-2377 rather than against my
own notes. `loadBeatportGenreList` returned a bare array, which cannot express
the difference between:

- **the API sent nothing** — '⚠️ No genres available' with a RETRY button, no
  toast, and NOTHING cached, so the next open tries again (the vanilla `return`s
  at 2376, before the caching at 2424); and
- **the filter removed everything** — an empty grid, a 'Loaded 0 genres for
  browsing' toast, and an empty list cached.

One says "try again", the other says "nothing to show". Collapsing them drops
the Retry button from the only case that can be retried. The loader now returns
`{ genres, rawCount }` and caches only when `rawCount > 0`.

### BEATPORT P10b — the genre browser modal (-ui/genre-browser-modal.tsx)

The shell around P10a's loader: open/close, the search box, and the four grid
states. Transcribed the vanilla's own classes rather than adopting the app's
DialogFrame, because `.genre-browser-modal-overlay` is `display:none` until
`.active` (style.css 33146-33163) and the whole genre stylesheet hangs off those
names — so the port renders the overlay WITH `active`, and a test asserts it.

Hand-rolled Escape / backdrop / scroll-lock, matching this port's existing
overlays (server-search-overlay, server-playlist-list). The backdrop compares
`event.target` to the overlay itself (2264-2268), so a click inside the modal
body cannot dismiss it.

**Mutation pass: 25 mutants, 23 killed, 1 removed as unproven code, 1 declared
equivalent with proof.**

- SURVIVED and REMOVED: a mounted-ref guard on the image callback. I added it
  during this slice to chase React act() warnings; it did not fix them (the
  count stayed at 6), React 18 no-ops setState on an unmounted component
  anyway, and no test could distinguish it. Deleting speculative code beats
  keeping it behind a comment claiming it matters.
- SURVIVED and DECLARED EQUIVALENT: 2491's `!imageLoadingActive` re-entry
  guard. The only ways back into the effect are `open` changing (and closing
  pauses) or `reloadToken` changing — and the retry that bumps that token only
  exists in the empty and failed states, where no image run can be in flight.
  Kept because it is the vanilla's guard and it is what would stop a future
  second caller doubling the worker count. Reasoning recorded on the line.
- SURVIVED and FIXED: nothing tested closing the modal WHILE pictures were
  still arriving. That is the behaviour the whole pause design exists for —
  without it two workers keep scraping Beatport for a modal nobody is looking
  at, one request every 100ms. Now tested, including that the run is left
  PAUSED rather than marked complete so the next open resumes.

**The act() warnings were a real signal, and the fix was test-side.** Six of
them, all from shell tests whose genre load resolved after their last
assertion. Those tests are about open/close, so they now hold the request open
forever; the image tests wait for the workers to drain. Warnings: 0.

Full suite 292 files / 6373 tests. Build clean. Lint clean.

**Beatport remaining:** the genre detail page (2683-3646) — the last region.

## THOROUGH VERIFICATION SWEEP (Boulder-prompted) — one real gap, one wrong
## claim of mine, and a self-inflicted hazard

### 1. COVERAGE ENUMERATION — every function in beatport-ui.js, classified

113 top-level functions, counted mechanically rather than estimated:

| region | lines | count | status |
|---|---|---|---|
| the five sliders | 14-1603 | 57 | ported or dissolved by React |
| the three top-10 lists | 1608-1855 | 8 | ported (P9) |
| the download bridge | 1858-2228 | 8 | ported (P7) |
| genre browser modal | 2243-2681 | 11 | ported (P10a/b) |
| **genre detail page** | **2683-3646** | **21** | **NOT YET PORTED** |
| Settings tenant | 3650+ | 8 | out of scope (documented boundary) |

"Dissolved by React" means the function exists only to manage imperative DOM or
listeners: setup*Navigation / setup*Indicators / goTo* / start*AutoPlay /
reset*AutoPlay / setup*HoverPause / cleanup* / populate* / create*Slides, plus
displayCachedGenres, restoreCachedImages, addGenreBrowserCardClickListeners and
isGenreBrowserModalOpen. Each was checked to have its BEHAVIOUR present in the
port, not merely to look dissolvable.

### 2. A CLAIM FROM MY OWN P0 READ WAS WRONG, AND IT COST A REAL BEHAVIOUR

The read said: "totalSlides starts at 4 … the 4 is the count of the static
placeholders, which is why the failure path works", and "the markup carries real
placeholder slides".

**index.html contains ZERO `beatport-rebuild-slide` elements.** Counted. The `4`
is a dead initial value that `populateBeatportSlider` overwrites and nothing
reads on the failure path. What the hero's failure arm actually leaves on screen
is the `beatport-rebuild-loading` block — "🎯 Loading Fresh Beatport Tracks…" —
permanently, with dead arrows.

That mattered, because the port had been written to the wrong claim:
BeatportSection rendered NOTHING while loading and NOTHING on failure for the
three sections whose failure arm replaces nothing. In the vanilla that is
invisible, because the placeholder is PAGE MARKUP. **The flip deletes that
markup.** So the port as committed would have shipped:

- all five sections as blank strips while Beatport is being scraped, and
- hero, charts and DJ blank FOREVER on a failed load.

Fixed: `loadingTitle` / `loadingSubtitle` join BEATPORT_SLIDERS (the exact copy
from index.html 2823, 2947, 2985, 3022, 3058) and BeatportSection renders the
block while loading and on the two non-error-block failure arms. The identical
reasoning had already been applied to the three top-10 lists in P9 — it simply
had not been carried back to the sliders.

Six new mutants cover it; sections suite is now 35/35.

### 3. A HAZARD I CREATED, AND THE FIX

I ran the mutation suites in the BACKGROUND while editing the same files. The
scripts snapshot every target file at start and rewrite it after each mutant, so
they silently reverted an edit in progress — and when the run was killed, they
left a MUTANT in the working tree (`-beatport.loaders.ts` carrying the wrong
error string, and later `-beatport.sections.tsx` with a swapped cache key). Both
were caught by `git status` / `git diff` and reverted.

Two fixes, both applied:
- the scripts now trap SIGTERM/SIGINT/SIGHUP and restore — `atexit` does NOT run
  on a signal, which is why the mutant survived the kill;
- mutation suites run in the FOREGROUND only, never concurrently with editing.

Also re-anchored three mutants in the downloads suite that had gone stale: P9's
openBeatportTop10List introduced a second copy of the latch-and-schedule shape,
so anchors that were unique when written began matching twice. An anchor that
matches twice is reported as a survivor, never silently skipped — which is how
they were found.

### 4. RE-VERIFICATION AGAINST THE COMMITTED CODE

All five mutation suites re-run in the foreground:

| suite | result |
|---|---|
| downloads | 32/32 killed |
| sections (+6 new) | 35/35 killed |
| top-10 | 28/28 killed |
| genres | 24/26 — 2 declared equivalent, proven by deleting BOTH |
| genre modal | 23/24 — 1 declared equivalent, reasoning on the line |

Full suite run FOUR times. Three green at 292 files / 6373 tests; one run had a
single failure whose identity I did not capture before it cleared. I am not
claiming it was a flake — I am recording that it happened once in four and was
not reproduced in the three runs since. Lint clean, build clean, working tree
free of stray mutants.

### BEATPORT P10c-i — the genre page shell + its hero slider

`-ui/genre-page.tsx`, covering 2683-2809 (the shell) and 2811-3118 (the hero).

**The genre hero is the main hero's twin** — same `beatport-rebuild-*` classes,
one release per slide, 5000ms — so it is BeatportSlider with the hero config and
its own ids. updateGenreHeroSlide, startGenreHeroSliderAutoPlay and
addGenreHeroReleaseClickHandlers all dissolve, and with them
`window.genreHeroSliderState`, which the P0 read flagged as the file's only
window-scoped state.

**Where it is NOT the twin — three things, each tested:**
- the artist comes from `artists_string`, not `artist`;
- the third line is the LABEL, falling back to '<Genre> Hero Release', where the
  main hero's is the fixed caption 'New on Beatport';
- **the urls are RELATIVE.** This endpoint alone returns paths (2869-2871), and
  the url is POSTed to /api/beatport/release-metadata — so an un-absolutised one
  would reach the scraper with no host. `absoluteBeatportUrl` is pure and
  directly tested.

**Two more single-endpoint quirks, both caught by writing the test first:**
- it reports failure in `data.message`, NOT the `data.error` the other nine use
  (2835). Reading the usual field would show the generic fallback for every
  backend-reported failure.
- it checks `response.ok` and puts the status line in front of the user
  (2828-2830) — one of only two Beatport endpoints that does. My first pass
  missed this; the test failed and the check went into the api layer where the
  genres endpoint's already lives.

**NOT transcribed, deliberately:** showGenrePageView stops the MAIN hero's
autoplay on the way in (2687-2690) and showGenreListView restarts it
(2791-2796). Both exist because the two sliders drove overlapping global state
through shared functions — "to prevent conflicts". In the port they are two
component instances with their own intervals and nothing shared, so there is no
conflict to prevent; and the modal covers the page either way.

**The genre Top 100 bug cannot recur.** The vanilla built the button block once
and reused it for every genre, so its listener had to read the genre off the
dataset — closing over the arguments pinned it to whichever genre was opened
first and downloaded THAT genre's chart (fixed in the vanilla, 93eaa90ac).
React remounts per genre, so the closure IS the current genre by construction.

**Mutation pass: 23 mutants, 23 killed** after one round. All three first-round
survivors were real gaps, and one was interesting: the click payload's `artist`
and `label` fields are not observable through the download flow at all —
openBeatportRelease reads only url, title and image_url. They are pinned by
testing the pure builder directly rather than through a rendered click, which is
also what the export-coverage gate wants.

**The full suite caught `absoluteBeatportUrl` exported with no test naming it** —
the same gate that caught openBeatportTop10List in P9. Scoped runs cannot see it.

Full suite 6397 tests. Build clean. Lint clean.

**Beatport remaining:** the genre top-10 lists + their chart handlers
(3123-3405), the genre Top 100 (3406-3443), and the genre top-10 releases
(3444-3646).

### P10c-i REVIEW — the hero's error block is DEAD UI in the vanilla

Checked the three genre-page loaders against each other rather than each on its
own, and they do not agree:

| loader | on failure |
|---|---|
| loadGenreTop10Lists (3151) | renders an error block, swallows |
| loadGenreTop10Releases (3464) | renders an error block, swallows |
| **loadGenreHeroSlider (2852-2862)** | renders an error block **AND RETHROWS** |

handleGenreBrowserCardClick runs all three in a `Promise.all` (2668-2672), so
the hero's throw rejects the lot, toasts, and calls showGenreListView() — the
user is bounced back to the genre grid and **never sees the block the hero just
rendered**. Its Retry button is unreachable, and would be broken anyway: it is
an inline `onclick` with the genre name string-interpolated into it, so any
genre containing an apostrophe would produce invalid JS.

The port keeps the user on the genre page and makes the block real, with a
working Retry — losing the whole page because one of three sections failed,
while the other two would have loaded fine, is not worth reproducing. DECLARED
in the component and covered by a test that asserts the page stays put.

This is the sort of thing only a cross-loader comparison finds: each of the
three reads perfectly sensibly in isolation.

### BEATPORT P10c-ii — the genre top-10 lists + the genre Top 100 button

Covers 3123-3296 (the lists), 3301-3353 (their chart clicks), 3358-3401 (the
DOM scrape) and 3406-3439 (the Top 100 button).

**Most of this was already ported.** handleGenreChartClick (3327-3353) is
byte-for-byte handleRebuildChartClick from sync-services.js — same latch, same
`Fetching track metadata... (0/n)` overlay, same enrich, same compilation modal
— so P9's openBeatportTop10List covers it unchanged, and getGenrePageTrackData
dissolves with the DOM scrape it exists to perform. The two lists themselves are
the homepage's, so TrackTop10List became reusable rather than restated: the
genre page overrides exactly three things — element id, subtitle, chart name.

**What is genuinely different, and tested:**
- **`has_hype_section`.** The homepage always renders both columns; the genre
  page removes the hype column OUTRIGHT when the backend says there is none
  (3219, and the explicit "No else block" comment at 3259), and collapses the
  grid to one centred track with an inline style (3179). The port requires BOTH
  the flag and a non-empty list, as 3219 does.
- the copy lower-cases the genre in three places (3176, 3184, 3225) while the
  section heading keeps its casing (3175);
- the chart names carry the genre: '<Genre> Beatport Top 10', '<Genre> Hype
  Top 10', '<Genre> Top 100';
- unlike the hero, this loader SWALLOWS its failure — the two sections fail
  independently, which is now asserted directly.

**FOUND: four classes the genre page emits have no CSS at all.** Checked by
plain substring, not just the anchored pattern:
`genre-top10-lists-container`, `genre-top10-loading-container`,
`genre-top10-error` and `error-detail` appear NOWHERE in style.css. So the
genre page's top-10 wrapper, its loading block and its error block are unstyled
today; the lists look right only because their CONTENT uses the
`beatport-top10-*` classes, which do exist.

`error-detail` is the near-miss: the HERO's error block uses
`genre-error-details` (plural, styled at 33560) while the top-10 one uses
`error-detail`. Almost certainly a typo that lost the styling. Both are
transcribed as-is and named in the artefact test — inventing CSS here would
redesign a page nobody asked me to touch.

**Mutation pass: 36 mutants, 36 killed** after one round. Three of the four
first-round survivors were real gaps (an empty beatport list is NOT a failure;
the Top 100 latch was untested; the enrichment stub echoed its input again) and
one was a stale anchor — the Top 100 button's markup had changed when I wired
its onClick.

**The full suite caught THREE exports with no test naming them** —
openBeatportGenreTop100, loadGenreTop10Lists and TrackTop10List, all exercised
through the page and none named. Given direct tests. That gate has now caught
something in P9, P10c-i and P10c-ii; a scoped run cannot see it.

Full suite 6418 tests. Build clean. Lint clean.

**Beatport remaining:** the genre top-10 releases (3444-3646) — the last region.

### BEATPORT P10c-iii — the genre top-10 releases. THE FILE IS DONE.

Covers 3444-3641, the last region of beatport-ui.js.

The cards are the homepage's, so ReleaseTop10Card became exported and reused
rather than restated — the genre page changes only the list id and the header.

**Details worth keeping, each tested:**
- the success header names the genre ('💿 Top 10 <Genre> Releases', 3481) and
  the ERROR header does NOT ('💿 Top 10 Releases', 3628) — with a different
  subtitle too;
- an empty list renders NOTHING and leaves the placeholder (3475), so
  loading and loaded-but-empty look identical, exactly as they do today;
- every card is bound with no url test (3549), so an url-less release reaches
  the handler and gets its toast;
- this loader SWALLOWS, like the top-10 lists and unlike the hero.

**ONE DECLARED FIX — the item the P0 read flagged and left open.**
handleGenreReleaseCardClick (3558-3617) is a byte-for-byte copy of
handleBeatportReleaseCardClick with ONE line missing: it never calls
registerBeatportDownload. So a release started from a genre page downloads with
no progress bubble — nothing on screen says anything is happening, though the
files do arrive.

Decided rather than left open: the function's own comment says "exact parity
with main page" (3556), the copy is otherwise identical line for line, and
restoring the call is purely additive. The port uses the SAME
openBeatportRelease as every other release card. Reversing it is one argument if
Boulder disagrees.

**Mutation pass: 43 mutants, 43 killed.** Three first-round survivors: a bad
mutant of mine (a no-op field spread), a stale anchor after formatting, and a
REAL test defect — 'keeps its placeholder for an empty list' asserted the
placeholder while it was still the pre-load state, so it passed on the first
tick before the response arrived. It now waits for a sibling section to settle
first.

**The export gate caught two more** (loadGenreTop10Releases, ReleaseTop10Card) —
four consecutive slices now.

Full suite 6428 tests. Build clean. Lint clean.

## BEATPORT IS COMPLETE — all 113 functions accounted for

| region | lines | status |
|---|---|---|
| five sliders | 14-1603 | ported / dissolved |
| three top-10 lists | 1608-1855 | ported |
| download bridge | 1858-2228 | ported |
| genre browser modal | 2243-2681 | ported |
| genre detail page | 2683-3646 | ported |
| Settings tenant | 3650+ | out of scope (documented boundary) |

Three live vanilla bugs were found by reading and fixed in the vanilla itself
(three frozen sliders, the genre Top 100 downloading the wrong genre); a fourth
(the missing genre download bubble) is fixed in the port and declared above.

**Sync-port remaining after Beatport:** the auto-sync schedule board, the 15-tab
page shell + sidebar, then the flip, the vanilla severs and the full
original-vs-port review.

## AUTO-SYNC SCHEDULE BOARD — P0 READ COMPLETE (auto-sync.js 436-2525)

The earlier entry read 35-470 verbatim and only INVENTORY-MAPPED 471-2525. This
closes that: 436-2525 is now read line by line. The pure core (35-470) is
already ported — `-sync.autosync.ts`, 30 exports, done in Sync P1c — so the
work ahead is the state builder, the four panels and the interaction layer.

### TENANCY — the file has THREE tenants, and only two are ours

1. **The schedule board** (436-2356) — this wave.
2. **The mirrored-pipeline tail** (2358-2525: parseMirroredPipelineResponse,
   editMirroredCustomName, editMirroredSourceRef, applyMirroredPipelineState,
   runMirroredPlaylistPipeline, pollMirroredPipelineStatus). **NOT ours to
   port** — stats-automations.js calls all five from the mirrored CARDS and the
   mirrored modal (604-606, 1150-1151), and the React mirrored tab already
   ports the behaviour. The board only CONSUMES one of them
   (runAutoSyncScheduledPlaylist → runMirroredPlaylistPipeline).
3. **Two shared-helpers.js seams the board calls through `typeof` guards**:
   `playlistQualityProfileSelectHtml` (1150) and
   `hydratePlaylistQualityProfileSelects` (1381). Both survive the flip; both
   need a seam-test row before the board can rely on them.

### THE STATE BUILDER — buildAutoSyncScheduleState (471-569), pure, P1 gold

Two passes over automations. The FIRST (playlist_pipeline) pushes anything it
cannot bucket onto `automationPipelines` — the read-only panel. The SECOND
(personalized_pipeline) does NOT: an unbucketable personalized automation is
silently dropped. **That asymmetry is real and easy to "tidy" away.**

**The deliberate `|| {}` asymmetry (496-501, commented in the vanilla).**
`trigger_type === 'schedule'` reads `auto.trigger_config || {}`;
`weekly_time` passes `auto.trigger_config` RAW. A null/non-object config on a
weekly row must fall through to `automationPipelines` as a broken row rather
than be silently bucketed as an every-day schedule — because
autoSyncWeeklyFromTrigger's defensive defaults would otherwise turn garbage
into "all 7 days". Transcribe exactly.

`enabled: auto.enabled !== false && auto.enabled !== 0` — tri-state, not truthy.

### FIVE FETCHES, THREE REQUIRED (602-650)

`/api/mirrored-playlists`, `/api/automations`,
`/api/playlist-pipeline/history?limit=<state>` are required and each checked
`!res.ok || data.error`. `/api/personalized/kinds` and
`/api/personalized/playlists` are BEST-EFFORT — `.catch(() => null)` plus their
own try/catch, so a kinds failure never breaks the board. ORDER MATTERS:
enrich first (tags variants, drops orphaned mirrors), THEN append the
synthetic not-yet-generated rows built against the enriched list.

### FINDINGS THE PORT MUST NOT LOSE

- **Scroll preservation across the full re-render (656-660, 726-729).** Reads
  `.auto-sync-tab-panel.active .auto-sync-lanes` scrollTop before rebuilding
  and restores it after — targeting the ACTIVE tab so it works on both boards.
  Without it, dropping a playlist snaps the board to the top. React's
  reconciliation removes the need only if the lanes element is not remounted.
- **Custom-interval lanes (795-802).** Buckets are merged with any in-use hours
  that are NOT in AUTO_SYNC_BUCKETS, so a 6h or 36h schedule made on the
  Automations page still gets its own lane instead of vanishing from the board.
- **`renderAutoSyncWeeklyPanel(playlists, playlistSchedules)` is NOT a bug.**
  It reads `weeklySchedules` from module state (862) and uses the passed HOURLY
  map only to mark a card `scheduled-elsewhere` and label it 'Hourly (…)'.
- **Multi-day weekly cards render under EVERY matching day** (922-928), built
  by iterating the schedules once rather than scanning per day.
- **Only the hourly sidebar has a Bulk button.** The weekly sidebar omits it.
- **One-schedule-per-playlist is enforced in BOTH directions, by delete-then-
  create** (2059-2067 and 2251-2263), each best-effort. The vanilla's own
  comment accepts the failure mode: a failed POST leaves the playlist
  unscheduled, which is recoverable.
- **Health dot (1981-1998):** last 3 runs for that playlist; ≥3 errored →
  'failing', ≥1 → 'warning'. Counts `skipped` as an error.
- **Polling (2334-2350):** 3s interval, started only when some playlist is
  `running`, and **skipped entirely while a drag is in progress**
  (`_autoSyncIsDragging`) so a refresh cannot yank the card out from under the
  pointer.
- **Run now is polymorphic (2308-2332):** a synthetic personalized row has no
  mirrored pipeline, so it POSTs `/api/automations/<id>/run` instead — and
  toasts 'Schedule it first, then Run now.' when unscheduled.
- **History is DOM-built, not innerHTML** (1394-1572) with a per-entry
  try/catch that swaps in an error card, plus a whole-list fallback when the
  renderer produced zero cards from a non-empty list. The filter is applied at
  populate time, and 'error' includes `skipped` while 'completed' includes
  `finished`.
- `loadMoreAutoSyncHistory` raises the limit by 50, capped at 500, and REFETCHES.
- The bulk menu is a transient body-appended popover positioned off the anchor
  rect, closed by a `{ once: true }` outside-click listener registered in a
  `setTimeout(0)` so the opening click does not immediately close it.
- **`promptAutoSyncBulkCustom` uses `window.prompt`** (1305) — forbidden by this
  repo's rules. The port must use the SoulSync confirm/prompt modal.

### PORT SHAPE

The dossier's earlier note holds: this is the most React-shaped vanilla in the
family — controlled editor draft state, pure helpers, DOM-built lists. The
weekly editor is already a controlled component in all but name (draft object,
discard on outside click, save/cancel).

Suggested slices: A) state builder + api layer (pure, differential-testable);
B) the hourly board; C) the weekly board + editor; D) monitor + automations +
history panels; E) wiring, polling and the two shared-helpers seams.

**Read status: COMPLETE.** No code written.

### AUTO-SYNC P0 ADDENDUM — the live-bug pass the entry above was missing

Boulder pulled me up on this: headline outcome #3 of this document makes
"LIVE BUGS for day-one knowing-fixes" a P0 deliverable, and my first write-up
skipped it. Done properly now.

**LIVE BUG 1 — bulk scheduling breaks the one-schedule-per-playlist invariant.**
`saveAutoSyncPlaylistSchedule` (2059-2067) deletes an existing WEEKLY automation
before installing an hourly one, and `saveAutoSyncWeeklySchedule` (2251-2263)
does the mirror-image delete. Both carry comments explaining that the UI assumes
one schedule per playlist. **`saveAutoSyncPlaylistScheduleSilent` (1368-1392) —
the function the BULK path calls — has no such delete.** So:

> Sidebar → a source's `Bulk` → any interval, on a source containing a playlist
> that already has a WEEKLY schedule, leaves BOTH automations live.

The playlist then runs on two schedules, appears as scheduled on both boards,
and unscheduling from one board leaves the other running. Non-obvious to a user
because each board looks correct in isolation. Fix is one block copied from
2059-2067 into the silent path.

**LIVE BUG 2 — "Unschedule all" only unschedules half.**
`bulkUnscheduleAutoSyncSource` (1343) selects its targets with
`playlistSchedules[p.id]` alone, so weekly schedules are untouched by a menu
item labelled "Unschedule all" (1280) and a confirm that says "Removes the
Auto-Sync schedules" (1350). Same root cause as bug 1: the bulk path predates
the weekly board and was never extended.

**REPO-RULE VIOLATION — `window.prompt` at 1305.** `promptAutoSyncBulkCustom`
uses the native prompt for the custom-interval entry. This repo forbids
window.confirm/alert/prompt; the port must use the SoulSync modal. (Note the
neighbouring bulk confirms already use `showConfirmDialog` correctly — only the
prompt is native.)

**UNVERIFIED, flagged for P1 rather than asserted — the health dot's ordering
assumption.** `autoSyncPlaylistHealth` (1986-1988) takes `.slice(0, 3)` of the
playlist's history and calls it "the last 3 runs". That is only true if
`/api/playlist-pipeline/history` returns newest-first. I traced the endpoint to
web_server.py 37961 but did not find the ORDER BY for the pipeline-history table
(the `ORDER BY started_at DESC` I found is `sync_history`, a different table).
**If the pipeline history is oldest-first, every health dot is reporting on the
three OLDEST runs.** P1 must confirm the query before this is ported as-is —
and the React version should sort defensively regardless, since the cost is one
comparator.

**Escaping — clean, with one theoretical hole.** The board uses `_esc`/`_escAttr`
throughout and interpolates only numeric ids into inline handlers. The one
exception: `openAutoSyncBulkMenu` and its buttons interpolate `_escAttr(source)`
inside a SINGLE-QUOTED JS string in an `onclick` (774, 1267, 1275, 1279).
`_escAttr` escapes HTML entities, not JS quotes, so a source key containing an
apostrophe would break out. Not reachable today — `source` is a fixed enum from
the mirrored-playlist rows — and it disappears entirely in React, where handlers
are functions rather than strings. Recorded so nobody "fixes" it into the port.

**Port notes.** No download-engine coupling: the board talks only to
`/api/automations*`, `/api/mirrored-playlists*`, `/api/playlist-pipeline/history`
and `/api/personalized/*`. `_autoSyncScheduleState` and the five UI variables are
script-scoped and read by NOTHING outside this file, so they become React state
cleanly. The two `typeof`-guarded shared-helpers seams
(`playlistQualityProfileSelectHtml`, `hydratePlaylistQualityProfileSelects`)
must gain a vanilla-seams.test.ts row before the board relies on them, because
both are optional-chained today and would fail silently.

### AUTO-SYNC SLICE A — the state builder + the board's api layer

`buildAutoSyncScheduleState` joins the pure core in `-sync.autosync.ts`, and the
nine endpoints the board needs join `-sync.api.ts`. No UI yet.

**The two asymmetries from the P0 read are transcribed and pinned by tests:**

1. The `schedule` arm coerces `trigger_config || {}`; the `weekly_time` arm
   passes it RAW. The test proves WHY, not just that: it asserts the null
   config lands in the read-only panel, and then asserts
   `autoSyncWeeklyFromTrigger({})` returns all seven days — so a reader can see
   what the coercion would have silently produced.
2. The playlist_pipeline pass panels what it cannot bucket; the personalized
   pass DROPS it. Three separate tests, because the drop has three different
   exits — unresolvable row id, unparseable trigger, and a trigger type that is
   neither schedule nor weekly.

`enabled` is tri-state (`!== false && !== 0`), not truthiness — four assertions.

**Mutation pass: 12 mutants, 12 killed** after two rounds. Both first-round
survivors were fixtures that returned early and never reached the code under
test: one stopped at the row-id lookup, the other only used the two trigger
types the mutant's `else` cannot see. Two earlier test failures were also my
fixtures rather than the port — ownership has THREE signals (flag, legacy group,
`Auto-Sync:` name prefix) so clearing only the flag left the row owned, and
`autoSyncPersonalizedEntry` reads `action_config.kinds` as a one-element ARRAY.
Both now have tests of their own.

**The export gate caught all nine api additions** — five consecutive slices now.

Full suite 6448 tests. Build clean. Lint clean.

**Next:** slice B, the hourly board (lanes, sidebar groups, drag-drop,
custom-interval lanes, scroll preservation).

### AUTO-SYNC SLICE B — the hourly board

`renderAutoSyncSchedulePanel` (741-859) and everything it reaches: the source
icon (197-203), the sidebar kind-groups (436-457), the scheduled card
(1951-1976), the health dot (1978-1996), the next-run label (1999-2011), the
organize toggle (1920-1931) and the five drag handlers (2013-2049). Pure lane
model in `-sync.autosync.ts`, markup in `-ui/autosync-board.tsx`.

**Three vanilla globals dissolve rather than port.** `_autoSyncSidebarFilter`
exists only because the vanilla re-renders the whole panel through `innerHTML`
on every keystroke and then has to re-focus the input and restore the caret
(1080-1102); React keeps the input mounted, so the global, the re-render and
the caret dance all go. `_autoSyncExpandedKinds` becomes board state.
`_autoSyncIsDragging` is set by three handlers and read by the poller — it
belongs to slice E's wiring, not to the board.

**What reading the vanilla actually bought this slice.** `autoSyncBuildLanes`
merges custom intervals that are in use into the standard buckets. Without it a
6h schedule made on the Automations page has no lane and vanishes from the
board — the vanilla has a comment saying exactly that at 793-796, and the port
would have dropped it silently had the model been written from the bucket list
alone.

**Three tests that proved nothing, all caught by mutation.**

1. `autoSyncGroupBySource` sorts by DISPLAY LABEL, not the raw source key. With
   today's twelve labels the two orderings coincide for every pair — every
   label is essentially its key capitalised — so a test using real sources
   passes just as happily against a key sort. The labeller is now injectable
   and the test supplies one that genuinely reorders. The day a label stops
   matching its key the difference becomes user-visible.
2. The lane de-dupe needs TWO playlists sharing one custom interval, not one.
   With a single 6h schedule the `new Set` is unreachable.
3. `autoSyncMatchesFilter` searches the label, so the fixture has to use a
   source whose label differs from its key — `file` → `File Imports`, matched
   on "imports".

**A jsdom trap worth writing down.** jsdom implements no `DragEvent`, so
`fireEvent.dragLeave(el, { relatedTarget })` silently DROPS the property. Both
drag-leave tests were passing while asserting nothing about the child-guard at
2032 — the guard that stops the highlight flickering when the cursor moves onto
a card inside the lane. The fix is a real `MouseEvent`, which does carry
`relatedTarget`, handed to `fireEvent` so the state update flushes inside
`act()`. Dispatching it directly leaves the assertion reading pre-update DOM,
which is a second silent pass.

**Health dot ordering — RESOLVED, the vanilla is correct.** The P0 read flagged
that `.slice(0, 3)` calls the FIRST three rows "the last 3 runs" and could not
find the ORDER BY. It is `ORDER BY id DESC` at music_database.py 17820 — the
`ORDER BY started_at DESC` found during the P0 read belongs to `sync_history`,
a different table. The window really is the most recent three runs; no
defensive sort is needed, and the P0 addendum's fourth open item is closed.

**Mutation pass: 43 mutants, 43 killed** after one round of three survivors —
the health window (fixtures never exceeded three rows), the `typeof` guard on
the quality-profile seam (no fixture supplied a non-callable value), and the
drag-start payload (no test ever dragged a card FROM a lane). All three now
have tests; a fourth mutant was added for the sidebar card's drag-start, which
the first pass had not covered at all. The lane-model pass went 24/24.

**The export gate caught the three card helpers** — six consecutive slices.
They were exercised only through the component, which the gate correctly
refuses to count.

**Artefact check:** all 53 classes the board emits resolve in `static/style.css`.

Full suite 6510 tests. Build clean. Lint clean.

**Next:** slice C, the weekly board + the controlled editor popover.

### AUTO-SYNC SLICE C — the weekly board + the editor, on shared chrome

`renderAutoSyncWeeklyPanel` (861-977), `autoSyncWeeklyCardHtml` (979-1024),
`renderAutoSyncWeeklyEditor` (1026-1077) and the drop/editor handlers
(2145-2232). Seven day lanes, Mon–Sun, with a click-to-edit popover.

**The two boards now share their chrome instead of duplicating it.** The
vanilla duplicates the sidebar, the lane and the card across both panels, and
says so at 980-987: "Mirror the hourly board's autoSyncScheduledCardHtml shape
so the two boards stay visually consistent", followed by an explicit list of
the only four things that differ — the timing line, the click, the drag
functions, the unschedule helper. Those four became props;
`-ui/autosync-shared.tsx` holds the rest. Two copies that a comment BEGS to
stay in sync are two copies that will eventually drift.

That refactor moved the hourly board's code without changing its behaviour,
which its existing tests confirmed by continuing to pass untouched — the point
of testing rendered DOM rather than internals.

**One declared divergence, and it is a bug fix.** The hourly board's dragleave
guards on `col.contains(event.relatedTarget)` (2030-2035). The weekly board's
does NOT (2138-2142). Without the guard, moving the cursor from a lane onto a
card inside that lane fires dragleave and the drop highlight flickers off
mid-drag. The shared lane carries the guard, so the weekly board gains the
hourly board's behaviour. This is an asymmetry with no design behind it — the
vanilla's own hourly board is the evidence for what was intended.

**A SECOND declared divergence, found in the post-slice verification pass —
and it is live bug #5.** The vanilla's editor Save leaves the popover on
screen. `saveAutoSyncWeeklyFromEditor` (2218-2227) awaits the save, which ends
in `await refreshAutoSyncScheduleModal()` (2284) — a full re-render taken while
`_autoSyncWeeklyEditor` is STILL set, so the editor is rendered back into the
DOM — and only then nulls the flag, with no further render. Nothing re-renders
afterwards: the status poller (2346) only runs while a pipeline is running, so
in the ordinary case the popover sits there until the user dismisses it by
hand. The sibling exit `unscheduleAutoSyncWeeklyFromEditor` (2229-2234) nulls
FIRST and is correct. The two exits were written in opposite orders and only
one of them works — which is what made it findable: the asymmetry was the tell,
not the symptom. The port closes on save.

I had already built it correctly and had a passing test and a killed mutant for
it ("leave the editor open after a successful save"), but had NOT written the
divergence down. Building the right thing by accident is not the same as
declaring it, so this is recorded rather than quietly enjoyed.

**One piece of vanilla defensiveness intentionally dropped.** `autoSyncWeeklyDrop`
re-checks `AUTO_SYNC_WEEKDAYS.includes(day)` (2152). In the port `day` comes
from mapping `AUTO_SYNC_WEEKDAYS` itself, so the check has nothing to guard.

**Two vanilla workarounds that dissolve.** The editor's day toggle re-renders
the whole modal (2200) while its time and tz inputs deliberately do not
(2205-2215) — an asymmetry that exists purely because an innerHTML re-render
would eat the caret mid-typing. React re-renders all three without losing
focus, so the draft is plain state and the distinction disappears. Same story
as slice B's `_autoSyncSidebarFilter`.

**Three states in the weekly sidebar, not two.** A playlist scheduled on the
HOURLY board renders `scheduled-elsewhere` with an "Hourly (every 8 hours)"
label (878-887), so it reads as spoken-for without looking like it runs weekly.

**Mutation pass: 84 mutants, 84 killed.** One anchor missed on the first run
because the refactor had moved it from the board into the shared module; it was
RE-ANCHORED and re-run rather than counted as passing.

**My test fixtures were wrong before the port was.** I wrote the weekday
fixtures as `'monday'`; the real keys are `'mon'`, and the label is `Mon @
09:00`, not prose. Sixteen tests failed loudly and immediately — which is the
behaviour you want from a fixture that guessed.

**The export gate caught a gratuitous export** — `AutoSyncOrganizeRow` was
exported but used only by the card in its own module. Un-exported rather than
given a ceremonial test. Seven consecutive slices.

**Artefact check found one unstyled class: `auto-sync-weekly-lanes`.** It
appears once in auto-sync.js and ZERO times in style.css, so it is a dead hook
in the vanilla too, not a port regression. Carried faithfully. The other 70
classes resolve.

**Health dot ordering is now resolved** — see the correction in slice B above.

Full suite 6558 tests. Build clean. Lint clean.

**Next:** slice D, the monitor / automations / history panels.

### AUTO-SYNC SLICE D-i — the live monitor + the read-only Automations panel

`getAutoSyncPipelinePlaylists` / the two status maps / `renderAutoSyncPipelineMonitor`
/ `autoSyncPipelineMonitorCardHtml` (1104-1183), and
`renderAutoSyncAutomationPanel` / `autoSyncAutomationCardHtml` (1185-1198,
1883-1918). Slice D was split: the history panel is 490 lines across 27
functions and becomes D-ii.

**Two caps that compose, and the composition is the point.** The monitor takes
ALL running rows, then at most 2 finished ones, then caps the whole list at 4
(1133-1135). With five pipelines running you see four running rows and no
recent ones — live work crowds out history. Both caps have their own test,
plus one for the interaction, because a single test of either would pass
against the wrong composition order.

**A redundancy worth keeping, found by mutation.** `getAutoSyncPipelinePlaylists`
sorts running-first (1108-1110), and the monitor then re-partitions
running-first anyway when it builds `[...running, ...recent]`. Deleting the
sort's rule is therefore INVISIBLE from the panel — the mutant survived. It is
now pinned by a direct test of the selector, because the rule is part of that
function's contract and any other consumer would silently lose it.

**A load-order dependency the port does not inherit.** auto-sync.js calls
`_autoFormatTrigger` (stats-automations.js 4154) UNGUARDED at 1890, while
listing `_autoParseUTC` from the same file as a cross-file global in its own
header comment. That call is safe today only because index.html 8398-8399
loads stats-automations.js immediately before auto-sync.js. The port
reimplements the formatter and consults the global for exactly one branch —
`_findBlockDef(type)?.label`, which reads block definitions that file fetches
at runtime — and only when the type is unmapped, since a mapped label always
wins (4179). So an exotic trigger keeps its configured label while that file is
loaded, and degrades to the humanized identifier instead of throwing if it
ever is not.

**My fixture was wrong before the port was, again.** The automation fixture
omitted `action_type: 'playlist_pipeline'`, which
`autoSyncPlaylistIdFromAutomation` requires before it will resolve an id at
all. The test failed loudly; the coupling now has a test of its own.

**A type that was too narrow.** `MirroredRow.pipeline_state` was declared as
`{ status?: string }` back in slice B, which was all the card needed. The
monitor reads phase, progress, timestamps and logs off the same object, so it
is now the full `PipelineState`. Caught by the type-checker on the test
fixtures, not by a runtime failure.

**Mutation pass: 42 mutants, 42 killed** after one round of three survivors —
the redundant sort above, and both `schedule`-trigger defaults (every fixture
supplied an interval and a unit, so `|| 1` and `|| 'hours'` were never
exercised).

**The export gate caught eight things** — seven core helpers reachable only
through the components, and `AutoSyncAutomationCard`, exported but used only by
its own panel. The seven got direct tests; the component was un-exported.
Eight consecutive slices.

**Artefact check:** all 31 classes resolve.

Full suite 6613 tests. Build clean. Lint clean.

**Next:** slice D-ii, the run-history panel (1200-1253, 1394-1882) — filter
tabs, load-more, and the 27-function entry renderer.

### AUTO-SYNC SLICE D-ii — the run-history panel

`renderAutoSyncHistoryPanel` (1200-1243), `populateAutoSyncHistoryList`
(1394-1436), `createAutoSyncHistoryEntryElement` (1478-1572) and the helpers
under them.

**TEN OF THE REGION'S 27 FUNCTIONS ARE DEAD and were not ported.** Seven have
no call site at all — `autoSyncHistoryStatHtml` (1693),
`autoSyncHistoryPreviewPill` (1705), `autoSyncHistoryResultPill` (1716),
`autoSyncHistorySnapshotHtml` (1805), `autoSyncHistoryObjectHtml` (1824),
`autoSyncHistoryLogsHtml` (1845), `autoSyncHistoryFallbackSummary` (1644) —
and three are reachable only from those: `autoSyncHistoryPreviewText`,
`autoSyncHistoryFactHtml`, `autoSyncHumanizeKey`. They are earlier drafts of
the detail panel that the current one replaced; `autoSyncHistoryLogsHtml` is a
12-line twin of the 20-line `autoSyncHistoryLogsCompactHtml` that actually
runs. Verified by locating EVERY reference to each name, not by grep count
alone — three of the ten have two references and are still dead, because the
second reference is inside another dead function. Roughly 180 lines of vanilla
with no React counterpart.

**The two-phase render dissolves; the per-row error isolation does NOT.** The
vanilla emits a `data-renderer="pending"` placeholder, walks the DOM to build
cards with createElement, then binds click/keydown in a third pass — all
because live listeners cannot go in an innerHTML string. React renders once, so
the placeholder, the `data-renderer` markers, the binding pass and
`createAutoSyncHistoryListFallback` have no counterpart. But the vanilla ALSO
wraps each card build in try/catch and substitutes an error card (1428-1432),
and in React a throwing child unmounts its parent — so that one needed a real
error boundary, with a test that renders a genuinely throwing row and asserts
its two neighbours survive.

**A latent vanilla crash, found because the port reproduced it faithfully.**
The vanilla works hard to tolerate a malformed row: the normalizer has a whole
branch for "not an object at all" (1596-1607) and every card build is wrapped
in try/catch. But its filter and tab-count paths dereference `.status` BEFORE
either runs, and the tab counts execute on every render regardless of the
active filter (1211-1213) — so a null row throws out of
`renderAutoSyncHistoryPanel` and blanks the WHOLE history panel, which is
exactly what the other two guards exist to prevent. Declared hardening: one
optional chain. A mutant that removes it is part of the suite.

**One provably-equivalent mutant, annotated rather than left unkillable.** The
`stopPropagation` on "Run pipeline again" (1754) is inert in BOTH codebases —
the detail panel is a sibling of the clickable row, not a child, so the click
never had a row handler to bubble into. Kept for faithfulness, annotated in the
source, and removed from the mutation suite with the reasoning.

**Mutation pass: 47 mutants, 47 killed** after two rounds. First-round
survivors were real gaps: load-more and the running total compare against
DIFFERENT counts (window vs visible) and no fixture distinguished them; the
paging helper had no test at all; and `timeAgo`'s minutes branch was never
exercised because every fixture was over an hour old.

**The export gate caught sixteen things** — fifteen core helpers reachable only
through the component, and `AutoSyncHistoryEntryCard`, exported but used only
inside its own module. Nine consecutive slices.

**Artefact check: six classes are unstyled, and all six are unstyled in the
VANILLA too** — `auto-sync-history-time`, `auto-sync-history-title-block`,
`auto-sync-history-title-row`, `stat-before`, `stat-after` and `zero` each
appear exactly once in auto-sync.js and zero times in style.css. Carried
faithfully, same as `auto-sync-weekly-lanes` in slice C. The other 53 resolve.

Full suite 6682 tests. Build clean. Lint clean.

**Next:** slice E — wiring: the modal shell and its five tabs, the schedule
save/unschedule/run actions, the bulk menu (whose `window.prompt` must become a
SoulSync modal), the 3s status poller and the two shared-helpers seams.

### AUTO-SYNC SLICE E-i — the modal shell, tabs and bulk popover

`openAutoSyncScheduleModal` / `renderAutoSyncScheduleModal` / `setAutoSyncTab`
(571-740) and `openAutoSyncBulkMenu` / `promptAutoSyncBulkCustom` (1256-1313).
The actions, the poller and the two shared-helpers seams are E-ii.

**THE WINDOW.PROMPT IS GONE.** `promptAutoSyncBulkCustom` (1305) collected the
custom interval with `window.prompt`, which this repo forbids outright. It is
now an inline field inside the bulk popover — the only user-visible change in
this slice, and a required one. The VALIDATION is unchanged and lives in
`autoSyncParseCustomInterval`, wording included; a test asserts `window.prompt`
is never called. Two new CSS classes were written for the field, since there is
nothing to transcribe.

**Scroll preservation dissolves, and it looks like a dropped feature.** The
vanilla reads `.scrollTop` off the active lane board before every re-render and
writes it back after (657-661, 736-739) — because innerHTML destroys and
rebuilds every node, so dropping a playlist used to snap the board to the top.
React keeps the nodes, so the scroll never moves and there is nothing to
restore. Written down because "the port lost the scroll fix" is exactly what
this looks like from a diff.

**All four panels stay MOUNTED, as the vanilla's `.active` toggle does
(727-730).** Unmounting the inactive tabs would be the obvious React shape and
would silently discard each board's sidebar filter and expanded kind-groups on
every tab switch. There is a test that types a filter, switches away and back,
and asserts it survived.

**A transcribed inconsistency, with the reasoning recorded.** `enabledCount`
(660-661) filters on `s.enabled` as plain TRUTHINESS, where every other read of
that field in the file treats it as tri-state (`!== false && !== 0`). A
schedule whose `enabled` the backend omitted therefore counts as scheduled but
NOT as active, while its card renders as enabled. Transcribed rather than
harmonised: it is a header statistic, and "fixing" it would make the port show
a different number than the vanilla for the same data. Both behaviours have
tests naming the asymmetry.

**Two overlapping types collapsed into one.** Slice B introduced
`AutoSyncHistoryRow` as a placeholder for the health dot; slice D-ii introduced
`AutoSyncHistoryEntry` as the real shape. Two names for one thing is the drift
this port exists to remove, so the placeholder is gone and every consumer names
the real type. `AutoSyncScheduleState.runHistory` was `unknown[]` from slice A
for the same reason — nothing consumed it yet — and is now typed too.

**Mutation pass: 38 mutants, 38 killed** after one round. Two anchors had
drifted through formatting and were RE-ANCHORED; the one real survivor was the
history-tab error badge, where the test checked the badge existed but not that
it was the ONLY one.

**The export gate caught four more.** Ten consecutive slices.

**A pre-existing flake, noted not chased:** `-sync.use-export.test.tsx > drops
the status after its autoHideMs` failed once under full-suite parallel load and
passes 3/3 in isolation. It is a timer test from P5g, untouched by this wave.

Full suite 6717 tests. Build clean. Lint clean.

**Next:** slice E-ii — the save/unschedule/run/organize actions, the bulk
scheduling loop (carrying the invariant fix), the 3s status poller with its
mid-drag skip, and the two `typeof`-guarded shared-helpers seams.

### AUTO-SYNC SLICE E-ii — the controller: actions, bulk, poller

`-sync.use-autosync.ts` — the load/refresh (602-650), the save / unschedule /
run / organize actions (2051-2134, 2237-2336, 1933-1949), both bulk paths
(1315-1392) and the status poller (2338-2360). This closes the Auto-Sync wave.

**THE TESTS FOUND A REAL DEFECT, and it was mine.** `now` is a prop, so a
caller writing `now={() => Date.now()}` inline hands the hook a NEW function
every render. It was in `refresh`'s dependency list, so `refresh` changed
identity on every render, so the load effect re-fired, which set state, which
re-rendered — an unbounded loop refetching FIVE endpoints per turn. In the
suite it showed up as every test after the first describe timing out at 5s;
in production it would have hammered the API for as long as the modal was
open. `now` now lives in a ref and is out of the dependency list.

That is the second time in this wave that a test failure was worth more than
the test: the history panel's null-row crash was the first.

**The invariant cannot regress in this codebase.** All three save paths —
hourly, weekly and the bulk loop — go through one `dropOpposing`. The vanilla
enforced it by copy-paste and one of the three copies was missing (live bug #1,
fixed separately in 37bec3bab). Here there is only one copy to forget.
Likewise `bulkUnschedule` reads both schedule maps through one accessor, so
live bug #2 has no counterpart either. Both have mutants.

**`_autoSyncIsDragging` survives as a REF.** The poller reads it to skip a tick
mid-drag (2347) — a re-render during a drag would yank the card out from under
the cursor — and it must never itself cause a render, which is what a ref is
for. Three tests: it polls at 3s while work runs, it skips while dragging, and
it resumes when the drag ends.

**The poller stops on all three exits**, each with its own test: nothing
running, the modal closed, and unmount. The closed case needed the REACHABLE
scenario to be testable at all — open, load, then close with work still
running, so the hook keeps its loaded state and only `open` has changed. A test
that merely mounted with `open: false` proves nothing, because the state is
empty and the poller would not have started regardless.

**Mutation pass: 43 mutants, 43 killed** after two rounds. Six anchors had
moved through formatting and were RE-ANCHORED. One of my own mutants was
BROKEN — it inserted a `void` statement instead of removing the guard it
claimed to remove, so it could never have failed; rewritten rather than
counted. Four real gaps: the personalized enrichment throwing (the fixture only
returned unusable data, never threw), a stale error never clearing, the
personalized run falling through to the vanilla engine as well, and both
poller stop conditions.

**The export gate caught six more.** Eleven consecutive slices.

Full suite 6761 tests. Build clean. Lint clean.

### AUTO-SYNC — the post-wave review pass

Three real defects, found by re-reading the port against the vanilla rather
than by any test going red.

**1. A FLIP HAZARD I had built in.** `runNow` reached for
`window.runMirroredPlaylistPipeline` — but that function is defined in
auto-sync.js ITSELF (2481), the file the flip deletes. It would have worked up
to the flip and then gone silently dead: exactly the failure mode
vanilla-seams.test.ts exists to prevent, and the reason that file's header
calls these seams silent. The ported replacement already existed —
`-sync.use-pipeline.ts`, written in P5g — so the runner is now a REQUIRED
injected option rather than a global lookup, and the port has no dependency on
the doomed symbol at all. The seam row and the globals.d.ts entry I had briefly
added for it came back out; there is nothing left to guard.

That also closed a stale note: P5g's header said the Auto-Sync board "is its
own wave and does not exist yet". It does now, and it does not want the
vanilla's push-based refresh — `useAutoSync` polls and re-reads on its own.
What it wants is the reverse wiring, which the header now says.

**2. The bulk confirm copy had been collapsed.** The vanilla writes
`Every ${autoSyncIntervalLabel(hours).toLowerCase().replace(/^every /, '')}.`
— "Every 12 hours." — while the SUCCESS toast on the same path uses the short
`autoSyncBucketLabel` — "12h". I had used the short form for both. They differ
deliberately; fixed, with the exact string pinned and a mutant guarding it.

**3. The hydrate seam was never called.** I had flagged this as "open for the
shell wave" at the end of E-i, which was wrong — it is squarely slice E's
scope. `playlistQualityProfileSelectHtml` emits an EMPTY select and
`hydratePlaylistQualityProfileSelects` fills it; missing the second throws
nothing and simply renders the control empty forever. It now hydrates per card,
in the card's own effect, which covers both of the vanilla's call sites
(735-744 and 1089-1096) and the cases they miss — a card reappearing because a
filter cleared or a tab switched. Both seams now have vanilla-seams.test.ts
rows and both resolve.

**A note on my own method.** Two of the scripted edits in this pass SILENTLY
NO-OPPED because oxfmt had rewrapped the target text and I had not asserted the
match. A failing test caught it. An edit that reports success while changing
nothing is the same class of problem as a test that passes while measuring
nothing — every scripted edit in this repo should assert its anchor, and the
mutation scripts already do.

Mutation after the fixes: 45/45 on the controller, 87/87 across the boards and
shared chrome — including three new seam mutants (never hydrate, hydrate with
no select, hydrate with the wrong profile id) and two for the fixes above.

Full suite 6768 tests. Build clean. Lint clean.

**Still open for the shell wave:** the modal needs an entry point from the
mirrored tab's Auto-Sync button, and the page must pass
`useMirroredPipeline().run` into `useAutoSync` as `runPipeline` — the type
system now requires it, so this cannot be forgotten silently.

### LIVE BUG FIX — bulk scheduling left weekly schedules running

The P0 addendum's live bug #1, fixed in the vanilla rather than only in the
port, because it is live for users today and the flip is several slices away.

**The defect.** Auto-Sync can install two kinds of automation for one playlist,
an hourly `schedule` and a `weekly_time`, and the engine runs BOTH. The two
interactive save paths each delete the opposing one first, with comments saying
so. `saveAutoSyncPlaylistScheduleSilent` (1368-1392) — the path the Bulk menu
drives — never did. Bulk-scheduling a source containing a weekly-scheduled
playlist left both automations live, so that playlist refreshed on two cadences
at once.

**Root cause, and why the fix is not a third copy.** The invariant was enforced
by copy-paste at each call site, and the third site simply never got a copy.
That is the bug class, not the instance. The enforcement is now one helper,
`dropOpposingAutoSyncSchedule(playlistId, keep)`, called by all three paths.

**The regression guard is source-level, and mutation-verified.** auto-sync.js
is a browser script with no module boundary, so `vanilla-autosync-invariants.test.ts`
asserts over its source the way vanilla-crossfile.test.ts already does: every
save path calls the helper, calls it BEFORE writing the automation (a delete
issued after the POST could race the create), the helper reads the OPPOSITE
map, and the enforcement exists in exactly one place. Three mutants were run
against it — re-introducing the original bug, inverting the helper's two maps,
and moving the drop after the write — and all three were killed. Delete this
test file with auto-sync.js at the flip.

**LIVE BUG #2 — also fixed, on Boulder's call.** `bulkUnscheduleAutoSyncSource`
(1339) filtered on `playlistSchedules` alone, so "Unschedule all" could not see
weekly schedules: it undercounted in its own confirm dialog, said "No scheduled
X playlists to unschedule" when weekly ones existed, and left them running.
Same root cause as #1 — the bulk paths predate weekly schedules and never
learned about them.

I raised it separately rather than folding it into the #1 fix, because it
changes what a button DOES rather than repairing a defect: "Unschedule all"
removing weekly schedules too is a product decision. Boulder agreed, so both
kinds now go, through a shared `autoSyncSchedulesForPlaylist(playlistId)`
accessor, and the confirm dialog says "hourly and weekly" so the user knows
what they are agreeing to. Five mutants — reading the hourly map alone,
deleting only the first schedule found, dropping the weekly map from the
accessor, keeping the absent one (which would DELETE undefined), and reverting
the confirm copy — all killed.


## SHELL WAVE — P0 READ (IN PROGRESS)

The 15-tab page shell and sidebar: the last structural piece before the flip.
**This read is NOT finished** — recorded here so the established facts survive,
with what remains explicitly listed. No building starts until it is complete.

### Established so far

**Extent.** The markup is index.html 2226-3318 (1,093 lines), closing cleanly
at the `#sync-page` div. The controller is `initializeSyncPage` in
sync-services.js 3694-4036 (343 lines) — the tab click handler plus whatever
follows it; sync-services.js is 11,482 lines total, so the shell is a small
region of a very large file and the boundary matters.

**Fifteen tabs, and every one resolves.** `server`, `spotify`,
`spotify-public`, `itunes-link`, `tidal`, `qobuz`, `deezer`, `deezer-link`,
`youtube`, `beatport`, `listenbrainz-sync`, `lastfm-sync`,
`soulsync-discovery-sync`, `import-file`, `mirrored`. The handler does an
UNGUARDED `document.getElementById(`${tabId}-tab-content`).classList.add(...)`
(3714), so a tab whose pane is missing would throw mid-handler and leave the
buttons in a half-updated state — active class moved, no content shown. I
checked all 15 against the panes: 15 tabs, 15 panes, no orphans either way.
Not a bug. (My first pass said `server` had no pane; the regex required
`class="sync-tab-content"` exactly and that one pane carries
`"sync-tab-content active"` because it is the default. Verified before
reporting, which is the point.)

**`listenbrainz-sync` is named to avoid a collision.** The comment at 3760-3763
says so outright: the id is not `listenbrainz` because
`${tabId}-tab-content` would then collide with the DISCOVER page's own
`#listenbrainz-tab-content`. Two pages, one id namespace. Worth carrying into
the port's naming even though React scopes its own DOM.

**Dead code in the sidebar branch (3707-3712).** `const isMobile =
window.innerWidth <= 1300;` is computed and never read. The comment right
below it explains why — the sidebar is now always hidden and shown only while
a sync is active — so the mobile check is vestigial from an earlier layout.
Do not port it.

**Four header buttons**, all inline onclick into vanilla globals: Auto-Sync
(`openAutoSyncScheduleModal` — the modal this wave just ported, so this is the
entry point E-i left open), Library Match (`openManualLibraryMatchTool`), Sync
History (`openSyncHistoryModal`), Download Origins
(`openDownloadOriginsModal('playlist')`). Three of the four are surfaces this
port has NOT touched; each needs a decision — port, or keep vanilla behind a
seam with a vanilla-seams.test.ts row.

**Per-tab lazy loading, with a one-shot flag each.** `deezerArlPlaylistsLoaded`,
`mirroredPlaylistsLoaded`, `window._serverPlaylistsLoaded`,
`window._listenbrainzSyncTabLoaded`, `window._lastfmSyncTabLoaded`,
`window._soulsyncDiscoverySyncTabLoaded`, plus Beatport's
`ensureBeatportContentLoaded` / `cleanupBeatportContent` pair — the only tab
with a teardown on leaving. Note the flags are a mix of script-scoped `let`s
and `window` properties; which is which decides whether each survives the flip.

### LIVE BUG — the Start Sync button cancels itself on every second visit

Traced end to end. This is the page's PRIMARY action and it silently does
nothing for half the users who reach it.

**The chain.**

1. `initializeSyncPage()` is called TWICE from init.js — once at boot (2885)
   and once from `loadPageData`'s `case 'sync'` (3290).
2. `loadPageData` runs on every navigation. `activatePage` (init.js 575) only
   short-circuits when you are ALREADY on the page and it is visible, so
   leaving /sync and coming back re-runs the whole initializer.
3. Of the 25 `addEventListener` calls in that initializer, only FOUR are
   guarded by a matching `removeEventListener` first — the Spotify, Tidal,
   Deezer-ARL and Qobuz refresh buttons (3814-3838). The other 21 accumulate a
   fresh listener on every visit.
4. One of the 21 is `startSyncBtn.addEventListener('click', startSequentialSync)`
   (3971).
5. `startSequentialSync` (downloads.js 4060) is a TOGGLE: if the manager is
   already running it CANCELS and returns (4067-4070).
6. `SequentialSyncManager.start()` sets `this.isRunning = true` SYNCHRONOUSLY
   (core.js 1246), before any async work.

**So:** visit /sync, navigate away, come back → the button has 2 listeners →
one click runs `startSequentialSync` twice in a row → the first call starts the
sync and synchronously sets `isRunning`, the second sees it and cancels. The
sync starts and dies within the same click. Visit an ODD number of times and it
works; an EVEN number and the button appears dead.

**The same accumulation hits everything else unguarded**, with milder effects:
the YouTube / Spotify-Link / iTunes-Link / Deezer-Link parse buttons fire N
duplicate parses, the Mirrored and Beatport-clear buttons fire N times, the
Beatport Top-100 buttons open N modals (the `modalOpening` latch the Beatport
wave ported would swallow the extras — it was written for a different reason
and happens to cover this), and the 15 TAB BUTTONS themselves re-run the whole
tab handler N times per click.

**Why the port fixes it structurally.** React binds via JSX props on mounted
elements; there is no accumulate-on-init path to get wrong. The bug cannot be
carried across unless someone reintroduces manual `addEventListener` in an
effect without a cleanup — which is what the effect-cleanup discipline is for.

**FIXED in the vanilla, at the root rather than per-binding.** Adding 21
`removeEventListener` calls would have treated the symptom — and half of those
handlers are arrow functions whose references would have had to be hoisted
first. The actual defect is that an INITIALIZER is being called as a per-visit
hook. The markup is static in index.html and never re-created, so the bindings
only need to happen once: a module-scoped `_syncPageListenersBound` flag now
guards them, with the two genuinely per-visit calls (`ensureBeatportContentLoaded`
when the Beatport tab is already active, and `updateBeatportClearButtonState`)
hoisted ABOVE the guard so they still run on every navigation.

Six mutants, all killed: no guard at all; the flag set AFTER binding so a throw
mid-bind re-stacks on the next visit; the flag declared inside the function
where it resets every call; and each of the two per-visit refreshes falling
inside the guard.

**One of those mutants initially SURVIVED, and it was my test that was wrong.**
The assertion compared `body.indexOf(call) < guardIndex` — but `indexOf`
returns -1 when the call is DELETED, and -1 is less than any index, so the test
passed on absence. It now asserts presence first, then position. Same family as
the earlier lesson about tests that pass while measuring nothing.

### The sidebar — read, and smaller than it looks

`.sync-sidebar` (index.html 3301-3315) is just two sections: Sync Actions
(`#selection-info` + the `#start-sync-btn` above) and Sync Progress (a bar,
a text line and a readonly `#sync-log-area` textarea). It is hidden by default
and revealed by `showSyncSidebar()` (downloads.js 4041-4048), which the sync
start calls — and which itself refuses below 1300px. The tab handler re-hides
it on every tab switch (3707-3712), where the `isMobile` const is computed and
never used.

### P0 COMPLETE — what the shell actually has to do

**The server tab is already fully ported.** Its markup (3230-3298) is the
richest of the fifteen — playlist list, disambiguation overlay, and a two-column
compare editor with four filters, an M3U export and a footer — and all of it
exists in React already: `server-playlist-list.tsx`, `server-compare-editor.tsx`,
`server-order-modal.tsx`, `server-search-overlay.tsx` and the `-sync.server.ts`
pure core. `serverEditorBack` and `_serverEditorFilter` do not appear by name
because React expresses them as an `onBack` prop and a `FILTERS` const; both are
there. The shell only has to MOUNT them.

**The three remaining header buttons are SEAMS, and safe ones.** Each target
lives in its own file that the flip does not touch —
`openManualLibraryMatchTool` in manual-library-match.js, `openSyncHistoryModal`
in wishlist-tools.js, `openDownloadOriginsModal` in origin-history.js. So the
React header calls them through `window.x?.()` and each gets a
vanilla-seams.test.ts row. The fourth, `openAutoSyncScheduleModal`, is settled:
this wave ported it, and the shell supplies the entry point E-i left open.

**Four initializer calls become DEAD at the flip, not seams.** `_initImportFileTab`
(stats-automations.js), `ensureBeatportContentLoaded` (sync-spotify.js),
`cleanupBeatportContent` (core.js) and `updateBeatportClearButtonState`
(sync-services.js) all exist to wire markup the port replaces — the React
import-file tab owns its own file read and submit, and the Beatport sections
self-load through `useBeatportOnce`. They go on the deletion worklist; each
needs a reachability check first, because three of the four live in files that
SURVIVE and may have other callers.

**The selection model, decided.** `startSequentialSync` (downloads.js 4079-4087)
reads membership from the `selectedPlaylists` Set and ORDER from
`document.querySelectorAll('.playlist-card')`. Two options were open since P0:
have the React cards keep `.playlist-card` + `data-playlist-id` so the DOM query
still works, or give the function an ordered-ids parameter. **Take the
parameter.** The DOM-order read is the engine reaching into the view to
reconstruct something the view already knows; keeping it would make a React
render order a load-bearing contract of the download engine, discoverable only
by breaking it. The parameter is additive and the existing call site can pass
the same DOM-derived array until the shell flips.

### Build slices, in order

**S1 — the shell chrome. DONE.** See the entry below.

**S2 — the sidebar.** Two sections, the selection info line, Start Sync, the
progress bar/text/log. Shown only while a sync runs and only above 1300px.

**S3 — mount the fifteen.** Wire every ported tab into its panel, plus the
per-tab lazy-load equivalents (React does this with mount, not one-shot flags).

**S4 — the route flip, severs and deletions**, with the reachability checks
above and the `startSequentialSync` parameter.


### SHELL SLICE S1 — header, the fifteen-tab strip, the panel switch

`-sync.shell.ts` (the tab table and the two small decisions) and
`-ui/sync-shell.tsx`. index.html 2226-2295 plus the tab handler at
sync-services.js 3694-3803.

**The vanilla's tab handler did four things and only one is code here.** It
moved the `active` class (this component), re-hid the sidebar (S2), ran a
one-shot lazy load per tab, and computed an `isMobile` const it never read.

**The one-shot load flags dissolve into mounting — but only half of them do.**
Each was `if (tabId === 'x' && !xLoaded) { xLoaded = true; loadX(); }` against a
script-scoped or `window` flag: a hand-rolled mount hook, needed because all
fifteen panels exist in the DOM from page load and only their class changes.
The "run once" half is just mounting. The OTHER half is that the flags never
reset, so leaving a tab keeps what it loaded — which is why the shell records
an `opened` Set and keeps a panel MOUNTED after you navigate away rather than
unmounting it. Unmounting would have been the obvious React shape and would
have re-fetched on every revisit. There is a test that counts mounts across
three round trips and expects exactly one.

**That mount test was wrong first.** It counted in the render body, which
counts RENDERS — and a panel that stays mounted still re-renders when the shell
changes tab, so it ticked without a remount and proved nothing. Moved into a
`useEffect(…, [])`.

**The tab table is checked against the markup, not just transcribed from it.**
`-sync.shell.test.ts` reads index.html and asserts the same fifteen ids in the
same order, the same label for each (used as both the visible label and the
title), the same sprite class, the same three `data-link` tabs, and that the
default is the one the markup marks `active`. A transcription test that only
compares the port to itself would pass while drifting.

**`normalizeSyncTab` has no vanilla counterpart.** The vanilla trusts
`dataset.tab` and then does an unguarded
`getElementById(`${tabId}-tab-content`)`, which throws mid-handler on a bad id
and leaves the strip half-updated. It survived the first mutation round because
nothing in the component can reach it — the strip only ever passes ids from the
table — so it now has direct tests, including that `__proto__` and `toString`
are not tab ids (they would be, with a plain-object lookup instead of a Set).

**Three seams, all safe.** Library Match, Sync History and Download Origins
call `window.x?.()`; their targets live in manual-library-match.js,
wishlist-tools.js and origin-history.js, none of which the flip touches. Rows
added to vanilla-seams.test.ts, plus a test that a missing seam no-ops rather
than throwing. Download Origins keeps its literal `'playlist'` scope argument.

**One new class, one transcribed rule.** The vanilla styles the header button
row inline (`style="display:flex;gap:8px;align-items:center;"` at 2236); the
port emits `.sync-header-actions` and the CSS is a 1:1 transcription of those
three declarations.

**The post-slice review caught a missing page id.** The root rendered
`<div className="page-shell">` with no id. The vanilla nests page-shell inside
`<div class="page" id="sync-page">`, and every flipped route collapses those
two while KEEPING the id — dashboard-page.tsx 38 is `page-shell
dashboard-container` with `id="dashboard-page"`. `#sync-page` appears in no CSS
rule and no JS lookup today, so nothing would have broken loudly; it is the
handle the legacy chrome resolves a page by, and dropping it silently would
have diverged from every other route for no reason. Added, with a test and a
mutant.

**Mutation pass: 36 mutants, 36 killed** after one round, the single survivor
being the unreachable normalizer above.

Full suite 6803 tests. Build clean. Lint clean. All 35 emitted classes resolve.

**Next:** S2, the sidebar — two sections, the selection line, Start Sync, and
the progress bar/text/log, shown only while a sync runs and only above 1300px.

## S2 — the sidebar: the full writer map

The read went wider than the P0 note suggested. Five elements, but **six writer
sites across five files** — and the note had missed two of them.

| element | written by |
|---|---|
| `#selection-info` | core.js 1340-1369 (`SequentialSyncManager.updateUI`), sync-spotify.js 1812-1830 (`updateSyncActionsUI`) |
| `#start-sync-btn` | the same two (text + `disabled`); click bound at sync-services.js 3990-3993 |
| `#sync-progress-bar` | **nothing** |
| `#sync-progress-text` | **nothing** |
| `#sync-log-area` | api-monitor.js 1075 / 1122 / 1131 |

`helper.js` 798-811 is not a writer: it is the tour/help copy table, keyed by
selector, alongside a tour step at 2394 and the page's anchor list at 3449.
Those three are content, not behaviour, and they keep working as long as the
selectors survive the flip — which is a reason to keep `.sync-sidebar`,
`#start-sync-btn` and `#sync-log-area` as the port's real class and ids.

### The two writers the P0 note missed

`sync-services.js:3990` binds the Start Sync click (the P0 note had the handler
but not the binding site), and `helper.js` turned out to be three references,
not the two the note listed. Both found by grepping every id rather than
trusting the note — the same reason the method says re-read the vanilla before
each slice instead of porting from notes.

### FINDING — the Sync Progress bar and text are DEAD UI

`#sync-progress-bar` and `#sync-progress-text` have **no writer anywhere**.
Confirmed by grepping both ids across `static/`, `index.html` and `src/`: the
only hits are the markup itself and one CSS rule (`#sync-progress-text`, style.css
19067). No indirect writer either — the `.progress-bar-fill` class IS used
elsewhere, but every JS site that writes one targets a different, dynamically
built id (`youtube-discovery-progress-*`, `modal-sync-bar-*`,
`sync-history-bar-*`), never this one.

So in a live sync the bar sits at `width: 0%` and the text reads
"Ready to sync..." from page load to page close. The section header says "Sync
Progress" and two of its three children have never moved.

The one that DOES work is the log textarea, which is what makes this hard to
notice: the section looks alive because logs scroll past underneath a frozen bar.

**Not a bug to fix in the vanilla** — there is no "correct" value to restore,
because nothing ever computed one. It is unbuilt UI, not broken UI. The port
carries the decision instead: see the S2 build notes.

### NOT a bug — the log area's socket gate (checked, because it looked like one)

`loadLogs` opens with `if (socketConnected) return; // WebSocket handles this`
(api-monitor.js 1115), and no socket handler in api-monitor.js writes the
textarea. That reads exactly like a poller whose promised twin was never
written — which would have meant the log area froze on
"Loading activity feed..." for every user with a working socket.

It is fine. The twin lives in **core.js 885**:
`socket.on('tool:logs', (data) => updateLogsFromData(data))` — the same
`updateLogsFromData` in api-monitor.js that the HTTP path calls, fed by
`socketio.emit('tool:logs', ...)` (web_server.py 41653), which formats the
activity feed identically to `/api/logs`. Socket up: push. Socket down: 3s
poll. A correct poller-twin, just one whose halves live in different files.

Recorded because the near-miss is the point: the finding was written up as a
live bug and deleted after checking. `logs:live` (settings.js 3876) is a
different stream — the settings page's log viewer, tailing app.log.

### LIVE BUG — log polling stopped after the first visit (mine, from the
Start-Sync fix)

The `_syncPageListenersBound` guard from the previous fix returns early on
every visit after the first. `initializeLiveLogViewer()` sat at the BOTTOM of
`initializeSyncPage`, below that guard — and `loadPageData` calls
`stopLogPolling()` unconditionally at the top of EVERY navigation (init.js
3278), for every page including sync itself.

So: boot starts polling; the first navigation stops it; `initializeSyncPage`
returns early from then on and nothing ever calls `startLogPolling` again.
The activity feed died permanently after one navigation.

Masked in normal operation by the `tool:logs` socket twin above — with the
socket up the poll is a no-op anyway. It only bites when the socket is down,
which is precisely the case the poll exists to cover.

**Fixed** by hoisting `initializeLiveLogViewer()` into the per-visit block
above the guard, where the two Beatport refreshes already live. It is
idempotent — `startLogPolling` returns early when already polling — so this
restores exactly the pre-guard behaviour rather than approximating it.

Four mutants, all killed: the call moved back below the guard; the call
deleted; a duplicate copy left at the bottom (the half-applied fix); and
`stopLogPolling` removed from `loadPageData`, which is the cross-file fact that
makes the hoist necessary at all. The last one is why that assertion reads
init.js rather than asserting position alone.

**Method note.** This was found by reading the writer files for S2, not by a
test — the previous slice's own mutation pass could not have caught it, because
every mutant was scoped to the fix's own file. The lesson is the one the
reachability rule already encodes: a guard that changes WHEN a function runs
has to be checked against every caller of everything below it, not just the
lines it sits next to.

### S2 BUILT — the sidebar

`-sync.sidebar.ts` (pure core), `-sync.events.ts` (the socket seam),
`-ui/sync-sidebar.tsx` (component + `useSyncLog`), `fetchSyncLogs` on the api
layer, two CSS modifiers, and the `ss:sync-logs` re-broadcast in
api-monitor.js.

**One function covers both writers.** `SequentialSyncManager.updateUI` and
`updateSyncActionsUI` say the same three things when idle, and the second
delegates to the first whenever a sync is running (sync-spotify.js 1814-1817).
The only asymmetry is that `updateSyncActionsUI` never sets the button LABEL —
safe only because it cannot run while the label is anything but 'Start Sync'.
The port collapses them, and a test pins that a selection change mid-run cannot
overwrite the progress line.

**Start Sync stays ONE callback.** The vanilla handler is a toggle and the
caller decides from `running`. Splitting it into `onStart`/`onCancel` would let
a caller wire the two inconsistently, which is the shape the accumulated-
listener bug took. There is a test for the single callback.

**DECLARED DIVERGENCE — the progress bar and text are now wired.** They had no
writer in the vanilla (see the S2 read above). Rather than transcribe two
permanently-dead elements into new React, the port feeds them from the numbers
`SequentialSyncManager` already computes for the selection line:
`syncProgressPercent` counts COMPLETED playlists over the queue (0% at the
start of the first, 100% only after the last), and `syncProgressLabel` reads
"N of M playlists". The idle strings are untouched — a page that never starts a
sync is byte-identical to today's. Both are clamped, so a stale index cannot
render a bar wider than its container.

**DECLARED DIVERGENCE — the >1300px rule moves to CSS.** `showSyncSidebar`
samples `window.innerWidth` once, at start, so a sync begun on a narrow window
keeps the sidebar hidden even after the window is widened. The stylesheet
already enforces the same rule with `!important`, so the port lets it, and the
wart goes away. The port re-asserts the single-column grid for the new
`.sync-content-area--with-sidebar` inside its own ≤1300px block: the existing
media query has equal specificity and appears EARLIER in the file, so without
that repeat a narrow screen would get a two-column grid whose second column is
`display: none`.

**Transcribed, not corrected: the tab switch hides the sidebar mid-sync.** The
vanilla tab handler hides it unconditionally (3751), without checking whether a
run is in progress, so switching tabs during a sync drops the progress panel
until the next run. `syncSidebarVisible(running, hiddenByTabSwitch)` keeps that
exactly, with a test naming it.

**The sidebar stays MOUNTED while hidden.** The vanilla log poller runs for as
long as the page is open regardless of whether the panel is on screen;
unmounting on hide would stop the feed. Only the display flips.

**The log feed is a poller twin, and both halves are pinned.** `ss:sync-logs`
(push) plus a 3s `/api/logs` fetch gated on `window._socketConnected` — the
same cadence and the same gate as `loadLogs`. The re-broadcast goes INSIDE
`updateLogsFromData`, before its shape guard, so the socket push and the HTTP
poll both reach React through one seam. Because the dispatch lives in a browser
script the port cannot import, `-sync.events.test.ts` reads api-monitor.js and
asserts the channel name, the enclosing function, and the ordering — this is
the seam direction `vanilla-seams.test.ts` does not cover (that file guards
React calling INTO vanilla; this guards vanilla dispatching OUT).

**A race the vanilla loses, fixed here.** A `/api/logs` request is in flight
for as long as the server takes; a socket push landing meanwhile is strictly
newer. The vanilla applies whichever resolves last, so the feed visibly
rewinds. `useSyncLog` counts pushes and drops a fetch result whose count
changed while it was awaiting. Found by a test, and it has its own mutant.

**The ids are load-bearing.** `.sync-sidebar`, `#start-sync-btn` and
`#sync-log-area` are helper.js tour anchors (798-811, plus the anchor list at
3449). Renaming any of them empties a help bubble silently rather than failing
anything, so there is a test asserting all three survive, and a mutant that
renames one.

**Mutation: 29 mutants, 28 killed.** The survivor is proven equivalent and
annotated in place — dropping the `data === null` check in `hydrate` changes
nothing, because `apply` already discards a nullish frame via `syncLogText`.

**The export-coverage gate fired again — twelfth consecutive slice.**
`fetchSyncLogs`, `SYNC_LOGS_EVENT`/`useSyncLogsEvent` and `useSyncLog` were all
exercised indirectly but named by no test. Closed with real tests, not by
un-exporting: three api cases (ok, non-ok, network down), five seam cases, and
two on the hook directly. The seam assertions got their own 3/3 mutation pass.

Full suite 6865 passing, clean run. Build clean. Lint clean. All nine emitted
classes resolve in style.css.

**Added to the S4 worklist.** After the flip, `#sync-log-area` is React-owned
DOM, but `updateLogsFromData` still writes it — a second writer on a React
element, exactly the hazard the dashboard's P7 hardening pass dealt with. That
function must become dispatch-only at S4. `initializeLiveLogViewer`,
`startLogPolling`, `loadLogs` and `cleanupSyncPageLogs` all become unreachable
at the same moment (`cleanupSyncPageLogs` is ALREADY dead — defined at
api-monitor.js 1154 and called from nowhere); each needs the usual reachability
check, because `stopLogPolling` is called from init.js and `updateLogsFromData`
is bound app-wide in core.js, not per page.

**Next:** S3 — mount the fifteen ported tabs into `SyncShell`'s `panels` prop.

### S2 post-slice review — three defects in my own work

The review pass after committing S2 found three, all in the log area's scroll
handling. Worth recording because two of them are the same failure mode this
dossier keeps naming.

**1. The code did not do what its comment said.** The docblock claimed the
scroll position was sampled "in the layout effect", from "the retained ref
values from the previous render". It was a plain `useEffect` reading the
element directly — which runs AFTER React has committed the new text, and
writing a textarea's value can clamp `scrollTop`. So it measured post-update
numbers the vanilla never sees, and the comment described an implementation
that did not exist.

Fixed by capturing the metrics in an `onScroll` handler — the position the
reader actually left the box in, which is what the rule is asking about — and
switching to `useLayoutEffect` so the reset lands before paint.

**2. There was no test for any of it.** The pure predicate
`syncLogShouldScrollTop` was well covered; the code that FEEDS it was not
exercised at all, so the whole measurement path was unverified. Four tests
added, with jsdom's zero-height elements given real `scrollHeight`/
`clientHeight` via `Object.defineProperty` — without that every position reads
as "at the top" and the tests assert nothing.

**3. Two of those four tests initially measured nothing.** Both the
"measures where the reader LEFT the box" and "text is unchanged" cases were
written so the correct and the broken implementation produce the SAME result,
so neither could ever fail. Rewritten with inputs on which the two disagree:
the recorded metrics say one thing, the element says the other, and the
assertion distinguishes them. This is the third time in this port a test has
passed while measuring nothing — first the jsdom `DragEvent` case, then the
`indexOf`-returns-−1 case, now this. The pattern is always the same: the
assertion is true for reasons unrelated to the behaviour under test. Mutation
testing caught all three; nothing else would have.

**A fourth, minor: a redundant guard.** The effect carried a
`previous.current === text` check that duplicated its own `[text]` dependency —
a mutant removing it SURVIVED, correctly. Deleted rather than annotated, since
the dependency array already says it.

Also closed: `SyncShell`'s new `sidebarVisible` prop shipped with no test. The
grid modifier now has one, plus two mutants.

Scroll rule: 6 mutants, 6 killed. Grid modifier: 2/2. Suite 6870 passing on two
consecutive clean runs — an earlier run in the same batch had one failure whose
name I did not capture, consistent with the known load-dependent flake but not
positively identified.

## S3 READ — the fifteen panels, and why this is not "just wiring"

Every tab component exists. What does NOT exist is the thing that feeds them,
and the earlier note calling S3 "wiring; every tab is already ported" was
optimistic. The components take **five different `onOpen` signatures**, thirteen
of them need a `SourceVertical` instance, and two need shared selection state.
That is a page controller, and it should be built and tested as one.

### The panel map

| tab id | component | needs |
|---|---|---|
| `server` | `ServerPlaylistList` | `onOpenCompare(playlist, mirrored \| null)` |
| `spotify` | `SpotifyTab` | `selectedIds`, `onToggleSelect` |
| `spotify-public` | `SpotifyPublicTab` | `vertical`, `onOpen(sourceId, playlist)` |
| `itunes-link` | `ITunesLinkTab` | `vertical`, `onOpen(sourceId, playlist)` |
| `tidal` | `TidalTab` | `vertical`, `onOpen(sourceId)` |
| `qobuz` | `QobuzTab` | `vertical`, `onOpen(sourceId)` |
| `deezer` | `DeezerArlTab` | — |
| `deezer-link` | `DeezerLinkTab` | `vertical`, `onOpen(sourceId, playlist)` |
| `youtube` | `YouTubeTab` | `vertical`, `onOpen(sourceId, playlist)` |
| `beatport` | the `Beatport*Section` set | `env` |
| `listenbrainz-sync` | `ListenBrainzSyncTab` | `vertical`, `onOpen(card)` |
| `lastfm-sync` | `LastfmSyncTab` | `vertical`, `onOpen(card)` |
| `soulsync-discovery-sync` | `SoulsyncDiscoveryTab` | — |
| `import-file` | `ImportFileTab` | `onImported()` |
| `mirrored` | `MirroredTab` | `vertical`, `onOpen(sourceId)`, `sourceName` |

Four signatures for `onOpen` (`(sourceId)`, `(sourceId, playlist)`, `(card)`,
`(playlist, mirrored)`) plus `onImported`. They are NOT interchangeable, and
collapsing them behind one handler is how a modal ends up opening on the wrong
argument.

### What the controller owns

1. **Thirteen `useSourceVertical` instances**, one per `SYNC_SOURCES` entry.
   ListenBrainz needs its `onDiscoveryComplete` option — that auto-mirror is
   what puts LB and Last.fm rows in the Mirrored tab and therefore on the
   Auto-Sync board, so omitting it silently empties two downstream surfaces.
2. **The selection store** — `selectedPlaylists` in the vanilla. Read by the
   Spotify tab AND by the sidebar's `SyncActionsState`. One source of truth.
3. **The shared modals** — discovery, fix, source-ref, export, auto-sync.
4. **`runPipeline`**, injected into `useAutoSync`; it must come from
   `useMirroredPipeline().run`, because the window function it used to resolve
   lives in the file the flip deletes. The type system already enforces this.
5. **The sequential-sync bridge** — the sidebar's one toggle, plus the
   `startSequentialSync(orderedIds)` parameter decision, which stops React
   render order being a load-bearing contract of the download engine.

### Recommended split

S3 is two slices, not one:

- **S3a — the controller.** Verticals, selection store, modal routing, the
  sequential-sync bridge. Pure-core where it can be, tested on its own.
- **S3b — the mount.** `panels` assembled from S3a's handles, plus the
  artefact check that all fifteen ids still resolve.

Doing them as one would produce a large untested-in-the-middle component, which
is exactly what the per-slice mutation discipline is meant to prevent.

### Extra review pass — three checks clean, one new hazard

**Clean.** (a) Both edited vanilla files parse: `oxlint static/api-monitor.js`
and `static/sync-services.js`, 0 errors. Nothing in the vitest suite EXECUTES
those files — it reads them as text — so a syntax error would have shipped
silently. (b) 189 python tests read them (`test_dashboard_seam`,
`test_script_split_integrity`, `test_tools_page_selectors`,
`test_vanilla_globals_resolve`, plus two wishlist UI tests); all pass. The
python suite had not been run at all for this work. (c) The CSS cascade was
verified by line number rather than by reasoning: `.sync-sidebar--visible`
(14756) lands after `.sync-sidebar { display: none }` (14744), the ≤1300px
`display: none !important` (14722) still wins, and no later rule sets `display`
on `.sync-sidebar` — 19089 is the `.progress-section` descendant.

**NEW HAZARD for S4 — five duplicate ids, and there is no guard.**

The React sidebar renders `#selection-info`, `#start-sync-btn`,
`#sync-progress-bar`, `#sync-progress-text` and `#sync-log-area`. The vanilla
markup at index.html 3301-3315 defines the same five. The moment the React page
mounts, every one exists TWICE unless that markup is deleted in the same change.

That is worse than untidy. Three surviving vanilla writers still call
`document.getElementById` on these — `SequentialSyncManager.updateUI`
(core.js), `updateSyncActionsUI` (sync-spotify.js) and `updateLogsFromData`
(api-monitor.js). `getElementById` returns the FIRST match in document order,
which would be the vanilla node — the one nobody can see. React would render
the visible one and the vanilla would keep writing the invisible one, so the
sidebar would look permanently frozen with nothing throwing.

**Checked for a guard: there is none.** No python test and no vitest test scans
index.html for duplicate ids. The "duplicate-id guard" from the discover flip
was a reasoning step in that PR, not a test that survived. So nothing would
catch this.

**S4 requirements, therefore:**
1. Delete index.html 3301-3315 in the SAME commit that mounts the React
   sidebar — not a follow-up.
2. Make `updateLogsFromData` dispatch-only (already recorded).
3. Sever the other two writers, which lose their reason to exist once React
   owns the selection state.
4. Consider adding the missing duplicate-id scan as a permanent guard; it would
   have caught this class of bug on several earlier flips too.

### CORRECTION — the duplicate-id guard DOES exist

The entry above says "Checked for a guard: there is none." **That is wrong.**
`tests/test_react_ids_are_not_duplicated.py` exists and enforces exactly this.
My greps searched for "duplicate" (the file says "duplicated") and looked in
`webui/src/test/`, which is the wrong side — the guard is python.

What that changes, and what it does not:

**The hazard is real and unchanged.** An id rendered by a React page must not
also live in index.html, because `getElementById` then resolves by document
order. The guard's own docstring says the current arrangement "happens to work
today only because `#webui-react-root` sits near the top of index.html", and
that document-order dependence is the thing being forbidden.

**But it is caught, not silent.** CI fails on it. The discover flip proved this
the hard way — `f6369f914` was a follow-up commit fixing 81 collisions after a
flip that had leaned on exactly the document-order reasoning above. The guard
was born from the wishlist port, where duplicated modal ids made the Back
button poke the wrong copy.

**The sync branch is ALREADY red on this test, and was before S2.** Verified by
running it in a worktree at `64672160a`: **55 collisions pre-S2, 60 now.** The
five new ones are the sidebar's, exactly as expected. Every sync tab component
built in P5a-P5g contributed to the other 55. This is the normal mid-port state
— the components exist, the vanilla markup has not been deleted yet — and it
goes green at S4, not before.

**So the S4 requirement stands, for a better reason than I gave.** Deleting
index.html's sync markup in the same commit as the flip is not defensive
guesswork; it is the only way this test passes. There is no allowlist to lean
on: both `ADOPTED` and `KNOWN_PRE_EXISTING` in that file are empty sets, and
the comments say keeping them empty is deliberate.

**Method note.** This is the second "there is no X" claim I have made this
session that turned out to be false — the first was the log-area socket twin,
which I checked before reporting; this one I reported first. A negative claim
about a codebase this size needs the same standard of proof as a positive one,
and one failed grep is not proof.

### Verification coverage for this wave — the exact boundary

Recorded so the scope of "verified" is not re-litigated later. The change
surface is four non-doc files: `sync-services.js`, `api-monitor.js`,
`style.css`, and the React sync files.

- **JS suite: 6870 passing**, two consecutive clean runs.
- **Mutation:** 29/29 on the sidebar core + component (one survivor proven
  equivalent), 6/6 on the scroll rule, 2/2 on the grid modifier, 4/4 on the
  log-polling hoist, 3/3 on the socket seam.
- **Python, every test that reads a file this wave touched:**
  189 (the two vanilla JS files) + 208 (style.css readers) + 241 (the broad
  `static/*.js` scanners, including `test_helper_tours` — the sidebar keeps
  helper.js's tour anchors on purpose). **638 passing, 0 failing.**
- **`test_react_ids_are_not_duplicated`: RED, pre-existing.** 55 collisions
  before this wave, 60 after; the five added are the sidebar's. Goes green at
  S4. See the correction above.
- **Both vanilla files parse** (`oxlint`, 0 errors) — nothing in the JS suite
  executes them, so this is the only thing standing between a typo and prod.

**NOT run: the full python suite** (~9351 tests, ~40 min). The claim being made
is narrower and precise — every test that reads a file this wave changed passes.
A full run is an S4 gate, not a per-slice one.

### S3a-i BUILT — the sequential-sync machine + the selection store

`-sync.sequential.ts`. Pure, because the vanilla's version is a singleton whose
methods also write the DOM — which is how `updateUI` became the only place that
knows what the button says. Here the rules are values, the sidebar renders
them, and the engine calls belong to the hook (S3a-ii).

**Order is an argument now, not a DOM query.** `startSequentialSync` builds its
queue by walking `document.querySelectorAll('.playlist-card')` and keeping the
selected ones (4079-4087). That makes React's render order a load-bearing
contract of the download engine: reorder the grid and the sync order changes.
`syncOrderedSelection(order, selected)` takes it explicitly. This was the
decision already recorded for S4; it lands here.

**complete() and cancel() are ONE function.** Their resets are identical
(1304-1307 vs 1324-1327) — they differ only in the toast and in cancel's
`if (!this.isRunning) return`. `sequentialFinish` is the reset; the caller
picks the announcement.

**One declared hardening: an empty queue is refused.** The vanilla's `start()`
would accept one; it never gets the chance because 4073 bails earlier with a
toast. Letting it through would set `running` with nothing to run — a sidebar
stuck on "Syncing 1/0" whose only exit is Cancel. Refused at the source
instead of relying on a guard two files away.

**The name lookup is injected.** 1365 resolves against `spotifyPlaylists`,
which is precisely why the label falls back to 'Unknown' for a queued id from
any other source. The bridge takes a `nameFor` and passes an unresolved name
straight through — `syncSelectionLabel` owns the 'Unknown' fallback, and
duplicating it would put one decision in two places. A test asserts the lookup
is not called at all while idle.

**Mutation: 17 mutants, 17 killed** — after one round. The first pass had a
survivor on `sequentialFinish`'s not-running guard, and the test was at fault,
not the code: it passed `SEQUENTIAL_IDLE`, for which the guarded and unguarded
versions return that same constant. Added the input where they disagree — a
stopped state that still carries residue. Fourth time this session that a test
has passed for reasons unrelated to what it claims to check.

Full suite 6898 passing, clean run. Build clean. Lint clean.

**Next: S3a-ii** — the hook that drives this against the engine (thirteen
`useSourceVertical` instances, `startPlaylistSync`, the completion poll), then
S3b mounts the panels.

### S3a-ii BUILT — the runner

`-sync.use-sequential.ts`. Drives the machine against the download engine,
which STAYS VANILLA: `startPlaylistSync`, the `activeSyncPollers` map and
`disablePlaylistSelection` all live in downloads.js and survive the flip. They
arrive as a REQUIRED injected object, same rule as `runPipeline` on
useAutoSync — a window lookup that resolves to undefined is a dead button, and
the type system can forbid it instead.

The loop is an async runner in a ref, not an effect: effects re-run on
dependency changes, and a sync run has to survive every re-render the page
makes while it is going.

**LIVE BUG #7 — cancelling pops a bogus success toast.** The vanilla's
`cancel()` resets the manager, but the `setTimeout(() => this.syncNext(), 1000)`
queued by the previous iteration still fires. It then finds `currentIndex (0)
>= queue.length (0)` — both just zeroed — and calls `complete()`, which
announces SUCCESS for zero playlists and computes its duration from a
`startTime` that cancel has already set to null. `Date.now() - null` is
`Date.now()`, so the toast reads roughly:

    Sequential sync completed for 0 playlists in 1754584800.0s

...in green, a second after you cancel. Not fixed in the vanilla — the flip is
close and the port cannot reproduce it, because the runner checks a cancel flag
at every await boundary. Same call as live bug #5. **Boulder's to decide.**

**Mutation: 12 mutants, 12 killed** — after two rounds, and BOTH survivors were
the tests, not the code. One asserted cancellation with `tick(1000)`, which
fires the inter-sync gap timer and lets the broken version reach the same toast;
the other never checked that the progress index advances at all, so a runner
that started every sync but never moved the label looked identical from the
engine's side. Fixed by advancing exactly `0` — flush the microtasks, fire no
timers — and by asserting the label moves from Alpha to Beta.

**A test harness that hung the suite.** The first version injected a `wait` that
resolved immediately, which turns the completion poll into an unbounded
microtask loop whenever a playlist never settles: it starves the event loop, so
vitest's own 5s timeout cannot fire and the run hangs rather than fails. Now on
fake timers driving the hook's REAL 1s cadence. The mutation script also treats
a timeout as a kill, so a mutant that hangs cannot be scored as a survivor.

### OPEN for S3b — the run does not survive navigation

`sequentialSyncManager` is a MODULE SINGLETON (core.js 409). Leaving /sync
mid-run and coming back finds the vanilla still running, because the manager
outlives the page. The hook's state is per-mount, so the React sidebar would
come back reading idle while the engine is still syncing — and the runner
closure would still be alive, holding refs to a dead component.

Not solved here because the fix depends on where the controller ends up
sitting: either the state is hoisted into a module-scoped store (closest to the
vanilla), or the controller lives above the route so it is not unmounted. That
is an S3b decision and it needs the page structure to exist first. Recorded so
the flip does not ship with a sidebar that forgets a running sync.

### LIVE BUG #7 FIXED — cancel no longer announces a bogus completion

`syncNext` now bails when the run is no longer running:

```js
async syncNext() {
    if (!this.isRunning) return;
    if (this.currentIndex >= this.queue.length) { this.complete(); return; }
```

The guard goes FIRST, and that ordering is the whole fix — placed after the
completion branch, the bad `complete()` has already fired. `cancel()` zeroes
the queue and nulls `startTime` while the previous iteration's
`setTimeout(syncNext, 1000)` is still pending; the callback then read `0 >= 0`,
called `complete()`, and popped a green *"Sequential sync completed for 0
playlists in 1754584800.0s"* — the epoch, because `Date.now() - null` is
`Date.now()`.

Five mutants, all killed: guard removed; guard moved after the completion
branch; guard inverted; cancel no longer clearing `isRunning`; cancel losing
its own not-running guard. Source-level regression tests sit with the other
vanilla invariants.

### THE RUN NOW SURVIVES NAVIGATION — the store is module-scoped

Previously flagged as open for S3b; it was not actually blocked. The fix does
not depend on where the controller sits, so it lands here.

`sequentialSyncManager` is a module singleton (core.js 409): leave /sync
mid-run and come back and the vanilla is still going. The hook's state was
per-mount, so React would have come back reading idle while the engine was
still syncing, with the runner holding refs to an unmounted tree.

The state now lives in a module-scoped store the hook subscribes to via
`useSyncExternalStore`, and the runner and the toggle are module-level
functions. Four tests pin the behaviour: a run keeps driving the engine after
unmount; a remount reads it as running; the new mount can cancel the run the
old one started; and the remount sees LATER progress rather than a snapshot
frozen at mount.

`toggleSequentialSync` is exported as the module-level entry — a run is not
owned by any page, and four more tests drive a full queue, a refusal and a
cancel with no component mounted at all.

**Mutation: 14/14** after re-anchoring, including two new ones for the store
itself (reading `storeState` directly instead of subscribing; `emit` no longer
notifying listeners).

**The export-coverage gate fired for the THIRTEENTH consecutive slice** —
`toggleSequentialSync` was exported and named by no test. Closed by testing it
rather than un-exporting it, since driving a run without React is exactly the
property the store exists to provide.

Suite 6924 passing, clean run. Build clean. Lint at the 684 baseline —
a count, not a "0 errors", because an unused variable in a test had already
slipped past once this session as 685.

### S3a-iii BUILT — the vertical registry (kettui-safe)

`-sync.verticals.ts`. Built against kettui's fourth observation — *similar
things get reimplemented per feature instead of sharing an abstraction*. Nine
hand-written `useSourceVertical(SYNC_SOURCES.x, …)` calls at a page's top level
is precisely that shape: nine places to forget an option, nine to update when
the contract changes, and no way to notice a tenth source was added and missed.
One table drives it instead.

**NINE verticals, not fifteen — and the earlier note saying thirteen was
wrong.** Several tabs share one: Last.fm Radio rides ListenBrainz's machinery,
Deezer-link rides Deezer's. A test pins the count so that if it ever equals the
tab count someone has conflated the two.

**Hook order is safe** because `SYNC_VERTICAL_IDS` is a frozen module constant —
same ids, same order, every render, which is all the rules of hooks require. It
is declared explicitly rather than derived from `Object.keys`, so the order is a
stated contract instead of an accident of how the table was typed. A test
asserts it covers `SYNC_SOURCES` exactly, so a source added to the table cannot
silently skip the registry.

**The ListenBrainz trap is now table data, not a call-site.**
`onDiscoveryComplete` auto-mirrors matched tracks when a discovery finishes,
and it is what puts LB and Last.fm playlists into the Mirrored tab and onto the
Auto-Sync board. Miss it and two surfaces come up empty with nothing failing —
so it belongs somewhere the registry always reads.

**One test was rewritten because it proved nothing.** The first version checked
`perSource` by asserting the returned vertical "was defined" — true whether or
not the option arrived, since options are invisible on `SourceVertical`. It now
mocks the vertical and asserts the ACTUAL call arguments: the named source gets
its options, every other source gets `{}`, and the configs arrive in the
declared order. Fifth instance this session of a test passing for reasons
unrelated to its claim.

**Mutation: 7 mutants, 7 killed** — a source dropped from the list; a source
listed twice; `perSource` ignored; options handed to the wrong source;
`undefined` instead of `{}`; every source given the same config; one vertical
shared by all.

**Lint caught 5 type ERRORS, not warnings.** `vi.fn(() => …)` infers a zero-arg
mock, so `mock.calls` typed as empty tuples and every destructure was an error.
Only visible because the check is `oxlint --type-check src` over the whole tree
and the count is compared against the 684 baseline — a per-file run of the
source alone was clean.

Suite 6933 passing, clean run. Build clean. Lint back at baseline.

### S3a-iv BUILT — the selection store

`-sync.use-selection.ts`. Deliberately tiny, because the vanilla's is: EVERY
mutation app-wide is the add/delete pair inside `togglePlaylistSelection`
(sync-spotify.js 1804-1808). The other four references to `selectedPlaylists`
only read `.size` or `.has`. Verified by grepping every occurrence — there is no
clear, no prune, no bulk select anywhere.

**Two behaviours that read like bugs and are the shipped design**, both
transcribed rather than corrected:

1. **A finished sync leaves everything still selected.** `complete()` resets the
   QUEUE, never the selection, so the sidebar goes straight back to "3
   playlists selected" with Start Sync live again.
2. **Refreshing does not prune ids whose cards are gone.** That is only safe
   because `syncOrderedSelection` keeps solely what the page currently lists —
   the two behaviours are a PAIR, and a test says so, because pruning one
   without the other would silently change which playlists sync.

**No `clear()`.** Nothing in the vanilla calls one, so adding it would be
inventing API — and an export no test could honestly justify. A mutant that
adds one to the surface is killed.

Mutation: 5 mutants, 5 killed — toggle that only adds; the set mutated in place
(consumers would go stale); count decoupled from the set; toggle identity
changing every render (it is handed to every playlist row); and a `clear()`
appearing on the surface.

Suite 6941 passing, clean run. Build clean. Lint at baseline.

**Controller status: 3 of 5 done.** Sequential sync ✓, verticals ✓, selection ✓.
Remaining: modal routing (five modals, four distinct `onOpen` signatures) and
the `runPipeline` injection into `useAutoSync`. Then S3b mounts the panels.

### Verification note — the python gap from the core.js fix

The live-bug-#7 fix touched `core.js` and that wave was verified with the JS
suite only. Closed now: the **9 python tests that read core.js pass, 238 cases**.
Recording it because the rule that keeps catching things is to run the tests
that read the FILES a change touched, not the tests that sound related.

### VERIFY PASS — the vertical was a new object on every render

Found by re-reading the registry rather than by a test. `useSourceVertical`
returned a fresh object literal every render — `useMemo` appeared ZERO times in
the file — so `verticals.tidal` had a new identity on each pass.

That was survivable while each tab was mounted on its own. It stops being
survivable now the PAGE builds nine of them and hands each down as a prop: the
sync page re-renders on every selection toggle, every tab switch, every
sequential-sync progress step and every 3s log frame, and each one would have
handed all fifteen tabs a new `vertical`, re-running any consumer effect or
callback keyed on it.

Fixed at the source rather than papered over in the registry, so every existing
consumer benefits. All eleven returned members were already `useCallback`, so
the memo's deps are exact and the identity now changes only when `states`
genuinely changes.

Two tests, both discriminating: the whole vertical (not just `.states`) is
stable across repeated re-renders, and it DOES hand out a new object once a
seed lands — a memo too sticky to update would be the opposite failure. 2/2
mutants: memo removed, and the memo never invalidating.

Checked and NOT a problem, while looking: the registry passes `perSource?.[id]
?? {}`, a fresh `{}` per source per render. Harmless — `useSourceVertical`
stores options in a ref (129-130) and no effect depends on them.

Suite 6942 passing. Build clean. Lint at baseline. The five tab test files that
consume a vertical all still pass.

### S3a-v BUILT — modal routing

`-sync.use-modals.ts` (which modal is open) and `-ui/sync-modals.tsx` (the
mount). Controller item 4 of 5.

**NOTHING RENDERED `SourceModals` BEFORE THIS.** It existed, it was tested, and
it had no production mount site anywhere in `src/` — the tabs only ever
signalled `onOpen` into the void. Confirmed by grepping every reference.

**ONE modal at a time, declared.** The vanilla keeps a separate open-flag per
tab, but only one tab is ever active, so two can never be reached — the
per-tab state is an artefact of nine regions written separately, not a
capability. Nine independent ids here would preserve the artefact AND add
something the vanilla cannot do: open Tidal's modal, switch tabs, open
Qobuz's, and both are mounted. A single slot cannot express it.

**The four `onOpen` shapes are NOT unified.** `(sourceId)`,
`(sourceId, playlist)`, `(card)` and the server tab's `(playlist, mirrored)`
stay distinct: only the first two open a source modal, the LB/Last.fm card
shape is resolved to a source id by its own tab, and the server tab's is a view
switch, not a modal. Flattening them behind one handler is exactly how a modal
opens on the wrong argument.

**Table-driven again**, from `SYNC_VERTICAL_IDS`, so a source in the table
cannot end up with no modal — the failure mode of nine hand-written blocks.

**`discoveryStartBody` deliberately NOT passed.** `SourceModals` already
derives the ListenBrainz `{playlist}` body from `config.discovery.startBody`,
with a comment saying it does so precisely so a wiring omission cannot break
the start invisibly. Passing it from here would put one decision in two places,
and this would be the copy that drifts. `standalone` comes from the existing
page-level `useStandalone()` — one signal, same for all nine.

**Mutation: 9 mutants, 9 killed** — `openIdFor` ignoring its argument (every
modal opens at once); open not replacing (two stack); close doing nothing;
`openIdFor` frozen by empty deps (the modal never moves); `openModal` identity
churning; every modal handed the same id; `mirroredSource` given to all nine;
a source dropped from the mount; and the wrong config per source.

Suite 6953 passing, clean run. Build clean. Lint at baseline.

**Controller status: 4 of 5.** Remaining: inject `runPipeline` into
`useAutoSync` — small, and already decided (it must come from
`useMirroredPipeline().run`, because the window function it used to resolve
lives in the file the flip deletes; the type system enforces it). Then S3b
mounts the panels.

**Verify pass on the modal mount.** The design keeps NINE hosts permanently
mounted, one per vertical, with only one open — so it is worth knowing what a
closed one costs. Checked: `SourceModals` returns `null` when `openId` is null
(155) and the file contains ZERO `useEffect`. So a closed host is one render
returning nothing: no overlay, no subscription, no poll. Free, as the design
assumes.

Pinned with a test, because the mount now DEPENDS on it — if a closed host
ever started rendering an overlay or mounting an effect, the page would pay
nine times on every render, and the failure would look like a page-wide
slowdown rather than a modal bug. 1/1 mutant (a closed host rendering an
overlay).

Suite 6954. Build clean. Lint at baseline.

### Controller item 5 is NOT a slice — but it surfaced a collision

`runPipeline` needs no build. Both sides already exist and the types already
meet: `PipelineController.run` is `(playlistId: number, name: string) =>
Promise<void>` and `UseAutoSyncOptions.runPipeline` is `(playlistId: number,
playlistName: string) => void`. It is one line at the page level, so it belongs
to S3b.

**But checking who OWNS the controller found a real collision.**
`MirroredTab` already builds its own (`mirrored-tab.tsx` 171). If the page
builds a second one to feed `useAutoSync`, there are TWO controllers with TWO
poller maps, and a playlist started from the Auto-Sync board would be polled by
both — the same status endpoint, twice, forever. Not broken, exactly: the
board's copy would write through a page-level `onState` while the tab's own
`resume` (653-655) picks the row up and renders it. Just a silent doubling of
the request rate, which is the exact class of problem the request-flood phase
already had to claw back once.

**One controller, owned by the page.** Its two collaborators split cleanly:

- `onState` writes through `verticals.mirrored.patchState` — the page HAS the
  vertical now, since the registry owns all nine. It can build this itself.
- `reload` refetches the mirrored rows, which only `MirroredTab` knows how to
  do. That is the one piece the page does not have.

**Recommended shape:** the page owns the controller and supplies
`reload: () => reloadRef.current?.()`, with `MirroredTab` registering its
`load` into that ref on mount. This is consistent with the hook's existing
design — its own docblock says "the collaborators live in refs so the returned
controller is STABLE" — and avoids hoisting the richest tab's row state, which
would be a far larger refactor for no additional correctness.

It changes `MirroredTab`'s props, so it gets built and tested as part of S3b
rather than bolted on here.

**Controller status: 4 built, 1 resolved-as-wiring.** S3b now has a named
design decision waiting for it instead of a trap.
