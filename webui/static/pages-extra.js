// ==================================================================================
// DASHBOARD — Recent Syncs Section
// ==================================================================================

// ==================================================================================
// SERVER PLAYLIST MANAGER — Sync Page Server Tab
// ==================================================================================

let _serverPlaylists = [];
let _serverEditorState = { playlistId: null, playlistName: '', tracks: [] };

async function loadServerPlaylists() {
    const container = document.getElementById('server-playlist-container');
    const editor = document.getElementById('server-editor');
    const btn = document.getElementById('server-refresh-btn');

    if (editor) editor.style.display = 'none';
    if (container) container.style.display = '';
    if (btn) { btn.disabled = true; btn.textContent = '🔄 Loading...'; }

    // Show skeleton loader
    if (container) {
        container.innerHTML = `<div class="server-pl-grid">${Array.from({ length: 6 }, (_, i) => `
            <div class="server-pl-card server-pl-skeleton" style="animation-delay: ${i * 0.06}s">
                <div class="server-pl-card-top">
                    <div class="skeleton-box" style="width:44px;height:44px;border-radius:12px"></div>
                    <div class="skeleton-box" style="width:28px;height:28px;border-radius:8px"></div>
                </div>
                <div class="server-pl-card-body">
                    <div class="skeleton-box" style="width:${60 + Math.random() * 30}%;height:14px;border-radius:4px;margin-bottom:8px"></div>
                    <div class="skeleton-box" style="width:40%;height:11px;border-radius:4px"></div>
                </div>
                <div class="server-pl-card-footer" style="border-top:1px solid rgba(255,255,255,0.05);padding-top:12px">
                    <div class="skeleton-box" style="width:60px;height:10px;border-radius:3px"></div>
                </div>
            </div>`).join('')}</div>`;
    }

    try {
        // Fetch server playlists, mirrored playlists, and sync history names in parallel
        const [serverRes, mirroredRes, historyNamesRes] = await Promise.all([
            fetch('/api/server/playlists'),
            fetch('/api/mirrored-playlists'),
            fetch('/api/sync/history/names'),
        ]);
        const data = await serverRes.json();
        let mirroredAll = [];
        try { mirroredAll = await mirroredRes.json(); } catch (_) { }
        if (!Array.isArray(mirroredAll)) mirroredAll = [];
        let historyNames = [];
        try { historyNames = await historyNamesRes.json(); } catch (_) { }
        if (!Array.isArray(historyNames)) historyNames = [];

        if (!data.success || !data.playlists) {
            if (container) container.innerHTML = `<div class="playlist-placeholder">${data.error || 'Could not load server playlists'}</div>`;
            return;
        }

        // Separate synced vs non-synced playlists
        const mirroredNames = new Set(mirroredAll.map(p => p.name.trim().toLowerCase()));
        const syncedNames = new Set(historyNames.map(n => n.trim().toLowerCase()));
        const synced = [];
        const unsynced = [];
        for (const pl of data.playlists) {
            const key = pl.name.trim().toLowerCase();
            if (mirroredNames.has(key) || syncedNames.has(key)) {
                pl._synced = true;
                synced.push(pl);
            } else {
                pl._synced = false;
                unsynced.push(pl);
            }
        }

        _serverPlaylists = [...synced, ...unsynced];
        const title = document.getElementById('server-tab-title');
        const serverName = data.server_type ? data.server_type.charAt(0).toUpperCase() + data.server_type.slice(1) : '';
        if (title) title.textContent = `Server Playlists (${serverName})`;

        if (synced.length === 0 && unsynced.length === 0) {
            if (container) container.innerHTML = '<div class="playlist-placeholder">No playlists found on your media server.</div>';
            return;
        }

        // Server type icon SVG
        const serverIcons = {
            plex: '<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M11.643 0H4.68l7.679 12L4.68 24h6.963L19.32 12z"/></svg>',
            jellyfin: '<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M12 2C8.5 2 6 5.1 6 9c0 2.4 1.2 5.5 3.3 8.7.7 1 1.5 2 2.2 2.9.2.3.4.3.5.4.1 0 .3-.1.5-.4.7-.9 1.5-1.9 2.2-2.9C16.8 14.5 18 11.4 18 9c0-3.9-2.5-7-6-7z"/></svg>',
            navidrome: '<svg viewBox="0 0 24 24" width="18" height="18" fill="currentColor"><path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/></svg>'
        };
        const sIcon = serverIcons[data.server_type] || serverIcons.plex;

        function _renderPlCard(pl, i, isSynced) {
            const hue = (i * 37 + 200) % 360;
            const safeName = _esc(pl.name).replace(/'/g, "\\'");
            const cardClass = isSynced ? 'server-pl-card' : 'server-pl-card server-pl-unsynced';
            const action = isSynced ? 'Open Editor' : 'View Tracks';
            return `
            <div class="${cardClass}" onclick="openServerPlaylistEditor('${pl.id}', '${safeName}')" style="animation-delay: ${i * 0.04}s; --card-hue: ${hue}">
                <div class="server-pl-card-glow"></div>
                <div class="server-pl-card-top">
                    <div class="server-pl-card-icon-wrap">
                        <div class="server-pl-card-bars">
                            <span></span><span></span><span></span><span></span>
                        </div>
                    </div>
                    <div class="server-pl-card-badge">${sIcon}</div>
                </div>
                <div class="server-pl-card-body">
                    <div class="server-pl-card-name">${_esc(pl.name)}</div>
                    <div class="server-pl-card-meta">
                        <span class="server-pl-track-count">${pl.track_count}</span> tracks
                        ${isSynced ? '<span class="server-pl-synced-badge">Synced</span>' : ''}
                    </div>
                </div>
                <div class="server-pl-card-footer">
                    <span class="server-pl-card-action">${action}</span>
                    <span class="server-pl-card-arrow">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M12 5l7 7-7 7"/></svg>
                    </span>
                </div>
            </div>`;
        }

        let html = '';

        if (synced.length > 0) {
            html += `<div class="server-pl-section">
                <div class="server-pl-section-header">
                    <span class="server-pl-section-icon">&#128279;</span>
                    <span class="server-pl-section-title">Synced Playlists</span>
                    <span class="server-pl-section-count">${synced.length}</span>
                </div>
                <div class="server-pl-grid">${synced.map((pl, i) => _renderPlCard(pl, i, true)).join('')}</div>
            </div>`;
        }

        if (unsynced.length > 0) {
            html += `<div class="server-pl-section server-pl-section-unsynced">
                <div class="server-pl-section-header">
                    <span class="server-pl-section-icon">&#127925;</span>
                    <span class="server-pl-section-title">Other Server Playlists</span>
                    <span class="server-pl-section-count">${unsynced.length}</span>
                </div>
                <div class="server-pl-grid">${unsynced.map((pl, i) => _renderPlCard(pl, i + synced.length, false)).join('')}</div>
            </div>`;
        }

        container.innerHTML = html;

    } catch (e) {
        if (container) container.innerHTML = `<div class="playlist-placeholder">Error: ${e.message}</div>`;
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = '🔄 Refresh'; }
    }
}

async function openServerPlaylistEditor(playlistId, playlistName) {
    // Step 1: Look up mirrored playlists by name
    let mirroredPlaylists = [];
    try {
        const res = await fetch('/api/mirrored-playlists');
        const all = await res.json();
        mirroredPlaylists = (Array.isArray(all) ? all : []).filter(p =>
            p.name.trim().toLowerCase() === playlistName.trim().toLowerCase()
        );
    } catch (e) {
        console.error('Failed to fetch mirrored playlists:', e);
    }

    if (mirroredPlaylists.length === 1) {
        // Single match — go straight to compare
        _openServerCompareView(playlistId, playlistName, mirroredPlaylists[0]);
    } else if (mirroredPlaylists.length === 0) {
        // No match — server-only view
        _openServerCompareView(playlistId, playlistName, null);
    } else {
        // Multiple — disambiguation
        _showServerDisambig(playlistId, playlistName, mirroredPlaylists);
    }
}

// ── Disambiguation ──

function _showServerDisambig(playlistId, playlistName, candidates) {
    const overlay = document.getElementById('server-disambig-overlay');
    const list = document.getElementById('server-disambig-list');
    const subtitle = document.getElementById('server-disambig-subtitle');
    if (!overlay || !list) return;

    if (subtitle) subtitle.textContent = `"${playlistName}" was found on ${candidates.length} sources. Which one do you want to compare against?`;

    const sourceIcons = { spotify: '🟢', tidal: '🌊', youtube: '▶️', beatport: '🎛️', deezer: '🟣', file: '📄' };

    list.innerHTML = candidates.map((p, i) => {
        const icon = sourceIcons[p.source] || '📋';
        const ago = timeAgo(p.mirrored_at || p.updated_at);
        return `
        <div class="server-disambig-card" onclick="selectDisambigPlaylist('${playlistId}', '${_esc(playlistName).replace(/'/g, "\\'")}', ${p.id})" style="animation-delay: ${i * 0.06}s">
            <div class="server-disambig-icon">${icon}</div>
            <div class="server-disambig-info">
                <div class="server-disambig-name">${_esc(p.name)}</div>
                <div class="server-disambig-details">
                    <span class="source-badge">${_esc(p.source)}</span>
                    <span>${p.track_count || 0} tracks</span>
                    ${p.owner ? `<span>by ${_esc(p.owner)}</span>` : ''}
                    <span>Mirrored ${ago}</span>
                </div>
            </div>
            <div class="server-disambig-arrow">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M12 5l7 7-7 7"/></svg>
            </div>
        </div>`;
    }).join('');

    overlay.classList.remove('hidden');
    requestAnimationFrame(() => overlay.classList.add('visible'));

    // Escape key + click backdrop to close
    overlay.onclick = e => { if (e.target === overlay) closeServerDisambig(); };
    window._disambigEsc = e => { if (e.key === 'Escape') closeServerDisambig(); };
    document.addEventListener('keydown', window._disambigEsc);
}

function closeServerDisambig() {
    const overlay = document.getElementById('server-disambig-overlay');
    if (overlay) {
        overlay.classList.remove('visible');
        setTimeout(() => overlay.classList.add('hidden'), 250);
    }
    if (window._disambigEsc) { document.removeEventListener('keydown', window._disambigEsc); window._disambigEsc = null; }
}

async function selectDisambigPlaylist(playlistId, playlistName, mirroredId) {
    closeServerDisambig();
    try {
        const res = await fetch(`/api/mirrored-playlists/${mirroredId}`);
        const mirrored = await res.json();
        _openServerCompareView(playlistId, playlistName, mirrored);
    } catch (e) {
        showToast('Failed to load mirrored playlist: ' + e.message, 'error');
    }
}

// ── Compare View ──

async function _openServerCompareView(playlistId, playlistName, mirroredPlaylist) {
    const container = document.getElementById('server-playlist-container');
    const editor = document.getElementById('server-editor');
    if (!editor) return;

    if (container) container.style.display = 'none';
    editor.style.display = '';

    const nameEl = document.getElementById('server-editor-name');
    const metaEl = document.getElementById('server-editor-meta');
    const banner = document.getElementById('server-no-source-banner');
    const sourceScroll = document.getElementById('server-col-source-scroll');
    const serverScroll = document.getElementById('server-col-server-scroll');

    if (nameEl) nameEl.textContent = playlistName;
    if (metaEl) metaEl.textContent = 'Loading comparison...';
    if (banner) banner.style.display = 'none';
    if (sourceScroll) sourceScroll.innerHTML = '<div style="text-align:center;padding:30px;color:rgba(255,255,255,0.2);font-size:12px">Loading...</div>';
    if (serverScroll) serverScroll.innerHTML = '<div style="text-align:center;padding:30px;color:rgba(255,255,255,0.2);font-size:12px">Loading...</div>';

    // Store state
    _serverEditorState = {
        playlistId,
        playlistName,
        mirroredPlaylist,
        tracks: [],
    };

    // Build API URL
    let url = `/api/server/playlist/${playlistId}/tracks?name=${encodeURIComponent(playlistName)}`;
    if (mirroredPlaylist && mirroredPlaylist.id) {
        url += `&mirrored_playlist_id=${mirroredPlaylist.id}`;
    }

    try {
        const response = await fetch(url);
        const data = await response.json();
        if (!data.success) {
            if (metaEl) metaEl.textContent = data.error || 'Failed to load';
            return;
        }

        _serverEditorState.tracks = data.tracks || [];
        _serverEditorState.serverType = data.server_type;
        // Order status: the columns render in SOURCE order, so a same-tracks-but-
        // reordered server playlist looks in-sync. order_status flags that drift;
        // server_order is the server's ACTUAL sequence for the read-only view.
        _serverEditorState.orderStatus = data.order_status || null;
        _serverEditorState.serverOrder = data.server_order || [];

        const tracks = _serverEditorState.tracks;
        const serverLabel = data.server_type ? data.server_type.charAt(0).toUpperCase() + data.server_type.slice(1) : 'Server';

        // Header metadata
        if (metaEl) metaEl.textContent = `${serverLabel} · ${data.server_track_count || 0} server tracks · ${data.source_track_count || 0} source tracks`;

        // Show no-source banner if needed
        if (!mirroredPlaylist && banner) {
            banner.style.display = '';
        }

        // Stats, filter counts, footer
        _updateCompareStats(tracks);

        // Column headers
        const sourceLabel = mirroredPlaylist ? (mirroredPlaylist.source || 'source').charAt(0).toUpperCase() + (mirroredPlaylist.source || 'source').slice(1) : 'Source';
        const sourceIconMap = { spotify: '🟢', tidal: '🌊', youtube: '▶️', beatport: '🎛️', deezer: '🟣', file: '📄' };
        const serverIconMap = { plex: '🟠', jellyfin: '🟣', navidrome: '🔵' };

        const srcIconEl = document.getElementById('server-col-source-icon');
        const srcLabelEl = document.getElementById('server-col-source-label');
        const srcCountEl = document.getElementById('server-col-source-count');
        const svrIconEl = document.getElementById('server-col-server-icon');
        const svrLabelEl = document.getElementById('server-col-server-label');
        const svrCountEl = document.getElementById('server-col-server-count');

        if (srcIconEl) srcIconEl.textContent = mirroredPlaylist ? (sourceIconMap[mirroredPlaylist.source] || '📋') : '📋';
        if (srcLabelEl) srcLabelEl.textContent = sourceLabel;
        if (srcCountEl) srcCountEl.textContent = `${data.source_track_count || 0} tracks`;
        if (svrIconEl) svrIconEl.textContent = serverIconMap[data.server_type] || '💻';
        if (svrLabelEl) svrLabelEl.textContent = serverLabel;
        if (svrCountEl) {
            const os = _serverEditorState.orderStatus;
            if (os && os.out_of_order) {
                // Accurate membership but different order than the source. Read-only:
                // source order is the source of truth; the badge opens the real order.
                svrCountEl.innerHTML = `${data.server_track_count || 0} tracks ` +
                    `<button type="button" class="server-order-badge" onclick="_showServerOrder()" ` +
                    `title="These tracks match the source, but the playlist is in a different order on ${serverLabel}. Click to view the actual server order.">` +
                    `&#9888; out of order</button>`;
            } else {
                svrCountEl.textContent = `${data.server_track_count || 0} tracks`;
            }
        }

        // Render columns — re-applying the active filter pill so the rows always
        // agree with it (a reload used to reset the rows to "all" while the
        // Missing pill stayed selected — #1005).
        _renderCompareColumns(tracks);
        _applyServerEditorFilter(_activeServerEditorFilter());

        // Scroll linking
        _setupScrollLinking();

    } catch (e) {
        if (metaEl) metaEl.textContent = 'Error: ' + e.message;
    }
}

function _updateCompareStats(tracks) {
    const matched = tracks.filter(t => t.match_status === 'matched').length;
    const missing = tracks.filter(t => t.match_status === 'missing').length;
    const extra = tracks.filter(t => t.match_status === 'extra').length;

    const statsEl = document.getElementById('server-editor-stats');
    if (statsEl) {
        statsEl.innerHTML = `
            <div class="server-editor-stat"><div class="server-editor-stat-num matched">${matched}</div><div class="server-editor-stat-label">Matched</div></div>
            <div class="server-editor-stat"><div class="server-editor-stat-num missing">${missing}</div><div class="server-editor-stat-label">Missing</div></div>
            ${extra > 0 ? `<div class="server-editor-stat"><div class="server-editor-stat-num extra">${extra}</div><div class="server-editor-stat-label">Extra</div></div>` : ''}
        `;
    }

    const editor = document.getElementById('server-editor');
    if (editor) {
        editor.querySelectorAll('.discog-filter').forEach(btn => {
            const f = btn.dataset.filter;
            if (f === 'all') btn.textContent = `All (${tracks.length})`;
            else if (f === 'matched') btn.textContent = `Matched (${matched})`;
            else if (f === 'missing') btn.textContent = `Missing (${missing})`;
            else if (f === 'extra') btn.textContent = `Extra (${extra})`;
        });
    }

    const footer = document.getElementById('server-editor-footer');
    if (footer) footer.textContent = `${matched}/${matched + missing} matched${extra > 0 ? ` · ${extra} extra on server` : ''}`;
}

// Read-only view of the server playlist's ACTUAL order. Source order is the source
// of truth; this just lets the user SEE how the server currently differs (no editing).
function _showServerOrder() {
    const esc = typeof _esc === 'function' ? _esc : s => s;
    const order = (_serverEditorState && _serverEditorState.serverOrder) || [];
    const serverType = (_serverEditorState && _serverEditorState.serverType) || 'server';
    const serverLabel = serverType.charAt(0).toUpperCase() + serverType.slice(1);

    document.getElementById('server-order-modal')?.remove();
    const rows = order.map((t, i) => {
        const art = t.thumb
            ? `<img class="server-order-art" src="${esc(t.thumb)}" alt="" loading="lazy" onerror="this.outerHTML='<div class=&quot;server-order-art server-order-art-ph&quot;>&#9835;</div>'">`
            : `<div class="server-order-art server-order-art-ph">&#9835;</div>`;
        return `
        <div class="server-order-row">
            <span class="server-order-num">${i + 1}</span>
            ${art}
            <div class="server-order-meta">
                <span class="server-order-title">${esc(t.title || 'Unknown')}</span>
                <span class="server-order-artist">${esc(t.artist || '')}</span>
            </div>
        </div>`;
    }).join('');

    // Align actions — reorder the server playlist to the source order. Two choices
    // for server-only "extra" tracks. Order-only: never adds the missing tracks
    // (that's the normal sync's job). Supported where reorder is implemented.
    const canAlign = serverType === 'navidrome' || serverType === 'plex' || serverType === 'jellyfin';
    const alignFoot = canAlign ? `
            <div class="server-order-foot">
                <div class="server-order-foot-label">Align this playlist to the source order</div>
                <div class="server-order-actions">
                    <button type="button" class="server-align-btn" onclick="_alignPlaylist(false)">
                        <span class="server-align-btn-t">Mirror source</span>
                        <span class="server-align-btn-d">reorder to match the source &middot; remove server-only tracks</span>
                    </button>
                    <button type="button" class="server-align-btn" onclick="_alignPlaylist(true)">
                        <span class="server-align-btn-t">Keep extras</span>
                        <span class="server-align-btn-d">reorder to match the source &middot; keep server-only tracks at the end</span>
                    </button>
                </div>
                <div class="server-order-foot-note">Missing tracks aren't added here &mdash; run a normal sync for those.</div>
            </div>` : '';

    const overlay = document.createElement('div');
    overlay.id = 'server-order-modal';
    overlay.className = 'server-order-overlay';
    overlay.innerHTML = `
        <div class="server-order-dialog" onclick="event.stopPropagation()">
            <div class="server-order-head">
                <div>
                    <div class="server-order-h1">${esc(serverLabel)} playlist order</div>
                    <div class="server-order-sub">the actual order on your server · source order stays the source of truth</div>
                </div>
                <button type="button" class="server-order-close" onclick="document.getElementById('server-order-modal').remove()">&times;</button>
            </div>
            <div class="server-order-list">${rows || '<div class="server-order-empty">No server tracks.</div>'}</div>
            ${alignFoot}
        </div>`;
    overlay.onclick = () => overlay.remove();
    document.body.appendChild(overlay);
}

// Align the server playlist's ORDER to the source (the "Align playlists" action).
// Sends the matched server-track ids in SOURCE order + the extras choice; the
// backend validates they're all in the playlist and rewrites. Order-only.
async function _alignPlaylist(keepExtras) {
    const st = _serverEditorState;
    if (!st || !st.playlistId) return;
    const matchedIds = (Array.isArray(st.tracks) ? st.tracks : [])
        .filter(t => t.match_status === 'matched' && t.server_track && t.server_track.id != null)
        .map(t => String(t.server_track.id));
    if (!matchedIds.length) {
        if (typeof showToast === 'function') showToast('Nothing to align', 'warning');
        return;
    }
    try {
        const resp = await fetch(`/api/server/playlist/${st.playlistId}/align`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                playlist_name: st.playlistName || '',
                matched_ids: matchedIds,
                keep_extras: !!keepExtras,
            }),
        });
        const data = await resp.json();
        if (data && data.success) {
            if (typeof showToast === 'function') showToast(`Playlist order aligned (${data.track_count} tracks)`, 'success');
            document.getElementById('server-order-modal')?.remove();
            _serverEditorRefresh();
        } else {
            if (typeof showToast === 'function') showToast((data && data.error) || 'Align failed', 'error');
        }
    } catch (e) {
        if (typeof showToast === 'function') showToast('Align failed: ' + e.message, 'error');
    }
}

function _formatDurationMs(ms) {
    if (!ms) return '';
    const s = Math.round(ms / 1000);
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

function _renderCompareColumns(tracks) {
    const sourceScroll = document.getElementById('server-col-source-scroll');
    const serverScroll = document.getElementById('server-col-server-scroll');
    if (!sourceScroll || !serverScroll) return;

    let sourceHTML = '';
    let serverHTML = '';

    tracks.forEach((t, i) => {
        const src = t.source_track;
        const svr = t.server_track;
        const status = t.match_status;
        const pairId = `pair-${i}`;

        // ── Source (left) column ──
        if (src) {
            const dur = _formatDurationMs(src.duration_ms);
            sourceHTML += `
            <div class="server-track-item ${status}" data-pair-id="${pairId}" data-index="${i}" data-status="${status}"
                 onclick="_compareTrackClick('source', ${i})">
                <div class="server-track-num">${src.position != null ? src.position : i + 1}</div>
                <div class="server-track-art">
                    ${src.image_url ? `<img src="${src.image_url}" alt="" loading="lazy">` : '<div class="server-track-art-empty"></div>'}
                </div>
                <div class="server-track-info">
                    <div class="server-track-title">${_esc(src.name)}</div>
                    <div class="server-track-artist">${_esc(src.artist || '')}</div>
                </div>
                <div class="server-track-duration">${dur}</div>
                <div class="server-track-status-dot"></div>
            </div>`;
        } else {
            // Extra track — no source
            sourceHTML += `
            <div class="server-track-item extra-gap" data-pair-id="${pairId}" data-index="${i}" data-status="${status}">
                <div class="server-track-empty-slot extra">
                    <span class="empty-slot-label">No source track</span>
                </div>
            </div>`;
        }

        // ── Server (right) column ──
        if (svr) {
            const dur = _formatDurationMs(svr.duration);
            const conf = t.confidence != null ? t.confidence : null;
            let confBadge = '';
            if (status === 'matched' && conf != null) {
                const pct = Math.round(conf * 100);
                const cls = pct >= 100 ? 'exact' : pct >= 90 ? 'high' : 'fuzzy';
                confBadge = `<span class="server-track-conf ${cls}" title="Title similarity">${pct}%</span>`;
            }
            serverHTML += `
            <div class="server-track-item ${status}" data-pair-id="${pairId}" data-index="${i}" data-status="${status}"
                 onclick="_compareTrackClick('server', ${i})">
                <div class="server-track-num">${i + 1}</div>
                <div class="server-track-art">
                    ${svr.thumb ? `<img src="${svr.thumb}" alt="" loading="lazy">` : '<div class="server-track-art-empty"></div>'}
                </div>
                <div class="server-track-info">
                    <div class="server-track-title">${_esc(svr.title)}</div>
                    <div class="server-track-artist">${_esc(svr.artist || '')}</div>
                </div>
                ${confBadge}
                <div class="server-track-duration">${dur}</div>
                <div class="server-track-actions">
                    ${status === 'matched' ? `<button class="server-track-swap-btn" onclick="event.stopPropagation(); serverSearchReplace(${i}, 'replace')" title="Swap for different version">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M7 16V4m0 0L3 8m4-4l4 4M17 8v12m0 0l4-4m-4 4l-4-4"/></svg>
                    </button>` : ''}
                    <button class="server-track-remove-btn" onclick="event.stopPropagation(); _serverRemoveTrack(${i}, '${svr.id}')" title="Remove from playlist">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M18 6L6 18M6 6l12 12"/></svg>
                    </button>
                </div>
                <div class="server-track-status-dot"></div>
            </div>`;
        } else {
            // Missing on server — clickable empty slot
            const hint = src ? `${src.artist || ''} — ${src.name}` : '';
            serverHTML += `
            <div class="server-track-item empty-slot-wrap" data-pair-id="${pairId}" data-index="${i}" data-status="${status}"
                 onclick="serverSearchReplace(${i}, 'add')">
                <div class="server-track-empty-slot missing">
                    <div class="empty-slot-icon">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
                    </div>
                    <span class="empty-slot-label">Find &amp; add</span>
                    <span class="empty-slot-hint">${_esc(hint)}</span>
                </div>
            </div>`;
        }
    });

    sourceScroll.innerHTML = sourceHTML;
    serverScroll.innerHTML = serverHTML;
}

function _setupScrollLinking() {
    const sourceScroll = document.getElementById('server-col-source-scroll');
    const serverScroll = document.getElementById('server-col-server-scroll');
    if (!sourceScroll || !serverScroll) return;

    // Remove old listeners to prevent accumulation on refresh
    if (window._serverScrollAC) window._serverScrollAC.abort();
    window._serverScrollAC = new AbortController();
    const signal = window._serverScrollAC.signal;

    let syncing = false;

    const syncScroll = (from, to) => {
        if (syncing) return;
        syncing = true;
        const maxFrom = from.scrollHeight - from.clientHeight;
        const maxTo = to.scrollHeight - to.clientHeight;
        if (maxFrom > 0 && maxTo > 0) {
            to.scrollTop = (from.scrollTop / maxFrom) * maxTo;
        }
        requestAnimationFrame(() => { syncing = false; });
    };

    sourceScroll.addEventListener('scroll', () => syncScroll(sourceScroll, serverScroll), { signal });
    serverScroll.addEventListener('scroll', () => syncScroll(serverScroll, sourceScroll), { signal });
}

function _compareTrackClick(side, index) {
    const otherSide = side === 'source' ? 'server' : 'source';
    const otherScroll = document.getElementById(`server-col-${otherSide}-scroll`);
    const pairId = `pair-${index}`;

    // Clear previous highlights
    document.querySelectorAll('.server-track-item.highlighted').forEach(el => el.classList.remove('highlighted'));

    // Highlight both paired items
    document.querySelectorAll(`[data-pair-id="${pairId}"]`).forEach(el => el.classList.add('highlighted'));

    // Scroll the OTHER column to show the paired item
    const target = otherScroll?.querySelector(`[data-pair-id="${pairId}"]`);
    if (target) {
        target.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
}

function _serverEditorRefresh() {
    _openServerCompareView(_serverEditorState.playlistId, _serverEditorState.playlistName, _serverEditorState.mirroredPlaylist);
}

/**
 * Export the currently-open server playlist as an M3U file. Takes the tracks
 * physically present ON the server (matched + extra) and reuses the shared M3U
 * writer, which resolves each to its real library file path (+ the configured
 * entry_base_path prefix) so media servers like Music Assistant can read it.
 * force:true bypasses the auto-save "m3u_export.enabled" gate — this is a manual
 * on-demand export.
 */
async function exportServerPlaylistM3U() {
    const st = _serverEditorState;
    const btn = document.getElementById('server-editor-export-btn');
    const tracks = (st && Array.isArray(st.tracks) ? st.tracks : [])
        .filter(t => t.server_track)
        .map(t => ({
            name: t.server_track.title,
            artist: t.server_track.artist || '',
            duration_ms: t.server_track.duration || 0,
        }));
    if (!tracks.length) {
        showToast('No server tracks to export', 'warning');
        return;
    }
    const orig = btn ? btn.textContent : '';
    if (btn) { btn.disabled = true; btn.textContent = '⏳ Exporting…'; }
    try {
        const res = await fetch('/api/generate-playlist-m3u', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                playlist_name: st.playlistName || 'Playlist',
                tracks,
                context_type: 'playlist',
                save_to_disk: true,
                force: true,
            }),
        });
        const data = await res.json();
        if (!res.ok || data.success === false) {
            throw new Error(data.error || 'Export failed');
        }
        // Download the .m3u to the browser (same as the other Export-as-M3U
        // buttons) — force=true also saved it server-side for media servers.
        const name = (st.playlistName || 'Playlist').replace(/[/\\?%*:|"<>]/g, '-');
        const blob = new Blob([data.m3u_content || ''], { type: 'audio/x-mpegurl;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = `${name}.m3u`;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        URL.revokeObjectURL(url);
        // `found` = server tracks resolved to a real library file path; any not in
        // SoulSync's library are skipped (can't write a path for them).
        const found = data.stats && data.stats.found != null ? data.stats.found : tracks.length;
        const note = found < tracks.length ? ` (${found}/${tracks.length} in library)` : ` (${found} tracks)`;
        showToast(`Exported M3U: ${st.playlistName}${note}`, 'success');
    } catch (e) {
        showToast(`M3U export failed: ${e.message}`, 'error');
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = orig || '📋 Export M3U'; }
    }
}

function serverEditorBack() {
    const container = document.getElementById('server-playlist-container');
    const editor = document.getElementById('server-editor');
    if (editor) editor.style.display = 'none';
    if (container) container.style.display = '';
}

function _serverEditorFilter(btn, filter) {
    btn.closest('.server-editor-filters').querySelectorAll('.discog-filter').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    _applyServerEditorFilter(filter);
}

// Show/hide rows for the given filter. Split out of the click handler because a
// re-render must re-apply it (#1005: reloading the compare view rebuilt both
// columns fully visible while the "Missing" pill stayed active — pill said
// Missing, columns showed everything).
function _applyServerEditorFilter(filter) {
    ['server-col-source-scroll', 'server-col-server-scroll'].forEach(colId => {
        document.querySelectorAll(`#${colId} .server-track-item`).forEach(item => {
            const status = item.dataset.status;
            item.style.display = (filter === 'all' || status === filter) ? '' : 'none';
        });
    });
}

function _activeServerEditorFilter() {
    const btn = document.querySelector('#server-editor .server-editor-filters .discog-filter.active[data-filter]');
    return btn ? btn.dataset.filter : 'all';
}

// Repaint stats + both columns from _serverEditorState.tracks, keeping the
// active filter applied and each column's scroll position (an in-place row
// patch must not throw the user back to the top of a 2k-track list).
function _rerenderCompare() {
    const tracks = _serverEditorState.tracks || [];
    const sourceScroll = document.getElementById('server-col-source-scroll');
    const serverScroll = document.getElementById('server-col-server-scroll');
    const keep = [sourceScroll?.scrollTop || 0, serverScroll?.scrollTop || 0];
    _updateCompareStats(tracks);
    _renderCompareColumns(tracks);
    _applyServerEditorFilter(_activeServerEditorFilter());
    if (sourceScroll) sourceScroll.scrollTop = keep[0];
    if (serverScroll) serverScroll.scrollTop = keep[1];
}

// ── Track Search / Replace ──

async function serverSearchReplace(trackIndex, mode) {
    const track = _serverEditorState.tracks[trackIndex];
    if (!track) return;

    const src = track.source_track || {};
    const svr = track.server_track || {};
    // Search by track name only first (more reliable than "artist trackname" blob)
    const searchQuery = src.name ? src.name.trim() : (svr.title || '').trim();
    const contextArtist = src.artist || svr.artist || '';
    const contextName = src.name || svr.title || '';
    // Pass the source artist as a relevance hint so an exact title+artist match
    // ranks to the top of the library search instead of being buried under
    // same-title tracks by other artists (#: "bad guy" by Billie Eilish).
    _serverEditorState.searchArtist = contextArtist;

    const existing = document.getElementById('server-search-overlay');
    if (existing) existing.remove();

    const overlay = document.createElement('div');
    overlay.id = 'server-search-overlay';
    overlay.className = 'server-search-overlay';
    overlay.innerHTML = `
        <div class="server-search-popover" id="server-search-popover">
            <div class="server-search-header">
                <div>
                    <div class="server-search-title">${mode === 'replace' ? 'Swap Track' : 'Add Track to Server'}</div>
                    ${contextName ? `<div class="server-search-context">
                        <span class="server-search-context-label">Source:</span>
                        <span class="server-search-context-artist">${_esc(contextArtist)}</span>
                        <span class="server-search-context-sep">—</span>
                        <span class="server-search-context-name">${_esc(contextName)}</span>
                    </div>` : ''}
                </div>
                <button class="server-search-close" onclick="document.getElementById('server-search-overlay')?.remove()">&times;</button>
            </div>
            <div class="server-search-input-wrap">
                <div class="server-search-input-icon">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
                </div>
                <input type="text" class="server-search-input" id="server-search-input" value="${_esc(searchQuery)}" placeholder="Search by track name, artist, or album..." onkeydown="if(event.key==='Enter') _serverSearchExecute()">
            </div>
            <div class="server-search-results-header" id="server-search-results-header"></div>
            <div class="server-search-results" id="server-search-results">
                <div class="server-search-hint">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" style="margin-bottom:6px;opacity:0.4"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
                    <br>Searching...
                </div>
            </div>
        </div>
    `;
    // Click overlay background or press Escape to close
    overlay.addEventListener('click', e => { if (e.target === overlay) overlay.remove(); });
    overlay._escHandler = e => { if (e.key === 'Escape') overlay.remove(); };
    document.addEventListener('keydown', overlay._escHandler);
    // Clean up Escape listener when overlay is removed
    const obs = new MutationObserver(() => {
        if (!document.body.contains(overlay)) { document.removeEventListener('keydown', overlay._escHandler); obs.disconnect(); }
    });
    obs.observe(document.body, { childList: true });

    const popover = overlay.querySelector('.server-search-popover');
    popover.dataset.trackIndex = trackIndex;
    popover.dataset.mode = mode;

    document.body.appendChild(overlay);
    requestAnimationFrame(() => overlay.classList.add('visible'));
    document.getElementById('server-search-input')?.focus();
    document.getElementById('server-search-input')?.select();

    _serverSearchExecute();
}

async function _serverSearchExecute() {
    const input = document.getElementById('server-search-input');
    const results = document.getElementById('server-search-results');
    const resultsHeader = document.getElementById('server-search-results-header');
    const popover = document.getElementById('server-search-popover');
    if (!input || !results || !popover) return;

    const query = input.value.trim();
    if (!query) {
        results.innerHTML = '<div class="server-search-hint">Type a search query</div>';
        if (resultsHeader) resultsHeader.textContent = '';
        return;
    }

    results.innerHTML = '<div class="server-search-hint"><div class="server-search-spinner"></div>Searching library...</div>';
    if (resultsHeader) resultsHeader.textContent = '';

    try {
        const artistHint = (_serverEditorState && _serverEditorState.searchArtist) || '';
        const response = await fetch(`/api/library/search-tracks?q=${encodeURIComponent(query)}&limit=20`
            + (artistHint ? `&artist=${encodeURIComponent(artistHint)}` : ''));
        const data = await response.json();

        if (!data.success || !data.tracks || data.tracks.length === 0) {
            results.innerHTML = `<div class="server-search-hint">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" style="margin-bottom:6px;opacity:0.3"><path d="M9.172 14.828a4 4 0 015.656 0M9 10h.01M15 10h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
                <br>No results found<br><span style="font-size:10px;opacity:0.5">Try different keywords or a shorter query</span>
            </div>`;
            return;
        }

        const trackIndex = parseInt(popover.dataset.trackIndex);
        const mode = popover.dataset.mode;

        // Kept so a Select can patch the compare row in place (no full reload)
        _serverEditorState._searchResults = data.tracks;

        if (resultsHeader) resultsHeader.textContent = `${data.tracks.length} result${data.tracks.length !== 1 ? 's' : ''}`;

        results.innerHTML = data.tracks.map((t, i) => {
            const ext = (t.file_path || '').split('.').pop().toUpperCase();
            const format = ['FLAC', 'MP3', 'OPUS', 'OGG', 'M4A', 'AAC', 'WAV'].includes(ext) ? (ext === 'M4A' ? 'AAC' : ext) : '';
            const dur = _formatDurationMs(t.duration);
            const bitrateStr = t.bitrate ? `${t.bitrate}k` : '';
            return `
                <div class="server-search-result" onclick="_serverSelectTrack(${trackIndex}, '${mode}', '${t.id}', this)" style="animation-delay:${i * 0.03}s">
                    <div class="server-search-result-art">
                        ${t.album_thumb_url ? `<img src="${t.album_thumb_url}" alt="" loading="lazy">` : '<div class="server-search-result-art-empty"></div>'}
                    </div>
                    <div class="server-search-result-info">
                        <div class="server-search-result-title">${_esc(t.title)}</div>
                        <div class="server-search-result-meta">${_esc(t.artist_name)}${t.album_title ? ` · ${_esc(t.album_title)}` : ''}</div>
                    </div>
                    <div class="server-search-result-details">
                        ${format ? `<span class="server-search-format">${format}</span>` : ''}
                        ${bitrateStr ? `<span class="server-search-bitrate">${bitrateStr}</span>` : ''}
                        ${dur ? `<span class="server-search-dur">${dur}</span>` : ''}
                    </div>
                    <button class="server-search-select-btn">Select</button>
                </div>
            `;
        }).join('');

    } catch (e) {
        results.innerHTML = `<div class="server-search-hint">Error: ${e.message}</div>`;
    }
}

async function _serverSelectTrack(trackIndex, mode, newTrackId, el) {
    const track = _serverEditorState.tracks[trackIndex];
    if (!track) return;

    const btn = el.querySelector('.server-search-select-btn');
    if (btn) { btn.disabled = true; btn.textContent = '...'; }

    try {
        let response;
        if (mode === 'replace') {
            // Same source_* fields the add branch below sends (#1159): without
            // them the backend persisted nothing, so a corrected bad match
            // reverted on the next compare — the sync's cached auto-match kept
            // winning and the hand-picked track showed up in Extras.
            const srcTrackR = track.source_track || {};
            response = await fetch(`/api/server/playlist/${_serverEditorState.playlistId}/replace-track`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    old_track_id: track.server_track?.id,
                    new_track_id: newTrackId,
                    playlist_name: _serverEditorState.playlistName,
                    source_track_id: srcTrackR.source_track_id || '',
                    source_title: srcTrackR.name || '',
                    source_artist: srcTrackR.artist || '',
                    source: srcTrackR.source || _serverEditorState.mirroredPlaylist?.source || '',
                })
            });
        } else {
            // Calculate the server-side position for this track
            // Count how many server tracks exist before this index
            let serverPos = 0;
            for (let k = 0; k < trackIndex; k++) {
                if (_serverEditorState.tracks[k]?.server_track) serverPos++;
            }
            // source_track carries source_track_id (Spotify ID) when this
            // came from a mirrored playlist — the backend uses it to
            // persist the Find & Add selection as a permanent match
            // override so future syncs auto-pair without user action.
            const srcTrack = track.source_track || {};
            response = await fetch(`/api/server/playlist/${_serverEditorState.playlistId}/add-track`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    track_id: newTrackId,
                    playlist_name: _serverEditorState.playlistName,
                    position: serverPos,
                    source_track_id: srcTrack.source_track_id || '',
                    source_title: srcTrack.name || '',
                    source_artist: srcTrack.artist || '',
                    // Provider of the source track, so the durable manual match
                    // (#787) records the right source. Retrieval is source-agnostic.
                    source: srcTrack.source || _serverEditorState.mirroredPlaylist?.source || '',
                })
            });
        }

        const data = await response.json();
        if (data.success) {
            showToast(data.message || 'Track updated', 'success');
            document.getElementById('server-search-overlay')?.remove();
            // Update playlist ID if server recreated it (Plex deletes+recreates)
            if (data.new_playlist_id) _serverEditorState.playlistId = data.new_playlist_id;

            // Patch the pair in place instead of re-fetching + re-matching the
            // whole playlist (#1005: one Find & Add threw a 2k-track compare
            // back to "Loading comparison..."). The server call above already
            // succeeded; the picked library track IS the server track (same id
            // space). The next full open recomputes order-status etc.
            const picked = (_serverEditorState._searchResults || [])
                .find(x => String(x.id) === String(newTrackId));
            if (picked) {
                track.server_track = {
                    id: String(newTrackId),
                    title: picked.title || '',
                    artist: picked.artist_name || '',
                    album: picked.album_title || '',
                    duration: picked.duration || 0,
                    thumb: picked.album_thumb_url || '',
                };
                track.match_status = 'matched';
                track.confidence = 1.0;
                track.override = true;
                // Link case: the picked track may already sit in the playlist as
                // an "extra" row (the backend links instead of duplicating) —
                // drop that row or the track shows twice until the next full load.
                const dupIdx = _serverEditorState.tracks.findIndex(p =>
                    p !== track && !p.source_track && p.server_track &&
                    String(p.server_track.id) === String(newTrackId));
                if (dupIdx >= 0) _serverEditorState.tracks.splice(dupIdx, 1);
                _rerenderCompare();
            } else {
                // couldn't identify the picked track locally — full reload fallback
                _openServerCompareView(_serverEditorState.playlistId, _serverEditorState.playlistName, _serverEditorState.mirroredPlaylist);
            }
        } else {
            showToast(data.error || 'Failed to update track', 'error');
            if (btn) { btn.disabled = false; btn.textContent = 'Select'; }
        }
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
        if (btn) { btn.disabled = false; btn.textContent = 'Select'; }
    }
}

async function _serverRemoveTrack(trackIndex, serverTrackId) {
    if (!serverTrackId) return;

    const track = _serverEditorState.tracks[trackIndex];
    const trackTitle = track?.server_track?.title || 'this track';

    if (!await showConfirmDialog({ title: 'Remove Track', message: `Remove "${trackTitle}" from this playlist?`, confirmText: 'Remove', destructive: true })) return;

    try {
        const response = await fetch(`/api/server/playlist/${_serverEditorState.playlistId}/remove-track`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                track_id: serverTrackId,
                playlist_name: _serverEditorState.playlistName,
            })
        });

        const data = await response.json();
        if (data.success) {
            showToast(data.message || 'Track removed', 'success');
            _serverEditorState.playlistId = data.new_playlist_id || _serverEditorState.playlistId;
            // Patch in place (#1005 — same as Find & Add): a matched pair loses
            // its server side and becomes missing; an extra row disappears.
            if (track && track.source_track) {
                track.server_track = null;
                track.match_status = 'missing';
                track.confidence = 0.0;
            } else {
                _serverEditorState.tracks.splice(trackIndex, 1);
            }
            _rerenderCompare();
        } else {
            showToast(data.error || 'Failed to remove track', 'error');
        }
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
    }
}


// The 30s dashboard sync-card cycle is gone with the flip — the React
// Recent Syncs card (syncs-card.tsx) runs its own 30s refresh while mounted,
// and #sync-history-cards is React-owned DOM this file must not write.

// Re-add a synced unmatched track to the wishlist from the sync-detail modal, with
// the same context the original sync used (resolved server-side from the entry).
async function _readdSyncWishlist(entryId, index, el) {
    if (el && el.dataset.busy) return;
    if (el) { el.dataset.busy = '1'; el.classList.add('is-busy'); }
    try {
        const resp = await fetch(`/api/sync/history/${entryId}/track/${index}/wishlist`, { method: 'POST' });
        const data = await resp.json();
        if (data && data.success) {
            if (el) {
                el.classList.remove('is-busy');
                el.classList.add('is-done');
                el.disabled = true;
                el.innerHTML = data.added ? '&#10003; Re-added' : '&#10003; On wishlist';
            }
            if (typeof showToast === 'function') {
                showToast(
                    data.added ? `Re-added "${data.name}" to wishlist` : `"${data.name}" is already on the wishlist`,
                    data.added ? 'success' : 'info',
                );
            }
        } else {
            if (el) { delete el.dataset.busy; el.classList.remove('is-busy'); }
            if (typeof showToast === 'function') showToast((data && data.error) || 'Could not re-add to wishlist', 'error');
        }
    } catch (e) {
        if (el) { delete el.dataset.busy; el.classList.remove('is-busy'); }
        if (typeof showToast === 'function') showToast('Could not re-add to wishlist: ' + e.message, 'error');
    }
}

async function openSyncDetailModal(entryId) {
    try {
        showLoadingOverlay('Loading sync details...');
        const response = await fetch(`/api/sync/history/${entryId}`);
        const data = await response.json();
        hideLoadingOverlay();

        if (!data.success || !data.entry) {
            showToast('Could not load sync details', 'error');
            return;
        }

        const entry = data.entry;
        const trackResults = entry.track_results || [];
        const name = entry.artist_name
            ? `${entry.artist_name} — ${entry.album_name || entry.playlist_name}`
            : entry.playlist_name || 'Unknown';

        // Build modal
        const overlay = document.createElement('div');
        overlay.className = 'discog-modal-overlay';
        overlay.id = 'sync-detail-overlay';

        const found = entry.tracks_found || 0;
        const total = entry.total_tracks || 0;
        const downloaded = entry.tracks_downloaded || 0;

        let trackRowsHtml = '';
        if (trackResults.length > 0) {
            trackRowsHtml = trackResults.map((t, i) => {
                const statusIcon = t.status === 'found' ? '✅' : '❌';
                const statusClass = t.status === 'found' ? 'matched' : 'unmatched';
                const confPct = Math.round((t.confidence || 0) * 100);
                const confClass = confPct >= 80 ? 'conf-high' : confPct >= 50 ? 'conf-mid' : 'conf-low';
                let dlIcon = '';
                if (t.download_status === 'completed') dlIcon = '✅';
                else if (t.download_status === 'failed') dlIcon = '❌';
                else if (t.download_status === 'not_found') dlIcon = '🔇';
                else if (t.download_status === 'cancelled') dlIcon = '🚫';

                let dlDisplay = dlIcon;
                if (!dlDisplay && t.download_status === 'wishlist') {
                    // Wing-it fallback stubs (no real metadata) were never actually
                    // wishlisted by the sync — show them as plain, non-clickable.
                    const isWingIt = String(t.source_track_id || '').startsWith('wing_it_');
                    if (isWingIt) {
                        dlDisplay = `<span class="sync-dl-unmatched" title="Couldn't be resolved to real metadata (wing-it fallback), so it was never added to the wishlist">Unmatched</span>`;
                    } else {
                        // Clickable: re-add this exact track to the wishlist with the
                        // same context the sync originally used.
                        dlDisplay = `<button type="button" class="sync-dl-wishlist sync-dl-wishlist-btn" `
                            + `onclick="_readdSyncWishlist(${entryId}, ${i}, this)" `
                            + `title="Re-add to wishlist with the original sync context">&rarr; Wishlist</button>`;
                    }
                }

                return `
                    <tr class="sync-detail-row ${statusClass}">
                        <td class="sync-detail-num">${i + 1}</td>
                        <td class="sync-detail-art">
                            ${t.image_url ? `<img src="${t.image_url}" alt="" loading="lazy">` : '<div class="sync-detail-art-empty"></div>'}
                        </td>
                        <td class="sync-detail-track">${_esc(t.name || '')}</td>
                        <td class="sync-detail-artist">${_esc(t.artist || '')}</td>
                        <td class="sync-detail-album">${_esc(t.album || '')}</td>
                        <td class="sync-detail-status">${statusIcon}</td>
                        <td class="sync-detail-conf"><span class="conf-badge ${confClass}">${confPct}%</span></td>
                        <td class="sync-detail-dl">${dlDisplay}</td>
                    </tr>
                `;
            }).join('');
        } else {
            // Fallback to tracks_json if no track_results (old syncs before data caching)
            const tracks = entry.tracks || [];
            const esc = typeof _esc === 'function' ? _esc : s => s;
            trackRowsHtml = `
                <tr><td colspan="8" class="sync-detail-notice">
                    <div class="sync-detail-notice-text">Per-track match data not available for this sync.<br>Re-sync this playlist to see detailed match results.</div>
                </td></tr>
            ` + tracks.map((t, i) => {
                const artists = t.artists || [];
                const artistName = artists.length > 0 ? (typeof artists[0] === 'string' ? artists[0] : artists[0]?.name || '') : '';
                const albumName = typeof t.album === 'object' ? (t.album?.name || '') : (t.album || '');
                return `
                    <tr class="sync-detail-row no-data">
                        <td class="sync-detail-num">${i + 1}</td>
                        <td class="sync-detail-art"></td>
                        <td class="sync-detail-track">${esc(t.name || '')}</td>
                        <td class="sync-detail-artist">${esc(artistName)}</td>
                        <td class="sync-detail-album">${esc(albumName)}</td>
                        <td class="sync-detail-status" colspan="3"></td>
                    </tr>
                `;
            }).join('');
        }

        // Count stats for filter bar
        const matchedCount = trackResults.filter(t => t.status === 'found').length;
        const unmatchedCount = trackResults.filter(t => t.status !== 'found').length;
        const downloadedCount = trackResults.filter(t => t.download_status === 'completed').length;

        overlay.innerHTML = `
            <div class="discog-modal">
                <div class="discog-modal-hero" ${entry.thumb_url ? `style="background-image:url('${entry.thumb_url}')"` : ''}>
                    <div class="discog-modal-hero-overlay"></div>
                    <div class="discog-modal-hero-content">
                        <h2 class="discog-modal-title">Sync Details</h2>
                        <p class="discog-modal-artist">${_esc(name)}</p>
                    </div>
                    <button class="discog-modal-close" onclick="document.getElementById('sync-detail-overlay')?.remove()">&times;</button>
                </div>
                <div class="discog-filter-bar">
                    <div class="discog-filters">
                        <button class="discog-filter active" data-filter="all" onclick="_syncDetailFilter(this, 'all')">All (${total})</button>
                        <button class="discog-filter" data-filter="matched" onclick="_syncDetailFilter(this, 'matched')">Matched (${matchedCount})</button>
                        <button class="discog-filter" data-filter="unmatched" onclick="_syncDetailFilter(this, 'unmatched')">Unmatched (${unmatchedCount})</button>
                        ${downloadedCount > 0 ? `<button class="discog-filter" data-filter="downloaded" onclick="_syncDetailFilter(this, 'downloaded')">Downloaded (${downloadedCount})</button>` : ''}
                    </div>
                </div>
                <div class="sync-detail-table-wrap">
                    <table class="sync-detail-table">
                        <thead>
                            <tr>
                                <th>#</th>
                                <th></th>
                                <th>Track</th>
                                <th>Artist</th>
                                <th>Album</th>
                                <th>Match</th>
                                <th>Conf.</th>
                                <th>Status</th>
                            </tr>
                        </thead>
                        <tbody id="sync-detail-tbody">
                            ${trackRowsHtml}
                        </tbody>
                    </table>
                </div>
                <div class="discog-footer">
                    <div class="discog-footer-info">${found} matched · ${downloaded} downloaded · ${total} total</div>
                    <div class="discog-footer-actions">
                        <button class="discog-cancel-btn" onclick="document.getElementById('sync-detail-overlay')?.remove()">Close</button>
                    </div>
                </div>
            </div>
        `;

        document.body.appendChild(overlay);
        requestAnimationFrame(() => overlay.classList.add('visible'));

    } catch (e) {
        hideLoadingOverlay();
        showToast('Failed to load sync details', 'error');
    }
}

function _syncDetailFilter(btn, filter) {
    // Update active button
    btn.closest('.discog-filters').querySelectorAll('.discog-filter').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');

    // Filter rows
    document.querySelectorAll('#sync-detail-tbody .sync-detail-row').forEach(row => {
        if (filter === 'all') {
            row.style.display = '';
        } else if (filter === 'matched') {
            row.style.display = row.classList.contains('matched') ? '' : 'none';
        } else if (filter === 'unmatched') {
            row.style.display = row.classList.contains('unmatched') ? '' : 'none';
        } else if (filter === 'downloaded') {
            const dlCell = row.querySelector('.sync-detail-dl');
            row.style.display = dlCell && dlCell.textContent.trim() === '✅' ? '' : 'none';
        }
    });
}

// The Downloads page lived here: the list, the batch panel, the batch
// history and the verification/quarantine review queue — about 1,700
// lines. It is React's now (webui/src/routes/active-downloads/).
//
// _adlOpenBatchModal moved to core.js as window.openDownloadBatchModal
// rather than being deleted: it reads activeDownloadProcesses,
// rehydrateModal and WishlistModalState, which are script-scoped there.
