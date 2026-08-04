// == API RATE MONITOR GAUGES   ==
// ===============================

const _rateMonitorState = {};
const _RATE_GAUGE_SERVICES = [
    'spotify', 'itunes', 'deezer', 'jiosaavn', 'lastfm', 'genius',
    'musicbrainz', 'audiodb', 'tidal', 'qobuz', 'discogs', 'amazon',
];
const _RATE_GAUGE_LABELS = {
    spotify: 'Spotify', itunes: 'Apple Music', deezer: 'Deezer', jiosaavn: 'JioSaavn',
    lastfm: 'Last.fm', genius: 'Genius', musicbrainz: 'MusicBrainz',
    audiodb: 'AudioDB', tidal: 'Tidal', qobuz: 'Qobuz', discogs: 'Discogs',
    amazon: 'Amazon Music',
};
const _RATE_GAUGE_COLORS = {
    spotify: '#1DB954', itunes: '#FC3C44', deezer: '#A238FF', jiosaavn: '#2BC5B4',
    lastfm: '#D51007', genius: '#FFFF64', musicbrainz: '#BA478F',
    audiodb: '#00BCD4', tidal: '#00FFFF', qobuz: '#FF6B35', discogs: '#D4A574',
    amazon: '#FF9900',
};


function _visibleRateGaugeServices() {
    if (typeof isJiosaavnExperimentalEnabled === 'function' && isJiosaavnExperimentalEnabled()) {
        return _RATE_GAUGE_SERVICES;
    }
    return _RATE_GAUGE_SERVICES.filter(svc => svc !== 'jiosaavn');
}

function _removeJiosaavnRateGauge() {
    // State-only since the flip — the jiosaavn BAR is React-owned
    // (rate-equalizer.tsx drops it on the experimental toggle); the old
    // .remove() calls would rip nodes out of React's tree.
    delete _rateMonitorState.jiosaavn;
}

function refreshRateMonitorExperimentalVisibility() {
    if (typeof isJiosaavnExperimentalEnabled === 'function' && !isJiosaavnExperimentalEnabled()) {
        _removeJiosaavnRateGauge();
    }
}


function _handleRateMonitorUpdate(data) {
    // Re-broadcast for the React dashboard's equalizer (tools-seam rule).
    window.dispatchEvent(new CustomEvent('ss:rate-monitor', { detail: data }));
    // The equalizer is React-rendered from that frame since the dashboard
    // flip (rate-equalizer.tsx). What stays vanilla is the DETAIL MODAL
    // (_openRateModal, opened from the React bars through the window seam) —
    // record every service's latest payload for it. The old renderer only
    // recorded state while its grid happened to be visible; the modal wants
    // it regardless.
    if (typeof isJiosaavnExperimentalEnabled === 'function' && !isJiosaavnExperimentalEnabled()) {
        _removeJiosaavnRateGauge();
    }
    for (const svc of _visibleRateGaugeServices()) {
        if (data[svc]) _rateMonitorState[svc] = data[svc];
    }
}


// ── Equalizer-bar renderer (dashboard) ─────────────────────────────────
//
// VU-meter aesthetic: one vertical bar per service. Bar height = current
// rate / limit. Service brand color fades up the bar with an animated
// glow at the tip when active. Click opens the same detail modal the
// speedometer used. Symmetric by design — any service count fits one
// flex row regardless of viewport.

// ── Rate Monitor Detail Modal ──

let _rateModalService = null;
let _rateModalInterval = null;

function _openRateModal(serviceKey) {
    _rateModalService = serviceKey;
    const label = _RATE_GAUGE_LABELS[serviceKey] || serviceKey;
    const accent = _RATE_GAUGE_COLORS[serviceKey] || '#888';

    let overlay = document.getElementById('rate-modal-overlay');
    if (overlay) overlay.remove();

    overlay = document.createElement('div');
    overlay.id = 'rate-modal-overlay';
    overlay.className = 'modal-overlay';
    overlay.onclick = (e) => { if (e.target === overlay) _closeRateModal(); };

    const isSpotify = serviceKey === 'spotify';
    const currentData = _rateMonitorState[serviceKey] || {};

    overlay.innerHTML = `
        <div class="rate-modal">
            <div class="rate-modal-header">
                <div class="rate-modal-header-info">
                    <div class="rate-modal-header-dot" style="background:${accent}"></div>
                    <div>
                        <h3>${label}</h3>
                        <span class="rate-modal-header-sub">${currentData.cpm || 0} calls/min — limit ${currentData.limit || '?'}/min</span>
                    </div>
                </div>
                <button class="watch-all-close" onclick="_closeRateModal()">&times;</button>
            </div>
            <div class="rate-modal-body">
                <div class="rate-modal-section-title">24-Hour Call History</div>
                <div class="rate-modal-chart-wrap">
                    <canvas id="rate-modal-chart" width="700" height="280"></canvas>
                    <div class="rate-modal-chart-legend" id="rate-modal-chart-legend"></div>
                </div>
                ${isSpotify ? '<div class="rate-modal-section-title">Per-Endpoint Breakdown</div><div class="rate-modal-endpoints" id="rate-modal-endpoints"></div>' : ''}
            </div>
        </div>
    `;
    document.body.appendChild(overlay);

    // Fetch main history + per-endpoint histories for Spotify
    const historyPromises = [
        fetch(`/api/rate-monitor/history/${serviceKey}`).then(r => r.json())
    ];
    if (isSpotify) {
        const activeEps = Object.keys(_rateMonitorState.spotify?.endpoints || {});
        for (const ep of activeEps) {
            historyPromises.push(
                fetch(`/api/rate-monitor/history/spotify:${ep}`).then(r => r.json()).catch(() => null)
            );
        }
    }
    Promise.all(historyPromises).then(results => {
        const main = results[0];
        const epHistories = isSpotify ? results.slice(1).filter(Boolean) : [];
        _renderRateChart(main.history || [], main.rate_limit || 60, accent, epHistories);
    }).catch(() => { });

    if (isSpotify) {
        _updateSpotifyEndpoints();
        _rateModalInterval = setInterval(_updateSpotifyEndpoints, 1000);
    }
}

function _closeRateModal() {
    const overlay = document.getElementById('rate-modal-overlay');
    if (overlay) overlay.remove();
    if (_rateModalInterval) { clearInterval(_rateModalInterval); _rateModalInterval = null; }
    _rateModalService = null;
}

function _renderRateChart(history, rateLimit, accent, epHistories = []) {
    const canvas = document.getElementById('rate-modal-chart');
    if (!canvas) return;

    // HiDPI support
    const dpr = window.devicePixelRatio || 1;
    const W = 700, H = 280;
    canvas.width = W * dpr;
    canvas.height = H * dpr;
    canvas.style.width = W + 'px';
    canvas.style.height = H + 'px';
    const ctx = canvas.getContext('2d');
    ctx.scale(dpr, dpr);

    const pad = { top: 24, right: 24, bottom: 36, left: 50 };
    const plotW = W - pad.left - pad.right;
    const plotH = H - pad.top - pad.bottom;

    ctx.clearRect(0, 0, W, H);

    // Build data points
    const now = Math.floor(Date.now() / 1000);
    const start = now - 86400;
    const points = [];

    if (history.length > 0) {
        const histMap = new Map(history.map(h => [h[0], h[1]]));
        for (let t = start; t <= now; t += 300) {
            const bucket = Math.floor(t / 60) * 60;
            let sum = 0;
            for (let m = bucket; m < bucket + 300; m += 60) sum += histMap.get(m) || 0;
            points.push({ t, v: sum / 5 });
        }
    }

    const maxVal = Math.max(rateLimit * 1.15, ...points.map(p => p.v), 1);

    // Grid lines (horizontal)
    ctx.strokeStyle = 'rgba(255,255,255,0.04)';
    ctx.lineWidth = 1;
    for (let i = 1; i <= 4; i++) {
        const y = pad.top + plotH * (1 - i / 4);
        ctx.beginPath();
        ctx.moveTo(pad.left, y);
        ctx.lineTo(pad.left + plotW, y);
        ctx.stroke();
    }

    // Danger zone band
    const dangerY = pad.top + plotH * (1 - rateLimit / maxVal);
    const grad = ctx.createLinearGradient(0, pad.top, 0, dangerY);
    grad.addColorStop(0, 'rgba(239, 68, 68, 0.08)');
    grad.addColorStop(1, 'rgba(239, 68, 68, 0.02)');
    ctx.fillStyle = grad;
    ctx.fillRect(pad.left, pad.top, plotW, dangerY - pad.top);

    // Rate limit line
    ctx.strokeStyle = 'rgba(239, 68, 68, 0.5)';
    ctx.setLineDash([8, 5]);
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(pad.left, dangerY);
    ctx.lineTo(pad.left + plotW, dangerY);
    ctx.stroke();
    ctx.setLineDash([]);

    ctx.fillStyle = 'rgba(239, 68, 68, 0.6)';
    ctx.font = '10px -apple-system, sans-serif';
    ctx.textAlign = 'left';
    ctx.fillText(`Rate limit: ${rateLimit}/min`, pad.left + 6, dangerY - 6);

    // Draw area fill + line
    if (points.length > 1) {
        // Area gradient fill
        const areaGrad = ctx.createLinearGradient(0, pad.top, 0, pad.top + plotH);
        // Parse accent to rgba
        areaGrad.addColorStop(0, accent + '30');
        areaGrad.addColorStop(1, accent + '05');

        ctx.beginPath();
        points.forEach((p, i) => {
            const x = pad.left + (i / (points.length - 1)) * plotW;
            const y = pad.top + plotH * (1 - p.v / maxVal);
            if (i === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
        });
        ctx.lineTo(pad.left + plotW, pad.top + plotH);
        ctx.lineTo(pad.left, pad.top + plotH);
        ctx.closePath();
        ctx.fillStyle = areaGrad;
        ctx.fill();

        // Line
        ctx.beginPath();
        points.forEach((p, i) => {
            const x = pad.left + (i / (points.length - 1)) * plotW;
            const y = pad.top + plotH * (1 - p.v / maxVal);
            if (i === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
        });
        ctx.strokeStyle = accent;
        ctx.lineWidth = 2;
        ctx.lineJoin = 'round';
        ctx.stroke();

        // Glow effect
        ctx.shadowColor = accent;
        ctx.shadowBlur = 8;
        ctx.stroke();
        ctx.shadowBlur = 0;
    }

    // Per-endpoint lines (Spotify breakdown)
    const legendEl = document.getElementById('rate-modal-chart-legend');
    if (epHistories.length > 0) {
        const epColors = ['#1DB954', '#FF6B6B', '#4ECDC4', '#FFE66D', '#A78BFA', '#F97316', '#06B6D4', '#EC4899', '#F472B6', '#34D399'];
        const legendItems = [];

        epHistories.forEach((epData, idx) => {
            if (!epData || !epData.history || epData.history.length === 0) return;
            const epName = (epData.service || '').replace('spotify:', '').replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
            const color = epColors[idx % epColors.length];
            legendItems.push({ name: epName, color });

            const histMap = new Map(epData.history.map(h => [h[0], h[1]]));
            const epPoints = [];
            for (let t = start; t <= now; t += 300) {
                const bucket = Math.floor(t / 60) * 60;
                let sum = 0;
                for (let m = bucket; m < bucket + 300; m += 60) sum += histMap.get(m) || 0;
                epPoints.push({ t, v: sum / 5 });
            }

            if (epPoints.length > 1) {
                ctx.beginPath();
                epPoints.forEach((p, i) => {
                    const x = pad.left + (i / (epPoints.length - 1)) * plotW;
                    const y = pad.top + plotH * (1 - p.v / maxVal);
                    if (i === 0) ctx.moveTo(x, y);
                    else ctx.lineTo(x, y);
                });
                ctx.strokeStyle = color + 'BB';
                ctx.lineWidth = 1.5;
                ctx.lineJoin = 'round';
                ctx.stroke();
            }
        });

        // HTML legend below chart
        if (legendEl && legendItems.length > 0) {
            legendEl.innerHTML = legendItems.map(item =>
                `<span class="rate-chart-legend-item"><span class="rate-chart-legend-dot" style="background:${item.color}"></span>${item.name}</span>`
            ).join('');
        }
    } else if (legendEl) {
        legendEl.innerHTML = '';
    }

    // X-axis labels
    ctx.fillStyle = 'rgba(255,255,255,0.3)';
    ctx.font = '10px -apple-system, sans-serif';
    ctx.textAlign = 'center';
    for (let i = 0; i <= 6; i++) {
        const t = start + (86400 * i / 6);
        const x = pad.left + (i / 6) * plotW;
        const d = new Date(t * 1000);
        const hr = d.getHours();
        const label = hr === 0 ? '12am' : hr < 12 ? `${hr}am` : hr === 12 ? '12pm' : `${hr - 12}pm`;
        ctx.fillText(label, x, H - 10);
        // Subtle vertical grid
        ctx.strokeStyle = 'rgba(255,255,255,0.03)';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(x, pad.top);
        ctx.lineTo(x, pad.top + plotH);
        ctx.stroke();
    }

    // Y-axis labels
    ctx.fillStyle = 'rgba(255,255,255,0.3)';
    ctx.textAlign = 'right';
    ctx.font = '10px -apple-system, sans-serif';
    for (let i = 0; i <= 4; i++) {
        const v = maxVal * i / 4;
        const y = pad.top + plotH * (1 - i / 4);
        ctx.fillText(Math.round(v), pad.left - 8, y + 4);
    }

    // Empty state
    if (points.length === 0) {
        ctx.fillStyle = 'rgba(255,255,255,0.15)';
        ctx.font = '13px -apple-system, sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText('No call history yet — data populates as API calls are made', W / 2, H / 2);
    }
}

function _updateSpotifyEndpoints() {
    const container = document.getElementById('rate-modal-endpoints');
    if (!container) return;
    const endpoints = _rateMonitorState.spotify?.endpoints || {};
    const entries = Object.entries(endpoints).sort((a, b) => b[1] - a[1]);

    if (entries.length === 0) {
        container.innerHTML = '<div class="rate-modal-ep-empty">No active Spotify endpoints — start an enrichment worker or search to see activity</div>';
        return;
    }

    const limit = _rateMonitorState.spotify?.limit || 171;
    container.innerHTML = entries.map(([ep, cpm]) => {
        const pct = Math.min(cpm / limit * 100, 100);
        const name = ep.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
        const color = pct > 80 ? '#ef4444' : pct > 60 ? '#eab308' : '#1DB954';
        return `<div class="rate-modal-ep">
            <span class="rate-modal-ep-name">${name}</span>
            <div class="rate-modal-ep-bar"><div class="rate-modal-ep-fill" style="width:${pct}%;background:${color}"></div></div>
            <span class="rate-modal-ep-value">${Math.round(cpm)}/min</span>
        </div>`;
    }).join('');
}

// --- Watchlist Functions ---

/**
 * Toggle an artist's watchlist status
 */
async function toggleWatchlist(event, artistId, artistName) {
    // Prevent event bubbling to parent card
    event.stopPropagation();

    const button = event.currentTarget;
    const icon = button.querySelector('.watchlist-icon');
    const text = button.querySelector('.watchlist-text');

    // Show loading state
    const originalText = text.textContent;
    text.textContent = 'Loading...';
    button.disabled = true;

    try {
        // Check current status
        const checkResponse = await fetch('/api/watchlist/check', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ artist_id: artistId })
        });

        const checkData = await checkResponse.json();
        if (!checkData.success) {
            throw new Error(checkData.error || 'Failed to check watchlist status');
        }

        const isWatching = checkData.is_watching;

        // Toggle watchlist status
        const endpoint = isWatching ? '/api/watchlist/remove' : '/api/watchlist/add';
        const payload = isWatching ?
            { artist_id: artistId } :
            { artist_id: artistId, artist_name: artistName };

        const response = await fetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        const data = await response.json();
        if (!data.success) {
            throw new Error(data.error || 'Failed to update watchlist');
        }

        // Update button appearance
        const gearBtn = button.parentElement?.querySelector('.watchlist-settings-btn');
        if (isWatching) {
            // Was watching, now removed
            icon.textContent = '👁️';
            text.textContent = 'Add to Watchlist';
            button.classList.remove('watching');
            if (gearBtn) gearBtn.classList.add('hidden');
            console.log(`❌ Removed ${artistName} from watchlist`);
        } else {
            // Was not watching, now added
            icon.textContent = '👁️';
            text.textContent = 'Watching...';
            button.classList.add('watching');
            if (gearBtn) gearBtn.classList.remove('hidden');
            console.log(`✅ Added ${artistName} to watchlist`);
        }

        // Update dashboard watchlist count
        updateWatchlistButtonCount();

    } catch (error) {
        console.error('Error toggling watchlist:', error);
        text.textContent = originalText;

        // Show error feedback
        const originalBackground = button.style.background;
        button.style.background = 'rgba(255, 59, 48, 0.3)';
        setTimeout(() => {
            button.style.background = originalBackground;
        }, 2000);
    } finally {
        button.disabled = false;
    }
}

/**
 * Update the watchlist button count on dashboard
 */
async function updateWatchlistButtonCount() {
    if (document.hidden) return; // Skip polling when tab is not visible
    if (socketConnected) return; // WebSocket is pushing updates — skip HTTP poll
    try {
        const response = await fetch('/api/watchlist/count');
        const data = await response.json();

        if (data.success) {
            // Only the SIDEBAR badge survives the dashboard flip — the hero
            // button, its badge and the countdown title are React-rendered
            // (dashboard-header.tsx polls this same endpoint itself).
            const wlNavBadge = document.getElementById('watchlist-nav-badge');
            if (wlNavBadge) {
                wlNavBadge.textContent = data.count;
                wlNavBadge.classList.toggle('hidden', data.count === 0);
            }
        }
    } catch (error) {
        console.error('Error updating watchlist count:', error);
    }
}

function _searchWishlistTrackManually(artistName, trackName) {
    navigateToPage('search');
    const query = `${artistName || ''} ${trackName || ''}`.trim();
    // The user is here because AUTO downloads kept failing — at this point they
    // want the FILE, not metadata browsing. Land on the Soulseek (basic) surface
    // with the search already running, instead of the default metadata source.
    //
    // The source-picker controller initializes ASYNCHRONOUSLY on a fresh
    // /search visit (settings fetch builds the icon row + sets the default
    // source). Swapping sections before it finishes gets overridden the moment
    // its first render fires (it re-activates the enhanced section for the
    // default source) — the "lands on basic then flips to metadata" bug. So:
    // WAIT for the soulseek icon to exist and click it — then the CONTROLLER
    // holds the soulseek state and its own renders short-circuit, nothing can
    // flip back. Only after ~4s without an icon do we fall back to a manual
    // section swap.
    let attempts = 0;
    const tryHandoff = () => {
        attempts += 1;
        const soulseekIcon = document.querySelector('#enh-source-row [data-source="soulseek"]');
        if (soulseekIcon) {
            // Sync the query into the search page BEFORE clicking: the icon
            // click hands off whatever query that page is holding, and it keeps
            // its query across navigation, so without this the wishlist's
            // "search manually" would run the LAST thing searched on /search
            // instead of this track. Same seam downloads.js uses.
            if (typeof window._searchPageSetQuery === 'function') {
                window._searchPageSetQuery(query || '');
            }
            soulseekIcon.click();
            return;
        }
        // Keep waiting: the icon row is React-rendered with the page, so it
        // arrives once the route mounts. There is no manual fallback any more —
        // the old one swapped #basic-search-section's classes and called
        // performDownloadsSearch, and React owns both of those now.
        if (attempts < 25) setTimeout(tryHandoff, 160);
    };
    setTimeout(tryHandoff, 200);
}

// Enhancement 8: navigate to the Search page pre-filled with this artist's name
function _navigateToArtistFromWishlist(artistName) {
    navigateToPage('search');
    setTimeout(() => {
        const searchInput = document.getElementById('enhanced-search-input');
        if (searchInput) {
            searchInput.value = artistName;
            searchInput.dispatchEvent(new Event('input'));
            searchInput.focus();
        }
    }, 300);
}

async function _nebulaDownload() {
    // Check if wishlist is already processing
    try {
        const statsResp = await fetch('/api/wishlist/stats');
        if (statsResp.ok) {
            const stats = await statsResp.json();
            if (stats.is_auto_processing) {
                // Navigate to downloads page so the user can see progress
                navigateToPage('active-downloads');
                showToast('Wishlist is currently being auto-processed', 'info');
                return;
            }
        }
        const procResp = await fetch('/api/active-processes');
        if (procResp.ok) {
            const procData = await procResp.json();
            const wishlistBatch = (procData.active_processes || []).find(p => p.playlist_id === 'wishlist');
            if (wishlistBatch) {
                // Show the existing download modal
                WishlistModalState.clearUserClosed();
                const clientProcess = activeDownloadProcesses['wishlist'];
                if (clientProcess && clientProcess.modalElement && document.body.contains(clientProcess.modalElement)) {
                    clientProcess.modalElement.style.display = 'flex';
                    WishlistModalState.setVisible();
                } else {
                    await rehydrateModal(wishlistBatch, true);
                }
                return;
            }
        }
    } catch (e) {}

    // No active process — show category choice
    const choice = await _showNebulaDownloadChoice();
    if (choice) await openDownloadMissingWishlistModal(choice);
}

function _showNebulaDownloadChoice() {
    return new Promise((resolve) => {
        const overlay = document.createElement('div');
        overlay.className = 'modal-overlay';
        overlay.style.display = 'flex';
        overlay.onclick = (e) => { if (e.target === overlay) { overlay.remove(); resolve(null); } };

        const albumCount = document.getElementById('wishlist-stat-albums')?.textContent || '0';
        const singleCount = document.getElementById('wishlist-stat-singles')?.textContent || '0';

        overlay.innerHTML = `
            <div class="delete-group-dialog">
                <div class="delete-group-icon">
                    <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="rgb(var(--accent-rgb))" stroke-width="1.8"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                </div>
                <h3 class="delete-group-title">Download Wishlist</h3>
                <p class="delete-group-message">Choose which category to process</p>
                <div class="delete-group-actions">
                    <button class="delete-group-btn delete-group-keep" id="ndc-albums">
                        &#128191; Albums &amp; EPs <span style="opacity:0.5;margin-left:6px">${albumCount} tracks</span>
                    </button>
                    <button class="delete-group-btn delete-group-keep" id="ndc-singles" style="border-color: rgba(var(--accent-rgb), 0.15); background: rgba(var(--accent-rgb), 0.06);">
                        &#11088; Singles <span style="opacity:0.5;margin-left:6px">${singleCount} tracks</span>
                    </button>
                    <button class="delete-group-btn delete-group-cancel" id="ndc-cancel">Cancel</button>
                </div>
            </div>
        `;

        overlay.querySelector('#ndc-albums').onclick = () => { overlay.remove(); resolve('albums'); };
        overlay.querySelector('#ndc-singles').onclick = () => { overlay.remove(); resolve('singles'); };
        overlay.querySelector('#ndc-cancel').onclick = () => { overlay.remove(); resolve(null); };

        document.addEventListener('keydown', function esc(e) {
            if (e.key === 'Escape') { overlay.remove(); resolve(null); document.removeEventListener('keydown', esc); }
        });

        document.body.appendChild(overlay);
    });
}

function formatNumber(num) {
    if (!num) return '0';
    return num.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

async function handleMetadataUpdateButtonClick() {
    const button = document.getElementById('metadata-update-button');
    const currentAction = button.textContent;

    if (currentAction === 'Begin Update') {
        // Get refresh interval from dropdown
        const refreshSelect = document.getElementById('metadata-refresh-interval');
        const refreshIntervalDays = refreshSelect.value !== undefined ? parseInt(refreshSelect.value) : 30;

        try {
            button.disabled = true;
            button.textContent = 'Starting...';

            const response = await fetch('/api/metadata/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ refresh_interval_days: refreshIntervalDays })
            });

            const data = await response.json();
            if (!data.success) {
                throw new Error(data.error || 'Failed to start metadata update');
            }

            showToast('Metadata update started!', 'success');

            // Start polling for status updates
            startMetadataUpdatePolling();

        } catch (error) {
            console.error('Error starting metadata update:', error);
            button.disabled = false;
            button.textContent = 'Begin Update';
            showToast(`Error: ${error.message}`, 'error');
        }
    } else {
        // Stop metadata update
        try {
            button.disabled = true;
            button.textContent = 'Stopping...';

            const response = await fetch('/api/metadata/stop', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            });

            if (!response.ok) {
                throw new Error('Failed to stop metadata update');
            }

        } catch (error) {
            console.error('Error stopping metadata update:', error);
            button.disabled = false;
            button.textContent = 'Stop Update';
        }
    }
}

/**
 * Start polling for metadata update status
 */
function startMetadataUpdatePolling() {
    if (metadataUpdatePolling) return; // Already polling

    metadataUpdatePolling = true;
    metadataUpdateInterval = setInterval(checkMetadataUpdateStatus, 1000); // Poll every second

    // Also check immediately
    checkMetadataUpdateStatus();
}

/**
 * Stop polling for metadata update status
 */
function stopMetadataUpdatePolling() {
    metadataUpdatePolling = false;
    if (metadataUpdateInterval) {
        clearInterval(metadataUpdateInterval);
        metadataUpdateInterval = null;
    }
}

/**
 * Check current metadata update status and update UI
 */
async function checkMetadataUpdateStatus() {
    if (socketConnected) return; // WebSocket handles this
    try {
        const response = await fetch('/api/metadata/status');
        const data = await response.json();

        if (data.success && data.status) {
            updateMetadataProgressUI(data.status);

            // Stop polling if completed or error
            if (data.status.status === 'completed' || data.status.status === 'error') {
                stopMetadataUpdatePolling();
            }
        }

    } catch (error) {
        console.warn('Could not fetch metadata update status:', error);
    }
}

function updateMetadataStatusFromData(data) {
    if (!data.success || !data.status) return;
    const prev = _lastToolStatus['metadata'];
    _lastToolStatus['metadata'] = data.status.status;
    if (prev !== undefined && data.status.status === prev && data.status.status !== 'running' && data.status.status !== 'stopping') return;
    updateMetadataProgressUI(data.status);
    if (data.status.status === 'completed' || data.status.status === 'error') {
        stopMetadataUpdatePolling();
    }
}

/**
 * Update metadata progress UI elements
 */
function updateMetadataProgressUI(status) {
    const button = document.getElementById('metadata-update-button');
    const phaseLabel = document.getElementById('metadata-phase-label');
    const progressLabel = document.getElementById('metadata-progress-label');
    const progressBar = document.getElementById('metadata-progress-bar');
    const refreshSelect = document.getElementById('metadata-refresh-interval');

    if (!button || !phaseLabel || !progressLabel || !progressBar || !refreshSelect) return;

    if (status.status === 'running') {
        button.textContent = 'Stop Update';
        button.disabled = false;
        refreshSelect.disabled = true;

        // Update current artist display
        const currentArtist = status.current_artist || 'Processing...';
        phaseLabel.textContent = `Current Artist: ${currentArtist}`;

        // Update progress
        const processed = status.processed || 0;
        const total = status.total || 0;
        const percentage = status.percentage || 0;

        progressLabel.textContent = `${processed} / ${total} artists (${percentage.toFixed(1)}%)`;
        progressBar.style.width = `${percentage}%`;

    } else if (status.status === 'stopping') {
        button.textContent = 'Stopping...';
        button.disabled = true;
        phaseLabel.textContent = 'Current Artist: Stopping...';

    } else if (status.status === 'completed') {
        button.textContent = 'Begin Update';
        button.disabled = false;
        refreshSelect.disabled = false;

        phaseLabel.textContent = 'Current Artist: Completed';

        const processed = status.processed || 0;
        const successful = status.successful || 0;
        const failed = status.failed || 0;

        progressLabel.textContent = `Completed: ${processed} processed, ${successful} successful, ${failed} failed`;
        progressBar.style.width = '100%';

        showToast(`Metadata update completed: ${successful} artists updated, ${failed} failed`, 'success');

    } else if (status.status === 'error') {
        button.textContent = 'Begin Update';
        button.disabled = false;
        refreshSelect.disabled = false;

        phaseLabel.textContent = 'Current Artist: Error occurred';
        progressLabel.textContent = status.error || 'Unknown error';
        progressBar.style.width = '0%';

    } else {
        // Idle state
        button.textContent = 'Begin Update';
        button.disabled = false;
        refreshSelect.disabled = false;

        phaseLabel.textContent = 'Current Artist: Not running';
        progressLabel.textContent = '0 / 0 artists (0.0%)';
        progressBar.style.width = '0%';
    }
}

/**
 * Check active media server and hide metadata updater if not Plex
 */
async function checkAndHideMetadataUpdaterForNonPlex() {
    try {
        const response = await fetch('/api/active-media-server');
        const data = await response.json();

        if (data.success) {
            const metadataCard = document.getElementById('metadata-updater-card');
            if (metadataCard) {
                // Show metadata updater only for Plex and Jellyfin
                if (data.active_server === 'plex' || data.active_server === 'jellyfin') {
                    metadataCard.style.display = 'flex';
                    console.log(`Metadata updater shown: ${data.active_server} is active server`);

                    // Update the header text to reflect the current server.
                    // The card markup is .tool-card-header > h4.tool-card-title —
                    // the old '.card-header h3' selector matched nothing, so this
                    // rename never happened. Same pattern the DB updater card uses
                    // (updateDbUpdaterCardInfo in wishlist-tools.js).
                    const headerElement = metadataCard.querySelector('.tool-card-title');
                    if (headerElement) {
                        const serverDisplayName = data.active_server.charAt(0).toUpperCase() + data.active_server.slice(1);
                        headerElement.textContent = `${serverDisplayName} Metadata Updater`;
                    }

                    // Update the description based on the server type
                    const descElement = metadataCard.querySelector('.metadata-updater-description');
                    if (descElement) {
                        if (data.active_server === 'jellyfin') {
                            descElement.textContent = 'Download and upload high-quality artist images from Spotify to your Jellyfin server for artists without photos.';
                        } else {
                            descElement.textContent = 'Download and upload high-quality artist images from Spotify to your Plex server for artists without photos.';
                        }
                    }
                } else {
                    // Hide metadata updater for Navidrome
                    metadataCard.style.display = 'none';
                    console.log(`Metadata updater hidden: ${data.active_server} does not support image uploads`);
                }
            }
        }
    } catch (error) {
        console.warn('Could not check active media server for metadata updater visibility:', error);
    }
}

async function checkAndShowMediaScanForPlex() {
    /**
     * Show media scan tool only for Plex (Jellyfin/Navidrome auto-scan)
     */
    try {
        const response = await fetch('/api/active-media-server');
        const data = await response.json();

        if (data.success) {
            const mediaScanCard = document.getElementById('media-scan-card');
            if (mediaScanCard) {
                // Show media scan tool only for Plex
                if (data.active_server === 'plex') {
                    mediaScanCard.style.display = 'flex';
                    console.log('Media scan tool shown: Plex is active server');
                } else {
                    // Hide for Jellyfin/Navidrome (they auto-scan)
                    mediaScanCard.style.display = 'none';
                    console.log(`Media scan tool hidden: ${data.active_server} auto-scans`);
                }
            }
        }
    } catch (error) {
        console.warn('Could not check active media server for media scan visibility:', error);
    }
}

async function handleMediaScanButtonClick() {
    /**
     * Trigger a manual Plex media library scan
     */
    const button = document.getElementById('media-scan-button');
    const phaseLabel = document.getElementById('media-scan-phase-label');
    const progressBar = document.getElementById('media-scan-progress-bar');
    const progressLabel = document.getElementById('media-scan-progress-label');
    const statusValue = document.getElementById('media-scan-status');

    if (!button) return;

    try {
        // Disable button and update UI
        button.disabled = true;
        phaseLabel.textContent = 'Requesting scan...';
        progressBar.style.width = '30%';
        progressLabel.textContent = 'Sending scan request to Plex';
        statusValue.textContent = 'Scanning...';
        statusValue.style.color = 'rgb(var(--accent-rgb))';

        // Request scan (database update handled by system automation)
        const response = await fetch('/api/scan/request', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                reason: 'Manual scan triggered from dashboard'
            })
        });

        const result = await response.json();

        if (result.success) {
            // Get delay from API response (graceful fallback to 60 if not provided)
            const delaySeconds = (result.scan_info && result.scan_info.delay_seconds) || 60;
            let remainingSeconds = delaySeconds;
            let countdownInterval = null;
            let pollInterval = null;

            // Update last scan time
            const lastTimeEl = document.getElementById('media-scan-last-time');
            if (lastTimeEl) {
                const now = new Date();
                lastTimeEl.textContent = now.toLocaleTimeString();
            }

            // Start countdown timer (visual feedback during delay)
            phaseLabel.textContent = 'Scan scheduled...';
            progressBar.style.width = '0%';

            countdownInterval = setInterval(() => {
                remainingSeconds--;

                // Update progress bar (0% -> 100% over delay period)
                const progress = ((delaySeconds - remainingSeconds) / delaySeconds) * 100;
                progressBar.style.width = `${progress}%`;

                // Update progress label with countdown
                if (remainingSeconds > 0) {
                    progressLabel.textContent = `Starting scan in ${remainingSeconds}s...`;
                } else {
                    progressLabel.textContent = 'Scan starting now...';
                }

                // When countdown reaches 0, start polling
                if (remainingSeconds <= 0) {
                    clearInterval(countdownInterval);

                    // Transition to scanning phase
                    phaseLabel.textContent = 'Scan in progress...';
                    progressBar.style.width = '100%';
                    progressLabel.textContent = 'Media server is scanning library...';
                    showToast('📡 Media scan started', 'success', 3000);

                    // Start polling for scan completion (5 minutes = 150 polls × 2s)
                    let pollCount = 0;
                    const maxPolls = 150; // 5 minutes

                    pollInterval = setInterval(async () => {
                        // Count EVERY tick, including the ones the websocket
                        // short-circuits below. Counting after that guard meant
                        // pollCount never advanced on a socket-connected client
                        // — the normal case — so this clearInterval was
                        // unreachable and the 2s timer leaked for the life of
                        // the page, once per Scan Library click.
                        pollCount++;

                        if (pollCount > maxPolls) {
                            // Polling timeout after 5 minutes
                            clearInterval(pollInterval);
                            pollInterval = null;
                            // With a live socket updateMediaScanFromData already
                            // owns the card, so don't stomp it with a synthetic
                            // "completed" state we never actually observed.
                            if (!socketConnected) {
                                button.disabled = false;
                                phaseLabel.textContent = 'Scan completed';
                                progressBar.style.width = '0%';
                                progressLabel.textContent = 'Ready for next scan';
                                statusValue.textContent = 'Idle';
                                statusValue.style.color = '#b3b3b3';
                                showToast('✅ Media scan completed', 'success', 3000);
                            }
                            return;
                        }

                        if (socketConnected) return; // Phase 5: WS handles scan status

                        try {
                            const statusResponse = await fetch('/api/scan/status');
                            const statusData = await statusResponse.json();

                            if (statusData.success && statusData.status) {
                                const status = statusData.status;

                                // Update status display
                                if (status.is_scanning) {
                                    phaseLabel.textContent = 'Media server scanning...';
                                    progressLabel.textContent = status.progress_message || 'Scan in progress';
                                } else if (status.status === 'idle') {
                                    // Scan completed
                                    clearInterval(pollInterval);
                                    button.disabled = false;
                                    phaseLabel.textContent = 'Scan completed successfully';
                                    progressBar.style.width = '0%';
                                    progressLabel.textContent = 'Ready for next scan';
                                    statusValue.textContent = 'Idle';
                                    statusValue.style.color = '#b3b3b3';
                                    showToast('✅ Media scan completed', 'success', 3000);
                                }
                            }
                        } catch (pollError) {
                            console.debug('Scan status poll error:', pollError);
                        }
                    }, 2000); // Poll every 2 seconds
                }
            }, 1000); // Update countdown every second

        } else {
            // Error occurred
            showToast(`❌ Scan request failed: ${result.error}`, 'error', 5000);
            button.disabled = false;
            phaseLabel.textContent = 'Scan failed';
            progressBar.style.width = '0%';
            progressLabel.textContent = result.error || 'Unknown error';
            statusValue.textContent = 'Error';
            statusValue.style.color = '#f44336';
        }

    } catch (error) {
        console.error('Error requesting media scan:', error);
        showToast('❌ Failed to request media scan', 'error', 3000);
        button.disabled = false;
        phaseLabel.textContent = 'Error';
        progressBar.style.width = '0%';
        progressLabel.textContent = error.message;
        statusValue.textContent = 'Error';
        statusValue.style.color = '#f44336';
    }
}

/**
 * Check for ongoing metadata update and restore state on page load
 */
async function checkAndRestoreMetadataUpdateState() {
    try {
        const response = await fetch('/api/metadata/status');
        const data = await response.json();

        if (data.success && data.status) {
            const status = data.status;

            // If metadata update is running, restore the UI state and start polling
            if (status.status === 'running') {
                console.log('Found ongoing metadata update, restoring state...');
                updateMetadataProgressUI(status);
                startMetadataUpdatePolling();
            } else if (status.status === 'completed' || status.status === 'error') {
                // Show final state but don't start polling
                updateMetadataProgressUI(status);
            }
        }
    } catch (error) {
        console.warn('Could not check metadata update state on page load:', error);
    }
}

// --- Live Log Viewer Functions ---

// Global state for log polling
let logPolling = false;
let logInterval = null;
let lastLogCount = 0;

/**
 * Initialize the live log viewer for sync page
 */
function initializeLiveLogViewer() {
    const logArea = document.getElementById('sync-log-area');
    if (!logArea) return;

    // Set initial content
    logArea.value = 'Loading activity feed...';

    // Start log polling
    startLogPolling();

    // Initial load
    loadLogs();
}

/**
 * Start polling for logs
 */
function startLogPolling() {
    if (logPolling) return; // Already polling

    logPolling = true;
    logInterval = setInterval(loadLogs, 3000); // Poll every 3 seconds
    console.log('📝 Started activity feed polling for sync page');
}

/**
 * Stop polling for logs
 */
function stopLogPolling() {
    logPolling = false;
    if (logInterval) {
        clearInterval(logInterval);
        logInterval = null;
        console.log('📝 Stopped log polling');
    }
}

/**
 * Load and display activity feed as logs
 */
async function loadLogs() {
    if (socketConnected) return; // WebSocket handles this
    try {
        const response = await fetch('/api/logs');
        const data = await response.json();
        updateLogsFromData(data);
    } catch (error) {
        console.warn('Could not load activity logs for sync page:', error);
        const logArea = document.getElementById('sync-log-area');
        if (logArea && (logArea.value === 'Loading logs...' || logArea.value === '')) {
            logArea.value = 'Error loading activity feed. Check console for details.';
        }
    }
}

function updateLogsFromData(data) {
    if (!data.logs || !Array.isArray(data.logs)) return;
    const logArea = document.getElementById('sync-log-area');
    if (!logArea) return;

    const logText = data.logs.join('\n');

    // Store current scroll state
    const wasAtTop = logArea.scrollTop <= 10;
    const wasUserScrolled = logArea.scrollTop < logArea.scrollHeight - logArea.clientHeight - 10;

    // Update content only if it has changed
    if (logArea.value !== logText) {
        logArea.value = logText;

        // Smart scrolling: stay at top for new entries, preserve user position if scrolled
        if (wasAtTop || !wasUserScrolled) {
            logArea.scrollTop = 0; // Stay at top since newest entries are now at top
        }
    }
}

/**
 * Stop log polling when leaving sync page
 */
function cleanupSyncPageLogs() {
    stopLogPolling();
}

// --- Global Cleanup on Page Unload ---
// Note: Automatic wishlist processing now runs server-side and continues even when browser is closed
// ===============================
