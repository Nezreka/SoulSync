// == API RATE MONITOR GAUGES   ==
// ===============================

const _rateMonitorState = {};
const _RATE_GAUGE_SERVICES = [
    'spotify', 'itunes', 'deezer', 'lastfm', 'genius',
    'musicbrainz', 'audiodb', 'tidal', 'qobuz', 'discogs',
];
const _RATE_GAUGE_LABELS = {
    spotify: 'Spotify', itunes: 'Apple Music', deezer: 'Deezer',
    lastfm: 'Last.fm', genius: 'Genius', musicbrainz: 'MusicBrainz',
    audiodb: 'AudioDB', tidal: 'Tidal', qobuz: 'Qobuz', discogs: 'Discogs',
};
const _RATE_GAUGE_COLORS = {
    spotify: '#1DB954', itunes: '#FC3C44', deezer: '#A238FF',
    lastfm: '#D51007', genius: '#FFFF64', musicbrainz: '#BA478F',
    audiodb: '#00BCD4', tidal: '#00FFFF', qobuz: '#FF6B35', discogs: '#D4A574',
};

// SVG constants — 240° arc, gap at bottom
const _G = { size: 160, cx: 80, cy: 84, r: 56, stroke: 8, startAngle: 240, totalArc: 240 };

function _gPt(angle, radius) {
    const rad = (angle - 90) * Math.PI / 180;
    const r = radius || _G.r;
    return { x: _G.cx + r * Math.cos(rad), y: _G.cy + r * Math.sin(rad) };
}

function _gArc(startDeg, endDeg, radius) {
    const r = radius || _G.r;
    const s = _gPt(startDeg, r), e = _gPt(endDeg, r);
    const sweep = ((endDeg - startDeg + 360) % 360);
    const large = sweep > 180 ? 1 : 0;
    return `M${s.x},${s.y} A${r},${r} 0 ${large} 1 ${e.x},${e.y}`;
}

function _handleRateMonitorUpdate(data) {
    const grid = document.getElementById('rate-monitor-grid');
    if (!grid) return;

    if (!grid.children.length) {
        for (const svc of _RATE_GAUGE_SERVICES) {
            const div = document.createElement('div');
            div.className = 'rate-gauge-card';
            div.id = `rate-gauge-${svc}`;
            div.onclick = () => _openRateModal(svc);
            grid.appendChild(div);
        }
    }

    for (const svc of _RATE_GAUGE_SERVICES) {
        const d = data[svc];
        if (!d) continue;
        _rateMonitorState[svc] = d;
        const container = document.getElementById(`rate-gauge-${svc}`);
        if (!container) continue;

        const value = d.cpm || 0;
        const max = d.limit || 60;
        const pct = Math.min(value / max, 1);
        const accent = _RATE_GAUGE_COLORS[svc] || '#888';
        const label = _RATE_GAUGE_LABELS[svc] || svc;
        const worker = d.worker || {};
        const wStatus = worker.status || 'stopped';
        const isRateLimited = d.rate_limited === true;

        // Build or update the card content
        let gaugeWrap = container.querySelector('.gauge-arc-wrap');
        if (!gaugeWrap) {
            // Full rebuild
            container.innerHTML = `
                <div class="gauge-card-header">
                    <span class="gauge-card-dot" style="background:${accent}"></span>
                    <span class="gauge-card-name">${label}</span>
                    <span class="gauge-card-status" data-status="${wStatus}">${_workerStatusLabel(wStatus, worker)}</span>
                </div>
                <div class="gauge-arc-wrap">${_buildGaugeSVG(svc, value, max)}</div>
                <div class="gauge-card-stats">
                    <div class="gauge-card-stat"><span class="gauge-card-stat-val">${value.toFixed(0)}</span><span class="gauge-card-stat-label">calls/min</span></div>
                    <div class="gauge-card-stat"><span class="gauge-card-stat-val">${worker.calls_1h || 0}</span><span class="gauge-card-stat-label">last hour</span></div>
                    <div class="gauge-card-stat"><span class="gauge-card-stat-val">${(worker.calls_24h || 0).toLocaleString()}</span><span class="gauge-card-stat-label">24h</span></div>
                </div>
                ${svc === 'spotify' && worker.daily_budget ? _buildBudgetBar(worker.daily_budget) : ''}
                ${isRateLimited ? _buildRateLimitBadge(d) : ''}
            `;
        } else {
            // Fast update — only change values
            _updateGauge(gaugeWrap, value, max, svc);

            // Update status
            const statusEl = container.querySelector('.gauge-card-status');
            if (statusEl) {
                statusEl.dataset.status = wStatus;
                statusEl.textContent = _workerStatusLabel(wStatus, worker);
            }

            // Update stats
            const statVals = container.querySelectorAll('.gauge-card-stat-val');
            if (statVals[0]) statVals[0].textContent = value.toFixed(0);
            if (statVals[1]) statVals[1].textContent = worker.calls_1h || 0;
            if (statVals[2]) statVals[2].textContent = (worker.calls_24h || 0).toLocaleString();

            // Budget bar (Spotify)
            if (svc === 'spotify' && worker.daily_budget) {
                let budgetEl = container.querySelector('.gauge-budget-bar');
                if (!budgetEl) {
                    const div = document.createElement('div');
                    div.innerHTML = _buildBudgetBar(worker.daily_budget);
                    const statsEl = container.querySelector('.gauge-card-stats');
                    if (statsEl) statsEl.after(div.firstElementChild);
                } else {
                    const b = worker.daily_budget;
                    const pctB = Math.min(100, Math.round((b.used / b.limit) * 100));
                    const fill = budgetEl.querySelector('.gauge-budget-fill');
                    if (fill) { fill.style.width = pctB + '%'; }
                    const label = budgetEl.querySelector('.gauge-budget-label');
                    if (label) label.textContent = `${b.used.toLocaleString()} / ${b.limit.toLocaleString()} daily`;
                }
            }

            // Rate limit badge
            let badge = container.querySelector('.gauge-rl-badge');
            if (isRateLimited) {
                if (!badge) {
                    const div = document.createElement('div');
                    div.innerHTML = _buildRateLimitBadge(d);
                    container.appendChild(div.firstElementChild);
                } else {
                    const mins = Math.ceil((d.rl_remaining || 0) / 60);
                    const timeEl = badge.querySelector('.gauge-rl-time');
                    if (timeEl) timeEl.textContent = mins > 60 ? `${Math.floor(mins / 60)}h ${mins % 60}m` : `${mins}m`;
                }
            } else if (badge) {
                badge.remove();
            }
        }

        container.classList.toggle('danger', pct > 0.8 || isRateLimited);
        container.classList.toggle('active', value > 0 || wStatus === 'running');
        container.classList.toggle('rate-limited', isRateLimited);
    }
}

function _workerStatusLabel(status, worker) {
    if (status === 'not_configured') return 'Not configured';
    if (status === 'paused') return worker.yield_reason === 'downloads' ? 'Yielding' : 'Paused';
    if (status === 'idle') return 'Idle';
    if (status === 'running') return 'Running';
    return 'Stopped';
}

function _buildBudgetBar(budget) {
    const pct = Math.min(100, Math.round((budget.used / budget.limit) * 100));
    const cls = budget.exhausted ? 'exhausted' : pct > 80 ? 'high' : '';
    return `<div class="gauge-budget-bar ${cls}">
        <div class="gauge-budget-fill" style="width:${pct}%"></div>
        <span class="gauge-budget-label">${budget.used.toLocaleString()} / ${budget.limit.toLocaleString()} daily</span>
    </div>`;
}

function _buildRateLimitBadge(d) {
    const mins = Math.ceil((d.rl_remaining || 0) / 60);
    const text = mins > 60 ? `${Math.floor(mins / 60)}h ${mins % 60}m` : `${mins}m`;
    return `<div class="gauge-rl-badge"><span class="gauge-rl-dot"></span>RATE LIMITED<span class="gauge-rl-time">${text}</span></div>`;
}

function _buildGaugeSVG(svc, value, max) {
    const { size, cx, cy, r, stroke, startAngle, totalArc } = _G;
    const label = _RATE_GAUGE_LABELS[svc] || svc;
    const accent = _RATE_GAUGE_COLORS[svc] || '#888';
    const pct = Math.min(value / max, 1);
    const endAngle = startAngle + pct * totalArc;
    const arcEnd = startAngle + totalArc;
    const glowId = `glow-${svc}`;

    // Endpoint dot position
    const dot = pct > 0 ? _gPt(endAngle, r) : null;

    // Gradient ID for the colored arc
    const gradId = `grad-${svc}`;

    const color = pct > 0.8 ? '#ef4444' : pct > 0.6 ? '#eab308' : accent;

    return `
        <svg viewBox="0 0 ${size} ${size}" class="rate-gauge-svg">
            <!-- Background track -->
            <path d="${_gArc(startAngle, arcEnd)}" fill="none" stroke="rgba(255,255,255,0.05)" stroke-width="${stroke}" stroke-linecap="round"/>

            <!-- Danger zone marker (last 20% of arc, subtle) -->
            <path d="${_gArc(startAngle + totalArc * 0.8, arcEnd)}" fill="none" stroke="rgba(239,68,68,0.1)" stroke-width="${stroke}" stroke-linecap="round"/>

            <!-- Active arc (CSS handles glow via drop-shadow) -->
            ${pct > 0 ? `<path class="gauge-active-arc" data-color="${color}" d="${_gArc(startAngle, endAngle)}" fill="none" stroke="${color}" stroke-width="${stroke}" stroke-linecap="round" style="filter:drop-shadow(0 0 6px ${color}60)"/>` : ''}

            <!-- Endpoint dot -->
            ${dot ? `<circle class="gauge-dot" cx="${dot.x}" cy="${dot.y}" r="5" fill="${color}" style="filter:drop-shadow(0 0 4px ${color}80)"/><circle cx="${dot.x}" cy="${dot.y}" r="2.5" fill="#fff" opacity="0.9"/>` : ''}

            <!-- Scale labels: 0 and max -->
            <text x="${_gPt(startAngle, r + 16).x}" y="${_gPt(startAngle, r + 16).y}" text-anchor="middle" dominant-baseline="middle" fill="rgba(255,255,255,0.2)" font-size="9" font-family="-apple-system,sans-serif">0</text>
            <text x="${_gPt(arcEnd, r + 16).x}" y="${_gPt(arcEnd, r + 16).y}" text-anchor="middle" dominant-baseline="middle" fill="rgba(255,255,255,0.2)" font-size="9" font-family="-apple-system,sans-serif">${max}</text>

            <!-- Center content -->
            <text class="gauge-value" x="${cx}" y="${cy - 12}" text-anchor="middle" dominant-baseline="middle">${Math.round(value)}</text>
            <text class="gauge-unit" x="${cx}" y="${cy + 6}" text-anchor="middle">/min</text>

            <!-- Service label -->
            <text class="gauge-label" x="${cx}" y="${cy + 28}" text-anchor="middle">${label}</text>
        </svg>
    `;
}

function _updateGauge(container, value, max, svc) {
    const { r, stroke, startAngle, totalArc } = _G;
    const accent = _RATE_GAUGE_COLORS[svc] || '#888';
    const pct = Math.min(value / max, 1);
    const endAngle = startAngle + pct * totalArc;
    const color = pct > 0.8 ? '#ef4444' : pct > 0.6 ? '#eab308' : accent;

    // Update center value
    const valText = container.querySelector('.gauge-value');
    if (valText) valText.textContent = Math.round(value);

    // Update active arc
    const activeArc = container.querySelector('.gauge-active-arc');
    if (pct > 0) {
        const d = _gArc(startAngle, endAngle);
        if (activeArc) {
            activeArc.setAttribute('d', d);
            activeArc.setAttribute('stroke', color);
            activeArc.style.filter = `drop-shadow(0 0 6px ${color}60)`;
        } else {
            // Rebuild the whole gauge when transitioning from 0 to active
            container.innerHTML = _buildGaugeSVG(svc, value, max);
            return;
        }
    } else if (activeArc) {
        activeArc.remove();
        // Also remove dots
        container.querySelectorAll('.gauge-dot').forEach(d => d.remove());
        const innerDot = container.querySelector('.gauge-dot + circle');
        if (innerDot) innerDot.remove();
        return;
    }

    // Update endpoint dot
    const gaugeDot = container.querySelector('.gauge-dot');
    if (pct > 0 && gaugeDot) {
        const dot = _gPt(endAngle, r);
        gaugeDot.setAttribute('cx', dot.x);
        gaugeDot.setAttribute('cy', dot.y);
        gaugeDot.setAttribute('fill', color);
        gaugeDot.style.filter = `drop-shadow(0 0 4px ${color}80)`;
        const inner = gaugeDot.nextElementSibling;
        if (inner && inner.tagName === 'circle') {
            inner.setAttribute('cx', dot.x);
            inner.setAttribute('cy', dot.y);
        }
    }
}

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

async function fetchAndUpdateSystemStats() {
    if (socketConnected) return; // WebSocket handles this
    if (document.hidden) return; // Skip polling when tab is not visible
    try {
        const response = await fetch('/api/system/stats');
        if (!response.ok) return;

        const data = await response.json();

        // Update all stat cards
        updateStatCard('active-downloads-card', data.active_downloads, 'Currently downloading');
        updateStatCard('finished-downloads-card', data.finished_downloads, 'Completed this session');
        updateStatCard('download-speed-card', data.download_speed, 'Combined speed');
        updateStatCard('active-syncs-card', data.active_syncs, 'Playlists syncing');
        updateStatCard('uptime-card', data.uptime, 'Application runtime');
        updateStatCard('memory-card', data.memory_usage, 'Current usage');

    } catch (error) {
        console.warn('Could not fetch system stats:', error);
    }
}

function updateStatCard(cardId, value, subtitle) {
    const card = document.getElementById(cardId);
    if (card) {
        const valueElement = card.querySelector('.stat-card-value');
        const subtitleElement = card.querySelector('.stat-card-subtitle');

        if (valueElement) {
            valueElement.textContent = value;
        }
        if (subtitleElement) {
            subtitleElement.textContent = subtitle;
        }
    }
}

async function fetchAndUpdateActivityFeed() {
    if (socketConnected) return; // WebSocket handles this
    if (document.hidden) return; // Skip polling when tab is not visible
    try {
        const response = await fetch('/api/activity/feed');
        if (!response.ok) {
            console.warn('Activity feed response not ok:', response.status, response.statusText);
            return;
        }

        const data = await response.json();
        console.log('Activity feed data received:', data);
        updateActivityFeed(data.activities || []);

    } catch (error) {
        console.warn('Could not fetch activity feed:', error);
    }
}

// Cache last feed signature to avoid unnecessary DOM rebuilds (prevents blink)
let _lastActivityFeedSig = '';

function updateActivityFeed(activities) {
    const feedContainer = document.getElementById('dashboard-activity-feed');
    if (!feedContainer) return;

    if (activities.length === 0) {
        if (_lastActivityFeedSig === 'empty') return;
        _lastActivityFeedSig = 'empty';
        feedContainer.innerHTML = `
            <div class="activity-item">
                <span class="activity-icon">📊</span>
                <div class="activity-text-content">
                    <p class="activity-title">System Started</p>
                    <p class="activity-subtitle">Dashboard initialized successfully</p>
                </div>
                <p class="activity-time">Just now</p>
            </div>
        `;
        return;
    }

    const items = activities.slice(0, 5);
    // Build signature from titles+subtitles to detect actual changes
    const sig = items.map(a => a.title + a.subtitle).join('|');
    const feedChanged = sig !== _lastActivityFeedSig;
    _lastActivityFeedSig = sig;

    if (!feedChanged) {
        // Just update timestamps without rebuilding DOM
        const timeEls = feedContainer.querySelectorAll('.activity-time');
        items.forEach((activity, i) => {
            if (timeEls[i]) timeEls[i].textContent = timeAgo(activity.time);
        });
        return;
    }

    // Full rebuild only when feed content actually changed
    feedContainer.innerHTML = '';
    items.forEach((activity, index) => {
        const activityElement = document.createElement('div');
        activityElement.className = 'activity-item';
        activityElement.innerHTML = `
            <span class="activity-icon">${escapeHtml(activity.icon)}</span>
            <div class="activity-text-content">
                <p class="activity-title">${escapeHtml(activity.title)}</p>
                <p class="activity-subtitle">${escapeHtml(activity.subtitle)}</p>
            </div>
            <p class="activity-time">${timeAgo(activity.time)}</p>
        `;
        feedContainer.appendChild(activityElement);

        if (index < items.length - 1) {
            const separator = document.createElement('div');
            separator.className = 'activity-separator';
            feedContainer.appendChild(separator);
        }
    });
}

async function checkForActivityToasts() {
    if (socketConnected) return; // WebSocket handles this (instant push)
    if (document.hidden) return; // Skip polling when tab is not visible
    try {
        const response = await fetch('/api/activity/toasts');
        if (!response.ok) return;

        const data = await response.json();
        const toasts = data.toasts || [];

        toasts.forEach(activity => {
            // Convert activity to toast type based on icon/title
            let toastType = 'info';
            if (activity.icon === '✅' || activity.title.includes('Complete')) {
                toastType = 'success';
            } else if (activity.icon === '❌' || activity.title.includes('Failed') || activity.title.includes('Error')) {
                toastType = 'error';
            } else if (activity.icon === '🚫' || activity.title.includes('Cancelled')) {
                toastType = 'warning';
            }

            // Show toast with activity info
            showToast(`${activity.title}: ${activity.subtitle}`, toastType);
        });

    } catch (error) {
        // Silently fail for toast checking to avoid spam
    }
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
            _updateHeroBtnCount('watchlist-button', 'watchlist-badge', data.count);
            // Update sidebar nav badge
            const wlNavBadge = document.getElementById('watchlist-nav-badge');
            if (wlNavBadge) {
                wlNavBadge.textContent = data.count;
                wlNavBadge.classList.toggle('hidden', data.count === 0);
            }
            const watchlistButton = document.getElementById('watchlist-button');
            if (watchlistButton) {
                const countdownText = data.next_run_in_seconds ? formatCountdownTime(data.next_run_in_seconds) : '';
                if (countdownText) {
                    watchlistButton.title = `Next auto-scan in ${countdownText}`;
                }
            }
        }
    } catch (error) {
        console.error('Error updating watchlist count:', error);
    }
}

/**
 * Check and update watchlist status for all visible artist cards
 */
async function updateArtistCardWatchlistStatus() {
    const artistCards = document.querySelectorAll('.artist-card');
    const artistIds = [];
    for (const card of artistCards) {
        const artistId = card.dataset.artistId;
        if (artistId) artistIds.push(artistId);
    }
    if (!artistIds.length) return;

    try {
        const response = await fetch('/api/watchlist/check-batch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ artist_ids: artistIds })
        });

        const data = await response.json();
        if (data.success && data.results) {
            for (const card of artistCards) {
                const artistId = card.dataset.artistId;
                if (!artistId) continue;

                const button = card.querySelector('.watchlist-toggle-btn');
                if (!button) continue;
                const icon = button.querySelector('.watchlist-icon');
                const text = button.querySelector('.watchlist-text');

                const gearBtn = button.parentElement?.querySelector('.watchlist-settings-btn');
                if (data.results[artistId]) {
                    if (icon) icon.textContent = '👁️';
                    if (text) text.textContent = 'Watching...';
                    button.classList.add('watching');
                    if (gearBtn) gearBtn.classList.remove('hidden');
                } else {
                    if (icon) icon.textContent = '👁️';
                    if (text) text.textContent = 'Add to Watchlist';
                    button.classList.remove('watching');
                    if (gearBtn) gearBtn.classList.add('hidden');
                }
            }
        }
    } catch (error) {
        console.error('Error batch checking watchlist status:', error);
    }
}

/**
 * Initialize/refresh the watchlist sidebar page
 */
async function initializeWatchlistPage() {
    try {
        const emptyEl = document.getElementById('watchlist-page-empty');
        const gridEl = document.getElementById('watchlist-artists-list');
        const countEl = document.getElementById('watchlist-page-count');
        const overrideBanner = document.getElementById('watchlist-page-override-banner');

        // Fetch count, artists, scan status, global config in parallel
        const [countRes, artistsRes, statusRes, globalRes] = await Promise.all([
            fetch('/api/watchlist/count').then(r => r.json()),
            fetch('/api/watchlist/artists').then(r => r.json()),
            fetch('/api/watchlist/scan/status').then(r => r.json()),
            fetch('/api/watchlist/global-config').then(r => r.json()).catch(() => ({ success: false })),
        ]);

        const count = countRes.success ? countRes.count : 0;
        const artists = artistsRes.success ? artistsRes.artists : [];
        const scanStatus = statusRes.success ? statusRes.status : 'idle';
        const globalOverrideActive = globalRes.success && globalRes.config && globalRes.config.global_override_enabled;

        // Update count
        if (countEl) countEl.textContent = `${count} artist${count !== 1 ? 's' : ''}`;

        // Update nav badge
        const navBadge = document.getElementById('watchlist-nav-badge');
        if (navBadge) {
            navBadge.textContent = count;
            navBadge.classList.toggle('hidden', count === 0);
        }

        // Empty state
        if (count === 0) {
            if (emptyEl) emptyEl.style.display = '';
            if (gridEl) gridEl.style.display = 'none';
            watchlistPageState.isInitialized = true;
            return;
        }
        if (emptyEl) emptyEl.style.display = 'none';
        if (gridEl) gridEl.style.display = '';

        // Store artists for sorting
        watchlistPageState.artists = artists;

        // Last scan summary strip
        const scanStrip = document.getElementById('watchlist-last-scan-strip');
        const scanText = document.getElementById('watchlist-last-scan-text');
        if (scanStrip && scanText && statusRes.completed_at && statusRes.summary) {
            const completedDate = new Date(statusRes.completed_at);
            const ago = _formatTimeAgo(completedDate);
            const found = statusRes.summary.new_tracks_found || 0;
            const added = statusRes.summary.tracks_added_to_wishlist || 0;
            scanText.textContent = `Last scan: ${ago} — ${found} new track${found !== 1 ? 's' : ''} found, ${added} added to wishlist`;
            scanStrip.style.display = '';
        } else if (scanStrip) {
            scanStrip.style.display = 'none';
        }

        // Global override banner
        if (overrideBanner) overrideBanner.style.display = globalOverrideActive ? '' : 'none';
        const settingsBtn = document.getElementById('watchlist-page-settings-btn');
        if (settingsBtn) {
            settingsBtn.classList.toggle('watchlist-global-settings-active', globalOverrideActive);
            settingsBtn.innerHTML = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg> ${globalOverrideActive ? 'Global Override ON' : 'Global Settings'}`;
        }

        // Render artist cards
        if (gridEl) {
            gridEl.innerHTML = artists.map(artist => {
                const pills = [];
                if (artist.include_albums) pills.push('<span class="watchlist-pill watchlist-pill-active">Albums</span>');
                if (artist.include_eps) pills.push('<span class="watchlist-pill watchlist-pill-active">EPs</span>');
                if (artist.include_singles) pills.push('<span class="watchlist-pill watchlist-pill-active">Singles</span>');
                if (artist.include_live) pills.push('<span class="watchlist-pill watchlist-pill-filter">Live</span>');
                if (artist.include_remixes) pills.push('<span class="watchlist-pill watchlist-pill-filter">Remixes</span>');
                if (artist.include_acoustic) pills.push('<span class="watchlist-pill watchlist-pill-filter">Acoustic</span>');
                if (artist.include_compilations) pills.push('<span class="watchlist-pill watchlist-pill-filter">Compilations</span>');
                const sourceBadges = [];
                if (artist.spotify_artist_id) sourceBadges.push('<span class="watchlist-source-badge watchlist-source-spotify">Spotify</span>');
                if (artist.itunes_artist_id) sourceBadges.push('<span class="watchlist-source-badge watchlist-source-itunes">iTunes</span>');
                if (artist.deezer_artist_id) sourceBadges.push('<span class="watchlist-source-badge watchlist-source-deezer">Deezer</span>');
                if (artist.discogs_artist_id) sourceBadges.push('<span class="watchlist-source-badge watchlist-source-discogs">Discogs</span>');
                const artistPrimaryId = artist.spotify_artist_id || artist.itunes_artist_id || artist.deezer_artist_id || artist.discogs_artist_id;
                return `
                    <div class="watchlist-artist-card"
                         data-artist-name="${artist.artist_name.toLowerCase().replace(/"/g, '&quot;')}"
                         data-artist-id="${artistPrimaryId}"
                         data-last-scan="${artist.last_scan_timestamp || ''}"
                         data-added="${artist.date_added || ''}">
                        <label class="watchlist-card-checkbox" onclick="event.stopPropagation();">
                            <input type="checkbox" class="watchlist-select-cb"
                                   data-artist-id="${artistPrimaryId}"
                                   data-artist-name="${escapeHtml(artist.artist_name)}"
                                   onchange="updateWatchlistBatchBar()">
                            <span class="watchlist-checkbox-custom"></span>
                        </label>
                        <button class="watchlist-card-gear"
                                data-artist-id="${artistPrimaryId}"
                                data-artist-name="${escapeHtml(artist.artist_name)}"
                                onclick="event.stopPropagation();"
                                title="Artist settings">
                            <svg viewBox="0 0 24 24"><path d="M19.14 12.94c.04-.3.06-.61.06-.94 0-.32-.02-.64-.07-.94l2.03-1.58a.49.49 0 00.12-.61l-1.92-3.32a.49.49 0 00-.59-.22l-2.39.96c-.5-.38-1.03-.7-1.62-.94l-.36-2.54a.48.48 0 00-.48-.41h-3.84a.48.48 0 00-.48.41l-.36 2.54c-.59.24-1.13.57-1.62.94l-2.39-.96a.49.49 0 00-.59.22L2.74 8.87a.48.48 0 00.12.61l2.03 1.58c-.05.3-.07.62-.07.94s.02.64.07.94l-2.03 1.58a.49.49 0 00-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.26.41.48.41h3.84c.24 0 .44-.17.48-.41l.36-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32c.12-.22.07-.47-.12-.61l-2.01-1.58zM12 15.6A3.6 3.6 0 1115.6 12 3.6 3.6 0 0112 15.6z"/></svg>
                        </button>
                        <div class="watchlist-card-image">
                            ${artist.image_url ? `<img src="${artist.image_url}" alt="${escapeHtml(artist.artist_name)}" onerror="if(!this.dataset.retried){this.dataset.retried='1';this.src=this.src;}else{this.parentElement.innerHTML='<div class=\\'watchlist-card-image-fallback\\'>🎤</div>';}">` : '<div class="watchlist-card-image-fallback">🎤</div>'}
                        </div>
                        <div class="watchlist-card-info">
                            <span class="watchlist-card-name">${escapeHtml(artist.artist_name)}</span>
                            <span class="watchlist-card-meta">${formatRelativeScanTime(artist.last_scan_timestamp)}</span>
                        </div>
                        ${sourceBadges.length > 0 ? `<div class="watchlist-card-sources">${sourceBadges.join('')}</div>` : ''}
                        ${pills.length > 0 ? `<div class="watchlist-card-pills">${pills.join('')}</div>` : ''}
                    </div>
                `;
            }).join('');

            // Wire up gear buttons
            gridEl.querySelectorAll('.watchlist-card-gear').forEach(button => {
                button.addEventListener('click', () => {
                    openWatchlistArtistConfigModal(button.getAttribute('data-artist-id'), button.getAttribute('data-artist-name'));
                });
            });

            // Wire up artist card clicks
            gridEl.querySelectorAll('.watchlist-artist-card').forEach(item => {
                item.addEventListener('click', (e) => {
                    if (e.target.closest('.watchlist-card-gear') || e.target.closest('.watchlist-card-checkbox')) return;
                    const artistId = item.getAttribute('data-artist-id');
                    const artistName = item.querySelector('.watchlist-card-name').textContent;
                    openWatchlistArtistDetailView(artistId, artistName);
                });
            });
        }

        // Scan status
        const scanStatusEl = document.getElementById('watchlist-scan-status');
        const liveActivityEl = document.getElementById('watchlist-live-activity');
        const scanBtn = document.getElementById('scan-watchlist-btn');
        const cancelBtn = document.getElementById('cancel-watchlist-scan-btn');

        if (scanStatus === 'scanning') {
            if (scanStatusEl) scanStatusEl.style.display = '';
            if (liveActivityEl) liveActivityEl.style.display = 'flex';
            if (scanBtn) { scanBtn.disabled = true; scanBtn.classList.add('btn-processing'); scanBtn.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg> Scanning...'; }
            if (cancelBtn) cancelBtn.style.display = '';
            pollWatchlistScanStatus();
        } else {
            if (scanStatusEl && statusRes.summary) {
                scanStatusEl.style.display = '';
                const summaryEl = document.getElementById('watchlist-page-scan-summary');
                if (summaryEl) {
                    summaryEl.style.display = '';
                    summaryEl.innerHTML = `<span class="sync-stat">Artists: ${statusRes.summary.total_artists || 0}</span><span class="sync-separator"> • </span><span class="sync-stat">New tracks: ${statusRes.summary.new_tracks_found || 0}</span><span class="sync-separator"> • </span><span class="sync-stat">Added to wishlist: ${statusRes.summary.tracks_added_to_wishlist || 0}</span>`;
                }
            }
        }

        // Start countdown timer
        const nextRunSeconds = countRes.next_run_in_seconds || 0;
        startWatchlistCountdownTimer(nextRunSeconds);

        watchlistPageState.isInitialized = true;

    } catch (error) {
        console.error('Error initializing watchlist page:', error);
        showToast('Failed to load watchlist', 'error');
    }
}

/**
 * Initialize/refresh the wishlist sidebar page
 */
async function initializeWishlistPage() {
    try {
        const emptyEl = document.getElementById('wishlist-page-empty');
        const nebulaEl = document.getElementById('wishlist-nebula');
        const countEl = document.getElementById('wishlist-page-count');
        const tracksSection = document.getElementById('wishlist-category-tracks');
        const statsStrip = document.getElementById('wishlist-stats-strip');

        const [statsRes, cycleRes, albumRes, singleRes, watchlistRes] = await Promise.all([
            fetch('/api/wishlist/stats').then(r => r.json()),
            fetch('/api/wishlist/cycle').then(r => r.json()),
            fetch('/api/wishlist/tracks?category=albums').then(r => r.json()),
            fetch('/api/wishlist/tracks?category=singles').then(r => r.json()),
            fetch('/api/watchlist/artists').then(r => r.json()).catch(() => ({ success: false })),
        ]);

        // Build artist name → image URL map from watchlist
        const _artistImageMap = new Map();
        if (watchlistRes.success && watchlistRes.artists) {
            for (const wa of watchlistRes.artists) {
                if (wa.artist_name && wa.image_url) _artistImageMap.set(wa.artist_name.toLowerCase(), wa.image_url);
            }
        }

        const { singles = 0, albums = 0, total = 0 } = statsRes;
        const currentCycle = cycleRes.cycle || 'albums';

        if (countEl) countEl.textContent = `${total} track${total !== 1 ? 's' : ''}`;
        const navBadge = document.getElementById('wishlist-nav-badge');
        if (navBadge) { navBadge.textContent = total; navBadge.classList.toggle('hidden', total === 0); }

        const statAlbums = document.getElementById('wishlist-stat-albums');
        const statSingles = document.getElementById('wishlist-stat-singles');
        const statCycle = document.getElementById('wishlist-stat-cycle');
        if (statAlbums) statAlbums.textContent = albums;
        if (statSingles) statSingles.textContent = singles;
        if (statCycle) statCycle.textContent = currentCycle === 'albums' ? 'Albums/EPs' : 'Singles';

        if (total === 0) {
            if (emptyEl) emptyEl.style.display = '';
            if (nebulaEl) nebulaEl.style.display = 'none';
            if (tracksSection) tracksSection.style.display = 'none';
            if (statsStrip) statsStrip.style.display = 'none';
            wishlistPageState.isInitialized = true;
            return;
        }
        if (emptyEl) emptyEl.style.display = 'none';
        if (nebulaEl) nebulaEl.style.display = '';
        if (tracksSection) tracksSection.style.display = 'none';
        if (statsStrip) statsStrip.style.display = '';

        _renderWishlistNebula(albumRes.tracks || [], singleRes.tracks || [], _artistImageMap, currentCycle);
        startWishlistCountdownTimer(currentCycle, statsRes.next_run_in_seconds || 0);

        // Live processing: check if wishlist download is active and start polling
        _startNebulaLivePolling(currentCycle, _artistImageMap);

        wishlistPageState.isInitialized = true;

    } catch (error) {
        console.error('Error initializing wishlist page:', error);
        showToast('Failed to load wishlist', 'error');
    }
}

/* ═══════════════════════════════════════════════════════════════════
   WISHLIST NEBULA — Artist orbs with album/single satellites
   ═══════════════════════════════════════════════════════════════════ */

function _renderWishlistNebula(albumTracks, singleTracks, artistImageMap, currentCycle) {
    const field = document.getElementById('wl-nebula-field');
    if (!field) return;
    artistImageMap = artistImageMap || new Map();

    const artistMap = new Map();
    function _parse(track, type) {
        let sd = track.spotify_data;
        if (typeof sd === 'string') { try { sd = JSON.parse(sd); } catch (e) { return null; } }
        if (!sd) return null;
        const raw = sd.album;
        const albumName = (typeof raw === 'string' ? raw : raw?.name) || 'Unknown';
        const albumImage = (typeof raw === 'object' && raw?.images?.[0]?.url) || '';
        let artist = 'Unknown Artist';
        if (sd.artists?.[0]?.name) artist = sd.artists[0].name;
        else if (typeof sd.artists?.[0] === 'string') artist = sd.artists[0];
        return { track: sd.name || 'Unknown', artist, album: albumName, image: albumImage, type, id: track.spotify_track_id || track.id || '' };
    }

    for (const t of albumTracks) { const p = _parse(t, 'album'); if (p) { if (!artistMap.has(p.artist)) artistMap.set(p.artist, { albums: new Map(), singles: [] }); const a = artistMap.get(p.artist); if (!a.albums.has(p.album)) a.albums.set(p.album, { image: p.image, tracks: [] }); a.albums.get(p.album).tracks.push(p); } }
    for (const t of singleTracks) { const p = _parse(t, 'single'); if (p) { if (!artistMap.has(p.artist)) artistMap.set(p.artist, { albums: new Map(), singles: [] }); artistMap.get(p.artist).singles.push(p); } }

    if (artistMap.size === 0) { field.innerHTML = '<div class="wl-nebula-empty">Your wishlist is empty</div>'; return; }

    const sorted = [...artistMap.entries()].sort((a, b) => {
        const ac = [...a[1].albums.values()].reduce((s, al) => s + al.tracks.length, 0) + a[1].singles.length;
        const bc = [...b[1].albums.values()].reduce((s, al) => s + al.tracks.length, 0) + b[1].singles.length;
        return bc - ac;
    });

    function _hue(n) { let h = 0; for (let i = 0; i < n.length; i++) h = n.charCodeAt(i) + ((h << 5) - h); return Math.abs(h) % 360; }

    let html = '';
    sorted.forEach(([name, data], idx) => {
        const total = [...data.albums.values()].reduce((s, a) => s + a.tracks.length, 0) + data.singles.length;
        const hasAlbums = data.albums.size > 0;
        const hue = _hue(name);
        const sz = total >= 10 ? 'orb-lg' : total >= 4 ? 'orb-md' : 'orb-sm';

        // Enhancement 1: prefer watchlist artist photo over album cover
        let img = artistImageMap.get(name.toLowerCase()) || '';
        if (!img) { for (const [, ad] of data.albums) { if (ad.image) { img = ad.image; break; } } }
        if (!img && data.singles.length) img = data.singles[0].image || '';

        // Enhancement 3: pulse if this artist has albums and current cycle is albums
        const pulseClass = (hasAlbums && currentCycle === 'albums') ? ' orb-pulse' : '';

        // Enhancement 7: staggered entry animation
        const delay = Math.min(idx * 60, 800);

        html += `<div class="wl-orb-group" data-artist="${escapeHtml(name)}" style="animation-delay:${delay}ms">`;

        // Enhancement 2: hover tooltip
        html += `<div class="wl-orb-tooltip">${escapeHtml(name)}<br><span>${total} track${total !== 1 ? 's' : ''}</span></div>`;

        html += `<div class="wl-orb ${sz}${pulseClass}" style="--orb-hue:${hue}" onclick="_toggleOrbExpand(this)">`;
        html += `<div class="wl-orb-glow"></div>`;
        html += img ? `<img class="wl-orb-img" src="${img}" alt="">` : `<div class="wl-orb-initials">${escapeHtml(name.substring(0, 2).toUpperCase())}</div>`;
        html += `<div class="wl-orb-ring"></div>`;

        // Enhancement 5: album art ring (show up to 6 album covers around the orb)
        const ringCovers = [];
        for (const [, ad] of data.albums) { if (ad.image && ringCovers.length < 6) ringCovers.push(ad.image); }
        for (const s of data.singles) { if (s.image && ringCovers.length < 6) ringCovers.push(s.image); }
        if (ringCovers.length >= 3) {
            html += `<div class="wl-orb-art-ring">`;
            ringCovers.forEach((url, i) => {
                const angle = (360 / ringCovers.length) * i;
                html += `<img class="wl-art-ring-item" src="${url}" style="--ring-angle:${angle}deg" alt="">`;
            });
            html += `</div>`;
        }

        html += `</div>`; // /orb

        // Enhancement 8: clickable artist name → navigate to artist detail
        html += `<div class="wl-orb-label" onclick="event.stopPropagation(); _navigateToArtistFromWishlist('${escapeHtml(name)}')" title="View artist">${escapeHtml(name)}</div>`;
        html += `<div class="wl-orb-meta">${total} track${total !== 1 ? 's' : ''}</div>`;

        // Expanded content
        html += `<div class="wl-orb-expanded">`;
        if (data.albums.size > 0) {
            html += `<div class="wl-album-fan">`;
            for (const [an, ad] of data.albums) {
                const tileId = 'wl-tile-' + an.replace(/\W/g, '_') + '_' + idx;
                html += `<div class="wl-album-tile" data-album="${escapeHtml(an)}" onclick="_toggleAlbumTile(this)">`;
                html += `<div class="wl-album-tile-art">${ad.image ? `<img src="${ad.image}" alt="">` : `<div class="wl-album-tile-fallback">&#128191;</div>`}</div>`;
                html += `<div class="wl-album-tile-info"><div class="wl-album-tile-name">${escapeHtml(an)}</div><div class="wl-album-tile-count">${ad.tracks.length} track${ad.tracks.length !== 1 ? 's' : ''}</div></div>`;
                html += `<span class="wl-album-tile-badge">${ad.tracks.length}</span>`;
                html += `<button class="wl-album-tile-remove" onclick="event.stopPropagation();_removeWishlistAlbum('${escapeHtml(an)}')" title="Remove album">&#10005;</button>`;
                // Track list (hidden until tile clicked)
                html += `<div class="wl-tile-tracks">`;
                for (const tr of ad.tracks) {
                    html += `<div class="wl-tile-track">`;
                    html += `<span class="wl-tile-track-name">${escapeHtml(tr.track)}</span>`;
                    html += `<button class="wl-tile-track-remove" onclick="event.stopPropagation();_removeWishlistTrack('${escapeHtml(tr.id)}')" title="Remove track">&#10005;</button>`;
                    html += `</div>`;
                }
                html += `</div>`;
                html += `</div>`;
            }
            html += `</div>`;
        }
        if (data.singles.length > 0) {
            html += `<div class="wl-singles-orbit">`;
            for (const s of data.singles) {
                html += `<div class="wl-single-moon" data-track-id="${escapeHtml(s.id)}">`;
                html += s.image ? `<img src="${s.image}" alt="">` : `<span class="wl-moon-fallback">&#11088;</span>`;
                html += `<div class="wl-moon-label">${escapeHtml(s.track)}</div>`;
                html += `<button class="wl-moon-remove-btn" onclick="event.stopPropagation();_removeWishlistTrack('${escapeHtml(s.id)}')" title="Remove">&#10005;</button>`;
                html += `</div>`;
            }
            html += `</div>`;
        }
        html += `</div></div>`; // /expanded, /group
    });

    field.innerHTML = html;
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

function _toggleAlbumTile(tileEl) {
    const wasExpanded = tileEl.classList.contains('tile-expanded');
    // Collapse all tiles in this group
    tileEl.closest('.wl-album-fan')?.querySelectorAll('.wl-album-tile.tile-expanded').forEach(t => t.classList.remove('tile-expanded'));
    if (!wasExpanded) tileEl.classList.add('tile-expanded');
}

function _toggleOrbExpand(el) {
    const g = el.closest('.wl-orb-group');
    if (!g) return;
    const was = g.classList.contains('expanded');
    document.querySelectorAll('.wl-orb-group.expanded').forEach(o => o.classList.remove('expanded'));
    if (!was) g.classList.add('expanded');
}

async function _removeWishlistAlbum(albumName) {
    if (!await showConfirmDialog({ title: 'Remove Album', message: `Remove all tracks from "${albumName}"?`, confirmText: 'Remove', destructive: true })) return;
    try {
        const res = await fetch('/api/wishlist/remove-album', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ album_name: albumName }) });
        const data = await res.json();
        if (data.success) { showToast(`Removed "${albumName}"`, 'success'); wishlistPageState.isInitialized = false; await initializeWishlistPage(); await updateWishlistCount(); }
        else showToast(data.error || 'Failed', 'error');
    } catch (err) { showToast('Error: ' + err.message, 'error'); }
}

async function _removeWishlistTrack(trackId) {
    try {
        const res = await fetch('/api/wishlist/remove-track', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ spotify_track_id: trackId }) });
        const data = await res.json();
        if (data.success) {
            showToast('Removed', 'success');
            await updateWishlistCount();
            // Re-render nebula to reflect removal
            wishlistPageState.isInitialized = false;
            await initializeWishlistPage();
        }
    } catch (err) { showToast('Error: ' + err.message, 'error'); }
}

function _filterNebula() {
    const q = (document.getElementById('wl-nebula-search')?.value || '').toLowerCase().trim();
    document.querySelectorAll('.wl-orb-group').forEach(g => {
        const a = (g.dataset.artist || '').toLowerCase();
        const albums = [...g.querySelectorAll('.wl-satellite')].map(s => (s.dataset.album || '').toLowerCase());
        const match = !q || a.includes(q) || albums.some(al => al.includes(q));
        g.style.display = match ? '' : 'none';
        if (!match) g.classList.remove('expanded');
    });
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

function _nebulaBack() {
    const t = document.getElementById('wishlist-category-tracks');
    const n = document.getElementById('wishlist-nebula');
    if (t) t.style.display = 'none';
    if (n) n.style.display = '';
    window.selectedWishlistCategory = null;
    wishlistPageState.isInitialized = false;
    initializeWishlistPage();
}

// ── Live processing state for nebula ──
let _nebulaLivePollInterval = null;
let _nebulaLastTotal = null;

function _startNebulaLivePolling(currentCycle, artistImageMap) {
    _stopNebulaLivePolling();
    _nebulaLastTotal = null;

    _nebulaLivePollInterval = setInterval(async () => {
        if (currentPage !== 'wishlist') { _stopNebulaLivePolling(); return; }

        try {
            // Use wishlist stats which has is_auto_processing flag
            const statsResp = await fetch('/api/wishlist/stats');
            if (!statsResp.ok) return;
            const stats = await statsResp.json();
            const isProcessing = stats.is_auto_processing || false;
            const newTotal = stats.total || 0;

            // Also check for manual wishlist download batches
            let hasBatch = false;
            try {
                const procResp = await fetch('/api/active-processes');
                if (procResp.ok) {
                    const procData = await procResp.json();
                    hasBatch = (procData.active_processes || []).some(p => p.playlist_id === 'wishlist');
                }
            } catch (e) {}

            const active = isProcessing || hasBatch;
            const nebulaField = document.getElementById('wl-nebula-field');
            if (!nebulaField) return;

            if (active) {
                nebulaField.classList.add('nebula-processing');
                document.querySelectorAll('.wl-orb-group').forEach(g => g.classList.add('orb-processing'));

                // Tracks completed — re-render
                if (_nebulaLastTotal !== null && newTotal < _nebulaLastTotal) {
                    const [albumRes, singleRes] = await Promise.all([
                        fetch('/api/wishlist/tracks?category=albums').then(r => r.json()),
                        fetch('/api/wishlist/tracks?category=singles').then(r => r.json()),
                    ]);
                    _renderWishlistNebula(albumRes.tracks || [], singleRes.tracks || [], artistImageMap, currentCycle);

                    const countEl = document.getElementById('wishlist-page-count');
                    if (countEl) countEl.textContent = `${newTotal} track${newTotal !== 1 ? 's' : ''}`;
                    const sa = document.getElementById('wishlist-stat-albums');
                    const ss = document.getElementById('wishlist-stat-singles');
                    if (sa) sa.textContent = stats.albums || 0;
                    if (ss) ss.textContent = stats.singles || 0;

                    // Re-add processing classes after re-render
                    document.getElementById('wl-nebula-field')?.classList.add('nebula-processing');
                    document.querySelectorAll('.wl-orb-group').forEach(g => g.classList.add('orb-processing'));
                }
                _nebulaLastTotal = newTotal;
            } else {
                nebulaField.classList.remove('nebula-processing');
                document.querySelectorAll('.wl-orb-group.orb-processing').forEach(g => g.classList.remove('orb-processing'));

                if (_nebulaLastTotal !== null) {
                    _nebulaLastTotal = null;
                    wishlistPageState.isInitialized = false;
                    await initializeWishlistPage();
                    await updateWishlistCount();
                }
            }
        } catch (e) {}
    }, 5000);
}

function _stopNebulaLivePolling() {
    if (_nebulaLivePollInterval) {
        clearInterval(_nebulaLivePollInterval);
        _nebulaLivePollInterval = null;
    }
    _nebulaLastTotal = null;
}

/**
 * Sort the watchlist artist grid by the selected criteria.
 */
function sortWatchlistArtists(sortBy) {
    const grid = document.getElementById('watchlist-artists-list');
    if (!grid) return;
    const cards = Array.from(grid.querySelectorAll('.watchlist-artist-card'));
    if (cards.length === 0) return;

    cards.sort((a, b) => {
        switch (sortBy) {
            case 'name-asc':
                return (a.dataset.artistName || '').localeCompare(b.dataset.artistName || '');
            case 'name-desc':
                return (b.dataset.artistName || '').localeCompare(a.dataset.artistName || '');
            case 'scan-oldest': {
                const aTime = a.dataset.lastScan ? new Date(a.dataset.lastScan).getTime() : 0;
                const bTime = b.dataset.lastScan ? new Date(b.dataset.lastScan).getTime() : 0;
                return aTime - bTime; // oldest first (never scanned = 0 = top)
            }
            case 'scan-newest': {
                const aTime = a.dataset.lastScan ? new Date(a.dataset.lastScan).getTime() : 0;
                const bTime = b.dataset.lastScan ? new Date(b.dataset.lastScan).getTime() : 0;
                return bTime - aTime;
            }
            case 'added-newest': {
                const aTime = a.dataset.added ? new Date(a.dataset.added).getTime() : 0;
                const bTime = b.dataset.added ? new Date(b.dataset.added).getTime() : 0;
                return bTime - aTime;
            }
            default:
                return 0;
        }
    });

    // Re-append in sorted order (preserves event listeners)
    cards.forEach(card => grid.appendChild(card));
}

/**
 * Filter wishlist tracks by search query within the active track list.
 */
function filterWishlistTracks() {
    const input = document.getElementById('wishlist-track-search-input');
    if (!input) return;
    const query = input.value.toLowerCase().trim();
    const tracksList = document.getElementById('wishlist-tracks-list');
    if (!tracksList) return;

    // For albums view: filter album cards by album name or track names within
    const albumCards = tracksList.querySelectorAll('.wishlist-album-card');
    if (albumCards.length > 0) {
        albumCards.forEach(card => {
            const albumHeader = card.querySelector('.wishlist-album-header');
            const albumName = (albumHeader?.querySelector('.wishlist-album-name')?.textContent || '').toLowerCase();
            const artistName = (albumHeader?.querySelector('.wishlist-album-artist')?.textContent || '').toLowerCase();
            const tracks = card.querySelectorAll('.wishlist-album-track');
            let albumHasMatch = !query || albumName.includes(query) || artistName.includes(query);

            // Also check individual track names
            if (!albumHasMatch && tracks.length > 0) {
                tracks.forEach(track => {
                    const trackName = (track.textContent || '').toLowerCase();
                    if (trackName.includes(query)) albumHasMatch = true;
                });
            }

            card.style.display = albumHasMatch ? '' : 'none';
        });
        return;
    }

    // For singles view: filter individual track rows
    const trackRows = tracksList.querySelectorAll('.playlist-track-item-with-image, .playlist-track-item');
    trackRows.forEach(row => {
        const text = (row.textContent || '').toLowerCase();
        row.style.display = (!query || text.includes(query)) ? '' : 'none';
    });
}

/**
 * Format a Date object as a relative time string (e.g. "2 hours ago")
 */
function _formatTimeAgo(date) {
    const now = new Date();
    const diffMs = now - date;
    const diffMins = Math.floor(diffMs / 60000);
    if (diffMins < 1) return 'just now';
    if (diffMins < 60) return `${diffMins}m ago`;
    const diffHours = Math.floor(diffMins / 60);
    if (diffHours < 24) return `${diffHours}h ago`;
    const diffDays = Math.floor(diffHours / 24);
    if (diffDays === 1) return 'yesterday';
    if (diffDays < 7) return `${diffDays}d ago`;
    return date.toLocaleDateString();
}

/**
 * Show watchlist modal (legacy — kept for backward compatibility)
 */
async function showWatchlistModal() {
    try {
        // Check if watchlist has any artists
        const countResponse = await fetch('/api/watchlist/count');
        const countData = await countResponse.json();

        if (!countData.success) {
            console.error('Error getting watchlist count:', countData.error);
            return;
        }

        if (countData.count === 0) {
            // Show empty state message
            alert('Your watchlist is empty!\n\nAdd artists to your watchlist from the Artists page to monitor them for new releases.');
            return;
        }

        // Get watchlist artists
        const artistsResponse = await fetch('/api/watchlist/artists');
        const artistsData = await artistsResponse.json();

        if (!artistsData.success) {
            console.error('Error getting watchlist artists:', artistsData.error);
            return;
        }

        // Create modal if it doesn't exist
        let modal = document.getElementById('watchlist-modal');
        if (!modal) {
            modal = document.createElement('div');
            modal.id = 'watchlist-modal';
            modal.className = 'modal-overlay';
            document.body.appendChild(modal);
        }

        // Get scan status and global config
        const statusResponse = await fetch('/api/watchlist/scan/status');
        const statusData = await statusResponse.json();
        const scanStatus = statusData.success ? statusData.status : 'idle';

        let globalOverrideActive = false;
        try {
            const globalConfigResponse = await fetch('/api/watchlist/global-config');
            const globalConfigData = await globalConfigResponse.json();
            globalOverrideActive = globalConfigData.success && globalConfigData.config.global_override_enabled;
        } catch (e) {
            console.debug('Could not fetch global config:', e);
        }

        // Format countdown timer
        const nextRunSeconds = countData.next_run_in_seconds || 0;
        const countdownText = formatCountdownTime(nextRunSeconds);

        // Build modal content
        modal.innerHTML = `
            <div class="modal-container playlist-modal watchlist-fullscreen">
                <div class="playlist-modal-header">
                    <div class="playlist-header-content" style="width: 100%;">
                        <h2><svg class="watchlist-header-icon" width="24" height="24" viewBox="0 0 24 24" fill="rgb(var(--accent-rgb))"><path d="M12 4.5C7 4.5 2.73 7.61 1 12c1.73 4.39 6 7.5 11 7.5s9.27-3.11 11-7.5c-1.73-4.39-6-7.5-11-7.5zM12 17c-2.76 0-5-2.24-5-5s2.24-5 5-5 5 2.24 5 5-2.24 5-5 5zm0-8c-1.66 0-3 1.34-3 3s1.34 3 3 3 3-1.34 3-3-1.34-3-3-3z"/></svg> Watchlist</h2>
                        <div class="playlist-quick-info">
                            <span class="playlist-track-count">${countData.count} artist${countData.count !== 1 ? 's' : ''}</span>
                            <span class="playlist-owner" id="watchlist-next-auto-timer">Next Auto${countdownText ? ': ' + countdownText : ''}</span>
                        </div>
                        <div class="playlist-modal-sync-status" id="watchlist-scan-status" style="display: ${scanStatus !== 'idle' ? 'flex' : 'none'}; flex-direction: column; align-items: center;">
                            <!-- Live Visual Activity Display -->
                            <div id="watchlist-live-activity" class="watchlist-live-activity" style="display: ${scanStatus === 'scanning' ? 'flex' : 'none'};">
                                <!-- Artist Photo -->
                                <div class="watchlist-live-activity-col">
                                    <img id="watchlist-artist-img" class="watchlist-live-activity-artist-img" src="" alt="Artist" onerror="this.style.display='none';" />
                                    <div id="watchlist-artist-name" class="watchlist-live-activity-label">Waiting...</div>
                                </div>

                                <!-- Album Cover -->
                                <div class="watchlist-live-activity-col">
                                    <img id="watchlist-album-img" class="watchlist-live-activity-album-img" src="" alt="Album" onerror="this.style.display='none';" />
                                    <div id="watchlist-album-name" class="watchlist-live-activity-label">Waiting...</div>
                                </div>

                                <!-- Track and Wishlist Feed -->
                                <div class="watchlist-live-activity-feed">
                                    <div class="watchlist-live-activity-feed-label">Current Track:</div>
                                    <div id="watchlist-track-name" class="watchlist-live-activity-track">Waiting...</div>

                                    <div class="watchlist-live-activity-feed-label-orange">✨ Recently Added:</div>
                                    <div id="watchlist-additions-feed" style="max-height: 80px; overflow-y: auto; display: flex; flex-direction: column; gap: 4px; font-size: 10px;">
                                        <!-- Populated by JavaScript -->
                                    </div>
                                </div>
                            </div>

                            ${statusData.summary ? `
                                <div class="scan-status-summary" style="margin-top: 8px; font-size: 13px; opacity: 0.8;">
                                    <span class="sync-stat">Artists: ${statusData.summary.total_artists || 0}</span>
                                    <span class="sync-separator"> • </span>
                                    <span class="sync-stat">New tracks: ${statusData.summary.new_tracks_found || 0}</span>
                                    <span class="sync-separator"> • </span>
                                    <span class="sync-stat">Added to wishlist: ${statusData.summary.tracks_added_to_wishlist || 0}</span>
                                </div>
                            ` : ''}
                        </div>
                    </div>
                    <span class="playlist-modal-close" onclick="closeWatchlistModal()">&times;</span>
                </div>

                <div class="playlist-modal-body">
                    <div class="watchlist-actions" style="margin-bottom: 16px; display: flex; gap: 12px; align-items: center; padding: 12px 32px; flex-wrap: wrap;">
                        <button class="playlist-modal-btn playlist-modal-btn-primary watchlist-btn-scan ${scanStatus === 'scanning' ? 'btn-processing' : ''}"
                                id="scan-watchlist-btn"
                                onclick="startWatchlistScan()"
                                ${scanStatus === 'scanning' ? 'disabled' : ''}>
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
                            ${scanStatus === 'scanning' ? 'Scanning...' : 'Scan for New Releases'}
                        </button>
                        <button class="playlist-modal-btn playlist-modal-btn-secondary watchlist-btn-cancel"
                                id="cancel-watchlist-scan-btn"
                                onclick="cancelWatchlistScan()"
                                style="display: ${scanStatus === 'scanning' ? '' : 'none'};">
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>
                            Cancel Scan
                        </button>
                        <button class="playlist-modal-btn playlist-modal-btn-secondary watchlist-btn-similar"
                                id="update-similar-artists-btn"
                                onclick="updateSimilarArtists()"
                                ${scanStatus === 'scanning' ? 'disabled' : ''}>
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>
                            Update Similar Artists
                        </button>
                        <button class="playlist-modal-btn playlist-modal-btn-secondary watchlist-btn-settings ${globalOverrideActive ? 'watchlist-global-settings-active' : ''}"
                                id="watchlist-global-settings-btn"
                                onclick="openWatchlistGlobalSettingsModal()">
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
                            ${globalOverrideActive ? 'Global Override ON' : 'Global Settings'}
                        </button>
                    </div>

                    ${globalOverrideActive ? `
                    <div class="watchlist-global-override-banner" style="margin: 0 32px 12px;">
                        <span>⚠️</span>
                        <span>Global override is active — per-artist settings are being ignored during scans.</span>
                    </div>
                    ` : ''}

                    <!-- Search Bar -->
                    <div class="watchlist-search-container" style="margin-bottom: 12px;">
                        <svg class="watchlist-search-icon" width="16" height="16" viewBox="0 0 24 24" fill="rgba(255,255,255,0.35)"><path d="M15.5 14h-.79l-.28-.27A6.471 6.471 0 0016 9.5 6.5 6.5 0 109.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"/></svg>
                        <input type="text"
                               id="watchlist-search-input"
                               class="watchlist-search-input"
                               placeholder="Search watchlist..."
                               oninput="filterWatchlistArtists()">
                    </div>

                    <!-- Batch action bar -->
                    <div class="watchlist-batch-bar" id="watchlist-batch-bar">
                        <label class="watchlist-select-all-label" onclick="event.stopPropagation();">
                            <input type="checkbox" id="watchlist-select-all-cb" onchange="toggleWatchlistSelectAll(this.checked)">
                            <span>Select All</span>
                        </label>
                        <span class="watchlist-batch-count" id="watchlist-batch-count"></span>
                        <button class="playlist-modal-btn playlist-modal-btn-secondary watchlist-batch-remove-btn"
                                id="watchlist-batch-remove-btn"
                                onclick="batchRemoveFromWatchlist()"
                                style="display: none;">
                            Remove Selected
                        </button>
                    </div>

                    <div class="watchlist-artists-grid" id="watchlist-artists-list">
                        ${artistsData.artists.map(artist => {
            const pills = [];
            if (artist.include_albums) pills.push('<span class="watchlist-pill watchlist-pill-active">Albums</span>');
            if (artist.include_eps) pills.push('<span class="watchlist-pill watchlist-pill-active">EPs</span>');
            if (artist.include_singles) pills.push('<span class="watchlist-pill watchlist-pill-active">Singles</span>');
            if (artist.include_live) pills.push('<span class="watchlist-pill watchlist-pill-filter">Live</span>');
            if (artist.include_remixes) pills.push('<span class="watchlist-pill watchlist-pill-filter">Remixes</span>');
            if (artist.include_acoustic) pills.push('<span class="watchlist-pill watchlist-pill-filter">Acoustic</span>');
            if (artist.include_compilations) pills.push('<span class="watchlist-pill watchlist-pill-filter">Compilations</span>');
            const sourceBadges = [];
            if (artist.spotify_artist_id) sourceBadges.push('<span class="watchlist-source-badge watchlist-source-spotify">Spotify</span>');
            if (artist.itunes_artist_id) sourceBadges.push('<span class="watchlist-source-badge watchlist-source-itunes">iTunes</span>');
            if (artist.deezer_artist_id) sourceBadges.push('<span class="watchlist-source-badge watchlist-source-deezer">Deezer</span>');
            if (artist.discogs_artist_id) sourceBadges.push('<span class="watchlist-source-badge watchlist-source-discogs">Discogs</span>');
            const artistPrimaryId = artist.spotify_artist_id || artist.itunes_artist_id || artist.deezer_artist_id || artist.discogs_artist_id;
            return `
                            <div class="watchlist-artist-card"
                                 data-artist-name="${artist.artist_name.toLowerCase().replace(/"/g, '&quot;')}"
                                 data-artist-id="${artistPrimaryId}">

                                <label class="watchlist-card-checkbox" onclick="event.stopPropagation();">
                                    <input type="checkbox" class="watchlist-select-cb"
                                           data-artist-id="${artistPrimaryId}"
                                           data-artist-name="${escapeHtml(artist.artist_name)}"
                                           onchange="updateWatchlistBatchBar()">
                                    <span class="watchlist-checkbox-custom"></span>
                                </label>

                                <button class="watchlist-card-gear"
                                        data-artist-id="${artistPrimaryId}"
                                        data-artist-name="${escapeHtml(artist.artist_name)}"
                                        onclick="event.stopPropagation();"
                                        title="Artist settings">
                                    <svg viewBox="0 0 24 24"><path d="M19.14 12.94c.04-.3.06-.61.06-.94 0-.32-.02-.64-.07-.94l2.03-1.58a.49.49 0 00.12-.61l-1.92-3.32a.49.49 0 00-.59-.22l-2.39.96c-.5-.38-1.03-.7-1.62-.94l-.36-2.54a.48.48 0 00-.48-.41h-3.84a.48.48 0 00-.48.41l-.36 2.54c-.59.24-1.13.57-1.62.94l-2.39-.96a.49.49 0 00-.59.22L2.74 8.87a.48.48 0 00.12.61l2.03 1.58c-.05.3-.07.62-.07.94s.02.64.07.94l-2.03 1.58a.49.49 0 00-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.26.41.48.41h3.84c.24 0 .44-.17.48-.41l.36-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32c.12-.22.07-.47-.12-.61l-2.01-1.58zM12 15.6A3.6 3.6 0 1115.6 12 3.6 3.6 0 0112 15.6z"/></svg>
                                </button>

                                <div class="watchlist-card-image">
                                    ${artist.image_url ? `
                                        <img src="${artist.image_url}"
                                             alt="${escapeHtml(artist.artist_name)}"
                                             loading="lazy"
                                             onerror="this.parentElement.innerHTML='<div class=\\'watchlist-card-image-fallback\\'>🎤</div>';">
                                    ` : `
                                        <div class="watchlist-card-image-fallback">🎤</div>
                                    `}
                                </div>

                                <div class="watchlist-card-info">
                                    <span class="watchlist-card-name">${escapeHtml(artist.artist_name)}</span>
                                    <span class="watchlist-card-meta">${formatRelativeScanTime(artist.last_scan_timestamp)}</span>
                                </div>
                                ${sourceBadges.length > 0 ? `<div class="watchlist-card-sources">${sourceBadges.join('')}</div>` : ''}
                                ${pills.length > 0 ? `<div class="watchlist-card-pills">${pills.join('')}</div>` : ''}
                            </div>
                        `}).join('')}
                    </div>
                </div>
            </div>
        `;

        // Add event listeners for gear buttons
        modal.querySelectorAll('.watchlist-card-gear').forEach(button => {
            button.addEventListener('click', () => {
                const artistId = button.getAttribute('data-artist-id');
                const artistName = button.getAttribute('data-artist-name');
                openWatchlistArtistConfigModal(artistId, artistName);
            });
        });

        // Add click handlers to artist cards (except for gear button or checkbox)
        modal.querySelectorAll('.watchlist-artist-card').forEach(item => {
            item.addEventListener('click', (e) => {
                if (e.target.closest('.watchlist-card-gear') || e.target.closest('.watchlist-card-checkbox')) {
                    return;
                }

                const artistId = item.getAttribute('data-artist-id');
                const artistName = item.querySelector('.watchlist-card-name').textContent;

                console.log(`🎵 Artist card clicked: ${artistName} (${artistId})`);
                openWatchlistArtistDetailView(artistId, artistName);
            });
        });

        // Show modal
        modal.style.display = 'flex';

        // Start countdown timer update interval
        startWatchlistCountdownTimer(nextRunSeconds);

        // Start polling for scan status if scanning
        if (scanStatus === 'scanning') {
            pollWatchlistScanStatus();
        }

    } catch (error) {
        console.error('Error showing watchlist modal:', error);
    }
}

function startWatchlistCountdownTimer(initialSeconds) {
    // Clear any existing interval
    if (watchlistCountdownInterval) {
        clearInterval(watchlistCountdownInterval);
    }

    let remainingSeconds = initialSeconds;

    watchlistCountdownInterval = setInterval(async () => {
        remainingSeconds--;

        if (remainingSeconds <= 0) {
            // Timer expired, fetch fresh data
            try {
                const response = await fetch('/api/watchlist/count');
                const data = await response.json();
                remainingSeconds = data.next_run_in_seconds || 0;

                const timerElement = document.getElementById('watchlist-next-auto-timer');
                if (timerElement) {
                    const countdownText = formatCountdownTime(remainingSeconds);
                    timerElement.textContent = `Next Auto${countdownText ? ': ' + countdownText : ''}`;
                }
            } catch (error) {
                console.debug('Error updating watchlist countdown:', error);
            }
        } else {
            // Update the display
            const timerElement = document.getElementById('watchlist-next-auto-timer');
            if (timerElement) {
                const countdownText = formatCountdownTime(remainingSeconds);
                timerElement.textContent = `Next Auto${countdownText ? ': ' + countdownText : ''}`;
            }
        }
    }, 1000); // Update every second
}

/**
 * Close watchlist modal
 */
function closeWatchlistModal() {
    // Stop countdown timer
    if (watchlistCountdownInterval) {
        clearInterval(watchlistCountdownInterval);
        watchlistCountdownInterval = null;
    }

    const modal = document.getElementById('watchlist-modal');
    if (modal) {
        modal.style.display = 'none';
    }
}

/**
 * Populate the linked provider section in the watchlist config modal.
 * Shows which Spotify/iTunes/Deezer artist is linked and allows changing it.
 */
function _populateLinkedProviderSection(artistId, artistName, spotifyId, itunesId, artistInfo, deezerId, discogsId) {
    const section = document.getElementById('watchlist-linked-provider-section');
    const content = document.getElementById('watchlist-linked-provider-content');
    if (!section || !content) return;

    section.style.display = '';

    const sources = [
        { key: 'spotify', label: 'Spotify', icon: '🟢', id: spotifyId || '', color: '#1db954' },
        { key: 'itunes', label: 'Apple Music', icon: '🔴', id: itunesId || '', color: '#fc3c44' },
        { key: 'deezer', label: 'Deezer', icon: '🟣', id: deezerId || '', color: '#a238ff' },
        { key: 'discogs', label: 'Discogs', icon: '🟤', id: discogsId || '', color: '#b08968' },
    ];

    let html = '<div class="wl-linked-sources">';
    for (const src of sources) {
        const matched = !!src.id;
        const shortId = src.id ? (src.id.length > 16 ? src.id.substring(0, 14) + '...' : src.id) : '';
        html += `
            <div class="wl-linked-row ${matched ? 'matched' : 'unmatched'}" data-source="${src.key}">
                <span class="wl-linked-icon">${src.icon}</span>
                <span class="wl-linked-label">${src.label}</span>
                <span class="wl-linked-status">${matched
                ? `<span class="wl-linked-id" title="${escapeHtml(src.id)}">${shortId}</span>`
                : '<span class="wl-linked-none">Not matched</span>'
            }</span>
                <button class="wl-linked-fix-btn" onclick="_openSourceSearch('${src.key}', '${escapeForInlineJs(artistId)}', '${escapeForInlineJs(artistName)}')">${matched ? 'Fix' : 'Match'}</button>
                ${matched ? `<button class="wl-linked-clear-btn" onclick="_clearSourceMatch('${src.key}', '${escapeForInlineJs(artistId)}', '${escapeForInlineJs(artistName)}')" title="Clear this match">&times;</button>` : ''}
            </div>`;
    }
    html += '</div>';

    // Per-source search panel (hidden, populated on Fix/Match click)
    html += `<div class="wl-linked-search-panel" id="wl-linked-search-panel" style="display:none">
        <div class="wl-linked-search-header">
            <span id="wl-linked-search-title">Search</span>
            <button class="wl-linked-search-close" onclick="document.getElementById('wl-linked-search-panel').style.display='none'">&times;</button>
        </div>
        <div class="wl-linked-search-input-row">
            <input type="text" id="wl-linked-search-input" class="watchlist-linked-search-input"
                   placeholder="Search..." value="${escapeHtml(artistName)}">
            <button class="watchlist-linked-search-btn" id="wl-linked-search-go">Search</button>
        </div>
        <div class="wl-linked-search-results" id="wl-linked-search-results"></div>
    </div>`;

    content.innerHTML = html;
}

/**
 * Open per-source search panel for fixing a specific provider match.
 */
function _openSourceSearch(sourceKey, artistId, artistName) {
    const panel = document.getElementById('wl-linked-search-panel');
    if (!panel) return;
    const labels = { spotify: 'Spotify', itunes: 'Apple Music', deezer: 'Deezer', discogs: 'Discogs' };
    document.getElementById('wl-linked-search-title').textContent = `Search ${labels[sourceKey] || sourceKey}`;
    const input = document.getElementById('wl-linked-search-input');
    input.value = artistName;
    document.getElementById('wl-linked-search-results').innerHTML = '';
    panel.style.display = '';
    panel.dataset.source = sourceKey;
    panel.dataset.artistId = artistId;
    panel.dataset.artistName = artistName;
    input.focus();
    input.select();

    const doSearch = () => _searchSourceArtists(sourceKey, artistId);
    document.getElementById('wl-linked-search-go').onclick = doSearch;
    input.onkeydown = (e) => { if (e.key === 'Enter') doSearch(); };
}

async function _searchSourceArtists(sourceKey, watchlistArtistId) {
    const input = document.getElementById('wl-linked-search-input');
    const container = document.getElementById('wl-linked-search-results');
    const query = input?.value?.trim();
    if (!query || !container) return;

    container.innerHTML = '<div style="padding:12px;color:#888;text-align:center">Searching...</div>';

    try {
        const response = await fetch('/api/library/search-service', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ service: sourceKey, entity_type: 'artist', query })
        });
        const data = await response.json();
        if (!data.success) throw new Error(data.error);

        const results = data.results || [];
        if (!results.length) {
            container.innerHTML = '<div style="padding:12px;color:#888;text-align:center">No artists found</div>';
            return;
        }

        let html = '';
        for (const r of results) {
            html += `<div class="watchlist-linked-search-result" data-id="${escapeHtml(r.id)}" data-name="${escapeHtml(r.name)}">
                ${r.image ? `<img src="${r.image}" alt="" class="watchlist-linked-result-img">` :
                    `<div class="watchlist-linked-result-img-placeholder">🎵</div>`}
                <div class="watchlist-linked-result-info">
                    <div class="watchlist-linked-result-name">${escapeHtml(r.name)}</div>
                    <div class="watchlist-linked-result-meta">${escapeHtml(r.extra || '')}</div>
                </div>
                <button class="watchlist-linked-select-btn">Select</button>
            </div>`;
        }
        container.innerHTML = html;

        container.querySelectorAll('.watchlist-linked-search-result').forEach(el => {
            el.querySelector('.watchlist-linked-select-btn').onclick = async (e) => {
                e.stopPropagation();
                await _linkSourceArtist(sourceKey, watchlistArtistId, el.dataset.id, el.dataset.name);
            };
        });
    } catch (err) {
        console.error(`Error searching ${sourceKey}:`, err);
        container.innerHTML = '<div style="padding:12px;color:#f44;text-align:center">Search error</div>';
    }
}

async function _linkSourceArtist(sourceKey, watchlistArtistId, newId, newName) {
    try {
        const response = await fetch(`/api/watchlist/artist/${watchlistArtistId}/link-provider`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ provider_id: newId, provider: sourceKey })
        });
        const data = await response.json();
        if (!data.success) {
            showToast(`Failed to link: ${data.error}`, 'error');
            return;
        }
        showToast(`Linked to "${newName}" on ${sourceKey}`, 'success');
        // Refresh the modal
        const panel = document.getElementById('wl-linked-search-panel');
        const artistName = panel?.dataset?.artistName || newName;
        closeWatchlistArtistConfigModal();
        setTimeout(() => openWatchlistArtistConfigModal(watchlistArtistId, artistName), 300);
    } catch (err) {
        showToast('Failed to link artist', 'error');
    }
}

async function _clearSourceMatch(sourceKey, watchlistArtistId, artistName) {
    try {
        const response = await fetch(`/api/watchlist/artist/${watchlistArtistId}/link-provider`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ provider_id: '', provider: sourceKey })
        });
        const data = await response.json();
        if (!data.success) {
            showToast(`Failed to clear: ${data.error}`, 'error');
            return;
        }
        showToast(`Cleared ${sourceKey} match`, 'success');
        closeWatchlistArtistConfigModal();
        setTimeout(() => openWatchlistArtistConfigModal(watchlistArtistId, artistName), 300);
    } catch (err) {
        showToast('Failed to clear match', 'error');
    }
}

/**
 * Open watchlist artist configuration modal
 * @param {string} artistId - Spotify artist ID
 * @param {string} artistName - Artist name
 */
async function openWatchlistArtistConfigModal(artistId, artistName) {
    try {
        console.log(`🎨 Opening config modal for artist: ${artistName} (${artistId})`);

        // Fetch artist config and info
        const response = await fetch(`/api/watchlist/artist/${artistId}/config`);
        const data = await response.json();

        if (!data.success) {
            console.error('Error loading artist config:', data.error);
            showToast(`Error loading artist configuration: ${data.error}`, 'error');
            return;
        }

        const { config, artist, spotify_artist_id, itunes_artist_id, deezer_artist_id, discogs_artist_id, watchlist_name } = data;

        // Populate linked provider section (use DB watchlist_name for mismatch comparison)
        _populateLinkedProviderSection(artistId, watchlist_name || artistName, spotify_artist_id, itunes_artist_id, artist, deezer_artist_id, discogs_artist_id);

        // Check if global override is active
        let globalOverrideActive = false;
        try {
            const globalResponse = await fetch('/api/watchlist/global-config');
            const globalData = await globalResponse.json();
            globalOverrideActive = globalData.success && globalData.config.global_override_enabled;
        } catch (e) {
            console.debug('Could not check global config:', e);
        }

        // Generate hero section
        const heroHTML = `
            ${artist.image_url ? `
                <img src="${artist.image_url}"
                     alt="${escapeHtml(artist.name)}"
                     class="watchlist-artist-config-hero-image"
                     loading="lazy">
            ` : ''}
            <div class="watchlist-artist-config-hero-info">
                <h2 class="watchlist-artist-config-hero-name">${escapeHtml(artist.name)}</h2>
                <div class="watchlist-artist-config-hero-stats">
                    <div class="watchlist-artist-config-stat">
                        <span class="watchlist-artist-config-stat-value">${formatNumber(artist.followers)}</span>
                        <span class="watchlist-artist-config-stat-label">Followers</span>
                    </div>
                    <div class="watchlist-artist-config-stat">
                        <span class="watchlist-artist-config-stat-value">${artist.popularity}/100</span>
                        <span class="watchlist-artist-config-stat-label">Popularity</span>
                    </div>
                </div>
                ${artist.genres && artist.genres.length > 0 ? `
                    <div class="watchlist-artist-config-hero-genres">
                        ${artist.genres.slice(0, 3).map(genre =>
            `<span class="watchlist-artist-config-genre-tag">${escapeHtml(genre)}</span>`
        ).join('')}
                    </div>
                ` : ''}
            </div>
        `;

        // Populate hero section
        const heroContainer = document.getElementById('watchlist-artist-config-hero');
        if (heroContainer) {
            heroContainer.innerHTML = heroHTML;
        }

        // Set checkbox states
        document.getElementById('config-include-albums').checked = config.include_albums;
        document.getElementById('config-include-eps').checked = config.include_eps;
        document.getElementById('config-include-singles').checked = config.include_singles;
        document.getElementById('config-include-live').checked = config.include_live || false;
        document.getElementById('config-include-remixes').checked = config.include_remixes || false;
        document.getElementById('config-include-acoustic').checked = config.include_acoustic || false;
        document.getElementById('config-include-compilations').checked = config.include_compilations || false;
        document.getElementById('config-include-instrumentals').checked = config.include_instrumentals || false;
        document.getElementById('config-lookback-days').value = config.lookback_days != null ? String(config.lookback_days) : '';

        // Populate metadata source selector
        const sourceSelector = document.getElementById('config-metadata-source-selector');
        if (sourceSelector) {
            const sources = [
                { key: 'spotify', label: 'Spotify', id: spotify_artist_id, color: '#1DB954' },
                { key: 'deezer', label: 'Deezer', id: deezer_artist_id, color: '#A238FF' },
                { key: 'itunes', label: 'Apple Music', id: itunes_artist_id, color: '#FC3C44' },
                { key: 'discogs', label: 'Discogs', id: discogs_artist_id, color: '#333' },
            ];
            const globalSource = data.global_metadata_source || 'deezer';
            const currentOverride = config.preferred_metadata_source;
            const globalLabel = { spotify: 'Spotify', deezer: 'Deezer', itunes: 'Apple Music', discogs: 'Discogs' }[globalSource] || globalSource;

            let html = `<button class="config-msrc-btn ${!currentOverride ? 'active' : ''}" data-source="" title="Use global default (${globalLabel})">
                <span class="config-msrc-icon">🌐</span><span class="config-msrc-label">Default (${globalLabel})</span>
            </button>`;
            for (const src of sources) {
                if (!src.id) continue;
                const isActive = currentOverride === src.key;
                html += `<button class="config-msrc-btn ${isActive ? 'active' : ''}" data-source="${src.key}" style="${isActive ? 'border-color:' + src.color : ''}" title="${src.label}">
                    <span class="config-msrc-label">${src.label}</span>
                </button>`;
            }
            sourceSelector.innerHTML = html;
            sourceSelector.querySelectorAll('.config-msrc-btn').forEach(btn => {
                btn.addEventListener('click', () => {
                    sourceSelector.querySelectorAll('.config-msrc-btn').forEach(b => {
                        b.classList.remove('active');
                        b.style.borderColor = '';
                    });
                    btn.classList.add('active');
                    const color = sources.find(s => s.key === btn.dataset.source)?.color;
                    if (color) btn.style.borderColor = color;
                });
            });
        }

        // Show global override notice if active
        const existingNotice = document.querySelector('.global-override-notice');
        if (existingNotice) existingNotice.remove();

        if (globalOverrideActive) {
            const notice = document.createElement('div');
            notice.className = 'global-override-notice watchlist-global-override-banner';
            notice.innerHTML = '<span>⚠️</span><span>Global override is active — these per-artist settings are currently ignored during scans.</span>';
            const configBody = document.querySelector('.watchlist-artist-config-body');
            if (configBody) configBody.insertBefore(notice, configBody.firstChild);
        }

        // Store artist ID for saving
        const modal = document.getElementById('watchlist-artist-config-modal');
        if (modal) {
            modal.setAttribute('data-artist-id', artistId);
        }

        // Show modal
        const overlay = document.getElementById('watchlist-artist-config-modal-overlay');
        if (overlay) {
            overlay.classList.remove('hidden');
        }

        // Add save button handler
        const saveBtn = document.getElementById('save-artist-config-btn');
        if (saveBtn) {
            // Remove old listeners
            const newSaveBtn = saveBtn.cloneNode(true);
            saveBtn.parentNode.replaceChild(newSaveBtn, saveBtn);

            // Add new listener
            newSaveBtn.addEventListener('click', () => saveWatchlistArtistConfig(artistId));
        }

    } catch (error) {
        console.error('Error opening watchlist artist config modal:', error);
        showToast(`Error: ${error.message}`, 'error');
    }
}

/**
 * Close watchlist artist configuration modal
 */
function closeWatchlistArtistConfigModal() {
    const overlay = document.getElementById('watchlist-artist-config-modal-overlay');
    if (overlay) {
        overlay.classList.add('hidden');
    }

    // Clear hero content
    const heroContainer = document.getElementById('watchlist-artist-config-hero');
    if (heroContainer) {
        heroContainer.innerHTML = '';
    }

    // Clear linked provider section
    const linkedContent = document.getElementById('watchlist-linked-provider-content');
    if (linkedContent) linkedContent.innerHTML = '';
    const linkedSection = document.getElementById('watchlist-linked-provider-section');
    if (linkedSection) linkedSection.style.display = 'none';
}

/**
 * Open watchlist artist detail view (slides in from right)
 */
async function openWatchlistArtistDetailView(artistId, artistName) {
    try {
        const response = await fetch(`/api/watchlist/artist/${artistId}/config`);
        const data = await response.json();

        if (!data.success) {
            showToast(`Error loading artist info: ${data.error}`, 'error');
            return;
        }

        const { config, artist, recent_releases, spotify_artist_id, itunes_artist_id, deezer_artist_id, discogs_artist_id } = data;

        // Remove existing overlay if any
        const existing = document.querySelector('.watchlist-artist-detail-overlay');
        if (existing) existing.remove();

        const overlay = document.createElement('div');
        overlay.className = 'watchlist-artist-detail-overlay';

        // Build pills
        const pills = [];
        if (config.include_albums) pills.push('<span class="watchlist-pill watchlist-pill-active">Albums</span>');
        if (config.include_eps) pills.push('<span class="watchlist-pill watchlist-pill-active">EPs</span>');
        if (config.include_singles) pills.push('<span class="watchlist-pill watchlist-pill-active">Singles</span>');
        if (config.include_live) pills.push('<span class="watchlist-pill watchlist-pill-filter">Live</span>');
        if (config.include_remixes) pills.push('<span class="watchlist-pill watchlist-pill-filter">Remixes</span>');
        if (config.include_acoustic) pills.push('<span class="watchlist-pill watchlist-pill-filter">Acoustic</span>');
        if (config.include_compilations) pills.push('<span class="watchlist-pill watchlist-pill-filter">Compilations</span>');

        // Build scan info
        const scanTimeText = config.last_scan_timestamp ? formatRelativeScanTime(config.last_scan_timestamp) : 'Never scanned';
        const dateAddedText = config.date_added ? `Added ${new Date(config.date_added).toLocaleDateString()}` : '';

        // Build metadata tags (style, mood, label)
        const metaTags = [];
        if (artist.style) metaTags.push(`<span class="watchlist-detail-genre-tag">${escapeHtml(artist.style)}</span>`);
        if (artist.mood) metaTags.push(`<span class="watchlist-detail-genre-tag">${escapeHtml(artist.mood)}</span>`);
        if (artist.label) metaTags.push(`<span class="watchlist-detail-genre-tag">${escapeHtml(artist.label)}</span>`);

        overlay.innerHTML = `
            ${artist.banner_url ? `
                <div class="watchlist-detail-banner">
                    <img src="${artist.banner_url}" alt="" onerror="this.parentElement.remove();">
                    <div class="watchlist-detail-banner-fade"></div>
                </div>
            ` : ''}

            <div class="watchlist-detail-content ${artist.banner_url ? 'has-banner' : ''}">
                <button class="watchlist-detail-back watchlist-detail-back-btn">
                    ← Back to Watchlist
                </button>

                <div class="watchlist-detail-hero">
                    ${artist.image_url ? `<img src="${artist.image_url}" alt="${escapeHtml(artist.name)}" onerror="this.style.display='none';">` : ''}
                    <div class="watchlist-detail-hero-info">
                        <h2 class="watchlist-detail-hero-name">${escapeHtml(artist.name)}</h2>
                        ${artist.followers || artist.popularity ? `
                        <div class="watchlist-detail-hero-stats">
                            ${artist.followers ? `
                            <div class="watchlist-detail-stat">
                                <span class="watchlist-detail-stat-value">${formatNumber(artist.followers)}</span>
                                <span class="watchlist-detail-stat-label">Followers</span>
                            </div>` : ''}
                            ${artist.popularity ? `
                            <div class="watchlist-detail-stat">
                                <span class="watchlist-detail-stat-value">${artist.popularity}/100</span>
                                <span class="watchlist-detail-stat-label">Popularity</span>
                            </div>` : ''}
                        </div>
                        ` : ''}
                        ${artist.genres && artist.genres.length > 0 ? `
                            <div class="watchlist-detail-hero-genres">
                                ${artist.genres.map(g => `<span class="watchlist-detail-genre-tag">${escapeHtml(g)}</span>`).join('')}
                            </div>
                        ` : ''}
                    </div>
                </div>

                ${artist.summary ? `
                <div class="watchlist-detail-section">
                    <div class="watchlist-detail-section-title">About</div>
                    <p class="watchlist-detail-bio">${escapeHtml(artist.summary)}</p>
                </div>
                ` : ''}

                ${metaTags.length > 0 ? `
                <div class="watchlist-detail-section">
                    <div class="watchlist-detail-section-title">Info</div>
                    <div class="watchlist-detail-hero-genres">${metaTags.join('')}</div>
                </div>
                ` : ''}

                ${recent_releases && recent_releases.length > 0 ? `
                <div class="watchlist-detail-section">
                    <div class="watchlist-detail-section-title">Recent Releases</div>
                    <div class="watchlist-detail-releases">
                        ${recent_releases.map(r => `
                            <div class="watchlist-detail-release">
                                ${r.album_cover_url ? `<img src="${r.album_cover_url}" alt="" onerror="this.style.display='none';">` : ''}
                                <div class="watchlist-detail-release-info">
                                    <span class="watchlist-detail-release-name">${escapeHtml(r.album_name)}</span>
                                    <span class="watchlist-detail-release-meta">${r.release_date}${r.track_count ? ` · ${r.track_count} tracks` : ''}</span>
                                </div>
                            </div>
                        `).join('')}
                    </div>
                </div>
                ` : ''}

                <div class="watchlist-detail-section">
                    <div class="watchlist-detail-section-title">Watchlist</div>
                    <div class="watchlist-detail-watchlist-info">
                        <span class="watchlist-card-meta">${scanTimeText}</span>
                        ${dateAddedText ? `<span class="watchlist-detail-info-sep">·</span><span class="watchlist-card-meta">${dateAddedText}</span>` : ''}
                    </div>
                    <div class="watchlist-card-pills" style="padding: 0; margin-top: 8px;">
                        ${pills.length > 0 ? pills.join('') : '<span class="watchlist-card-meta">No release types enabled</span>'}
                    </div>
                </div>

                <div class="watchlist-detail-actions">
                    <button class="watchlist-detail-discog-btn watchlist-detail-discog-action">View Discography</button>
                    <button class="watchlist-detail-settings-btn watchlist-detail-settings-action">Settings</button>
                    <button class="watchlist-detail-remove-btn watchlist-detail-remove-action">Remove from Watchlist</button>
                </div>
            </div>
        `;

        // Wire up event listeners (avoids inline onclick escaping issues)
        overlay.querySelector('.watchlist-detail-back-btn').addEventListener('click', () => {
            closeWatchlistArtistDetailView();
        });

        overlay.querySelector('.watchlist-detail-discog-action').addEventListener('click', () => {
            // Use the ID matching the active metadata source
            let discogId, source;
            const activeSrc = (currentMusicSourceName || '').toLowerCase();
            if (activeSrc.includes('spotify') && spotify_artist_id) {
                discogId = spotify_artist_id; source = 'spotify';
            } else if (activeSrc.includes('discogs') && discogs_artist_id) {
                discogId = discogs_artist_id; source = 'discogs';
            } else if (activeSrc.includes('deezer') && deezer_artist_id) {
                discogId = deezer_artist_id; source = 'deezer';
            } else if (itunes_artist_id) {
                discogId = itunes_artist_id; source = 'itunes';
            } else {
                discogId = spotify_artist_id || discogs_artist_id || deezer_artist_id || itunes_artist_id;
                source = spotify_artist_id ? 'spotify' : discogs_artist_id ? 'discogs' : deezer_artist_id ? 'deezer' : 'itunes';
            }
            if (discogId) {
                closeWatchlistArtistDetailView();
                navigateToArtistDetail(discogId, artistName, source);
            }
        });

        overlay.querySelector('.watchlist-detail-settings-action').addEventListener('click', () => {
            // Remove overlay immediately so it doesn't block the config modal
            const detailOverlay = document.querySelector('.watchlist-artist-detail-overlay');
            if (detailOverlay) detailOverlay.remove();
            openWatchlistArtistConfigModal(artistId, artistName);
        });

        overlay.querySelector('.watchlist-detail-remove-action').addEventListener('click', () => {
            removeFromWatchlistModal(artistId, artistName);
        });

        // Append to body as a fixed overlay
        document.body.appendChild(overlay);
        // Trigger slide-in animation
        requestAnimationFrame(() => overlay.classList.add('visible'));

    } catch (error) {
        console.error('Error opening artist detail view:', error);
        showToast(`Error: ${error.message}`, 'error');
    }
}

/**
 * Close watchlist artist detail view (slides out)
 */
function closeWatchlistArtistDetailView() {
    const overlay = document.querySelector('.watchlist-artist-detail-overlay');
    if (overlay) {
        overlay.classList.remove('visible');
        overlay.addEventListener('transitionend', () => overlay.remove(), { once: true });
    }
}

/**
 * Open global watchlist settings modal
 */
async function openWatchlistGlobalSettingsModal() {
    try {
        const response = await fetch('/api/watchlist/global-config');
        const data = await response.json();

        if (!data.success) {
            showToast(`Error loading global settings: ${data.error}`, 'error');
            return;
        }

        const config = data.config;

        // Populate checkboxes
        document.getElementById('global-override-enabled').checked = config.global_override_enabled;
        document.getElementById('global-include-albums').checked = config.include_albums;
        document.getElementById('global-include-eps').checked = config.include_eps;
        document.getElementById('global-include-singles').checked = config.include_singles;
        document.getElementById('global-include-live').checked = config.include_live;
        document.getElementById('global-include-remixes').checked = config.include_remixes;
        document.getElementById('global-include-acoustic').checked = config.include_acoustic;
        document.getElementById('global-include-compilations').checked = config.include_compilations;
        document.getElementById('global-include-instrumentals').checked = config.include_instrumentals;
        document.getElementById('global-exclude-terms').value = config.exclude_terms || '';

        // Sync "Include Everything" checkbox
        syncGlobalIncludeAllCheckbox();

        // Update options visibility based on toggle state
        toggleGlobalOverrideOptions();

        // Update toggle label border
        const toggleLabel = document.getElementById('global-override-toggle-label');
        if (toggleLabel) {
            toggleLabel.style.border = config.global_override_enabled
                ? '2px solid rgba(29, 185, 84, 0.5)'
                : '2px solid rgba(255, 255, 255, 0.1)';
        }

        // Show modal
        const overlay = document.getElementById('watchlist-global-config-modal-overlay');
        if (overlay) overlay.classList.remove('hidden');

    } catch (error) {
        console.error('Error opening global watchlist settings:', error);
        showToast(`Error: ${error.message}`, 'error');
    }
}

/**
 * Close global watchlist settings modal
 */
function closeWatchlistGlobalSettingsModal() {
    const overlay = document.getElementById('watchlist-global-config-modal-overlay');
    if (overlay) overlay.classList.add('hidden');
}

/**
 * Toggle global override options visibility
 */
function toggleGlobalOverrideOptions() {
    const enabled = document.getElementById('global-override-enabled').checked;
    const options = document.getElementById('global-override-options');
    if (options) {
        options.style.opacity = enabled ? '1' : '0.4';
        options.style.pointerEvents = enabled ? 'auto' : 'none';
    }

    // Update toggle label border
    const toggleLabel = document.getElementById('global-override-toggle-label');
    if (toggleLabel) {
        toggleLabel.style.border = enabled
            ? '2px solid rgba(29, 185, 84, 0.5)'
            : '2px solid rgba(255, 255, 255, 0.1)';
    }
}

/**
 * Toggle all global include checkboxes
 */
function toggleGlobalIncludeAll() {
    const checked = document.getElementById('global-include-all').checked;
    ['global-include-albums', 'global-include-eps', 'global-include-singles',
        'global-include-live', 'global-include-remixes', 'global-include-acoustic',
        'global-include-compilations', 'global-include-instrumentals'].forEach(id => {
            const el = document.getElementById(id);
            if (el) el.checked = checked;
        });
}

/**
 * Sync the "Include Everything" checkbox based on individual checkbox states
 */
function syncGlobalIncludeAllCheckbox() {
    const allIds = ['global-include-albums', 'global-include-eps', 'global-include-singles',
        'global-include-live', 'global-include-remixes', 'global-include-acoustic',
        'global-include-compilations', 'global-include-instrumentals'];
    const allChecked = allIds.every(id => {
        const el = document.getElementById(id);
        return el && el.checked;
    });
    const includeAllEl = document.getElementById('global-include-all');
    if (includeAllEl) includeAllEl.checked = allChecked;
}

/**
 * Save global watchlist configuration
 */
async function saveWatchlistGlobalConfig() {
    try {
        const globalOverrideEnabled = document.getElementById('global-override-enabled').checked;
        const includeAlbums = document.getElementById('global-include-albums').checked;
        const includeEps = document.getElementById('global-include-eps').checked;
        const includeSingles = document.getElementById('global-include-singles').checked;
        const includeLive = document.getElementById('global-include-live').checked;
        const includeRemixes = document.getElementById('global-include-remixes').checked;
        const includeAcoustic = document.getElementById('global-include-acoustic').checked;
        const includeCompilations = document.getElementById('global-include-compilations').checked;
        const includeInstrumentals = document.getElementById('global-include-instrumentals').checked;
        const excludeTerms = (document.getElementById('global-exclude-terms').value || '').trim();

        if (globalOverrideEnabled && !includeAlbums && !includeEps && !includeSingles) {
            showToast('Please select at least one release type', 'error');
            return;
        }

        const saveBtn = document.getElementById('save-global-config-btn');
        if (saveBtn) {
            saveBtn.disabled = true;
            saveBtn.textContent = 'Saving...';
        }

        const response = await fetch('/api/watchlist/global-config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                global_override_enabled: globalOverrideEnabled,
                include_albums: includeAlbums,
                include_eps: includeEps,
                include_singles: includeSingles,
                include_live: includeLive,
                include_remixes: includeRemixes,
                include_acoustic: includeAcoustic,
                include_compilations: includeCompilations,
                include_instrumentals: includeInstrumentals,
                exclude_terms: excludeTerms,
            })
        });

        const data = await response.json();

        if (data.success) {
            showToast('Global watchlist settings saved', 'success');
            closeWatchlistGlobalSettingsModal();

            // Refresh the watchlist page to update the grid
            if (currentPage === 'watchlist') {
                watchlistPageState.isInitialized = false;
                await initializeWatchlistPage();
            }
        } else {
            showToast(`Error: ${data.error}`, 'error');
        }

    } catch (error) {
        console.error('Error saving global config:', error);
        showToast(`Error: ${error.message}`, 'error');
    } finally {
        const saveBtn = document.getElementById('save-global-config-btn');
        if (saveBtn) {
            saveBtn.disabled = false;
            saveBtn.textContent = 'Save Global Settings';
        }
    }
}

/**
 * Save watchlist artist configuration
 * @param {string} artistId - Spotify artist ID
 */
async function saveWatchlistArtistConfig(artistId) {
    try {
        const includeAlbums = document.getElementById('config-include-albums').checked;
        const includeEps = document.getElementById('config-include-eps').checked;
        const includeSingles = document.getElementById('config-include-singles').checked;
        const includeLive = document.getElementById('config-include-live').checked;
        const includeRemixes = document.getElementById('config-include-remixes').checked;
        const includeAcoustic = document.getElementById('config-include-acoustic').checked;
        const includeCompilations = document.getElementById('config-include-compilations').checked;
        const includeInstrumentals = document.getElementById('config-include-instrumentals').checked;
        const lookbackDaysVal = document.getElementById('config-lookback-days').value;
        const lookbackDays = lookbackDaysVal !== '' ? parseInt(lookbackDaysVal) : null;
        const activeSourceBtn = document.querySelector('#config-metadata-source-selector .config-msrc-btn.active');
        const preferredMetadataSource = activeSourceBtn ? (activeSourceBtn.dataset.source || null) : null;

        // Validate at least one release type is selected
        if (!includeAlbums && !includeEps && !includeSingles) {
            showToast('Please select at least one release type', 'error');
            return;
        }

        // Disable save button
        const saveBtn = document.getElementById('save-artist-config-btn');
        if (saveBtn) {
            saveBtn.disabled = true;
            saveBtn.textContent = 'Saving...';
        }

        // Send update to backend
        const response = await fetch(`/api/watchlist/artist/${artistId}/config`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                include_albums: includeAlbums,
                include_eps: includeEps,
                include_singles: includeSingles,
                include_live: includeLive,
                include_remixes: includeRemixes,
                include_acoustic: includeAcoustic,
                include_compilations: includeCompilations,
                include_instrumentals: includeInstrumentals,
                lookback_days: lookbackDays,
                preferred_metadata_source: preferredMetadataSource,
            })
        });

        const data = await response.json();

        if (data.success) {
            showToast('Artist preferences saved successfully', 'success');
            closeWatchlistArtistConfigModal();

            // Refresh watchlist page if we're on it
            if (currentPage === 'watchlist') {
                watchlistPageState.isInitialized = false;
                await initializeWatchlistPage();
            }
        } else {
            showToast(`Error saving preferences: ${data.error}`, 'error');
        }

    } catch (error) {
        console.error('Error saving watchlist artist config:', error);
        showToast(`Error: ${error.message}`, 'error');
    } finally {
        // Re-enable save button
        const saveBtn = document.getElementById('save-artist-config-btn');
        if (saveBtn) {
            saveBtn.disabled = false;
            saveBtn.textContent = 'Save Preferences';
        }
    }
}

/**
 * Format large numbers with commas
 * @param {number} num - Number to format
 * @returns {string} Formatted number
 */
function formatNumber(num) {
    if (!num) return '0';
    return num.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

/**
 * Format last scan timestamp as relative time
 */
function formatRelativeScanTime(isoString) {
    if (!isoString) return 'Never scanned';
    const diff = Date.now() - new Date(isoString).getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return 'Scanned just now';
    if (mins < 60) return `Scanned ${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `Scanned ${hrs}h ago`;
    const days = Math.floor(hrs / 24);
    if (days < 30) return `Scanned ${days}d ago`;
    const months = Math.floor(days / 30);
    return `Scanned ${months}mo ago`;
}

/**
 * Filter watchlist artists based on search input
 */
function filterWatchlistArtists() {
    const searchInput = document.getElementById('watchlist-search-input');
    const artistsList = document.getElementById('watchlist-artists-list');

    if (!searchInput || !artistsList) return;

    const searchTerm = searchInput.value.toLowerCase().trim();
    const artistItems = artistsList.querySelectorAll('.watchlist-artist-card');

    artistItems.forEach(item => {
        const artistName = item.getAttribute('data-artist-name');

        if (!searchTerm || artistName.includes(searchTerm)) {
            item.style.display = '';
        } else {
            item.style.display = 'none';
        }
    });

    // Refresh batch bar in case visible selection changed
    updateWatchlistBatchBar();
}

/**
 * Start watchlist scan
 */
async function cancelWatchlistScan() {
    try {
        const cancelBtn = document.getElementById('cancel-watchlist-scan-btn');
        if (cancelBtn) {
            cancelBtn.disabled = true;
            cancelBtn.textContent = 'Cancelling...';
        }

        const response = await fetch('/api/watchlist/scan/cancel', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });

        const data = await response.json();
        if (!data.success) {
            throw new Error(data.error || 'Failed to cancel scan');
        }

        showToast('Cancel request sent — scan will stop after current artist', 'info');

    } catch (error) {
        console.error('Error cancelling watchlist scan:', error);
        showToast(`Error cancelling scan: ${error.message}`, 'error');
        const cancelBtn = document.getElementById('cancel-watchlist-scan-btn');
        if (cancelBtn) {
            cancelBtn.disabled = false;
            cancelBtn.textContent = 'Cancel Scan';
        }
    }
}

async function startWatchlistScan() {
    try {
        const button = document.getElementById('scan-watchlist-btn');
        button.disabled = true;
        button.textContent = 'Starting scan...';
        button.classList.add('btn-processing');

        const response = await fetch('/api/watchlist/scan', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });

        const data = await response.json();
        if (!data.success) {
            throw new Error(data.error || 'Failed to start scan');
        }

        button.textContent = 'Scanning...';

        // Show cancel button
        const cancelBtn = document.getElementById('cancel-watchlist-scan-btn');
        if (cancelBtn) {
            cancelBtn.style.display = '';
            cancelBtn.disabled = false;
            cancelBtn.textContent = 'Cancel Scan';
        }

        // Show scan status
        const statusDiv = document.getElementById('watchlist-scan-status');
        if (statusDiv) {
            statusDiv.style.display = 'flex';
        }

        // Start polling for updates
        pollWatchlistScanStatus();

    } catch (error) {
        console.error('Error starting watchlist scan:', error);
        const button = document.getElementById('scan-watchlist-btn');
        button.disabled = false;
        button.textContent = 'Scan for New Releases';
        button.classList.remove('btn-processing');
        alert(`Error starting scan: ${error.message}`);
    }
}

/**
 * Poll watchlist scan status
 */
function handleWatchlistScanData(data) {
    const button = document.getElementById('scan-watchlist-btn');
    const liveActivity = document.getElementById('watchlist-live-activity');

    // Show/hide cancel button based on scan status
    const cancelBtn = document.getElementById('cancel-watchlist-scan-btn');
    if (cancelBtn) {
        cancelBtn.style.display = data.status === 'scanning' ? '' : 'none';
    }

    // Update live visual activity display
    if (liveActivity && data.status === 'scanning') {
        liveActivity.style.display = 'flex';

        // Update artist image and name
        const artistImg = document.getElementById('watchlist-artist-img');
        const artistName = document.getElementById('watchlist-artist-name');
        if (artistImg && data.current_artist_image_url) {
            artistImg.src = data.current_artist_image_url;
            artistImg.style.display = 'block';
        }
        if (artistName) {
            artistName.textContent = data.current_artist_name || 'Processing...';
        }

        // Update album image and name
        const albumImg = document.getElementById('watchlist-album-img');
        const albumName = document.getElementById('watchlist-album-name');
        if (albumImg && data.current_album_image_url) {
            albumImg.src = data.current_album_image_url;
            albumImg.style.display = 'block';
        } else if (albumImg) {
            albumImg.style.display = 'none';
        }
        if (albumName) {
            albumName.textContent = data.current_album || (data.current_phase === 'fetching_discography' ? 'Fetching releases...' : 'Processing...');
        }

        // Update current track
        const trackName = document.getElementById('watchlist-track-name');
        if (trackName) {
            trackName.textContent = data.current_track_name || (data.current_phase === 'fetching_discography' ? 'Fetching releases...' : 'Processing...');
        }

        // Update wishlist additions feed
        const additionsFeed = document.getElementById('watchlist-additions-feed');
        if (additionsFeed) {
            if (data.recent_wishlist_additions && data.recent_wishlist_additions.length > 0) {
                additionsFeed.innerHTML = data.recent_wishlist_additions.map(item => `
                    <div class="watchlist-live-addition-item">
                        <img src="${item.album_image_url || ''}" alt="" onerror="this.style.display='none';" />
                        <div class="watchlist-live-addition-item-info">
                            <div class="watchlist-live-addition-item-track">${item.track_name}</div>
                            <div class="watchlist-live-addition-item-artist">${item.artist_name}</div>
                        </div>
                    </div>
                `).join('');
            } else {
                additionsFeed.innerHTML = '<div class="watchlist-live-addition-empty">No tracks added yet...</div>';
            }
        }
    } else if (liveActivity && data.status !== 'scanning') {
        liveActivity.style.display = 'none';
    }

    if (data.status === 'completed') {
        if (button) {
            button.disabled = false;
            button.textContent = 'Scan for New Releases';
            button.classList.remove('btn-processing');
        }

        // Hide live activity
        if (liveActivity) {
            liveActivity.style.display = 'none';
        }

        // Show completion message in status div
        const statusDiv = document.getElementById('watchlist-scan-status');
        if (statusDiv && data.summary) {
            const newTracks = data.summary.new_tracks_found || 0;
            const addedTracks = data.summary.tracks_added_to_wishlist || 0;
            const totalArtists = data.summary.total_artists || 0;
            const successfulScans = data.summary.successful_scans || 0;

            let completionMessage = `Scan completed: ${successfulScans}/${totalArtists} artists scanned`;
            if (newTracks > 0) {
                completionMessage += `, found ${newTracks} new track${newTracks !== 1 ? 's' : ''}`;
                if (addedTracks > 0) {
                    completionMessage += `, added ${addedTracks} to wishlist`;
                }
            } else {
                completionMessage += ', no new tracks found';
            }

            // Update the scan status display with completion message and summary
            statusDiv.innerHTML = `
                <div class="watchlist-scan-completion">
                    <div class="watchlist-scan-completion-message">${completionMessage}</div>
                    <div style="font-size: 13px; opacity: 0.8;">
                        <span class="sync-stat">Artists: ${totalArtists}</span>
                        <span class="sync-separator"> • </span>
                        <span class="sync-stat">New tracks: ${newTracks}</span>
                        <span class="sync-separator"> • </span>
                        <span class="sync-stat">Added to wishlist: ${addedTracks}</span>
                    </div>
                </div>
            `;
        }

        // Update watchlist count
        updateWatchlistButtonCount();

        console.log('Watchlist scan completed:', data.summary);

    } else if (data.status === 'cancelled') {
        if (button) {
            button.disabled = false;
            button.textContent = 'Scan for New Releases';
            button.classList.remove('btn-processing');
        }

        // Hide cancel button
        const cancelBtn = document.getElementById('cancel-watchlist-scan-btn');
        if (cancelBtn) {
            cancelBtn.style.display = 'none';
            cancelBtn.disabled = false;
            cancelBtn.textContent = 'Cancel Scan';
        }

        // Hide live activity
        if (liveActivity) {
            liveActivity.style.display = 'none';
        }

        // Show cancellation message
        const statusDiv = document.getElementById('watchlist-scan-status');
        if (statusDiv && data.summary) {
            const scanned = data.summary.total_artists || 0;
            const newTracks = data.summary.new_tracks_found || 0;
            const addedTracks = data.summary.tracks_added_to_wishlist || 0;

            statusDiv.innerHTML = `
                <div class="watchlist-scan-completion">
                    <div class="watchlist-scan-completion-message">Scan cancelled after ${scanned} artist${scanned !== 1 ? 's' : ''}</div>
                    <div style="font-size: 13px; opacity: 0.8;">
                        <span class="sync-stat">Scanned: ${scanned}</span>
                        <span class="sync-separator"> &bull; </span>
                        <span class="sync-stat">New tracks: ${newTracks}</span>
                        <span class="sync-separator"> &bull; </span>
                        <span class="sync-stat">Added to wishlist: ${addedTracks}</span>
                    </div>
                </div>
            `;
        }

        // Update watchlist count
        updateWatchlistButtonCount();

        showToast('Watchlist scan cancelled', 'info');
        console.log('Watchlist scan cancelled:', data.summary);

    } else if (data.status === 'error') {
        if (button) {
            button.disabled = false;
            button.textContent = 'Scan for New Releases';
            button.classList.remove('btn-processing');
        }

        // Hide cancel button
        const cancelBtn = document.getElementById('cancel-watchlist-scan-btn');
        if (cancelBtn) {
            cancelBtn.style.display = 'none';
        }

        // Hide live activity
        if (liveActivity) {
            liveActivity.style.display = 'none';
        }

        console.error('Watchlist scan error:', data.error);
    }
}

async function pollWatchlistScanStatus() {
    if (socketConnected) return; // Phase 5: WS handles scan updates
    try {
        const response = await fetch('/api/watchlist/scan/status');
        const data = await response.json();

        if (data.success) {
            handleWatchlistScanData(data);
            if (data.status === 'completed' || data.status === 'error' || data.status === 'cancelled') {
                return; // Stop polling
            }
        }

        // Continue polling if still scanning
        if (data.success && data.status === 'scanning') {
            setTimeout(pollWatchlistScanStatus, 2000); // Poll every 2 seconds
        }

    } catch (error) {
        console.error('Error polling watchlist scan status:', error);
    }
}

/**
 * Update similar artists for discovery feature
 */
async function updateSimilarArtists() {
    try {
        const button = document.getElementById('update-similar-artists-btn');
        const scanButton = document.getElementById('scan-watchlist-btn');

        button.disabled = true;
        button.textContent = 'Updating...';
        button.classList.add('btn-processing');
        if (scanButton) scanButton.disabled = true;

        const response = await fetch('/api/watchlist/update-similar-artists', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });

        const data = await response.json();
        if (!data.success) {
            throw new Error(data.error || 'Failed to update similar artists');
        }

        showToast('Updating similar artists in background...', 'success');

        // Poll for completion
        pollSimilarArtistsUpdate();

    } catch (error) {
        console.error('Error updating similar artists:', error);
        const button = document.getElementById('update-similar-artists-btn');
        const scanButton = document.getElementById('scan-watchlist-btn');

        button.disabled = false;
        button.textContent = 'Update Similar Artists';
        button.classList.remove('btn-processing');
        if (scanButton) scanButton.disabled = false;

        showToast(`Error: ${error.message}`, 'error');
    }
}

/**
 * Poll similar artists update status
 */
async function pollSimilarArtistsUpdate() {
    try {
        const response = await fetch('/api/watchlist/similar-artists-status');
        const data = await response.json();

        if (data.success) {
            const button = document.getElementById('update-similar-artists-btn');
            const scanButton = document.getElementById('scan-watchlist-btn');

            if (data.status === 'completed') {
                if (button) {
                    button.disabled = false;
                    button.textContent = 'Update Similar Artists';
                    button.classList.remove('btn-processing');
                }
                if (scanButton) scanButton.disabled = false;

                showToast(`Updated similar artists for ${data.artists_processed || 0} artists!`, 'success');
                return; // Stop polling

            } else if (data.status === 'error') {
                if (button) {
                    button.disabled = false;
                    button.textContent = 'Update Similar Artists';
                    button.classList.remove('btn-processing');
                }
                if (scanButton) scanButton.disabled = false;

                showToast('Error updating similar artists', 'error');
                return; // Stop polling
            } else if (data.status === 'running') {
                // Update button text with progress
                if (button && data.current_artist) {
                    button.textContent = `Updating... (${data.artists_processed || 0}/${data.total_artists || 0})`;
                }
            }
        }

        // Continue polling if still running
        if (data.success && data.status === 'running') {
            setTimeout(pollSimilarArtistsUpdate, 1000); // Poll every 1 second
        }

    } catch (error) {
        console.error('Error polling similar artists update:', error);
        const button = document.getElementById('update-similar-artists-btn');
        const scanButton = document.getElementById('scan-watchlist-btn');

        if (button) {
            button.disabled = false;
            button.textContent = 'Update Similar Artists';
            button.classList.remove('btn-processing');
        }
        if (scanButton) scanButton.disabled = false;
    }
}

/**
 * Remove artist from watchlist via modal
 */
async function removeFromWatchlistModal(artistId, artistName) {
    try {
        const response = await fetch('/api/watchlist/remove', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ artist_id: artistId })
        });

        const data = await response.json();
        if (!data.success) {
            throw new Error(data.error || 'Failed to remove from watchlist');
        }

        console.log(`❌ Removed ${artistName} from watchlist`);

        // Close detail view if open
        closeWatchlistArtistDetailView();

        // Refresh the watchlist page
        watchlistPageState.isInitialized = false;
        await initializeWatchlistPage();

        // Update button count
        updateWatchlistButtonCount();

        // Update any visible artist cards
        updateArtistCardWatchlistStatus();

    } catch (error) {
        console.error('Error removing from watchlist:', error);
        alert(`Error removing ${artistName} from watchlist: ${error.message}`);
    }
}


/**
 * Get visible checked checkboxes (not hidden by search filter)
 */
function getVisibleCheckedWatchlist() {
    return Array.from(document.querySelectorAll('.watchlist-select-cb:checked')).filter(cb => {
        const item = cb.closest('.watchlist-artist-card');
        return item && item.style.display !== 'none';
    });
}

/**
 * Update the batch action bar based on checkbox selection
 */
function updateWatchlistBatchBar() {
    const checked = getVisibleCheckedWatchlist();
    const countEl = document.getElementById('watchlist-batch-count');
    const removeBtn = document.getElementById('watchlist-batch-remove-btn');
    const selectAllCb = document.getElementById('watchlist-select-all-cb');

    if (checked.length > 0) {
        countEl.textContent = `${checked.length} selected`;
        removeBtn.style.display = '';
    } else {
        countEl.textContent = '';
        removeBtn.style.display = 'none';
    }

    // Update select-all checkbox state
    if (selectAllCb) {
        const visible = Array.from(document.querySelectorAll('.watchlist-select-cb')).filter(cb => {
            const card = cb.closest('.watchlist-artist-card');
            return card && card.style.display !== 'none';
        });
        selectAllCb.checked = visible.length > 0 && checked.length === visible.length;
        selectAllCb.indeterminate = checked.length > 0 && checked.length < visible.length;
    }
}

function toggleWatchlistSelectAll(checked) {
    const checkboxes = document.querySelectorAll('.watchlist-select-cb');
    checkboxes.forEach(cb => {
        const card = cb.closest('.watchlist-artist-card');
        if (card && card.style.display !== 'none') {
            cb.checked = checked;
        }
    });
    updateWatchlistBatchBar();
}

/**
 * Batch remove selected artists from watchlist
 */
async function batchRemoveFromWatchlist() {
    const checked = getVisibleCheckedWatchlist();
    if (checked.length === 0) return;

    const count = checked.length;
    if (!await showConfirmDialog({ title: 'Remove Artists', message: `Remove ${count} artist${count !== 1 ? 's' : ''} from your watchlist?`, confirmText: 'Remove', destructive: true })) return;

    const artistIds = checked.map(cb => cb.getAttribute('data-artist-id'));

    try {
        const response = await fetch('/api/watchlist/remove-batch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ artist_ids: artistIds })
        });

        const data = await response.json();
        if (!data.success) {
            throw new Error(data.error || 'Failed to remove artists');
        }

        console.log(`❌ Batch removed ${data.removed} artists from watchlist`);

        // Refresh the watchlist page
        watchlistPageState.isInitialized = false;
        await initializeWatchlistPage();

        // Update button count
        updateWatchlistButtonCount();

        // Update any visible artist cards
        updateArtistCardWatchlistStatus();

    } catch (error) {
        console.error('Error batch removing from watchlist:', error);
        alert(`Error removing artists: ${error.message}`);
    }
}

// --- Metadata Updater Functions ---

// Global state for metadata update polling
let metadataUpdatePolling = false;
let metadataUpdateInterval = null;

/**
 * Handle metadata update button click
 */
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

                    // Update the header text to reflect the current server
                    const headerElement = metadataCard.querySelector('.card-header h3');
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
                        if (socketConnected) return; // Phase 5: WS handles scan status
                        pollCount++;

                        if (pollCount > maxPolls) {
                            // Polling timeout after 5 minutes
                            clearInterval(pollInterval);
                            button.disabled = false;
                            phaseLabel.textContent = 'Scan completed';
                            progressBar.style.width = '0%';
                            progressLabel.textContent = 'Ready for next scan';
                            statusValue.textContent = 'Idle';
                            statusValue.style.color = '#b3b3b3';
                            showToast('✅ Media scan completed', 'success', 3000);
                            return;
                        }

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

