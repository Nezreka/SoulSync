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

// Brand logos rendered inside each equalizer bar's avatar disc.
// Same URLs the header-actions worker-orb buttons use — sourced
// from each service's official press / SVG-repo asset so the row
// reads as branded chips, not anonymous initials. AudioDB ships
// no public logo URL, so it lives as a local static file.
const _RATE_GAUGE_LOGOS = {
    spotify:    '/static/img/brands/spotify.png',
    itunes:     '/static/img/brands/itunes.png',
    deezer:     '/static/img/brands/deezer.png',
    jiosaavn:   '/static/img/brands/jiosaavn.webp',
    lastfm:     '/static/img/brands/lastfm.png',
    genius:     '/static/img/brands/genius.png',
    musicbrainz:'/static/img/brands/musicbrainz.png',
    audiodb:    '/static/audiodb.png',
    tidal:      '/static/img/brands/tidal.svg',
    qobuz:      '/static/img/brands/qobuz.svg',
    discogs:    '/static/img/brands/discogs.svg',
    amazon:     '/static/amazon.svg',
};

// Per-service display state for the equalizer bars. Holds the last
// rendered count so we can animate digit changes via easing, and the
// last fill height so the spike-detect peak flash only fires on a
// real upward step (not on repaint / equal-value socket updates).
const _eqDisplay = {};

function _visibleRateGaugeServices() {
    if (typeof isJiosaavnExperimentalEnabled === 'function' && isJiosaavnExperimentalEnabled()) {
        return _RATE_GAUGE_SERVICES;
    }
    return _RATE_GAUGE_SERVICES.filter(svc => svc !== 'jiosaavn');
}

function _removeJiosaavnRateGauge() {
    document.getElementById('rate-eq-jiosaavn')?.remove();
    document.getElementById('rate-gauge-jiosaavn')?.remove();
    delete _rateMonitorState.jiosaavn;
    delete _eqDisplay.jiosaavn;
}

function refreshRateMonitorExperimentalVisibility() {
    if (typeof isJiosaavnExperimentalEnabled === 'function' && !isJiosaavnExperimentalEnabled()) {
        _removeJiosaavnRateGauge();
    }
}

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

    if (typeof isJiosaavnExperimentalEnabled === 'function' && !isJiosaavnExperimentalEnabled()) {
        _removeJiosaavnRateGauge();
    }

    const visibleServices = _visibleRateGaugeServices();

    // Skip DOM writes while the equalizer is off-screen (you're on another page).
    // All pages stay mounted, so updating a hidden grid still fires every
    // MutationObserver on the document — including password-manager extensions
    // that re-scan the WHOLE DOM on each mutation — for zero visible benefit.
    // offsetParent is null when an ancestor is display:none. The next update that
    // arrives while the dashboard is visible renders normally.
    if (grid.offsetParent === null) return;

    // The dashboard rate monitor uses the equalizer-bar visual — a
    // vertical-bar VU-meter row that fits any service count without
    // an orphan grid cell. Detail page / mobile breakpoints keep the
    // legacy speedometer card markup so existing CSS still applies.
    const useEqualizer = grid.classList.contains('rate-monitor-grid--equalizer')
        || grid.closest('[data-card="enrichment"]') !== null;

    if (useEqualizer) {
        grid.classList.add('rate-monitor-grid--equalizer');
        _renderEqualizerBars(grid, data);
        return;
    }

    if (!grid.children.length) {
        for (const svc of visibleServices) {
            const div = document.createElement('div');
            div.className = 'rate-gauge-card';
            div.id = `rate-gauge-${svc}`;
            div.onclick = () => _openRateModal(svc);
            grid.appendChild(div);
        }
    } else {
        for (const svc of visibleServices) {
            if (document.getElementById(`rate-gauge-${svc}`)) continue;
            const div = document.createElement('div');
            div.className = 'rate-gauge-card';
            div.id = `rate-gauge-${svc}`;
            div.onclick = () => _openRateModal(svc);
            grid.appendChild(div);
        }
    }

    for (const svc of visibleServices) {
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


// ── Equalizer-bar renderer (dashboard) ─────────────────────────────────
//
// VU-meter aesthetic: one vertical bar per service. Bar height = current
// rate / limit. Service brand color fades up the bar with an animated
// glow at the tip when active. Click opens the same detail modal the
// speedometer used. Symmetric by design — any service count fits one
// flex row regardless of viewport.

function _renderEqualizerBars(grid, data) {
    if (typeof isJiosaavnExperimentalEnabled === 'function' && !isJiosaavnExperimentalEnabled()) {
        _removeJiosaavnRateGauge();
    }

    const visibleServices = _visibleRateGaugeServices();

    for (const svc of visibleServices) {
        if (document.getElementById(`rate-eq-${svc}`)) continue;
            const accent = _RATE_GAUGE_COLORS[svc] || '#888';
            const label = _RATE_GAUGE_LABELS[svc] || svc;
            const logoSrc = _RATE_GAUGE_LOGOS[svc] || '';
            const fallbackGlyph = (label[0] || '?').toUpperCase();
            const bar = document.createElement('button');
            bar.type = 'button';
            bar.className = 'rate-eq';
            bar.id = `rate-eq-${svc}`;
            bar.dataset.svc = svc;
            bar.style.setProperty('--eq-accent', accent);
            bar.setAttribute('aria-label', `${label} rate detail`);
            bar.onclick = () => _openRateModal(svc);
            // Layout, top-to-bottom:
            //   - avatar disc (brand logo) anchored above the track —
            //     ``onerror`` swap to the initial-letter glyph keeps the
            //     UI legible if a CDN URL ever breaks
            //   - track (with reflection puddle as ::after) holding the
            //     fill / shimmer / peak tip / live count
            //   - meta column (state pill + service name)
            const avatar = logoSrc
                ? `<div class="rate-eq-avatar" aria-hidden="true">
                       <img class="rate-eq-avatar-logo" src="${logoSrc}" alt=""
                            onerror="this.parentElement.classList.add('rate-eq-avatar--fallback');this.replaceWith(Object.assign(document.createElement('span'),{className:'rate-eq-avatar-glyph',textContent:'${fallbackGlyph}'}));">
                   </div>`
                : `<div class="rate-eq-avatar rate-eq-avatar--fallback" aria-hidden="true">
                       <span class="rate-eq-avatar-glyph">${fallbackGlyph}</span>
                   </div>`;
            bar.innerHTML = `
                <div class="rate-eq-avatar-wrap">${avatar}</div>
                <div class="rate-eq-track">
                    <div class="rate-eq-ticks"></div>
                    <div class="rate-eq-fill">
                        <div class="rate-eq-shimmer"></div>
                        <div class="rate-eq-tip"></div>
                    </div>
                    <div class="rate-eq-peak"></div>
                    <div class="rate-eq-cooldown">
                        <div class="rate-eq-cooldown-fill"></div>
                        <div class="rate-eq-cooldown-time"></div>
                    </div>
                    <div class="rate-eq-value">0</div>
                </div>
                <div class="rate-eq-meta">
                    <span class="rate-eq-state" data-status="stopped">
                        <span class="rate-eq-state-dot"></span>
                        <span class="rate-eq-state-text">Stopped</span>
                    </span>
                    <span class="rate-eq-name">${label}</span>
                </div>
            `;
            grid.appendChild(bar);
            _eqDisplay[svc] = { value: 0, pct: 0.04 };
    }

    for (const svc of visibleServices) {
        const d = data[svc];
        if (!d) continue;
        _rateMonitorState[svc] = d;
        const bar = document.getElementById(`rate-eq-${svc}`);
        if (!bar) continue;

        const value = d.cpm || 0;
        const max = d.limit || 60;
        // Clamp the visual fill to [4%, 100%]. The 4% floor lets idle
        // bars still show a sliver of accent so the row reads as
        // present-but-quiet instead of empty — critical for the
        // "everything alive" vibe; an actual zero would make most
        // services disappear most of the time.
        const pct = Math.max(0.04, Math.min(value / max, 1));
        const worker = d.worker || {};
        const wStatus = worker.status || 'stopped';
        const isRateLimited = d.rate_limited === true;

        const prev = _eqDisplay[svc] || { value: 0, pct: 0.04 };

        const fill = bar.querySelector('.rate-eq-fill');
        if (fill) fill.style.height = `${pct * 100}%`;

        // Peak-hold tick — the VU-meter idiom: a thin marker sticks at the
        // recent maximum, holds ~1.2s, then falls a few %/update until it
        // rests on the live fill. A burst stays readable after it's over.
        const nowTs = performance.now();
        let peak = prev.peak ?? pct;
        let peakAt = prev.peakAt ?? nowTs;
        if (pct >= peak) {
            peak = pct;
            peakAt = nowTs;
        } else if (nowTs - peakAt > 1200) {
            peak = Math.max(pct, peak - 0.045);
        }
        const peakEl = bar.querySelector('.rate-eq-peak');
        if (peakEl) {
            peakEl.style.bottom = `${peak * 100}%`;
            // Only show while meaningfully above the live fill — idle bars
            // shouldn't carry a stray line.
            peakEl.classList.toggle('visible', peak > pct + 0.015 && peak > 0.06);
        }

        // The reflection puddle (CSS ::after on the track) fades in
        // proportional to the real (unclamped) rate so idle services
        // don't pollute the row with a floor of glow. Bound to a
        // CSS variable so the puddle and any future glow-aware
        // styling can share the same source-of-truth value.
        const realPct = max > 0 ? value / max : 0;
        bar.style.setProperty('--eq-glow', String(Math.min(1, realPct + 0.04)));

        // Rolling counter — animate the digit change instead of
        // snapping. Premium feel; matches the smooth height
        // transition the fill already has.
        const val = bar.querySelector('.rate-eq-value');
        if (val) {
            const rounded = Math.round(value);
            const prevRounded = Math.round(prev.value);
            if (rounded !== prevRounded) {
                _animateRollingNumber(val, prevRounded, rounded);
            } else {
                val.textContent = rounded;
            }
        }

        // Peak-flash detector: if the rate stepped UPWARD between
        // socket updates, briefly trigger the .peak-flash class on
        // the bar so the tip emits a quick accent burst. Mimics a
        // hardware VU meter's peak-detect LED — sells the "alive"
        // feeling and ties bar movement to actual call activity.
        // Only fires on real increases above a small noise floor
        // (jitter on near-zero rates would otherwise pulse the
        // bar constantly).
        const PEAK_JITTER_THRESHOLD = 1;
        if (value - prev.value > PEAK_JITTER_THRESHOLD) {
            bar.classList.remove('peak-flash');
            // Force a reflow so re-adding the class restarts the
            // animation even if it was already mid-cycle.
            void bar.offsetWidth;  // eslint-disable-line no-unused-expressions
            bar.classList.add('peak-flash');
            window.setTimeout(() => bar.classList.remove('peak-flash'), 700);
        }

        const state = bar.querySelector('.rate-eq-state');
        if (state) {
            state.dataset.status = wStatus;
            const text = state.querySelector('.rate-eq-state-text');
            if (text) text.textContent = _workerStatusLabel(wStatus, worker);
        }

        // Danger threshold uses the REAL (unclamped) ratio so the
        // 4% visual floor for idle bars doesn't push everything into
        // a permanent green state — the threshold reads true rate.
        bar.classList.toggle('danger', realPct > 0.8 || isRateLimited);
        bar.classList.toggle('active', value > 0 || wStatus === 'running');
        bar.classList.toggle('rate-limited', isRateLimited);

        // Cooldown drain — a ban isn't just "red", it's a COUNTDOWN. The
        // payload only carries seconds remaining, so latch the largest value
        // seen this ban as the denominator; the red column then drains away
        // as the ban ticks down and the timer shows m:ss until recovery.
        const rlRemaining = isRateLimited ? Math.max(0, Math.round(d.rl_remaining || 0)) : 0;
        let rlTotal = prev.rlTotal || 0;
        const cooling = rlRemaining > 0;
        if (cooling) {
            rlTotal = Math.max(rlTotal, rlRemaining);
            const cdFill = bar.querySelector('.rate-eq-cooldown-fill');
            if (cdFill) cdFill.style.height = `${(rlRemaining / rlTotal) * 100}%`;
            const cdTime = bar.querySelector('.rate-eq-cooldown-time');
            if (cdTime) {
                const m = Math.floor(rlRemaining / 60);
                const s = String(rlRemaining % 60).padStart(2, '0');
                cdTime.textContent = `${m}:${s}`;
            }
        } else {
            rlTotal = 0;
            // Recovery moment: the ban just ended — flash the bar back alive.
            if (prev.cooling) {
                bar.classList.remove('recovered');
                void bar.offsetWidth;  // restart the animation
                bar.classList.add('recovered');
                window.setTimeout(() => bar.classList.remove('recovered'), 1300);
            }
        }
        bar.classList.toggle('cooldown', cooling);

        // Call embers: tiny accent sparks rise off the fill tip, spawned per
        // socket update in proportion to REAL traffic — motion strictly means
        // API calls are happening right now. Suppressed during cooldown and
        // under reduced-effects / max-performance.
        if (!window._reduceEffectsActive && !window._maxPerfActive && !cooling && realPct > 0.03) {
            _spawnEmbers(bar, pct, realPct > 0.6 ? 3 : realPct > 0.25 ? 2 : 1);
        }

        // Daily-budget ring (Spotify is the only service with a real daily
        // cap): a conic rim inside the avatar disc fills as the budget is
        // spent — green → amber → red, purple once the worker has bridged
        // to Spotify Free for the rest of the day.
        const budget = worker.daily_budget;
        if (budget && budget.limit > 0) {
            const bPct = Math.min(1, (budget.used || 0) / budget.limit);
            const bridged = !!worker.using_free && !!budget.exhausted;
            bar.classList.add('has-budget');
            bar.style.setProperty('--eq-budget', String(Math.max(bPct, 0.02)));
            bar.style.setProperty('--eq-budget-color',
                bridged ? '#a78bfa' : bPct < 0.7 ? '#4ade80' : bPct < 0.95 ? '#fbbf24' : '#ef4444');
            const avatar = bar.querySelector('.rate-eq-avatar');
            if (avatar) {
                avatar.title = bridged
                    ? `Daily budget spent — running on Spotify Free (${budget.used}/${budget.limit})`
                    : `Daily API budget: ${budget.used}/${budget.limit}`;
            }
        } else {
            bar.classList.remove('has-budget');
        }

        _eqDisplay[svc] = { value, pct, peak, peakAt, rlTotal, cooling };
    }
}

// Spawn ember particles at a bar's fill tip. Self-removing DOM sparks with
// a per-bar live cap so a busy hour can't accumulate nodes.
function _spawnEmbers(bar, pct, count) {
    const track = bar.querySelector('.rate-eq-track');
    if (!track || track.querySelectorAll('.rate-eq-ember').length > 6) return;
    for (let i = 0; i < count; i++) {
        const e = document.createElement('span');
        e.className = 'rate-eq-ember';
        e.style.left = `${20 + Math.random() * 60}%`;
        e.style.bottom = `${Math.min(96, pct * 100)}%`;
        e.style.setProperty('--ember-drift', `${(Math.random() - 0.5) * 14}px`);
        e.style.animationDuration = `${1.1 + Math.random() * 0.7}s`;
        e.addEventListener('animationend', () => e.remove());
        track.appendChild(e);
    }
}

// Animate a single integer counter from `from` to `to` with an
// easeOutCubic curve. Used by the equalizer bars so the live count
// digit-rolls instead of snapping when sockets push a new value.
const _eqRollingHandles = new WeakMap();

function _animateRollingNumber(el, from, to, duration = 520) {
    // Cancel any animation already running on this element so we
    // don't get two RAF loops fighting over its textContent.
    const prev = _eqRollingHandles.get(el);
    if (prev) cancelAnimationFrame(prev);

    if (from === to) {
        el.textContent = String(to);
        return;
    }
    const start = performance.now();
    const span = to - from;
    function step(now) {
        const t = Math.min(1, (now - start) / duration);
        const eased = 1 - Math.pow(1 - t, 3);
        el.textContent = String(Math.round(from + span * eased));
        if (t < 1) {
            _eqRollingHandles.set(el, requestAnimationFrame(step));
        } else {
            _eqRollingHandles.delete(el);
        }
    }
    _eqRollingHandles.set(el, requestAnimationFrame(step));
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
        updateStatCard('finished-downloads-card', data.finished_downloads, 'Completed downloads');
        updateStatCard('download-speed-card', data.download_speed, 'Combined speed');
        updateStatCard('active-syncs-card', data.active_syncs, 'Playlists syncing');
        updateStatCard('uptime-card', data.uptime, 'Application runtime');
        // system memory % headline + SoulSync's own RSS in the subtitle (#935 follow-up)
        updateStatCard('memory-card', data.memory_usage,
            data.process_memory ? `SoulSync · ${data.process_memory}` : 'Current usage');

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

// Activity items carry `timestamp` (Unix epoch seconds) — `activity.time`
// is a human label like "Now" that doesn't parse as a date. Use the
// epoch for relative-time formatting; fall back to the label only
// when no timestamp is present (legacy items, future shapes).
function _activityTimeAgo(activity) {
    const ts = activity && activity.timestamp;
    if (typeof ts !== 'number' || !isFinite(ts)) {
        return (activity && activity.time) || '';
    }
    const diffMs = Date.now() - ts * 1000;
    if (diffMs < 60000) return 'Just now';
    const mins = Math.floor(diffMs / 60000);
    if (mins < 60) return `${mins}m ago`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `${hours}h ago`;
    const days = Math.floor(hours / 24);
    if (days < 30) return `${days}d ago`;
    return `${Math.floor(days / 30)}mo ago`;
}

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
            if (timeEls[i]) timeEls[i].textContent = _activityTimeAgo(activity);
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
            <p class="activity-time">${_activityTimeAgo(activity)}</p>
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
        const basicInput = document.getElementById('downloads-search-input');
        const soulseekIcon = document.querySelector('#enh-source-row [data-source="soulseek"]');
        if (soulseekIcon) {
            if (basicInput) basicInput.value = query;
            // Sync the controller's query BEFORE clicking — otherwise
            // onSoulseekSelected fires with a stale query and overwrites it.
            if (typeof _searchPageController !== 'undefined' && _searchPageController) {
                _searchPageController.state.query = query;
            }
            soulseekIcon.click();
            return;
        }
        if (attempts < 25) { setTimeout(tryHandoff, 160); return; }
        // Controller never came up — swap sections and run the search directly.
        const basicSection = document.getElementById('basic-search-section');
        const enhancedSection = document.getElementById('enhanced-search-section');
        if (basicSection) basicSection.classList.add('active');
        if (enhancedSection) enhancedSection.classList.remove('active');
        if (basicInput) basicInput.value = query;
        if (basicInput && basicInput.value && typeof performDownloadsSearch === 'function') {
            performDownloadsSearch();
        }
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
