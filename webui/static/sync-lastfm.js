// ===================================================================
// LAST.FM RADIO SYNC TAB
// ===================================================================
// Phase 1c.2 of the Discover-to-Sync unification. Surfaces the user's
// generated Last.fm Radio playlists as a Sync-page tab so they can be
// discovered + mirrored alongside ListenBrainz, Tidal, Qobuz, etc.
//
// Last.fm Radio playlists live in the same ``listenbrainz_playlists``
// SQLite table as ListenBrainz playlists (with
// ``playlist_type='lastfm_radio'``) and run through the same
// ``openDownloadModalForListenBrainzPlaylist`` discovery flow. So this
// module is intentionally thin — list + render + click handoff.
// The refresh loop, discovery polling, sync→mirror creation, and the
// modal itself are all shared with the ListenBrainz tab.
//
// New Last.fm radios are GENERATED from the Discover page (with a
// seed track). This tab is for listing existing radios + syncing
// them to a mirror — not for generation.

let _lastfmSyncPlaylists = [];

async function loadLastfmSyncPlaylists() {
    const container = document.getElementById('lastfm-sync-playlist-container');
    const refreshBtn = document.getElementById('lastfm-sync-refresh-btn');
    if (!container) return;

    container.innerHTML = `<div class="playlist-placeholder">🔄 Loading Last.fm Radio playlists...</div>`;
    if (refreshBtn) {
        refreshBtn.disabled = true;
        refreshBtn.textContent = '🔄 Loading...';
    }

    try {
        const resp = await fetch('/api/discover/listenbrainz/lastfm-radio');
        const data = await resp.json();
        if (!data.success && data.error) {
            container.innerHTML = `<div class="playlist-placeholder">❌ ${escapeHtml(data.error)}</div>`;
            return;
        }
        _lastfmSyncPlaylists = data.playlists || [];
        renderLastfmSyncPlaylists();
        console.log(`📻 Last.fm Sync tab loaded: ${_lastfmSyncPlaylists.length} radios`);
    } catch (err) {
        container.innerHTML = `<div class="playlist-placeholder">❌ Error loading Last.fm radios: ${err.message}</div>`;
        if (typeof showToast === 'function') {
            showToast(`Error loading Last.fm radios: ${err.message}`, 'error');
        }
    } finally {
        if (refreshBtn) {
            refreshBtn.disabled = false;
            refreshBtn.textContent = '🔄 Refresh';
        }
    }
}

function renderLastfmSyncPlaylists() {
    const container = document.getElementById('lastfm-sync-playlist-container');
    if (!container) return;

    if (_lastfmSyncPlaylists.length === 0) {
        container.innerHTML = `<div class="playlist-placeholder">No Last.fm Radio playlists yet. Generate one from the Discover page by picking a seed track.</div>`;
        return;
    }

    container.innerHTML = _lastfmSyncPlaylists.map(p => {
        const inner = p.playlist || p;
        const mbid = (inner.identifier || '').split('/').pop() || inner.id || '';
        const title = inner.title || 'Last.fm Radio';
        const creator = inner.creator || 'Last.fm';
        let count = 0;
        if (inner.track_count) count = inner.track_count;
        else if (inner.annotation && inner.annotation.track_count) count = inner.annotation.track_count;
        else if (Array.isArray(inner.track) && inner.track.length > 0) count = inner.track.length;

        const state = (typeof listenbrainzPlaylistStates !== 'undefined'
            && listenbrainzPlaylistStates[mbid]) || null;
        const phase = state && state.phase ? state.phase : 'fresh';
        const phaseText = (typeof getPhaseText === 'function')
            ? getPhaseText(phase) : (phase === 'fresh' ? 'Ready to discover' : phase);
        const phaseColor = (typeof getPhaseColor === 'function')
            ? getPhaseColor(phase) : '#999';
        const buttonText = (typeof getActionButtonText === 'function')
            ? getActionButtonText(phase) : 'Discover';

        return `
            <div class="youtube-playlist-card lastfm-playlist-card"
                 id="lastfm-sync-card-${escapeHtml(mbid)}"
                 data-lb-mbid="${escapeHtml(mbid)}"
                 data-lb-title="${escapeHtml(title)}">
                <div class="playlist-card-icon">📻</div>
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

    if (typeof wirePhaseSyncCards === 'function') {
        wirePhaseSyncCards(
            'lastfm-sync',
            '#lastfm-sync-playlist-container',
            '.lastfm-playlist-card',
            card => card.dataset.lbMbid,
            mbid => {
                const c = document.querySelector(`#lastfm-sync-card-${CSS.escape(mbid)}`);
                if (typeof handleListenBrainzSyncCardClick === 'function') {
                    handleListenBrainzSyncCardClick(mbid, c?.dataset.lbTitle || '');
                }
            }
        );
    }

    // Reuse the shared refresh loop from sync-listenbrainz.js — it
    // already iterates Last.fm cards alongside LB cards.
    if (typeof _startLbSyncCardRefreshLoop === 'function') {
        const tab = document.getElementById('lastfm-sync-tab-content');
        if (tab && tab.classList.contains('active')) {
            _startLbSyncCardRefreshLoop();
        }
    }
}

document.addEventListener('DOMContentLoaded', () => {
    const btn = document.getElementById('lastfm-sync-refresh-btn');
    if (btn) btn.addEventListener('click', loadLastfmSyncPlaylists);
});
