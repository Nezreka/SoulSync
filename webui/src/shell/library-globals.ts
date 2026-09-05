/**
 * Library globals - the cross-file contract that outlived library.js.
 * Ported from webui/static/library-globals.js.
 *
 * When the library + artist-detail pages went React, everything page-shaped
 * moved to webui/src and library.js was deleted. These few pieces stayed as a
 * shared contract because OTHER classic scripts and the React shell call them
 * by global name:
 *
 *   - artistDetailPageState / the back-label stack - the shared state spine.
 *     React syncs into the SAME objects (vanilla-state.ts); stats-automations
 *     reads them for Artist Radio and the report-issue modal.
 *   - navigateToArtistDetail - init.js, enrichment.js and the shell bridge
 *     route through it; it owns the label-stack push/pop semantics.
 *   - playLibraryTrack - downloads.js, enrichment.js, stats-automations.js and
 *     the shell bridge all start library playback through it.
 *   - _updateSidebarLibraryBreadcrumb - init.js repaints the nav breadcrumb.
 *   - _handoffLibrarySearchToEnhancedSearch - the React library and label
 *     pages hand a query off to the search page with it.
 *
 * The globals this reads (currentPage, audioPlayer, npRepeatMode, and the
 * core.js/media-player.js functions) are global LEXICAL bindings or classic
 * function declarations in still-classic scripts - reached as bare names
 * through the global scope chain, declared ambiently below. When their homes
 * port into the shell, the declares migrate with them.
 */

declare global {
  /* eslint-disable no-var */
  var PAGE_WILL_CHANGE_EVENT: string;
  var currentPage: string;
  var audioPlayer: HTMLAudioElement | null;
  var npRepeatMode: string;
  var navigateToPage: (pageId: string, options?: Record<string, unknown>) => void;
  var setTrackInfo: (info: Record<string, unknown>) => void;
  var showLoadingAnimation: () => void;
  var hideLoadingAnimation: () => void;
  var startStream: (result: Record<string, unknown>) => void;
  var startAudioPlayback: () => Promise<void>;
  var clearTrack: () => void;
  var showToast: ((message: string, type?: string, durationOrContext?: number | string) => void) | undefined;
  /* eslint-enable no-var */
}

interface LibraryPlayableTrack {
  id?: number | string;
  title?: string;
  name?: string;
  file_path?: string;
  bitrate?: number;
  sample_rate?: number;
  artist_id?: number;
  album_id?: number;
  artist_name?: string;
  _stats_image?: string | null;
  /** Play THIS file, skipping the title+artist refresh below. See playLibraryTrack. */
  exact_path?: boolean;
  /** Library v2 ids. A v2 row's `id` is a v2 id and means nothing to the legacy
   *  resolve-track lookup or to the media server - the typed ids below say which
   *  of the three namespaces a caller actually holds. */
  lib2_track_id?: number | string | null;
  legacy_track_id?: number | string | null;
  server_track_id?: number | string | null;
  lib2_artist_id?: number | string | null;
}

interface ArtistDetailPageState {
  isInitialized: boolean;
  currentArtistId: string | number | null;
  currentArtistName: string | null;
  currentArtistSource: string | null;
  enhancedView: boolean;
  enhancedData: {
    albums?: Array<{ tracks?: Array<{ id?: number | string }>; thumb_url?: string }>;
    artist?: { thumb_url?: string };
  } | null;
  expandedAlbums: Set<unknown>;
  selectedTracks: Set<unknown>;
  editingCell: unknown;
  enhancedTrackSort: Record<string, unknown>;
  completionController?: AbortController | null;
}

export function _handoffLibrarySearchToEnhancedSearch(query: string): void {
  if (typeof navigateToPage !== 'function') return;
  navigateToPage('search');
  setTimeout(() => {
    const input = document.getElementById('enhanced-search-input') as HTMLInputElement | null;
    if (input && query) {
      input.value = query;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }, 300);
}

// ===============================================
// Artist Detail Page Functions
// ===============================================

// Artist detail page state
const _ARTIST_DETAIL_BACK_LABELS: Record<string, string> = {
  library: 'Back to Library',
  search: 'Back to Search',
  discover: 'Back to Discover',
  watchlist: 'Back to Watchlist',
  wishlist: 'Back to Wishlist',
  stats: 'Back to Stats',
  'playlist-explorer': 'Back to Explorer',
  automations: 'Back to Automations',
  dashboard: 'Back to Dashboard',
  sync: 'Back to Sync',
  'active-downloads': 'Back to Downloads',
};

// Stack of origins for the back-button label. Each entry: {type:'page', pageId}
// or {type:'artist', name}. Pushed on forward navigation, popped on back.
// Separate from browser history - only used for the label display.
const _artistDetailLabelStack: Array<{ type: 'page'; pageId: string } | { type: 'artist'; name: string }> = [];
let _artistDetailGoingBack = false;

// Exported for the React artist-detail page, which renders the back button.
// Arrivals from a still-vanilla page (search, label detail, enrichment) push
// onto this stack here, so React has to read the SAME array to label the
// button "Back to Search" rather than a bare "Back".
window.artistDetailBackLabels = _ARTIST_DETAIL_BACK_LABELS;
window.artistDetailLabelStack = _artistDetailLabelStack;

const artistDetailPageState: ArtistDetailPageState = {
  isInitialized: false,
  currentArtistId: null,
  currentArtistName: null,
  currentArtistSource: null,
  enhancedView: false,
  enhancedData: null,
  expandedAlbums: new Set(),
  selectedTracks: new Set(),
  editingCell: null,
  enhancedTrackSort: {},
};

// Exported for the React artist-detail page and for the classic scripts that
// used to read the global lexical binding bare (their bare reads now fall
// through the scope chain to this window property - same object, not a copy).
window.artistDetailPageState = artistDetailPageState as unknown as typeof window.artistDetailPageState;

export function clearArtistDetailPageState(): void {
  if (artistDetailPageState.completionController) {
    artistDetailPageState.completionController.abort();
    artistDetailPageState.completionController = null;
  }

  artistDetailPageState.currentArtistId = null;
  artistDetailPageState.currentArtistName = null;
  artistDetailPageState.currentArtistSource = null;
}

// core.js declares PAGE_WILL_CHANGE_EVENT as a global lexical const and has
// run by the time the shell bundle loads; the literal fallback keeps this
// module loadable standalone (tests) and matches core.js:3 byte for byte.
const _PAGE_WILL_CHANGE = typeof PAGE_WILL_CHANGE_EVENT !== 'undefined'
  ? PAGE_WILL_CHANGE_EVENT : 'ss:webui-page-will-change';
window.addEventListener(_PAGE_WILL_CHANGE, (event) => {
  const detail = (event as CustomEvent<{ fromPageId?: string; toPageId?: string }>).detail || {};
  if (detail.fromPageId === 'artist-detail' && detail.toPageId !== 'artist-detail') {
    clearArtistDetailPageState();
  }
});

// Maximum visible characters of an artist name in the sidebar Library
// breadcrumb. Names longer than this get truncated with an ellipsis so the
// nav button width stays consistent across the rest of the sidebar.
const _SIDEBAR_BREADCRUMB_ARTIST_MAXLEN = 14;

export function _updateSidebarLibraryBreadcrumb(): void {
  // Rewrite the Library nav button label between plain "Library" and a
  // "Library / <Artist>" breadcrumb depending on whether the user is on
  // the artist-detail pseudo-page. Pure visual - touches no app state.
  const btn = document.querySelector('[data-page="library"]');
  if (!btn) return;
  const textEl = btn.querySelector('.nav-text') as HTMLElement | null;
  if (!textEl) return;

  const onArtistDetail = (typeof currentPage === 'string' && currentPage === 'artist-detail');
  const artistName = onArtistDetail ? (artistDetailPageState.currentArtistName || '') : '';

  if (!onArtistDetail || !artistName) {
    // Default state: plain "Library" label. Use textContent so we wipe
    // any previously-injected breadcrumb spans cleanly.
    textEl.textContent = 'Library';
    textEl.removeAttribute('data-breadcrumb');
    return;
  }

  // Truncate long names so the button width stays consistent.
  let display = artistName;
  if (display.length > _SIDEBAR_BREADCRUMB_ARTIST_MAXLEN) {
    display = display.slice(0, _SIDEBAR_BREADCRUMB_ARTIST_MAXLEN - 1).trimEnd() + '…';
  }

  // Render via inline spans so CSS can style the root / separator / context
  // independently. Escape via textContent on individual spans.
  textEl.setAttribute('data-breadcrumb', '1');
  textEl.textContent = '';
  const root = document.createElement('span');
  root.className = 'nav-text-root';
  root.textContent = 'Library';
  const sep = document.createElement('span');
  sep.className = 'nav-text-sep';
  sep.textContent = ' / ';
  const ctx = document.createElement('span');
  ctx.className = 'nav-text-context';
  ctx.textContent = display;
  ctx.title = artistName; // full name on hover
  textEl.appendChild(root);
  textEl.appendChild(sep);
  textEl.appendChild(ctx);
}

export function navigateToArtistDetail(
  artistId: string | number,
  artistName: string,
  sourceOverride: string | null = null,
  options: { skipRouteChange?: boolean } = {},
): void {
  const normalizedSource = sourceOverride || null;

  // Skip reload if already on this exact artist/source (prevents double-fetch
  // when the router fires activateLegacyPath after navigating to an
  // /artist-detail/:source/:id URL).
  if (artistId &&
      String(artistId) === String(artistDetailPageState.currentArtistId) &&
      String(normalizedSource || '') === String(artistDetailPageState.currentArtistSource || '')) {
    if (currentPage !== 'artist-detail') {
      navigateToPage('artist-detail', {
        artistId,
        artistSource: normalizedSource,
        skipRouteChange: options.skipRouteChange === true,
      });
    }
    return;
  }
  console.log(`🎵 Navigating to artist detail: ${artistName} (ID: ${artistId}${sourceOverride ? `, source: ${sourceOverride}` : ''})`);

  // Maintain the label stack. Back navigations pop; forward navigations push.
  // Only treat the flag as a back-nav signal when we're still on artist-detail -
  // if history.back() landed on a non-artist page first, the flag is stale.
  if (_artistDetailGoingBack && currentPage === 'artist-detail') {
    _artistDetailLabelStack.pop();
    _artistDetailGoingBack = false;
  } else {
    _artistDetailGoingBack = false; // clear any stale flag
    if (currentPage !== 'artist-detail') {
      // Cleared IN PLACE, not reassigned: window.artistDetailLabelStack
      // holds this same array for the React page, and swapping the
      // binding would leave React reading a detached copy.
      _artistDetailLabelStack.length = 0; // fresh chain from a non-artist page
    }
    if (currentPage === 'artist-detail' && artistDetailPageState.currentArtistName) {
      _artistDetailLabelStack.push({ type: 'artist', name: artistDetailPageState.currentArtistName });
    } else {
      const pageId = (typeof currentPage === 'string' && currentPage && currentPage !== 'artist-detail')
        ? currentPage : 'library';
      _artistDetailLabelStack.push({ type: 'page', pageId });
    }
  }

  // Abort any in-progress completion stream
  if (artistDetailPageState.completionController) {
    artistDetailPageState.completionController.abort();
    artistDetailPageState.completionController = null;
  }

  // The vanilla cancelled its inline edit and removed the manual-match
  // overlay here. Both are React-owned now: editingCell is never set, and
  // #enhanced-manual-match-overlay is a React-rendered node that must not be
  // removed out from under its owner - the route change unmounts it.

  // Store current artist info and reset enhanced view state
  artistDetailPageState.currentArtistId = artistId;
  artistDetailPageState.currentArtistName = artistName;
  artistDetailPageState.currentArtistSource = normalizedSource;
  artistDetailPageState.enhancedData = null;
  artistDetailPageState.expandedAlbums = new Set();
  // Cleared IN PLACE: React mirrors its selection into this same Set, and the
  // vanilla track-delete path deletes from it. Swapping the object out would
  // leave both writing somewhere nobody reads.
  artistDetailPageState.selectedTracks.clear();
  artistDetailPageState.enhancedTrackSort = {};
  artistDetailPageState.enhancedView = false;

  // Hand off. React owns this route outright now - this function's remaining
  // job is the state written above, which a dozen globals over in
  // stats-automations.js and the Enhanced modals read back out.
  navigateToPage('artist-detail', {
    artistId,
    artistSource: normalizedSource,
    skipRouteChange: options.skipRouteChange === true,
  });
}

export async function playLibraryTrack(
  track: LibraryPlayableTrack,
  albumTitle?: string,
  artistName?: string,
): Promise<void> {
  if (!track.file_path) {
    showToast?.('No file available for this track', 'error');
    return;
  }

  // Library tracks have authoritative metadata in the SoulSync DB - when the
  // caller has a track.id, fetch the canonical row from resolve-track and
  // overwrite the caller-supplied fields with the DB values. Falls back
  // silently to the caller-supplied values on any error so we never lose the
  // play action over a metadata fetch.
  //
  // exact_path opts out. resolve-track matches on title+artist with LIMIT 1, so
  // for two copies of the same song it hands BOTH the same file_path - a caller
  // auditioning duplicates would play identical audio twice and think it had
  // compared them. Callers that already hold the exact file say so.
  //
  // Library v2 rows carry a `lib2_track_id` and address the legacy row (if it
  // still exists) as `legacy_track_id`. `track.id` is then a v2 id, which
  // resolve-track cannot look up - running it would overwrite good metadata
  // with a miss, so a v2-only row skips the refresh entirely.
  if (
    !track.exact_path &&
    (track.legacy_track_id || (track.id && !track.lib2_track_id)) &&
    (track.title || track.name) &&
    (artistName || track.artist_name)
  ) {
    try {
      const _dbResp = await fetch('/api/stats/resolve-track', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title: track.title || track.name,
          artist: artistName || track.artist_name || '',
        }),
      });
      const _dbData = (await _dbResp.json()) as {
        success?: boolean;
        track?: {
          id?: number; title?: string; file_path?: string; bitrate?: number;
          artist_id?: number; album_id?: number; image_url?: string;
          album_thumb_url?: string; album_title?: string; artist_name?: string;
        };
      };
      if (_dbData && _dbData.success && _dbData.track) {
        const _row = _dbData.track;
        track = {
          ...track,
          id: _row.id ?? track.id,
          title: _row.title || track.title,
          file_path: _row.file_path || track.file_path,
          bitrate: _row.bitrate ?? track.bitrate,
          artist_id: _row.artist_id ?? track.artist_id,
          album_id: _row.album_id ?? track.album_id,
          _stats_image: _row.image_url || _row.album_thumb_url || track._stats_image || null,
        };
        if (_row.album_title) albumTitle = _row.album_title;
        if (_row.artist_name) artistName = _row.artist_name;
      }
    } catch (_dbErr) {
      console.debug('library track DB refresh skipped:', _dbErr);
    }
  }

  try {
    // Stop any current playback first
    if (audioPlayer && !audioPlayer.paused) {
      audioPlayer.pause();
    }

    // Get album art from enhanced data if available
    let albumArt: string | null | undefined = null;
    if (artistDetailPageState.enhancedData) {
      const albums = artistDetailPageState.enhancedData.albums || [];
      for (const a of albums) {
        if ((a.tracks || []).some((t) => t.id === track.id)) {
          albumArt = a.thumb_url;
          break;
        }
      }
      if (!albumArt) albumArt = artistDetailPageState.enhancedData.artist?.thumb_url;
    }
    if (!albumArt && track._stats_image) albumArt = track._stats_image;

    // Set track info in the media player UI
    setTrackInfo({
      title: track.title || 'Unknown Track',
      artist: artistName || 'Unknown Artist',
      album: albumTitle || 'Unknown Album',
      filename: track.file_path,
      is_library: true,
      image_url: albumArt,
      id: track.id,
      lib2_track_id: track.lib2_track_id || null,
      legacy_track_id: track.legacy_track_id || null,
      server_track_id: track.server_track_id || null,
      artist_id: track.artist_id,
      // iss29-B08: a V2-native track has no legacy artist id, so the player's
      // "Go to artist" needs the lib2 one to route to /library?artist=.
      // setTrackInfo drops anything it isn't handed.
      lib2_artist_id: track.lib2_artist_id || null,
      album_id: track.album_id,
      bitrate: track.bitrate,
      sample_rate: track.sample_rate,
    });

    // Show loading state
    showLoadingAnimation();
    const loadingText = document.querySelector('.loading-text');
    if (loadingText) {
      loadingText.textContent = 'Loading library track...';
    }

    // POST to library play endpoint
    const response = await fetch('/api/library/play', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        file_path: track.file_path,
        title: track.title || '',
        artist: artistName || '',
        album: albumTitle || '',
        // Server song id so playback can stream via the media server
        // when the file isn't on SoulSync's disk (#809). A Library v2 row's
        // `id` is a v2 id and means nothing to the media server, so only a
        // server/legacy id may be sent as `track_id`; the typed ids ride
        // alongside for the v2-aware endpoints.
        track_id:
          track.server_track_id ||
          track.legacy_track_id ||
          (track.lib2_track_id ? null : track.id || null),
        lib2_track_id: track.lib2_track_id || null,
        legacy_track_id: track.legacy_track_id || null,
        server_track_id: track.server_track_id || null,
      }),
    });

    const result = (await response.json()) as { success?: boolean; error?: string };
    if (!result.success) {
      // File not on disk - fall back to streaming from configured source
      console.warn('Library file not found, falling back to stream source');
      hideLoadingAnimation();
      const streamRes = await fetch('/api/enhanced-search/stream-track', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          track_name: track.title || '',
          artist_name: artistName || '',
          album_name: albumTitle || '',
        }),
      });
      const streamData = (await streamRes.json()) as {
        success?: boolean;
        result?: Record<string, unknown>;
      };
      if (streamData.success && streamData.result) {
        streamData.result.artist = artistName;
        streamData.result.title = track.title;
        streamData.result.album = albumTitle;
        streamData.result.image_url = track._stats_image || null;
        startStream(streamData.result);
        return;
      }
      throw new Error(result.error || 'Failed to start library playback');
    }

    // Re-apply repeat-one loop property
    if (audioPlayer) audioPlayer.loop = (npRepeatMode === 'one');
    // Stream state is already "ready" - start audio playback directly
    await startAudioPlayback();
  } catch (error) {
    console.error('Library playback error:', error);
    showToast?.(`Playback error: ${(error as Error).message}`, 'error');
    hideLoadingAnimation();
    clearTrack();
  }
}
