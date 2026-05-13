// IMPORT PAGE (full page, replaces old modal)
// ===================================================================

let importJobIdCounter = 0;

const importPageState = {
    stagingFiles: [],
    selectedSingles: new Set(),
    albumData: null,          // response from /api/import/album/match
    matchOverrides: {},       // { trackIndex: stagingFileIndex }  — manual drag-drop overrides
    singlesManualMatches: {}, // { stagingFileIndex: { id, name, artist, album, ... } }
    initialized: false,
    activeTab: 'album',
    tapSelectedChip: null,    // for mobile tap-to-assign fallback
    // Album lookup cache for click handlers. Populated by suggestions /
    // search renderers; read by importPageSelectAlbum so the match POST
    // can include `source` + `name` + `artist` (without these, backend
    // can't look up cross-source IDs and falls back to a broken
    // "Unknown Artist / album_id as title / 0 tracks" placeholder —
    // github issue #524).
    _albumLookup: {},         // { albumId: { id, name, artist, source } }
};

// ===============================
// STATS PAGE
// ===============================

let _statsRange = '7d';
let _statsTimelineChart = null;
let _statsGenreChart = null;
let _statsDbStorageChart = null;
let _statsInitialized = false;

function initializeStatsPage() {
    if (_statsInitialized) {
        loadStatsData();
        return;
    }
    _statsInitialized = true;

    // Time range buttons
    const rangeContainer = document.getElementById('stats-time-range');
    if (rangeContainer) {
        rangeContainer.addEventListener('click', (e) => {
            const btn = e.target.closest('.stats-range-btn');
            if (!btn) return;
            _statsRange = btn.dataset.range;
            rangeContainer.querySelectorAll('.stats-range-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            loadStatsData();
        });
    }

    loadStatsData();
    _updateStatsLastSynced();
}

async function triggerStatsSync() {
    const btn = document.getElementById('stats-sync-btn');
    if (btn) btn.classList.add('syncing');

    try {
        const resp = await fetch('/api/listening-stats/sync', { method: 'POST' });
        const data = await resp.json();
        if (data.success) {
            showToast('Syncing listening data...', 'info');
            // Wait a few seconds for the sync to complete, then reload
            setTimeout(async () => {
                await loadStatsData();
                _updateStatsLastSynced();
                if (btn) btn.classList.remove('syncing');
                showToast('Listening stats updated', 'success');
            }, 5000);
        } else {
            showToast(data.error || 'Sync failed', 'error');
            if (btn) btn.classList.remove('syncing');
        }
    } catch (e) {
        showToast('Sync failed', 'error');
        if (btn) btn.classList.remove('syncing');
    }
}

async function _updateStatsLastSynced() {
    const el = document.getElementById('stats-last-synced');
    if (!el) return;
    try {
        const resp = await fetch('/api/listening-stats/status');
        const data = await resp.json();
        if (data.stats && data.stats.last_poll) {
            el.textContent = `Last synced: ${data.stats.last_poll}`;
        } else {
            el.textContent = 'Not synced yet';
        }
    } catch {
        el.textContent = '';
    }
}

async function loadStatsData() {
    // Show loading state
    document.querySelectorAll('.stats-card-value').forEach(el => el.style.opacity = '0.3');

    // Single cached endpoint — instant response
    let data;
    try {
        const resp = await fetch(`/api/stats/cached?range=${_statsRange}`);
        data = await resp.json();
    } catch {
        data = {};
    }

    if (!data.success) {
        // Cache not available — show empty state, user should hit Sync
        data = {
            overview: {}, top_artists: [], top_albums: [], top_tracks: [],
            timeline: [], genres: [], recent: [], health: {}
        };
    }

    const overview = data.overview || {};
    const emptyEl = document.getElementById('stats-empty');
    const hasData = (overview.total_plays || 0) > 0;

    if (emptyEl) {
        emptyEl.classList.toggle('hidden', hasData);
    }
    // Hide main content sections when no data
    const mainSections = document.querySelectorAll('.stats-overview, .stats-main-grid, .stats-full-width');
    mainSections.forEach(el => el.style.display = hasData ? '' : 'none');

    // Overview cards
    const _fmt = (n) => {
        if (!n) return '0';
        if (n >= 1000000) return (n / 1000000).toFixed(1).replace(/\.0$/, '') + 'M';
        if (n >= 1000) return (n / 1000).toFixed(1).replace(/\.0$/, '') + 'K';
        return n.toLocaleString();
    };
    const _fmtTime = (ms) => {
        if (!ms) return '0h';
        const hours = Math.floor(ms / 3600000);
        const mins = Math.floor((ms % 3600000) / 60000);
        if (hours > 0) return `${hours}h ${mins}m`;
        return `${mins}m`;
    };

    // Restore opacity
    document.querySelectorAll('.stats-card-value').forEach(el => el.style.opacity = '1');

    _setText('stats-total-plays', _fmt(overview.total_plays));
    _setText('stats-listening-time', _fmtTime(overview.total_time_ms));
    _setText('stats-unique-artists', _fmt(overview.unique_artists));
    _setText('stats-unique-albums', _fmt(overview.unique_albums));
    _setText('stats-unique-tracks', _fmt(overview.unique_tracks));

    // Top Artists — visual bubbles
    _renderTopArtistsVisual(data.top_artists || []);

    // Top Artists — ranked list
    _renderRankedList('stats-top-artists', data.top_artists || [], (item, i) => `
        <div class="stats-ranked-item">
            <span class="stats-ranked-num">${i + 1}</span>
            ${item.image_url ? `<img class="stats-ranked-img" src="${item.image_url}" alt="" onerror="this.style.display='none'">` : ''}
            <div class="stats-ranked-info">
                <div class="stats-ranked-name">${item.id ? `<a class="stats-artist-link" onclick="navigateToPage('library');setTimeout(()=>navigateToArtistDetail('${item.id}','${_esc(item.name).replace(/'/g, "\\'")}'),300)">${_esc(item.name)}</a>` : _esc(item.name)}${item.soul_id && !String(item.soul_id).startsWith('soul_unnamed_') ? ' <img src="/static/trans2.png" style="width:12px;height:12px;vertical-align:middle;opacity:0.5;" title="SoulID">' : ''}</div>
                <div class="stats-ranked-meta">${item.global_listeners ? _fmt(item.global_listeners) + ' global listeners' : ''}</div>
            </div>
            <span class="stats-ranked-count">${_fmt(item.play_count)} plays</span>
        </div>
    `);

    // Top Albums
    _renderRankedList('stats-top-albums', data.top_albums || [], (item, i) => `
        <div class="stats-ranked-item">
            <span class="stats-ranked-num">${i + 1}</span>
            ${item.image_url ? `<img class="stats-ranked-img" src="${item.image_url}" alt="" onerror="this.style.display='none'">` : ''}
            <div class="stats-ranked-info">
                <div class="stats-ranked-name">${_esc(item.name)}</div>
                <div class="stats-ranked-meta">${item.artist_id ? `<a class="stats-artist-link" onclick="navigateToPage('library');setTimeout(()=>navigateToArtistDetail('${item.artist_id}','${_esc(item.artist || '').replace(/'/g, "\\'")}'),300)">${_esc(item.artist || '')}</a>` : _esc(item.artist || '')}</div>
            </div>
            <span class="stats-ranked-count">${_fmt(item.play_count)} plays</span>
        </div>
    `);

    // Top Tracks
    _renderRankedList('stats-top-tracks', data.top_tracks || [], (item, i) => `
        <div class="stats-ranked-item">
            <span class="stats-ranked-num">${i + 1}</span>
            ${item.image_url ? `<img class="stats-ranked-img" src="${item.image_url}" alt="" onerror="this.style.display='none'">` : ''}
            <div class="stats-ranked-info">
                <div class="stats-ranked-name">${_esc(item.name)}</div>
                <div class="stats-ranked-meta">${item.artist_id ? `<a class="stats-artist-link" onclick="navigateToPage('library');setTimeout(()=>navigateToArtistDetail('${item.artist_id}','${_esc(item.artist || '').replace(/'/g, "\\'")}'),300)">${_esc(item.artist || '')}</a>` : _esc(item.artist || '')}${item.album ? ' · ' + _esc(item.album) : ''}</div>
            </div>
            <button class="stats-play-btn" onclick="event.stopPropagation();playStatsTrack('${_esc(item.name).replace(/'/g, "\\'")}','${_esc(item.artist || '').replace(/'/g, "\\'")}','${_esc(item.album || '').replace(/'/g, "\\'")}')" title="Play">▶</button>
            <span class="stats-ranked-count">${_fmt(item.play_count)} plays</span>
        </div>
    `);

    // Timeline chart
    _renderTimelineChart(data.timeline || []);

    // Genre chart
    _renderGenreChart(data.genres || []);

    // Library health
    _renderLibraryHealth(data.health || {});

    // DB storage chart (separate fetch — not part of cached stats)
    _loadDbStorageChart();
    // Library disk usage (separate fetch — populated by deep scan)
    _loadLibraryDiskUsage();

    // Recent plays
    _renderRecentPlays(data.recent || []);
}

function _renderTopArtistsVisual(artists) {
    const el = document.getElementById('stats-top-artists-visual');
    if (!el || !artists.length) { if (el) el.innerHTML = ''; return; }

    const top5 = artists.slice(0, 5);
    const maxPlays = top5[0]?.play_count || 1;
    const _fmt = (n) => {
        if (!n) return '0';
        if (n >= 1000000) return (n / 1000000).toFixed(1).replace(/\.0$/, '') + 'M';
        if (n >= 1000) return (n / 1000).toFixed(1).replace(/\.0$/, '') + 'K';
        return n.toString();
    };

    el.innerHTML = `<div class="stats-artist-bubbles">
        ${top5.map((a, i) => {
        const pct = Math.round((a.play_count / maxPlays) * 100);
        const size = 44 + (4 - i) * 6; // Largest first: 68, 62, 56, 50, 44
        return `<div class="stats-artist-bubble" onclick="${a.id ? `navigateToPage('library');setTimeout(()=>navigateToArtistDetail('${a.id}','${_esc(a.name).replace(/'/g, "\\\\'")}'),300)` : ''}" style="cursor:${a.id ? 'pointer' : 'default'}">
                <div class="stats-bubble-img" style="width:${size}px;height:${size}px;${a.image_url ? `background-image:url('${a.image_url}')` : ''}">
                    ${!a.image_url ? `<span>${(a.name || '?')[0]}</span>` : ''}
                </div>
                <div class="stats-bubble-bar-container">
                    <div class="stats-bubble-bar" style="width:${pct}%"></div>
                </div>
                <div class="stats-bubble-name">${_esc(a.name)}</div>
                <div class="stats-bubble-count">${_fmt(a.play_count)}</div>
            </div>`;
    }).join('')}
    </div>`;
}

function _setText(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
}

function _renderRankedList(containerId, items, template) {
    const el = document.getElementById(containerId);
    if (!el) return;
    el.innerHTML = items.length
        ? items.map((item, i) => template(item, i)).join('')
        : '<div style="color:rgba(255,255,255,0.3);font-size:0.85em;padding:12px;">No data yet</div>';
}

function _renderTimelineChart(data) {
    const canvas = document.getElementById('stats-timeline-chart');
    if (!canvas || typeof Chart === 'undefined') return;

    if (_statsTimelineChart) _statsTimelineChart.destroy();

    _statsTimelineChart = new Chart(canvas, {
        type: 'bar',
        data: {
            labels: data.map(d => d.date),
            datasets: [{
                label: 'Plays',
                data: data.map(d => d.plays),
                backgroundColor: `rgba(${getComputedStyle(document.documentElement).getPropertyValue('--accent-rgb').trim() || '29,185,84'}, 0.5)`,
                borderColor: `rgba(${getComputedStyle(document.documentElement).getPropertyValue('--accent-rgb').trim() || '29,185,84'}, 0.8)`,
                borderWidth: 1,
                borderRadius: 4,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: {
                x: { grid: { display: false }, ticks: { color: 'rgba(255,255,255,0.3)', font: { size: 10 }, maxTicksLimit: 12 } },
                y: { grid: { color: 'rgba(255,255,255,0.04)' }, ticks: { color: 'rgba(255,255,255,0.3)', font: { size: 10 } }, beginAtZero: true },
            }
        }
    });
}

function _renderGenreChart(data) {
    const canvas = document.getElementById('stats-genre-chart');
    const legend = document.getElementById('stats-genre-legend');
    if (!canvas || typeof Chart === 'undefined') return;

    if (_statsGenreChart) _statsGenreChart.destroy();

    const colors = [
        '#1db954', '#1ed760', '#4ade80', '#7c3aed', '#a855f7',
        '#ec4899', '#f43f5e', '#f97316', '#eab308', '#06b6d4',
        '#3b82f6', '#6366f1', '#14b8a6', '#84cc16', '#f59e0b',
    ];

    const top = data.slice(0, 10);

    _statsGenreChart = new Chart(canvas, {
        type: 'doughnut',
        data: {
            labels: top.map(g => g.genre),
            datasets: [{
                data: top.map(g => g.play_count),
                backgroundColor: colors.slice(0, top.length),
                borderWidth: 0,
                hoverOffset: 6,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            cutout: '65%',
            plugins: { legend: { display: false } },
        }
    });

    if (legend) {
        legend.innerHTML = top.map((g, i) => `
            <div class="stats-genre-legend-item">
                <span class="stats-genre-dot" style="background:${colors[i]}"></span>
                <span>${g.genre}</span>
                <span class="stats-genre-pct">${g.percentage}%</span>
            </div>
        `).join('');
    }
}

function _renderLibraryHealth(data) {
    if (!data || !data.total_tracks) return;

    const _fmt = (n) => {
        if (!n) return '0';
        if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
        if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
        return n.toLocaleString();
    };

    _setText('stats-unplayed', `${_fmt(data.unplayed_count)} (${data.unplayed_percentage || 0}%)`);
    _setText('stats-total-duration', data.total_duration_ms ? `${Math.floor(data.total_duration_ms / 3600000)}h` : '0h');
    _setText('stats-total-tracks-count', _fmt(data.total_tracks));

    // Format bar
    const bar = document.getElementById('stats-format-bar');
    if (bar && data.format_breakdown) {
        const total = Object.values(data.format_breakdown).reduce((s, v) => s + v, 0) || 1;
        const fmtColors = { FLAC: '#3b82f6', MP3: '#f97316', Opus: '#a855f7', AAC: '#14b8a6', OGG: '#eab308', WAV: '#ec4899', Other: '#555' };

        bar.innerHTML = Object.entries(data.format_breakdown).map(([fmt, count]) => {
            const pct = (count / total * 100).toFixed(1);
            return `<div class="stats-format-segment" style="flex:${count};background:${fmtColors[fmt] || '#555'}" title="${fmt}: ${count} tracks (${pct}%)">${pct > 8 ? fmt : ''}</div>`;
        }).join('');
    }

    // Enrichment coverage
    const enrichEl = document.getElementById('stats-enrichment-coverage');
    if (enrichEl && data.enrichment_coverage) {
        const ec = data.enrichment_coverage;
        const services = [
            { name: 'Spotify', pct: ec.spotify || 0, color: '#1db954' },
            { name: 'MusicBrainz', pct: ec.musicbrainz || 0, color: '#ba55d3' },
            { name: 'Deezer', pct: ec.deezer || 0, color: '#a238ff' },
            { name: 'Last.fm', pct: ec.lastfm || 0, color: '#d51007' },
            { name: 'iTunes', pct: ec.itunes || 0, color: '#fc3c44' },
            { name: 'AudioDB', pct: ec.audiodb || 0, color: '#1a9fff' },
            { name: 'Genius', pct: ec.genius || 0, color: '#ffff64' },
            { name: 'Tidal', pct: ec.tidal || 0, color: '#00ffff' },
            { name: 'Qobuz', pct: ec.qobuz || 0, color: '#4285f4' },
        ];
        enrichEl.innerHTML = services.map(s => `
            <div class="stats-enrich-item">
                <span class="stats-enrich-name">${s.name}</span>
                <div class="stats-enrich-bar"><div class="stats-enrich-fill" style="width:${s.pct}%;background:${s.color}"></div></div>
                <span class="stats-enrich-pct">${s.pct}%</span>
            </div>
        `).join('');
    }
}

async function _loadDbStorageChart() {
    try {
        const resp = await fetch('/api/stats/db-storage');
        const data = await resp.json();
        if (!data.success || !data.tables || !data.tables.length) return;
        _renderDbStorageChart(data.tables, data.total_file_size, data.method);
    } catch (e) {
        console.debug('DB storage chart load failed:', e);
    }
}

async function _loadLibraryDiskUsage() {
    try {
        const resp = await fetch('/api/stats/library-disk-usage');
        const data = await resp.json();
        if (!data.success) return;
        _renderLibraryDiskUsage(data);
    } catch (e) {
        console.debug('Library disk usage load failed:', e);
    }
}

function _formatBytes(n) {
    if (!n || n <= 0) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let i = 0;
    let v = n;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    return `${v.toFixed(v < 10 ? 2 : 1)} ${units[i]}`;
}

function _renderLibraryDiskUsage(data) {
    const totalEl = document.getElementById('stats-disk-total-value');
    const metaEl = document.getElementById('stats-disk-total-meta');
    const formatsEl = document.getElementById('stats-disk-formats');
    if (!totalEl || !metaEl || !formatsEl) return;

    if (!data.has_data || !data.total_bytes) {
        totalEl.textContent = '—';
        metaEl.textContent = data.tracks_without_size > 0
            ? `Run a Deep Scan to populate (${data.tracks_without_size.toLocaleString()} tracks pending)`
            : 'No tracks in library yet';
        formatsEl.innerHTML = '';
        return;
    }

    totalEl.textContent = _formatBytes(data.total_bytes);

    const withSize = data.tracks_with_size || 0;
    const withoutSize = data.tracks_without_size || 0;
    const trackBits = `${withSize.toLocaleString()} tracks measured`;
    const pendingBits = withoutSize > 0
        ? ` (+${withoutSize.toLocaleString()} pending next Deep Scan)`
        : '';
    metaEl.textContent = trackBits + pendingBits;

    // Per-format bars sorted by size descending. Skip if no breakdown.
    const formats = Object.entries(data.by_format || {}).sort((a, b) => b[1] - a[1]);
    if (!formats.length) { formatsEl.innerHTML = ''; return; }

    const max = formats[0][1] || 1;
    formatsEl.innerHTML = formats.map(([ext, bytes]) => {
        const pct = Math.max(2, Math.round((bytes / max) * 100));
        return `
            <div class="stats-disk-format-row">
                <span class="stats-disk-format-name">${ext.toUpperCase()}</span>
                <div class="stats-disk-format-bar">
                    <div class="stats-disk-format-fill" style="width:${pct}%"></div>
                </div>
                <span class="stats-disk-format-size">${_formatBytes(bytes)}</span>
            </div>
        `;
    }).join('');
}

function _renderDbStorageChart(tables, totalFileSize, method) {
    const canvas = document.getElementById('stats-db-storage-chart');
    if (!canvas || typeof Chart === 'undefined') return;

    if (_statsDbStorageChart) _statsDbStorageChart.destroy();

    // Top 8 tables, group rest as "Other"
    const top = tables.slice(0, 8);
    const rest = tables.slice(8);
    const restSize = rest.reduce((s, t) => s + t.size, 0);
    if (restSize > 0) top.push({ name: 'Other', size: restSize });

    const colors = ['#3b82f6', '#f97316', '#a855f7', '#14b8a6', '#eab308', '#ec4899', '#6366f1', '#22c55e', '#555'];

    _statsDbStorageChart = new Chart(canvas, {
        type: 'doughnut',
        data: {
            labels: top.map(t => t.name),
            datasets: [{
                data: top.map(t => t.size),
                backgroundColor: colors.slice(0, top.length),
                borderWidth: 0,
                hoverOffset: 4,
            }],
        },
        options: {
            responsive: false,
            cutout: '65%',
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        label: (ctx) => {
                            const val = ctx.parsed;
                            if (method === 'dbstat') {
                                if (val > 1048576) return ` ${(val / 1048576).toFixed(1)} MB`;
                                return ` ${(val / 1024).toFixed(0)} KB`;
                            }
                            return ` ${val.toLocaleString()} rows`;
                        }
                    }
                }
            },
        },
    });

    // Center label — total file size
    const totalEl = document.getElementById('stats-db-total');
    if (totalEl) {
        let sizeStr;
        if (totalFileSize > 1073741824) sizeStr = (totalFileSize / 1073741824).toFixed(2) + ' GB';
        else if (totalFileSize > 1048576) sizeStr = (totalFileSize / 1048576).toFixed(1) + ' MB';
        else sizeStr = (totalFileSize / 1024).toFixed(0) + ' KB';
        totalEl.innerHTML = `<div class="stats-db-total-value">${sizeStr}</div><div class="stats-db-total-label">Total Size</div>`;
    }

    // Legend
    const legendEl = document.getElementById('stats-db-legend');
    if (legendEl) {
        legendEl.innerHTML = top.map((t, i) => {
            let sizeLabel;
            if (method === 'dbstat') {
                if (t.size > 1048576) sizeLabel = (t.size / 1048576).toFixed(1) + ' MB';
                else sizeLabel = (t.size / 1024).toFixed(0) + ' KB';
            } else {
                sizeLabel = t.size.toLocaleString() + ' rows';
            }
            return `<div class="stats-db-legend-item">
                <span class="stats-db-legend-dot" style="background:${colors[i]}"></span>
                <span class="stats-db-legend-name">${t.name}</span>
                <span class="stats-db-legend-size">${sizeLabel}</span>
            </div>`;
        }).join('');
    }
}

async function playStatsTrack(title, artist, album) {
    // 1. Try the library first — fastest and best quality if owned.
    try {
        const resp = await fetch('/api/stats/resolve-track', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title, artist }),
        });
        const data = await resp.json();
        if (data.success && data.track) {
            const t = data.track;
            playLibraryTrack({
                id: t.id,
                title: t.title,
                file_path: t.file_path,
                bitrate: t.bitrate,
                artist_id: t.artist_id,
                album_id: t.album_id,
                _stats_image: t.image_url || null,
            }, t.album_title || album || '', t.artist_name || artist || '');
            return;
        }
    } catch (e) {
        console.debug('Library resolve failed, will try streaming fallback:', e);
    }

    // 2. Library miss — fall back to streaming via the enhanced-search streamer
    //    (Soulseek → YouTube → other configured sources, same pipeline used by
    //    the search results' play button).
    if (typeof showLoadingOverlay === 'function') {
        showLoadingOverlay(`Searching for ${title}...`);
    }
    try {
        const streamResp = await fetch('/api/enhanced-search/stream-track', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                track_name: title,
                artist_name: artist,
                album_name: album || '',
                duration_ms: 0,
            }),
        });
        const streamData = await streamResp.json();
        if (typeof hideLoadingOverlay === 'function') hideLoadingOverlay();

        if (streamData.success && streamData.result) {
            if (typeof startStream === 'function') {
                await startStream(streamData.result);
            } else {
                showToast('Streaming not available', 'error');
            }
        } else {
            showToast(streamData.error || 'Track not found in library or any source', 'error');
        }
    } catch (e) {
        if (typeof hideLoadingOverlay === 'function') hideLoadingOverlay();
        showToast('Failed to play track', 'error');
        console.error('Stream fallback failed:', e);
    }
}

function _renderRecentPlays(tracks) {
    const el = document.getElementById('stats-recent-plays');
    if (!el) return;

    if (!tracks.length) {
        el.innerHTML = '<div style="color:rgba(255,255,255,0.3);font-size:0.85em;padding:12px;">No recent plays</div>';
        return;
    }

    const _ago = (dateStr) => {
        if (!dateStr) return '';
        const diff = Date.now() - new Date(dateStr).getTime();
        const mins = Math.floor(diff / 60000);
        if (mins < 60) return `${mins}m ago`;
        const hours = Math.floor(mins / 60);
        if (hours < 24) return `${hours}h ago`;
        const days = Math.floor(hours / 24);
        if (days < 30) return `${days}d ago`;
        return `${Math.floor(days / 30)}mo ago`;
    };

    el.innerHTML = tracks.map(t => `
        <div class="stats-recent-item">
            <button class="stats-play-btn stats-play-btn-sm" onclick="event.stopPropagation();playStatsTrack('${_esc(t.title).replace(/'/g, "\\'")}','${_esc(t.artist || '').replace(/'/g, "\\'")}','${_esc(t.album || '').replace(/'/g, "\\'")}')" title="Play">▶</button>
            <span class="stats-recent-title">${_esc(t.title)}</span>
            <span class="stats-recent-artist">${_esc(t.artist || '')}</span>
            <span class="stats-recent-time">${_ago(t.played_at)}</span>
        </div>
    `).join('');
}

// --- Initialization ---

function initializeImportPage() {
    if (!importPageState.initialized) {
        importPageState.initialized = true;
        importPageRefreshStaging();
        importPageLoadAutoGroups();
        importPageLoadSuggestions();
    }
}

async function importPageRefreshStaging() {
    // Clear finished jobs from the queue
    importPageClearFinishedJobs();

    try {
        const resp = await fetch('/api/import/staging/files');
        const data = await resp.json();
        if (!data.success) {
            document.getElementById('import-page-staging-path').textContent = `Import folder: error`;
            return;
        }

        importPageState.stagingFiles = data.files || [];
        document.getElementById('import-page-staging-path').textContent = `Import: ${data.staging_path || 'Not configured'}`;

        const totalSize = importPageState.stagingFiles.reduce((s, f) => s + (f.size || 0), 0);
        const sizeStr = totalSize > 1073741824 ? `${(totalSize / 1073741824).toFixed(1)} GB`
            : totalSize > 1048576 ? `${(totalSize / 1048576).toFixed(0)} MB`
                : `${(totalSize / 1024).toFixed(0)} KB`;
        document.getElementById('import-page-staging-stats').textContent =
            `${importPageState.stagingFiles.length} file${importPageState.stagingFiles.length !== 1 ? 's' : ''}${totalSize ? ' · ' + sizeStr : ''}`;

        // Refresh the current tab view after data is loaded
        if (importPageState.activeTab === 'singles') {
            importPageRenderSinglesList();
        } else if (importPageState.activeTab === 'album') {
            importPageLoadAutoGroups();
        }
        // Always refresh suggestions and groups in background
        importPageLoadSuggestions();
    } catch (err) {
        console.error('Failed to refresh staging:', err);
    }
}

function importPageSwitchTab(tab) {
    importPageState.activeTab = tab;
    document.getElementById('import-page-tab-album').classList.toggle('active', tab === 'album');
    document.getElementById('import-page-tab-singles').classList.toggle('active', tab === 'singles');
    document.getElementById('import-page-tab-auto')?.classList.toggle('active', tab === 'auto');
    document.getElementById('import-page-album-content').classList.toggle('active', tab === 'album');
    document.getElementById('import-page-singles-content')?.classList.toggle('active', tab === 'singles');
    document.getElementById('import-page-auto-content')?.classList.toggle('active', tab === 'auto');

    if (tab === 'singles' && importPageState.stagingFiles.length > 0) {
        importPageRenderSinglesList();
    }
    if (tab === 'auto') {
        _autoImportLoadStatus();
        _autoImportLoadResults();
        _autoImportStartPolling();
    } else {
        _autoImportStopPolling();
    }
}

// ── Auto-Import Tab ──
let _autoImportPollInterval = null;
let _autoImportFilter = 'all';
let _autoImportLastStatus = null;

function _autoImportStartPolling() {
    _autoImportStopPolling();
    _autoImportPollInterval = setInterval(async () => {
        if (importPageState.activeTab === 'auto') {
            await _autoImportLoadStatus();
            _autoImportLoadResults();
        }
    }, 5000);
}

function _autoImportStopPolling() {
    if (_autoImportPollInterval) { clearInterval(_autoImportPollInterval); _autoImportPollInterval = null; }
}

async function _autoImportToggle(enabled) {
    // Optimistically update toggle state so it doesn't flicker
    const toggle = document.getElementById('auto-import-enabled');
    if (toggle) toggle.checked = enabled;
    const statusText = document.getElementById('auto-import-status-text');
    if (statusText) statusText.textContent = enabled ? 'Starting...' : 'Stopping...';

    try {
        const res = await fetch('/api/auto-import/toggle', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled })
        });
        const data = await res.json();
        if (data.success) {
            showToast(enabled ? 'Auto-import enabled' : 'Auto-import disabled', 'success');
            _autoImportLoadStatus();
        } else {
            // Revert on failure
            if (toggle) toggle.checked = !enabled;
        }
    } catch (e) {
        showToast('Error: ' + e.message, 'error');
        if (toggle) toggle.checked = !enabled;
    }
}

async function _autoImportLoadStatus() {
    try {
        const res = await fetch('/api/auto-import/status');
        const data = await res.json();
        if (!data.success) return;
        _autoImportLastStatus = data;

        const toggle = document.getElementById('auto-import-enabled');
        const statusText = document.getElementById('auto-import-status-text');
        const settingsRow = document.getElementById('auto-import-settings-row');
        const scanNowBtn = document.getElementById('auto-import-scan-now');
        const progressEl = document.getElementById('auto-import-progress');
        const progressText = document.getElementById('auto-import-progress-text');

        if (toggle) toggle.checked = data.running;
        if (settingsRow) settingsRow.style.display = data.running ? '' : 'none';
        if (scanNowBtn) scanNowBtn.style.display = data.running ? '' : 'none';

        // Live scan + per-track processing progress.
        // `active_imports` (added when the worker switched to a bounded
        // executor pool) is the source of truth; multiple albums can be
        // in flight at once. Render each one on its own line; fall back
        // to the legacy single-line summary for older backend payloads.
        if (progressEl) {
            const active = Array.isArray(data.active_imports) ? data.active_imports : [];
            if (active.length > 0) {
                progressEl.style.display = '';
                if (progressText) {
                    const lines = active.map(a => {
                        const folder = a.folder_name || '...';
                        const idx = a.track_index || 0;
                        const total = a.track_total || 0;
                        const trackName = a.track_name || '';
                        if (a.status === 'processing' && total > 0) {
                            return `${folder} — track ${idx}/${total}: ${trackName}`;
                        }
                        if (a.status === 'matching') return `${folder} — matching tracks…`;
                        if (a.status === 'identifying') return `${folder} — identifying…`;
                        return `${folder} — queued`;
                    });
                    progressText.textContent = lines.length === 1
                        ? `Processing ${lines[0]}`
                        : `Processing ${lines.length} imports:\n${lines.join('\n')}`;
                }
            } else if (data.current_status === 'scanning') {
                progressEl.style.display = '';
                if (progressText) {
                    const stats = data.stats || {};
                    progressText.textContent = `Scanning… (${stats.scanned || 0} processed)`;
                }
            } else {
                progressEl.style.display = 'none';
            }
        }

        if (statusText) {
            if (data.paused) statusText.textContent = 'Paused';
            else if (data.current_status === 'processing') statusText.textContent = 'Processing...';
            else if (data.current_status === 'scanning') statusText.textContent = 'Scanning...';
            else if (data.running) {
                // Show last scan time
                let watchText = 'Watching';
                if (data.last_scan_time) {
                    try {
                        const lastScan = new Date(data.last_scan_time);
                        const diffS = Math.floor((Date.now() - lastScan) / 1000);
                        if (diffS < 60) watchText = `Watching (scanned ${diffS}s ago)`;
                        else if (diffS < 3600) watchText = `Watching (scanned ${Math.floor(diffS / 60)}m ago)`;
                    } catch (e) {}
                }
                statusText.textContent = watchText;
            } else statusText.textContent = 'Disabled';
            const _runningClass = data.current_status === 'scanning'
                ? 'scanning'
                : data.current_status === 'processing'
                    ? 'processing'
                    : 'active';
            statusText.className = 'auto-import-status ' + (data.running ? _runningClass : 'disabled');
        }
    } catch (e) {}
}

async function _autoImportLoadResults() {
    const container = document.getElementById('auto-import-results');
    if (!container) return;
    try {
        const res = await fetch('/api/auto-import/results?limit=100');
        const data = await res.json();
        if (!data.success || !data.results || data.results.length === 0) {
            if (!container.querySelector('.auto-import-card')) {
                container.innerHTML = `<div class="auto-import-empty">
                    <p>No imports yet. Drop album folders or single tracks into your import folder.</p>
                </div>`;
            }
            // Hide stats and filters
            const statsEl = document.getElementById('auto-import-stats');
            const filtersEl = document.getElementById('auto-import-filters');
            if (statsEl) statsEl.style.display = 'none';
            if (filtersEl) filtersEl.style.display = 'none';
            return;
        }

        // Compute stats
        const allResults = data.results;
        const importedCount = allResults.filter(r => r.status === 'completed' || r.status === 'approved').length;
        const reviewCount = allResults.filter(r => r.status === 'pending_review').length;
        const failedCount = allResults.filter(r => r.status === 'failed' || r.status === 'needs_identification').length;

        // Update stats
        const statsEl = document.getElementById('auto-import-stats');
        if (statsEl) {
            statsEl.style.display = '';
            document.getElementById('auto-import-stat-imported').textContent = `${importedCount} imported`;
            document.getElementById('auto-import-stat-review').textContent = `${reviewCount} review`;
            document.getElementById('auto-import-stat-failed').textContent = `${failedCount} failed`;
        }

        // Show filters
        const filtersEl = document.getElementById('auto-import-filters');
        if (filtersEl) {
            filtersEl.style.display = '';
            // Show batch action buttons when applicable
            const approveAllBtn = document.getElementById('auto-import-approve-all');
            const clearBtn = document.getElementById('auto-import-clear-completed');
            if (approveAllBtn) approveAllBtn.style.display = reviewCount > 0 ? '' : 'none';
            if (clearBtn) clearBtn.style.display = (importedCount + failedCount) > 0 ? '' : 'none';
        }

        // Apply filter
        let filtered = allResults;
        if (_autoImportFilter === 'pending') filtered = allResults.filter(r => r.status === 'pending_review');
        else if (_autoImportFilter === 'imported') filtered = allResults.filter(r => r.status === 'completed' || r.status === 'approved');
        else if (_autoImportFilter === 'failed') filtered = allResults.filter(r => r.status === 'failed' || r.status === 'needs_identification');

        if (filtered.length === 0) {
            const filterName = _autoImportFilter === 'pending' ? 'pending review' : _autoImportFilter;
            container.innerHTML = `<div class="auto-import-empty"><p>No ${filterName} items.</p></div>`;
            return;
        }

        container.innerHTML = filtered.map((r, idx) => {
            const confPct = Math.round((r.confidence || 0) * 100);
            const confClass = confPct >= 90 ? 'high' : confPct >= 70 ? 'medium' : 'low';
            const statusLabels = {
                'completed': 'Imported', 'pending_review': 'Needs Review',
                'needs_identification': 'Unidentified', 'failed': 'Failed',
                'scanning': 'Scanning...', 'matched': 'Matched',
                'rejected': 'Dismissed', 'approved': 'Approved',
                'processing': 'Processing',
            };
            const statusIcons = {
                'completed': '\u2713', 'pending_review': '\u26A0',
                'needs_identification': '\u2717', 'failed': '\u2717',
                'scanning': '\u231B', 'matched': '\u2713',
                'rejected': '\u2715', 'approved': '\u2713',
                'processing': '\u29D7',
            };
            const statusLabel = statusLabels[r.status] || r.status;
            const statusIcon = statusIcons[r.status] || '';
            const statusClass = r.status === 'completed' ? 'completed' : r.status === 'pending_review' ? 'review' :
                r.status === 'failed' || r.status === 'needs_identification' ? 'failed' :
                r.status === 'processing' ? 'processing' : 'neutral';

            // Live per-track progress for the row currently being processed.
            // Match by folder_hash through the `active_imports` array
            // — the worker now runs multiple imports in parallel via a
            // bounded executor pool, so `current_folder` alone can't
            // identify a row's live state.
            const liveStatus = _autoImportLastStatus;
            const liveActive = (liveStatus && Array.isArray(liveStatus.active_imports))
                ? liveStatus.active_imports.find(a => a.folder_hash === r.folder_hash)
                : null;
            const isLiveProcessing = r.status === 'processing'
                && liveActive && liveActive.status === 'processing';
            const liveTrackIdx = isLiveProcessing ? (liveActive.track_index || 0) : 0;
            const liveTrackTotal = isLiveProcessing ? (liveActive.track_total || 0) : 0;
            const liveTrackName = isLiveProcessing ? (liveActive.track_name || '') : '';

            // Parse match data for track details
            let matchCount = 0, totalTracks = 0, trackDetails = [];
            if (r.match_data) {
                try {
                    const md = typeof r.match_data === 'string' ? JSON.parse(r.match_data) : r.match_data;
                    matchCount = md.matched_count || 0;
                    totalTracks = md.total_tracks || 0;
                    if (md.matches) {
                        trackDetails = md.matches.map(m => ({
                            name: m.track_name || m.track?.name || 'Unknown',
                            file: m.file ? m.file.split(/[/\\]/).pop() : '?',
                            confidence: Math.round((m.confidence || 0) * 100),
                        }));
                    }
                } catch (e) {}
            }

            let matchSummary = totalTracks > 0 ? `${matchCount}/${totalTracks} tracks` : `${r.total_files} files`;
            if (isLiveProcessing && liveTrackTotal > 0) {
                matchSummary = `track ${liveTrackIdx}/${liveTrackTotal}: ${liveTrackName}`;
            }
            const methodLabels = { tags: 'Tags', folder_name: 'Folder Name', acoustid: 'AcoustID', filename: 'Filename' };
            const methodLabel = methodLabels[r.identification_method] || r.identification_method || '';

            // Time ago
            let timeAgo = '';
            if (r.created_at) {
                try {
                    const d = new Date(r.created_at);
                    const diffM = Math.floor((Date.now() - d) / 60000);
                    if (diffM < 1) timeAgo = 'just now';
                    else if (diffM < 60) timeAgo = `${diffM}m ago`;
                    else if (diffM < 1440) timeAgo = `${Math.floor(diffM / 60)}h ago`;
                    else timeAgo = `${Math.floor(diffM / 1440)}d ago`;
                } catch (e) {}
            }

            let actions = '';
            if (r.status === 'pending_review') {
                actions = `<div class="auto-import-actions">
                    <button class="watchlist-action-btn watchlist-action-primary" onclick="event.stopPropagation(); _autoImportApprove(${r.id})">Approve & Import</button>
                    <button class="watchlist-action-btn watchlist-action-secondary" onclick="event.stopPropagation(); _autoImportReject(${r.id})">Dismiss</button>
                </div>`;
            }

            // Expanded track list (hidden by default)
            let trackListHtml = '';
            if (trackDetails.length > 0) {
                trackListHtml = `<div class="auto-import-track-list" id="auto-import-tracks-${idx}">
                    <div class="auto-import-track-list-header">
                        <span>Track</span><span>Matched File</span><span>Conf</span>
                    </div>
                    ${trackDetails.map((t, tIdx) => {
                        const tConfClass = t.confidence >= 90 ? 'high' : t.confidence >= 70 ? 'medium' : 'low';
                        // 1-based liveTrackIdx — current row glows, prior rows dim as "done".
                        let rowState = '';
                        if (isLiveProcessing && liveTrackIdx > 0) {
                            if (tIdx + 1 === liveTrackIdx) rowState = ' auto-import-track-row-active';
                            else if (tIdx + 1 < liveTrackIdx) rowState = ' auto-import-track-row-done';
                        }
                        return `<div class="auto-import-track-row${rowState}">
                            <span class="auto-import-track-name">${escapeHtml(t.name)}</span>
                            <span class="auto-import-track-file">${escapeHtml(t.file)}</span>
                            <span class="auto-import-track-conf auto-import-conf-${tConfClass}">${t.confidence}%</span>
                        </div>`;
                    }).join('')}
                </div>`;
            }

            return `<div class="auto-import-card auto-import-${statusClass}" onclick="_autoImportToggleDetail(${idx})" style="cursor:pointer">
                <div class="auto-import-card-top">
                    <div class="auto-import-card-left">
                        ${r.image_url ? `<img class="auto-import-card-art" src="${r.image_url}" alt="" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'"><div class="auto-import-card-art-fallback" style="display:none">\uD83D\uDCBF</div>` : `<div class="auto-import-card-art-fallback">\uD83D\uDCBF</div>`}
                    </div>
                    <div class="auto-import-card-center">
                        <div class="auto-import-card-album">${escapeHtml(r.album_name || r.folder_name)}</div>
                        <div class="auto-import-card-artist">${escapeHtml(r.artist_name || 'Unknown Artist')}</div>
                        <div class="auto-import-card-meta">
                            <span>${matchSummary}</span>
                            ${methodLabel ? `<span class="auto-import-method-badge">${methodLabel}</span>` : ''}
                            ${timeAgo ? `<span>${timeAgo}</span>` : ''}
                        </div>
                        ${r.error_message ? `<div class="auto-import-card-error">${escapeHtml(r.error_message)}</div>` : ''}
                    </div>
                    <div class="auto-import-card-right">
                        <div class="auto-import-status-badge auto-import-badge-${statusClass}">${statusIcon} ${statusLabel}</div>
                        <div class="auto-import-confidence-bar">
                            <div class="auto-import-confidence-fill auto-import-conf-${confClass}" style="width:${confPct}%"></div>
                        </div>
                        <div class="auto-import-confidence-text">${confPct}% confidence</div>
                        ${actions}
                    </div>
                </div>
                <div class="auto-import-card-folder-path">${escapeHtml(r.folder_name)}</div>
                ${trackListHtml}
            </div>`;
        }).join('');

    } catch (e) {}
}

async function _autoImportSaveSettings() {
    const confidence = (document.getElementById('auto-import-confidence')?.value || 90) / 100;
    const interval = parseInt(document.getElementById('auto-import-interval')?.value || 60);
    try {
        await fetch('/api/auto-import/settings', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ confidence_threshold: confidence, scan_interval: interval })
        });
        showToast('Settings saved', 'success');
    } catch (e) { showToast('Error', 'error'); }
}

function _autoImportSetFilter(filter) {
    _autoImportFilter = filter;
    document.querySelectorAll('#auto-import-filters .adl-pill').forEach(p =>
        p.classList.toggle('active', p.dataset.filter === filter));
    _autoImportLoadResults();
}

async function _autoImportScanNow() {
    try {
        const res = await fetch('/api/auto-import/scan-now', { method: 'POST' });
        const data = await res.json();
        if (data.success) {
            showToast('Scan triggered', 'success');
            _autoImportLoadStatus();
        } else {
            showToast(data.error || 'Failed to trigger scan', 'error');
        }
    } catch (e) { showToast('Error: ' + e.message, 'error'); }
}

async function _autoImportApproveAll() {
    const confirmed = await showConfirmDialog({
        title: 'Approve All',
        message: 'Approve and import all pending review items?',
        confirmText: 'Approve All',
    });
    if (!confirmed) return;
    try {
        const res = await fetch('/api/auto-import/approve-all', { method: 'POST' });
        const data = await res.json();
        if (data.success) {
            showToast(`Approved ${data.count || 0} items`, 'success');
            _autoImportLoadResults();
        } else {
            showToast(data.error || 'Failed', 'error');
        }
    } catch (e) { showToast('Error: ' + e.message, 'error'); }
}

async function _autoImportClearCompleted() {
    try {
        const res = await fetch('/api/auto-import/clear-completed', { method: 'POST' });
        const data = await res.json();
        if (data.success) {
            showToast(`Cleared ${data.count || 0} imported items`, 'success');
            _autoImportLoadResults();
        } else {
            showToast(data.error || 'Failed', 'error');
        }
    } catch (e) { showToast('Error: ' + e.message, 'error'); }
}

function _autoImportToggleDetail(idx) {
    const trackList = document.getElementById(`auto-import-tracks-${idx}`);
    if (trackList) {
        trackList.classList.toggle('expanded');
    }
}
window._autoImportToggleDetail = _autoImportToggleDetail;
window._autoImportSetFilter = _autoImportSetFilter;
window._autoImportScanNow = _autoImportScanNow;
window._autoImportApproveAll = _autoImportApproveAll;
window._autoImportClearCompleted = _autoImportClearCompleted;

async function _autoImportApprove(id) {
    try {
        const res = await fetch(`/api/auto-import/approve/${id}`, { method: 'POST' });
        const data = await res.json();
        if (data.success) { showToast('Approved', 'success'); _autoImportLoadResults(); }
        else showToast(data.error || 'Failed', 'error');
    } catch (e) { showToast('Error', 'error'); }
}

async function _autoImportReject(id) {
    try {
        const res = await fetch(`/api/auto-import/reject/${id}`, { method: 'POST' });
        const data = await res.json();
        if (data.success) { showToast('Dismissed', 'success'); _autoImportLoadResults(); }
        else showToast(data.error || 'Failed', 'error');
    } catch (e) { showToast('Error', 'error'); }
}

// --- Album Tab: Auto-Detected Groups (from file tags) ---

async function importPageLoadAutoGroups() {
    const grid = document.getElementById('import-page-suggestions-grid');
    if (!grid) return;

    try {
        const resp = await fetch('/api/import/staging/groups');
        if (!resp.ok) return;
        const data = await resp.json();

        if (!data.success || !data.groups || data.groups.length === 0) return;

        // Build auto-groups section above suggestions
        let groupsContainer = document.getElementById('import-page-auto-groups');
        if (!groupsContainer) {
            groupsContainer = document.createElement('div');
            groupsContainer.id = 'import-page-auto-groups';
            groupsContainer.style.marginBottom = '16px';
            const suggestionsSection = document.getElementById('import-page-suggestions');
            if (suggestionsSection) {
                suggestionsSection.parentNode.insertBefore(groupsContainer, suggestionsSection);
            } else {
                grid.parentNode.insertBefore(groupsContainer, grid);
            }
        }

        groupsContainer.innerHTML = `
            <div style="font-size:0.82em;font-weight:600;letter-spacing:0.05em;text-transform:uppercase;color:rgba(255,255,255,0.5);margin-bottom:10px;">
                Auto-Detected Albums
            </div>
            <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:10px;">
                ${data.groups.map((g, idx) => `
                    <div class="import-page-album-card" style="cursor:pointer;display:flex;align-items:center;gap:12px;padding:12px;border-radius:10px;background:rgba(255,255,255,0.03);border:1px solid rgba(255,255,255,0.06);transition:all 0.2s;"
                         onmouseenter="this.style.borderColor='rgba(255,255,255,0.12)';this.style.background='rgba(255,255,255,0.05)'"
                         onmouseleave="this.style.borderColor='rgba(255,255,255,0.06)';this.style.background='rgba(255,255,255,0.03)'"
                         onclick="importPageMatchAutoGroup(${idx})">
                        <div style="width:48px;height:48px;border-radius:8px;background:rgba(var(--accent-rgb,29,185,84),0.15);display:flex;align-items:center;justify-content:center;flex-shrink:0;font-size:1.2em;">
                            ${g.file_count}
                        </div>
                        <div style="min-width:0;">
                            <div style="font-size:0.92em;font-weight:500;color:#fff;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;" title="${_escAttr(g.album)}">${_esc(g.album)}</div>
                            <div style="font-size:0.8em;color:rgba(255,255,255,0.5);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;" title="${_escAttr(g.artist)}">${_esc(g.artist)} · ${g.file_count} tracks</div>
                        </div>
                    </div>
                `).join('')}
            </div>
        `;

        // Store groups for click handler
        importPageState._autoGroups = data.groups;
    } catch (err) {
        console.warn('Failed to load auto-groups:', err);
    }
}

async function importPageMatchAutoGroup(groupIdx) {
    const group = importPageState._autoGroups?.[groupIdx];
    if (!group) return;

    // Search for the album by name + artist
    const query = `${group.artist} ${group.album}`;
    const searchInput = document.getElementById('import-page-album-search-input');
    if (searchInput) searchInput.value = query;

    // Hide suggestions/groups, show search results
    const suggestionsEl = document.getElementById('import-page-suggestions');
    const groupsEl = document.getElementById('import-page-auto-groups');
    if (suggestionsEl) suggestionsEl.style.display = 'none';
    if (groupsEl) groupsEl.style.display = 'none';

    const grid = document.getElementById('import-page-album-results');
    if (grid) grid.innerHTML = '<div style="color:#888;text-align:center;padding:20px;">Searching...</div>';

    try {
        const resp = await fetch(`/api/import/search/albums?q=${encodeURIComponent(query)}&limit=12`);
        const data = await resp.json();

        if (data.success && data.albums && data.albums.length > 0) {
            // Store file_paths filter so match only includes this group's files
            importPageState._autoGroupFilePaths = group.file_paths;

            // Render results — user picks the right album
            grid.innerHTML = data.albums.map(a => _renderSuggestionCard(a)).join('');
        } else {
            grid.innerHTML = '<div style="color:#888;text-align:center;padding:20px;">No albums found — try searching manually</div>';
        }
    } catch (err) {
        console.error('Auto-group search failed:', err);
        if (grid) grid.innerHTML = '<div style="color:#888;text-align:center;padding:20px;">Search failed</div>';
    }
}

// --- Album Tab: Suggestions (server-side cache, just fetch and render) ---

async function importPageLoadSuggestions() {
    const section = document.getElementById('import-page-suggestions');
    const grid = document.getElementById('import-page-suggestions-grid');
    if (!section || !grid) return;

    try {
        const resp = await fetch('/api/import/staging/suggestions');
        if (!resp.ok) return;
        const data = await resp.json();

        if (!data.success || !data.suggestions || data.suggestions.length === 0) {
            if (!data.ready) {
                // Server is still building cache — show placeholder, retry shortly
                section.style.display = '';
                grid.innerHTML = '<div style="color:#888;font-size:13px;padding:8px;">Loading suggestions...</div>';
                setTimeout(() => importPageLoadSuggestions(), 3000);
            } else {
                section.style.display = 'none';
                grid.innerHTML = '';
            }
            return;
        }

        section.style.display = '';
        grid.innerHTML = data.suggestions.map(a => _renderSuggestionCard(a)).join('');
    } catch (err) {
        // Network error or server not ready — fail silently
        console.warn('Failed to load import suggestions:', err);
    }
}

function _renderSuggestionCard(a) {
    // Cache the album lookup so importPageSelectAlbum can pull source +
    // name + artist on click (the onclick can only carry the ID string
    // — see github issue #524 root cause).
    importPageState._albumLookup[a.id] = {
        id: a.id, name: a.name || '', artist: a.artist || '', source: a.source || '',
    };
    return `<div class="import-page-album-card" onclick="importPageSelectAlbum('${_escAttr(a.id)}')">
        <img src="${a.image_url || '/static/placeholder.png'}" alt="${_escAttr(a.name)}" loading="lazy" onerror="this.src='/static/placeholder.png'">
        <div class="import-page-album-card-title" title="${_escAttr(a.name)}">${_esc(a.name)}</div>
        <div class="import-page-album-card-artist" title="${_escAttr(a.artist)}">${_esc(a.artist)}</div>
        <div class="import-page-album-card-meta">${a.total_tracks} tracks · ${a.release_date ? a.release_date.substring(0, 4) : ''}</div>
    </div>`;
}

// --- Album Tab: Search ---

async function importPageSearchAlbum() {
    const query = document.getElementById('import-page-album-search-input').value.trim();
    if (!query) return;

    document.getElementById('import-page-suggestions').style.display = 'none';
    const groupsEl = document.getElementById('import-page-auto-groups');
    if (groupsEl) groupsEl.style.display = 'none';
    const grid = document.getElementById('import-page-album-results');
    grid.innerHTML = '<div style="color:#888;text-align:center;padding:20px;">Searching...</div>';

    try {
        const resp = await fetch(`/api/import/search/albums?q=${encodeURIComponent(query)}&limit=12`);
        const data = await resp.json();
        if (!data.success || !data.albums.length) {
            grid.innerHTML = '<div style="color:#888;text-align:center;padding:20px;">No albums found</div>';
            return;
        }
        grid.innerHTML = data.albums.map(a => {
            // Cache album lookup so the click handler can include source
            // + name + artist on the match POST (see #524).
            importPageState._albumLookup[a.id] = {
                id: a.id, name: a.name || '', artist: a.artist || '', source: a.source || '',
            };
            return `
            <div class="import-page-album-card" onclick="importPageSelectAlbum('${_escAttr(a.id)}')">
                <img src="${a.image_url || '/static/placeholder.png'}" alt="${_escAttr(a.name)}" loading="lazy" onerror="this.src='/static/placeholder.png'">
                <div class="import-page-album-card-title" title="${_escAttr(a.name)}">${_esc(a.name)}</div>
                <div class="import-page-album-card-artist" title="${_escAttr(a.artist)}">${_esc(a.artist)}</div>
                <div class="import-page-album-card-meta">${a.total_tracks} tracks · ${a.release_date ? a.release_date.substring(0, 4) : ''}</div>
            </div>`;
        }).join('');
        document.getElementById('import-page-album-clear-btn').classList.remove('hidden');
    } catch (err) {
        grid.innerHTML = `<div style="color:#ef4444;text-align:center;padding:20px;">Error: ${err.message}</div>`;
    }
}

// --- Album Tab: Select Album & Match ---

async function importPageSelectAlbum(albumId) {
    document.getElementById('import-page-album-search-section').classList.add('hidden');
    document.getElementById('import-page-album-match-section').classList.remove('hidden');

    const matchList = document.getElementById('import-page-match-list');
    matchList.innerHTML = '<div style="color:#888;text-align:center;padding:20px;">Matching files to tracklist...</div>';

    try {
        // Include file_paths filter if matching from an auto-group.
        // CRITICAL: include source + album_name + album_artist from the
        // search/suggestion result. Without `source`, the backend can't
        // route the lookup to the metadata source the album_id came
        // from — for example a Deezer album_id needs Deezer's get_album
        // call. Cross-source lookup fails silently, returns the
        // failure-fallback dict with album_id-as-name + Unknown Artist
        // + 0 tracks, then the import flow writes that broken metadata
        // to the library DB (github issue #524).
        const cached = importPageState._albumLookup[albumId] || {};
        const matchBody = {
            album_id: albumId,
            source: cached.source || '',
            album_name: cached.name || '',
            album_artist: cached.artist || '',
        };
        if (importPageState._autoGroupFilePaths) {
            matchBody.file_paths = importPageState._autoGroupFilePaths;
            importPageState._autoGroupFilePaths = null; // clear after use
        }
        const resp = await fetch('/api/import/album/match', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(matchBody)
        });
        const data = await resp.json();
        if (!data.success) {
            matchList.innerHTML = `<div style="color:#ef4444;padding:20px;">Error: ${data.error}</div>`;
            return;
        }

        importPageState.albumData = data;
        importPageState.matchOverrides = {};

        // Render hero
        const album = data.album;
        document.getElementById('import-page-album-hero').innerHTML = `
            <img src="${album.image_url || '/static/placeholder.png'}" alt="${_escAttr(album.name)}" loading="lazy" onerror="this.src='/static/placeholder.png'">
            <div class="import-page-album-hero-info">
                <div class="import-page-album-hero-title">${_esc(album.name)}</div>
                <div class="import-page-album-hero-artist">${_esc(album.artist)}</div>
                <div class="import-page-album-hero-meta">${album.total_tracks} tracks · ${album.release_date ? album.release_date.substring(0, 4) : ''}</div>
            </div>
        `;

        importPageRenderMatchList();
    } catch (err) {
        matchList.innerHTML = `<div style="color:#ef4444;padding:20px;">Error: ${err.message}</div>`;
    }
}

function importPageRenderMatchList() {
    const data = importPageState.albumData;
    if (!data) return;

    const matchList = document.getElementById('import-page-match-list');
    const overrides = importPageState.matchOverrides;

    // Build effective matches: auto-match overridden by manual overrides
    // Also track which staging files are used (auto or override)
    const usedStagingFiles = new Set();

    // First pass: collect overridden indices
    Object.values(overrides).forEach(sfIdx => usedStagingFiles.add(sfIdx));

    // Build rows
    let matchedCount = 0;
    const rows = data.matches.map((m, idx) => {
        const trackInfo = _importPageGetTrackDisplayInfo(m, idx);
        let file = null;
        let confidence = m.confidence;
        let isOverride = false;

        if (overrides.hasOwnProperty(idx)) {
            const sfIdx = overrides[idx];
            if (sfIdx === -1) {
                // Forcibly unmatched — no file
                file = null;
            } else {
                // Manual override
                file = importPageState.stagingFiles[sfIdx] || null;
                confidence = 1.0;
                isOverride = true;
                usedStagingFiles.add(sfIdx);
            }
        } else if (m.staging_file) {
            file = m.staging_file;
            // Check if this file was reassigned to another track via override
            const autoFileName = m.staging_file.filename;
            const reassigned = Object.entries(overrides).some(([tIdx, sfIdx]) => {
                const sf = importPageState.stagingFiles[sfIdx];
                return sf && sf.filename === autoFileName && parseInt(tIdx) !== idx;
            });
            if (!reassigned) {
                usedStagingFiles.add(-1); // placeholder — auto-matched file
            } else {
                file = null; // file was reassigned elsewhere
            }
        }

        if (file) matchedCount++;
        const confPercent = Math.round(confidence * 100);
        const confClass = confidence >= 0.7 ? '' : 'low';

        return `
            <div class="import-page-match-row ${file ? 'matched' : ''}"
                 ondragover="importPageHandleDragOver(event)" ondragleave="this.classList.remove('drag-over')" ondrop="importPageHandleDrop(event, ${idx})"
                 onclick="importPageTapAssign(${idx})">
                <span class="import-page-match-num">${trackInfo.displayTrackNumber}</span>
                <span class="import-page-match-track">${_esc(trackInfo.name)}</span>
                <span class="import-page-match-file ${file ? 'has-file' : ''}">
                    ${file
                ? `<span class="import-page-match-file-name">${_esc(file.filename)}</span>
                           <span class="import-page-match-confidence ${confClass}">${confPercent}%</span>`
                : `<span class="import-page-match-drop-zone">Drop a file here</span>`}
                </span>
                <span>${file ? `<button class="import-page-match-unmatch" onclick="event.stopPropagation(); importPageUnmatchTrack(${idx})">✕</button>` : ''}</span>
            </div>
        `;
    });

    matchList.innerHTML = rows.join('');

    // Unmatched file pool
    const unmatchedFiles = [];
    importPageState.stagingFiles.forEach((f, i) => {
        // Check if used by override
        if (Object.values(overrides).includes(i)) return;
        // Check if used by auto-match (not overridden away)
        const autoUsed = data.matches.some((m, mIdx) => {
            if (overrides.hasOwnProperty(mIdx)) return false;
            return m.staging_file && m.staging_file.filename === f.filename;
        });
        if (autoUsed) return;
        unmatchedFiles.push({ file: f, index: i });
    });

    const poolChips = document.getElementById('import-page-pool-chips');
    document.getElementById('import-page-unmatched-count').textContent = unmatchedFiles.length;

    if (unmatchedFiles.length === 0) {
        poolChips.innerHTML = '<span class="import-page-pool-empty">All files matched</span>';
    } else {
        poolChips.innerHTML = unmatchedFiles.map(({ file, index }) => `
            <span class="import-page-file-chip ${importPageState.tapSelectedChip === index ? 'selected' : ''}"
                  draggable="true" ondragstart="importPageStartDrag(event, ${index})"
                  onclick="event.stopPropagation(); importPageTapSelectChip(${index})">
                ${_esc(file.filename)}
            </span>
        `).join('');
    }

    // Stats & button
    document.getElementById('import-page-match-stats').textContent = `${matchedCount} of ${data.matches.length} tracks matched`;
    const processBtn = document.getElementById('import-page-album-process-btn');
    processBtn.disabled = matchedCount === 0;
    processBtn.textContent = `Process ${matchedCount} Track${matchedCount !== 1 ? 's' : ''}`;
}

function _importPageGetTrackDisplayInfo(item, index) {
    const track = item?.track || item?.spotify_track || {};
    const rawTrackNumber = track.track_number ?? track.trackNumber ?? null;
    const trackNumber = rawTrackNumber === null || rawTrackNumber === undefined || rawTrackNumber === ''
        ? null
        : String(rawTrackNumber).split('/')[0].trim();

    return {
        track,
        name: track.name || track.title || `Track ${index + 1}`,
        trackNumber,
        displayTrackNumber: trackNumber || String(index + 1),
    };
}

// --- Album Tab: Drag and Drop ---

function importPageStartDrag(event, stagingFileIndex) {
    event.dataTransfer.setData('text/plain', stagingFileIndex.toString());
    event.dataTransfer.effectAllowed = 'move';
}

function importPageHandleDragOver(event) {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
    event.currentTarget.classList.add('drag-over');
    // Remove drag-over from others
    document.querySelectorAll('.import-page-match-row.drag-over').forEach(el => {
        if (el !== event.currentTarget) el.classList.remove('drag-over');
    });
}

function importPageHandleDrop(event, trackIndex) {
    event.preventDefault();
    event.currentTarget.classList.remove('drag-over');
    const stagingFileIndex = parseInt(event.dataTransfer.getData('text/plain'));
    if (isNaN(stagingFileIndex)) return;

    // Remove this staging file from any other track it was assigned to
    Object.keys(importPageState.matchOverrides).forEach(k => {
        if (importPageState.matchOverrides[k] === stagingFileIndex) {
            delete importPageState.matchOverrides[k];
        }
    });

    importPageState.matchOverrides[trackIndex] = stagingFileIndex;
    importPageState.tapSelectedChip = null;
    importPageRenderMatchList();
}

// Mobile tap-to-assign fallback
function importPageTapSelectChip(stagingFileIndex) {
    if (importPageState.tapSelectedChip === stagingFileIndex) {
        importPageState.tapSelectedChip = null;
    } else {
        importPageState.tapSelectedChip = stagingFileIndex;
    }
    importPageRenderMatchList();
}

function importPageTapAssign(trackIndex) {
    if (importPageState.tapSelectedChip === null) return;
    const stagingFileIndex = importPageState.tapSelectedChip;

    // Remove from any other track
    Object.keys(importPageState.matchOverrides).forEach(k => {
        if (importPageState.matchOverrides[k] === stagingFileIndex) {
            delete importPageState.matchOverrides[k];
        }
    });

    importPageState.matchOverrides[trackIndex] = stagingFileIndex;
    importPageState.tapSelectedChip = null;
    importPageRenderMatchList();
}

function importPageUnmatchTrack(trackIndex) {
    delete importPageState.matchOverrides[trackIndex];
    // Also remove auto-match by setting override to -1 special value? No — just delete override and let auto-match stay.
    // Actually, to truly unmatch: we need to suppress the auto-match too.
    // We'll use a sentinel: override = -1 means "forcibly unmatched"
    const m = importPageState.albumData?.matches[trackIndex];
    if (m && m.staging_file) {
        importPageState.matchOverrides[trackIndex] = -1; // sentinel: force no match
    }
    importPageRenderMatchList();
}

function importPageAutoRematch() {
    importPageState.matchOverrides = {};
    importPageState.tapSelectedChip = null;
    importPageRenderMatchList();
}

// --- Album Tab: Process ---

function importPageProcessAlbum() {
    const data = importPageState.albumData;
    if (!data) return;

    // Build effective matches with overrides applied
    const overrides = importPageState.matchOverrides;
    const effectiveMatches = [];
    data.matches.forEach((m, idx) => {
        if (overrides.hasOwnProperty(idx)) {
            if (overrides[idx] === -1) return; // forcibly unmatched — skip
            const sf = importPageState.stagingFiles[overrides[idx]];
            effectiveMatches.push({ ...m, staging_file: sf, confidence: 1.0 });
        } else if (m.staging_file !== null) {
            effectiveMatches.push(m);
        }
    });

    if (effectiveMatches.length === 0) return;

    // Add to queue and reset search immediately so user can queue more
    const album = data.album;
    _importQueueAdd({
        type: 'album',
        label: album.name,
        sublabel: `${album.artist} · ${effectiveMatches.length} tracks`,
        imageUrl: album.image_url,
        items: effectiveMatches,
        albumData: album,
    });

    importPageResetAlbumSearch();
}

function importPageResetAlbumSearch() {
    importPageState.albumData = null;
    importPageState.matchOverrides = {};
    importPageState.tapSelectedChip = null;
    importPageState._autoGroupFilePaths = null;

    document.getElementById('import-page-album-search-section').classList.remove('hidden');
    document.getElementById('import-page-album-match-section').classList.add('hidden');

    // Clear search
    document.getElementById('import-page-album-results').innerHTML = '';
    document.getElementById('import-page-album-search-input').value = '';
    document.getElementById('import-page-album-clear-btn').classList.add('hidden');

    // Re-show auto-groups
    const groupsEl = document.getElementById('import-page-auto-groups');
    if (groupsEl) groupsEl.style.display = '';

    // Refresh suggestions & staging
    importPageLoadAutoGroups();
    importPageLoadSuggestions();
    importPageRefreshStaging();
}

// --- Singles Tab ---

function importPageRenderSinglesList() {
    const list = document.getElementById('import-page-singles-list');
    const files = importPageState.stagingFiles;

    if (files.length === 0) {
        list.innerHTML = '<div class="import-page-empty-state">No audio files found in import folder</div>';
        return;
    }

    list.innerHTML = files.map((f, i) => {
        const isSelected = importPageState.selectedSingles.has(i);
        const manualMatch = importPageState.singlesManualMatches[i];
        const searchOpen = document.querySelector(`[data-singles-search="${i}"]`);

        let html = `
            <div class="import-page-single-item ${manualMatch ? 'matched' : ''}" data-single-idx="${i}">
                <div class="import-page-single-checkbox ${isSelected ? 'checked' : ''}"
                     onclick="importPageToggleSingle(${i})"></div>
                <div class="import-page-single-info">
                    <div class="import-page-single-filename">${_esc(f.filename)}</div>
                    <div class="import-page-single-meta">
                        ${f.title ? `<span>${_esc(f.title)}</span>` : ''}
                        ${f.artist ? `<span>${_esc(f.artist)}</span>` : ''}
                        ${f.extension ? `<span>${f.extension}</span>` : ''}
                    </div>
                    ${manualMatch ? `
                        <div class="import-page-single-matched-info">
                            &#10003; ${_esc(manualMatch.name)} - ${_esc(manualMatch.artist)}
                            <span class="import-page-single-matched-change" onclick="event.stopPropagation(); importPageOpenSingleSearch(${i})">change</span>
                        </div>
                    ` : ''}
                </div>
                <div class="import-page-single-actions">
                    <button class="import-page-identify-btn" onclick="event.stopPropagation(); importPageOpenSingleSearch(${i})">
                        &#128269; Identify
                    </button>
                </div>
            </div>
        `;
        return html;
    }).join('');

    importPageUpdateSinglesProcessButton();
}

function importPageToggleSingle(idx) {
    if (importPageState.selectedSingles.has(idx)) {
        importPageState.selectedSingles.delete(idx);
    } else {
        importPageState.selectedSingles.add(idx);
    }
    // Update checkbox UI without full re-render
    const item = document.querySelector(`[data-single-idx="${idx}"]`);
    if (item) {
        const cb = item.querySelector('.import-page-single-checkbox');
        if (cb) cb.classList.toggle('checked', importPageState.selectedSingles.has(idx));
    }
    importPageUpdateSinglesProcessButton();
}

function importPageSelectAllSingles() {
    const allSelected = importPageState.selectedSingles.size === importPageState.stagingFiles.length;
    if (allSelected) {
        importPageState.selectedSingles.clear();
    } else {
        importPageState.stagingFiles.forEach((_, i) => importPageState.selectedSingles.add(i));
    }
    document.getElementById('import-page-select-all-text').textContent = allSelected ? 'Select All' : 'Deselect All';
    // Update all checkboxes
    document.querySelectorAll('.import-page-single-checkbox').forEach((cb, i) => {
        cb.classList.toggle('checked', importPageState.selectedSingles.has(i));
    });
    importPageUpdateSinglesProcessButton();
}

function importPageUpdateSinglesProcessButton() {
    const btn = document.getElementById('import-page-singles-process-btn');
    const count = importPageState.selectedSingles.size;
    btn.textContent = `Process Selected (${count})`;
    btn.disabled = count === 0;
}

function importPageOpenSingleSearch(fileIdx) {
    const item = document.querySelector(`[data-single-idx="${fileIdx}"]`);
    if (!item) return;

    // Remove any existing search panel
    const existing = item.querySelector('.import-page-single-search-panel');
    if (existing) {
        existing.remove();
        return;
    }

    // Close other open panels
    document.querySelectorAll('.import-page-single-search-panel').forEach(p => p.remove());

    const f = importPageState.stagingFiles[fileIdx];
    const defaultQuery = [f.artist, f.title].filter(Boolean).join(' ') || f.filename.replace(/\.[^.]+$/, '');

    const panel = document.createElement('div');
    panel.className = 'import-page-single-search-panel';
    panel.innerHTML = `
        <div class="import-page-single-search-bar">
            <input type="text" class="import-page-single-search-input"
                   value="${_escAttr(defaultQuery)}" placeholder="Search artist - title..."
                   onkeydown="if(event.key==='Enter')importPageSearchSingleTrack(${fileIdx}, this.value)">
            <button class="import-page-single-search-go"
                    onclick="importPageSearchSingleTrack(${fileIdx}, this.previousElementSibling.value)">Search</button>
        </div>
        <div class="import-page-single-search-results" id="import-single-results-${fileIdx}"></div>
    `;
    item.appendChild(panel);

    // Auto-search
    const input = panel.querySelector('input');
    input.focus();
    if (defaultQuery) {
        importPageSearchSingleTrack(fileIdx, defaultQuery);
    }
}

async function importPageSearchSingleTrack(fileIdx, query) {
    if (!query || !query.trim()) return;

    const resultsDiv = document.getElementById(`import-single-results-${fileIdx}`);
    if (!resultsDiv) return;
    resultsDiv.innerHTML = '<div style="color:#888;padding:8px;font-size:12px;">Searching...</div>';

    try {
        const resp = await fetch(`/api/import/search/tracks?q=${encodeURIComponent(query.trim())}&limit=6`);
        const data = await resp.json();
        if (!data.success || !data.tracks.length) {
            resultsDiv.innerHTML = '<div style="color:#888;padding:8px;font-size:12px;">No results found</div>';
            return;
        }
        // Store results in a temp cache so we can reference by index
        window._importSingleSearchResults = window._importSingleSearchResults || {};
        window._importSingleSearchResults[fileIdx] = data.tracks;

        resultsDiv.innerHTML = data.tracks.map((t, tIdx) => {
            const dur = t.duration_ms ? `${Math.floor(t.duration_ms / 60000)}:${String(Math.floor((t.duration_ms % 60000) / 1000)).padStart(2, '0')}` : '';
            return `
                <div class="import-page-single-result-item" onclick="importPageSelectSingleMatch(${fileIdx}, ${tIdx})">
                    ${t.image_url ? `<img class="import-page-single-result-img" src="${t.image_url}" onerror="this.src='/static/placeholder.png'">` : ''}
                    <div class="import-page-single-result-info">
                        <div class="import-page-single-result-name">${_esc(t.name)} - ${_esc(t.artist)}</div>
                        <div class="import-page-single-result-detail">${_esc(t.album)}${dur ? ' · ' + dur : ''}</div>
                    </div>
                    <button class="import-page-single-result-select">Select</button>
                </div>
            `;
        }).join('');
    } catch (err) {
        resultsDiv.innerHTML = `<div style="color:#ef4444;padding:8px;font-size:12px;">Error: ${err.message}</div>`;
    }
}

function importPageSelectSingleMatch(fileIdx, trackIdx) {
    const trackData = window._importSingleSearchResults?.[fileIdx]?.[trackIdx];
    if (!trackData) return;
    importPageState.singlesManualMatches[fileIdx] = trackData;

    // Auto-select this file
    importPageState.selectedSingles.add(fileIdx);

    // Close search panel and re-render this item
    importPageRenderSinglesList();
}

// --- Singles Tab: Process ---

function importPageProcessSingles() {
    if (importPageState.selectedSingles.size === 0) return;

    const filesToProcess = Array.from(importPageState.selectedSingles).map(i => {
        const f = importPageState.stagingFiles[i];
        const manualMatch = importPageState.singlesManualMatches[i];
        if (manualMatch) {
            return { ...f, manual_match: manualMatch };
        }
        return f;
    });

    // Add to queue and reset immediately
    _importQueueAdd({
        type: 'singles',
        label: `${filesToProcess.length} Single${filesToProcess.length !== 1 ? 's' : ''}`,
        sublabel: filesToProcess.map(f => f.title || f.filename).slice(0, 3).join(', ') + (filesToProcess.length > 3 ? '...' : ''),
        imageUrl: null,
        items: filesToProcess,
    });

    importPageState.selectedSingles.clear();
    importPageState.singlesManualMatches = {};
    importPageUpdateSinglesProcessButton();
    importPageRefreshStaging();
}

// --- Processing Queue ---

const _importQueue = []; // { id, type, label, sublabel, imageUrl, status, processed, total, errors }

function _importQueueAdd(job) {
    const id = ++importJobIdCounter;
    const entry = {
        id,
        type: job.type,
        label: job.label,
        sublabel: job.sublabel,
        imageUrl: job.imageUrl,
        status: 'running',   // running | done | error
        processed: 0,
        total: job.items.length,
        errors: [],
    };
    _importQueue.push(entry);
    _importQueueRender();

    // Fire and forget — runs in background
    _importQueueRunJob(entry, job);
}

async function _importQueueRunJob(entry, job) {
    for (let i = 0; i < job.items.length; i++) {
        const itemName = job.type === 'album'
            ? _importPageGetTrackDisplayInfo(job.items[i], i).name
            : (job.items[i].title || job.items[i].filename || `File ${i + 1}`);

        // Update status with current track info
        entry.sublabel = `Processing ${i + 1}/${job.items.length}: ${itemName}`;
        _importQueueRender();

        try {
            let resp;
            if (job.type === 'album') {
                resp = await fetch('/api/import/album/process', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        album: job.albumData,
                        matches: [job.items[i]]
                    })
                });
            } else {
                resp = await fetch('/api/import/singles/process', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ files: [job.items[i]] })
                });
            }

            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = await resp.json();
            if (data.success) entry.processed += (data.processed || 0);
            if (data.errors && data.errors.length > 0) entry.errors.push(...data.errors);
        } catch (err) {
            entry.errors.push(`${itemName}: ${err.message}`);
        }

        _importQueueRender();
    }

    entry.status = entry.errors.length > 0 && entry.processed === 0 ? 'error' : 'done';
    _importQueueRender();

    // Refresh staging and suggestions since files moved
    importPageRefreshStaging();
    importPageLoadSuggestions();
}

function _importQueueRender() {
    const container = document.getElementById('import-page-queue');
    const list = document.getElementById('import-page-queue-list');
    const clearBtn = document.getElementById('import-page-queue-clear');
    if (!container || !list) return;

    if (_importQueue.length === 0) {
        container.classList.add('hidden');
        return;
    }

    container.classList.remove('hidden');

    // Show clear button only if there are finished jobs
    const hasFinished = _importQueue.some(j => j.status !== 'running');
    clearBtn.style.display = hasFinished ? '' : 'none';

    list.innerHTML = _importQueue.map(j => {
        const pct = j.total > 0 ? Math.round((j.processed / j.total) * 100) : 0;
        const fillClass = j.status === 'error' ? 'error' : '';
        let statusText, statusClass;
        if (j.status === 'running') {
            statusText = `${j.processed}/${j.total}`;
            statusClass = '';
        } else if (j.status === 'done') {
            statusText = j.errors.length > 0 ? `${j.processed}/${j.total} (${j.errors.length} err)` : 'Done';
            statusClass = j.errors.length > 0 ? 'error' : 'done';
        } else {
            statusText = 'Failed';
            statusClass = 'error';
        }

        return `
            <div class="import-page-queue-item">
                ${j.imageUrl
                ? `<img class="import-page-queue-art" src="${j.imageUrl}" onerror="this.src='/static/placeholder.png'">`
                : `<div class="import-page-queue-art" style="background:rgba(255,255,255,0.06);display:flex;align-items:center;justify-content:center;font-size:18px;color:rgba(255,255,255,0.3);">&#9834;</div>`}
                <div class="import-page-queue-info">
                    <div class="import-page-queue-name">${_esc(j.label)}</div>
                    <div class="import-page-queue-detail">${_esc(j.sublabel)}</div>
                </div>
                <div class="import-page-queue-progress">
                    <div class="import-page-queue-bar">
                        <div class="import-page-queue-fill ${fillClass}" style="width:${j.status === 'done' || j.status === 'error' ? 100 : pct}%"></div>
                    </div>
                    <div class="import-page-queue-status ${statusClass}">${statusText}</div>
                </div>
            </div>
        `;
    }).join('');
}

function importPageClearFinishedJobs() {
    for (let i = _importQueue.length - 1; i >= 0; i--) {
        if (_importQueue[i].status !== 'running') {
            _importQueue.splice(i, 1);
        }
    }
    _importQueueRender();
}

// ── Import File Tab ──────────────────────────────────────────────────

let _importFileState = {
    rawText: '',
    fileName: '',
    fileType: '',      // 'csv' or 'text'
    headers: [],       // CSV column headers
    rows: [],          // raw parsed rows (arrays for csv, strings for text)
    columnMap: {},     // { columnIndex: 'track_name' | 'artist_name' | 'album_name' | 'duration' | 'skip' }
    parsedTracks: []   // final [{track_name, artist_name, album_name, duration_ms}]
};

function _initImportFileTab() {
    const dropzone = document.getElementById('import-file-dropzone');
    const fileInput = document.getElementById('import-file-input');
    if (!dropzone || !fileInput) return;

    dropzone.addEventListener('click', () => fileInput.click());
    dropzone.addEventListener('dragover', (e) => { e.preventDefault(); dropzone.classList.add('drag-over'); });
    dropzone.addEventListener('dragleave', () => dropzone.classList.remove('drag-over'));
    dropzone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropzone.classList.remove('drag-over');
        const file = e.dataTransfer.files[0];
        if (file) _importFileRead(file);
    });
    fileInput.addEventListener('change', () => {
        if (fileInput.files[0]) _importFileRead(fileInput.files[0]);
        fileInput.value = '';
    });

    // Enable/disable import button based on playlist name
    const nameInput = document.getElementById('import-file-playlist-name');
    if (nameInput) {
        nameInput.addEventListener('input', () => {
            const btn = document.getElementById('import-file-import-btn');
            if (btn) btn.disabled = !nameInput.value.trim();
        });
    }
}

function _importFileRead(file) {
    const ext = file.name.split('.').pop().toLowerCase();
    if (!['csv', 'tsv', 'txt'].includes(ext)) {
        showToast('Unsupported file type. Use CSV, TSV, or TXT.', 'error');
        return;
    }

    const reader = new FileReader();
    reader.onload = (e) => {
        _importFileState.rawText = e.target.result;
        _importFileState.fileName = file.name;
        _importFileState.fileType = (ext === 'txt') ? 'text' : 'csv';
        _importFileParseAndPreview();
    };
    reader.readAsText(file);
}

function _importFileDetectDelimiter(firstLine) {
    const tab = (firstLine.match(/\t/g) || []).length;
    const semi = (firstLine.match(/;/g) || []).length;
    const comma = (firstLine.match(/,/g) || []).length;
    if (tab >= comma && tab >= semi && tab > 0) return '\t';
    if (semi >= comma && semi > 0) return ';';
    return ',';
}

function _importFileParseCsv(text, delimiter) {
    const lines = text.split(/\r?\n/).filter(l => l.trim());
    if (lines.length < 2) return { headers: [], rows: [] };

    // Parse CSV with basic quote handling
    function parseLine(line) {
        const result = [];
        let current = '';
        let inQuotes = false;
        for (let i = 0; i < line.length; i++) {
            const ch = line[i];
            if (ch === '"') {
                if (inQuotes && i + 1 < line.length && line[i + 1] === '"') {
                    current += '"';
                    i++;
                } else {
                    inQuotes = !inQuotes;
                }
            } else if (ch === delimiter && !inQuotes) {
                result.push(current.trim());
                current = '';
            } else {
                current += ch;
            }
        }
        result.push(current.trim());
        return result;
    }

    const headers = parseLine(lines[0]);
    const rows = [];
    for (let i = 1; i < lines.length; i++) {
        const row = parseLine(lines[i]);
        if (row.some(cell => cell)) rows.push(row);
    }
    return { headers, rows };
}

function _importFileAutoMapColumns(headers) {
    const map = {};
    const lowerHeaders = headers.map(h => h.toLowerCase().trim());

    const trackPatterns = ['track_name', 'track name', 'track', 'title', 'song', 'song_name', 'song name', 'name'];
    const artistPatterns = ['artist_name', 'artist name', 'artist', 'artists', 'performer'];
    const albumPatterns = ['album_name', 'album name', 'album'];
    const durationPatterns = ['duration', 'duration_ms', 'length', 'time'];

    function findMatch(patterns) {
        for (const p of patterns) {
            const idx = lowerHeaders.indexOf(p);
            if (idx !== -1 && !(idx in map)) return idx;
        }
        return -1;
    }

    const trackIdx = findMatch(trackPatterns);
    if (trackIdx !== -1) map[trackIdx] = 'track_name';

    const artistIdx = findMatch(artistPatterns);
    if (artistIdx !== -1) map[artistIdx] = 'artist_name';

    const albumIdx = findMatch(albumPatterns);
    if (albumIdx !== -1) map[albumIdx] = 'album_name';

    const durIdx = findMatch(durationPatterns);
    if (durIdx !== -1) map[durIdx] = 'duration';

    return map;
}

function _importFileParseAndPreview() {
    const state = _importFileState;
    const text = state.rawText;

    if (state.fileType === 'text') {
        // Plain text: one track per line
        const lines = text.split(/\r?\n/).filter(l => l.trim());
        state.rows = lines;
        state.headers = [];
        state.columnMap = {};
    } else {
        // CSV/TSV
        const firstLine = text.split(/\r?\n/)[0] || '';
        const delimiter = _importFileDetectDelimiter(firstLine);
        const { headers, rows } = _importFileParseCsv(text, delimiter);
        state.headers = headers;
        state.rows = rows;
        state.columnMap = _importFileAutoMapColumns(headers);
    }

    _importFileBuildTracks();
    _importFileRenderPreview();
}

function _importFileBuildTracks() {
    const state = _importFileState;
    state.parsedTracks = [];

    if (state.fileType === 'text') {
        const orderEl = document.getElementById('import-file-text-order');
        const sepEl = document.getElementById('import-file-text-separator');
        const order = orderEl ? orderEl.value : 'artist-title';
        const sep = sepEl ? sepEl.value : ' - ';

        for (const line of state.rows) {
            const parts = line.split(sep);
            if (parts.length >= 2) {
                const a = parts[0].trim();
                const b = parts.slice(1).join(sep).trim();
                state.parsedTracks.push({
                    track_name: order === 'artist-title' ? b : a,
                    artist_name: order === 'artist-title' ? a : b,
                    album_name: '',
                    duration_ms: 0
                });
            } else {
                // Can't split — treat whole line as track name
                state.parsedTracks.push({
                    track_name: line.trim(),
                    artist_name: '',
                    album_name: '',
                    duration_ms: 0
                });
            }
        }
    } else {
        // CSV mapped
        const map = state.columnMap;
        const trackCol = Object.keys(map).find(k => map[k] === 'track_name');
        const artistCol = Object.keys(map).find(k => map[k] === 'artist_name');
        const albumCol = Object.keys(map).find(k => map[k] === 'album_name');
        const durCol = Object.keys(map).find(k => map[k] === 'duration');

        for (const row of state.rows) {
            const track = trackCol !== undefined ? (row[trackCol] || '') : '';
            const artist = artistCol !== undefined ? (row[artistCol] || '') : '';
            const album = albumCol !== undefined ? (row[albumCol] || '') : '';
            let dur = durCol !== undefined ? (row[durCol] || '') : '';

            // Parse duration: could be ms, seconds, or mm:ss
            let durationMs = 0;
            if (dur) {
                dur = dur.trim();
                if (dur.includes(':')) {
                    const parts = dur.split(':');
                    durationMs = (parseInt(parts[0]) * 60 + parseInt(parts[1] || 0)) * 1000;
                } else {
                    const num = parseFloat(dur);
                    durationMs = num > 10000 ? num : num * 1000; // assume ms if > 10000, else seconds
                }
                if (isNaN(durationMs)) durationMs = 0;
            }

            state.parsedTracks.push({
                track_name: track,
                artist_name: artist,
                album_name: album,
                duration_ms: durationMs
            });
        }
    }
}

function _importFileRenderPreview() {
    const state = _importFileState;
    const validTracks = state.parsedTracks.filter(t => t.track_name || t.artist_name);

    // Show/hide sections
    document.getElementById('import-file-upload-zone').style.display = 'none';
    document.getElementById('import-file-preview-section').style.display = '';

    // File info
    document.getElementById('import-file-name-label').textContent = state.fileName;
    document.getElementById('import-file-track-count').textContent = `${validTracks.length} track${validTracks.length !== 1 ? 's' : ''} parsed`;

    // Show format controls based on file type
    document.getElementById('import-file-text-format').style.display = state.fileType === 'text' ? '' : 'none';
    document.getElementById('import-file-column-mapping').style.display = state.fileType === 'csv' ? '' : 'none';

    // Render column mapping for CSV
    if (state.fileType === 'csv') {
        _importFileRenderColumnMapping();
    }

    // Pre-fill playlist name from filename (strip extension)
    const nameInput = document.getElementById('import-file-playlist-name');
    if (nameInput && !nameInput.value) {
        nameInput.value = state.fileName.replace(/\.[^.]+$/, '');
    }
    // Update button state
    const btn = document.getElementById('import-file-import-btn');
    if (btn) btn.disabled = !nameInput.value.trim();

    // Render preview table
    const tbody = document.getElementById('import-file-preview-tbody');
    tbody.innerHTML = '';

    state.parsedTracks.forEach((t, i) => {
        const valid = !!(t.track_name || t.artist_name);
        const tr = document.createElement('tr');
        if (!valid) tr.classList.add('invalid-row');
        tr.innerHTML = `
            <td>${i + 1}</td>
            <td>${_esc(t.track_name)}</td>
            <td>${_esc(t.artist_name)}</td>
            <td>${_esc(t.album_name)}</td>
        `;
        tbody.appendChild(tr);
    });
}

function _importFileRenderColumnMapping() {
    const state = _importFileState;
    const container = document.getElementById('import-file-mapping-selects');
    container.innerHTML = '';

    const options = ['skip', 'track_name', 'artist_name', 'album_name', 'duration'];
    const optLabels = { skip: 'Skip', track_name: 'Track', artist_name: 'Artist', album_name: 'Album', duration: 'Duration' };

    state.headers.forEach((header, idx) => {
        const mapped = state.columnMap[idx] || 'skip';
        const wrap = document.createElement('div');
        wrap.className = 'import-file-col-map';
        if (mapped === 'track_name') wrap.classList.add('mapped-track');
        else if (mapped === 'artist_name') wrap.classList.add('mapped-artist');
        else if (mapped === 'album_name') wrap.classList.add('mapped-album');

        const label = document.createElement('span');
        label.className = 'import-file-col-label';
        label.textContent = header;
        label.title = header;

        const sel = document.createElement('select');
        sel.className = 'import-file-select';
        options.forEach(o => {
            const opt = document.createElement('option');
            opt.value = o;
            opt.textContent = optLabels[o];
            if (o === mapped) opt.selected = true;
            sel.appendChild(opt);
        });
        sel.addEventListener('change', () => {
            if (sel.value === 'skip') {
                delete state.columnMap[idx];
            } else {
                // Remove this mapping from any other column
                for (const k of Object.keys(state.columnMap)) {
                    if (state.columnMap[k] === sel.value) delete state.columnMap[k];
                }
                state.columnMap[idx] = sel.value;
            }
            _importFileBuildTracks();
            _importFileRenderPreview();
        });

        wrap.appendChild(label);
        wrap.appendChild(sel);
        container.appendChild(wrap);
    });
}

function importFileReparse() {
    _importFileBuildTracks();
    _importFileRenderPreview();
}

function importFileClear() {
    _importFileState = {
        rawText: '', fileName: '', fileType: '',
        headers: [], rows: [], columnMap: {}, parsedTracks: []
    };
    document.getElementById('import-file-upload-zone').style.display = '';
    document.getElementById('import-file-preview-section').style.display = 'none';
    document.getElementById('import-file-playlist-name').value = '';
    document.getElementById('import-file-preview-tbody').innerHTML = '';
}

function importFileSubmit() {
    const nameInput = document.getElementById('import-file-playlist-name');
    const name = nameInput ? nameInput.value.trim() : '';
    if (!name) {
        showToast('Please enter a playlist name.', 'error');
        nameInput && nameInput.focus();
        return;
    }

    const tracks = _importFileState.parsedTracks.filter(t => t.track_name || t.artist_name);
    if (!tracks.length) {
        showToast('No valid tracks to import.', 'error');
        return;
    }

    // Use a unique ID based on timestamp so multiple imports don't collide
    const sourceId = `file_${Date.now()}`;

    mirrorPlaylist('file', sourceId, name, tracks, {
        description: `Imported from ${_importFileState.fileName}`,
        owner: 'local'
    });

    showToast(`Imported "${name}" with ${tracks.length} tracks`, 'success');
    importFileClear();

    // Switch to mirrored tab so user sees the result
    const mirroredBtn = document.querySelector('.sync-tab-button[data-tab="mirrored"]');
    if (mirroredBtn) {
        mirroredBtn.click();
        // Reload mirrored playlists to show the new one
        setTimeout(() => loadMirroredPlaylists(), 500);
    }
}

// ── Mirrored Playlists ────────────────────────────────────────────────

let mirroredPlaylistsLoaded = false;

/**
 * Fire-and-forget helper: send parsed playlist data to be mirrored on the backend.
 */
function mirrorPlaylist(source, sourceId, name, tracks, metadata = {}) {
    const normalizedTracks = tracks.map(t => ({
        track_name: t.track_name || t.name || '',
        artist_name: t.artist_name || (Array.isArray(t.artists) ? (typeof t.artists[0] === 'object' ? t.artists[0].name : t.artists[0]) : t.artists || ''),
        album_name: t.album_name || (typeof t.album === 'object' ? (t.album && t.album.name) : t.album) || '',
        duration_ms: t.duration_ms || 0,
        image_url: t.image_url || (t.album && typeof t.album === 'object' && t.album.images && t.album.images[0] ? t.album.images[0].url : null),
        source_track_id: t.source_track_id || t.id || t.spotify_track_id || '',
        extra_data: t.extra_data || null
    }));

    fetch('/api/mirror-playlist', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            source,
            source_playlist_id: String(sourceId),
            name,
            tracks: normalizedTracks,
            description: metadata.description || '',
            owner: metadata.owner || '',
            image_url: metadata.image_url || ''
        })
    }).then(r => r.json()).then(data => {
        if (data.success) console.log(`Mirrored ${source} playlist: ${name} (${normalizedTracks.length} tracks)`);
    }).catch(err => console.warn('Mirror save failed:', err));
}

/**
 * Load and render all mirrored playlists into the Mirrored tab.
 */
async function loadMirroredPlaylists() {
    const container = document.getElementById('mirrored-playlist-container');
    if (!container) return;
    container.innerHTML = `<div class="playlist-placeholder">Loading mirrored playlists...</div>`;

    try {
        const res = await fetch('/api/mirrored-playlists');
        const playlists = await res.json();
        if (playlists.error) throw new Error(playlists.error);

        if (!playlists.length) {
            container.innerHTML = `<div class="playlist-placeholder">Playlists you parse from any service will appear here as persistent backups.</div>`;
            return;
        }

        container.innerHTML = '';
        playlists.forEach(p => renderMirroredCard(p, container));
        mirroredPlaylistsLoaded = true;

        // Hydrate discovery states from backend (survives page refresh)
        await hydrateMirroredDiscoveryStates();
    } catch (err) {
        container.innerHTML = `<div class="playlist-placeholder">Error loading mirrored playlists: ${err.message}</div>`;
    }
}

function renderMirroredCard(p, container) {
    const ago = timeAgo(p.updated_at || p.mirrored_at);
    const hash = `mirrored_${p.id}`;
    const state = youtubePlaylistStates[hash];
    const phase = state ? state.phase : null;

    // Build phase indicator
    let phaseHtml = '';
    if (phase === 'discovering') {
        const pct = state.discoveryProgress || state.discovery_progress || 0;
        phaseHtml = `<span style="color:#a78bfa;">Discovering ${pct}%</span>`;
    } else if (phase === 'discovered') {
        const matches = state.spotifyMatches || state.spotify_matches || 0;
        const total = state.spotify_total || p.track_count;
        phaseHtml = `<span style="color:#22c55e;">Discovered ${matches}/${total}</span>`;
    } else if (phase === 'syncing' || phase === 'sync_complete') {
        phaseHtml = `<span style="color:#3b82f6;">${phase === 'syncing' ? 'Syncing...' : 'Synced'}</span>`;
    } else if (phase === 'downloading') {
        phaseHtml = `<span style="color:#f59e0b;">Downloading...</span>`;
    } else if (phase === 'download_complete') {
        phaseHtml = `<span style="color:#22c55e;">Downloaded</span>`;
    }

    const sourceIcons = { spotify: '🎵', tidal: '🌊', youtube: '▶', beatport: '🎛', file: '📄' };
    const srcIcon = sourceIcons[p.source] || '📋';

    // Discovery ratio
    const disc = p.discovered_count || 0;
    const tot = p.total_count || p.track_count || 0;
    let ratioHtml = '';
    if (disc > 0) {
        const complete = disc >= tot;
        const srcName = typeof currentMusicSourceName !== 'undefined' ? currentMusicSourceName : 'metadata';
        ratioHtml = `<span class="discovery-ratio${complete ? ' complete' : ''}">${disc}/${tot} discovered on ${srcName}</span>`;
    }

    const card = document.createElement('div');
    card.className = 'mirrored-playlist-card';
    card.id = `mirrored-card-${p.id}`;
    card.innerHTML = `
        <div class="source-icon ${_escAttr(p.source)}">${srcIcon}</div>
        <div class="mirrored-card-info">
            <div class="card-name">${_esc(p.name)}</div>
            <div class="card-meta">
                <span class="source-badge ${_escAttr(p.source)}">${_esc(p.source)}</span>
                <span>${p.track_count} tracks</span>
                <span>Mirrored ${ago}</span>
                ${ratioHtml}
                ${phaseHtml}
            </div>
        </div>
        ${disc > 0 ? `<button class="mirrored-card-clear" onclick="event.stopPropagation(); clearMirroredDiscovery(${p.id}, '${_escAttr(p.name)}')" title="Clear discovery data">↺</button>` : ''}
        <button class="mirrored-card-delete" onclick="event.stopPropagation(); deleteMirroredPlaylist(${p.id}, '${_escAttr(p.name)}')" title="Delete mirror">✕</button>
    `;
    card.addEventListener('click', () => {
        const st = youtubePlaylistStates[hash];
        // Treat as non-fresh if phase is set, or if a poller/discovery modal exists
        const hasActiveDiscovery = activeYouTubePollers[hash] || document.getElementById(`youtube-discovery-modal-${hash}`);
        if (st && ((st.phase && st.phase !== 'fresh') || hasActiveDiscovery)) {
            if (st.phase === 'downloading' || st.phase === 'download_complete') {
                // Open download modal directly (follows Tidal/YouTube card click pattern)
                const spotifyPlaylistId = st.convertedSpotifyPlaylistId;
                if (spotifyPlaylistId && activeDownloadProcesses[spotifyPlaylistId]) {
                    // Modal already exists — just show it
                    const process = activeDownloadProcesses[spotifyPlaylistId];
                    if (process.modalElement) {
                        if (process.status === 'complete') {
                            showToast('Showing previous results. Close this modal to start a new analysis.', 'info');
                        }
                        process.modalElement.style.display = 'flex';
                    }
                } else if (spotifyPlaylistId) {
                    // Need to rehydrate the download modal
                    rehydrateMirroredDownloadModal(hash, st);
                } else {
                    // No converted playlist ID yet, fall back to discovery modal
                    openYouTubeDiscoveryModal(hash);
                }
            } else {
                openYouTubeDiscoveryModal(hash);
                if (st.phase === 'discovering' && !activeYouTubePollers[hash]) {
                    startYouTubeDiscoveryPolling(hash);
                }
            }
        } else {
            openMirroredPlaylistModal(p.id);
        }
    });
    container.appendChild(card);
}

function updateMirroredCardPhase(urlHash, phase) {
    // Update the state phase (updateYouTubeCardPhase skips this for mirrored playlists due to no cardElement)
    const state = youtubePlaylistStates[urlHash];
    if (state) state.phase = phase;

    // Extract the numeric ID from urlHash (e.g., 'mirrored_3' → '3')
    const mirroredId = urlHash.replace('mirrored_', '');
    const card = document.getElementById(`mirrored-card-${mirroredId}`);
    if (!card) return;

    const metaEl = card.querySelector('.card-meta');
    if (!metaEl) return;

    // Remove old phase indicator
    const oldPhase = metaEl.querySelector('span[style]');
    if (oldPhase) oldPhase.remove();

    // Add new phase indicator
    let phaseHtml = '';
    switch (phase) {
        case 'discovering':
            phaseHtml = `<span style="color:#a78bfa;">Discovering...</span>`;
            break;
        case 'discovered':
            const matches = state?.spotifyMatches || state?.spotify_matches || 0;
            const total = state?.spotify_total || 0;
            phaseHtml = `<span style="color:#22c55e;">Discovered ${matches}/${total}</span>`;
            break;
        case 'syncing':
            phaseHtml = `<span style="color:#3b82f6;">Syncing...</span>`;
            break;
        case 'sync_complete':
            phaseHtml = `<span style="color:#3b82f6;">Synced</span>`;
            break;
        case 'downloading':
            phaseHtml = `<span style="color:#f59e0b;">Downloading...</span>`;
            break;
        case 'download_complete':
            phaseHtml = `<span style="color:#22c55e;">Downloaded</span>`;
            break;
    }
    if (phaseHtml) {
        metaEl.insertAdjacentHTML('beforeend', phaseHtml);
    }
}

async function rehydrateMirroredDownloadModal(urlHash, state) {
    try {
        if (!state || !state.playlist) {
            showToast('Cannot open download modal - invalid playlist data', 'error');
            return;
        }

        console.log(`💧 [Rehydration] Rehydrating mirrored download modal for: ${state.playlist.name}`);

        // Get discovery results from backend if not already loaded
        let discoveryRes = state.discoveryResults || state.discovery_results;
        if (!discoveryRes || discoveryRes.length === 0) {
            console.log(`🔍 Fetching discovery results from backend for mirrored playlist: ${urlHash}`);
            const stateResponse = await fetch(`/api/youtube/state/${urlHash}`);
            if (stateResponse.ok) {
                const fullState = await stateResponse.json();
                state.discovery_results = fullState.discovery_results;
                state.discoveryResults = fullState.discovery_results;
                state.convertedSpotifyPlaylistId = fullState.converted_spotify_playlist_id;
                state.download_process_id = fullState.download_process_id;
                discoveryRes = fullState.discovery_results;
                console.log(`✅ Loaded ${discoveryRes?.length || 0} discovery results from backend`);
            } else {
                showToast('Error loading playlist data', 'error');
                return;
            }
        }

        // Extract Spotify tracks from discovery results
        const spotifyTracks = (discoveryRes || [])
            .filter(r => r.spotify_data || (r.spotify_track && r.status_class === 'found'))
            .map(r => {
                if (r.spotify_data) return r.spotify_data;
                const albumData = r.spotify_album || 'Unknown Album';
                return {
                    id: r.spotify_id || 'unknown',
                    name: r.spotify_track || 'Unknown Track',
                    artists: r.spotify_artist ? [r.spotify_artist] : ['Unknown Artist'],
                    album: typeof albumData === 'object' ? albumData : { name: albumData, album_type: 'album', images: [] },
                    duration_ms: 0
                };
            });

        if (spotifyTracks.length === 0) {
            showToast('No Spotify matches found for download', 'error');
            return;
        }

        const virtualPlaylistId = state.convertedSpotifyPlaylistId;
        const playlistName = state.playlist.name;

        // Create the download modal
        await openDownloadMissingModalForYouTube(virtualPlaylistId, playlistName, spotifyTracks);

        // If we have a download process ID, set up the modal for the running/complete state
        if (state.download_process_id) {
            const process = activeDownloadProcesses[virtualPlaylistId];
            if (process) {
                process.status = state.phase === 'download_complete' ? 'complete' : 'running';
                process.batchId = state.download_process_id;

                const beginBtn = document.getElementById(`begin-analysis-btn-${virtualPlaylistId}`);
                const cancelBtn = document.getElementById(`cancel-all-btn-${virtualPlaylistId}`);

                if (state.phase === 'downloading') {
                    if (beginBtn) beginBtn.style.display = 'none';
                    if (cancelBtn) cancelBtn.style.display = 'inline-block';

                    // Start polling for live updates
                    startModalDownloadPolling(virtualPlaylistId);
                    console.log(`🔄 Started polling for active mirrored download: ${state.download_process_id}`);
                } else if (state.phase === 'download_complete') {
                    if (beginBtn) beginBtn.style.display = 'none';
                    if (cancelBtn) cancelBtn.style.display = 'none';
                    console.log(`✅ Showing completed mirrored download results: ${state.download_process_id}`);

                    // Fetch final results to populate the modal
                    try {
                        const response = await fetch(`/api/playlists/${state.download_process_id}/download_status`);
                        if (response.ok) {
                            const data = await response.json();
                            if (data.phase === 'complete' && data.tasks) {
                                updateCompletedModalResults(virtualPlaylistId, data);
                            }
                        }
                    } catch (err) {
                        console.warn('Could not load completed download results:', err);
                    }
                }
            }
        }

        console.log(`✅ Successfully rehydrated mirrored download modal for: ${state.playlist.name}`);
    } catch (error) {
        console.error('❌ Error rehydrating mirrored download modal:', error);
        showToast('Error opening download modal', 'error');
    }
}

async function hydrateMirroredDiscoveryStates() {
    try {
        const res = await fetch('/api/mirrored-playlists/discovery-states');
        const data = await res.json();
        if (data.error || !data.states || data.states.length === 0) return;

        console.log(`Hydrating ${data.states.length} mirrored discovery states`);

        for (const s of data.states) {
            const hash = s.url_hash;

            youtubePlaylistStates[hash] = {
                playlist: s.playlist,
                phase: s.phase,
                discovery_results: s.discovery_results || [],
                discoveryResults: s.discovery_results || [],
                discovery_progress: s.discovery_progress || 0,
                discoveryProgress: s.discovery_progress || 0,
                spotify_matches: s.spotify_matches || 0,
                spotifyMatches: s.spotify_matches || 0,
                spotify_total: s.spotify_total || 0,
                status: s.status || '',
                url: s.playlist?.url || '',
                sync_playlist_id: null,
                converted_spotify_playlist_id: s.converted_spotify_playlist_id,
                convertedSpotifyPlaylistId: s.converted_spotify_playlist_id,
                download_process_id: s.download_process_id,
                created_at: Date.now() / 1000,
                last_accessed: Date.now() / 1000,
                discovery_future: null,
                sync_progress: {},
                is_mirrored_playlist: true,
                mirrored_source: s.playlist?.source || ''
            };

            // Update the card to reflect the current phase
            const card = document.getElementById(`mirrored-card-${s.playlist_id}`);
            if (card) {
                const metaEl = card.querySelector('.card-meta');
                if (metaEl) {
                    // Remove old phase span and add new one
                    const oldPhase = metaEl.querySelector('span[style]');
                    if (oldPhase) oldPhase.remove();

                    if (s.phase === 'discovering') {
                        metaEl.insertAdjacentHTML('beforeend', `<span style="color:#a78bfa;">Discovering ${s.discovery_progress || 0}%</span>`);
                    } else if (s.phase === 'discovered') {
                        metaEl.insertAdjacentHTML('beforeend', `<span style="color:#22c55e;">Discovered ${s.spotify_matches || 0}/${s.spotify_total || 0}</span>`);
                    } else if (s.phase === 'syncing' || s.phase === 'sync_complete') {
                        metaEl.insertAdjacentHTML('beforeend', `<span style="color:#3b82f6;">${s.phase === 'syncing' ? 'Syncing...' : 'Synced'}</span>`);
                    } else if (s.phase === 'downloading') {
                        metaEl.insertAdjacentHTML('beforeend', `<span style="color:#f59e0b;">Downloading...</span>`);
                    } else if (s.phase === 'download_complete') {
                        metaEl.insertAdjacentHTML('beforeend', `<span style="color:#22c55e;">Downloaded</span>`);
                    }
                }
            }

            // Resume polling if discovery is in progress
            if (s.phase === 'discovering' && !activeYouTubePollers[hash]) {
                startYouTubeDiscoveryPolling(hash);
            }
        }
    } catch (err) {
        console.warn('Failed to hydrate mirrored discovery states:', err);
    }
}

function timeAgo(dateStr) {
    if (!dateStr) return '';
    // Handle ISO formats: "Z" suffix, "+00:00" offset, or bare (assume UTC)
    let ts = dateStr;
    if (!ts.includes('Z') && !ts.includes('+') && !ts.includes('-', 10)) ts += 'Z';
    const diff = Date.now() - new Date(ts).getTime();
    const secs = Math.floor(diff / 1000);
    if (secs < 5) return 'just now';
    if (secs < 60) return `${secs}s ago`;
    const mins = Math.floor(secs / 60);
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    const days = Math.floor(hrs / 24);
    if (days < 30) return `${days}d ago`;
    return `${Math.floor(days / 30)}mo ago`;
}

/**
 * Open modal showing all tracks in a mirrored playlist.
 */
async function openMirroredPlaylistModal(playlistId) {
    showLoadingOverlay('Loading mirrored playlist...');
    try {
        const res = await fetch(`/api/mirrored-playlists/${playlistId}`);
        const data = await res.json();
        if (data.error) throw new Error(data.error);

        hideLoadingOverlay();

        // Remove any existing modal
        const old = document.getElementById('mirrored-track-modal');
        if (old) old.remove();

        const overlay = document.createElement('div');
        overlay.id = 'mirrored-track-modal';
        overlay.className = 'mirrored-modal-overlay';

        const tracks = data.tracks || [];
        const source = data.source || 'unknown';
        const sourceIcons = { spotify: '🎵', tidal: '🌊', youtube: '▶', beatport: '🎛' };
        const sourceIcon = sourceIcons[source] || '📋';

        const trackRows = tracks.map(t => {
            const dur = t.duration_ms ? `${Math.floor(t.duration_ms / 60000)}:${String(Math.floor((t.duration_ms % 60000) / 1000)).padStart(2, '0')}` : '';
            return `<div class="mirrored-track-row">
                <span class="track-pos">${t.position}</span>
                <span class="track-title">${_esc(t.track_name)}</span>
                <span class="track-artist">${_esc(t.artist_name)}</span>
                <span class="track-album">${_esc(t.album_name)}</span>
                <span class="track-duration">${dur}</span>
            </div>`;
        }).join('');

        overlay.innerHTML = `
            <div class="mirrored-modal">
                <div class="mirrored-modal-header">
                    <div class="mirrored-modal-hero">
                        <div class="mirrored-modal-hero-icon ${_escAttr(source)}">${sourceIcon}</div>
                        <div class="mirrored-modal-hero-info">
                            <h2 class="mirrored-modal-hero-title">${_esc(data.name)}</h2>
                            <div class="mirrored-modal-hero-subtitle">
                                <span class="mirrored-modal-hero-badge">${_esc(source)}</span>
                                <span>${tracks.length} tracks</span>
                                <span>&middot;</span>
                                <span>Mirrored ${timeAgo(data.updated_at || data.mirrored_at)}</span>
                            </div>
                        </div>
                    </div>
                    <span class="mirrored-modal-close" onclick="closeMirroredModal()">&times;</span>
                </div>
                <div class="mirrored-modal-tracks">
                    <div class="mirrored-track-header">
                        <span>#</span><span>Track</span><span>Artist</span><span>Album</span><span style="text-align:right">Time</span>
                    </div>
                    ${trackRows}
                </div>
                <div class="mirrored-modal-footer">
                    <div class="mirrored-modal-footer-left">
                        <button class="mirrored-btn-delete" onclick="closeMirroredModal(); deleteMirroredPlaylist(${playlistId}, '${_escAttr(data.name)}')">Delete Mirror</button>
                    </div>
                    <div class="mirrored-modal-footer-right" style="display:flex;gap:10px;">
                        <button class="mirrored-btn-close" onclick="closeMirroredModal()">Close</button>
                        <button class="mirrored-btn-discover" onclick="discoverMirroredPlaylist(${playlistId})">Discover</button>
                    </div>
                </div>
            </div>
        `;

        overlay.addEventListener('click', e => { if (e.target === overlay) closeMirroredModal(); });
        document.body.appendChild(overlay);
    } catch (err) {
        hideLoadingOverlay();
        showToast(`Error: ${err.message}`, 'error');
    }
}

function closeMirroredModal() {
    const m = document.getElementById('mirrored-track-modal');
    if (m) m.remove();
}

/**
 * Delete a mirrored playlist after confirmation.
 */
async function clearMirroredDiscovery(playlistId, name) {
    if (!await showConfirmDialog({ title: 'Clear Discovery Data', message: `Clear discovery data for "${name}"? You can re-discover afterwards to get updated cover art.` })) return;
    try {
        const res = await fetch(`/api/mirrored-playlists/${playlistId}/clear-discovery`, { method: 'POST' });
        const data = await res.json();
        if (data.success) {
            showToast(`Cleared discovery for ${name} (${data.cleared} tracks)`, 'success');
            // Signal cancellation to any running worker, then clear state
            const hash = `mirrored_${playlistId}`;
            if (youtubePlaylistStates[hash]) {
                youtubePlaylistStates[hash].phase = 'cancelled';
            }
            delete youtubePlaylistStates[hash];
            const staleModal = document.getElementById(`youtube-discovery-modal-${hash}`);
            if (staleModal) staleModal.remove();
            loadMirroredPlaylists();
        } else {
            showToast(data.error || 'Failed to clear discovery', 'error');
        }
    } catch (err) {
        showToast(`Error: ${err.message}`, 'error');
    }
}

// ==================== Discovery Pool Modal ====================

let _discoveryPoolOverlay = null;
let _discoveryPoolData = null;
let _discoveryPoolView = 'categories'; // 'categories' | 'failed' | 'matched'
let _discoveryPoolPlaylistFilter = null;

async function loadDiscoveryPoolStats() {
    try {
        const res = await fetch('/api/discovery-pool');
        const data = await res.json();
        const matchedEl = document.getElementById('discovery-pool-matched-count');
        const failedEl = document.getElementById('discovery-pool-failed-count');
        if (matchedEl) matchedEl.textContent = data.stats.matched || 0;
        if (failedEl) failedEl.textContent = data.stats.failed || 0;
    } catch (e) { }
}

async function openDiscoveryPoolModal(playlistId = null) {
    _discoveryPoolPlaylistFilter = playlistId;
    _discoveryPoolView = 'categories';

    // Fetch pool data
    let url = '/api/discovery-pool';
    if (playlistId) url += `?playlist_id=${playlistId}`;
    try {
        const res = await fetch(url);
        _discoveryPoolData = await res.json();
    } catch (err) {
        showToast('Failed to load discovery pool', 'error');
        return;
    }

    // Remove existing overlay if present
    if (_discoveryPoolOverlay) _discoveryPoolOverlay.remove();

    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay';
    overlay.id = 'discovery-pool-overlay';
    overlay.onclick = (e) => { if (e.target === overlay) closeDiscoveryPoolModal(); };

    const playlistOptions = (_discoveryPoolData.playlists || [])
        .map(p => `<option value="${p.id}" ${playlistId == p.id ? 'selected' : ''}>${_esc(p.name)}</option>`)
        .join('');

    const failedCount = _discoveryPoolData.stats.failed || 0;
    const matchedCount = _discoveryPoolData.stats.matched || 0;

    overlay.innerHTML = `
        <div class="modal-container playlist-modal">
            <div class="playlist-modal-header">
                <div class="playlist-header-content">
                    <h2>Discovery Pool</h2>
                    <div class="playlist-quick-info">
                        <span class="playlist-track-count" id="pool-header-matched">${matchedCount} Matched</span>
                        <span class="playlist-owner ${failedCount > 0 ? 'pool-header-failed-highlight' : ''}" id="pool-header-failed">${failedCount} Failed</span>
                        <select class="pool-playlist-filter" onchange="filterDiscoveryPool(this.value)">
                            <option value="">All Playlists</option>
                            ${playlistOptions}
                        </select>
                    </div>
                </div>
                <span class="playlist-modal-close" onclick="closeDiscoveryPoolModal()">&times;</span>
            </div>

            <div class="playlist-modal-body">
                <div class="pool-category-grid" id="pool-category-grid">
                    <div class="pool-category-card failed" onclick="showPoolList('failed')">
                        <div class="pool-category-fallback failed"></div>
                        <div class="pool-category-overlay"></div>
                        <div class="pool-category-content">
                            <div class="pool-category-icon">&#9888;</div>
                            <div class="pool-category-count failed" id="pool-cat-failed-count">${failedCount}</div>
                            <div class="pool-category-label">tracks need attention</div>
                        </div>
                        <div class="pool-category-top-bar failed"></div>
                    </div>
                    <div class="pool-category-card matched" onclick="showPoolList('matched')">
                        <div class="pool-category-fallback matched" id="pool-matched-bg"></div>
                        <div class="pool-category-overlay"></div>
                        <div class="pool-category-content">
                            <div class="pool-category-icon">&#10003;</div>
                            <div class="pool-category-count matched" id="pool-cat-matched-count">${matchedCount}</div>
                            <div class="pool-category-label">cached matches</div>
                        </div>
                        <div class="pool-category-top-bar matched"></div>
                    </div>
                </div>

                <div class="pool-list-view" id="pool-list-view" style="display: none;">
                    <div class="pool-list-header">
                        <button class="pool-back-btn" onclick="showPoolCategories()">&larr; Back</button>
                        <span class="pool-list-title" id="pool-list-title"></span>
                        <input type="text" class="pool-list-search" id="pool-list-search" placeholder="Filter tracks..." oninput="renderPoolList()">
                    </div>
                    <div class="pool-list-content" id="pool-list-content"></div>
                </div>
            </div>

            <div class="playlist-modal-footer">
                <div class="playlist-modal-footer-left"></div>
                <div class="playlist-modal-footer-right">
                    <button class="playlist-modal-btn playlist-modal-btn-secondary" onclick="closeDiscoveryPoolModal()">Close</button>
                </div>
            </div>
        </div>
    `;

    document.body.appendChild(overlay);
    overlay.style.display = 'flex';
    _discoveryPoolOverlay = overlay;

    // Build matched mosaic if images available
    _buildPoolMatchedMosaic();
}

function _buildPoolMatchedMosaic() {
    const entries = _discoveryPoolData.matched || [];
    const images = [];
    for (const e of entries) {
        const md = e.matched_data || {};
        if (md.image_url && images.indexOf(md.image_url) === -1) {
            images.push(md.image_url);
            if (images.length >= 20) break;
        }
    }
    const bgEl = document.getElementById('pool-matched-bg');
    if (!bgEl || images.length < 4) return; // keep fallback gradient

    // Build mosaic rows similar to wishlist
    bgEl.innerHTML = '';
    bgEl.className = 'wishlist-mosaic-background';
    const rows = 4;
    const imgPerRow = Math.ceil(images.length / rows) * 2; // duplicate for seamless loop
    for (let r = 0; r < rows; r++) {
        const wrapper = document.createElement('div');
        wrapper.className = 'wishlist-mosaic-row-wrapper';
        const row = document.createElement('div');
        row.className = 'wishlist-mosaic-row' + (r % 2 === 1 ? ' scroll-right' : '');
        row.style.setProperty('--speed', (25 + r * 5) + 's');
        row.style.animationDelay = (r * 0.15) + 's';
        for (let i = 0; i < imgPerRow; i++) {
            const img = images[(i + r * 3) % images.length];
            const tile = document.createElement('div');
            tile.className = 'wishlist-mosaic-tile';
            tile.innerHTML = `<div class="wishlist-mosaic-image" style="background-image: url('${img}')"></div>`;
            row.appendChild(tile);
        }
        wrapper.appendChild(row);
        bgEl.appendChild(wrapper);
    }
}

function closeDiscoveryPoolModal() {
    if (_discoveryPoolOverlay) {
        _discoveryPoolOverlay.remove();
        _discoveryPoolOverlay = null;
    }
    _discoveryPoolData = null;
    // Refresh dashboard stats
    loadDiscoveryPoolStats();
}

function showPoolCategories() {
    _discoveryPoolView = 'categories';
    const grid = document.getElementById('pool-category-grid');
    const list = document.getElementById('pool-list-view');
    if (grid) grid.style.display = '';
    if (list) list.style.display = 'none';
}

function showPoolList(category) {
    _discoveryPoolView = category;
    const grid = document.getElementById('pool-category-grid');
    const list = document.getElementById('pool-list-view');
    if (grid) grid.style.display = 'none';
    if (list) list.style.display = '';

    const titleEl = document.getElementById('pool-list-title');
    if (titleEl) titleEl.textContent = category === 'failed' ? 'Failed Tracks' : 'Matched Tracks';

    // Clear search filter when switching views
    const searchEl = document.getElementById('pool-list-search');
    if (searchEl) searchEl.value = '';

    renderPoolList();
}

async function filterDiscoveryPool(playlistId) {
    _discoveryPoolPlaylistFilter = playlistId || null;
    let url = '/api/discovery-pool';
    if (playlistId) url += `?playlist_id=${playlistId}`;
    try {
        const res = await fetch(url);
        _discoveryPoolData = await res.json();
        // Update header counts
        _updatePoolHeaderCounts();
        // Update category card counts
        const failedCountEl = document.getElementById('pool-cat-failed-count');
        const matchedCountEl = document.getElementById('pool-cat-matched-count');
        if (failedCountEl) failedCountEl.textContent = _discoveryPoolData.stats.failed || 0;
        if (matchedCountEl) matchedCountEl.textContent = _discoveryPoolData.stats.matched || 0;
        // If viewing a list, refresh it
        if (_discoveryPoolView === 'failed' || _discoveryPoolView === 'matched') {
            renderPoolList();
        }
    } catch (err) {
        showToast('Failed to filter discovery pool', 'error');
    }
}

function _updatePoolHeaderCounts() {
    if (!_discoveryPoolData) return;
    const failedCount = _discoveryPoolData.stats.failed || 0;
    const matchedCount = _discoveryPoolData.stats.matched || 0;
    const matchedEl = document.getElementById('pool-header-matched');
    const failedEl = document.getElementById('pool-header-failed');
    if (matchedEl) matchedEl.textContent = `${matchedCount} Matched`;
    if (failedEl) {
        failedEl.textContent = `${failedCount} Failed`;
        failedEl.classList.toggle('pool-header-failed-highlight', failedCount > 0);
    }
}

function renderPoolList() {
    const container = document.getElementById('pool-list-content');
    if (!container || !_discoveryPoolData) return;

    // Client-side search filter
    const searchEl = document.getElementById('pool-list-search');
    const query = (searchEl ? searchEl.value : '').toLowerCase().trim();

    if (_discoveryPoolView === 'failed') {
        let tracks = _discoveryPoolData.failed || [];
        if (query) {
            tracks = tracks.filter(t =>
                (t.track_name || '').toLowerCase().includes(query) ||
                (t.artist_name || '').toLowerCase().includes(query) ||
                (t.playlist_name || '').toLowerCase().includes(query)
            );
        }
        if (tracks.length === 0) {
            container.innerHTML = query
                ? '<div class="pool-empty">No failed tracks match your filter.</div>'
                : '<div class="pool-empty">No failed discoveries. All tracks matched successfully.</div>';
            return;
        }
        container.innerHTML = tracks.map(t => `
            <div class="pool-track-row pool-failed">
                <div class="pool-track-info">
                    <div class="pool-track-name">${_esc(t.track_name)}</div>
                    <div class="pool-track-meta">
                        <span class="pool-track-artist">${_esc(t.artist_name)}</span>
                        <span class="pool-track-playlist-badge">${_esc(t.playlist_name)}</span>
                    </div>
                </div>
                <button class="playlist-modal-btn playlist-modal-btn-primary pool-fix-btn" onclick="openPoolFixModal(${t.id}, '${_escAttr(t.track_name)}', '${_escAttr(t.artist_name)}')">Fix Match</button>
            </div>
        `).join('');
    } else {
        let entries = _discoveryPoolData.matched || [];
        if (query) {
            entries = entries.filter(e => {
                const md = e.matched_data || {};
                const matchedName = md.name || '';
                return (e.original_title || '').toLowerCase().includes(query) ||
                    (e.original_artist || '').toLowerCase().includes(query) ||
                    matchedName.toLowerCase().includes(query);
            });
        }
        if (entries.length === 0) {
            container.innerHTML = query
                ? '<div class="pool-empty">No matched tracks match your filter.</div>'
                : '<div class="pool-empty">No cached discovery matches yet.</div>';
            return;
        }
        container.innerHTML = entries.map(e => {
            const md = e.matched_data || {};
            const matchedArtists = (md.artists || []).map(a => typeof a === 'string' ? a : (a.name || '')).join(', ');
            const conf = Math.round((e.confidence || 0) * 100);
            const confClass = conf >= 80 ? 'high' : (conf >= 70 ? 'mid' : 'low');
            const album = md.album || {};
            const albumImages = (typeof album === 'object' && album.images) ? album.images : [];
            const imgUrl = md.image_url || (albumImages.length > 0 ? albumImages[0].url || '' : '');
            return `
                <div class="pool-track-row pool-matched">
                    ${imgUrl ? `<img class="pool-match-image" src="${_esc(imgUrl)}" alt="" onerror="this.style.display='none'" />` : '<div class="pool-match-image-placeholder"></div>'}
                    <div class="pool-track-info">
                        <div class="pool-track-name">${_esc(e.original_title)}</div>
                        <div class="pool-track-meta">
                            <span class="pool-track-artist">${_esc(e.original_artist)}</span>
                            <span class="pool-track-arrow">&rarr;</span>
                            <span class="pool-match-name">${_esc(md.name || '?')}</span>
                            <span class="pool-match-provider">${_esc(e.provider)}</span>
                        </div>
                    </div>
                    <span class="pool-confidence-badge ${confClass}">${conf}%</span>
                    <span class="pool-use-count">${e.use_count}&times;</span>
                    <button class="pool-rematch-btn" onclick="rematchPoolCacheEntry(${e.id}, '${_escAttr(e.original_title)}', '${_escAttr(e.original_artist)}')" title="Rematch this track">Rematch</button>
                    <button class="pool-remove-btn" onclick="removePoolCacheEntry(${e.id})" title="Remove cached match">&times;</button>
                </div>
            `;
        }).join('');
    }
}

function rematchPoolCacheEntry(cacheId, originalTitle, originalArtist) {
    // Open the fix modal in "rematch" mode — saves to cache instead of mirrored tracks
    openPoolRematchModal(cacheId, originalTitle, originalArtist);
}

function openPoolRematchModal(cacheId, trackName, artistName) {
    // Reuses the fix modal UI but saves via the rematch endpoint
    let fixOverlay = document.getElementById('pool-fix-overlay');
    if (fixOverlay) fixOverlay.remove();

    fixOverlay = document.createElement('div');
    fixOverlay.className = 'pool-fix-overlay';
    fixOverlay.id = 'pool-fix-overlay';
    fixOverlay.addEventListener('mousedown', (e) => {
        if (e.target === fixOverlay) {
            e.preventDefault();
            closePoolFixModal();
        }
    });

    fixOverlay.innerHTML = `
        <div class="pool-fix-modal" onmousedown="event.stopPropagation()">
            <div class="pool-fix-header">
                <h2>Rematch Track</h2>
                <button class="pool-fix-close" onclick="closePoolFixModal()" title="Close">✕</button>
            </div>
            <div class="pool-fix-body">
                <div class="pool-fix-source">
                    <div class="pool-fix-source-label">Current Match</div>
                    <div class="pool-fix-source-row">
                        <span class="pool-fix-source-title">${_esc(trackName)}</span>
                        <span class="pool-fix-source-sep">—</span>
                        <span class="pool-fix-source-artist">${_esc(artistName)}</span>
                    </div>
                </div>
                <div class="pool-fix-search">
                    <div class="pool-fix-input-row">
                        <div class="pool-fix-input-wrap">
                            <label for="pool-fix-track-input">Track</label>
                            <input type="text" id="pool-fix-track-input" placeholder="Track name" value="${_escAttr(trackName)}">
                        </div>
                        <div class="pool-fix-input-wrap">
                            <label for="pool-fix-artist-input">Artist</label>
                            <input type="text" id="pool-fix-artist-input" placeholder="Artist name" value="${_escAttr(artistName)}">
                        </div>
                        <button class="pool-fix-search-btn" onclick="searchPoolFix()">Search</button>
                    </div>
                </div>
                <div class="pool-fix-results-area">
                    <div id="pool-fix-results" class="pool-fix-results-list">
                        <div class="pool-fix-empty">Searching...</div>
                    </div>
                </div>
            </div>
            <div class="pool-fix-footer">
                <button class="pool-fix-cancel" onclick="closePoolFixModal()">Cancel</button>
            </div>
        </div>
    `;

    // Store rematch context
    fixOverlay.dataset.mode = 'rematch';
    fixOverlay.dataset.cacheId = cacheId;
    fixOverlay.dataset.originalTitle = trackName;
    fixOverlay.dataset.originalArtist = artistName;
    document.body.appendChild(fixOverlay);

    const trackInput = fixOverlay.querySelector('#pool-fix-track-input');
    const artistInput = fixOverlay.querySelector('#pool-fix-artist-input');
    const enterHandler = (e) => { if (e.key === 'Enter') searchPoolFix(); };
    trackInput.addEventListener('keypress', enterHandler);
    artistInput.addEventListener('keypress', enterHandler);
    trackInput.focus();
    trackInput.select();

    setTimeout(() => searchPoolFix(), 500);
}

async function removePoolCacheEntry(entryId) {
    if (!await showConfirmDialog({ title: 'Remove Cache Entry', message: 'Remove this cached match? The track will be re-discovered fresh next time.' })) return;
    try {
        const res = await fetch(`/api/discovery-pool/cache/${entryId}`, { method: 'DELETE' });
        const data = await res.json();
        if (data.success) {
            showToast('Cache entry removed', 'success');
            filterDiscoveryPool(_discoveryPoolPlaylistFilter || '');
        } else {
            showToast(data.error || 'Failed to remove', 'error');
        }
    } catch (err) {
        showToast(`Error: ${err.message}`, 'error');
    }
}

// --- Pool Fix Sub-Modal ---

function openPoolFixModal(trackId, trackName, artistName) {
    // Create sub-modal overlay inside the pool modal
    let fixOverlay = document.getElementById('pool-fix-overlay');
    if (fixOverlay) fixOverlay.remove();

    fixOverlay = document.createElement('div');
    fixOverlay.className = 'pool-fix-overlay';
    fixOverlay.id = 'pool-fix-overlay';

    // Only close on click to the overlay itself — use a dedicated close zone
    // to prevent accidental dismissal when clicking near inputs
    fixOverlay.addEventListener('mousedown', (e) => {
        if (e.target === fixOverlay) {
            e.preventDefault(); // Prevent stealing focus from inputs
            closePoolFixModal();
        }
    });

    fixOverlay.innerHTML = `
        <div class="pool-fix-modal" onmousedown="event.stopPropagation()">
            <div class="pool-fix-header">
                <h2>Fix Track Match</h2>
                <button class="pool-fix-close" onclick="closePoolFixModal()" title="Close">✕</button>
            </div>
            <div class="pool-fix-body">
                <div class="pool-fix-source">
                    <div class="pool-fix-source-label">Original Track</div>
                    <div class="pool-fix-source-row">
                        <span class="pool-fix-source-title">${_esc(trackName)}</span>
                        <span class="pool-fix-source-sep">—</span>
                        <span class="pool-fix-source-artist">${_esc(artistName)}</span>
                    </div>
                </div>
                <div class="pool-fix-search">
                    <div class="pool-fix-input-row">
                        <div class="pool-fix-input-wrap">
                            <label for="pool-fix-track-input">Track</label>
                            <input type="text" id="pool-fix-track-input" placeholder="Track name" value="${_escAttr(trackName)}">
                        </div>
                        <div class="pool-fix-input-wrap">
                            <label for="pool-fix-artist-input">Artist</label>
                            <input type="text" id="pool-fix-artist-input" placeholder="Artist name" value="${_escAttr(artistName)}">
                        </div>
                        <button class="pool-fix-search-btn" onclick="searchPoolFix()">Search</button>
                    </div>
                </div>
                <div class="pool-fix-results-area">
                    <div id="pool-fix-results" class="pool-fix-results-list">
                        <div class="pool-fix-empty">Searching...</div>
                    </div>
                </div>
            </div>
            <div class="pool-fix-footer">
                <button class="pool-fix-cancel" onclick="closePoolFixModal()">Cancel</button>
            </div>
        </div>
    `;

    fixOverlay.dataset.trackId = trackId;
    document.body.appendChild(fixOverlay);

    // Add enter key support
    const trackInput = fixOverlay.querySelector('#pool-fix-track-input');
    const artistInput = fixOverlay.querySelector('#pool-fix-artist-input');
    const enterHandler = (e) => { if (e.key === 'Enter') searchPoolFix(); };
    trackInput.addEventListener('keypress', enterHandler);
    artistInput.addEventListener('keypress', enterHandler);

    // Focus the track input
    trackInput.focus();
    trackInput.select();

    // Auto-search after a delay
    setTimeout(() => searchPoolFix(), 500);
}

function closePoolFixModal() {
    const fixOverlay = document.getElementById('pool-fix-overlay');
    if (fixOverlay) fixOverlay.remove();
}

async function searchPoolFix() {
    const trackInput = document.getElementById('pool-fix-track-input');
    const artistInput = document.getElementById('pool-fix-artist-input');
    const resultsContainer = document.getElementById('pool-fix-results');
    if (!trackInput || !resultsContainer) return;

    const trackVal = trackInput.value.trim();
    const artistVal = artistInput.value.trim();
    if (!trackVal && !artistVal) {
        resultsContainer.innerHTML = '<div class="pool-fix-empty">Enter a search term</div>';
        return;
    }

    resultsContainer.innerHTML = '<div class="pool-fix-empty"><div class="pool-fix-spinner"></div>Searching...</div>';

    try {
        const params = new URLSearchParams();
        if (trackVal) params.set('track', trackVal);
        if (artistVal) params.set('artist', artistVal);
        params.set('limit', '20');
        const res = await fetch(`/api/spotify/search_tracks?${params.toString()}`);
        const data = await res.json();
        const tracks = data.tracks || [];

        if (tracks.length === 0) {
            resultsContainer.innerHTML = '<div class="pool-fix-empty">No results found</div>';
            return;
        }

        resultsContainer.innerHTML = tracks.map((track) => {
            const artists = (track.artists || []).join(', ');
            const duration = track.duration_ms ? formatDuration(track.duration_ms) : '';
            const albumText = track.album ? ` · ${_esc(track.album)}` : '';
            return `
                <div class="pool-fix-result" onclick='selectPoolFixTrack(${JSON.stringify(track).replace(/'/g, "&#39;")})'>
                    <div class="pool-fix-result-main">
                        <div class="pool-fix-result-title">${_esc(track.name || 'Unknown')}</div>
                        <div class="pool-fix-result-meta">${_esc(artists)}${albumText}</div>
                    </div>
                    ${duration ? `<div class="pool-fix-result-dur">${duration}</div>` : ''}
                </div>
            `;
        }).join('');
    } catch (err) {
        resultsContainer.innerHTML = `<div class="pool-fix-empty">Search failed: ${_esc(err.message)}</div>`;
    }
}

async function selectPoolFixTrack(track) {
    const fixOverlay = document.getElementById('pool-fix-overlay');
    if (!fixOverlay) return;

    // Confirm selection
    const artists = (track.artists || []).join(', ');
    if (!await showConfirmDialog({ title: 'Confirm Match', message: `Match to "${track.name}" by ${artists}?`, confirmText: 'Confirm' })) return;

    const isRematch = fixOverlay.dataset.mode === 'rematch';

    try {
        let res, data;
        if (isRematch) {
            // Rematch mode: save new match to discovery cache
            res = await fetch('/api/discovery-pool/rematch', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    cache_id: parseInt(fixOverlay.dataset.cacheId),
                    original_title: fixOverlay.dataset.originalTitle,
                    original_artist: fixOverlay.dataset.originalArtist,
                    spotify_track: track,
                }),
            });
            data = await res.json();
        } else {
            // Normal fix mode: save to mirrored track
            const trackId = parseInt(fixOverlay.dataset.trackId);
            res = await fetch('/api/discovery-pool/fix', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    track_id: trackId,
                    spotify_track: track,
                }),
            });
            data = await res.json();
        }

        if (data.success) {
            showToast(`Matched: ${track.name}`, 'success');
            closePoolFixModal();
            // Refresh pool data
            filterDiscoveryPool(_discoveryPoolPlaylistFilter || '');
        } else {
            showToast(data.error || 'Failed to fix track', 'error');
        }
    } catch (err) {
        showToast(`Error: ${err.message}`, 'error');
    }
}

async function deleteMirroredPlaylist(playlistId, name) {
    if (!await showConfirmDialog({ title: 'Delete Playlist', message: `Delete mirrored playlist "${name}"?`, confirmText: 'Delete', destructive: true })) return;
    try {
        const res = await fetch(`/api/mirrored-playlists/${playlistId}`, { method: 'DELETE' });
        const data = await res.json();
        if (data.success) {
            showToast(`Deleted mirror: ${name}`, 'success');
            loadMirroredPlaylists();
        } else {
            showToast(data.error || 'Failed to delete', 'error');
        }
    } catch (err) {
        showToast(`Error: ${err.message}`, 'error');
    }
}

/**
 * Launch the existing discovery modal for a mirrored playlist by creating
 * a temporary entry in youtubePlaylistStates and reusing openYouTubeDiscoveryModal.
 */
async function discoverMirroredPlaylist(playlistId) {
    closeMirroredModal();
    const tempHash = `mirrored_${playlistId}`;

    // If state already exists (discovery in progress or completed), just reopen the modal
    const existingState = youtubePlaylistStates[tempHash];
    const hasActiveDiscovery = activeYouTubePollers[tempHash] || document.getElementById(`youtube-discovery-modal-${tempHash}`);
    if (existingState && (existingState.phase !== 'fresh' || hasActiveDiscovery)) {
        openYouTubeDiscoveryModal(tempHash);
        // Resume polling if discovery is in progress but poller stopped
        if (existingState.phase === 'discovering' && !activeYouTubePollers[tempHash]) {
            startYouTubeDiscoveryPolling(tempHash);
        }
        return;
    }

    showLoadingOverlay('Preparing discovery...');
    try {
        // Register the mirrored playlist on the backend so the YouTube discovery pipeline can find it
        const prepRes = await fetch(`/api/mirrored-playlists/${playlistId}/prepare-discovery`, { method: 'POST' });
        const prepData = await prepRes.json();
        if (prepData.error) throw new Error(prepData.error);

        // Also fetch the full data for the frontend state
        const res = await fetch(`/api/mirrored-playlists/${playlistId}`);
        const data = await res.json();
        if (data.error) throw new Error(data.error);
        hideLoadingOverlay();

        // Build tracks in the format the discovery modal expects
        const tracks = (data.tracks || []).map(t => ({
            id: t.source_track_id || `mirrored_${t.id}`,
            name: t.track_name,
            artists: [t.artist_name],
            album: t.album_name || '',
            duration_ms: t.duration_ms || 0
        }));

        // Check if backend returned cached results
        if (prepData.from_cache) {
            // Fetch the pre-populated status from the backend
            const statusRes = await fetch(`/api/youtube/discovery/status/${tempHash}`);
            const statusData = await statusRes.json();
            if (statusData.error) throw new Error(statusData.error);

            youtubePlaylistStates[tempHash] = {
                playlist: {
                    name: data.name,
                    tracks: tracks,
                    track_count: tracks.length
                },
                phase: statusData.phase || 'discovered',
                discovery_results: statusData.results || [],
                discoveryResults: statusData.results || [],
                discovery_progress: statusData.progress || 100,
                spotify_matches: statusData.spotify_matches || 0,
                spotifyMatches: statusData.spotify_matches || 0,
                spotify_total: tracks.length,
                status: statusData.status || 'complete',
                url: `mirrored://${data.source}/${data.source_playlist_id}`,
                sync_playlist_id: null,
                converted_spotify_playlist_id: null,
                download_process_id: null,
                created_at: Date.now() / 1000,
                last_accessed: Date.now() / 1000,
                discovery_future: null,
                sync_progress: {},
                is_mirrored_playlist: true,
                mirrored_source: data.source
            };

            const cached = prepData.cached_matches || 0;
            const total = prepData.total_tracks || tracks.length;
            showToast(`Loaded ${cached}/${total} cached discovery results`, 'success');
        } else {
            // No cached data — fresh state
            youtubePlaylistStates[tempHash] = {
                playlist: {
                    name: data.name,
                    tracks: tracks,
                    track_count: tracks.length
                },
                phase: 'fresh',
                discovery_results: [],
                discovery_progress: 0,
                spotify_matches: 0,
                spotify_total: tracks.length,
                status: 'parsed',
                url: `mirrored://${data.source}/${data.source_playlist_id}`,
                sync_playlist_id: null,
                converted_spotify_playlist_id: null,
                download_process_id: null,
                created_at: Date.now() / 1000,
                last_accessed: Date.now() / 1000,
                discovery_future: null,
                sync_progress: {},
                is_mirrored_playlist: true,
                mirrored_source: data.source
            };
        }

        openYouTubeDiscoveryModal(tempHash);
    } catch (err) {
        hideLoadingOverlay();
        showToast(`Error: ${err.message}`, 'error');
    }
}

// ===============================
// AUTOMATIONS — Visual Builder
// ===============================

async function retryFailedMirroredDiscovery(urlHash) {
    // Extract playlist ID from url_hash (format: "mirrored_<id>")
    const playlistId = urlHash.replace('mirrored_', '');
    try {
        const res = await fetch(`/api/mirrored-playlists/${playlistId}/retry-failed-discovery`, { method: 'POST' });
        const data = await res.json();
        if (data.error) {
            showToast(`Error: ${data.error}`, 'error');
            return;
        }
        if (data.retry_count === 0) {
            showToast('All tracks already found!', 'success');
            return;
        }

        // Update frontend state to discovering
        const state = youtubePlaylistStates[urlHash];
        if (state) {
            state.phase = 'discovering';
            state.status = 'discovering';
            state.discovery_progress = 0;
        }

        // Update modal buttons to show discovering state
        updateYouTubeModalButtons(urlHash, 'discovering');

        // Start polling for progress
        startYouTubeDiscoveryPolling(urlHash);

        showToast(`Retrying ${data.retry_count} failed tracks...`, 'info');
    } catch (err) {
        showToast(`Error retrying discovery: ${err.message}`, 'error');
    }
}

let _autoBlocks = null; // cached block definitions from /api/automations/blocks
let _autoBuilder = { editId: null, when: null, do: null, then: [], isSystem: false };

let _autoMirroredPlaylists = null; // cached mirrored playlist list
let _autoSpotifyAuthenticated = false; // whether Spotify is authed (for refresh filtering)

const _autoIcons = {
    schedule: '\u23F1\uFE0F', daily_time: '\u{1F570}\uFE0F', weekly_time: '\uD83D\uDCC5', app_started: '\uD83D\uDE80', track_downloaded: '\u2B07\uFE0F', batch_complete: '\u2705',
    watchlist_new_release: '\uD83D\uDD14', playlist_synced: '\uD83D\uDD04',
    playlist_changed: '\u270F\uFE0F',
    process_wishlist: '\uD83D\uDCCB', scan_watchlist: '\uD83D\uDC41\uFE0F',
    scan_library: '\uD83D\uDD04', refresh_mirrored: '\uD83D\uDCC2', sync_playlist: '\uD83D\uDD01',
    discover_playlist: '\uD83D\uDD0D', discovery_completed: '\uD83D\uDD0D',
    notify_only: '\uD83D\uDD14', discord_webhook: '\uD83D\uDCAC', pushbullet: '\uD83D\uDD14', telegram: '\u2709\uFE0F', webhook: '\uD83C\uDF10',
    signal_received: '\u26A1', fire_signal: '\u26A1', run_script: '\uD83D\uDCBB',
    // Phase 3
    wishlist_processing_completed: '\u2705', watchlist_scan_completed: '\u2705',
    database_update_completed: '\uD83D\uDDC4\uFE0F', download_failed: '\u274C',
    download_quarantined: '\u26A0\uFE0F', wishlist_item_added: '\u2795',
    watchlist_artist_added: '\uD83D\uDC64', watchlist_artist_removed: '\uD83D\uDC64',
    import_completed: '\uD83D\uDCE5', mirrored_playlist_created: '\uD83D\uDCC2',
    quality_scan_completed: '\uD83D\uDCCA', duplicate_scan_completed: '\uD83D\uDDC2\uFE0F', library_scan_completed: '\uD83D\uDCE1',
    start_database_update: '\uD83D\uDDC4\uFE0F', run_duplicate_cleaner: '\uD83D\uDDC2\uFE0F',
    clear_quarantine: '\uD83D\uDDD1\uFE0F', cleanup_wishlist: '\uD83E\uDDF9',
    update_discovery_pool: '\uD83E\uDDED', start_quality_scan: '\uD83D\uDCCA',
    backup_database: '\uD83D\uDCBE',
    refresh_beatport_cache: '\uD83C\uDFB5',
    clean_search_history: '\uD83D\uDDD1\uFE0F',
    clean_completed_downloads: '\u2705',
    full_cleanup: '\uD83E\uDDF9',
    playlist_pipeline: '\uD83D\uDE80',
};

// --- Inspiration Templates ---
// --- Automation Hub Data ---

// ── Automation Hub: One-Click Pipeline Groups ──
const AUTO_HUB_GROUPS = [
    {
        id: 'playlist-pipeline', icon: '🚀', name: 'Playlist Pipeline (All-in-One)',
        desc: 'Single automation that runs the full playlist lifecycle: refresh → discover → sync → download missing. No signal wiring needed.',
        category: 'Sync', badge: '1 automation', color: '#8b5cf6',
        steps: [
            { label: 'Refresh', icon: '🔄', type: 'action' },
            { label: 'Discover', icon: '🔍', type: 'action' },
            { label: 'Sync', icon: '🔗', type: 'action' },
            { label: 'Download', icon: '📥', type: 'action' },
        ],
        automations: [
            { name: 'Playlist Pipeline', trigger_type: 'schedule', trigger_config: { interval: 6, unit: 'hours' }, action_type: 'playlist_pipeline', action_config: { all: true }, then_actions: [], group_name: 'Playlist Pipeline' },
        ]
    },
    {
        id: 'new-music-pipeline', icon: '🚀', name: 'New Music Pipeline',
        desc: 'Full hands-free new music workflow. Scans your watchlist for releases, downloads them, cleans up, and notifies you.',
        category: 'Discovery', badge: '4 automations', color: '#f97316',
        steps: [
            { label: 'Scan Artists', icon: '🔍', type: 'action' },
            { label: 'Download', icon: '📥', type: 'action' },
            { label: 'Cleanup', icon: '🧹', type: 'action' },
            { label: 'Notify', icon: '🔔', type: 'notify' },
        ],
        automations: [
            { name: 'New Music — Scan Watchlist', trigger_type: 'schedule', trigger_config: { interval: 12, unit: 'hours' }, action_type: 'scan_watchlist', action_config: {}, then_actions: [{ type: 'fire_signal', config: { signal_name: 'nm_scanned' } }], group_name: 'New Music Pipeline' },
            { name: 'New Music — Download', trigger_type: 'signal_received', trigger_config: { signal_name: 'nm_scanned' }, action_type: 'process_wishlist', action_config: {}, then_actions: [{ type: 'fire_signal', config: { signal_name: 'nm_downloaded' } }], group_name: 'New Music Pipeline' },
            { name: 'New Music — Cleanup', trigger_type: 'signal_received', trigger_config: { signal_name: 'nm_downloaded' }, action_type: 'full_cleanup', action_config: {}, then_actions: [{ type: 'fire_signal', config: { signal_name: 'nm_cleaned' } }], group_name: 'New Music Pipeline' },
            { name: 'New Music — Notify', trigger_type: 'signal_received', trigger_config: { signal_name: 'nm_cleaned' }, action_type: 'notify_only', action_config: {}, then_actions: [], group_name: 'New Music Pipeline', needs_notify: true },
        ]
    },
    {
        id: 'nightly-ops', icon: '🌙', name: 'Nightly Operations',
        desc: 'Staggered overnight maintenance: scan, download, cleanup, and backup while you sleep.',
        category: 'Maintenance', badge: '4 automations', color: '#8b5cf6',
        steps: [
            { label: '1AM Scan', icon: '🔍', type: 'action' },
            { label: '2AM Download', icon: '📥', type: 'action' },
            { label: '3AM Cleanup', icon: '🧹', type: 'action' },
            { label: '4AM Backup', icon: '💾', type: 'action' },
        ],
        automations: [
            { name: 'Nightly — 1AM Scan', trigger_type: 'daily_time', trigger_config: { time: '01:00' }, action_type: 'scan_watchlist', action_config: {}, then_actions: [], group_name: 'Nightly Operations' },
            { name: 'Nightly — 2AM Download', trigger_type: 'daily_time', trigger_config: { time: '02:00' }, action_type: 'process_wishlist', action_config: {}, then_actions: [], group_name: 'Nightly Operations' },
            { name: 'Nightly — 3AM Cleanup', trigger_type: 'daily_time', trigger_config: { time: '03:00' }, action_type: 'full_cleanup', action_config: {}, then_actions: [], group_name: 'Nightly Operations' },
            { name: 'Nightly — 4AM Backup', trigger_type: 'daily_time', trigger_config: { time: '04:00' }, action_type: 'backup_database', action_config: {}, then_actions: [], group_name: 'Nightly Operations' },
        ]
    },
    {
        id: 'download-monitor', icon: '📊', name: 'Download Monitor',
        desc: 'Stay informed about your downloads. Get notified on failures, quarantined files, and completed batches.',
        category: 'Alerts', badge: '3 automations', color: '#ef4444',
        steps: [
            { label: 'Failures', icon: '❌', type: 'notify' },
            { label: 'Quarantine', icon: '⚠️', type: 'notify' },
            { label: 'Complete', icon: '✅', type: 'notify' },
        ],
        automations: [
            { name: 'Alert — Download Failed', trigger_type: 'download_failed', trigger_config: {}, action_type: 'notify_only', action_config: {}, then_actions: [], group_name: 'Download Monitor', needs_notify: true },
            { name: 'Alert — File Quarantined', trigger_type: 'download_quarantined', trigger_config: {}, action_type: 'notify_only', action_config: {}, then_actions: [], group_name: 'Download Monitor', needs_notify: true },
            { name: 'Alert — Batch Complete', trigger_type: 'batch_complete', trigger_config: {}, action_type: 'notify_only', action_config: {}, then_actions: [], group_name: 'Download Monitor', needs_notify: true },
        ]
    },
    {
        id: 'library-guardian', icon: '🛡️', name: 'Library Guardian',
        desc: 'Protect your library quality. After scans, runs quality checks and notifies you of any issues found.',
        category: 'Maintenance', badge: '2 automations', color: '#f59e0b',
        steps: [
            { label: 'Quality Scan', icon: '✅', type: 'action' },
            { label: 'Notify', icon: '🔔', type: 'notify' },
        ],
        automations: [
            { name: 'Guardian — Quality Check', trigger_type: 'library_scan_completed', trigger_config: {}, action_type: 'start_quality_scan', action_config: {}, then_actions: [{ type: 'fire_signal', config: { signal_name: 'guardian_quality_done' } }], group_name: 'Library Guardian' },
            { name: 'Guardian — Notify', trigger_type: 'signal_received', trigger_config: { signal_name: 'guardian_quality_done' }, action_type: 'notify_only', action_config: {}, then_actions: [], group_name: 'Library Guardian', needs_notify: true },
        ]
    },
    {
        id: 'startup-recovery', icon: '⚡', name: 'Startup Recovery',
        desc: 'Self-heal after a restart. Scans your library, processes pending wishlist items, and cleans up automatically.',
        category: 'Maintenance', badge: '3 automations', color: '#14b8a6',
        steps: [
            { label: 'Scan Library', icon: '📚', type: 'action' },
            { label: 'Process Wishlist', icon: '📥', type: 'action' },
            { label: 'Cleanup', icon: '🧹', type: 'action' },
        ],
        automations: [
            { name: 'Startup — Scan Library', trigger_type: 'app_started', trigger_config: {}, action_type: 'scan_library', action_config: {}, then_actions: [{ type: 'fire_signal', config: { signal_name: 'startup_scanned' } }], group_name: 'Startup Recovery' },
            { name: 'Startup — Process Wishlist', trigger_type: 'signal_received', trigger_config: { signal_name: 'startup_scanned' }, action_type: 'process_wishlist', action_config: {}, then_actions: [{ type: 'fire_signal', config: { signal_name: 'startup_processed' } }], group_name: 'Startup Recovery' },
            { name: 'Startup — Cleanup', trigger_type: 'signal_received', trigger_config: { signal_name: 'startup_processed' }, action_type: 'full_cleanup', action_config: {}, then_actions: [], group_name: 'Startup Recovery' },
        ]
    },
    {
        id: 'import-pipeline', icon: '📦', name: 'Import Pipeline',
        desc: 'After importing files, automatically scans your library, runs a quality check, and notifies you when complete.',
        category: 'Maintenance', badge: '3 automations', color: '#a855f7',
        steps: [
            { label: 'Scan Library', icon: '📚', type: 'action' },
            { label: 'Quality Check', icon: '✅', type: 'action' },
            { label: 'Notify', icon: '🔔', type: 'notify' },
        ],
        automations: [
            { name: 'Import — Scan Library', trigger_type: 'import_completed', trigger_config: {}, action_type: 'scan_library', action_config: {}, then_actions: [{ type: 'fire_signal', config: { signal_name: 'import_scanned' } }], group_name: 'Import Pipeline' },
            { name: 'Import — Quality Check', trigger_type: 'signal_received', trigger_config: { signal_name: 'import_scanned' }, action_type: 'start_quality_scan', action_config: {}, then_actions: [{ type: 'fire_signal', config: { signal_name: 'import_quality_done' } }], group_name: 'Import Pipeline' },
            { name: 'Import — Notify', trigger_type: 'signal_received', trigger_config: { signal_name: 'import_quality_done' }, action_type: 'notify_only', action_config: {}, then_actions: [], group_name: 'Import Pipeline', needs_notify: true },
        ]
    },
    {
        id: 'weekly-deep-clean', icon: '✨', name: 'Weekly Deep Clean',
        desc: 'Comprehensive weekly sweep: find duplicates, check quality, clean up, back up, and report results.',
        category: 'Maintenance', badge: '5 automations', color: '#ec4899',
        steps: [
            { label: 'Duplicates', icon: '📋', type: 'action' },
            { label: 'Quality', icon: '✅', type: 'action' },
            { label: 'Cleanup', icon: '🧹', type: 'action' },
            { label: 'Backup', icon: '💾', type: 'action' },
            { label: 'Notify', icon: '🔔', type: 'notify' },
        ],
        automations: [
            { name: 'Deep Clean — Duplicates', trigger_type: 'weekly_time', trigger_config: { days: ['sunday'], time: '02:00' }, action_type: 'run_duplicate_cleaner', action_config: {}, then_actions: [{ type: 'fire_signal', config: { signal_name: 'dc_dedup_done' } }], group_name: 'Weekly Deep Clean' },
            { name: 'Deep Clean — Quality', trigger_type: 'signal_received', trigger_config: { signal_name: 'dc_dedup_done' }, action_type: 'start_quality_scan', action_config: {}, then_actions: [{ type: 'fire_signal', config: { signal_name: 'dc_quality_done' } }], group_name: 'Weekly Deep Clean' },
            { name: 'Deep Clean — Cleanup', trigger_type: 'signal_received', trigger_config: { signal_name: 'dc_quality_done' }, action_type: 'full_cleanup', action_config: {}, then_actions: [{ type: 'fire_signal', config: { signal_name: 'dc_cleanup_done' } }], group_name: 'Weekly Deep Clean' },
            { name: 'Deep Clean — Backup', trigger_type: 'signal_received', trigger_config: { signal_name: 'dc_cleanup_done' }, action_type: 'backup_database', action_config: {}, then_actions: [{ type: 'fire_signal', config: { signal_name: 'dc_backup_done' } }], group_name: 'Weekly Deep Clean' },
            { name: 'Deep Clean — Notify', trigger_type: 'signal_received', trigger_config: { signal_name: 'dc_backup_done' }, action_type: 'notify_only', action_config: {}, then_actions: [], group_name: 'Weekly Deep Clean', needs_notify: true },
        ]
    },
    {
        id: 'beatport-fresh', icon: '🎧', name: 'Beatport Fresh',
        desc: 'Keep your Beatport charts and playlists up to date with a daily cache refresh.',
        category: 'Discovery', badge: '1 automation', color: '#84cc16',
        steps: [
            { label: 'Refresh Cache', icon: '🔄', type: 'action' },
        ],
        automations: [
            { name: 'Beatport — Daily Refresh', trigger_type: 'daily_time', trigger_config: { time: '05:00' }, action_type: 'refresh_beatport_cache', action_config: {}, then_actions: [], group_name: 'Beatport Fresh' },
        ]
    },
];

const AUTO_HUB_RECIPES = [
    // Sync & Playlists
    {
        id: 'spotify-auto-sync', icon: '\uD83D\uDD01', name: 'Spotify Playlist Auto-Sync', desc: 'Refresh all mirrored playlists every 6 hours to keep them in sync with Spotify.',
        category: 'Sync', difficulty: 'beginner', when: { type: 'schedule', config: { interval: 6, unit: 'hours' } }, do: { type: 'refresh_mirrored', config: {} }, then: []
    },
    {
        id: 'release-radar-pipeline', icon: '\uD83D\uDCE1', name: 'Release Radar Pipeline', desc: 'Every Friday, refresh mirrored playlists, discover new tracks, then sync. Chain 3 automations for a full pipeline.',
        category: 'Sync', difficulty: 'intermediate', when: { type: 'weekly_time', config: { days: ['friday'], time: '18:00' } }, do: { type: 'refresh_mirrored', config: {} }, then: [],
        chain: ['Refresh Mirrored', 'Discover Playlist', 'Sync Playlist'], note: 'Create 3 separate automations and chain them with signals for the full pipeline.'
    },
    {
        id: 'discover-weekly-grab', icon: '\uD83C\uDFB5', name: 'Discover Weekly Grab', desc: 'Every Monday, refresh your mirrored Discover Weekly to capture the new playlist before Spotify replaces it.',
        category: 'Sync', difficulty: 'beginner', when: { type: 'weekly_time', config: { days: ['monday'], time: '08:00' } }, do: { type: 'refresh_mirrored', config: {} }, then: []
    },
    {
        id: 'playlist-change-watcher', icon: '\uD83D\uDD14', name: 'Playlist Change Watcher', desc: 'Get a Discord notification whenever any tracked playlist changes.',
        category: 'Sync', difficulty: 'beginner', when: { type: 'playlist_changed', config: {} }, do: { type: 'notify_only', config: {} }, then: [{ type: 'discord_webhook', config: {} }]
    },
    {
        id: 'new-mirror-discovery', icon: '\uD83D\uDD0D', name: 'New Mirror Auto-Discovery', desc: 'Automatically discover tracks when you mirror a new playlist.',
        category: 'Sync', difficulty: 'beginner', when: { type: 'mirrored_playlist_created', config: {} }, do: { type: 'discover_playlist', config: {} }, then: []
    },
    // New Music Discovery
    {
        id: 'complete-new-release', icon: '\uD83D\uDE80', name: 'Complete New Release Pipeline', desc: 'Full hands-free chain: scan watchlist \u2192 process wishlist \u2192 quality scan \u2192 notify. Requires 3 automations linked by signals.',
        category: 'Discovery', difficulty: 'advanced', when: { type: 'schedule', config: { interval: 12, unit: 'hours' } }, do: { type: 'scan_watchlist', config: {} }, then: [{ type: 'fire_signal', config: { signal_name: 'watchlist_done' } }],
        chain: ['Scan Watchlist', '\u26A1 watchlist_done', 'Process Wishlist', '\u26A1 wishlist_done', 'Quality Scan', 'Discord'],
        note: 'Create 3 automations: (1) Schedule\u2192Scan Watchlist\u2192fire watchlist_done, (2) Signal watchlist_done\u2192Process Wishlist\u2192fire wishlist_done, (3) Signal wishlist_done\u2192Quality Scan\u2192Discord.'
    },
    {
        id: 'new-release-monitor', icon: '\uD83D\uDD14', name: 'New Release Monitor', desc: 'Scan your watchlist for new releases every 12 hours.',
        category: 'Discovery', difficulty: 'beginner', when: { type: 'schedule', config: { interval: 12, unit: 'hours' } }, do: { type: 'scan_watchlist', config: {} }, then: []
    },
    {
        id: 'artist-watch-alert', icon: '\uD83C\uDFA4', name: 'Artist Watch Alert', desc: 'Get a Telegram notification when you add a new artist to your watchlist.',
        category: 'Discovery', difficulty: 'beginner', when: { type: 'watchlist_artist_added', config: {} }, do: { type: 'notify_only', config: {} }, then: [{ type: 'telegram', config: {} }]
    },
    {
        id: 'discovery-pool-refresh', icon: '\uD83C\uDF10', name: 'Discovery Pool Refresh', desc: 'Refresh the discovery pool every night at 2 AM with fresh recommendations.',
        category: 'Discovery', difficulty: 'beginner', when: { type: 'daily_time', config: { time: '02:00' } }, do: { type: 'update_discovery_pool', config: {} }, then: []
    },
    {
        id: 'nightly-wishlist', icon: '\uD83C\uDF19', name: 'Nightly Wishlist Processor', desc: 'Process your wishlist at 3 AM every night while you sleep.',
        category: 'Discovery', difficulty: 'beginner', when: { type: 'daily_time', config: { time: '03:00' } }, do: { type: 'process_wishlist', config: {} }, then: []
    },
    // Library Maintenance
    {
        id: 'full-library-maintenance', icon: '\uD83E\uDDF9', name: 'Full Library Maintenance', desc: 'Run full cleanup every Saturday at 5 AM \u2014 dedup, quarantine, wishlist tidy.',
        category: 'Maintenance', difficulty: 'intermediate', when: { type: 'weekly_time', config: { days: ['saturday'], time: '05:00' } }, do: { type: 'full_cleanup', config: {} }, then: []
    },
    {
        id: 'post-batch-cleanup', icon: '\uD83E\uDDF9', name: 'Post-Batch Cleanup', desc: 'Run a full cleanup after any batch download completes.',
        category: 'Maintenance', difficulty: 'beginner', when: { type: 'batch_complete', config: {} }, do: { type: 'full_cleanup', config: {} }, then: []
    },
    {
        id: 'weekly-db-backup', icon: '\uD83D\uDCBE', name: 'Weekly Database Backup', desc: 'Back up your database every Sunday at 4 AM.',
        category: 'Maintenance', difficulty: 'beginner', when: { type: 'weekly_time', config: { days: ['sunday'], time: '04:00' } }, do: { type: 'backup_database', config: {} }, then: []
    },
    {
        id: 'quality-assurance', icon: '\u2705', name: 'Quality Assurance Pipeline', desc: 'After a library scan completes, run a quality scan and fire a signal when done.',
        category: 'Maintenance', difficulty: 'intermediate', when: { type: 'library_scan_completed', config: {} }, do: { type: 'start_quality_scan', config: {} }, then: [{ type: 'fire_signal', config: { signal_name: 'quality_done' } }]
    },
    {
        id: 'import-cleanup', icon: '\uD83D\uDCE5', name: 'Import Cleanup', desc: 'Automatically scan the library after an import completes to keep things tidy.',
        category: 'Maintenance', difficulty: 'intermediate', when: { type: 'import_completed', config: {} }, do: { type: 'scan_library', config: {} }, then: []
    },
    // Notifications & Alerts
    {
        id: 'download-failure-alert', icon: '\u274C', name: 'Download Failure Alert', desc: 'Get notified via Discord when a download fails.',
        category: 'Alerts', difficulty: 'beginner', when: { type: 'download_failed', config: {} }, do: { type: 'notify_only', config: {} }, then: [{ type: 'discord_webhook', config: {} }]
    },
    {
        id: 'quarantine-alert', icon: '\u26A0\uFE0F', name: 'Quarantine Alert', desc: 'Get a Pushbullet alert when a file is quarantined.',
        category: 'Alerts', difficulty: 'beginner', when: { type: 'download_quarantined', config: {} }, do: { type: 'notify_only', config: {} }, then: [{ type: 'pushbullet', config: {} }]
    },
    {
        id: 'batch-complete-notify', icon: '\uD83C\uDFC1', name: 'Batch Complete Notification', desc: 'Get a Telegram message when a batch download finishes.',
        category: 'Alerts', difficulty: 'beginner', when: { type: 'batch_complete', config: {} }, do: { type: 'notify_only', config: {} }, then: [{ type: 'telegram', config: {} }]
    },
    // Power User Chains
    {
        id: 'full-hands-free', icon: '\uD83E\uDD16', name: 'Full Hands-Free Pipeline', desc: 'The ultimate automation chain: scan \u2192 process \u2192 download \u2192 clean \u2192 notify. Requires 5 automations linked by signals.',
        category: 'Chains', difficulty: 'advanced', when: { type: 'schedule', config: { interval: 12, unit: 'hours' } }, do: { type: 'scan_watchlist', config: {} }, then: [{ type: 'fire_signal', config: { signal_name: 'scan_done' } }],
        chain: ['Scan Watchlist', '\u26A1 scan_done', 'Process Wishlist', '\u26A1 process_done', 'Full Cleanup', '\u26A1 cleanup_done', 'Quality Scan', 'Discord'],
        note: 'Build 4-5 automations, each firing a signal for the next step. Start small and add stages.'
    },
    {
        id: 'staggered-nightly', icon: '\uD83C\uDF03', name: 'Staggered Nightly Pipeline', desc: 'Spread tasks across the night: 1 AM scan, 2 AM process, 3 AM cleanup, 4 AM backup.',
        category: 'Chains', difficulty: 'intermediate', when: { type: 'daily_time', config: { time: '01:00' } }, do: { type: 'scan_watchlist', config: {} }, then: [],
        chain: ['1:00 Scan', '2:00 Process', '3:00 Cleanup', '4:00 Backup'],
        note: 'Create 4 daily_time automations at staggered hours. No signals needed \u2014 just timing.'
    },
];

const AUTO_HUB_GUIDES = [
    {
        id: 'auto-sync-playlists', icon: '\uD83D\uDD01', title: 'Auto-Sync Your Spotify Playlists', subtitle: 'Mirror a Spotify playlist and schedule automatic refreshes.', difficulty: 'beginner',
        steps: [
            'Go to the <strong>Playlists</strong> page and find a Spotify playlist you want to track.',
            'Click <strong>Mirror Playlist</strong> to create a local copy.',
            'Go to <strong>Automations</strong> and click <strong>New Automation</strong>.',
            'Set WHEN to <strong>Schedule \u2192 Every 6 hours</strong>.',
            'Set DO to <strong>Refresh Mirrored Playlists</strong>.',
            'Save and enable \u2014 your playlist will now stay in sync automatically.'
        ], relatedRecipes: ['spotify-auto-sync', 'discover-weekly-grab']
    },
    {
        id: 'discord-download-alerts', icon: '\uD83D\uDCE2', title: 'Get Discord Alerts for Downloads', subtitle: 'Set up Discord webhook notifications for download events.', difficulty: 'beginner',
        steps: [
            'In Discord, go to your channel\'s settings \u2192 <strong>Integrations \u2192 Webhooks</strong>.',
            'Create a webhook and copy the URL.',
            'In SoulSync, go to <strong>Settings \u2192 Notifications</strong> and paste the Discord webhook URL.',
            'Go to <strong>Automations \u2192 New Automation</strong>.',
            'Set WHEN to <strong>Download Failed</strong> (or any event), DO to <strong>Notify Only</strong>, THEN to <strong>Discord</strong>.'
        ], relatedRecipes: ['download-failure-alert', 'batch-complete-notify']
    },
    {
        id: 'hands-free-pipeline', icon: '\uD83E\uDD16', title: 'Build a Hands-Free Library Pipeline', subtitle: 'Chain watchlist scanning, wishlist processing, and cleanup with signals.', difficulty: 'intermediate',
        steps: [
            'Create Automation 1: <strong>Schedule (12h) \u2192 Scan Watchlist</strong>, THEN fire signal <code>scan_done</code>.',
            'Create Automation 2: <strong>Signal scan_done \u2192 Process Wishlist</strong>, THEN fire signal <code>process_done</code>.',
            'Create Automation 3: <strong>Signal process_done \u2192 Full Cleanup</strong>.',
            'Enable all three automations.',
            'Test by manually running Automation 1 \u2014 watch the chain execute.',
            'Add a THEN notification (Discord/Telegram) to the last automation for completion alerts.',
            'Adjust the schedule interval based on how often you want new music checked.'
        ], relatedRecipes: ['complete-new-release', 'full-hands-free']
    },
    {
        id: 'signal-chains', icon: '\u26A1', title: 'Set Up Signal Chains', subtitle: 'Use fire_signal and signal_received to link automations together.', difficulty: 'advanced',
        steps: [
            'Understand the concept: <strong>fire_signal</strong> is a THEN action that emits a named signal. <strong>signal_received</strong> is a WHEN trigger that listens for it.',
            'In your first automation, add a THEN action \u2192 <strong>Fire Signal</strong> and name it (e.g., <code>step1_done</code>).',
            'Create a second automation with WHEN \u2192 <strong>Signal Received</strong> \u2192 signal name <code>step1_done</code>.',
            'The second automation will fire automatically when the first one completes.',
            'Chain up to 5 levels deep (safety limit). SoulSync detects cycles automatically.',
            'Use descriptive signal names like <code>watchlist_scanned</code> or <code>cleanup_finished</code>.'
        ], relatedRecipes: ['quality-assurance', 'complete-new-release']
    },
    {
        id: 'nightly-maintenance', icon: '\uD83C\uDF19', title: 'Schedule Nightly Maintenance', subtitle: 'Set up backup, cleanup, and quality scans to run overnight.', difficulty: 'intermediate',
        steps: [
            'Create a <strong>Daily Time (04:00) \u2192 Backup Database</strong> automation.',
            'Create a <strong>Weekly Time (Saturday, 05:00) \u2192 Full Cleanup</strong> automation.',
            'Create a <strong>Daily Time (02:00) \u2192 Update Discovery Pool</strong> automation.',
            'Stagger times by at least 1 hour to avoid resource contention.',
            'Add Discord/Telegram notifications to any you want alerts for.'
        ], relatedRecipes: ['weekly-db-backup', 'full-library-maintenance', 'staggered-nightly']
    },
];

const AUTO_HUB_TIPS = [
    { icon: '\u26A1', title: 'Signal Chaining 101', body: '<strong>fire_signal</strong> (a THEN action) emits a named event. <strong>signal_received</strong> (a WHEN trigger) listens for it. This lets you chain automations: when one finishes, the next starts automatically.', tag: 'Signals' },
    { icon: '\u23F0', title: 'Stagger Your Schedules', body: 'If you have multiple timed automations, space them at least 1 hour apart. Running scan, process, and cleanup at the same time creates resource contention and can slow everything down.', tag: 'Performance' },
    { icon: '\uD83C\uDFAF', title: 'Use Conditions to Filter', body: 'Add conditions to event triggers to only fire on specific artists, formats, or quality levels. For example, trigger only when a downloaded track\'s artist matches "Radiohead".', tag: 'Filtering' },
    { icon: '\uD83D\uDCC1', title: 'Group Related Automations', body: 'Use the Group dropdown when creating automations to organize them. Groups like "Nightly", "Notifications", or "Pipeline" make it easy to find and manage related automations.', tag: 'Organization' },
    { icon: '\uD83D\uDD04', title: 'Avoid Chain Loops', body: 'SoulSync has built-in cycle detection, but it\'s good practice to design signal names carefully. If A fires signal X and B listens for X and fires Y, make sure nothing fires X again downstream.', tag: 'Safety' },
    { icon: '\uD83D\uDCDA', title: 'Stack THEN Actions', body: 'Each automation supports up to 3 THEN actions. Combine notification channels (Discord + Telegram) with a fire_signal to both notify yourself and trigger the next automation.', tag: 'Power' },
    { icon: '\u2699\uFE0F', title: 'System vs Custom', body: 'System automations handle core tasks like Spotify enrichment and are managed automatically. Create custom automations to extend their behavior \u2014 trigger on their completion events.', tag: 'Basics' },
    { icon: '\uD83E\uDDEA', title: 'Test with Notify Only', body: 'Set DO to <strong>Notify Only</strong> when testing a new trigger. You\'ll see when it fires without any side effects. Once you\'re confident in the timing, switch to the real action.', tag: 'Testing' },
];

const AUTO_HUB_REFERENCE = {
    triggers: [
        {
            group: 'Time-Based', items: [
                { type: 'schedule', label: 'Schedule', desc: 'Repeating interval (e.g., every 6 hours)' },
                { type: 'daily_time', label: 'Daily Time', desc: 'Every day at a specific time (e.g., 03:00)' },
                { type: 'weekly_time', label: 'Weekly Time', desc: 'Specific days + time (e.g., Saturday at 05:00)' },
            ]
        },
        {
            group: 'Download Events', items: [
                { type: 'track_downloaded', label: 'Track Downloaded', desc: 'Fires when a single track download completes' },
                { type: 'batch_complete', label: 'Batch Complete', desc: 'Fires when a batch download job finishes' },
                { type: 'download_failed', label: 'Download Failed', desc: 'Fires when a download fails or errors out' },
                { type: 'download_quarantined', label: 'File Quarantined', desc: 'Fires when a downloaded file is quarantined for quality issues' },
            ]
        },
        {
            group: 'Watchlist & Wishlist', items: [
                { type: 'watchlist_new_release', label: 'New Release Found', desc: 'Fires when a watched artist has a new release' },
                { type: 'watchlist_scan_completed', label: 'Watchlist Scan Done', desc: 'Fires after a full watchlist scan completes' },
                { type: 'watchlist_artist_added', label: 'Artist Watched', desc: 'Fires when a new artist is added to the watchlist' },
                { type: 'watchlist_artist_removed', label: 'Artist Unwatched', desc: 'Fires when an artist is removed from the watchlist' },
                { type: 'wishlist_item_added', label: 'Wishlist Item Added', desc: 'Fires when a new item is added to the wishlist' },
                { type: 'wishlist_processing_completed', label: 'Wishlist Processed', desc: 'Fires after the wishlist processor completes a run' },
            ]
        },
        {
            group: 'Playlists', items: [
                { type: 'playlist_synced', label: 'Playlist Synced', desc: 'Fires when a playlist sync operation completes' },
                { type: 'playlist_changed', label: 'Playlist Changed', desc: 'Fires when a tracked playlist has changes detected' },
                { type: 'mirrored_playlist_created', label: 'Playlist Mirrored', desc: 'Fires when a new mirrored playlist is created' },
                { type: 'discovery_completed', label: 'Discovery Complete', desc: 'Fires when playlist discovery finishes' },
            ]
        },
        {
            group: 'Library & System', items: [
                { type: 'app_started', label: 'App Started', desc: 'Fires once when SoulSync starts up' },
                { type: 'import_completed', label: 'Import Complete', desc: 'Fires when a library import operation finishes' },
                { type: 'library_scan_completed', label: 'Library Scan Done', desc: 'Fires after a full library scan completes' },
                { type: 'quality_scan_completed', label: 'Quality Scan Done', desc: 'Fires when a quality scan finishes' },
                { type: 'duplicate_scan_completed', label: 'Duplicate Scan Done', desc: 'Fires when the duplicate scanner finishes' },
                { type: 'database_update_completed', label: 'Database Updated', desc: 'Fires after a database update operation' },
            ]
        },
        {
            group: 'Signals', items: [
                { type: 'signal_received', label: 'Signal Received', desc: 'Fires when a named signal is emitted by another automation\'s fire_signal THEN action' },
            ]
        },
    ],
    actions: [
        {
            group: 'Downloads & Sync', items: [
                { type: 'playlist_pipeline', label: 'Playlist Pipeline', desc: 'Full lifecycle: refresh → discover → sync → download missing' },
                { type: 'process_wishlist', label: 'Process Wishlist', desc: 'Download all pending wishlist items' },
                { type: 'refresh_mirrored', label: 'Refresh Mirrored', desc: 'Refresh all mirrored playlists from their sources' },
                { type: 'sync_playlist', label: 'Sync Playlist', desc: 'Sync a specific playlist to your library' },
                { type: 'discover_playlist', label: 'Discover Playlist', desc: 'Run track discovery on mirrored playlists' },
                { type: 'scan_watchlist', label: 'Scan Watchlist', desc: 'Check watched artists for new releases' },
                { type: 'update_discovery_pool', label: 'Update Discovery', desc: 'Refresh the discovery pool with new recommendations' },
            ]
        },
        {
            group: 'Library Tools', items: [
                { type: 'scan_library', label: 'Scan Library', desc: 'Full scan of local music library files' },
                { type: 'start_quality_scan', label: 'Quality Scan', desc: 'Check library tracks for quality issues' },
                { type: 'start_database_update', label: 'Update Database', desc: 'Run a database update/maintenance operation' },
                { type: 'backup_database', label: 'Backup Database', desc: 'Create a backup of the music database' },
            ]
        },
        {
            group: 'Cleanup', items: [
                { type: 'full_cleanup', label: 'Full Cleanup', desc: 'Run all cleanup tasks: dedup, quarantine, wishlist tidy' },
                { type: 'run_duplicate_cleaner', label: 'Duplicate Cleaner', desc: 'Find and handle duplicate tracks' },
                { type: 'clear_quarantine', label: 'Clear Quarantine', desc: 'Remove all quarantined files' },
                { type: 'cleanup_wishlist', label: 'Clean Wishlist', desc: 'Remove completed/invalid wishlist items' },
                { type: 'clean_search_history', label: 'Clean Search History', desc: 'Clear old search history entries' },
                { type: 'clean_completed_downloads', label: 'Clean Downloads', desc: 'Remove completed download records' },
            ]
        },
        {
            group: 'Other', items: [
                { type: 'notify_only', label: 'Notify Only', desc: 'No action \u2014 just trigger THEN notifications. Great for testing.' },
            ]
        },
    ],
    thenActions: [
        {
            group: 'Notifications', items: [
                { type: 'discord_webhook', label: 'Discord Webhook', desc: 'Send a message to a Discord channel via webhook' },
                { type: 'telegram', label: 'Telegram', desc: 'Send a message to a Telegram chat via bot' },
                { type: 'pushbullet', label: 'Pushbullet', desc: 'Send a push notification via Pushbullet' },
            ]
        },
        {
            group: 'Chaining', items: [
                { type: 'fire_signal', label: 'Fire Signal', desc: 'Emit a named signal that other automations can listen for with signal_received' },
            ]
        },
    ],
};

// --- Load & Render List ---

// Drag-and-drop state
let _autoDragState = null;
let _autoDragEnterCount = 0;
let _autoDragExpandTimer = null;

function _buildAutomationSection(id, label, automations, useGrid, options = {}) {
    const groupName = options.groupName || null;
    const isProtected = options.isProtected || false; // System, Hub sections

    const section = document.createElement('div');
    section.className = 'automations-section';
    if (isProtected) section.classList.add('section-protected');
    section.id = id;
    if (groupName) section.dataset.groupName = groupName;
    const collapsed = localStorage.getItem('auto_section_' + id) === '1';
    if (collapsed) section.classList.add('collapsed');

    const header = document.createElement('div');
    header.className = 'automations-section-header';

    // Group header actions (rename, bulk toggle, delete) — only for user groups
    let actionsHtml = '';
    if (groupName && !isProtected) {
        const enabledCount = automations.filter(a => a.enabled).length;
        const allEnabled = enabledCount === automations.length;
        actionsHtml = `
            <div class="section-actions" onclick="event.stopPropagation();">
                <button class="section-action-btn" title="${allEnabled ? 'Disable all' : 'Enable all'}" onclick="_bulkToggleGroup('${_escAttr(groupName)}', ${allEnabled})">
                    ${allEnabled ? '⏸' : '▶'}
                </button>
                <button class="section-action-btn" title="Rename group" onclick="_startRenameGroup('${_escAttr(groupName)}', this)">
                    ✏️
                </button>
                <button class="section-action-btn section-action-danger" title="Delete group" onclick="_deleteGroup('${_escAttr(groupName)}')">
                    🗑️
                </button>
            </div>
        `;
    }

    header.innerHTML = `
        <span class="section-chevron">&#9660;</span>
        <span class="section-label">${label}</span>
        <span class="section-count">${automations.length}</span>
        ${actionsHtml}
        <span class="section-line"></span>
    `;
    header.onclick = (e) => {
        if (e.target.closest('.section-actions')) return;
        section.classList.toggle('collapsed');
        localStorage.setItem('auto_section_' + id, section.classList.contains('collapsed') ? '1' : '0');
    };

    const body = document.createElement('div');
    body.className = 'automations-section-body';

    // Drop zone setup (not for protected sections)
    if (!isProtected) {
        const dropGroupName = groupName; // null for "My Automations"
        body.addEventListener('dragover', (e) => {
            if (!_autoDragState) return;
            e.preventDefault();
            e.dataTransfer.dropEffect = 'move';
            body.classList.add('drop-target');
        });
        body.addEventListener('dragenter', (e) => {
            if (!_autoDragState) return;
            _autoDragEnterCount++;
            body.classList.add('drop-target');
            // Expand collapsed sections on drag-hover
            if (section.classList.contains('collapsed')) {
                _autoDragExpandTimer = setTimeout(() => {
                    section.classList.remove('collapsed');
                }, 500);
            }
        });
        body.addEventListener('dragleave', (e) => {
            if (!_autoDragState) return;
            _autoDragEnterCount--;
            if (_autoDragEnterCount <= 0) {
                _autoDragEnterCount = 0;
                body.classList.remove('drop-target');
                if (_autoDragExpandTimer) { clearTimeout(_autoDragExpandTimer); _autoDragExpandTimer = null; }
            }
        });
        body.addEventListener('drop', async (e) => {
            e.preventDefault();
            body.classList.remove('drop-target');
            _autoDragEnterCount = 0;
            if (!_autoDragState) return;
            const draggedId = _autoDragState.id;
            const fromGroup = _autoDragState.groupName;
            if (fromGroup === dropGroupName) return; // Same group, no-op
            try {
                const res = await fetch('/api/automations/' + draggedId, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ group_name: dropGroupName })
                });
                const data = await res.json();
                if (data.error) throw new Error(data.error);
                showToast(dropGroupName ? `Moved to "${dropGroupName}"` : 'Moved to My Automations', 'success');
                await loadAutomations();
            } catch (err) { showToast('Error: ' + err.message, 'error'); }
        });
    }

    const container = document.createElement('div');
    container.className = useGrid ? 'automations-grid' : 'automations-user-list';
    automations.forEach(a => container.appendChild(renderAutomationCard(a)));
    body.appendChild(container);
    section.appendChild(header);
    section.appendChild(body);
    return section;
}

/**
 * Delete a group — ungroups all automations (moves to My Automations).
 */
async function _deleteGroup(groupName) {
    // Collect automation IDs in this group
    const ids = [];
    document.querySelectorAll(`.automations-section[data-group-name="${groupName}"] .automation-card`).forEach(card => {
        if (card.dataset.id) ids.push(parseInt(card.dataset.id));
    });

    if (ids.length === 0) { await loadAutomations(); return; }

    // Show choice dialog — ungroup or delete all
    const choice = await _showDeleteGroupDialog(groupName, ids.length);
    if (!choice) return;

    try {
        if (choice === 'ungroup') {
            const res = await fetch('/api/automations/group', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ automation_ids: ids, group_name: null })
            });
            const data = await res.json();
            if (data.error) throw new Error(data.error);
            showToast(`Dissolved group "${groupName}" — ${data.updated} automations moved to My Automations`, 'success');
        } else if (choice === 'delete_all') {
            // Delete each automation
            let deleted = 0;
            for (const id of ids) {
                try {
                    const res = await fetch('/api/automations/' + id, { method: 'DELETE' });
                    const data = await res.json();
                    if (data.success) deleted++;
                } catch (e) {}
            }
            showToast(`Deleted group "${groupName}" and ${deleted} automation${deleted !== 1 ? 's' : ''}`, 'success');
        }
        await loadAutomations();
    } catch (err) { showToast('Error: ' + err.message, 'error'); }
}

function _showDeleteGroupDialog(groupName, count) {
    return new Promise((resolve) => {
        const overlay = document.createElement('div');
        overlay.className = 'modal-overlay';
        overlay.style.display = 'flex';
        overlay.onclick = (e) => { if (e.target === overlay) { overlay.remove(); resolve(null); } };

        overlay.innerHTML = `
            <div class="delete-group-dialog">
                <div class="delete-group-icon">🗑️</div>
                <h3 class="delete-group-title">Delete Group "${groupName}"</h3>
                <p class="delete-group-message">This group contains ${count} automation${count !== 1 ? 's' : ''}. What would you like to do?</p>
                <div class="delete-group-actions">
                    <button class="delete-group-btn delete-group-keep" id="dg-ungroup">
                        Keep Automations — move to My Automations
                    </button>
                    <button class="delete-group-btn delete-group-remove" id="dg-delete">
                        Delete Everything — remove group and all ${count} automation${count !== 1 ? 's' : ''}
                    </button>
                    <button class="delete-group-btn delete-group-cancel" id="dg-cancel">
                        Cancel
                    </button>
                </div>
            </div>
        `;

        overlay.querySelector('#dg-ungroup').onclick = () => { overlay.remove(); resolve('ungroup'); };
        overlay.querySelector('#dg-delete').onclick = () => { overlay.remove(); resolve('delete_all'); };
        overlay.querySelector('#dg-cancel').onclick = () => { overlay.remove(); resolve(null); };

        document.addEventListener('keydown', function esc(e) {
            if (e.key === 'Escape') { overlay.remove(); resolve(null); document.removeEventListener('keydown', esc); }
        });

        document.body.appendChild(overlay);
    });
}

/**
 * Rename a group — inline edit on the section header label.
 */
function _startRenameGroup(groupName, btnEl) {
    const section = btnEl.closest('.automations-section');
    const labelEl = section?.querySelector('.section-label');
    if (!labelEl) return;

    const input = document.createElement('input');
    input.className = 'section-rename-input';
    input.value = groupName;
    input.onclick = (e) => e.stopPropagation();

    const originalText = labelEl.textContent;
    labelEl.textContent = '';
    labelEl.appendChild(input);
    input.focus();
    input.select();

    const finish = async (save) => {
        const newName = input.value.trim();
        input.removeEventListener('blur', blurHandler);
        if (!save || !newName || newName === groupName) {
            labelEl.textContent = originalText;
            return;
        }

        const ids = [];
        section.querySelectorAll('.automation-card').forEach(card => {
            if (card.dataset.id) ids.push(parseInt(card.dataset.id));
        });

        try {
            const res = await fetch('/api/automations/group', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ automation_ids: ids, group_name: newName })
            });
            const data = await res.json();
            if (data.error) throw new Error(data.error);
            showToast(`Renamed to "${newName}"`, 'success');
            await loadAutomations();
        } catch (err) {
            showToast('Error: ' + err.message, 'error');
            labelEl.textContent = originalText;
        }
    };

    input.addEventListener('keydown', (e) => {
        e.stopPropagation();
        if (e.key === 'Enter') { e.preventDefault(); finish(true); }
        if (e.key === 'Escape') { finish(false); }
    });
    const blurHandler = () => finish(true);
    input.addEventListener('blur', blurHandler);
}

/**
 * Bulk toggle all automations in a group.
 */
async function _bulkToggleGroup(groupName, currentlyAllEnabled) {
    const ids = [];
    document.querySelectorAll(`.automations-section[data-group-name="${groupName}"] .automation-card`).forEach(card => {
        if (card.dataset.id) ids.push(parseInt(card.dataset.id));
    });
    if (ids.length === 0) return;

    const targetEnabled = !currentlyAllEnabled;
    try {
        const res = await fetch('/api/automations/bulk-toggle', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ automation_ids: ids, enabled: targetEnabled })
        });
        const data = await res.json();
        if (data.error) throw new Error(data.error);
        showToast(`${targetEnabled ? 'Enabled' : 'Disabled'} ${data.updated} automations`, 'success');
        await loadAutomations();
    } catch (err) { showToast('Error: ' + err.message, 'error'); }
}

async function loadAutomations() {
    const list = document.getElementById('automations-list');
    const empty = document.getElementById('automations-empty');
    const statsBar = document.getElementById('automations-stats');
    if (!list || !empty) return;
    try {
        const res = await fetch('/api/automations');
        const automations = await res.json();
        if (automations.error) throw new Error(automations.error);
        if (!automations.length) {
            list.innerHTML = ''; empty.style.display = '';
            if (statsBar) statsBar.innerHTML = '';
            return;
        }
        empty.style.display = 'none';
        list.innerHTML = '';

        const systemAutos = automations.filter(a => a.is_system);
        const userAutos = automations.filter(a => !a.is_system);

        if (systemAutos.length) {
            list.appendChild(_buildAutomationSection('auto-section-system', 'System', systemAutos, true, { isProtected: true }));
        }

        // Automation Hub section
        list.appendChild(_buildAutomationHub());

        // User automations — split by group
        const groups = [...new Set(userAutos.filter(a => a.group_name).map(a => a.group_name))].sort();
        const ungrouped = userAutos.filter(a => !a.group_name);
        groups.forEach(g => {
            const groupAutos = userAutos.filter(a => a.group_name === g);
            if (groupAutos.length) {
                list.appendChild(_buildAutomationSection('auto-section-group-' + g.replace(/\W+/g, '_'), '\uD83D\uDCC1 ' + g, groupAutos, true, { groupName: g }));
            }
        });
        if (ungrouped.length) {
            list.appendChild(_buildAutomationSection('auto-section-custom', 'My Automations', ungrouped, true));
        }

        // Stats summary bar
        if (statsBar) {
            const total = automations.length;
            const active = automations.filter(a => a.enabled).length;
            const sys = systemAutos.length;
            const custom = userAutos.length;
            statsBar.innerHTML = `
                <span class="auto-stat"><strong>${active}</strong> Active</span>
                <span class="auto-stat"><strong>${sys}</strong> System</span>
                <span class="auto-stat"><strong>${custom}</strong> Custom</span>
            `;
        }

        // Filter bar — show when 6+ automations
        _initAutoFilterBar(automations);
        // Catch up on current automation progress
        try {
            const progRes = await fetch('/api/automations/progress');
            const progData = await progRes.json();
            if (!progData.error) updateAutomationProgressFromData(progData);
        } catch (e) { }
    } catch (err) {
        list.innerHTML = ''; empty.style.display = '';
        if (statsBar) statsBar.innerHTML = '';
    }
}

// --- Automation Hub ---

function _buildAutomationHub() {
    const section = document.createElement('div');
    section.className = 'automations-section';
    section.id = 'auto-section-hub';
    const collapsed = localStorage.getItem('auto_section_auto-section-hub') === '1';
    if (collapsed) section.classList.add('collapsed');
    const header = document.createElement('div');
    header.className = 'automations-section-header';
    header.innerHTML = `
        <span class="section-chevron">&#9660;</span>
        <span class="section-label">Automation Hub</span>
        <span class="section-count">${AUTO_HUB_GROUPS.length} pipelines · ${AUTO_HUB_RECIPES.length} recipes</span>
        <span class="section-line"></span>
    `;
    header.onclick = () => {
        section.classList.toggle('collapsed');
        localStorage.setItem('auto_section_auto-section-hub', section.classList.contains('collapsed') ? '1' : '0');
    };
    const body = document.createElement('div');
    body.className = 'automations-section-body';

    const activeTab = localStorage.getItem('auto_hub_tab') || 'pipelines';
    const tabs = [
        { id: 'pipelines', label: 'Pipelines' },
        { id: 'recipes', label: 'Singles' },
        { id: 'guides', label: 'Quick Start' },
        { id: 'tips', label: 'Tips' },
        { id: 'reference', label: 'Reference' },
    ];

    const tabBar = document.createElement('div');
    tabBar.className = 'auto-hub-tabs';
    tabs.forEach(t => {
        const btn = document.createElement('button');
        btn.className = 'auto-hub-tab' + (t.id === activeTab ? ' active' : '');
        btn.textContent = t.label;
        btn.dataset.tab = t.id;
        btn.onclick = (e) => { e.stopPropagation(); _switchHubTab(t.id, body); };
        tabBar.appendChild(btn);
    });
    body.appendChild(tabBar);

    // Build all tab contents
    const pipelinesPane = _buildHubPipelines();
    pipelinesPane.id = 'auto-hub-pane-pipelines';
    pipelinesPane.className = 'auto-hub-tab-content' + (activeTab === 'pipelines' ? ' active' : '');
    body.appendChild(pipelinesPane);

    const recipesPane = _buildHubRecipes();
    recipesPane.id = 'auto-hub-pane-recipes';
    recipesPane.className = 'auto-hub-tab-content' + (activeTab === 'recipes' ? ' active' : '');
    body.appendChild(recipesPane);

    const guidesPane = _buildHubGuides();
    guidesPane.id = 'auto-hub-pane-guides';
    guidesPane.className = 'auto-hub-tab-content' + (activeTab === 'guides' ? ' active' : '');
    body.appendChild(guidesPane);

    const tipsPane = _buildHubTips();
    tipsPane.id = 'auto-hub-pane-tips';
    tipsPane.className = 'auto-hub-tab-content' + (activeTab === 'tips' ? ' active' : '');
    body.appendChild(tipsPane);

    const refPane = _buildHubReference();
    refPane.id = 'auto-hub-pane-reference';
    refPane.className = 'auto-hub-tab-content' + (activeTab === 'reference' ? ' active' : '');
    body.appendChild(refPane);

    section.appendChild(header);
    section.appendChild(body);
    return section;
}

function _switchHubTab(tabId, bodyEl) {
    const container = bodyEl || document.querySelector('#auto-section-hub .automations-section-body');
    if (!container) return;
    container.querySelectorAll('.auto-hub-tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tabId));
    container.querySelectorAll('.auto-hub-tab-content').forEach(p => p.classList.toggle('active', p.id === 'auto-hub-pane-' + tabId));
    localStorage.setItem('auto_hub_tab', tabId);
}

function _buildHubPipelines() {
    const pane = document.createElement('div');

    const intro = document.createElement('div');
    intro.className = 'auto-hub-pipeline-intro';
    intro.innerHTML = 'One-click deployment — each pipeline creates multiple linked automations that work together.';
    pane.appendChild(intro);

    const grid = document.createElement('div');
    grid.className = 'auto-hub-pipeline-grid';

    AUTO_HUB_GROUPS.forEach(group => {
        const card = document.createElement('div');
        card.className = 'auto-hub-pipeline-card';
        card.style.setProperty('--pipeline-color', group.color);

        // Pipeline flow visualization
        const stepsHtml = group.steps.map((step, i) => {
            const nodeClass = step.type === 'notify' ? 'pipeline-node-notify' : 'pipeline-node-action';
            return (i > 0 ? '<span class="pipeline-connector"></span>' : '') +
                `<div class="pipeline-node ${nodeClass}">
                    <span class="pipeline-node-icon">${step.icon}</span>
                    <span class="pipeline-node-label">${step.label}</span>
                </div>`;
        }).join('');

        card.innerHTML = `
            <div class="pipeline-card-top">
                <span class="pipeline-card-icon">${group.icon}</span>
                <div class="pipeline-card-title-row">
                    <div class="pipeline-card-name">${group.name}</div>
                    <span class="pipeline-card-badge">${group.badge}</span>
                </div>
            </div>
            <div class="pipeline-card-desc">${group.desc}</div>
            <div class="pipeline-flow">${stepsHtml}</div>
            <div class="pipeline-card-footer">
                <button class="pipeline-deploy-btn" onclick="event.stopPropagation(); deployHubGroup('${group.id}')">Deploy Pipeline</button>
            </div>
        `;

        card.addEventListener('click', (e) => {
            if (e.target.closest('.pipeline-deploy-btn')) return;
            showPipelineDetail(group.id);
        });

        grid.appendChild(card);
    });

    pane.appendChild(grid);
    return pane;
}

function showPipelineDetail(groupId) {
    const group = AUTO_HUB_GROUPS.find(g => g.id === groupId);
    if (!group) return;

    // Build automation detail list
    const autoDetails = group.automations.map((auto, i) => {
        const triggerLabel = _autoFormatTrigger(auto.trigger_type, auto.trigger_config);
        const actionLabel = _autoFormatAction(auto.action_type);
        const thenLabels = auto.then_actions.map(t => {
            if (t.type === 'fire_signal') return `⚡ Signal: ${t.config.signal_name}`;
            return _autoFormatNotify(t.type);
        });
        if (auto.needs_notify) thenLabels.push('🔔 Your notification');

        return `
            <div class="pipeline-detail-auto" style="--step-color: ${group.color}">
                <div class="pipeline-detail-step-num">${i + 1}</div>
                <div class="pipeline-detail-step-body">
                    <div class="pipeline-detail-step-name">${auto.name}</div>
                    <div class="pipeline-detail-step-flow">
                        <span class="pipeline-detail-tag when">WHEN</span>
                        <span class="pipeline-detail-tag-value">${_esc(triggerLabel)}</span>
                        <span class="pipeline-detail-tag do">DO</span>
                        <span class="pipeline-detail-tag-value">${_esc(actionLabel)}</span>
                        ${thenLabels.length ? `<span class="pipeline-detail-tag then">THEN</span><span class="pipeline-detail-tag-value">${thenLabels.map(t => _esc(t)).join(', ')}</span>` : ''}
                    </div>
                </div>
            </div>`;
    }).join('');

    // Build flow diagram
    const flowHtml = group.steps.map((step, i) => {
        const nodeClass = step.type === 'notify' ? 'pipeline-node-notify' : 'pipeline-node-action';
        return (i > 0 ? '<span class="pipeline-connector"></span>' : '') +
            `<div class="pipeline-node ${nodeClass}">
                <span class="pipeline-node-icon">${step.icon}</span>
                <span class="pipeline-node-label">${step.label}</span>
            </div>`;
    }).join('');

    const overlay = document.createElement('div');
    overlay.className = 'pipeline-detail-overlay';
    overlay.innerHTML = `
        <div class="pipeline-detail-modal" style="--pipeline-color: ${group.color}">
            <button class="pipeline-detail-close" onclick="this.closest('.pipeline-detail-overlay').remove()">&times;</button>
            <div class="pipeline-detail-header">
                <span class="pipeline-detail-icon">${group.icon}</span>
                <div>
                    <div class="pipeline-detail-title">${group.name}</div>
                    <div class="pipeline-detail-desc">${group.desc}</div>
                </div>
            </div>
            <div class="pipeline-detail-flow" style="--pipeline-color: ${group.color}">${flowHtml}</div>
            <div class="pipeline-detail-section-title">How It Works</div>
            <div class="pipeline-detail-section-desc">This pipeline deploys ${group.automations.length} automations${group.automations.some(a => a.then_actions.some(t => t.type === 'fire_signal')) ? ' linked by signals — each step triggers the next automatically' : ' running on independent schedules'}.</div>
            <div class="pipeline-detail-autos">${autoDetails}</div>
            <button class="pipeline-deploy-btn" style="--pipeline-color: ${group.color}; margin-top: 8px;" onclick="this.closest('.pipeline-detail-overlay').remove(); deployHubGroup('${group.id}')">Deploy Pipeline</button>
        </div>
    `;

    overlay.addEventListener('click', (e) => {
        if (e.target === overlay) overlay.remove();
    });

    document.body.appendChild(overlay);
}

function _buildHubRecipes() {
    const pane = document.createElement('div');
    const categories = ['All', 'Sync', 'Discovery', 'Maintenance', 'Alerts', 'Chains'];
    const difficulties = ['All', 'Beginner', 'Intermediate', 'Advanced'];

    let activeCat = 'All', activeDiff = 'All';

    // Category filters
    const catFilters = document.createElement('div');
    catFilters.className = 'auto-hub-filters';
    categories.forEach(c => {
        const pill = document.createElement('button');
        pill.className = 'auto-hub-filter-pill' + (c === 'All' ? ' active' : '');
        pill.textContent = c;
        pill.dataset.filter = c;
        pill.dataset.filterType = 'category';
        pill.onclick = () => {
            activeCat = c;
            catFilters.querySelectorAll('.auto-hub-filter-pill').forEach(p => p.classList.toggle('active', p.dataset.filter === c));
            filterRecipes();
        };
        catFilters.appendChild(pill);
    });
    pane.appendChild(catFilters);

    // Difficulty filters
    const diffFilters = document.createElement('div');
    diffFilters.className = 'auto-hub-filters';
    difficulties.forEach(d => {
        const pill = document.createElement('button');
        pill.className = 'auto-hub-filter-pill' + (d === 'All' ? ' active' : '');
        pill.textContent = d;
        pill.dataset.filter = d;
        pill.dataset.filterType = 'difficulty';
        pill.onclick = () => {
            activeDiff = d;
            diffFilters.querySelectorAll('.auto-hub-filter-pill').forEach(p => p.classList.toggle('active', p.dataset.filter === d));
            filterRecipes();
        };
        diffFilters.appendChild(pill);
    });
    pane.appendChild(diffFilters);

    const grid = document.createElement('div');
    grid.className = 'auto-hub-recipes-grid';

    AUTO_HUB_RECIPES.forEach(r => {
        const card = document.createElement('div');
        card.className = 'auto-hub-recipe-card';
        card.dataset.category = r.category;
        card.dataset.difficulty = r.difficulty;

        const trigLabel = _autoFormatTrigger(r.when.type, r.when.config);
        const actLabel = _autoFormatAction(r.do.type);

        let chainHTML = '';
        if (r.chain) {
            chainHTML = '<div class="auto-hub-recipe-chain">' + r.chain.map((step, i) => {
                let cls = 'flow-action';
                if (i === 0) cls = 'flow-trigger';
                else if (step.startsWith('\u26A1')) cls = 'flow-notify';
                return (i > 0 ? '<span class="flow-arrow">&rarr;</span>' : '') +
                    `<span class="${cls}">${_esc(step)}</span>`;
            }).join('') + '</div>';
        } else {
            chainHTML = `<div class="auto-hub-recipe-chain">
                <span class="flow-trigger">${_esc(trigLabel)}</span>
                <span class="flow-arrow">&rarr;</span>
                <span class="flow-action">${_esc(actLabel)}</span>
                ${r.then.length ? r.then.map(th => `<span class="flow-arrow">&rarr;</span><span class="flow-notify">${_esc(_autoFormatNotify(th.type))}</span>`).join('') : ''}
            </div>`;
        }

        card.innerHTML = `
            <div class="auto-hub-recipe-header">
                <div class="auto-hub-recipe-icon">${r.icon}</div>
                <div class="auto-hub-recipe-name">${_esc(r.name)}</div>
                <span class="auto-hub-badge ${r.difficulty}">${_esc(r.difficulty)}</span>
            </div>
            <div class="auto-hub-recipe-desc">${_esc(r.desc)}</div>
            ${chainHTML}
            ${r.note ? `<div class="auto-hub-recipe-note">${_esc(r.note)}</div>` : ''}
            <button class="auto-hub-recipe-use" onclick="event.stopPropagation(); useHubRecipe('${r.id}')">Use This</button>
        `;
        card.onclick = () => useHubRecipe(r.id);
        grid.appendChild(card);
    });
    pane.appendChild(grid);

    function filterRecipes() {
        grid.querySelectorAll('.auto-hub-recipe-card').forEach(card => {
            const catMatch = activeCat === 'All' || card.dataset.category === activeCat;
            const diffMatch = activeDiff === 'All' || card.dataset.difficulty === activeDiff.toLowerCase();
            card.style.display = (catMatch && diffMatch) ? '' : 'none';
        });
    }

    return pane;
}

function _buildHubGuides() {
    const pane = document.createElement('div');

    const callout = document.createElement('div');
    callout.className = 'auto-hub-callout';
    callout.innerHTML = '<span class="auto-hub-callout-icon">\uD83D\uDCA1</span><span>Click any guide to expand step-by-step instructions. Related recipes let you jump straight to a pre-filled template.</span>';
    pane.appendChild(callout);

    AUTO_HUB_GUIDES.forEach(g => {
        const card = document.createElement('div');
        card.className = 'auto-hub-guide-card';

        const headerEl = document.createElement('div');
        headerEl.className = 'auto-hub-guide-header';
        headerEl.innerHTML = `
            <span class="auto-hub-guide-icon">${g.icon}</span>
            <span class="auto-hub-guide-title">${_esc(g.title)}</span>
            <span class="auto-hub-badge ${g.difficulty}">${_esc(g.difficulty)}</span>
            <span class="auto-hub-guide-chevron">&#9660;</span>
        `;
        headerEl.onclick = () => card.classList.toggle('expanded');
        card.appendChild(headerEl);

        const bodyEl = document.createElement('div');
        bodyEl.className = 'auto-hub-guide-body';
        bodyEl.innerHTML = `
            <div class="auto-hub-guide-subtitle">${_esc(g.subtitle)}</div>
            <ol class="auto-hub-steps">${g.steps.map(s => `<li>${s}</li>`).join('')}</ol>
            ${g.relatedRecipes.length ? `
                <div class="auto-hub-guide-related">
                    <span class="auto-hub-guide-related-label">Related:</span>
                    ${g.relatedRecipes.map(rId => {
            const recipe = AUTO_HUB_RECIPES.find(r => r.id === rId);
            return recipe ? `<button class="auto-hub-guide-related-link" onclick="event.stopPropagation(); useHubRecipe('${rId}')">${recipe.icon} ${_esc(recipe.name)}</button>` : '';
        }).join('')}
                </div>
            ` : ''}
        `;
        card.appendChild(bodyEl);
        pane.appendChild(card);
    });

    return pane;
}

function _buildHubTips() {
    const pane = document.createElement('div');

    const callout = document.createElement('div');
    callout.className = 'auto-hub-callout';
    callout.innerHTML = '<span class="auto-hub-callout-icon">\u2728</span><span>Power-user tips to get the most out of your automations.</span>';
    pane.appendChild(callout);

    const grid = document.createElement('div');
    grid.className = 'auto-hub-tips-grid';
    AUTO_HUB_TIPS.forEach(t => {
        const card = document.createElement('div');
        card.className = 'auto-hub-tip-card';
        card.innerHTML = `
            <div class="auto-hub-tip-header">
                <span class="auto-hub-tip-icon">${t.icon}</span>
                <span class="auto-hub-tip-title">${_esc(t.title)}</span>
                <span class="auto-hub-tip-tag">${_esc(t.tag)}</span>
            </div>
            <div class="auto-hub-tip-body">${t.body}</div>
        `;
        grid.appendChild(card);
    });
    pane.appendChild(grid);
    return pane;
}

function _buildHubReference() {
    const pane = document.createElement('div');
    const sections = [
        { label: 'Triggers (WHEN)', data: AUTO_HUB_REFERENCE.triggers },
        { label: 'Actions (DO)', data: AUTO_HUB_REFERENCE.actions },
        { label: 'Then Actions (THEN)', data: AUTO_HUB_REFERENCE.thenActions },
    ];

    sections.forEach(sec => {
        const totalItems = sec.data.reduce((n, g) => n + g.items.length, 0);
        const group = document.createElement('div');
        group.className = 'auto-hub-ref-group';

        const header = document.createElement('div');
        header.className = 'auto-hub-ref-group-header';
        header.innerHTML = `
            <span class="auto-hub-ref-group-label">${_esc(sec.label)}</span>
            <span class="auto-hub-ref-group-count">${totalItems}</span>
            <span class="auto-hub-ref-chevron">&#9660;</span>
        `;
        header.onclick = () => group.classList.toggle('expanded');
        group.appendChild(header);

        const body = document.createElement('div');
        body.className = 'auto-hub-ref-body';
        sec.data.forEach(sub => {
            body.innerHTML += `<div class="auto-hub-ref-subheader">${_esc(sub.group)}</div>`;
            let tableHTML = '<table class="auto-hub-table"><thead><tr><th>Type</th><th>Description</th></tr></thead><tbody>';
            sub.items.forEach(item => {
                tableHTML += `<tr><td>${_esc(item.label)}</td><td>${_esc(item.desc)}</td></tr>`;
            });
            tableHTML += '</tbody></table>';
            body.innerHTML += tableHTML;
        });
        group.appendChild(body);
        pane.appendChild(group);
    });
    return pane;
}

async function useHubRecipe(recipeId) {
    const t = AUTO_HUB_RECIPES.find(r => r.id === recipeId);
    if (!t) return;
    await showAutomationBuilder();
    document.getElementById('builder-name').value = t.name;
    _autoBuilder.when = { type: t.when.type, config: JSON.parse(JSON.stringify(t.when.config)) };
    _autoBuilder.do = { type: t.do.type, config: JSON.parse(JSON.stringify(t.do.config)) };
    _autoBuilder.then = t.then.map(th => ({ type: th.type, config: JSON.parse(JSON.stringify(th.config)) }));
    _renderBuilderSidebar();
    _renderBuilderCanvas();
    if (t.note) {
        showToast(t.note, 'info');
    }
}

async function deployHubGroup(groupId) {
    const group = AUTO_HUB_GROUPS.find(g => g.id === groupId);
    if (!group) return;

    // Check if any automations need notifications — prompt for config
    const needsNotify = group.automations.some(a => a.needs_notify);
    let notifyConfig = null;

    if (needsNotify) {
        notifyConfig = await _promptNotifyConfig(group.name);
        if (notifyConfig === null) return; // User cancelled
        if (notifyConfig === false) notifyConfig = null; // Skip notifications, still deploy
    }

    // Deploy all automations in the group
    let created = 0, failed = 0;
    for (const auto of group.automations) {
        try {
            const payload = {
                name: auto.name,
                trigger_type: auto.trigger_type,
                trigger_config: auto.trigger_config,
                action_type: auto.action_type,
                action_config: auto.action_config,
                then_actions: [...auto.then_actions],
                group_name: auto.group_name,
                enabled: true,
            };

            // Inject notification config for automations that need it
            if (auto.needs_notify && notifyConfig) {
                payload.then_actions.push(notifyConfig);
            }

            const response = await fetch('/api/automations', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });

            if (response.ok) {
                created++;
            } else {
                const err = await response.json();
                console.error(`Failed to create "${auto.name}":`, err);
                failed++;
            }
        } catch (e) {
            console.error(`Error creating "${auto.name}":`, e);
            failed++;
        }
    }

    if (created > 0) {
        showToast(`Deployed "${group.name}" — ${created} automation${created > 1 ? 's' : ''} created${failed ? `, ${failed} failed` : ''}`, 'success');
        loadAutomations();
    } else {
        showToast(`Failed to deploy "${group.name}"`, 'error');
    }
}

function _promptNotifyConfig(groupName) {
    return new Promise(resolve => {
        const overlay = document.createElement('div');
        overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.7);z-index:10000;display:flex;align-items:center;justify-content:center;';

        overlay.innerHTML = `
            <div style="background:var(--bg-secondary, #1e1e2e);border:1px solid rgba(255,255,255,0.1);border-radius:14px;padding:28px;max-width:420px;width:90%;color:var(--text-primary, #fff);font-family:inherit;">
                <h3 style="margin:0 0 6px;font-size:1.1em;">Configure Notifications</h3>
                <p style="margin:0 0 18px;font-size:0.85em;opacity:0.5;">${groupName} includes notification steps. Choose how to get notified.</p>
                <div style="display:flex;flex-direction:column;gap:10px;margin-bottom:18px;">
                    <label style="font-size:0.85em;opacity:0.7;">Notification Type</label>
                    <select id="deploy-notify-type" style="padding:9px 12px;background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.08);border-radius:8px;color:#fff;font-size:0.9em;">
                        <option value="discord_webhook">Discord Webhook</option>
                        <option value="telegram">Telegram</option>
                        <option value="pushbullet">Pushbullet</option>
                        <option value="none">Skip Notifications</option>
                    </select>
                    <div id="deploy-notify-fields"></div>
                </div>
                <div style="display:flex;gap:10px;justify-content:flex-end;">
                    <button id="deploy-notify-cancel" style="padding:8px 20px;border:1px solid rgba(255,255,255,0.1);border-radius:8px;background:transparent;color:rgba(255,255,255,0.7);cursor:pointer;font-size:0.88em;">Cancel</button>
                    <button id="deploy-notify-confirm" style="padding:8px 20px;border:none;border-radius:8px;background:var(--accent-color,#1db954);color:#fff;cursor:pointer;font-size:0.88em;font-weight:600;">Deploy</button>
                </div>
            </div>
        `;

        document.body.appendChild(overlay);

        const typeSelect = overlay.querySelector('#deploy-notify-type');
        const fieldsDiv = overlay.querySelector('#deploy-notify-fields');

        function updateFields() {
            const type = typeSelect.value;
            if (type === 'discord_webhook') {
                fieldsDiv.innerHTML = '<input id="deploy-notify-url" type="text" placeholder="Discord Webhook URL" style="width:100%;padding:9px 12px;background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.08);border-radius:8px;color:#fff;font-size:0.88em;margin-top:6px;box-sizing:border-box;">';
            } else if (type === 'telegram') {
                fieldsDiv.innerHTML = '<input id="deploy-notify-token" type="text" placeholder="Bot Token" style="width:100%;padding:9px 12px;background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.08);border-radius:8px;color:#fff;font-size:0.88em;margin-top:6px;box-sizing:border-box;"><input id="deploy-notify-chat" type="text" placeholder="Chat ID" style="width:100%;padding:9px 12px;background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.08);border-radius:8px;color:#fff;font-size:0.88em;margin-top:6px;box-sizing:border-box;">';
            } else if (type === 'pushbullet') {
                fieldsDiv.innerHTML = '<input id="deploy-notify-token" type="text" placeholder="Access Token" style="width:100%;padding:9px 12px;background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.08);border-radius:8px;color:#fff;font-size:0.88em;margin-top:6px;box-sizing:border-box;">';
            } else {
                fieldsDiv.innerHTML = '';
            }
        }
        typeSelect.addEventListener('change', updateFields);
        updateFields();

        overlay.querySelector('#deploy-notify-cancel').addEventListener('click', () => {
            document.body.removeChild(overlay);
            resolve(null);
        });

        overlay.querySelector('#deploy-notify-confirm').addEventListener('click', () => {
            const type = typeSelect.value;
            let config = {};
            if (type === 'discord_webhook') {
                config = { webhook_url: (overlay.querySelector('#deploy-notify-url')?.value || '').trim() };
            } else if (type === 'telegram') {
                config = { bot_token: (overlay.querySelector('#deploy-notify-token')?.value || '').trim(), chat_id: (overlay.querySelector('#deploy-notify-chat')?.value || '').trim() };
            } else if (type === 'pushbullet') {
                config = { access_token: (overlay.querySelector('#deploy-notify-token')?.value || '').trim() };
            } else {
                document.body.removeChild(overlay);
                resolve(false); // Skip notifications but still deploy
                return;
            }
            document.body.removeChild(overlay);
            resolve({ type, config });
        });

        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) { document.body.removeChild(overlay); resolve(null); }
        });
    });
}

// --- Filter Bar ---
function _initAutoFilterBar(automations) {
    const bar = document.getElementById('auto-filter-bar');
    if (!bar) return;
    if (automations.length < 7) { bar.style.display = 'none'; return; }
    bar.style.display = '';

    // Populate trigger dropdown
    const trigSel = document.getElementById('auto-filter-trigger');
    const actSel = document.getElementById('auto-filter-action');
    const trigTypes = [...new Set(automations.map(a => a.trigger_type))].sort();
    const actTypes = [...new Set(automations.map(a => a.action_type))].sort();
    const prevTrig = trigSel.value;
    const prevAct = actSel.value;
    trigSel.innerHTML = '<option value="">All Triggers</option>' + trigTypes.map(t =>
        `<option value="${_escAttr(t)}">${_esc(_autoFormatTrigger(t, {}))}</option>`).join('');
    actSel.innerHTML = '<option value="">All Actions</option>' + actTypes.map(t =>
        `<option value="${_escAttr(t)}">${_esc(_autoFormatAction(t))}</option>`).join('');
    trigSel.value = prevTrig;
    actSel.value = prevAct;

    // Bind events (use a flag to avoid double-binding)
    if (!bar.dataset.bound) {
        bar.dataset.bound = '1';
        document.getElementById('auto-filter-search').addEventListener('input', _filterAutomations);
        trigSel.addEventListener('change', _filterAutomations);
        actSel.addEventListener('change', _filterAutomations);
    }
    _filterAutomations();
}

function _filterAutomations() {
    const q = (document.getElementById('auto-filter-search').value || '').toLowerCase().trim();
    const trigFilter = document.getElementById('auto-filter-trigger').value;
    const actFilter = document.getElementById('auto-filter-action').value;
    const cards = document.querySelectorAll('#automations-list .automation-card');
    let visible = 0;
    cards.forEach(card => {
        const name = (card.querySelector('.automation-name')?.textContent || '').toLowerCase();
        const trig = card.querySelector('.flow-trigger')?.textContent || '';
        const act = card.querySelector('.flow-action')?.textContent || '';
        // Match search text against name, trigger label, action label
        const matchQ = !q || name.includes(q) || trig.toLowerCase().includes(q) || act.toLowerCase().includes(q);
        // Match trigger/action type filters using data attributes
        const matchTrig = !trigFilter || card.dataset.triggerType === trigFilter;
        const matchAct = !actFilter || card.dataset.actionType === actFilter;
        const show = matchQ && matchTrig && matchAct;
        card.style.display = show ? '' : 'none';
        if (show) visible++;
    });
    const countEl = document.getElementById('auto-filter-count');
    if (countEl) {
        countEl.textContent = (q || trigFilter || actFilter) ? `${visible} of ${cards.length}` : '';
    }
}

// --- Group Dropdown ---
let _activeGroupDropdown = null;

function _showGroupDropdown(event, autoId, currentGroup) {
    // Close any existing dropdown
    _closeGroupDropdown();

    const btn = event.currentTarget;
    const card = btn.closest('.automation-card');
    if (!card) return;

    // Collect all existing group names from visible cards
    const allGroups = new Set();
    document.querySelectorAll('#automations-list .automation-card .automation-group-btn[data-group]').forEach(b => {
        const g = b.dataset.group;
        if (g) allGroups.add(g);
    });

    const dropdown = document.createElement('div');
    dropdown.className = 'auto-group-dropdown';

    let html = '';
    if (currentGroup) {
        html += `<div class="auto-group-option ungroup" onclick="_assignGroup(${autoId}, null)">Remove from group</div>`;
        html += '<div class="auto-group-divider"></div>';
    }
    allGroups.forEach(g => {
        const isActive = g === currentGroup;
        html += `<div class="auto-group-option${isActive ? ' active' : ''}" onclick="_assignGroup(${autoId}, '${_escAttr(g)}')">${_esc(g)}</div>`;
    });
    if (allGroups.size) html += '<div class="auto-group-divider"></div>';
    html += `<input class="auto-group-input" placeholder="New group name..." onkeydown="if(event.key==='Enter'){_assignGroup(${autoId}, this.value.trim()); event.preventDefault();}">`;

    dropdown.innerHTML = html;

    // Position dropdown on document.body to avoid overflow:hidden clipping
    const rect = btn.getBoundingClientRect();
    dropdown.style.position = 'fixed';
    dropdown.style.right = (window.innerWidth - rect.right) + 'px';
    dropdown.style.left = 'auto';
    document.body.appendChild(dropdown);
    _activeGroupDropdown = dropdown;

    // Open upward if not enough room below
    const dropdownHeight = dropdown.offsetHeight;
    if (rect.bottom + 4 + dropdownHeight > window.innerHeight && rect.top - 4 - dropdownHeight > 0) {
        dropdown.style.top = (rect.top - 4 - dropdownHeight) + 'px';
    } else {
        dropdown.style.top = (rect.bottom + 4) + 'px';
    }

    // Focus the input
    setTimeout(() => dropdown.querySelector('.auto-group-input')?.focus(), 50);

    // Close on outside click
    const handler = (e) => {
        if (!dropdown.contains(e.target) && e.target !== btn) {
            _closeGroupDropdown();
            document.removeEventListener('click', handler, true);
        }
    };
    setTimeout(() => document.addEventListener('click', handler, true), 10);
}

function _closeGroupDropdown() {
    if (_activeGroupDropdown) {
        _activeGroupDropdown.remove();
        _activeGroupDropdown = null;
    }
}

async function _assignGroup(autoId, groupName) {
    _closeGroupDropdown();
    try {
        const res = await fetch('/api/automations/' + autoId, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ group_name: groupName || null })
        });
        const data = await res.json();
        if (data.error) throw new Error(data.error);
        showToast(groupName ? `Moved to "${groupName}"` : 'Removed from group', 'success');
        await loadAutomations();
    } catch (err) { showToast('Error: ' + err.message, 'error'); }
}

function renderAutomationCard(a) {
    const card = document.createElement('div');
    card.className = 'automation-card' + (a.enabled ? '' : ' disabled') + (a.is_system ? ' system' : '');
    card.dataset.id = a.id;
    card.dataset.triggerType = a.trigger_type || '';
    card.dataset.actionType = a.action_type || '';

    // Drag-and-drop (non-system only)
    if (!a.is_system) {
        card.draggable = true;
        card.addEventListener('dragstart', (e) => {
            _autoDragState = { id: a.id, groupName: a.group_name || null };
            e.dataTransfer.setData('text/plain', String(a.id));
            e.dataTransfer.effectAllowed = 'move';
            card.classList.add('dragging');
            // Dim protected sections during drag
            document.querySelectorAll('.section-protected').forEach(s => s.classList.add('no-drop'));
        });
        card.addEventListener('dragend', () => {
            card.classList.remove('dragging');
            _autoDragState = null;
            _autoDragEnterCount = 0;
            document.querySelectorAll('.drop-target').forEach(el => el.classList.remove('drop-target'));
            document.querySelectorAll('.no-drop').forEach(el => el.classList.remove('no-drop'));
            if (_autoDragExpandTimer) { clearTimeout(_autoDragExpandTimer); _autoDragExpandTimer = null; }
        });
    }
    const tIcon = _autoIcons[a.trigger_type] || '\u2699\uFE0F';
    const aIcon = _autoIcons[a.action_type] || '\u2699\uFE0F';
    const tl = tIcon + ' ' + _autoFormatTrigger(a.trigger_type, a.trigger_config);
    const al = aIcon + ' ' + _autoFormatAction(a.action_type);
    const thenItems = a.then_actions || [];
    const actionDelay = a.action_config && a.action_config.delay ? a.action_config.delay : 0;
    const metaParts = [];
    if (a.last_run) metaParts.push('Last: ' + _autoTimeAgo(a.last_run));
    const _timerTriggers = ['schedule', 'daily_time', 'weekly_time'];
    if (a.next_run && a.enabled && _timerTriggers.includes(a.trigger_type)) metaParts.push('<span class="auto-next-run" data-next="' + _escAttr(a.next_run) + '">Next: ' + _autoTimeUntil(a.next_run) + '</span>');
    if (!_timerTriggers.includes(a.trigger_type) && a.enabled) metaParts.push('Listening');
    if (a.run_count) metaParts.push('<span class="auto-runs-link" onclick="event.stopPropagation(); showAutomationHistory(' + a.id + ', \'' + _escAttr(a.name) + '\', \'' + _escAttr(a.action_type || '') + '\')" title="View run history">Runs: ' + a.run_count + '</span>');
    if (a.last_error) metaParts.push('Error: ' + _esc(a.last_error));

    const dupeBtn = a.is_system ? '' :
        `<button class="automation-dupe-btn" title="Duplicate" onclick="event.stopPropagation(); duplicateAutomation(${a.id})">&#128203;</button>`;
    const groupBtn = a.is_system ? '' :
        `<button class="automation-group-btn${a.group_name ? ' grouped' : ''}" data-group="${_escAttr(a.group_name || '')}" title="${a.group_name ? 'Group: ' + _escAttr(a.group_name) : 'Assign group'}" onclick="event.stopPropagation(); _showGroupDropdown(event, ${a.id}, ${a.group_name ? "'" + _escAttr(a.group_name) + "'" : 'null'})">&#128193;</button>`;
    const deleteBtn = a.is_system ? '' :
        `<button class="automation-delete-btn" title="Delete" onclick="event.stopPropagation(); deleteAutomation(${a.id}, '${_escAttr(a.name)}')">&#128465;</button>`;

    card.innerHTML = `
        <div class="automation-status ${a.enabled ? 'enabled' : 'disabled'}"></div>
        <div class="automation-info">
            <div class="automation-name">${_esc(a.name)}</div>
            <div class="automation-flow">
                <span class="flow-trigger">${_esc(tl)}</span>
                <span class="flow-arrow">&rarr;</span>
                ${actionDelay ? `<span class="flow-delay">\u23F3 ${actionDelay}m</span><span class="flow-arrow">&rarr;</span>` : ''}
                <span class="flow-action">${_esc(al)}</span>
                ${thenItems.length ? thenItems.map(t => `<span class="flow-arrow">&rarr;</span><span class="flow-notify">${_esc(_autoFormatNotify(t.type))}</span>`).join('') : ''}
            </div>
            <div class="automation-meta">${metaParts.join(' &middot; ')}</div>
        </div>
        <div class="automation-actions">
            <button class="automation-run-btn" title="Run now" onclick="event.stopPropagation(); runAutomation(${a.id})">&#9654;</button>
            <label class="automation-toggle" onclick="event.stopPropagation();">
                <input type="checkbox" ${a.enabled ? 'checked' : ''} onchange="toggleAutomation(${a.id})">
                <span class="toggle-slider"></span>
            </label>
            <button class="automation-edit-btn" title="Edit" onclick="event.stopPropagation(); showAutomationBuilder(${a.id})">&#9881;</button>
            ${dupeBtn}
            ${groupBtn}
            ${deleteBtn}
        </div>
    `;
    return card;
}

function _autoFormatTrigger(type, config) {
    if (type === 'schedule' && config) return 'Every ' + (config.interval || 1) + ' ' + (config.unit || 'hours');
    if (type === 'daily_time' && config) return 'Daily at ' + (config.time || '00:00');
    if (type === 'weekly_time' && config) {
        const days = (config.days || []).map(d => d.charAt(0).toUpperCase() + d.slice(1)).join(', ');
        return (days || 'Every day') + ' at ' + (config.time || '00:00');
    }
    if (type === 'signal_received' && config) {
        const sig = config.signal_name || 'unknown';
        return 'Signal: ' + sig;
    }
    const labels = {
        app_started: 'App Started', track_downloaded: 'Track Downloaded', batch_complete: 'Batch Complete',
        watchlist_new_release: 'New Release Found', playlist_synced: 'Playlist Synced',
        playlist_changed: 'Playlist Changed', discovery_completed: 'Discovery Complete',
        wishlist_processing_completed: 'Wishlist Processed', watchlist_scan_completed: 'Watchlist Scan Done',
        database_update_completed: 'Database Updated', download_failed: 'Download Failed',
        download_quarantined: 'File Quarantined', wishlist_item_added: 'Wishlist Item Added',
        watchlist_artist_added: 'Artist Watched', watchlist_artist_removed: 'Artist Unwatched',
        import_completed: 'Import Complete', mirrored_playlist_created: 'Playlist Mirrored',
        quality_scan_completed: 'Quality Scan Done', duplicate_scan_completed: 'Duplicate Scan Done',
        library_scan_completed: 'Library Scan Done', signal_received: 'Signal Received'
    };
    let label = labels[type] || type || 'Unknown';
    if (config && config.conditions && config.conditions.length) {
        const first = config.conditions[0];
        label += ' (' + first.field + ' ' + first.operator + ' "' + first.value + '"' +
            (config.conditions.length > 1 ? ' +' + (config.conditions.length - 1) + ' more' : '') + ')';
    }
    return label;
}
function _autoFormatAction(type) {
    const labels = {
        process_wishlist: 'Process Wishlist', scan_watchlist: 'Scan Watchlist',
        scan_library: 'Scan Library', refresh_mirrored: 'Refresh Mirrored',
        sync_playlist: 'Sync Playlist', discover_playlist: 'Discover Playlist',
        notify_only: 'Notify Only',
        start_database_update: 'Update Database', run_duplicate_cleaner: 'Run Duplicate Cleaner',
        clear_quarantine: 'Clear Quarantine', cleanup_wishlist: 'Clean Up Wishlist',
        update_discovery_pool: 'Update Discovery', start_quality_scan: 'Run Quality Scan',
        backup_database: 'Backup Database',
        refresh_beatport_cache: 'Refresh Beatport Cache', clean_search_history: 'Clean Search History',
        clean_completed_downloads: 'Clean Completed Downloads',
        full_cleanup: 'Full Cleanup',
        playlist_pipeline: 'Playlist Pipeline'
    };
    return labels[type] || type || 'Unknown';
}
function _autoFormatNotify(type) {
    if (type === 'discord_webhook') return 'Discord';
    if (type === 'pushbullet') return 'Pushbullet';
    if (type === 'telegram') return 'Telegram';
    if (type === 'fire_signal') return '\u26A1 Signal';
    if (type === 'run_script') return '\uD83D\uDCBB Script';
    return type || '';
}
function _autoParseUTC(ts) {
    // If timestamp already has timezone info (+00:00 or Z), parse as-is; otherwise append Z to treat as UTC
    if (/[Zz]$/.test(ts) || /[+-]\d{2}:\d{2}$/.test(ts)) return new Date(ts).getTime();
    return new Date(ts + 'Z').getTime();
}
function _autoTimeAgo(ts) {
    if (!ts) return 'Never';
    const d = (Date.now() - _autoParseUTC(ts)) / 1000;
    if (d < 60) return 'just now'; if (d < 3600) return Math.floor(d / 60) + 'm ago';
    if (d < 86400) return Math.floor(d / 3600) + 'h ago'; return Math.floor(d / 86400) + 'd ago';
}
function _autoTimeUntil(ts) {
    if (!ts) return '';
    const d = (_autoParseUTC(ts) - Date.now()) / 1000;
    if (d <= 0) return 'soon'; if (d < 60) return 'in ' + Math.ceil(d) + 's';
    if (d < 3600) return 'in ' + Math.ceil(d / 60) + 'm'; if (d < 86400) return 'in ' + Math.round(d / 3600) + 'h';
    return 'in ' + Math.round(d / 86400) + 'd';
}

// --- Live countdown for "Next: in Xs" ---
setInterval(() => {
    document.querySelectorAll('.auto-next-run[data-next]').forEach(el => {
        el.textContent = 'Next: ' + _autoTimeUntil(el.dataset.next);
    });
}, 1000);

// --- CRUD ---

async function deleteAutomation(id, name) {
    if (!await showConfirmDialog({ title: 'Delete Automation', message: `Delete automation "${name}"?`, confirmText: 'Delete', destructive: true })) return;
    try {
        const res = await fetch('/api/automations/' + id, { method: 'DELETE' });
        const data = await res.json();
        if (data.error) throw new Error(data.error);
        showToast('Automation deleted', 'success');
        await loadAutomations();
    } catch (err) { showToast('Error: ' + err.message, 'error'); }
}

async function duplicateAutomation(id) {
    try {
        const res = await fetch('/api/automations/' + id + '/duplicate', { method: 'POST' });
        const data = await res.json();
        if (data.error) throw new Error(data.error);
        showToast('Automation duplicated', 'success');
        await loadAutomations();
    } catch (err) { showToast('Error: ' + err.message, 'error'); }
}

async function toggleAutomation(id) {
    try {
        const res = await fetch('/api/automations/' + id + '/toggle', { method: 'POST' });
        const data = await res.json();
        if (data.error) throw new Error(data.error);
        await loadAutomations();
    } catch (err) { showToast('Error: ' + err.message, 'error'); }
}

// --- Automation Progress Tracking ---
const _autoProgressLogCounts = {};
const _autoProgressHideTimers = {};

function updateAutomationProgressFromData(data) {
    for (const [aidStr, state] of Object.entries(data)) {
        const aid = parseInt(aidStr);
        const card = document.querySelector(`.automation-card[data-id="${aid}"]`);
        if (!card) continue;

        let panel = card.querySelector('.automation-output');
        if (!panel) {
            panel = document.createElement('div');
            panel.className = 'automation-output';
            panel.innerHTML = `
                <div class="auto-progress-bar-wrap"><div class="auto-progress-bar" style="width:0%"></div></div>
                <div class="auto-progress-phase"></div>
                <div class="auto-progress-log"></div>
            `;
            card.appendChild(panel);
            _autoProgressLogCounts[aid] = 0;
        }

        // Update progress bar
        const bar = panel.querySelector('.auto-progress-bar');
        bar.style.width = (state.progress || 0) + '%';

        // Update phase text
        const phaseEl = panel.querySelector('.auto-progress-phase');
        phaseEl.textContent = state.phase || '';

        // Status indicator on card
        const statusDot = card.querySelector('.automation-status');

        if (state.status === 'running') {
            if (statusDot) statusDot.className = 'automation-status running';
            card.classList.add('running');
            panel.classList.add('visible');
            panel.classList.remove('finished', 'error');
            if (_autoProgressHideTimers[aid]) {
                clearTimeout(_autoProgressHideTimers[aid]);
                delete _autoProgressHideTimers[aid];
            }
            // Reset log for new run (handles re-run within hide window)
            if (_autoProgressLogCounts[aid] > 0 && state.log && state.log.length < _autoProgressLogCounts[aid]) {
                const existingLog = panel.querySelector('.auto-progress-log');
                if (existingLog) existingLog.innerHTML = '';
                _autoProgressLogCounts[aid] = 0;
            }
        } else if (state.status === 'finished' || state.status === 'error') {
            if (statusDot) statusDot.className = 'automation-status ' + (card.querySelector('input[type=checkbox]')?.checked ? 'enabled' : 'disabled');
            card.classList.remove('running');
            bar.style.width = '100%';
            panel.classList.add('finished');
            if (state.status === 'error') panel.classList.add('error');
            if (!_autoProgressHideTimers[aid]) {
                _autoProgressHideTimers[aid] = setTimeout(() => {
                    panel.classList.remove('visible');
                    delete _autoProgressHideTimers[aid];
                    _autoProgressLogCounts[aid] = 0;
                }, 30000);
            }
        }

        // Update log lines
        const logEl = panel.querySelector('.auto-progress-log');
        const rendered = _autoProgressLogCounts[aid] || 0;
        const logLines = state.log || [];
        if (logLines.length > rendered) {
            // Normal append — log is still growing
            for (let i = rendered; i < logLines.length; i++) {
                const line = logLines[i];
                const div = document.createElement('div');
                div.className = 'auto-log-line ' + (line.type || 'info');
                div.textContent = line.text;
                logEl.appendChild(div);
            }
            _autoProgressLogCounts[aid] = logLines.length;
            logEl.scrollTop = logEl.scrollHeight;
        } else if (logLines.length === rendered && logLines.length >= 50) {
            // Log buffer is full and rotating — replace last few lines
            const children = logEl.children;
            if (children.length > 0) {
                const lastServerLine = logLines[logLines.length - 1];
                const lastDomLine = children[children.length - 1];
                if (lastServerLine && lastDomLine.textContent !== lastServerLine.text) {
                    // Content changed — full re-render
                    logEl.innerHTML = '';
                    for (const line of logLines) {
                        const div = document.createElement('div');
                        div.className = 'auto-log-line ' + (line.type || 'info');
                        div.textContent = line.text;
                        logEl.appendChild(div);
                    }
                    _autoProgressLogCounts[aid] = logLines.length;
                    logEl.scrollTop = logEl.scrollHeight;
                }
            }
        }
    }
}

async function runAutomation(id) {
    try {
        const res = await fetch('/api/automations/' + id + '/run', { method: 'POST' });
        const data = await res.json();
        if (data.error) throw new Error(data.error);
        showToast('Automation triggered', 'success');
        setTimeout(() => loadAutomations(), 1500);
    } catch (err) { showToast('Error: ' + err.message, 'error'); }
}

const _RESULT_DISPLAY_MAP = {
    'start_database_update': [
        { key: 'artists', label: 'Artists' },
        { key: 'albums', label: 'Albums' },
        { key: 'tracks', label: 'Tracks' },
        { key: 'removed_artists', label: 'Removed Artists', hideZero: true },
        { key: 'removed_albums', label: 'Removed Albums', hideZero: true },
        { key: 'removed_tracks', label: 'Removed Tracks', hideZero: true },
    ],
    'deep_scan_library': [
        { key: 'artists', label: 'Artists' },
        { key: 'albums', label: 'Albums' },
        { key: 'tracks', label: 'Tracks' },
        { key: 'removed_artists', label: 'Removed Artists', hideZero: true },
        { key: 'removed_albums', label: 'Removed Albums', hideZero: true },
        { key: 'removed_tracks', label: 'Removed Tracks', hideZero: true },
    ],
    'scan_watchlist': [
        { key: 'artists_scanned', label: 'Artists Scanned' },
        { key: 'successful_scans', label: 'Successful' },
        { key: 'new_tracks_found', label: 'New Tracks' },
        { key: 'tracks_added_to_wishlist', label: 'Added to Wishlist' },
    ],
    'run_duplicate_cleaner': [
        { key: 'files_scanned', label: 'Files Scanned' },
        { key: 'duplicates_found', label: 'Duplicates Found' },
        { key: 'files_deleted', label: 'Files Deleted' },
        { key: 'space_freed_mb', label: 'Space Freed (MB)' },
    ],
    'start_quality_scan': [
        { key: 'tracks_scanned', label: 'Tracks Scanned' },
        { key: 'quality_met', label: 'Quality Met' },
        { key: 'low_quality', label: 'Low Quality' },
        { key: 'matched', label: 'Added to Wishlist' },
    ],
    'scan_library': [
        { key: 'scan_duration_seconds', label: 'Duration (s)' },
    ],
    'backup_database': [
        { key: 'size_mb', label: 'Backup Size (MB)' },
    ],
    'refresh_mirrored': [
        { key: 'refreshed', label: 'Playlists Refreshed' },
        { key: 'errors', label: 'Errors', hideZero: true },
    ],
    'clear_quarantine': [
        { key: 'removed', label: 'Items Removed' },
    ],
    'cleanup_wishlist': [
        { key: 'removed', label: 'Duplicates Removed' },
    ],
    'full_cleanup': [
        { key: 'quarantine_removed', label: 'Quarantine Removed' },
        { key: 'staging_removed', label: 'Import Dirs Removed' },
        { key: 'total_removed', label: 'Total Items Removed' },
    ],
    'playlist_pipeline': [
        { key: 'playlists_refreshed', label: 'Refreshed' },
        { key: 'tracks_discovered', label: 'Discovered' },
        { key: 'tracks_synced', label: 'Synced' },
        { key: 'sync_skipped', label: 'Skipped', hideZero: true },
        { key: 'wishlist_queued', label: 'Wishlist Queued' },
        { key: 'duration_seconds', label: 'Duration (s)' },
    ],
};

function _renderResultStats(resultJson, actionType) {
    if (!resultJson || typeof resultJson !== 'object') return '';
    var fields = _RESULT_DISPLAY_MAP[actionType];
    var items = [];
    if (fields) {
        fields.forEach(function (f) {
            var val = resultJson[f.key];
            if (val == null) return;
            if (f.hideZero && (val === 0 || val === '0')) return;
            items.push({ label: f.label, value: val });
        });
    } else {
        // Generic fallback: show all non-status, non-underscore keys
        Object.keys(resultJson).forEach(function (k) {
            if (k === 'status' || k.startsWith('_')) return;
            var label = k.replace(/_/g, ' ').replace(/\b\w/g, function (c) { return c.toUpperCase(); });
            items.push({ label: label, value: resultJson[k] });
        });
    }
    if (items.length === 0) return '';
    var html = '<div class="history-stats-grid">';
    items.forEach(function (it) {
        html += '<div class="history-stat-item"><div class="history-stat-label">' + _esc(it.label) + '</div><div class="history-stat-value">' + _esc(String(it.value)) + '</div></div>';
    });
    html += '</div>';
    return html;
}

async function showAutomationHistory(automationId, automationName, actionType) {
    let modal = document.getElementById('automation-history-modal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'automation-history-modal';
        modal.className = 'modal-overlay';
        document.body.appendChild(modal);
    }
    modal.innerHTML = '<div class="modal-container automation-history-modal"><div class="history-modal-header"><h3>Run History: ' + _esc(automationName) + '</h3><button class="history-close-btn" onclick="document.getElementById(\'automation-history-modal\').style.display=\'none\'">&times;</button></div><div class="history-modal-body"><div class="history-loading">Loading...</div></div></div>';
    modal.style.display = 'flex';
    modal.onclick = function (e) { if (e.target === modal) modal.style.display = 'none'; };

    try {
        const res = await fetch('/api/automations/' + automationId + '/history?limit=50');
        const data = await res.json();
        if (data.error) throw new Error(data.error);
        const body = modal.querySelector('.history-modal-body');
        if (!data.history || data.history.length === 0) {
            body.innerHTML = '<div class="history-empty">No run history yet. History will be recorded on future runs.</div>';
            return;
        }
        let html = '<div class="history-entries">';
        data.history.forEach(function (entry) {
            const statusClass = 'history-status-' + (entry.status || 'completed');
            const statusLabel = (entry.status || 'completed').charAt(0).toUpperCase() + (entry.status || 'completed').slice(1);
            const timeAgo = _autoTimeAgo(entry.started_at);
            const duration = entry.duration_seconds != null ? _formatDuration(entry.duration_seconds) : '';
            const summary = entry.summary ? _esc(entry.summary) : '';
            const hasLogs = entry.log_lines && entry.log_lines.length > 0;
            const entryId = 'history-entry-' + entry.id;

            html += '<div class="history-entry">';
            html += '<div class="history-entry-header" onclick="var el=document.getElementById(\'' + entryId + '\'); if(el) el.classList.toggle(\'expanded\')">';
            html += '<span class="history-status-badge ' + statusClass + '">' + statusLabel + '</span>';
            html += '<span class="history-time">' + timeAgo + '</span>';
            if (duration) html += '<span class="history-duration">' + duration + '</span>';
            if (hasLogs) html += '<span class="history-expand-icon">&#9660;</span>';
            html += '</div>';
            if (summary) html += '<div class="history-summary">' + summary + '</div>';
            if (entry.result_json && typeof entry.result_json === 'object') {
                html += _renderResultStats(entry.result_json, actionType);
            }
            if (hasLogs) {
                html += '<div id="' + entryId + '" class="history-log-section">';
                entry.log_lines.forEach(function (log) {
                    html += '<div class="history-log-line history-log-' + (log.type || 'info') + '">' + _esc(log.text || '') + '</div>';
                });
                html += '</div>';
            }
            html += '</div>';
        });
        html += '</div>';
        if (data.total > data.history.length) {
            html += '<div class="history-total">Showing ' + data.history.length + ' of ' + data.total + ' runs</div>';
        }
        body.innerHTML = html;
    } catch (err) {
        const body = modal.querySelector('.history-modal-body');
        if (body) body.innerHTML = '<div class="history-empty">Error loading history: ' + _esc(err.message) + '</div>';
    }
}

function _formatDuration(seconds) {
    if (seconds < 1) return '<1s';
    if (seconds < 60) return Math.round(seconds) + 's';
    var m = Math.floor(seconds / 60);
    var s = Math.round(seconds % 60);
    if (m < 60) return m + 'm ' + s + 's';
    var h = Math.floor(m / 60);
    m = m % 60;
    return h + 'h ' + m + 'm';
}

async function saveAutomation() {
    const name = document.getElementById('builder-name').value.trim();
    if (!name) { showToast('Name is required', 'error'); return; }
    if (!_autoBuilder.when) { showToast('Add a trigger (WHEN)', 'error'); return; }
    if (!_autoBuilder.do) { showToast('Add an action (DO)', 'error'); return; }

    // Read configs from DOM
    const triggerConfig = _readPlacedConfig('when');
    const actionConfig = _readPlacedConfig('do');

    // Read THEN actions (multi-slot)
    const thenActions = _autoBuilder.then.map((item, i) => ({
        type: item.type,
        config: _readPlacedConfig('then-' + i),
    }));

    // Read optional delay from DO slot
    const delayEl = document.getElementById('cfg-do-delay');
    const delayVal = delayEl ? parseInt(delayEl.value) : 0;
    if (delayVal > 0) actionConfig.delay = delayVal;

    const groupInput = document.getElementById('builder-group-name');
    const groupName = groupInput ? groupInput.value.trim() : '';

    const body = {
        name,
        trigger_type: _autoBuilder.when.type, trigger_config: triggerConfig,
        action_type: _autoBuilder.do.type, action_config: actionConfig,
        then_actions: thenActions,
        group_name: groupName || null,
    };

    try {
        let res;
        if (_autoBuilder.editId) {
            res = await fetch('/api/automations/' + _autoBuilder.editId, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
        } else {
            res = await fetch('/api/automations', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
        }
        const data = await res.json();
        if (data.error) throw new Error(data.error);
        showToast(_autoBuilder.editId ? 'Automation updated' : 'Automation created', 'success');
        hideAutomationBuilder();
        await loadAutomations();
    } catch (err) { showToast('Error: ' + err.message, 'error'); }
}

// --- Builder View ---

async function showAutomationBuilder(editId) {
    // Load block definitions (always refresh)
    try {
        const res = await fetch('/api/automations/blocks');
        _autoBlocks = await res.json();
    } catch (e) {
        if (!_autoBlocks) { showToast('Failed to load blocks', 'error'); return; }
    }

    _autoMirroredPlaylists = null; // invalidate so it re-fetches
    _autoSpotifyAuthenticated = false;
    _autoBuilder = { editId: editId || null, when: null, do: null, then: [], isSystem: false };

    // Populate group datalist from existing automations
    try {
        const allRes = await fetch('/api/automations');
        const allAutos = await allRes.json();
        const groupSet = new Set();
        if (Array.isArray(allAutos)) allAutos.forEach(a => { if (a.group_name) groupSet.add(a.group_name); });
        const datalist = document.getElementById('builder-group-list');
        if (datalist) datalist.innerHTML = [...groupSet].sort().map(g => `<option value="${_escAttr(g)}">`).join('');
    } catch (e) { }

    // If editing, load automation data
    if (editId) {
        try {
            const res = await fetch('/api/automations/' + editId);
            const a = await res.json();
            if (a.error) throw new Error(a.error);
            document.getElementById('builder-name').value = a.name || '';
            const groupInput = document.getElementById('builder-group-name');
            if (groupInput) groupInput.value = a.group_name || '';
            _autoBuilder.when = { type: a.trigger_type, config: a.trigger_config || {} };
            _autoBuilder.do = { type: a.action_type, config: a.action_config || {} };
            // Load then_actions array
            _autoBuilder.then = (a.then_actions || []).map(item => ({
                type: item.type, config: item.config || {}
            }));
            // Backward compat: if no then_actions but has notify_type
            if (!_autoBuilder.then.length && a.notify_type) {
                _autoBuilder.then = [{ type: a.notify_type, config: a.notify_config || {} }];
            }
            _autoBuilder.isSystem = !!a.is_system;
        } catch (err) { showToast('Failed to load automation', 'error'); return; }
    } else {
        document.getElementById('builder-name').value = '';
        const groupInput = document.getElementById('builder-group-name');
        if (groupInput) groupInput.value = '';
    }

    // System automations: lock the name field and hide group
    document.getElementById('builder-name').readOnly = _autoBuilder.isSystem;
    const groupEl = document.getElementById('builder-group-name');
    if (groupEl) groupEl.style.display = _autoBuilder.isSystem ? 'none' : '';

    _renderBuilderSidebar();
    _renderBuilderCanvas();

    document.getElementById('automations-list-view').style.display = 'none';
    document.getElementById('automations-builder-view').style.display = '';
}

function hideAutomationBuilder() {
    document.getElementById('automations-builder-view').style.display = 'none';
    document.getElementById('automations-list-view').style.display = '';
    document.getElementById('builder-name').readOnly = false;
    _autoBuilder = { editId: null, when: null, do: null, then: [], isSystem: false };
}

// --- Sidebar ---

function _renderBuilderSidebar() {
    const sidebar = document.getElementById('builder-sidebar');
    if (!sidebar || !_autoBlocks) return;

    let html = '';
    const sections = [
        { key: 'triggers', title: 'Triggers', slot: 'when' },
        { key: 'actions', title: 'Actions', slot: 'do' },
        { key: 'notifications', title: 'Then', slot: 'then' },
    ];

    sections.forEach(sec => {
        html += `<div class="sidebar-section"><div class="sidebar-section-title">${sec.title}</div>`;
        (_autoBlocks[sec.key] || []).forEach(block => {
            const icon = _autoIcons[block.type] || '\u2699\uFE0F';
            const disabled = !block.available;
            const helpKey = 'auto-' + block.type;
            const hasHelp = !!TOOL_HELP_CONTENT[helpKey];
            html += `<div class="block-item${disabled ? ' coming-soon' : ''}" ${!disabled ? `draggable="true" ondragstart="_autoDragStart(event,'${block.type}','${sec.slot}')" onclick="_autoClickBlock('${block.type}','${sec.slot}')"` : ''}>
                <div class="block-item-icon">${icon}</div>
                <div class="block-item-text">
                    <div class="block-item-label">${_esc(block.label)}</div>
                    <div class="block-item-desc">${_esc(block.description)}</div>
                </div>
                ${disabled ? '<span class="coming-soon-badge">Soon</span>' : ''}
                ${hasHelp ? `<button class="tool-help-button block-help-btn" onclick="event.stopPropagation(); openToolHelpModal('${helpKey}')" title="Learn more">?</button>` : ''}
            </div>`;
        });
        html += '</div>';
    });
    sidebar.innerHTML = html;
}

// --- Canvas ---

function _renderBuilderCanvas() {
    const canvas = document.getElementById('builder-canvas');
    if (!canvas) return;

    let html = '';

    // WHEN slot
    const whenData = _autoBuilder.when;
    html += '<span class="flow-slot-label when">WHEN</span>';
    if (whenData) {
        html += `<div class="flow-slot filled" id="slot-when" ondragover="_autoDragOver(event,'when')" ondragleave="_autoDragLeave(event,'when')" ondrop="_autoDrop(event,'when')">
            ${_renderPlacedBlock('when', whenData)}
        </div>`;
    } else {
        html += `<div class="flow-slot empty" id="slot-when" ondragover="_autoDragOver(event,'when')" ondragleave="_autoDragLeave(event,'when')" ondrop="_autoDrop(event,'when')">
            <div class="flow-slot-prompt">Drag a trigger here — WHEN does this run?</div>
        </div>`;
    }

    html += '<div class="flow-connector"></div>';

    // DO slot
    const doData = _autoBuilder.do;
    html += '<span class="flow-slot-label do">DO</span>';
    if (doData) {
        html += `<div class="flow-slot filled" id="slot-do" ondragover="_autoDragOver(event,'do')" ondragleave="_autoDragLeave(event,'do')" ondrop="_autoDrop(event,'do')">
            ${_renderPlacedBlock('do', doData)}
        </div>`;
    } else {
        html += `<div class="flow-slot empty" id="slot-do" ondragover="_autoDragOver(event,'do')" ondragleave="_autoDragLeave(event,'do')" ondrop="_autoDrop(event,'do')">
            <div class="flow-slot-prompt">Drag an action here — WHAT should it do?</div>
        </div>`;
    }

    html += '<div class="flow-connector"></div>';

    // THEN section (multi-slot, 1-3 items)
    html += '<span class="flow-slot-label then">THEN</span>';
    if (_autoBuilder.then.length > 0) {
        _autoBuilder.then.forEach((item, i) => {
            if (i > 0) html += '<div class="flow-connector small"></div>';
            html += `<div class="flow-slot filled then-slot" id="slot-then-${i}">
                ${_renderPlacedBlock('then-' + i, item)}
            </div>`;
        });
    }
    if (_autoBuilder.then.length < 3) {
        if (_autoBuilder.then.length > 0) html += '<div class="flow-connector small"></div>';
        html += `<div class="flow-slot empty then-add" id="slot-then-add"
            ondragover="_autoDragOver(event,'then')" ondragleave="_autoDragLeave(event,'then')" ondrop="_autoDrop(event,'then')">
            <div class="flow-slot-prompt">${_autoBuilder.then.length === 0
                ? 'Drag a then-action here (optional)'
                : '+ Add another (max 3)'}</div>
        </div>`;
    }

    canvas.innerHTML = html;
    // Load mirrored playlist selects if any are present
    _autoLoadMirroredSelects();
    // Set up checkbox state for refresh_mirrored
    ['when', 'do'].forEach(sk => {
        const allCb = document.getElementById('cfg-' + sk + '-all');
        if (allCb) _autoTogglePlaylistSelect(sk);
    });
    // Also check then slots
    _autoBuilder.then.forEach((item, i) => {
        const allCb = document.getElementById('cfg-then-' + i + '-all');
        if (allCb) _autoTogglePlaylistSelect('then-' + i);
    });
}

function _renderPlacedBlock(slotKey, data) {
    const blockDef = _findBlockDef(data.type);
    const icon = _autoIcons[data.type] || '\u2699\uFE0F';
    const label = blockDef ? blockDef.label : data.type;
    const configHtml = _renderBlockConfigFields(slotKey, data.type, data.config || {});

    // Add optional delay field for action blocks
    let delayHtml = '';
    if (slotKey === 'do') {
        const delayVal = (data.config && data.config.delay) || '';
        delayHtml = `<div class="placed-block-config"><div class="config-row">
            <label>Delay (minutes)</label>
            <input type="number" id="cfg-${slotKey}-delay" value="${delayVal}" min="0" placeholder="0" style="width:80px;" title="Wait before executing action">
        </div></div>`;
    }

    // System automations: lock trigger and action slots (no remove, no replace)
    const locked = _autoBuilder.isSystem && (slotKey === 'when' || slotKey === 'do');
    const removeBtn = locked ? '' : `<button class="placed-block-remove" onclick="_autoRemoveBlock('${slotKey}')">\u2715</button>`;

    return `<div class="placed-block${locked ? ' locked' : ''}" data-type="${_escAttr(data.type)}">
        <div class="placed-block-header">
            <span class="placed-block-icon">${icon}</span>
            <span class="placed-block-label">${_esc(label)}</span>
            ${removeBtn}
        </div>
        ${configHtml ? '<div class="placed-block-config">' + configHtml + '</div>' : ''}
        ${delayHtml}
    </div>`;
}

function _renderBlockConfigFields(slotKey, blockType, config) {
    if (blockType === 'schedule') {
        const interval = config.interval || 6;
        const unit = config.unit || 'hours';
        return `<div class="config-row">
            <label>Every</label>
            <input type="number" id="cfg-${slotKey}-interval" value="${interval}" min="1" style="width:70px;">
            <select id="cfg-${slotKey}-unit">
                <option value="minutes"${unit === 'minutes' ? ' selected' : ''}>Minutes</option>
                <option value="hours"${unit === 'hours' ? ' selected' : ''}>Hours</option>
                <option value="days"${unit === 'days' ? ' selected' : ''}>Days</option>
            </select>
        </div>`;
    }
    if (blockType === 'daily_time') {
        const timeVal = config.time || '03:00';
        return `<div class="config-row">
            <label>At</label>
            <input type="time" id="cfg-${slotKey}-time" value="${timeVal}">
        </div>`;
    }
    if (blockType === 'weekly_time') {
        const timeVal = config.time || '03:00';
        const selectedDays = config.days || [];
        const allDays = [['mon', 'Mon'], ['tue', 'Tue'], ['wed', 'Wed'], ['thu', 'Thu'], ['fri', 'Fri'], ['sat', 'Sat'], ['sun', 'Sun']];
        let dayHtml = '<div class="config-row"><label>Days</label><div class="day-picker" id="cfg-' + slotKey + '-days">';
        allDays.forEach(([val, lbl]) => {
            const active = selectedDays.includes(val) ? ' active' : '';
            dayHtml += `<button type="button" class="day-btn${active}" data-day="${val}" onclick="this.classList.toggle('active')">${lbl}</button>`;
        });
        dayHtml += '</div></div>';
        return `<div class="config-row">
            <label>At</label>
            <input type="time" id="cfg-${slotKey}-time" value="${timeVal}">
        </div>${dayHtml}`;
    }

    // Event triggers with conditions
    const blockDef = _findBlockDef(blockType);
    if (blockDef && blockDef.has_conditions) {
        return _renderConditionBuilder(slotKey, blockDef, config);
    }

    if (blockType === 'process_wishlist') {
        const cat = config.category || 'all';
        return `<div class="config-row">
            <label>Category</label>
            <select id="cfg-${slotKey}-category">
                <option value="all"${cat === 'all' ? ' selected' : ''}>All</option>
                <option value="albums"${cat === 'albums' ? ' selected' : ''}>Albums</option>
                <option value="singles"${cat === 'singles' ? ' selected' : ''}>Singles</option>
            </select>
        </div>`;
    }
    if (blockType === 'signal_received') {
        const sigName = _escAttr(config.signal_name || '');
        const knownSignals = (_autoBlocks && _autoBlocks.known_signals) || [];
        return `<div class="config-row">
            <label>Signal Name</label>
            <input type="text" id="cfg-${slotKey}-signal_name" value="${sigName}"
                list="known-signals-list-${slotKey}" placeholder="e.g. libraryReady"
                oninput="this.value = this.value.toLowerCase().replace(/[^a-z0-9_\\-]/g, '_').replace(/_+/g, '_').replace(/^_|_$/g, '')"
                style="font-family:monospace;">
            <datalist id="known-signals-list-${slotKey}">
                ${knownSignals.map(s => `<option value="${s}">`).join('')}
            </datalist>
        </div>
        <div class="config-row" style="color:rgba(255,255,255,0.35);font-size:11px;">Triggers when another automation fires this signal</div>`;
    }
    if (blockType === 'fire_signal') {
        const sigName = _escAttr(config.signal_name || '');
        const knownSignals = (_autoBlocks && _autoBlocks.known_signals) || [];
        return `<div class="config-row">
            <label>Signal Name</label>
            <input type="text" id="cfg-${slotKey}-signal_name" value="${sigName}"
                list="known-signals-fire-${slotKey}" placeholder="e.g. libraryReady"
                oninput="this.value = this.value.toLowerCase().replace(/[^a-z0-9_\\-]/g, '_').replace(/_+/g, '_').replace(/^_|_$/g, '')"
                style="font-family:monospace;">
            <datalist id="known-signals-fire-${slotKey}">
                ${knownSignals.map(s => `<option value="${s}">`).join('')}
            </datalist>
        </div>
        <div class="config-row" style="color:rgba(255,255,255,0.35);font-size:11px;">Other automations with "Signal Received" trigger will wake up</div>`;
    }
    if (blockType === 'run_script') {
        const scriptName = _escAttr(config.script_name || '');
        const timeout = config.timeout || 60;
        // Fetch scripts list and populate
        const selectId = `cfg-${slotKey}-script_name`;
        setTimeout(async () => {
            try {
                const resp = await fetch('/api/scripts');
                const data = await resp.json();
                const sel = document.getElementById(selectId);
                if (sel && data.scripts) {
                    sel.innerHTML = '<option value="">Select a script...</option>' +
                        data.scripts.map(s => `<option value="${_escAttr(s.name)}"${s.name === scriptName ? ' selected' : ''}>${escapeHtml(s.name)} (${s.extension})</option>`).join('');
                }
            } catch (e) { console.warn('Failed to load scripts:', e); }
        }, 100);
        return `<div class="config-row">
            <label>Script</label>
            <select id="${selectId}">
                <option value="${scriptName}">${scriptName || 'Loading...'}</option>
            </select>
        </div>
        <div class="config-row">
            <label>Timeout</label>
            <input type="number" id="cfg-${slotKey}-timeout" value="${timeout}" min="5" max="300" style="width:80px;"> seconds
        </div>
        <div class="config-row" style="color:rgba(255,255,255,0.35);font-size:11px;">Place scripts in the <code>scripts/</code> folder. Supported: .sh, .py, .bat, .ps1</div>`;
    }
    if (blockType === 'scan_watchlist' || blockType === 'scan_library' || blockType === 'notify_only') {
        return '<div class="config-row" style="color:rgba(255,255,255,0.4);font-size:12px;">No configuration needed</div>';
    }
    if (blockType === 'refresh_mirrored') {
        const allChecked = config.all ? ' checked' : '';
        return `<div class="config-row">
            <label>Playlist</label>
            <select id="cfg-${slotKey}-playlist_id" class="mirrored-playlist-select" data-block-type="refresh_mirrored" data-value="${_escAttr(config.playlist_id || '')}">
                <option value="">Loading...</option>
            </select>
        </div>
        <div class="config-row">
            <label><input type="checkbox" id="cfg-${slotKey}-all"${allChecked} onchange="_autoTogglePlaylistSelect('${slotKey}')"> Refresh all mirrored playlists</label>
        </div>`;
    }
    if (blockType === 'sync_playlist') {
        return `<div class="config-row">
            <label>Playlist</label>
            <select id="cfg-${slotKey}-playlist_id" class="mirrored-playlist-select" data-value="${_escAttr(config.playlist_id || '')}">
                <option value="">Loading...</option>
            </select>
        </div>`;
    }
    if (blockType === 'discover_playlist') {
        const allChecked = config.all ? ' checked' : '';
        return `<div class="config-row">
            <label>Playlist</label>
            <select id="cfg-${slotKey}-playlist_id" class="mirrored-playlist-select" data-value="${_escAttr(config.playlist_id || '')}">
                <option value="">Loading...</option>
            </select>
        </div>
        <div class="config-row">
            <label><input type="checkbox" id="cfg-${slotKey}-all"${allChecked} onchange="_autoTogglePlaylistSelect('${slotKey}')"> Discover all mirrored playlists</label>
        </div>`;
    }
    if (blockType === 'playlist_pipeline') {
        const allChecked = config.all ? ' checked' : '';
        const skipWishlistChecked = config.skip_wishlist ? ' checked' : '';
        return `<div class="config-row">
            <label>Playlist</label>
            <select id="cfg-${slotKey}-playlist_id" class="mirrored-playlist-select" data-value="${_escAttr(config.playlist_id || '')}">
                <option value="">Loading...</option>
            </select>
        </div>
        <div class="config-row">
            <label><input type="checkbox" id="cfg-${slotKey}-all"${allChecked} onchange="_autoTogglePlaylistSelect('${slotKey}')"> Process all mirrored playlists</label>
        </div>
        <div class="config-row">
            <label><input type="checkbox" id="cfg-${slotKey}-skip_wishlist"${skipWishlistChecked}> Skip wishlist processing</label>
        </div>
        <div class="config-row" style="color:rgba(255,255,255,0.35);font-size:11px;">Runs 4 phases: Refresh → Discover → Sync → Download Missing</div>`;
    }
    // Shared variable tags builder for notification types
    function _notifyVarHtml(slotKey) {
        let allVars = ['time', 'name', 'run_count', 'status'];
        const triggerDef = _autoBuilder.when ? _findBlockDef(_autoBuilder.when.type) : null;
        if (triggerDef && triggerDef.variables) {
            triggerDef.variables.forEach(v => { if (!allVars.includes(v)) allVars.push(v); });
        }
        let html = '<div class="variable-tags">';
        allVars.forEach(v => { html += `<span class="variable-tag" onclick="_autoInsertVar('cfg-${slotKey}-message','{${v}}')">{${v}}</span>`; });
        return html + '</div>';
    }

    if (blockType === 'discord_webhook') {
        const url = _escAttr(config.webhook_url || '');
        return `<div class="config-row">
            <label>Webhook URL</label>
            <input type="text" id="cfg-${slotKey}-webhook_url" value="${url}" placeholder="https://discord.com/api/webhooks/...">
        </div>
        <div class="config-row">
            <label>Message</label>
            <textarea id="cfg-${slotKey}-message" placeholder="Message with {variables}...">${config.message || '{name} completed with status: {status}'}</textarea>
        </div>
        ${_notifyVarHtml(slotKey)}`;
    }
    if (blockType === 'pushbullet') {
        const token = _escAttr(config.access_token || '');
        return `<div class="config-row">
            <label>Access Token</label>
            <input type="text" id="cfg-${slotKey}-access_token" value="${token}" placeholder="o.xxxxxxxxxxxxxxxxxxxx">
        </div>
        <div class="config-row">
            <label>Title</label>
            <input type="text" id="cfg-${slotKey}-title" value="${_escAttr(config.title || '{name}')}" placeholder="Notification title">
        </div>
        <div class="config-row">
            <label>Message</label>
            <textarea id="cfg-${slotKey}-message" placeholder="Message with {variables}...">${config.message || 'Completed with status: {status}'}</textarea>
        </div>
        ${_notifyVarHtml(slotKey)}`;
    }
    if (blockType === 'telegram') {
        const botToken = _escAttr(config.bot_token || '');
        const chatId = _escAttr(config.chat_id || '');
        return `<div class="config-row">
            <label>Bot Token</label>
            <input type="text" id="cfg-${slotKey}-bot_token" value="${botToken}" placeholder="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11">
        </div>
        <div class="config-row">
            <label>Chat ID</label>
            <input type="text" id="cfg-${slotKey}-chat_id" value="${chatId}" placeholder="-1001234567890 or @channelname">
        </div>
        <div class="config-row">
            <label>Message</label>
            <textarea id="cfg-${slotKey}-message" placeholder="Message with {variables}...">${config.message || '{name} completed with status: {status}'}</textarea>
        </div>
        ${_notifyVarHtml(slotKey)}`;
    }
    if (blockType === 'webhook') {
        const url = _escAttr(config.url || '');
        const hdrs = (config.headers || '').replace(/"/g, '&quot;');
        return `<div class="config-row">
            <label>URL</label>
            <input type="text" id="cfg-${slotKey}-url" value="${url}" placeholder="https://your-server.com/hook">
        </div>
        <div class="config-row">
            <label>Headers <span style="opacity:0.4;font-weight:400">(one per line, Key: Value)</span></label>
            <textarea id="cfg-${slotKey}-headers" placeholder="Authorization: Bearer token123\nX-Custom: value" style="font-family:monospace;font-size:11px;">${hdrs}</textarea>
        </div>
        <div class="config-row">
            <label>Custom Message <span style="opacity:0.4;font-weight:400">(optional)</span></label>
            <textarea id="cfg-${slotKey}-message" placeholder="Message with {variables}...">${config.message || ''}</textarea>
        </div>
        <div class="config-row" style="color:rgba(255,255,255,0.35);font-size:11px;">
            Sends a JSON POST with all event variables. Custom message added as "message" field if set.
        </div>
        ${_notifyVarHtml(slotKey)}`;
    }
    return '';
}

// --- Condition Builder ---

function _renderConditionBuilder(slotKey, blockDef, config) {
    const conditions = config.conditions || [];
    const match = config.match || 'all';
    const fields = blockDef.condition_fields || [];

    let html = '<div class="condition-builder" id="conditions-' + slotKey + '">';
    html += `<div class="config-row condition-header">
        <label>Match</label>
        <select id="cfg-${slotKey}-match" class="condition-match-select">
            <option value="all"${match === 'all' ? ' selected' : ''}>All conditions</option>
            <option value="any"${match === 'any' ? ' selected' : ''}>Any condition</option>
        </select>
    </div>`;

    html += '<div id="condition-rows-' + slotKey + '">';
    if (conditions.length) {
        conditions.forEach((cond, i) => {
            html += _renderConditionRow(slotKey, i, fields, cond);
        });
    }
    html += '</div>';

    html += `<button class="add-condition-btn" onclick="_autoAddCondition('${slotKey}')">+ Add Condition</button>`;
    html += '</div>';

    if (!conditions.length) {
        html += '<div class="config-row" style="color:rgba(255,255,255,0.35);font-size:12px;margin-top:4px;">No conditions = triggers on every event</div>';
    }

    return html;
}

function _renderConditionRow(slotKey, index, fields, cond) {
    const field = cond ? cond.field : (fields[0] || '');
    const operator = cond ? cond.operator : 'equals';
    const value = cond ? _escAttr(cond.value) : '';

    let fieldOpts = '';
    fields.forEach(f => { fieldOpts += `<option value="${f}"${f === field ? ' selected' : ''}>${f}</option>`; });

    // For playlist-related triggers, use a mirrored playlist dropdown instead of free text
    const triggerType = _autoBuilder.when ? _autoBuilder.when.type : '';
    const usePlaylistSelect = ((triggerType === 'playlist_changed' || triggerType === 'discovery_completed') && field === 'playlist_name');
    const valueHtml = usePlaylistSelect
        ? `<select class="cond-value mirrored-playlist-name-select" data-slot="${slotKey}" data-idx="${index}" data-value="${value}"></select>`
        : `<input type="text" class="cond-value" data-slot="${slotKey}" data-idx="${index}" value="${value}" placeholder="value...">`;

    return `<div class="condition-row" data-index="${index}">
        <select class="cond-field" data-slot="${slotKey}" data-idx="${index}">${fieldOpts}</select>
        <select class="cond-operator" data-slot="${slotKey}" data-idx="${index}">
            <option value="equals"${operator === 'equals' ? ' selected' : ''}>equals</option>
            <option value="contains"${operator === 'contains' ? ' selected' : ''}>contains</option>
            <option value="starts_with"${operator === 'starts_with' ? ' selected' : ''}>starts with</option>
            <option value="not_contains"${operator === 'not_contains' ? ' selected' : ''}>not contains</option>
        </select>
        ${valueHtml}
        <button class="remove-condition-btn" onclick="_autoRemoveCondition('${slotKey}',${index})">\u2715</button>
    </div>`;
}

function _autoAddCondition(slotKey) {
    const data = _autoBuilder[slotKey];
    if (!data) return;
    if (!data.config) data.config = {};
    if (!data.config.conditions) data.config.conditions = [];

    // Save existing conditions from DOM before re-render
    _autoSaveConditionsFromDOM(slotKey);

    const blockDef = _findBlockDef(data.type);
    const fields = blockDef ? (blockDef.condition_fields || []) : [];
    data.config.conditions.push({ field: fields[0] || '', operator: 'contains', value: '' });
    _renderBuilderCanvas();
    // Re-populate mirrored playlist selects if needed
    _autoLoadMirroredSelects();
}

function _autoRemoveCondition(slotKey, index) {
    const data = _autoBuilder[slotKey];
    if (!data || !data.config || !data.config.conditions) return;
    _autoSaveConditionsFromDOM(slotKey);
    data.config.conditions.splice(index, 1);
    _renderBuilderCanvas();
    _autoLoadMirroredSelects();
}

function _autoSaveConditionsFromDOM(slotKey) {
    const data = _autoBuilder[slotKey];
    if (!data || !data.config) return;
    const container = document.getElementById('condition-rows-' + slotKey);
    if (!container) return;
    const rows = container.querySelectorAll('.condition-row');
    const conditions = [];
    rows.forEach(row => {
        const field = row.querySelector('.cond-field')?.value || '';
        const operator = row.querySelector('.cond-operator')?.value || 'contains';
        const value = row.querySelector('.cond-value')?.value || '';
        conditions.push({ field, operator, value });
    });
    data.config.conditions = conditions;
    // Also save match mode
    const matchEl = document.getElementById('cfg-' + slotKey + '-match');
    if (matchEl) data.config.match = matchEl.value;
}

// --- Mirrored Playlist Select ---

function _autoTogglePlaylistSelect(slotKey) {
    const allCb = document.getElementById('cfg-' + slotKey + '-all');
    const sel = document.getElementById('cfg-' + slotKey + '-playlist_id');
    if (sel) sel.disabled = allCb && allCb.checked;
}

async function _autoLoadMirroredSelects() {
    const selects = document.querySelectorAll('.mirrored-playlist-select');
    const nameSelects = document.querySelectorAll('.mirrored-playlist-name-select');
    if (!selects.length && !nameSelects.length) return;

    if (!_autoMirroredPlaylists) {
        try {
            const res = await fetch('/api/mirrored-playlists/list');
            const data = await res.json();
            // New format returns { playlists, spotify_authenticated }
            if (Array.isArray(data)) {
                // Backward compat: old format was plain array
                _autoMirroredPlaylists = data;
                _autoSpotifyAuthenticated = false;
            } else {
                _autoMirroredPlaylists = data.playlists || [];
                _autoSpotifyAuthenticated = data.spotify_authenticated || false;
            }
        } catch (e) { _autoMirroredPlaylists = []; _autoSpotifyAuthenticated = false; }
    }

    selects.forEach(sel => {
        const savedValue = sel.dataset.value || '';
        const isRefresh = sel.dataset.blockType === 'refresh_mirrored';
        sel.innerHTML = '<option value="">-- Select playlist --</option>';
        _autoMirroredPlaylists.forEach(p => {
            // For refresh selects: hide file playlists, hide spotify (library) if not authed
            if (isRefresh) {
                if (p.source === 'file' || p.source === 'beatport') return;
                if (p.source === 'spotify' && !_autoSpotifyAuthenticated) return;
            }
            sel.innerHTML += `<option value="${p.id}"${String(p.id) === savedValue ? ' selected' : ''}>${_esc(p.name)}</option>`;
        });
    });

    nameSelects.forEach(sel => {
        const savedValue = sel.dataset.value || '';
        sel.innerHTML = '<option value="">-- Select playlist --</option>';
        _autoMirroredPlaylists.forEach(p => {
            sel.innerHTML += `<option value="${_escAttr(p.name)}"${p.name === savedValue ? ' selected' : ''}>${_esc(p.name)}</option>`;
        });
    });
}

function _readPlacedConfig(slotKey) {
    let data;
    if (slotKey.startsWith('then-')) {
        const idx = parseInt(slotKey.split('-')[1]);
        data = _autoBuilder.then[idx];
    } else {
        data = _autoBuilder[slotKey];
    }
    if (!data) return {};
    const type = data.type;
    if (type === 'schedule') {
        return {
            interval: parseInt(document.getElementById('cfg-' + slotKey + '-interval')?.value) || 6,
            unit: document.getElementById('cfg-' + slotKey + '-unit')?.value || 'hours',
        };
    }
    if (type === 'daily_time') {
        return { time: document.getElementById('cfg-' + slotKey + '-time')?.value || '03:00' };
    }
    if (type === 'weekly_time') {
        const daysEl = document.getElementById('cfg-' + slotKey + '-days');
        const days = daysEl ? Array.from(daysEl.querySelectorAll('.day-btn.active')).map(b => b.dataset.day) : [];
        return {
            time: document.getElementById('cfg-' + slotKey + '-time')?.value || '03:00',
            days,
        };
    }
    // Event triggers with conditions
    const blockDef = _findBlockDef(type);
    if (blockDef && blockDef.has_conditions) {
        _autoSaveConditionsFromDOM(slotKey);
        return {
            conditions: (data.config && data.config.conditions) || [],
            match: document.getElementById('cfg-' + slotKey + '-match')?.value || 'all',
        };
    }
    if (type === 'process_wishlist') {
        return { category: document.getElementById('cfg-' + slotKey + '-category')?.value || 'all' };
    }
    if (type === 'refresh_mirrored') {
        const allCb = document.getElementById('cfg-' + slotKey + '-all');
        return {
            playlist_id: document.getElementById('cfg-' + slotKey + '-playlist_id')?.value || '',
            all: allCb ? allCb.checked : false,
        };
    }
    if (type === 'sync_playlist') {
        return { playlist_id: document.getElementById('cfg-' + slotKey + '-playlist_id')?.value || '' };
    }
    if (type === 'discover_playlist') {
        const allCb = document.getElementById('cfg-' + slotKey + '-all');
        return {
            playlist_id: document.getElementById('cfg-' + slotKey + '-playlist_id')?.value || '',
            all: allCb ? allCb.checked : false,
        };
    }
    if (type === 'playlist_pipeline') {
        const allCb = document.getElementById('cfg-' + slotKey + '-all');
        const skipWl = document.getElementById('cfg-' + slotKey + '-skip_wishlist');
        return {
            playlist_id: document.getElementById('cfg-' + slotKey + '-playlist_id')?.value || '',
            all: allCb ? allCb.checked : false,
            skip_wishlist: skipWl ? skipWl.checked : false,
        };
    }
    if (type === 'signal_received' || type === 'fire_signal') {
        return { signal_name: document.getElementById('cfg-' + slotKey + '-signal_name')?.value?.trim() || '' };
    }
    if (type === 'run_script') {
        return {
            script_name: document.getElementById('cfg-' + slotKey + '-script_name')?.value || '',
            timeout: parseInt(document.getElementById('cfg-' + slotKey + '-timeout')?.value || '60') || 60,
        };
    }
    if (type === 'discord_webhook') {
        return {
            webhook_url: document.getElementById('cfg-' + slotKey + '-webhook_url')?.value?.trim() || '',
            message: document.getElementById('cfg-' + slotKey + '-message')?.value || '',
        };
    }
    if (type === 'pushbullet') {
        return {
            access_token: document.getElementById('cfg-' + slotKey + '-access_token')?.value?.trim() || '',
            title: document.getElementById('cfg-' + slotKey + '-title')?.value || '',
            message: document.getElementById('cfg-' + slotKey + '-message')?.value || '',
        };
    }
    if (type === 'telegram') {
        return {
            bot_token: document.getElementById('cfg-' + slotKey + '-bot_token')?.value?.trim() || '',
            chat_id: document.getElementById('cfg-' + slotKey + '-chat_id')?.value?.trim() || '',
            message: document.getElementById('cfg-' + slotKey + '-message')?.value || '',
        };
    }
    if (type === 'webhook') {
        return {
            url: document.getElementById('cfg-' + slotKey + '-url')?.value?.trim() || '',
            headers: document.getElementById('cfg-' + slotKey + '-headers')?.value || '',
            message: document.getElementById('cfg-' + slotKey + '-message')?.value || '',
        };
    }
    return {};
}

function _findBlockDef(type) {
    if (!_autoBlocks) return null;
    for (const cat of ['triggers', 'actions', 'notifications']) {
        const found = (_autoBlocks[cat] || []).find(b => b.type === type);
        if (found) return found;
    }
    return null;
}

// --- Drag & Drop ---

function _autoDragStart(e, blockType, slotCategory) {
    e.dataTransfer.setData('text/plain', JSON.stringify({ type: blockType, slot: slotCategory }));
    e.dataTransfer.effectAllowed = 'copy';
}

function _autoDragOver(e, slotKey) {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'copy';
    const targetId = slotKey === 'then' ? 'slot-then-add' : 'slot-' + slotKey;
    document.getElementById(targetId)?.classList.add('drag-over');
}

function _autoDragLeave(e, slotKey) {
    const targetId = slotKey === 'then' ? 'slot-then-add' : 'slot-' + slotKey;
    document.getElementById(targetId)?.classList.remove('drag-over');
}

function _autoDrop(e, slotKey) {
    e.preventDefault();
    const dropTargetId = slotKey === 'then' ? 'slot-then-add' : 'slot-' + slotKey;
    document.getElementById(dropTargetId)?.classList.remove('drag-over');
    if (_autoBuilder.isSystem && (slotKey === 'when' || slotKey === 'do')) return;
    try {
        const data = JSON.parse(e.dataTransfer.getData('text/plain'));
        // Handle THEN slot (append to array)
        if (slotKey === 'then') {
            if (data.slot !== 'then') { showToast('Wrong slot — drop ' + data.slot + ' blocks here', 'error'); return; }
            if (_autoBuilder.then.length >= 3) { showToast('Maximum 3 then-actions', 'error'); return; }
            _autoBuilder.then.push({ type: data.type, config: {} });
        } else {
            if (data.slot !== slotKey) { showToast('Wrong slot — drop ' + data.slot + ' blocks here', 'error'); return; }
            _autoBuilder[slotKey] = { type: data.type, config: {} };
        }
        _renderBuilderCanvas();
    } catch (err) { }
}

// Click-to-add (alternative to drag)
function _autoClickBlock(blockType, slotCategory) {
    if (_autoBuilder.isSystem && (slotCategory === 'when' || slotCategory === 'do')) return;
    if (slotCategory === 'then') {
        if (_autoBuilder.then.length >= 3) { showToast('Maximum 3 then-actions', 'error'); return; }
        _autoBuilder.then.push({ type: blockType, config: {} });
    } else {
        _autoBuilder[slotCategory] = { type: blockType, config: {} };
    }
    _renderBuilderCanvas();
}

function _autoRemoveBlock(slotKey) {
    if (_autoBuilder.isSystem && (slotKey === 'when' || slotKey === 'do')) return;
    // Handle then-N slots
    if (slotKey.startsWith('then-')) {
        const idx = parseInt(slotKey.split('-')[1]);
        if (!isNaN(idx) && idx >= 0 && idx < _autoBuilder.then.length) {
            _autoBuilder.then.splice(idx, 1);
        }
    } else {
        _autoBuilder[slotKey] = null;
    }
    _renderBuilderCanvas();
}

// Variable insertion
function _autoInsertVar(textareaId, variable) {
    const el = document.getElementById(textareaId);
    if (!el) return;
    const start = el.selectionStart, end = el.selectionEnd;
    el.value = el.value.substring(0, start) + variable + el.value.substring(end);
    el.selectionStart = el.selectionEnd = start + variable.length;
    el.focus();
}

// ===== ISSUES PAGE =====

const ISSUE_CATEGORIES = {
    wrong_track: { label: 'Wrong Track', icon: '&#10060;', description: 'This file plays a completely different song than expected', applies: ['track'] },
    wrong_metadata: { label: 'Wrong Metadata', icon: '&#9998;', description: 'Title, artist, year, or other tags are incorrect', applies: ['track', 'album'] },
    wrong_cover: { label: 'Wrong Cover Art', icon: '&#128247;', description: 'The album artwork is wrong or missing', applies: ['album'] },
    wrong_artist: { label: 'Wrong Artist', icon: '&#128100;', description: 'This track is filed under the wrong artist', applies: ['track'] },
    duplicate_tracks: { label: 'Duplicate Tracks', icon: '&#128257;', description: 'The same track appears more than once in this album', applies: ['album'] },
    missing_tracks: { label: 'Missing Tracks', icon: '&#10067;', description: 'Tracks that should be here are missing from this album', applies: ['album'] },
    audio_quality: { label: 'Audio Quality', icon: '&#127925;', description: 'Audio has quality issues — clipping, low bitrate, silence, etc.', applies: ['track'] },
    wrong_album: { label: 'Wrong Album', icon: '&#128191;', description: 'This track belongs to a different album', applies: ['track'] },
    incomplete_album: { label: 'Incomplete Album', icon: '&#9888;', description: 'Album is partially downloaded — some tracks present, others not', applies: ['album'] },
    other: { label: 'Other', icon: '&#128172;', description: 'Any other issue not listed above', applies: ['track', 'album'] },
};

const ISSUE_STATUS_META = {
    open: { label: 'Open', cls: 'issue-status-open' },
    in_progress: { label: 'In Progress', cls: 'issue-status-progress' },
    resolved: { label: 'Resolved', cls: 'issue-status-resolved' },
    dismissed: { label: 'Dismissed', cls: 'issue-status-dismissed' },
};

let _issuesPageState = { loaded: false };

function _issueHeaders(extra) {
    const h = { 'X-Profile-Id': String(currentProfile ? currentProfile.id : 1) };
    if (extra) Object.assign(h, extra);
    return h;
}

async function loadIssuesPage() {
    const admin = isEnhancedAdmin();
    const subtitle = document.getElementById('issues-subtitle');
    if (subtitle) {
        subtitle.textContent = admin ? 'Manage and resolve reported library problems' : 'Track and resolve library problems';
    }
    await Promise.all([loadIssuesList(), loadIssuesCounts()]);
}

async function loadIssuesCounts() {
    try {
        const resp = await fetch('/api/issues/counts', { headers: _issueHeaders() });
        const data = await resp.json();
        if (!data.success) return;
        const counts = data.counts;
        const statsEl = document.getElementById('issues-stats');
        if (!statsEl) return;
        const total = (counts.open || 0) + (counts.in_progress || 0) + (counts.resolved || 0) + (counts.dismissed || 0);
        statsEl.innerHTML = `
            <div class="issues-stat-card issues-stat-open">
                <div class="issues-stat-number">${counts.open || 0}</div>
                <div class="issues-stat-label">Open</div>
            </div>
            <div class="issues-stat-card issues-stat-progress">
                <div class="issues-stat-number">${counts.in_progress || 0}</div>
                <div class="issues-stat-label">In Progress</div>
            </div>
            <div class="issues-stat-card issues-stat-resolved">
                <div class="issues-stat-number">${counts.resolved || 0}</div>
                <div class="issues-stat-label">Resolved</div>
            </div>
            <div class="issues-stat-card issues-stat-dismissed">
                <div class="issues-stat-number">${counts.dismissed || 0}</div>
                <div class="issues-stat-label">Dismissed</div>
            </div>
            <div class="issues-stat-card issues-stat-total">
                <div class="issues-stat-number">${total}</div>
                <div class="issues-stat-label">Total</div>
            </div>
        `;
        // Update nav badge
        const badge = document.getElementById('issues-nav-badge');
        if (badge) {
            const openCount = counts.open || 0;
            badge.textContent = openCount;
            badge.classList.toggle('hidden', openCount === 0);
        }
    } catch (e) {
        console.error('Failed to load issue counts:', e);
    }
}

async function loadIssuesList() {
    const listEl = document.getElementById('issues-list');
    if (!listEl) return;
    listEl.innerHTML = '<div class="issues-loading"><div class="issues-spinner"></div>Loading issues...</div>';

    const statusFilter = document.getElementById('issues-filter-status')?.value || '';
    const categoryFilter = document.getElementById('issues-filter-category')?.value || '';

    let url = '/api/issues?';
    if (statusFilter) url += `status=${encodeURIComponent(statusFilter)}&`;
    if (categoryFilter) url += `category=${encodeURIComponent(categoryFilter)}&`;

    try {
        const profileId = currentProfile ? currentProfile.id : 1;
        const resp = await fetch(url, { headers: { 'X-Profile-Id': String(profileId) } });
        const data = await resp.json();
        if (!data.success || !data.issues || data.issues.length === 0) {
            listEl.innerHTML = `
                <div class="issues-empty">
                    <div class="issues-empty-icon">&#128269;</div>
                    <div class="issues-empty-title">No issues found</div>
                    <div class="issues-empty-text">${statusFilter || categoryFilter ? 'Try adjusting your filters' : 'No issues have been reported yet'}</div>
                </div>
            `;
            return;
        }
        listEl.innerHTML = '';
        data.issues.forEach(issue => {
            listEl.appendChild(renderIssueCard(issue));
        });
    } catch (e) {
        console.error('Failed to load issues:', e);
        listEl.innerHTML = '<div class="issues-empty"><div class="issues-empty-title">Failed to load issues</div></div>';
    }
}

function renderIssueCard(issue) {
    const card = document.createElement('div');
    card.className = 'issue-card';
    card.dataset.issueId = issue.id;
    card.onclick = () => showIssueDetailModal(issue.id);

    const catMeta = ISSUE_CATEGORIES[issue.category] || ISSUE_CATEGORIES.other;
    const statusMeta = ISSUE_STATUS_META[issue.status] || ISSUE_STATUS_META.open;
    const admin = isEnhancedAdmin();

    let snapshot = {};
    try { snapshot = typeof issue.snapshot_data === 'string' ? JSON.parse(issue.snapshot_data || '{}') : (issue.snapshot_data || {}); } catch (e) { }

    const entityLabel = issue.entity_type === 'track' ? 'Track' : (issue.entity_type === 'album' ? 'Album' : 'Artist');
    const entityName = snapshot.title || snapshot.name || `${entityLabel} #${issue.entity_id}`;
    const artistName = snapshot.artist_name || '';
    const albumName = snapshot.album_title || '';
    const thumbUrl = snapshot.thumb_url || snapshot.album_thumb || '';

    const createdDate = issue.created_at ? new Date(issue.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) : '';
    const createdTime = issue.created_at ? new Date(issue.created_at).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' }) : '';

    // Priority indicator
    const priorityCls = issue.priority === 'high' ? 'issue-priority-high' : (issue.priority === 'low' ? 'issue-priority-low' : 'issue-priority-normal');

    let thumbHtml = '';
    if (thumbUrl) {
        thumbHtml = `<img class="issue-card-thumb" src="${_escAttr(thumbUrl)}" alt="" onerror="this.style.display='none'">`;
    } else {
        thumbHtml = `<div class="issue-card-thumb-placeholder">${catMeta.icon}</div>`;
    }

    let metaLine = '';
    if (issue.entity_type === 'track') {
        metaLine = [artistName, albumName].filter(Boolean).map(s => _esc(s)).join(' &mdash; ');
    } else if (issue.entity_type === 'album') {
        metaLine = artistName ? _esc(artistName) : '';
    }

    let profileBadge = '';
    if (admin && issue.reporter_name) {
        profileBadge = `<span class="issue-card-profile">by ${_esc(issue.reporter_name)}</span>`;
    }

    let adminResponseIndicator = '';
    if (issue.admin_response) {
        adminResponseIndicator = '<span class="issue-card-responded" title="Admin has responded">&#128172;</span>';
    }

    card.innerHTML = `
        <div class="issue-card-left">
            ${thumbHtml}
        </div>
        <div class="issue-card-center">
            <div class="issue-card-title-row">
                <span class="issue-card-category-icon" title="${_escAttr(catMeta.label)}">${catMeta.icon}</span>
                <span class="issue-card-title">${_esc(issue.title)}</span>
                ${adminResponseIndicator}
            </div>
            <div class="issue-card-entity">
                <span class="issue-card-entity-type">${_esc(entityLabel)}</span>
                <span class="issue-card-entity-name">${_esc(entityName)}</span>
                ${metaLine ? `<span class="issue-card-meta-line">${metaLine}</span>` : ''}
            </div>
            ${issue.description ? `<div class="issue-card-description">${_esc(issue.description)}</div>` : ''}
            <div class="issue-card-footer">
                <span class="issue-card-date">${_esc(createdDate)} ${_esc(createdTime)}</span>
                ${profileBadge}
            </div>
        </div>
        <div class="issue-card-right">
            <span class="issue-status-badge ${statusMeta.cls}">${_esc(statusMeta.label)}</span>
            <span class="issue-priority-dot ${priorityCls}" title="${_esc(issue.priority)} priority"></span>
        </div>
    `;
    return card;
}

// --- Report Issue Modal ---

let _reportIssueState = {};

function showReportIssueModal(entityType, entityId, entityName, artistName, albumTitle) {
    _reportIssueState = { entityType, entityId, entityName, artistName, albumTitle: albumTitle || '' };
    const overlay = document.getElementById('report-issue-overlay');
    const titleEl = document.getElementById('report-issue-title');
    const body = document.getElementById('report-issue-body');
    if (!overlay || !body) return;

    const entityLabel = entityType === 'track' ? 'Track' : (entityType === 'album' ? 'Album' : 'Artist');
    titleEl.textContent = `Report Issue — ${entityLabel}`;

    body.innerHTML = `
        <div class="report-issue-entity-info">
            <div class="report-issue-entity-name">${_esc(entityName)}</div>
            ${artistName ? `<div class="report-issue-entity-artist">${_esc(artistName)}${albumTitle ? ' &mdash; ' + _esc(albumTitle) : ''}</div>` : ''}
        </div>
        <div class="report-issue-section">
            <label class="report-issue-label">What's the problem?</label>
            <div class="report-issue-category-grid" id="report-issue-categories">
                ${Object.entries(ISSUE_CATEGORIES)
            .filter(([, cat]) => !cat.applies || cat.applies.includes(entityType))
            .map(([key, cat]) => `
                    <div class="report-issue-category-card" data-category="${_escAttr(key)}" onclick="selectIssueCategory(this, '${_escAttr(key)}')">
                        <div class="report-issue-category-icon">${cat.icon}</div>
                        <div class="report-issue-category-label">${_esc(cat.label)}</div>
                        <div class="report-issue-category-desc">${_esc(cat.description)}</div>
                    </div>
                `).join('')}
            </div>
        </div>
        <div class="report-issue-section" id="report-issue-details-section" style="display:none">
            <label class="report-issue-label">Title</label>
            <input type="text" class="report-issue-input" id="report-issue-input-title" placeholder="Brief summary of the issue..." maxlength="200" oninput="event.target._userEdited = true">
            <label class="report-issue-label" style="margin-top:12px">Details <span class="report-issue-optional">(optional)</span></label>
            <textarea class="report-issue-textarea" id="report-issue-input-desc" placeholder="Provide more details about what's wrong — what you expected vs what you see..." rows="4" maxlength="2000"></textarea>
            <label class="report-issue-label" style="margin-top:12px">Priority</label>
            <div class="report-issue-priority-row">
                <button class="report-issue-priority-btn" data-priority="low" onclick="selectIssuePriority(this, 'low')">Low</button>
                <button class="report-issue-priority-btn selected" data-priority="normal" onclick="selectIssuePriority(this, 'normal')">Normal</button>
                <button class="report-issue-priority-btn" data-priority="high" onclick="selectIssuePriority(this, 'high')">High</button>
            </div>
        </div>
    `;

    _reportIssueState.selectedCategory = null;
    _reportIssueState.selectedPriority = 'normal';
    const submitBtn = document.getElementById('report-issue-submit-btn');
    if (submitBtn) submitBtn.disabled = true;

    overlay.classList.remove('hidden');
}

function selectIssueCategory(el, category) {
    document.querySelectorAll('.report-issue-category-card').forEach(c => c.classList.remove('selected'));
    el.classList.add('selected');
    _reportIssueState.selectedCategory = category;

    const detailsSection = document.getElementById('report-issue-details-section');
    if (detailsSection) detailsSection.style.display = '';

    // Auto-generate title based on category
    const titleInput = document.getElementById('report-issue-input-title');
    const catMeta = ISSUE_CATEGORIES[category];
    if (titleInput && !titleInput._userEdited) {
        const entityName = _reportIssueState.entityName || '';
        titleInput.value = `${catMeta.label}: ${entityName}`;
    }

    const submitBtn = document.getElementById('report-issue-submit-btn');
    if (submitBtn) submitBtn.disabled = false;
}

function selectIssuePriority(el, priority) {
    document.querySelectorAll('.report-issue-priority-btn').forEach(b => b.classList.remove('selected'));
    el.classList.add('selected');
    _reportIssueState.selectedPriority = priority;
}

function closeReportIssueModal() {
    const overlay = document.getElementById('report-issue-overlay');
    if (overlay) overlay.classList.add('hidden');
    _reportIssueState = {};
}

async function submitIssue() {
    if (_reportIssueState._submitting) return;
    const category = _reportIssueState.selectedCategory;
    if (!category) {
        showToast('Please select an issue category', 'error');
        return;
    }

    const titleInput = document.getElementById('report-issue-input-title');
    const descInput = document.getElementById('report-issue-input-desc');
    const title = (titleInput?.value || '').trim();
    const description = (descInput?.value || '').trim();

    if (!title) {
        showToast('Please provide a title for the issue', 'error');
        return;
    }

    _reportIssueState._submitting = true;
    const submitBtn = document.getElementById('report-issue-submit-btn');
    if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.textContent = 'Submitting...';
    }

    try {
        const resp = await fetch('/api/issues', {
            method: 'POST',
            headers: _issueHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({
                profile_id: currentProfile ? currentProfile.id : 1,
                entity_type: _reportIssueState.entityType,
                entity_id: String(_reportIssueState.entityId),
                category: category,
                title: title,
                description: description,
                priority: _reportIssueState.selectedPriority || 'normal',
            }),
        });
        const data = await resp.json();
        if (data.success) {
            showToast('Issue reported successfully', 'success');
            closeReportIssueModal();
            // Refresh issues page if visible
            const issuesPage = document.getElementById('issues-page');
            if (issuesPage && issuesPage.classList.contains('active')) {
                loadIssuesPage();
            }
            // Update badge
            loadIssuesBadge();
        } else {
            showToast(data.error || 'Failed to submit issue', 'error');
        }
    } catch (e) {
        console.error('Failed to submit issue:', e);
        showToast('Failed to submit issue', 'error');
    } finally {
        _reportIssueState._submitting = false;
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.textContent = 'Submit Issue';
        }
    }
}

// --- Issue Detail Modal ---

async function showIssueDetailModal(issueId) {
    const overlay = document.getElementById('issue-detail-overlay');
    const body = document.getElementById('issue-detail-body');
    const footer = document.getElementById('issue-detail-footer');
    const titleEl = document.getElementById('issue-detail-title');
    if (!overlay || !body) return;

    body.innerHTML = '<div class="issues-loading"><div class="issues-spinner"></div>Loading...</div>';
    footer.innerHTML = '<button class="enhanced-bulk-btn secondary" onclick="closeIssueDetailModal()">Close</button>';
    overlay.classList.remove('hidden');

    try {
        const resp = await fetch(`/api/issues/${issueId}`, { headers: _issueHeaders() });
        const data = await resp.json();
        if (!data.success || !data.issue) {
            body.innerHTML = '<div class="issues-empty"><div class="issues-empty-title">Issue not found</div></div>';
            return;
        }
        renderIssueDetail(data.issue, body, footer, titleEl);
    } catch (e) {
        console.error('Failed to load issue:', e);
        body.innerHTML = '<div class="issues-empty"><div class="issues-empty-title">Failed to load issue</div></div>';
    }
}

function renderIssueDetail(issue, body, footer, titleEl) {
    const admin = isEnhancedAdmin();
    const catMeta = ISSUE_CATEGORIES[issue.category] || ISSUE_CATEGORIES.other;
    const statusMeta = ISSUE_STATUS_META[issue.status] || ISSUE_STATUS_META.open;

    let snapshot = {};
    try { snapshot = typeof issue.snapshot_data === 'string' ? JSON.parse(issue.snapshot_data || '{}') : (issue.snapshot_data || {}); } catch (e) { }

    const entityLabel = issue.entity_type === 'track' ? 'Track' : (issue.entity_type === 'album' ? 'Album' : 'Artist');
    const entityName = snapshot.title || snapshot.name || `${entityLabel} #${issue.entity_id}`;
    const artistName = snapshot.artist_name || (issue.entity_type === 'artist' ? snapshot.name : '') || '';
    const albumTitle = issue.entity_type === 'album' ? (snapshot.title || '') : (snapshot.album_title || '');
    const artistId = issue.entity_type === 'artist' ? snapshot.id : snapshot.artist_id;

    // Resolve image URLs — album art and artist photo
    let artistThumb = '';
    let albumThumb = '';
    if (issue.entity_type === 'album') {
        albumThumb = snapshot.thumb_url || '';
        artistThumb = snapshot.artist_thumb || '';
    } else if (issue.entity_type === 'track') {
        albumThumb = snapshot.album_thumb || '';
        artistThumb = snapshot.artist_thumb || '';
    } else {
        // Artist issue
        artistThumb = snapshot.thumb_url || '';
    }

    // Determine the album-level Spotify ID for download/wishlist actions
    const spotifyAlbumId = snapshot.spotify_album_id || '';

    console.log('Issue detail snapshot:', { entityType: issue.entity_type, albumThumb, artistThumb, spotifyAlbumId, snapshotKeys: Object.keys(snapshot) });

    const createdDate = issue.created_at ? new Date(issue.created_at).toLocaleString() : 'Unknown';
    const resolvedDate = issue.resolved_at ? new Date(issue.resolved_at).toLocaleString() : '';

    titleEl.textContent = `Issue #${issue.id}`;

    // --- Build external links chips ---
    function _extLinks(snap) {
        const links = [];
        if (snap.spotify_artist_id) links.push({ svc: 'Spotify', type: 'Artist', url: `https://open.spotify.com/artist/${snap.spotify_artist_id}`, cls: 'ext-spotify' });
        if (snap.spotify_album_id) links.push({ svc: 'Spotify', type: 'Album', url: `https://open.spotify.com/album/${snap.spotify_album_id}`, cls: 'ext-spotify' });
        if (snap.spotify_track_id) links.push({ svc: 'Spotify', type: 'Track', url: `https://open.spotify.com/track/${snap.spotify_track_id}`, cls: 'ext-spotify' });
        if (snap.artist_musicbrainz_id) links.push({ svc: 'MusicBrainz', type: 'Artist', url: `https://musicbrainz.org/artist/${snap.artist_musicbrainz_id}`, cls: 'ext-mb' });
        if (snap.musicbrainz_release_id) links.push({ svc: 'MusicBrainz', type: 'Release', url: `https://musicbrainz.org/release/${snap.musicbrainz_release_id}`, cls: 'ext-mb' });
        if (snap.musicbrainz_recording_id) links.push({ svc: 'MusicBrainz', type: 'Recording', url: `https://musicbrainz.org/recording/${snap.musicbrainz_recording_id}`, cls: 'ext-mb' });
        if (snap.artist_deezer_id) links.push({ svc: 'Deezer', type: 'Artist', url: `https://www.deezer.com/artist/${snap.artist_deezer_id}`, cls: 'ext-deezer' });
        if (snap.album_deezer_id) links.push({ svc: 'Deezer', type: 'Album', url: `https://www.deezer.com/album/${snap.album_deezer_id}`, cls: 'ext-deezer' });
        if (snap.track_deezer_id) links.push({ svc: 'Deezer', type: 'Track', url: `https://www.deezer.com/track/${snap.track_deezer_id}`, cls: 'ext-deezer' });
        if (snap.artist_tidal_id) links.push({ svc: 'Tidal', type: 'Artist', url: `https://listen.tidal.com/artist/${snap.artist_tidal_id}`, cls: 'ext-tidal' });
        if (snap.album_tidal_id) links.push({ svc: 'Tidal', type: 'Album', url: `https://listen.tidal.com/album/${snap.album_tidal_id}`, cls: 'ext-tidal' });
        if (snap.artist_qobuz_id) links.push({ svc: 'Qobuz', type: 'Artist', cls: 'ext-qobuz', id: snap.artist_qobuz_id });
        if (snap.album_qobuz_id) links.push({ svc: 'Qobuz', type: 'Album', cls: 'ext-qobuz', id: snap.album_qobuz_id });
        return links;
    }

    const extLinks = _extLinks(snapshot);
    let extLinksHtml = '';
    if (extLinks.length > 0) {
        const chips = extLinks.map(l => {
            if (l.url) {
                return `<a class="issue-ext-chip ${l.cls}" href="${_escAttr(l.url)}" target="_blank" rel="noopener" title="${_escAttr(l.svc + ' ' + l.type)}">${_esc(l.svc)} <span class="issue-ext-chip-type">${_esc(l.type)}</span></a>`;
            }
            return `<span class="issue-ext-chip ${l.cls}" title="${_escAttr(l.svc + ' ' + l.type + ': ' + l.id)}">${_esc(l.svc)} <span class="issue-ext-chip-type">${_esc(l.type)}</span></span>`;
        }).join('');
        extLinksHtml = `<div class="issue-ext-chips" style="margin-top:8px">${chips}</div>`;
    }

    // --- Build enhanced-library-style album/track widget ---
    // Determine which album data to show (for album issues it's the entity, for track issues it's the parent)
    const showAlbumWidget = (issue.entity_type === 'album' || issue.entity_type === 'track');
    const albumName = issue.entity_type === 'album' ? (snapshot.title || '') : (snapshot.album_title || '');
    const albumYear = snapshot.year || '';
    const albumLabel = snapshot.label || '';
    const albumType = snapshot.record_type || '';
    const albumTrackCount = issue.entity_type === 'album' ? (snapshot.track_count || '') : (snapshot.album_track_count || '');
    const albumGenres = snapshot.genres || [];

    // --- Build the hero section (artist photo + album art + info) ---
    let heroHtml = '';
    if (showAlbumWidget) {
        // Genre tags
        let genreTagsHtml = '';
        if (Array.isArray(albumGenres) && albumGenres.length > 0) {
            genreTagsHtml = `<div class="issue-hero-genres">${albumGenres.slice(0, 5).map(g => `<span class="issue-hero-genre-tag">${_esc(g)}</span>`).join('')}</div>`;
        }

        // Album meta line
        const albumMetaParts = [];
        if (albumYear) albumMetaParts.push(String(albumYear));
        if (albumType) albumMetaParts.push(albumType.charAt(0).toUpperCase() + albumType.slice(1));
        if (albumTrackCount) albumMetaParts.push(albumTrackCount + ' tracks');
        if (albumLabel) albumMetaParts.push(albumLabel);

        // For track issues, show the track title under the album
        const trackNameLine = issue.entity_type === 'track' && entityName
            ? `<div class="issue-hero-track-name">&#9835; ${_esc(entityName)}</div>` : '';

        heroHtml = `
            <div class="issue-hero">
                <div class="issue-hero-art-group">
                    ${artistThumb ? `<img class="issue-hero-artist-thumb" src="${_escAttr(artistThumb)}" alt="" onerror="this.style.display='none'">` : ''}
                    ${albumThumb ? `<img class="issue-hero-album-art" src="${_escAttr(albumThumb)}" alt="" onerror="this.style.display='none';this.nextElementSibling.style.display='flex'">` : ''}
                    <div class="issue-hero-album-placeholder" style="${albumThumb ? 'display:none' : ''}">${catMeta.icon}</div>
                </div>
                <div class="issue-hero-info">
                    ${artistName ? `<div class="issue-hero-artist">${_esc(artistName)}</div>` : ''}
                    <div class="issue-hero-album">${_esc(albumName)}</div>
                    ${trackNameLine}
                    ${albumMetaParts.length > 0 ? `<div class="issue-hero-meta">${_esc(albumMetaParts.join(' \u00B7 '))}</div>` : ''}
                    ${genreTagsHtml}
                    ${extLinksHtml}
                </div>
            </div>
        `;
    } else {
        // Artist-level issue — simpler hero
        heroHtml = `
            <div class="issue-hero">
                <div class="issue-hero-art-group">
                    ${artistThumb ? `<img class="issue-hero-album-art" src="${_escAttr(artistThumb)}" alt="" onerror="this.style.display='none'">` : `<div class="issue-hero-album-placeholder">${catMeta.icon}</div>`}
                </div>
                <div class="issue-hero-info">
                    <div class="issue-hero-album">${_esc(entityName)}</div>
                    ${extLinksHtml}
                </div>
            </div>
        `;
    }

    // --- Issue info bar ---
    let issueInfoHtml = `
        <div class="issue-detail-info-bar">
            <div class="issue-detail-info-left">
                <span class="issue-status-badge ${statusMeta.cls}">${_esc(statusMeta.label)}</span>
                <span class="issue-priority-dot issue-priority-${_escAttr(issue.priority || 'normal')}" title="${_esc((issue.priority || 'normal'))} priority"></span>
                <span class="issue-detail-category">${catMeta.icon} ${_esc(catMeta.label)}</span>
            </div>
            <div class="issue-detail-info-right">
                <span class="issue-detail-date">Reported ${_esc(createdDate)}</span>
                ${issue.reporter_name && admin ? `<span class="issue-detail-profile">by ${_esc(issue.reporter_name)}</span>` : ''}
                ${resolvedDate ? `<span class="issue-detail-date">Resolved ${_esc(resolvedDate)}</span>` : ''}
            </div>
        </div>
    `;

    // --- Issue description ---
    let descriptionHtml = `
        <div class="issue-detail-section">
            <div class="issue-detail-section-title">Issue</div>
            <div class="issue-detail-title-text">${_esc(issue.title)}</div>
            ${issue.description ? `<div class="issue-detail-description">${_esc(issue.description)}</div>` : '<div class="issue-detail-no-desc">No additional details provided</div>'}
        </div>
    `;

    // --- Action buttons (Download Album / Add to Wishlist) for admin ---
    let actionButtonsHtml = '';
    if (admin && (issue.entity_type === 'album' || issue.entity_type === 'track')) {
        actionButtonsHtml = `
            <div class="issue-action-buttons">
                <button class="issue-action-btn issue-action-download" id="issue-action-download" title="Download this album">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                    Download Album
                </button>
                <button class="issue-action-btn issue-action-wishlist" id="issue-action-wishlist" title="Add all tracks to wishlist">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21l-7-5-7 5V5a2 2 0 012-2h10a2 2 0 012 2z"/></svg>
                    Add to Wishlist
                </button>
            </div>
        `;
    }

    // --- Metadata grid for track-level issues ---
    let metaGridHtml = '';
    if (issue.entity_type === 'track') {
        const metaItems = [];
        if (snapshot.track_number) metaItems.push({ icon: '#', label: 'Track', value: String(snapshot.track_number) });
        if (snapshot.duration) metaItems.push({ icon: '&#9719;', label: 'Duration', value: typeof snapshot.duration === 'number' ? formatDurationMs(snapshot.duration) : String(snapshot.duration) });
        if (snapshot.format) metaItems.push({ icon: '&#128190;', label: 'Format', value: snapshot.format });
        if (snapshot.bitrate) metaItems.push({ icon: '&#127926;', label: 'Bitrate', value: snapshot.bitrate + ' kbps' });
        if (snapshot.bpm) metaItems.push({ icon: '&#9835;', label: 'BPM', value: String(snapshot.bpm) });
        if (snapshot.quality) metaItems.push({ icon: '&#9733;', label: 'Quality', value: snapshot.quality });
        if (metaItems.length > 0) {
            metaGridHtml = `
                <div class="issue-detail-section">
                    <div class="issue-detail-section-title">Track Details</div>
                    <div class="issue-detail-meta-grid">
                        ${metaItems.map(m => `
                            <div class="issue-meta-item">
                                <span class="issue-meta-icon">${m.icon}</span>
                                <span class="issue-meta-label">${_esc(m.label)}</span>
                                <span class="issue-meta-value">${_esc(m.value)}</span>
                            </div>
                        `).join('')}
                    </div>
                </div>
            `;
        }
    }

    // --- File path display for tracks ---
    let filePathHtml = '';
    if (snapshot.file_path) {
        filePathHtml = `
            <div class="issue-detail-section">
                <div class="issue-detail-section-title">File Path</div>
                <div class="issue-detail-filepath">${_esc(snapshot.file_path)}</div>
            </div>
        `;
    }

    // --- Enhanced-library-style track listing ---
    let trackListHtml = '';
    if (snapshot.tracks && Array.isArray(snapshot.tracks) && snapshot.tracks.length > 0) {
        let lastDisc = null;
        let rows = '';
        const hasMultiDisc = snapshot.tracks.some(tr => (tr.disc_number || 1) > 1);
        snapshot.tracks.forEach(t => {
            const disc = t.disc_number || 1;
            if (hasMultiDisc && disc !== lastDisc) {
                rows += `<div class="issue-detail-tracklist-disc">Disc ${disc}</div>`;
                lastDisc = disc;
            }
            const fmt = t.format || (t.file_path ? t.file_path.split('.').pop().toUpperCase() : '');
            const fmtLower = fmt.toLowerCase();
            const fmtClass = fmtLower === 'flac' ? 'flac' : (fmtLower === 'mp3' ? 'mp3' : 'other');
            const br = t.bitrate ? parseInt(t.bitrate) : 0;
            const brClass = br >= 320 || fmtLower === 'flac' ? 'high' : (br >= 192 ? 'medium' : 'low');
            const durStr = t.duration && typeof t.duration === 'number' ? formatDurationMs(t.duration) : '';

            rows += `
                <div class="issue-detail-tracklist-row">
                    <span class="issue-detail-tracklist-num">${_esc(String(t.track_number || '-'))}</span>
                    <span class="issue-detail-tracklist-title">${_esc(t.title || 'Unknown')}</span>
                    ${durStr ? `<span class="issue-detail-tracklist-dur">${durStr}</span>` : ''}
                    <span class="issue-detail-tracklist-meta">
                        ${fmt ? `<span class="enhanced-format-badge ${fmtClass}">${_esc(fmt)}</span>` : ''}
                        ${br ? `<span class="enhanced-bitrate ${brClass}">${br}k</span>` : ''}
                    </span>
                </div>
            `;
        });
        trackListHtml = `
            <div class="issue-detail-section">
                <div class="issue-detail-section-title">Track Listing <span class="issue-detail-section-count">${snapshot.tracks.length} tracks</span></div>
                <div class="issue-detail-tracklist">${rows}</div>
            </div>
        `;
    }

    // --- Admin response section ---
    let adminResponseHtml = '';
    if (admin) {
        adminResponseHtml = `
            <div class="issue-detail-section">
                <div class="issue-detail-section-title">Admin Response</div>
                <textarea class="issue-detail-response-textarea" id="issue-detail-response-input" placeholder="Write a response to the reporter..." rows="3">${_esc(issue.admin_response || '')}</textarea>
            </div>
        `;
    } else if (issue.admin_response) {
        adminResponseHtml = `
            <div class="issue-detail-section">
                <div class="issue-detail-section-title">Admin Response</div>
                <div class="issue-detail-admin-response">${_esc(issue.admin_response)}</div>
            </div>
        `;
    }

    body.innerHTML = `
        ${heroHtml}
        ${issueInfoHtml}
        ${actionButtonsHtml}
        ${descriptionHtml}
        ${metaGridHtml}
        ${filePathHtml}
        ${trackListHtml}
        ${adminResponseHtml}
    `;

    // --- Footer with status action buttons ---
    const safeId = parseInt(issue.id, 10);
    let footerHtml = '<button class="enhanced-bulk-btn secondary" onclick="closeIssueDetailModal()">Close</button>';

    if (admin) {
        if (issue.status === 'open' || issue.status === 'in_progress') {
            if (issue.status === 'open') {
                footerHtml += `<button class="enhanced-bulk-btn issue-btn-progress" onclick="updateIssueStatus(${safeId}, 'in_progress')">Mark In Progress</button>`;
            }
            footerHtml += `<button class="enhanced-bulk-btn issue-btn-resolve" onclick="updateIssueStatus(${safeId}, 'resolved')">Resolve</button>`;
            footerHtml += `<button class="enhanced-bulk-btn issue-btn-dismiss" onclick="updateIssueStatus(${safeId}, 'dismissed')">Dismiss</button>`;
        } else {
            footerHtml += `<button class="enhanced-bulk-btn issue-btn-reopen" onclick="updateIssueStatus(${safeId}, 'open')">Reopen</button>`;
        }
        footerHtml += `<button class="enhanced-bulk-btn issue-btn-delete" onclick="deleteIssue(${safeId})">Delete</button>`;
    } else {
        if (issue.status === 'open') {
            footerHtml += `<button class="enhanced-bulk-btn issue-btn-delete" onclick="deleteIssue(${safeId})">Withdraw</button>`;
        }
    }

    footer.innerHTML = footerHtml;

    // --- Attach action button handlers ---
    const dlBtn = document.getElementById('issue-action-download');
    if (dlBtn) {
        dlBtn.onclick = () => issueDownloadAlbum(spotifyAlbumId, artistName, albumName);
    }
    const wlBtn = document.getElementById('issue-action-wishlist');
    if (wlBtn) {
        wlBtn.onclick = () => issueAddToWishlist(spotifyAlbumId, artistName, albumName);
    }
}

// --- Issue Action: Download Album ---
async function issueDownloadAlbum(spotifyAlbumId, artistName, albumName) {
    const btn = document.getElementById('issue-action-download');
    if (!spotifyAlbumId && (!artistName || !albumName)) {
        showToast('No album ID or artist/album info available for download', 'warning');
        return;
    }
    try {
        if (btn) { btn.disabled = true; btn.textContent = 'Loading...'; }

        let response;
        if (spotifyAlbumId) {
            const albumParams = new URLSearchParams({ name: albumName || '', artist: artistName || '' });
            response = await fetch(`/api/spotify/album/${encodeURIComponent(spotifyAlbumId)}?${albumParams}`);
        } else {
            // No Spotify album ID — search for the album by name
            const query = `${artistName} ${albumName}`;
            const searchResp = await fetch('/api/enhanced-search', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ query })
            });
            if (!searchResp.ok) throw new Error('Album search failed');
            const searchData = await searchResp.json();
            const foundAlbum = searchData.spotify_albums?.[0];
            if (!foundAlbum || !foundAlbum.id) {
                showToast(`Could not find "${albumName}" by ${artistName}`, 'warning');
                return;
            }
            const albumParams = new URLSearchParams({ name: foundAlbum.name || albumName, artist: foundAlbum.artist || artistName });
            response = await fetch(`/api/spotify/album/${encodeURIComponent(foundAlbum.id)}?${albumParams}`);
        }

        if (!response.ok) {
            if (response.status === 401) throw new Error('Spotify not authenticated');
            throw new Error(`Failed to load album: ${response.status}`);
        }

        const albumData = await response.json();
        if (!albumData || !albumData.tracks || albumData.tracks.length === 0) {
            showToast(`No tracks available for "${albumName}"`, 'warning');
            return;
        }

        // Close the issue modal first
        closeIssueDetailModal();

        const resolvedAlbumId = albumData.id || spotifyAlbumId || Date.now();
        const virtualPlaylistId = `issue_download_${resolvedAlbumId}`;

        // Enrich tracks with album metadata
        const enrichedTracks = albumData.tracks.map(track => ({
            ...track,
            album: {
                name: albumData.name,
                id: albumData.id,
                album_type: albumData.album_type || 'album',
                images: albumData.images || [],
                release_date: albumData.release_date,
                total_tracks: albumData.total_tracks
            }
        }));

        const playlistName = `[${artistName}] ${albumData.name}`;
        const artistObject = { id: `issue_${artistName}`, name: artistName, image_url: '' };
        const fullAlbumObject = {
            name: albumData.name,
            id: albumData.id,
            album_type: albumData.album_type || 'album',
            images: albumData.images || [],
            image_url: albumData.images?.[0]?.url || null,
            release_date: albumData.release_date,
            total_tracks: albumData.total_tracks,
            artists: albumData.artists || [{ name: artistName }]
        };

        await openDownloadMissingModalForArtistAlbum(
            virtualPlaylistId, playlistName, enrichedTracks, fullAlbumObject, artistObject, true
        );

        // Register download bubble so it appears on the dashboard
        const albumType = fullAlbumObject.album_type || 'album';
        registerArtistDownload(artistObject, fullAlbumObject, virtualPlaylistId, albumType);

    } catch (error) {
        console.error('Issue download error:', error);
        showToast(`Error: ${error.message}`, 'error');
    } finally {
        if (btn) { btn.disabled = false; btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg> Download Album'; }
    }
}

// --- Redownload Library Album (Enhanced View) ---
async function redownloadLibraryAlbum(album, artistName, btn) {
    const albumName = album.title || '';
    const spotifyAlbumId = album.spotify_album_id || '';

    if (!spotifyAlbumId && !albumName) {
        showToast('No album ID or name available for redownload', 'warning');
        return;
    }

    const origText = btn ? btn.innerHTML : '';
    try {
        if (btn) { btn.disabled = true; btn.textContent = 'Loading...'; }

        let response;
        if (spotifyAlbumId) {
            const params = new URLSearchParams({ name: albumName, artist: artistName || '' });
            response = await fetch(`/api/spotify/album/${encodeURIComponent(spotifyAlbumId)}?${params}`);
        }

        // Fallback: search by name if no ID or direct fetch failed
        if (!response || !response.ok) {
            const query = `${artistName || ''} ${albumName}`.trim();
            const searchResp = await fetch('/api/enhanced-search', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ query })
            });
            if (!searchResp.ok) throw new Error('Album search failed');
            const searchData = await searchResp.json();
            const found = searchData.spotify_albums?.[0] || searchData.itunes_albums?.[0];
            if (!found || !found.id) {
                showToast(`Could not find "${albumName}" by ${artistName || 'unknown'}`, 'warning');
                return;
            }
            const params = new URLSearchParams({ name: found.name || albumName, artist: found.artist || artistName || '' });
            response = await fetch(`/api/spotify/album/${encodeURIComponent(found.id)}?${params}`);
        }

        if (!response.ok) throw new Error(`Failed to load album: ${response.status}`);

        const albumData = await response.json();
        if (!albumData || !albumData.tracks || albumData.tracks.length === 0) {
            showToast(`No tracks found for "${albumName}"`, 'warning');
            return;
        }

        const resolvedId = albumData.id || spotifyAlbumId || album.id;
        const virtualPlaylistId = `library_redownload_${resolvedId}`;
        const playlistName = `[${artistName || 'Unknown'}] ${albumData.name}`;

        const enrichedTracks = albumData.tracks.map(track => ({
            ...track,
            album: {
                name: albumData.name,
                id: albumData.id,
                album_type: albumData.album_type || 'album',
                images: albumData.images || [],
                release_date: albumData.release_date,
                total_tracks: albumData.total_tracks
            }
        }));

        const enhancedArtist = artistDetailPageState.enhancedData?.artist;
        const artistObject = {
            id: artistDetailPageState.currentArtistId || `library_${artistName || album.id}`,
            name: artistName || '',
            image_url: enhancedArtist?.thumb_url || ''
        };
        const fullAlbumObject = {
            name: albumData.name,
            id: albumData.id,
            album_type: albumData.album_type || 'album',
            images: albumData.images || [],
            image_url: albumData.images?.[0]?.url || null,
            release_date: albumData.release_date,
            total_tracks: albumData.total_tracks,
            artists: albumData.artists || [{ name: artistName || '' }]
        };

        await openDownloadMissingModalForArtistAlbum(
            virtualPlaylistId, playlistName, enrichedTracks, fullAlbumObject, artistObject, true
        );

        // Register download bubble so it appears on the dashboard
        const albumType = fullAlbumObject.album_type || 'album';
        registerArtistDownload(artistObject, fullAlbumObject, virtualPlaylistId, albumType);

    } catch (error) {
        console.error('Redownload album error:', error);
        showToast(`Error: ${error.message}`, 'error');
    } finally {
        if (btn) { btn.disabled = false; btn.innerHTML = origText; }
    }
}

// --- Issue Action: Add to Wishlist ---
async function issueAddToWishlist(spotifyAlbumId, artistName, albumName) {
    const btn = document.getElementById('issue-action-wishlist');
    if (!spotifyAlbumId && (!artistName || !albumName)) {
        showToast('No album ID or artist/album info available', 'warning');
        return;
    }
    try {
        if (btn) { btn.disabled = true; btn.textContent = 'Loading...'; }

        let response;
        if (spotifyAlbumId) {
            const albumParams = new URLSearchParams({ name: albumName || '', artist: artistName || '' });
            response = await fetch(`/api/spotify/album/${encodeURIComponent(spotifyAlbumId)}?${albumParams}`);
        } else {
            // No Spotify album ID — search for the album by name
            const query = `${artistName} ${albumName}`;
            const searchResp = await fetch('/api/enhanced-search', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ query })
            });
            if (!searchResp.ok) throw new Error('Album search failed');
            const searchData = await searchResp.json();
            const foundAlbum = searchData.spotify_albums?.[0];
            if (!foundAlbum || !foundAlbum.id) {
                showToast(`Could not find "${albumName}" by ${artistName}`, 'warning');
                return;
            }
            const albumParams = new URLSearchParams({ name: foundAlbum.name || albumName, artist: foundAlbum.artist || artistName });
            response = await fetch(`/api/spotify/album/${encodeURIComponent(foundAlbum.id)}?${albumParams}`);
        }

        if (!response.ok) throw new Error(`Failed to load album: ${response.status}`);

        const albumData = await response.json();
        if (!albumData || !albumData.tracks || albumData.tracks.length === 0) {
            showToast(`No tracks available for "${albumName}"`, 'warning');
            return;
        }

        // Close issue modal and open wishlist modal
        closeIssueDetailModal();

        const albumArtists = albumData.artists || [{ name: artistName }];
        const album = {
            name: albumData.name,
            id: albumData.id,
            album_type: albumData.album_type || 'album',
            images: albumData.images || [],
            release_date: albumData.release_date,
            total_tracks: albumData.total_tracks,
            artists: albumArtists
        };
        const artist = { id: null, name: artistName };

        // Enrich tracks with album metadata — use album artist for wishlist grouping
        // (Spotify returns per-track artists which can differ on compilations/soundtracks)
        const tracks = albumData.tracks.map(t => ({
            ...t,
            artists: albumArtists,
            album: album
        }));

        await openAddToWishlistModal(album, artist, tracks, albumData.album_type || 'album');

    } catch (error) {
        console.error('Issue wishlist error:', error);
        showToast(`Error: ${error.message}`, 'error');
    } finally {
        if (btn) { btn.disabled = false; btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21l-7-5-7 5V5a2 2 0 012-2h10a2 2 0 012 2z"/></svg> Add to Wishlist'; }
    }
}

async function updateIssueStatus(issueId, newStatus) {
    const payload = { status: newStatus };

    // Include admin response if present
    const responseInput = document.getElementById('issue-detail-response-input');
    if (responseInput) {
        payload.admin_response = responseInput.value.trim();
    }

    try {
        const resp = await fetch(`/api/issues/${issueId}`, {
            method: 'PUT',
            headers: _issueHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify(payload),
        });
        const data = await resp.json();
        if (data.success) {
            showToast(`Issue ${newStatus === 'resolved' ? 'resolved' : newStatus === 'dismissed' ? 'dismissed' : newStatus === 'in_progress' ? 'marked in progress' : 'reopened'}`, 'success');
            closeIssueDetailModal();
            // Refresh if on issues page
            const issuesPage = document.getElementById('issues-page');
            if (issuesPage && issuesPage.classList.contains('active')) {
                loadIssuesPage();
            }
            loadIssuesBadge();
        } else {
            showToast(data.error || 'Failed to update issue', 'error');
        }
    } catch (e) {
        console.error('Failed to update issue:', e);
        showToast('Failed to update issue', 'error');
    }
}

async function deleteIssue(issueId) {
    if (!confirm('Are you sure you want to delete this issue?')) return;
    try {
        const resp = await fetch(`/api/issues/${issueId}`, { method: 'DELETE', headers: _issueHeaders() });
        const data = await resp.json();
        if (data.success) {
            showToast('Issue deleted', 'success');
            closeIssueDetailModal();
            const issuesPage = document.getElementById('issues-page');
            if (issuesPage && issuesPage.classList.contains('active')) {
                loadIssuesPage();
            }
            loadIssuesBadge();
        } else {
            showToast(data.error || 'Failed to delete issue', 'error');
        }
    } catch (e) {
        console.error('Failed to delete issue:', e);
        showToast('Failed to delete issue', 'error');
    }
}

function closeIssueDetailModal() {
    const overlay = document.getElementById('issue-detail-overlay');
    if (overlay) overlay.classList.add('hidden');
}

async function loadIssuesBadge() {
    try {
        const resp = await fetch('/api/issues/counts', { headers: _issueHeaders() });
        const data = await resp.json();
        if (!data.success) return;
        const badge = document.getElementById('issues-nav-badge');
        if (badge) {
            const openCount = data.counts.open || 0;
            badge.textContent = openCount;
            badge.classList.toggle('hidden', openCount === 0);
        }
    } catch (e) { }
}

// ===== END ISSUES PAGE =====

// --- Helpers ---

function _esc(str) {
    if (!str) return '';
    const d = document.createElement('div');
    d.textContent = str;
    return d.innerHTML;
}

function _escAttr(str) {
    if (!str) return '';
    return String(str).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/'/g, '&#39;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// ===== ENHANCE QUALITY MODAL =====

let _enhanceQualityData = null;
let _enhanceArtistId = null;

const ENHANCE_TIER_MAP = {
    'lossless': { num: 1, label: 'Lossless', cssClass: 'lossless' },
    'high_lossy': { num: 2, label: 'High Lossy', cssClass: 'high-lossy' },
    'standard_lossy': { num: 3, label: 'Standard Lossy', cssClass: 'standard-lossy' },
    'low_lossy': { num: 4, label: 'Low Lossy', cssClass: 'low-lossy' },
    'unknown': { num: 999, label: 'Unknown', cssClass: 'unknown' },
};

async function checkArtistEnhanceEligibility(artistId) {
    const btn = document.getElementById('library-artist-enhance-btn');
    if (!btn) return;
    btn.classList.add('hidden');
    _enhanceArtistId = artistId;

    try {
        const resp = await fetch(`/api/library/artist/${artistId}/quality-analysis`);
        if (!resp.ok) return;
        const data = await resp.json();
        if (!data.success || !data.tracks || data.tracks.length === 0) return;

        _enhanceQualityData = data;

        // Show button if any tracks are below the user's min acceptable tier
        const minTier = data.min_acceptable_tier || 1;
        const belowCount = data.tracks.filter(t => t.tier_num > minTier).length;
        if (belowCount > 0) {
            btn.classList.remove('hidden');
            btn.querySelector('.enhance-text').textContent = `Enhance Quality (${belowCount})`;
        }
    } catch (e) {
        console.debug('Enhance eligibility check failed:', e);
    }
}

async function playArtistRadio() {
    try {
        const artistId = artistDetailPageState.currentArtistId;
        const artistName = artistDetailPageState.currentArtistName || '';
        if (!artistId) {
            showToast('No artist selected', 'error');
            return;
        }

        // Get tracks from this artist's library
        const resp = await fetch(`/api/library/artist/${artistId}/enhanced`);
        if (!resp.ok) throw new Error('Failed to load artist data');
        const data = await resp.json();
        if (!data.success) throw new Error(data.error || 'Failed');

        // Collect all tracks with file paths
        const allTracks = [];
        for (const album of (data.albums || [])) {
            for (const track of (album.tracks || [])) {
                if (track.file_path) {
                    allTracks.push({ track, album });
                }
            }
        }

        if (!allTracks.length) {
            showToast('No playable tracks found for this artist', 'error');
            return;
        }

        // Pick a random track
        const random = allTracks[Math.floor(Math.random() * allTracks.length)];
        const albumArt = random.album.thumb_url || data.artist?.thumb_url || null;

        // Clear existing queue and disable radio before starting fresh
        npRadioMode = false;
        clearQueue();
        if (audioPlayer && !audioPlayer.paused) {
            audioPlayer.pause();
        }

        // Play the track first, then enable radio mode after a short delay
        // so currentTrack is set and the radio queue fill triggers
        playLibraryTrack({
            id: random.track.id,
            title: random.track.title,
            file_path: random.track.file_path,
            bitrate: random.track.bitrate,
            artist_id: artistId,
            album_id: random.album.id,
        }, random.album.title || '', artistName);

        // Enable radio mode after track starts loading
        setTimeout(() => {
            npRadioMode = true;
            const radioBtn = document.querySelector('.np-radio-btn');
            if (radioBtn) radioBtn.classList.add('active');
        }, 1000);

        showToast(`Playing ${artistName} radio — similar tracks will auto-queue`, 'success');
    } catch (e) {
        showToast(`Failed to start artist radio: ${e.message}`, 'error');
    }
}

async function writeArtistImageToDisk(overwrite) {
    // Issue #572: writes `artist.jpg` to the artist's folder so
    // Navidrome (which has no API for artist images) picks up a
    // real photo on its next scan. Plex/Jellyfin also read this
    // file as a fallback, so it's safe to run regardless of which
    // server is active.
    const artistId = artistDetailPageState.currentArtistId;
    if (!artistId) {
        showToast('No artist selected', 'error');
        return;
    }
    const btn = document.getElementById('library-artist-write-image-btn');
    if (btn) {
        btn.disabled = true;
        const txt = btn.querySelector('.write-image-text');
        if (txt) txt.textContent = 'Writing…';
    }
    try {
        let resp = await fetch(`/api/artist/${artistId}/write-image-to-disk`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ overwrite: !!overwrite }),
        });
        let data = await resp.json();

        // Default protect-existing path: surface a confirm so the user
        // explicitly opts in to clobbering a hand-picked artist.jpg.
        if (!data.success && /already exists/i.test(data.error || '')) {
            if (confirm('artist.jpg already exists in the folder. Overwrite with the new image?')) {
                resp = await fetch(`/api/artist/${artistId}/write-image-to-disk`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ overwrite: true }),
                });
                data = await resp.json();
            } else {
                showToast('Cancelled — existing artist.jpg kept', 'info');
                return;
            }
        }

        if (!data.success) {
            showToast(`Failed: ${data.error || 'Unknown error'}`, 'error');
            return;
        }
        const scan = data.scan_triggered ? ' (Navidrome scan triggered)' : '';
        showToast(`artist.jpg written${scan}`, 'success');
    } catch (e) {
        showToast(`Write failed: ${e.message}`, 'error');
    } finally {
        if (btn) {
            btn.disabled = false;
            const txt = btn.querySelector('.write-image-text');
            if (txt) txt.textContent = 'Write Artist Image';
        }
    }
}
window.writeArtistImageToDisk = writeArtistImageToDisk;


function openEnhanceQualityModal() {
    if (!_enhanceQualityData) return;
    const data = _enhanceQualityData;

    // Remove existing modal if any
    const existing = document.getElementById('enhance-quality-overlay');
    if (existing) existing.remove();

    const overlay = document.createElement('div');
    overlay.id = 'enhance-quality-overlay';
    overlay.className = 'enhance-modal-overlay';
    overlay.onclick = (e) => { if (e.target === overlay) closeEnhanceQualityModal(); };

    const minTier = data.min_acceptable_tier || 1;
    const summary = data.quality_summary || {};

    overlay.innerHTML = `
        <div class="enhance-modal" onclick="event.stopPropagation()">
            <div class="enhance-modal-header">
                <h3>⚡ Enhance Quality — ${_esc(data.artist_name)}</h3>
                <button class="enhance-modal-close" onclick="closeEnhanceQualityModal()">&times;</button>
            </div>
            <div class="enhance-summary-bar">
                ${_buildEnhanceSummaryChips(summary)}
            </div>
            <div class="enhance-controls">
                <div class="enhance-tier-selector">
                    <label>Upgrade tracks below:</label>
                    <select id="enhance-tier-dropdown" onchange="updateEnhanceThreshold(parseInt(this.value))">
                        <option value="1" ${minTier <= 1 ? 'selected' : ''}>Lossless (FLAC/WAV)</option>
                        <option value="2" ${minTier === 2 ? 'selected' : ''}>High Lossy (OGG/Opus)</option>
                        <option value="3" ${minTier === 3 ? 'selected' : ''}>Standard Lossy (M4A/AAC)</option>
                        <option value="4" ${minTier >= 4 ? 'selected' : ''}>Low Lossy (MP3/WMA)</option>
                    </select>
                </div>
                <div class="enhance-select-controls">
                    <button class="enhance-select-btn" onclick="enhanceSelectAll(true)">Select All Below</button>
                    <button class="enhance-select-btn" onclick="enhanceSelectAll(false)">Deselect All</button>
                    <span class="enhance-selected-count" id="enhance-selected-count">0 selected</span>
                </div>
            </div>
            <div class="enhance-modal-body">
                <table class="enhance-track-table">
                    <thead>
                        <tr>
                            <th></th>
                            <th>#</th>
                            <th>Title</th>
                            <th>Album</th>
                            <th>Format</th>
                            <th>Bitrate</th>
                        </tr>
                    </thead>
                    <tbody id="enhance-track-tbody">
                    </tbody>
                </table>
            </div>
            <div class="enhance-modal-footer">
                <div class="enhance-footer-info" id="enhance-footer-info"></div>
                <div class="enhance-footer-actions">
                    <button class="enhance-btn secondary" onclick="closeEnhanceQualityModal()">Cancel</button>
                    <button class="enhance-btn primary" id="enhance-submit-btn" onclick="submitEnhanceQuality()" disabled>
                        ⚡ Enhance 0 Tracks
                    </button>
                </div>
            </div>
        </div>
    `;

    document.body.appendChild(overlay);
    renderEnhanceTrackRows(minTier);
}

function _buildEnhanceSummaryChips(summary) {
    const chips = [
        { key: 'lossless', label: 'FLAC', cssClass: 'lossless' },
        { key: 'high_lossy', label: 'OGG/Opus', cssClass: 'high-lossy' },
        { key: 'standard_lossy', label: 'M4A/AAC', cssClass: 'standard-lossy' },
        { key: 'low_lossy', label: 'MP3/WMA', cssClass: 'low-lossy' },
    ];
    return chips
        .filter(c => (summary[c.key] || 0) > 0)
        .map(c => `
            <div class="enhance-summary-chip ${c.cssClass}">
                <span class="chip-count">${summary[c.key]}</span>
                <span class="chip-label">${c.label}</span>
            </div>
        `).join('');
}

function renderEnhanceTrackRows(thresholdTier) {
    const tbody = document.getElementById('enhance-track-tbody');
    if (!tbody || !_enhanceQualityData) return;

    const tracks = _enhanceQualityData.tracks;
    // Sort: below-threshold first, then by album + track number
    const sorted = [...tracks].sort((a, b) => {
        const aBt = a.tier_num > thresholdTier ? 0 : 1;
        const bBt = b.tier_num > thresholdTier ? 0 : 1;
        if (aBt !== bBt) return aBt - bBt;
        const albumCmp = (a.album_title || '').localeCompare(b.album_title || '');
        if (albumCmp !== 0) return albumCmp;
        return (a.disc_number || 1) * 1000 + (a.track_number || 0) - ((b.disc_number || 1) * 1000 + (b.track_number || 0));
    });

    tbody.innerHTML = sorted.map(track => {
        const isBelow = track.tier_num > thresholdTier;
        const tierInfo = ENHANCE_TIER_MAP[track.tier_name] || ENHANCE_TIER_MAP['unknown'];
        const bitrateStr = track.bitrate ? `${track.bitrate} kbps` : '-';
        return `
            <tr class="enhance-track-row ${isBelow ? 'below-threshold' : 'above-threshold'}"
                data-track-id="${_escAttr(track.track_id)}" data-tier="${track.tier_num}">
                <td><input type="checkbox" class="enhance-track-check"
                    ${isBelow ? 'checked' : ''} onchange="updateEnhanceSelectedCount()"></td>
                <td>${track.track_number || '-'}</td>
                <td>${_esc(track.title)}</td>
                <td>${_esc(track.album_title)}</td>
                <td><span class="enhance-format-badge ${tierInfo.cssClass}">${_esc(track.format)}</span></td>
                <td><span class="enhance-bitrate">${bitrateStr}</span></td>
            </tr>
        `;
    }).join('');

    updateEnhanceSelectedCount();
}

function updateEnhanceThreshold(tierNum) {
    const rows = document.querySelectorAll('.enhance-track-row');
    rows.forEach(row => {
        const trackTier = parseInt(row.dataset.tier);
        const isBelow = trackTier > tierNum;
        const cb = row.querySelector('.enhance-track-check');

        row.classList.toggle('below-threshold', isBelow);
        row.classList.toggle('above-threshold', !isBelow);
        if (cb) cb.checked = isBelow;
    });
    updateEnhanceSelectedCount();
}

function enhanceSelectAll(select) {
    const thresholdTier = parseInt(document.getElementById('enhance-tier-dropdown')?.value || '1');
    const checks = document.querySelectorAll('.enhance-track-check');
    checks.forEach(cb => {
        const row = cb.closest('.enhance-track-row');
        const trackTier = parseInt(row?.dataset.tier || '999');
        if (select) {
            cb.checked = trackTier > thresholdTier;
        } else {
            cb.checked = false;
        }
    });
    updateEnhanceSelectedCount();
}

function updateEnhanceSelectedCount() {
    const checks = document.querySelectorAll('.enhance-track-check:checked');
    const count = checks.length;
    const countEl = document.getElementById('enhance-selected-count');
    const submitBtn = document.getElementById('enhance-submit-btn');

    if (countEl) countEl.textContent = `${count} selected`;
    if (submitBtn) {
        submitBtn.textContent = `⚡ Enhance ${count} Track${count !== 1 ? 's' : ''}`;
        submitBtn.disabled = count === 0;
    }
}

async function submitEnhanceQuality() {
    const checks = document.querySelectorAll('.enhance-track-check:checked');
    const trackIds = [];
    checks.forEach(cb => {
        const row = cb.closest('.enhance-track-row');
        if (row?.dataset.trackId) trackIds.push(row.dataset.trackId);
    });

    if (trackIds.length === 0) return;

    const submitBtn = document.getElementById('enhance-submit-btn');
    const footerInfo = document.getElementById('enhance-footer-info');
    if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.innerHTML = '<span class="enhance-spinner"></span>Processing...';
    }
    if (footerInfo) footerInfo.textContent = 'Matching tracks across metadata sources and adding to wishlist...';

    try {
        const resp = await fetch(`/api/library/artist/${_enhanceArtistId}/enhance`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ track_ids: trackIds })
        });

        const result = await resp.json();

        if (result.success) {
            const msg = `${result.enhanced_count} track${result.enhanced_count !== 1 ? 's' : ''} queued for enhancement`;
            if (footerInfo) footerInfo.textContent = msg;

            showToast(msg + (result.failed_count > 0 ? ` (${result.failed_count} failed)` : ''), 'success');

            // Update button count
            const enhBtn = document.getElementById('library-artist-enhance-btn');
            if (enhBtn && result.enhanced_count > 0) {
                const remaining = trackIds.length - result.enhanced_count;
                if (remaining <= 0) {
                    enhBtn.classList.add('hidden');
                }
            }

            if (submitBtn) {
                submitBtn.textContent = '✅ Done';
                submitBtn.disabled = true;
            }

            // Auto-close after short delay
            setTimeout(() => closeEnhanceQualityModal(), 1500);
        } else {
            throw new Error(result.error || 'Enhancement failed');
        }
    } catch (e) {
        console.error('Enhance quality error:', e);
        showToast(`Enhancement failed: ${e.message}`, 'error');
        if (submitBtn) {
            submitBtn.textContent = `⚡ Enhance ${trackIds.length} Tracks`;
            submitBtn.disabled = false;
        }
        if (footerInfo) footerInfo.textContent = '';
    }
}

function closeEnhanceQualityModal() {
    const overlay = document.getElementById('enhance-quality-overlay');
    if (overlay) {
        overlay.classList.add('hidden');
        setTimeout(() => overlay.remove(), 300);
    }
}

// Global exports
window.openEnhanceQualityModal = openEnhanceQualityModal;
window.closeEnhanceQualityModal = closeEnhanceQualityModal;
window.updateEnhanceThreshold = updateEnhanceThreshold;
window.enhanceSelectAll = enhanceSelectAll;
window.updateEnhanceSelectedCount = updateEnhanceSelectedCount;
window.submitEnhanceQuality = submitEnhanceQuality;

// ===== END ENHANCE QUALITY MODAL =====
