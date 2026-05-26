// ===================================================================
// LISTENBRAINZ SYNC TAB
// ===================================================================
// Phase 1c.1 of the Discover-to-Sync unification. Renders the user's
// cached ListenBrainz playlists as a Sync-page tab so they participate
// in the same discovery → mirror → auto-sync pipeline as Spotify /
// Tidal / Qobuz / etc. — without forcing the user to detour through
// the Discover page.
//
// All the heavy lifting (modal, discovery state machine, sync) already
// lives in sync-services.js + discover.js. This file is just the
// Sync-page entry point: list the cached playlists, render cards,
// pre-fetch tracks on click, then hand off to
// ``openDownloadModalForListenBrainzPlaylist`` which owns the rest.

let _lbSyncCurrentType = 'created_for_user';
let _lbSyncPlaylistsByType = {};  // {type: [playlist...]} cache

async function loadListenBrainzSyncPlaylists() {
    const container = document.getElementById('listenbrainz-sync-playlist-container');
    const refreshBtn = document.getElementById('listenbrainz-sync-refresh-btn');
    if (!container) return;

    container.innerHTML = `<div class="playlist-placeholder">🔄 Loading ListenBrainz playlists...</div>`;
    if (refreshBtn) {
        refreshBtn.disabled = true;
        refreshBtn.textContent = '🔄 Loading...';
    }

    // Fetch all three LB playlist categories in parallel. The Discover
    // page does the same; we mirror its behavior for state-cache parity.
    try {
        const [createdFor, userPl, collab] = await Promise.all([
            fetch('/api/discover/listenbrainz/created-for').then(r => r.json()),
            fetch('/api/discover/listenbrainz/user-playlists').then(r => r.json()),
            fetch('/api/discover/listenbrainz/collaborative').then(r => r.json()),
        ]);

        // Auth-failure responses look like `{success:false, error:'...'}`.
        // Surface them to the user instead of pretending the list was empty.
        const anyUnauthed = !createdFor.success && (
            (createdFor.error || '').toLowerCase().includes('not authenticated')
        );
        if (anyUnauthed) {
            container.innerHTML = `<div class="playlist-placeholder">ListenBrainz not connected. Add your token in Settings → Connections to see your playlists here.</div>`;
            return;
        }

        _lbSyncPlaylistsByType = {
            created_for_user: createdFor.playlists || [],
            user_created: userPl.playlists || [],
            collaborative: collab.playlists || [],
        };
        renderListenBrainzSyncPlaylists();

        console.log(
            `🎧 ListenBrainz Sync tab loaded: ${_lbSyncPlaylistsByType.created_for_user.length} for-you, ` +
            `${_lbSyncPlaylistsByType.user_created.length} user, ` +
            `${_lbSyncPlaylistsByType.collaborative.length} collaborative`
        );
    } catch (err) {
        container.innerHTML = `<div class="playlist-placeholder">❌ Error loading ListenBrainz playlists: ${err.message}</div>`;
        if (typeof showToast === 'function') {
            showToast(`Error loading ListenBrainz playlists: ${err.message}`, 'error');
        }
    } finally {
        if (refreshBtn) {
            refreshBtn.disabled = false;
            refreshBtn.textContent = '🔄 Refresh';
        }
    }
}

function renderListenBrainzSyncPlaylists() {
    const container = document.getElementById('listenbrainz-sync-playlist-container');
    if (!container) return;

    const playlists = _lbSyncPlaylistsByType[_lbSyncCurrentType] || [];
    if (playlists.length === 0) {
        const empty = {
            created_for_user: 'No "For You" playlists yet. ListenBrainz publishes Weekly Exploration / Top Discoveries on its own schedule.',
            user_created: 'You haven\'t created any ListenBrainz playlists yet.',
            collaborative: 'No collaborative playlists.',
        }[_lbSyncCurrentType] || 'No playlists.';
        container.innerHTML = `<div class="playlist-placeholder">${empty}</div>`;
        return;
    }

    container.innerHTML = playlists.map(p => {
        // The Discover-page endpoints wrap each entry in JSPF shape:
        //   { playlist: { identifier: 'https://.../<mbid>', title, creator,
        //                 annotation: {track_count}, track: [...] } }
        // Pull out the inner playlist object + extract the mbid from the URL.
        const inner = p.playlist || p;
        const mbid = (inner.identifier || '').split('/').pop() || inner.id || '';
        const title = inner.title || inner.name || 'ListenBrainz Playlist';
        const creator = inner.creator || 'ListenBrainz';
        let count = 0;
        if (inner.track_count) {
            count = inner.track_count;
        } else if (inner.annotation && inner.annotation.track_count) {
            count = inner.annotation.track_count;
        } else if (Array.isArray(inner.track) && inner.track.length > 0) {
            count = inner.track.length;
        }
        // Reuse listenbrainzPlaylistStates so the modal state survives
        // tab switches (matches Discover-page behavior).
        const state = (typeof listenbrainzPlaylistStates !== 'undefined'
            && listenbrainzPlaylistStates[mbid]) || null;
        const phase = state && state.phase ? state.phase : 'fresh';
        const phaseText = (typeof getPhaseText === 'function')
            ? getPhaseText(phase)
            : (phase === 'fresh' ? 'Ready to discover' : phase);
        const phaseColor = (typeof getPhaseColor === 'function')
            ? getPhaseColor(phase)
            : '#999';
        const buttonText = (typeof getActionButtonText === 'function')
            ? getActionButtonText(phase)
            : 'Discover';

        return `
            <div class="youtube-playlist-card listenbrainz-playlist-card"
                 id="listenbrainz-sync-card-${escapeHtml(mbid)}"
                 data-lb-mbid="${escapeHtml(mbid)}"
                 data-lb-title="${escapeHtml(title)}">
                <div class="playlist-card-icon">🎧</div>
                <div class="playlist-card-content">
                    <div class="playlist-card-name">${escapeHtml(title)}</div>
                    <div class="playlist-card-info">
                        <span class="playlist-card-track-count">${count} tracks</span>
                        <span class="playlist-card-owner">by ${escapeHtml(creator)}</span>
                        <span class="playlist-card-phase-text" style="color: ${phaseColor};">${phaseText}</span>
                    </div>
                </div>
                <div class="playlist-card-progress ${phase === 'fresh' ? 'hidden' : ''}"></div>
                <button class="playlist-card-action-btn">${buttonText}</button>
            </div>
        `;
    }).join('');

    // Wire click handlers.
    container.querySelectorAll('.listenbrainz-playlist-card').forEach(card => {
        card.addEventListener('click', () => {
            const mbid = card.dataset.lbMbid;
            const title = card.dataset.lbTitle;
            handleListenBrainzSyncCardClick(mbid, title);
        });
    });

    // If the tab is currently visible, kick the refresh loop so cards
    // start showing live state immediately. ``_startLbSyncCardRefreshLoop``
    // is idempotent + self-stops when the tab loses focus.
    const tab = document.getElementById('listenbrainz-sync-tab-content');
    if (tab && tab.classList.contains('active')) {
        _startLbSyncCardRefreshLoop();
    }
}

async function handleListenBrainzSyncCardClick(playlistMbid, playlistTitle) {
    if (!playlistMbid) {
        if (typeof showToast === 'function') showToast('Missing playlist ID', 'error');
        return;
    }

    // The Discover-page LB flow expects ``listenbrainzTracksCache[mbid]``
    // to be populated before opening the modal — it pulls tracks from
    // there when constructing the discovery state. On the Sync tab the
    // user may click an LB card without ever visiting Discover, so we
    // fetch + cache the tracks on demand here.
    try {
        if (typeof showLoadingOverlay === 'function') {
            showLoadingOverlay(`Loading ${playlistTitle}...`);
        }

        if (typeof listenbrainzTracksCache === 'undefined') {
            window.listenbrainzTracksCache = {};
        }
        let tracks = listenbrainzTracksCache[playlistMbid];
        if (!tracks || tracks.length === 0) {
            const resp = await fetch(`/api/discover/listenbrainz/playlist/${encodeURIComponent(playlistMbid)}`);
            if (!resp.ok) {
                throw new Error(`Failed to load playlist tracks (${resp.status})`);
            }
            const data = await resp.json();
            tracks = (data.tracks || []).map(t => ({
                track_name: t.track_name || '',
                artist_name: t.artist_name || '',
                album_name: t.album_name || '',
                duration_ms: t.duration_ms || 0,
                mbid: t.recording_mbid || t.mbid || '',
                release_mbid: t.release_mbid || '',
                album_cover_url: t.album_cover_url || '',
            }));
            listenbrainzTracksCache[playlistMbid] = tracks;
        }

        if (!tracks || tracks.length === 0) {
            throw new Error('Playlist has no tracks');
        }

        if (typeof hideLoadingOverlay === 'function') hideLoadingOverlay();

        // Hand off to the existing Discover-page modal opener. It owns
        // state init, discovery kickoff, polling, and the sync→mirror
        // step. The Sync tab is just a different entry point.
        if (typeof openDownloadModalForListenBrainzPlaylist === 'function') {
            await openDownloadModalForListenBrainzPlaylist(playlistMbid, playlistTitle);
        } else {
            throw new Error('LB discovery modal not available — discover.js may be missing');
        }
    } catch (err) {
        if (typeof hideLoadingOverlay === 'function') hideLoadingOverlay();
        console.error('Error opening LB playlist from Sync tab:', err);
        if (typeof showToast === 'function') {
            showToast(`Could not open playlist: ${err.message}`, 'error');
        }
    }
}

// Live card refresh — keeps the Sync-tab cards in sync with the
// canonical ``listenbrainzPlaylistStates`` dict that the discovery /
// sync polling loops own. Tidal does this via explicit
// ``updateTidalCardPhase`` / ``updateTidalCardProgress`` calls
// sprinkled through its polling code; we get the same UX with a
// single 500ms tick that reads the shared state. The loop only runs
// while the LB tab is the active Sync tab so it's cheap.

let _lbSyncCardRefreshInterval = null;

function _refreshOneLbSyncCard(card) {
    const mbid = card.dataset.lbMbid;
    if (!mbid) return;
    const state = (typeof listenbrainzPlaylistStates !== 'undefined')
        ? listenbrainzPlaylistStates[mbid] : null;
    if (!state) return;

    const phase = state.phase || 'fresh';
    const phaseEl = card.querySelector('.playlist-card-phase-text');
    if (phaseEl) {
        const text = (typeof getPhaseText === 'function') ? getPhaseText(phase) : phase;
        const color = (typeof getPhaseColor === 'function') ? getPhaseColor(phase) : '';
        if (phaseEl.textContent !== text) phaseEl.textContent = text;
        if (color) phaseEl.style.color = color;
    }

    const btnEl = card.querySelector('.playlist-card-action-btn');
    if (btnEl) {
        const btnText = (typeof getActionButtonText === 'function')
            ? getActionButtonText(phase) : btnEl.textContent;
        if (btnEl.textContent !== btnText) btnEl.textContent = btnText;
    }

    // Discovery progress mirrors Tidal's per-card text:
    // "♪ <total> / ✓ <matched> / ✗ <failed> / <percent>%".
    // During sync, swap to the sync progress payload the LB sync poller
    // writes into state.lastSyncProgress (same shape Tidal uses).
    const progEl = card.querySelector('.playlist-card-progress');
    if (!progEl) return;
    if (phase === 'fresh') {
        progEl.classList.add('hidden');
        progEl.textContent = '';
        return;
    }

    if ((phase === 'syncing' || phase === 'sync_complete') && state.lastSyncProgress) {
        const sp = state.lastSyncProgress;
        const matched = sp.matched_tracks || sp.spotify_matches || 0;
        const total = sp.total_tracks || sp.spotify_total || 0;
        const failed = (sp.failed_tracks !== undefined)
            ? sp.failed_tracks : Math.max(0, total - matched);
        const pct = total > 0 ? Math.round((matched / total) * 100) : 0;
        progEl.textContent = `♪ ${total} / ✓ ${matched} / ✗ ${failed} / ${pct}%`;
        progEl.classList.remove('hidden');
        return;
    }

    const total = state.spotify_total || state.spotifyTotal || 0;
    const matched = state.spotify_matches || state.spotifyMatches || 0;
    const failed = Math.max(0, total - matched);
    const pct = total > 0 ? Math.round((matched / total) * 100)
        : (state.discovery_progress || state.discoveryProgress || 0);
    progEl.textContent = `♪ ${total} / ✓ ${matched} / ✗ ${failed} / ${pct}%`;
    progEl.classList.remove('hidden');
}

function _refreshAllLbSyncCards() {
    document.querySelectorAll('#listenbrainz-sync-tab-content .listenbrainz-playlist-card')
        .forEach(_refreshOneLbSyncCard);
}

function _startLbSyncCardRefreshLoop() {
    if (_lbSyncCardRefreshInterval) return;
    _lbSyncCardRefreshInterval = setInterval(() => {
        const tab = document.getElementById('listenbrainz-sync-tab-content');
        if (!tab || !tab.classList.contains('active')) {
            _stopLbSyncCardRefreshLoop();
            return;
        }
        _refreshAllLbSyncCards();
    }, 500);
    // Initial tick so the user doesn't wait 500ms for the first update.
    _refreshAllLbSyncCards();
}

function _stopLbSyncCardRefreshLoop() {
    if (_lbSyncCardRefreshInterval) {
        clearInterval(_lbSyncCardRefreshInterval);
        _lbSyncCardRefreshInterval = null;
    }
}

// Sub-tab switching (For You / My Playlists / Collaborative).
function _initListenBrainzSyncSubTabs() {
    const subTabContainer = document.querySelector('#listenbrainz-sync-tab-content .listenbrainz-sub-tabs');
    if (!subTabContainer) return;
    subTabContainer.addEventListener('click', (e) => {
        const btn = e.target.closest('.listenbrainz-sub-tab-btn');
        if (!btn) return;
        const newType = btn.dataset.lbType;
        if (!newType || newType === _lbSyncCurrentType) return;

        subTabContainer.querySelectorAll('.listenbrainz-sub-tab-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');

        _lbSyncCurrentType = newType;
        renderListenBrainzSyncPlaylists();
    });
}

// Refresh button.
function _initListenBrainzSyncRefreshBtn() {
    const btn = document.getElementById('listenbrainz-sync-refresh-btn');
    if (!btn) return;
    btn.addEventListener('click', async () => {
        // Trigger backend refetch + re-render.
        try {
            await fetch('/api/discover/listenbrainz/refresh', { method: 'POST' });
        } catch (e) {
            // Non-fatal; we still re-load from the cache endpoints.
            console.warn('LB cache refresh failed (non-fatal):', e);
        }
        loadListenBrainzSyncPlaylists();
    });
}

// Bootstrap once when sync page DOM is ready. ``initializeSyncPage``
// runs at app boot; we hook our subtab + refresh listeners on top.
document.addEventListener('DOMContentLoaded', () => {
    _initListenBrainzSyncSubTabs();
    _initListenBrainzSyncRefreshBtn();
});
