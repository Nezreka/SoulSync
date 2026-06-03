/*
 * Manage Enrichment Workers modal.
 *
 * The dashboard "enrichment bubbles" expose hover/pause but no way to *manage*
 * a worker. This modal surfaces, per worker: live status + current item,
 * pause/resume, a matched/not-found/pending breakdown per entity type, and a
 * searchable/paginated browser of the items that source hasn't matched — each
 * with inline manual-match (reusing /api/library/search-service +
 * manual-match) and retry (clear-match, which re-queues the item).
 *
 * Backend: GET /api/enrichment/<id>/{status,breakdown,unmatched}, POST
 * .../{pause,resume}. The unmatched/breakdown routes are generic across all 11
 * workers (see core/enrichment/unmatched.py).
 */

// Per-source accent + the CSS selector of that worker's logo already rendered
// in the dashboard bubble. We reuse those exact <img> sources at runtime
// (via _emLogoSrc) so the modal shows the real logos — including AudioDB's
// inline base64 — and stays in sync if the dashboard logos ever change.
// imgFilter / imgRound mirror the per-logo CSS the dashboard bubbles apply, so
// black-on-dark icons (Discogs/Tidal/Qobuz/Amazon) get inverted to white and
// square logos (Last.fm) clip to a circle here too.
const ENRICHMENT_WORKERS = [
    { id: 'spotify',     name: 'Spotify',      color: '#1db954', logoSel: '.spotify-enrich-logo' },
    { id: 'itunes',      name: 'iTunes',       color: '#fb5bc5', logoSel: '.itunes-enrich-logo' },
    { id: 'musicbrainz', name: 'MusicBrainz',  color: '#ba55d3', logoSel: '.mb-logo' },
    { id: 'deezer',      name: 'Deezer',       color: '#a238ff', logoSel: '.deezer-logo' },
    { id: 'audiodb',     name: 'AudioDB',      color: '#1c8cf0', logoSel: '.audiodb-logo' },
    { id: 'discogs',     name: 'Discogs',      color: '#cfcfcf', logoSel: '.discogs-logo', imgFilter: 'brightness(0) invert(1)' },
    { id: 'lastfm',      name: 'Last.fm',      color: '#d51007', logoSel: '.lastfm-enrich-logo', imgRound: true },
    { id: 'genius',      name: 'Genius',       color: '#ffe600', logoSel: '.genius-enrich-logo' },
    { id: 'tidal',       name: 'Tidal',        color: '#00cfe6', logoSel: '.tidal-enrich-logo', imgFilter: 'invert(1) brightness(1.8)', imgRound: true },
    { id: 'qobuz',       name: 'Qobuz',        color: '#0070ef', logoSel: '.qobuz-enrich-logo', imgFilter: 'invert(1)', imgRound: true },
    { id: 'amazon',      name: 'Amazon Music', color: '#ff9900', logoSel: '.amazon-enrich-logo', imgFilter: 'brightness(0) invert(1)' },
];

const _emWorkerById = Object.fromEntries(ENRICHMENT_WORKERS.map(w => [w.id, w]));

// '#1db954' -> '29,185,84' for rgba(var(--em-accent-rgb), a) usage.
function _emHexToRgb(hex) {
    const h = String(hex || '').replace('#', '');
    const full = h.length === 3 ? h.split('').map(c => c + c).join('') : h;
    const n = parseInt(full, 16);
    if (isNaN(n) || full.length !== 6) return '120,120,120';
    return `${(n >> 16) & 255},${(n >> 8) & 255},${n & 255}`;
}

// Resolve a worker's logo URL from the live dashboard bubble (null if absent).
function _emLogoSrc(workerId) {
    const w = _emWorkerById[workerId];
    if (!w || !w.logoSel) return null;
    const img = document.querySelector(w.logoSel);
    return img && img.src ? img.src : null;
}

// A circular, glowing icon chip mirroring the dashboard bubbles. Falls back to
// a colored initial if the logo is missing or fails to load.
function _emIconHtml(workerId, size) {
    const w = _emWorkerById[workerId];
    const src = _emLogoSrc(workerId);
    const cls = `em-icon${size === 'lg' ? ' em-icon--lg' : ''}`;
    const initial = w.name.charAt(0).toUpperCase();
    const imgStyle = [
        w.imgFilter ? `filter:${w.imgFilter}` : '',
        w.imgRound ? 'border-radius:50%' : '',
    ].filter(Boolean).join(';');
    const inner = src
        ? `<img src="${_emEscape(src)}" alt="" class="em-icon-img"${imgStyle ? ` style="${imgStyle}"` : ''}
                onerror="this.replaceWith(Object.assign(document.createElement('span'),{className:'em-icon-letter',textContent:'${initial}'}))">`
        : `<span class="em-icon-letter">${initial}</span>`;
    return `<span class="${cls}" style="--em-accent:${w.color}">${inner}</span>`;
}

const enrichmentManagerState = {
    open: false,
    selected: null,
    statuses: {},       // id -> last /status payload
    breakdown: null,    // selected worker's breakdown
    priority: '',       // pinned 'process first' entity for selected worker ('' = auto)
    entityTab: 'artist',
    statusFilter: 'unmatched',
    search: '',
    page: 0,
    pageSize: 25,
    unmatched: null,    // { total, items }
    selectedItems: new Set(),  // ids checked for bulk retry
    pollTimer: null,
    loadToken: 0,       // guards against out-of-order async renders
};

function _emEntityLabel(entity, plural) {
    const map = { artist: 'Artist', album: 'Album', track: 'Track' };
    const base = map[entity] || entity;
    return plural ? base + 's' : base;
}

// Always present entities in the worker's real processing order, regardless of
// the order the API/object keys happen to arrive in.
const _EM_ENTITY_ORDER = { artist: 0, album: 1, track: 2 };
function _emOrderEntities(list) {
    return [...new Set(list || [])].sort(
        (a, b) => (_EM_ENTITY_ORDER[a] ?? 9) - (_EM_ENTITY_ORDER[b] ?? 9)
    );
}

function _emEscape(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => (
        { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
    ));
}

// Human "3 days ago" for a SQLite timestamp; '' when never attempted.
function _emRelativeTime(value) {
    if (!value) return '';
    // SQLite stores 'YYYY-MM-DD HH:MM:SS' (UTC) — normalize to ISO.
    const ts = Date.parse(String(value).replace(' ', 'T') + (String(value).includes('Z') ? '' : 'Z'));
    if (isNaN(ts)) return '';
    const secs = Math.max(0, (Date.now() - ts) / 1000);
    if (secs < 60) return 'just now';
    const mins = secs / 60;
    if (mins < 60) return `${Math.floor(mins)}m ago`;
    const hrs = mins / 60;
    if (hrs < 24) return `${Math.floor(hrs)}h ago`;
    const days = hrs / 24;
    if (days < 30) return `${Math.floor(days)}d ago`;
    const months = days / 30;
    if (months < 12) return `${Math.floor(months)}mo ago`;
    return `${Math.floor(months / 12)}y ago`;
}

// ── Open / close ──────────────────────────────────────────────────────────

async function openEnrichmentManager(workerId) {
    let overlay = document.getElementById('enrichment-manager-overlay');
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = 'enrichment-manager-overlay';
        overlay.className = 'modal-overlay em-overlay hidden';
        overlay.onclick = (e) => { if (e.target === overlay) closeEnrichmentManager(); };
        overlay.innerHTML = `
            <div class="enrichment-manager-modal" role="dialog" aria-modal="true"
                 aria-label="Manage Enrichment Workers" tabindex="-1">
                <div class="em-topbar">
                    <div class="em-topbar-icon"><span>🧬</span></div>
                    <div class="em-topbar-titles">
                        <h3 class="em-topbar-title">Enrichment Workers</h3>
                        <div class="em-topbar-sub">Match your library across every metadata source</div>
                    </div>
                    <div class="em-global">
                        <span class="em-global-label">Process first<br><span>everywhere</span></span>
                        <div class="em-global-tabs" id="em-global-tabs">
                            <button data-e="artist" onclick="setGlobalPriority('artist', this)">Artists</button>
                            <button data-e="album"  onclick="setGlobalPriority('album', this)">Albums</button>
                            <button data-e="track"  onclick="setGlobalPriority('track', this)">Tracks</button>
                            <button data-e="" class="em-global-auto" onclick="setGlobalPriority('', this)">Auto</button>
                        </div>
                    </div>
                    <div class="em-topbar-actions">
                        <button class="em-icon-btn" id="em-refresh-btn" title="Refresh"
                                onclick="refreshEnrichmentManager(this)">⟳</button>
                        <button class="em-icon-btn em-icon-btn--close" title="Close"
                                onclick="closeEnrichmentManager()">&times;</button>
                    </div>
                </div>
                <div class="em-body">
                    <div class="em-rail" id="em-rail"></div>
                    <div class="em-panel" id="em-panel"></div>
                </div>
            </div>`;
        document.body.appendChild(overlay);
    }

    overlay.classList.remove('hidden', 'em-closing');
    // Re-trigger the entrance animation even when reusing the element.
    const modal = overlay.querySelector('.enrichment-manager-modal');
    if (modal) { modal.classList.remove('em-in'); void modal.offsetWidth; modal.classList.add('em-in'); }
    document.body.classList.add('em-scroll-lock');
    document.addEventListener('keydown', _emOnKeydown);
    enrichmentManagerState.open = true;

    await refreshAllEnrichmentStatuses();
    renderEnrichmentRail();

    // Selection priority: explicit deep-link arg → last-viewed (remembered) →
    // first running worker → first in the list.
    let remembered = null;
    try { remembered = localStorage.getItem('em-last-worker'); } catch (_e) { /* ignore */ }
    const valid = (wid) => wid && _emWorkerById[wid];
    const running = ENRICHMENT_WORKERS.find(w => enrichmentManagerState.statuses[w.id]?.running);
    const initial = (valid(workerId) && workerId)
        || (valid(remembered) && remembered)
        || (running || ENRICHMENT_WORKERS[0]).id;
    selectEnrichmentWorker(initial);
    if (modal) setTimeout(() => modal.focus(), 60);

    if (enrichmentManagerState.pollTimer) clearInterval(enrichmentManagerState.pollTimer);
    enrichmentManagerState.pollTimer = setInterval(_emPollSelected, 3000);
}

function closeEnrichmentManager() {
    const overlay = document.getElementById('enrichment-manager-overlay');
    enrichmentManagerState.open = false;
    document.removeEventListener('keydown', _emOnKeydown);
    document.body.classList.remove('em-scroll-lock');
    if (enrichmentManagerState.pollTimer) {
        clearInterval(enrichmentManagerState.pollTimer);
        enrichmentManagerState.pollTimer = null;
    }
    if (!overlay) return;
    // Brief fade/scale-out, then hide.
    overlay.classList.add('em-closing');
    setTimeout(() => {
        overlay.classList.add('hidden');
        overlay.classList.remove('em-closing');
    }, 170);
}

// Escape closes the nested match overlay first (if open), else the manager.
function _emOnKeydown(e) {
    if (e.key !== 'Escape') return;
    const match = document.getElementById('enrichment-match-overlay');
    if (match) { match.remove(); return; }
    closeEnrichmentManager();
}

// Manual refresh: re-pull every worker's status + the selected worker's data.
async function refreshEnrichmentManager(btn) {
    if (btn) btn.classList.add('em-spinning');
    await refreshAllEnrichmentStatuses();
    renderEnrichmentRail();
    const sel = enrichmentManagerState.selected;
    if (sel) await Promise.all([_emLoadBreakdown(sel), _emLoadUnmatched()]);
    _emRenderEntityCards();
    _emRenderUnmatchedList();
    _emRenderPanelHeader();
    if (btn) setTimeout(() => btn.classList.remove('em-spinning'), 400);
}

// ── Status loading ──────────────────────────────────────────────────────────

async function refreshAllEnrichmentStatuses() {
    const results = await Promise.all(ENRICHMENT_WORKERS.map(async (w) => {
        try {
            const res = await fetch(`/api/enrichment/${w.id}/status`);
            return [w.id, res.ok ? await res.json() : null];
        } catch (_e) {
            return [w.id, null];
        }
    }));
    for (const [id, status] of results) enrichmentManagerState.statuses[id] = status;
}

async function _emPollSelected() {
    const id = enrichmentManagerState.selected;
    if (!id || !enrichmentManagerState.open) return;
    try {
        const res = await fetch(`/api/enrichment/${id}/status`);
        if (res.ok) {
            enrichmentManagerState.statuses[id] = await res.json();
            _emUpdateHeaderLive();   // in-place — no logo reflow/flicker
            _emUpdateRailRow(id);
            _emRenderEntityCards();
        }
    } catch (_e) { /* transient — keep last */ }
}

function _emStatusInfo(status) {
    if (!status || !status.enabled) return { cls: 'disabled', label: 'Disabled' };
    if (status.rate_limited) return { cls: 'ratelimited', label: 'Rate-limited' };
    if (status.paused) return { cls: 'paused', label: 'Paused' };
    if (status.idle) return { cls: 'idle', label: 'Idle' };
    if (status.running) return { cls: 'running', label: 'Running' };
    return { cls: 'stopped', label: 'Stopped' };
}

// Global "process first" — applies a group to EVERY worker. Like the per-worker
// pin, it also re-queues that group's previously-failed items so each worker
// sweeps ALL pending + failed of the group before moving on. Workers that don't
// enrich the entity (Genius/album, Discogs/track) reject with 400 and are
// skipped (no priority set, no re-queue). Workers run independently in parallel.
async function setGlobalPriority(entity, btn) {
    if (btn) {
        document.querySelectorAll('#em-global-tabs button').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
    }
    const perWorker = await Promise.all(ENRICHMENT_WORKERS.map(async (w) => {
        let okP = false, reset = 0;
        try {
            const r = await fetch(`/api/enrichment/${w.id}/priority`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ entity: entity || 'none' }),
            });
            okP = r.ok;
        } catch (_e) { /* skip */ }
        if (okP && entity) {
            try {
                const rr = await fetch(`/api/enrichment/${w.id}/retry`, {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ entity_type: entity, scope: 'failed' }),
                });
                if (rr.ok) reset = (await rr.json()).reset || 0;
            } catch (_e) { /* priority still set */ }
        }
        return { okP, reset };
    }));
    const n = perWorker.filter(x => x.okP).length;
    const totalReset = perWorker.reduce((s, x) => s + x.reset, 0);
    showToast(entity
        ? `${n} worker${n === 1 ? '' : 's'} → ${_emEntityLabel(entity, true).toLowerCase()} first`
          + (totalReset ? ` · re-queued ${totalReset.toLocaleString()} failed` : '')
        : `${n} worker${n === 1 ? '' : 's'} back to automatic order`, 'success');
    // Reflect on the currently-open worker.
    const sel = enrichmentManagerState.selected;
    if (sel) { await _emLoadBreakdown(sel); await _emLoadPriority(sel); _emRenderEntityCards(); }
}

// ── Left rail ───────────────────────────────────────────────────────────────

// Overall library coverage (% of items this source has attempted) from the
// status payload's progress block — a cheap at-a-glance rail signal.
function _emOverallPct(status) {
    const p = status && status.progress;
    if (!p) return null;
    let matched = 0, total = 0;
    for (const k of ['artists', 'albums', 'tracks']) {
        if (p[k]) { matched += p[k].matched || 0; total += p[k].total || 0; }
    }
    return total ? Math.round((matched / total) * 100) : 0;
}

// Rail sub-line: while running, show the group it's on ("Running · albums");
// otherwise the status + overall coverage %.
function _emRailSubText(status) {
    const info = _emStatusInfo(status);
    const phase = _emCurrentPhase(status);
    if (info.cls === 'running' && phase) return `${info.label} · ${_emEntityLabel(phase, true).toLowerCase()}`;
    const pct = _emOverallPct(status);
    return `${info.label}${pct == null ? '' : ` · ${pct}%`}`;
}

function renderEnrichmentRail() {
    const rail = document.getElementById('em-rail');
    if (!rail) return;
    rail.innerHTML = ENRICHMENT_WORKERS.map(w => {
        const status = enrichmentManagerState.statuses[w.id];
        const info = _emStatusInfo(status);
        const pct = _emOverallPct(status);
        const cov = pct == null ? '' : `
                    <span class="em-rail-cov"><span class="em-rail-cov-fill" style="width:${pct}%"></span></span>`;
        return `
            <button class="em-worker-row" id="em-row-${w.id}"
                    onclick="selectEnrichmentWorker('${w.id}')">
                ${_emIconHtml(w.id)}
                <span class="em-worker-meta">
                    <span class="em-worker-name">${_emEscape(w.name)}</span>
                    <span class="em-worker-sub">${_emRailSubText(status)}</span>
                    ${cov}
                </span>
                <span class="em-dot em-dot--${info.cls}" title="${info.label}"></span>
            </button>`;
    }).join('');
    _emHighlightRail();
}

function _emHighlightRail() {
    ENRICHMENT_WORKERS.forEach(w => {
        const row = document.getElementById(`em-row-${w.id}`);
        if (row) row.classList.toggle('active', w.id === enrichmentManagerState.selected);
    });
}

function _emUpdateRailRow(id) {
    const row = document.getElementById(`em-row-${id}`);
    if (!row) return;
    const status = enrichmentManagerState.statuses[id];
    const info = _emStatusInfo(status);
    const pct = _emOverallPct(status);
    const dot = row.querySelector('.em-dot');
    if (dot) { dot.className = `em-dot em-dot--${info.cls}`; dot.title = info.label; }
    const sub = row.querySelector('.em-worker-sub');
    if (sub) sub.textContent = _emRailSubText(status);
    const cov = row.querySelector('.em-rail-cov-fill');
    if (cov && pct != null) cov.style.width = `${pct}%`;
}

// ── Worker selection ──────────────────────────────────────────────────────────

async function selectEnrichmentWorker(id) {
    enrichmentManagerState.selected = id;
    try { localStorage.setItem('em-last-worker', id); } catch (_e) { /* ignore */ }
    enrichmentManagerState.selectedItems.clear();
    enrichmentManagerState.breakdown = null;
    enrichmentManagerState.unmatched = null;
    enrichmentManagerState.priority = '';
    enrichmentManagerState.search = '';
    enrichmentManagerState.page = 0;
    enrichmentManagerState.statusFilter = 'unmatched';
    _emHighlightRail();

    // Pick a default entity tab the worker actually supports (filled after the
    // unmatched call returns entity_types; default to artist meanwhile).
    enrichmentManagerState.entityTab = 'artist';
    renderEnrichmentPanel();
    await Promise.all([_emLoadBreakdown(id), _emLoadUnmatched(), _emLoadPriority(id)]);
    renderEnrichmentPanel();
}

async function _emLoadPriority(id) {
    try {
        const res = await fetch(`/api/enrichment/${id}/priority`);
        enrichmentManagerState.priority = res.ok ? ((await res.json()).priority || '') : '';
    } catch (_e) {
        enrichmentManagerState.priority = '';
    }
}

// Which phase the worker is on right now, from current_item.type.
function _emCurrentPhase(status) {
    const t = status && status.current_item && status.current_item.type;
    if (!t) return '';
    if (t.indexOf('artist') === 0) return 'artist';
    if (t.indexOf('album') === 0) return 'album';
    if (t.indexOf('track') === 0) return 'track';
    return '';
}

async function _emLoadBreakdown(id) {
    try {
        const res = await fetch(`/api/enrichment/${id}/breakdown`);
        enrichmentManagerState.breakdown = res.ok ? (await res.json()).breakdown : null;
    } catch (_e) {
        enrichmentManagerState.breakdown = null;
    }
}

async function _emLoadUnmatched() {
    const id = enrichmentManagerState.selected;
    const token = ++enrichmentManagerState.loadToken;
    const { entityTab, statusFilter, search, page, pageSize } = enrichmentManagerState;
    const params = new URLSearchParams({
        entity_type: entityTab,
        status: statusFilter,
        limit: String(pageSize),
        offset: String(page * pageSize),
    });
    if (search) params.set('q', search);
    try {
        const res = await fetch(`/api/enrichment/${id}/unmatched?${params}`);
        const data = res.ok ? await res.json() : { total: 0, items: [] };
        if (token !== enrichmentManagerState.loadToken) return; // stale
        enrichmentManagerState.unmatched = data;
    } catch (_e) {
        if (token === enrichmentManagerState.loadToken) {
            enrichmentManagerState.unmatched = { total: 0, items: [] };
        }
    }
}

// ── Detail panel ──────────────────────────────────────────────────────────────

function renderEnrichmentPanel() {
    const panel = document.getElementById('em-panel');
    if (!panel) return;
    const id = enrichmentManagerState.selected;
    const worker = _emWorkerById[id];
    if (!worker) { panel.innerHTML = ''; return; }

    // Theme the whole panel to the selected worker's accent colour.
    panel.style.setProperty('--em-accent', worker.color);
    panel.style.setProperty('--em-accent-rgb', _emHexToRgb(worker.color));

    panel.innerHTML = `
        <div class="em-panel-header" id="em-panel-header"></div>
        <div class="em-banner" id="em-banner" hidden></div>
        <div class="em-section-label em-section-label--row">
            <span>Coverage &amp; processing order <span class="em-section-sub">— click a group to enrich it first</span></span>
            <span class="em-coverage-overall" id="em-coverage-overall"></span>
        </div>
        <div class="em-cards" id="em-cards"></div>
        <div class="em-unmatched">
            <div class="em-unmatched-controls" id="em-unmatched-controls"></div>
            <div class="em-bulk-bar" id="em-bulk-bar" hidden></div>
            <div class="em-unmatched-list" id="em-unmatched-list" onkeydown="onEnrichmentListKey(event)"></div>
            <div class="em-pager" id="em-pager"></div>
        </div>`;
    _emRenderPanelHeader();
    _emRenderEntityCards();
    _emRenderUnmatchedControls();
    _emRenderUnmatchedList();
}

// Combined coverage + processing-order cards. Each entity card both visualises
// coverage (matched/not_found/pending segmented bar + %) AND is the click target
// to pin that group to enrich first ('now' = live phase, '📌 first' = pinned,
// '✓ done' = nothing left). One section instead of two redundant ones.
function _emRenderEntityCards() {
    const host = document.getElementById('em-cards');
    if (!host) return;
    const id = enrichmentManagerState.selected;
    const bd = enrichmentManagerState.breakdown;
    const supported = _emOrderEntities(
        (enrichmentManagerState.unmatched && enrichmentManagerState.unmatched.entity_types)
        || (bd && Object.keys(bd)) || ['artist']
    );

    if (!bd) {
        host.innerHTML = supported.map(() => `
            <div class="em-card em-skel-card">
                <div class="em-skel em-skel-line" style="width:45%"></div>
                <div class="em-skel em-skel-bar"></div>
                <div class="em-skel em-skel-line" style="width:70%"></div>
            </div>`).join('');
        return;
    }

    const status = enrichmentManagerState.statuses[id];
    const phase = _emCurrentPhase(status);
    const pinned = enrichmentManagerState.priority;
    const glyphs = { artist: '🎤', album: '💿', track: '🎵' };

    host.innerHTML = supported.map(e => {
        const d = bd[e] || {};
        const total = d.total || 0, matched = d.matched || 0, nf = d.not_found || 0, pend = d.pending || 0;
        const pct = total ? Math.round((matched / total) * 100) : 0;
        const seg = (n) => (total ? (n / total) * 100 : 0);
        const isPinned = pinned === e, isCurrent = phase === e, isDone = total > 0 && (pend + nf) === 0;
        const cls = ['em-card',
            isPinned ? 'em-card--pinned' : '',
            isCurrent ? 'em-card--current' : '',
            isDone ? 'em-card--done' : ''].filter(Boolean).join(' ');
        const badge = isPinned
            ? '<span class="em-card-badge em-card-badge--pin">📌 First</span>'
            : isCurrent
                ? '<span class="em-card-badge em-card-badge--now">● Now</span>'
                : isDone
                    ? '<span class="em-card-badge em-card-badge--done">✓ Done</span>'
                    : `<span class="em-card-badge">${(pend + nf).toLocaleString()} left</span>`;
        return `
            <button class="${cls}" title="${isPinned ? 'Pinned first — click for automatic order' : 'Process ' + _emEntityLabel(e, true).toLowerCase() + ' first'}"
                    onclick="setEnrichmentPriority('${isPinned ? '' : e}')">
                <div class="em-card-top">
                    <span class="em-card-glyph">${glyphs[e] || '•'}</span>
                    <span class="em-card-title">${_emEntityLabel(e, true)}</span>
                    ${badge}
                    <span class="em-card-pct">${pct}<span class="em-stat-pct-sym">%</span></span>
                </div>
                <div class="em-seg" title="${matched.toLocaleString()} matched · ${nf.toLocaleString()} not found · ${pend.toLocaleString()} pending">
                    <div class="em-seg-fill em-seg--matched" data-pct="${seg(matched)}" style="width:0%"></div>
                    <div class="em-seg-fill em-seg--nf" data-pct="${seg(nf)}" style="width:0%"></div>
                    <div class="em-seg-fill em-seg--pend" data-pct="${seg(pend)}" style="width:0%"></div>
                </div>
                <div class="em-stat-legend">
                    <span class="em-leg em-leg--matched" title="matched"><i></i>${matched.toLocaleString()}</span>
                    <span class="em-leg em-leg--nf" title="not found"><i></i>${nf.toLocaleString()}</span>
                    <span class="em-leg em-leg--pend" title="pending"><i></i>${pend.toLocaleString()}</span>
                </div>
            </button>`;
    }).join('');

    // Overall matched coverage across every entity type.
    const overall = document.getElementById('em-coverage-overall');
    if (overall) {
        let m = 0, t = 0;
        Object.values(bd).forEach(d => { m += d.matched || 0; t += d.total || 0; });
        const pct = t ? Math.round((m / t) * 100) : 0;
        overall.innerHTML = t ? `<strong>${pct}%</strong> matched · ${m.toLocaleString()} of ${t.toLocaleString()}` : '';
    }

    requestAnimationFrame(() => {
        host.querySelectorAll('.em-seg-fill').forEach(el => { el.style.width = `${el.dataset.pct || 0}%`; });
    });
}

async function setEnrichmentPriority(entity) {
    const id = enrichmentManagerState.selected;
    const name = _emWorkerById[id].name;
    try {
        const res = await fetch(`/api/enrichment/${id}/priority`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ entity: entity || 'none' }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.success) {
            showToast(data.error || 'Could not set processing order', 'error');
            return;
        }
        enrichmentManagerState.priority = data.priority || '';

        if (!enrichmentManagerState.priority) {
            showToast(`${name} back to automatic order`, 'success');
            _emRenderEntityCards();
            return;
        }

        // Pin means "process the whole group". Re-queue this group's
        // previously-failed items (not_found -> pending) so the worker sweeps
        // ALL unmatched, not just never-tried ones. Safe: each is attempted
        // once; still-unmatched become not_found again and the pending-only
        // pin won't re-pick them, so no loop.
        let reset = 0;
        try {
            const rr = await fetch(`/api/enrichment/${id}/retry`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ entity_type: entity, scope: 'failed' }),
            });
            const rd = await rr.json().catch(() => ({}));
            if (rr.ok) reset = rd.reset || 0;
        } catch (_e) { /* priority still set; failed sweep is best-effort */ }

        const label = _emEntityLabel(entity, true).toLowerCase();
        showToast(reset
            ? `${name} will process all ${label} first · re-queued ${reset.toLocaleString()} previously-failed`
            : `${name} will process ${label} first`, 'success');

        // Counts changed (failed -> pending) — refresh cards + list.
        await Promise.all([_emLoadBreakdown(id), _emLoadUnmatched()]);
        _emRenderEntityCards();
        _emRenderUnmatchedControls();
        _emRenderUnmatchedList();
    } catch (_e) {
        showToast('Error setting processing order', 'error');
    }
}

function _emRenderPanelHeader() {
    const host = document.getElementById('em-panel-header');
    if (!host) return;
    const id = enrichmentManagerState.selected;
    const worker = _emWorkerById[id];
    // Structure is rendered once per worker selection; the live bits below
    // (pill / current-item / errors / toggle) are updated in place by
    // _emUpdateHeaderLive on each poll so the logo never reflows or flickers.
    host.innerHTML = `
        <div class="em-hero">
            <div class="em-hero-glow"></div>
            ${_emIconHtml(id, 'lg')}
            <div class="em-ph-titles">
                <div class="em-ph-nameline">
                    <span class="em-ph-name">${_emEscape(worker.name)} <span class="em-ph-name-sub">enrichment</span></span>
                    <span class="em-pill" id="em-ph-pill"></span>
                </div>
                <div class="em-ph-sub" id="em-ph-current"></div>
            </div>
            <div class="em-ph-actions">
                <span id="em-ph-errors"></span>
                <span id="em-ph-budget"></span>
                <button class="em-btn" id="em-ph-toggle" onclick="toggleEnrichmentWorker('${id}')"></button>
            </div>
        </div>`;
    _emUpdateHeaderLive();
}

function _emUpdateHeaderLive() {
    const id = enrichmentManagerState.selected;
    const status = enrichmentManagerState.statuses[id];
    const info = _emStatusInfo(status);

    const pill = document.getElementById('em-ph-pill');
    if (pill) { pill.className = `em-pill em-pill--${info.cls}`; pill.textContent = info.label; }

    const hero = document.querySelector('#em-panel-header .em-hero');
    if (hero) hero.classList.toggle('em-hero--live', info.cls === 'running');

    const cur = document.getElementById('em-ph-current');
    if (cur) {
        const item = status && status.current_item;
        cur.innerHTML = item
            ? `Now enriching: <strong>${_emEscape(item.name || '')}</strong>${item.type ? ` <span class="em-muted">(${_emEscape(item.type)})</span>` : ''}`
            : '<span class="em-muted">No item processing</span>';
    }

    const budgetEl = document.getElementById('em-ph-budget');
    if (budgetEl) {
        const b = status && status.daily_budget;
        budgetEl.innerHTML = (b && b.limit)
            ? `<span class="em-chip" title="Daily API budget">Budget ${b.used ?? '?'} / ${b.limit}</span>` : '';
    }

    const errEl = document.getElementById('em-ph-errors');
    if (errEl) {
        const errors = (status && status.stats && status.stats.errors) || 0;
        errEl.innerHTML = errors ? `<span class="em-chip em-chip--err" title="Errors this run">⚠ ${errors}</span>` : '';
    }

    const toggle = document.getElementById('em-ph-toggle');
    if (toggle) {
        const isPaused = status && status.paused;
        toggle.disabled = !(status && status.enabled);
        toggle.classList.toggle('em-btn--go', !!isPaused);
        toggle.textContent = isPaused ? '▶ Resume' : '⏸ Pause';
    }

    // #1 unconfigured + #2 rate-limit banner.
    const banner = document.getElementById('em-banner');
    if (banner) {
        let html = '', cls = 'em-banner';
        if (status && status.enabled === false) {
            cls += ' em-banner--warn';
            html = '⚙️ This source isn’t configured — add its credentials in Settings. '
                 + 'Browsing works, but matches and retries won’t run until it’s set up.';
        } else if (status && status.rate_limited) {
            cls += ' em-banner--warn';
            const rl = status.rate_limit || {};
            const secs = rl.retry_after || rl.reset_in || rl.cooldown_seconds;
            const when = secs ? ` — resumes in ~${_emHumanDuration(secs)}` : ' — it will resume automatically';
            html = `⏳ Rate-limited by the source${when}.`;
        }
        banner.className = cls;
        banner.innerHTML = html;
        banner.hidden = !html;
    }
}

function _emHumanDuration(seconds) {
    const s = Math.max(0, Math.round(Number(seconds) || 0));
    if (s < 60) return `${s}s`;
    const m = Math.round(s / 60);
    return m < 60 ? `${m}m` : `${Math.round(m / 60)}h`;
}

function _emRenderUnmatchedControls() {
    const host = document.getElementById('em-unmatched-controls');
    if (!host) return;
    const data = enrichmentManagerState.unmatched;
    const supported = (data && data.entity_types) || ['artist'];
    const total = data ? (data.total || 0) : null;
    const entity = enrichmentManagerState.entityTab;
    const failed = enrichmentManagerState.breakdown?.[entity]?.not_found || 0;
    const tabs = supported.map(e => `
        <button class="em-seg-tab ${e === enrichmentManagerState.entityTab ? 'active' : ''}"
                onclick="setEnrichmentEntityTab('${e}')">${_emEntityLabel(e, true)}</button>`).join('');
    const bulkBtn = failed
        ? `<button class="em-btn em-btn--sm em-btn--ghost em-retry-all" title="Re-queue every not-found ${_emEntityLabel(entity, true).toLowerCase()}"
                   onclick="retryAllFailedEnrichment(this)">↻ Retry all failed</button>`
        : '';

    host.innerHTML = `
        <div class="em-unmatched-bar">
            <div class="em-section-label em-section-label--inline">
                Needs matching
                ${total == null ? '' : `<span class="em-count">${total.toLocaleString()}</span>`}
                ${bulkBtn}
            </div>
            <div class="em-filter-row">
                <div class="em-seg-tabs">${tabs}</div>
                <select class="em-select" onchange="setEnrichmentStatusFilter(this.value)">
                    <option value="unmatched" ${enrichmentManagerState.statusFilter === 'unmatched' ? 'selected' : ''}>All unmatched</option>
                    <option value="not_found" ${enrichmentManagerState.statusFilter === 'not_found' ? 'selected' : ''}>Not found</option>
                    <option value="pending" ${enrichmentManagerState.statusFilter === 'pending' ? 'selected' : ''}>Pending</option>
                </select>
                <div class="em-search-wrap">
                    <span class="em-search-ico">⌕</span>
                    <input class="em-search" type="text" placeholder="Search name…"
                           value="${_emEscape(enrichmentManagerState.search)}"
                           oninput="onEnrichmentSearchInput(this.value)">
                </div>
            </div>
        </div>
        <div class="em-hint">Failed lookups auto-retry after 30 days · “Retry” re-queues immediately · “Match” assigns a result by hand.</div>`;
}

function _emRenderUnmatchedList() {
    const host = document.getElementById('em-unmatched-list');
    if (!host) return;
    const data = enrichmentManagerState.unmatched;
    if (!data) {
        host.innerHTML = Array.from({ length: 6 }, () => `
            <div class="em-row em-skel-row">
                <div class="em-skel em-row-img"></div>
                <div class="em-row-info">
                    <div class="em-skel em-skel-line" style="width:55%"></div>
                    <div class="em-skel em-skel-line" style="width:30%;margin-top:6px"></div>
                </div>
            </div>`).join('');
        return;
    }
    // Keep the count badge in sync without re-rendering the controls (would
    // steal focus from the search box mid-type).
    const countEl = document.querySelector('#em-unmatched-controls .em-count');
    if (countEl) countEl.textContent = (data.total || 0).toLocaleString();

    if (!data.items.length) {
        const allMatched = enrichmentManagerState.statusFilter === 'unmatched';
        host.innerHTML = `<div class="em-empty">
            <div class="em-empty-emoji">${allMatched ? '🎉' : '🔍'}</div>
            <div>${allMatched
                ? 'Every item is matched for this source.'
                : 'Nothing matches this filter.'}</div>
        </div>`;
    } else {
        const id = enrichmentManagerState.selected;
        const entity = enrichmentManagerState.entityTab;
        host.innerHTML = data.items.map(item => {
            // Unmatched items rarely have artwork yet, so the box always shows a
            // subtle entity glyph; a real image (if any) layers over it and, on
            // error, removes itself to reveal the glyph — no ragged gaps.
            const phGlyph = { artist: '🎤', album: '💿', track: '🎵' }[entity] || '♪';
            const pic = item.image_url
                ? `<img class="em-row-img-pic" src="${_emEscape(item.image_url)}" alt="" loading="lazy" onerror="this.remove()">`
                : '';
            const img = `<div class="em-row-img em-row-img--ph">${phGlyph}${pic}</div>`;
            const rel = _emRelativeTime(item.last_attempted);
            const last = rel
                ? `<span class="em-muted">tried ${rel}</span>`
                : '<span class="em-muted">never tried</span>';
            const statusBadge = item.status === 'not_found'
                ? '<span class="em-chip em-chip--nf">not found</span>'
                : '<span class="em-chip em-chip--pend">pending</span>';
            const safeName = _emEscape(item.name || 'Unknown');
            const safeId = _emEscape(item.id);
            const parent = item.parent
                ? `<span class="em-row-parent" title="${_emEscape(item.parent)}">· ${_emEscape(item.parent)}</span>`
                : '';
            const checked = enrichmentManagerState.selectedItems.has(String(item.id)) ? 'checked' : '';
            const rowCls = item.status === 'not_found' ? 'em-row em-row--nf' : 'em-row em-row--pend';
            return `
                <div class="${rowCls}">
                    <input type="checkbox" class="em-row-check" ${checked}
                           aria-label="Select ${safeName}"
                           onchange="toggleEnrichmentRowSelect('${safeId}', this.checked)">
                    ${img}
                    <div class="em-row-info">
                        <div class="em-row-name" title="${safeName}">${safeName} ${parent}</div>
                        <div class="em-row-meta">${statusBadge} ${last}</div>
                    </div>
                    <div class="em-row-actions">
                        <button class="em-btn em-btn--sm" onclick="openEnrichmentMatch('${id}','${entity}','${safeId}', this)">Match</button>
                        <button class="em-btn em-btn--sm em-btn--ghost" title="Re-queue for the worker to try again"
                                onclick="retryEnrichmentItem('${id}','${entity}','${safeId}', this)">Retry</button>
                    </div>
                </div>`;
        }).join('');
    }
    _emRenderPager();
    _emRenderBulkBar();
}

function _emRenderBulkBar() {
    const bar = document.getElementById('em-bulk-bar');
    if (!bar) return;
    const n = enrichmentManagerState.selectedItems.size;
    if (!n) { bar.hidden = true; bar.innerHTML = ''; return; }
    bar.hidden = false;
    bar.innerHTML = `
        <span class="em-bulk-count">${n} selected</span>
        <button class="em-btn em-btn--sm" onclick="retrySelectedEnrichment(this)">↻ Retry selected</button>
        <button class="em-btn em-btn--sm em-btn--ghost" onclick="clearEnrichmentSelection()">Clear</button>`;
}

function toggleEnrichmentRowSelect(id, checked) {
    if (checked) enrichmentManagerState.selectedItems.add(String(id));
    else enrichmentManagerState.selectedItems.delete(String(id));
    _emRenderBulkBar();
}

function clearEnrichmentSelection() {
    enrichmentManagerState.selectedItems.clear();
    _emRenderUnmatchedList();
}

// #6 keyboard nav: ↑/↓ moves focus between rows' Match buttons (Enter/Space
// then activates natively). The list isn't refreshed by polling, so focus
// stays put between user actions.
function onEnrichmentListKey(e) {
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
    const host = document.getElementById('em-unmatched-list');
    if (!host) return;
    const btns = Array.from(host.querySelectorAll('.em-row .em-row-actions .em-btn:first-child'));
    if (!btns.length) return;
    e.preventDefault();
    const idx = btns.indexOf(document.activeElement);
    const next = e.key === 'ArrowDown'
        ? (idx < 0 ? 0 : Math.min(idx + 1, btns.length - 1))
        : (idx <= 0 ? 0 : idx - 1);
    btns[next].focus();
}

async function retrySelectedEnrichment(btn) {
    const service = enrichmentManagerState.selected;
    const entity = enrichmentManagerState.entityTab;
    const ids = Array.from(enrichmentManagerState.selectedItems);
    if (!ids.length) return;
    if (btn) { btn.disabled = true; btn.textContent = 'Re-queuing…'; }
    let ok = 0;
    // Cap concurrency to be gentle on the server.
    for (let i = 0; i < ids.length; i += 5) {
        const slice = ids.slice(i, i + 5);
        const results = await Promise.all(slice.map(eid =>
            fetch(`/api/enrichment/${service}/retry`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ entity_type: entity, scope: 'item', entity_id: eid }),
            }).then(r => r.ok).catch(() => false)
        ));
        ok += results.filter(Boolean).length;
    }
    showToast(`Re-queued ${ok.toLocaleString()} item(s)`, ok ? 'success' : 'error');
    enrichmentManagerState.selectedItems.clear();
    await Promise.all([_emLoadBreakdown(service), _emLoadUnmatched()]);
    _emRenderEntityCards();
    _emRenderUnmatchedList();
}

function _emRenderPager() {
    const host = document.getElementById('em-pager');
    if (!host) return;
    const data = enrichmentManagerState.unmatched;
    if (!data) { host.innerHTML = ''; return; }
    const { page, pageSize } = enrichmentManagerState;
    const total = data.total || 0;
    const from = total ? page * pageSize + 1 : 0;
    const to = Math.min((page + 1) * pageSize, total);
    const hasPrev = page > 0;
    const hasNext = to < total;
    host.innerHTML = `
        <button class="em-btn em-btn--sm" ${hasPrev ? '' : 'disabled'} onclick="changeEnrichmentPage(-1)">‹ Prev</button>
        <span class="em-muted">${from}–${to} of ${total.toLocaleString()}</span>
        <button class="em-btn em-btn--sm" ${hasNext ? '' : 'disabled'} onclick="changeEnrichmentPage(1)">Next ›</button>`;
}

// ── Controls ──────────────────────────────────────────────────────────────────

async function setEnrichmentEntityTab(entity) {
    enrichmentManagerState.entityTab = entity;
    enrichmentManagerState.page = 0;
    _emRenderUnmatchedControls();
    document.getElementById('em-unmatched-list').innerHTML = '<div class="enhanced-loading"><div class="spinner"></div></div>';
    await _emLoadUnmatched();
    _emRenderUnmatchedList();
}

async function setEnrichmentStatusFilter(value) {
    enrichmentManagerState.statusFilter = value;
    enrichmentManagerState.page = 0;
    await _emLoadUnmatched();
    _emRenderUnmatchedList();
}

let _emSearchDebounce = null;
function onEnrichmentSearchInput(value) {
    enrichmentManagerState.search = value;
    enrichmentManagerState.page = 0;
    if (_emSearchDebounce) clearTimeout(_emSearchDebounce);
    _emSearchDebounce = setTimeout(async () => {
        await _emLoadUnmatched();
        _emRenderUnmatchedList();
    }, 300);
}

async function changeEnrichmentPage(delta) {
    enrichmentManagerState.page = Math.max(0, enrichmentManagerState.page + delta);
    await _emLoadUnmatched();
    _emRenderUnmatchedList();
}

async function toggleEnrichmentWorker(id) {
    const status = enrichmentManagerState.statuses[id];
    const action = status?.paused ? 'resume' : 'pause';
    try {
        const res = await fetch(`/api/enrichment/${id}/${action}`, { method: 'POST' });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
            showToast(data.error || `Could not ${action} worker`, 'error');
            return;
        }
        showToast(`${_emWorkerById[id].name} ${action === 'pause' ? 'paused' : 'resumed'}`, 'success');
        await _emPollSelected();
    } catch (_e) {
        showToast(`Error trying to ${action} worker`, 'error');
    }
}

async function retryEnrichmentItem(service, entityType, entityId, btn) {
    if (btn) { btn.disabled = true; btn.textContent = '…'; }
    try {
        // Reset match_status to NULL (pending) so the worker re-attempts on its
        // next pass — see /retry (clearing to not_found would NOT re-queue).
        const res = await fetch(`/api/enrichment/${service}/retry`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ entity_type: entityType, scope: 'item', entity_id: entityId }),
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok && data.success) {
            showToast('Re-queued for enrichment', 'success');
            await Promise.all([_emLoadBreakdown(service), _emLoadUnmatched()]);
            _emRenderEntityCards();
            _emRenderUnmatchedList();
        } else {
            showToast(data.error || 'Failed to re-queue', 'error');
            if (btn) { btn.disabled = false; btn.textContent = 'Retry'; }
        }
    } catch (_e) {
        showToast('Error re-queuing item', 'error');
        if (btn) { btn.disabled = false; btn.textContent = 'Retry'; }
    }
}

// Bulk: re-queue every not_found item of the current entity type.
async function retryAllFailedEnrichment(btn) {
    const service = enrichmentManagerState.selected;
    const entity = enrichmentManagerState.entityTab;
    const bd = enrichmentManagerState.breakdown?.[entity];
    const failed = bd ? (bd.not_found || 0) : 0;
    if (!failed) { showToast('No failed items to retry', 'info'); return; }
    if (!confirm(`Re-queue all ${failed.toLocaleString()} not-found ${_emEntityLabel(entity, true).toLowerCase()} for ${_emWorkerById[service].name}? The worker will retry them on its next pass.`)) return;
    if (btn) { btn.disabled = true; btn.textContent = 'Re-queuing…'; }
    try {
        const res = await fetch(`/api/enrichment/${service}/retry`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ entity_type: entity, scope: 'failed' }),
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok && data.success) {
            showToast(`Re-queued ${(data.reset || 0).toLocaleString()} item(s)`, 'success');
            enrichmentManagerState.page = 0;
            await Promise.all([_emLoadBreakdown(service), _emLoadUnmatched()]);
            _emRenderEntityCards();
            _emRenderUnmatchedControls();
            _emRenderUnmatchedList();
        } else {
            showToast(data.error || 'Failed to re-queue', 'error');
        }
    } catch (_e) {
        showToast('Error re-queuing failed items', 'error');
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = '↻ Retry all failed'; }
    }
}

// ── Inline manual match (decoupled from the library artist-detail page) ───────

function openEnrichmentMatch(service, entityType, entityId, anchorBtn) {
    const defaultQuery = anchorBtn
        ? (anchorBtn.closest('.em-row')?.querySelector('.em-row-name')?.textContent || '')
        : '';
    const existing = document.getElementById('enrichment-match-overlay');
    if (existing) existing.remove();

    const overlay = document.createElement('div');
    overlay.id = 'enrichment-match-overlay';
    overlay.className = 'modal-overlay em-overlay';
    overlay.style.zIndex = '10010'; // above the manager modal
    overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };
    overlay.innerHTML = `
        <div class="enhanced-manual-match-modal em-in">
            <div class="enhanced-bulk-modal-header">
                <h3>Match ${_emEntityLabel(entityType)} on ${_emEscape(_emWorkerById[service]?.name || service)}</h3>
                <button class="enhanced-bulk-modal-close">&times;</button>
            </div>
            <div class="enhanced-match-search-row">
                <input type="text" class="enhanced-match-search-input" placeholder="Search…" value="${_emEscape(defaultQuery)}">
                <button class="enhanced-enrich-btn em-match-go">Search</button>
            </div>
            <div class="enhanced-match-results" id="enrichment-match-results">
                <div class="enhanced-match-results-hint">Search to find a match.</div>
            </div>
        </div>`;
    document.body.appendChild(overlay);

    const input = overlay.querySelector('.enhanced-match-search-input');
    const results = overlay.querySelector('#enrichment-match-results');
    overlay.querySelector('.enhanced-bulk-modal-close').onclick = () => overlay.remove();
    const run = () => _emRunMatchSearch(service, entityType, entityId, input.value, results, overlay);
    overlay.querySelector('.em-match-go').onclick = run;
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') run(); });
    if (defaultQuery.trim()) run();
    setTimeout(() => input.focus(), 50);
}

async function _emRunMatchSearch(service, entityType, entityId, query, container, overlay) {
    if (!query.trim()) {
        container.innerHTML = '<div class="enhanced-match-results-hint">Enter a search term</div>';
        return;
    }
    container.innerHTML = '<div class="enhanced-loading"><div class="spinner"></div></div>';
    try {
        const res = await fetch('/api/library/search-service', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ service, entity_type: entityType, query: query.trim() }),
        });
        const data = await res.json();
        if (!data.success) throw new Error(data.error || 'Search failed');
        const list = data.results || [];
        if (!list.length) {
            container.innerHTML = '<div class="enhanced-match-results-hint">No results. Try a different search.</div>';
            return;
        }
        container.innerHTML = '';
        list.forEach(r => {
            const row = document.createElement('div');
            row.className = 'enhanced-match-result-row';
            const imgHtml = r.image
                ? `<img class="enhanced-match-result-img" src="${_emEscape(r.image)}" alt="" onerror="this.style.display='none'">`
                : '<div class="enhanced-match-result-img-placeholder">&#127925;</div>';
            const providerLabel = r.provider && r.provider !== service ? ` (${_emEscape(r.provider)})` : '';
            row.innerHTML = `
                ${imgHtml}
                <div class="enhanced-match-result-info">
                    <div class="enhanced-match-result-name">${_emEscape(r.name || 'Unknown')}</div>
                    ${r.extra ? `<div class="enhanced-match-result-extra">${_emEscape(r.extra)}</div>` : ''}
                    <div class="enhanced-match-result-id">ID: ${_emEscape(r.id)}${providerLabel}</div>
                </div>`;
            const btn = document.createElement('button');
            btn.className = 'enhanced-meta-save-btn';
            btn.textContent = 'Match';
            btn.onclick = () => _emApplyMatch(entityType, entityId, r.provider || service, r.id, overlay);
            row.appendChild(btn);
            container.appendChild(row);
        });
    } catch (e) {
        container.innerHTML = `<div class="enhanced-match-results-hint">Search error: ${_emEscape(e.message)}</div>`;
    }
}

async function _emApplyMatch(entityType, entityId, service, serviceId, overlay) {
    try {
        const res = await fetch('/api/library/manual-match', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ entity_type: entityType, entity_id: entityId, service, service_id: serviceId }),
        });
        const data = await res.json();
        if (data.success) {
            showToast('Matched ✓', 'success');
            if (overlay) overlay.remove();
            // Refresh the manager's stats + list for the *selected* worker.
            const sel = enrichmentManagerState.selected;
            await Promise.all([_emLoadBreakdown(sel), _emLoadUnmatched()]);
            _emRenderEntityCards();
            _emRenderUnmatchedList();
        } else {
            showToast(data.error || 'Failed to match', 'error');
        }
    } catch (_e) {
        showToast('Error applying match', 'error');
    }
}

// Expose for inline onclick handlers.
window.openEnrichmentManager = openEnrichmentManager;
window.closeEnrichmentManager = closeEnrichmentManager;
window.refreshEnrichmentManager = refreshEnrichmentManager;
window.selectEnrichmentWorker = selectEnrichmentWorker;
window.setEnrichmentEntityTab = setEnrichmentEntityTab;
window.setEnrichmentStatusFilter = setEnrichmentStatusFilter;
window.onEnrichmentSearchInput = onEnrichmentSearchInput;
window.changeEnrichmentPage = changeEnrichmentPage;
window.toggleEnrichmentWorker = toggleEnrichmentWorker;
window.setEnrichmentPriority = setEnrichmentPriority;
window.setGlobalPriority = setGlobalPriority;
window.retryEnrichmentItem = retryEnrichmentItem;
window.retryAllFailedEnrichment = retryAllFailedEnrichment;
window.toggleEnrichmentRowSelect = toggleEnrichmentRowSelect;
window.clearEnrichmentSelection = clearEnrichmentSelection;
window.retrySelectedEnrichment = retrySelectedEnrichment;
window.onEnrichmentListKey = onEnrichmentListKey;
window.openEnrichmentMatch = openEnrichmentMatch;
