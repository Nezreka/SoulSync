// ===================================================================
// SOULSYNC DISCOVERY SYNC TAB (Phase 1c.3)
// ===================================================================
// Surfaces the user's persisted SoulSync Discovery / personalized
// playlists (decade mixes, hidden gems, popular picks, daily mixes,
// discovery shuffle, etc.) as a Sync-page tab so they participate
// in the mirrored-playlist + Auto-Sync pipeline like every other
// source.
//
// Different shape from the LB / Last.fm tabs: personalized tracks
// already carry Spotify / iTunes / Deezer IDs (matched at generation
// time from the discovery pool), so there's no MB-style "needs
// discovery" hop. Click → refresh kind → grab tracks → mirror as
// ``source='soulsync_discovery'`` with the matched_data shape
// downstream consumers already expect from auto-discovered Spotify
// mirrors.

let _soulsyncDiscoverySyncRecords = [];

async function loadSoulsyncDiscoverySyncPlaylists() {
    const container = document.getElementById('soulsync-discovery-sync-playlist-container');
    const refreshBtn = document.getElementById('soulsync-discovery-sync-refresh-btn');
    if (!container) return;

    container.innerHTML = `<div class="playlist-placeholder">🔄 Loading SoulSync Discovery playlists...</div>`;
    if (refreshBtn) {
        refreshBtn.disabled = true;
        refreshBtn.textContent = '🔄 Loading...';
    }

    try {
        const resp = await fetch('/api/personalized/playlists');
        const data = await resp.json();
        if (!data.success) {
            container.innerHTML = `<div class="playlist-placeholder">❌ ${escapeHtml(data.error || 'Failed to load')}</div>`;
            return;
        }
        _soulsyncDiscoverySyncRecords = data.playlists || [];
        renderSoulsyncDiscoverySyncPlaylists();
        console.log(`✨ SoulSync Discovery Sync tab loaded: ${_soulsyncDiscoverySyncRecords.length} playlists`);
    } catch (err) {
        container.innerHTML = `<div class="playlist-placeholder">❌ Error: ${err.message}</div>`;
        if (typeof showToast === 'function') {
            showToast(`Error loading SoulSync Discovery: ${err.message}`, 'error');
        }
    } finally {
        if (refreshBtn) {
            refreshBtn.disabled = false;
            refreshBtn.textContent = '🔄 Refresh';
        }
    }
}

function renderSoulsyncDiscoverySyncPlaylists() {
    const container = document.getElementById('soulsync-discovery-sync-playlist-container');
    if (!container) return;

    if (_soulsyncDiscoverySyncRecords.length === 0) {
        container.innerHTML = `<div class="playlist-placeholder">No SoulSync Discovery playlists yet. Open the Discover page and generate a few personalized playlists first.</div>`;
        return;
    }

    container.innerHTML = _soulsyncDiscoverySyncRecords.map(p => {
        const syntheticId = _soulsyncSyntheticId(p.kind, p.variant);
        const title = p.name || `${p.kind} ${p.variant || ''}`.trim();
        const subtitle = p.variant ? `${p.kind} · ${p.variant}` : p.kind;
        const count = p.track_count || 0;
        const stale = !!p.is_stale;
        const stalenessText = stale ? 'Stale — refresh to regenerate' : 'Ready';
        const stalenessColor = stale ? '#facc15' : '#14b8a6';

        return `
            <div class="youtube-playlist-card soulsync-discovery-playlist-card"
                 id="soulsync-discovery-sync-card-${escapeHtml(syntheticId)}"
                 data-ssd-kind="${escapeHtml(p.kind)}"
                 data-ssd-variant="${escapeHtml(p.variant || '')}"
                 data-ssd-id="${escapeHtml(syntheticId)}"
                 data-ssd-name="${escapeHtml(title)}">
                <div class="playlist-card-icon">✨</div>
                <div class="playlist-card-content">
                    <div class="playlist-card-name">${escapeHtml(title)}</div>
                    <div class="playlist-card-info">
                        <span class="playlist-card-track-count">${count} tracks</span>
                        <span class="playlist-card-owner">${escapeHtml(subtitle)}</span>
                        <span class="playlist-card-phase-text" style="color: ${stalenessColor};">${stalenessText}</span>
                    </div>
                </div>
                <div class="playlist-card-progress hidden"></div>
                <button class="playlist-card-action-btn">Refresh & Mirror</button>
            </div>
        `;
    }).join('');

    container.querySelectorAll('.soulsync-discovery-playlist-card').forEach(card => {
        card.addEventListener('click', () => {
            const kind = card.dataset.ssdKind;
            const variant = card.dataset.ssdVariant;
            const name = card.dataset.ssdName;
            handleSoulsyncDiscoverySyncCardClick(kind, variant, name, card);
        });
    });
}

function _soulsyncSyntheticId(kind, variant) {
    // Synthetic stable id keyed on (kind, variant) so re-refreshes UPSERT
    // the same mirror row instead of duplicating. Empty variant collapses
    // cleanly (e.g. hidden_gems with no variant -> "ssd_hidden_gems").
    return `ssd_${kind}${variant ? `_${variant}` : ''}`;
}

async function handleSoulsyncDiscoverySyncCardClick(kind, variant, name, cardEl) {
    if (!kind) {
        if (typeof showToast === 'function') showToast('Missing kind', 'error');
        return;
    }
    const btn = cardEl ? cardEl.querySelector('.playlist-card-action-btn') : null;
    const progEl = cardEl ? cardEl.querySelector('.playlist-card-progress') : null;
    if (btn) {
        btn.disabled = true;
        btn.textContent = 'Refreshing…';
    }
    if (progEl) progEl.classList.remove('hidden');

    try {
        // Trigger the kind's generator and grab fresh tracks.
        const url = variant
            ? `/api/personalized/playlist/${encodeURIComponent(kind)}/${encodeURIComponent(variant)}/refresh`
            : `/api/personalized/playlist/${encodeURIComponent(kind)}/refresh`;
        const resp = await fetch(url, { method: 'POST' });
        const data = await resp.json();
        if (!data.success) {
            throw new Error(data.error || 'Generator failed');
        }

        const rec = data.playlist || {};
        const tracks = data.tracks || [];
        const finalName = rec.name || name || `${kind} ${variant || ''}`.trim();
        const syntheticId = _soulsyncSyntheticId(kind, variant);

        if (tracks.length === 0) {
            if (typeof showToast === 'function') {
                showToast(`'${finalName}' generated 0 tracks. Try widening the playlist's config in Discover.`, 'warning');
            }
        }

        // Project each track into the mirrorPlaylist contract. Tracks
        // already carry provider IDs from the discovery pool, so the
        // matched_data block is filled inline — no separate discovery
        // worker pass needed.
        const mirrorTracks = tracks.map(t => {
            const trackId = t.spotify_track_id || t.itunes_track_id || t.deezer_track_id || '';
            const provider = t.spotify_track_id ? 'spotify'
                : (t.itunes_track_id ? 'itunes'
                : (t.deezer_track_id ? 'deezer' : (t.source || 'unknown')));
            const albumObj = { name: t.album_name || '' };
            if (t.album_cover_url) {
                albumObj.images = [{ url: t.album_cover_url, height: 600, width: 600 }];
            }
            const extra = trackId ? JSON.stringify({
                discovered: true,
                provider,
                confidence: 1.0,
                matched_data: {
                    id: trackId,
                    name: t.track_name || '',
                    artists: [{ name: t.artist_name || '' }],
                    album: albumObj,
                    duration_ms: t.duration_ms || 0,
                    image_url: t.album_cover_url || null,
                    source: provider,
                },
            }) : null;
            return {
                track_name: t.track_name || '',
                artist_name: t.artist_name || '',
                album_name: t.album_name || '',
                duration_ms: t.duration_ms || 0,
                image_url: t.album_cover_url || null,
                source_track_id: trackId,
                extra_data: extra,
            };
        });

        // POST inline so we can capture the returned mirrored_playlists
        // row id and open the detail modal afterward. ``mirrorPlaylist``
        // (in stats-automations.js) is fire-and-forget and doesn't
        // surface the id, which the next step needs.
        const normalizedTracks = mirrorTracks.map(t => ({
            track_name: t.track_name || '',
            artist_name: t.artist_name || '',
            album_name: t.album_name || '',
            duration_ms: t.duration_ms || 0,
            image_url: t.image_url || null,
            source_track_id: t.source_track_id || '',
            extra_data: t.extra_data || null,
        }));
        const mirrorResp = await fetch('/api/mirror-playlist', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                source: 'soulsync_discovery',
                source_playlist_id: syntheticId,
                name: finalName,
                tracks: normalizedTracks,
                description: `Personalized ${kind}${variant ? ' · ' + variant : ''} — regenerates on Auto-Sync refresh.`,
                owner: 'SoulSync',
                image_url: '',
            }),
        });
        const mirrorData = await mirrorResp.json();
        if (!mirrorData.success) {
            throw new Error(mirrorData.error || 'Mirror creation failed');
        }
        const mirroredId = mirrorData.playlist_id;

        if (progEl) {
            progEl.textContent = `♪ ${tracks.length} / ✓ ${mirrorTracks.length} / mirrored`;
        }
        if (btn) {
            btn.disabled = false;
            btn.textContent = 'Refresh & Mirror';
        }

        // Update the in-memory record so the card displays the new count.
        const idx = _soulsyncDiscoverySyncRecords.findIndex(
            r => r.kind === kind && (r.variant || '') === (variant || '')
        );
        if (idx >= 0) {
            _soulsyncDiscoverySyncRecords[idx] = {
                ..._soulsyncDiscoverySyncRecords[idx],
                ...rec,
                track_count: tracks.length,
                is_stale: false,
            };
        }

        if (typeof showToast === 'function') {
            showToast(`Mirrored '${finalName}' with ${mirrorTracks.length} tracks`, 'success');
        }

        // Open the mirrored-playlist detail modal so the user lands on
        // the tracks view + can trigger sync / download from there.
        // Same flow the Mirrored tab uses when clicking a row.
        if (mirroredId && typeof openMirroredPlaylistModal === 'function') {
            try {
                await openMirroredPlaylistModal(mirroredId);
            } catch (e) {
                console.warn('Could not open mirrored playlist detail:', e);
            }
        }
    } catch (err) {
        if (btn) {
            btn.disabled = false;
            btn.textContent = 'Refresh & Mirror';
        }
        if (typeof showToast === 'function') {
            showToast(`Refresh failed: ${err.message}`, 'error');
        }
        console.error('SoulSync Discovery refresh failed:', err);
    }
}

document.addEventListener('DOMContentLoaded', () => {
    const btn = document.getElementById('soulsync-discovery-sync-refresh-btn');
    if (btn) btn.addEventListener('click', loadSoulsyncDiscoverySyncPlaylists);
});
