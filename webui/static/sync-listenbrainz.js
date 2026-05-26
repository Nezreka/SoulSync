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
        const mbid = p.playlist_mbid || p.id;
        const title = p.title || p.name || 'ListenBrainz Playlist';
        const creator = p.creator || 'ListenBrainz';
        const count = p.track_count || 0;
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

// Sub-tab switching (For You / My Playlists / Collaborative).
function _initListenBrainzSyncSubTabs() {
    const subTabContainer = document.querySelector('#listenbrainz-tab-content .listenbrainz-sub-tabs');
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
