// Watchlist Scan History modal (#831 round 2).
//
// Every scan run is persisted server-side (watchlist_scan_runs) with its full
// track ledger — the tracks the run ADDED to the wishlist plus the found-but-
// skipped ones. This modal lists past runs (newest first) and expands each
// into its ledger. Note: this is what the watchlist PUT IN THE WISHLIST — the
// watchlist never downloads; downloaded tracks live in Download Origins.

let _wlhModalEl = null;
let _wlhRuns = [];
const _wlhEventsCache = new Map();

function openWatchlistHistoryModal() {
    if (!_wlhModalEl) {
        _wlhModalEl = document.createElement('div');
        _wlhModalEl.className = 'modal-overlay origin-modal-overlay';
        _wlhModalEl.innerHTML = `
            <div class="origin-modal">
                <div class="origin-modal-head">
                    <div>
                        <h2 class="origin-modal-title">Scan History</h2>
                        <p class="origin-modal-sub">Every watchlist scan and the tracks it added to your wishlist.</p>
                    </div>
                    <button class="origin-modal-close" onclick="closeWatchlistHistoryModal()" aria-label="Close">✕</button>
                </div>
                <div class="origin-modal-body" id="wlh-modal-body"></div>
            </div>`;
        _wlhModalEl.addEventListener('click', (e) => {
            if (e.target === _wlhModalEl) closeWatchlistHistoryModal();
        });
        document.body.appendChild(_wlhModalEl);
    }
    _wlhModalEl.classList.remove('hidden');
    _wlhLoadRuns();
}

function closeWatchlistHistoryModal() {
    if (_wlhModalEl) _wlhModalEl.classList.add('hidden');
}

async function _wlhLoadRuns() {
    const body = document.getElementById('wlh-modal-body');
    body.innerHTML = '<div class="origin-modal-loading">Loading…</div>';
    try {
        const resp = await fetch('/api/watchlist/scan/history?limit=50');
        const data = await resp.json();
        if (!data.success) throw new Error(data.error || 'Failed to load');
        _wlhRuns = data.runs || [];
        _wlhRenderRuns();
    } catch (err) {
        body.innerHTML = `<div class="origin-modal-empty">Couldn't load: ${escapeHtml(err.message)}</div>`;
    }
}

function _wlhRenderRuns() {
    const body = document.getElementById('wlh-modal-body');
    if (!_wlhRuns.length) {
        body.innerHTML = '<div class="origin-modal-empty">No scans recorded yet. Run a watchlist scan and it will appear here.</div>';
        return;
    }
    body.innerHTML = _wlhRuns.map(r => {
        const when = _wlhFormatDate(r.completed_at || r.started_at);
        const cancelled = r.status === 'cancelled';
        return `<div class="wlh-run" data-run="${escapeHtml(r.run_id)}">
            <button type="button" class="wlh-run-header" onclick="toggleWatchlistHistoryRun('${escapeHtml(r.run_id)}', this)">
                <span class="origin-group-caret">▸</span>
                <span class="wlh-run-when">${escapeHtml(when)}</span>
                ${cancelled ? '<span class="wlh-run-status cancelled">cancelled</span>' : ''}
                <span class="wlh-run-stats">
                    <span class="wlh-run-stat">${r.artists_scanned || 0}<i>artists</i></span>
                    <span class="wlh-run-stat">${r.tracks_found || 0}<i>found</i></span>
                    <span class="wlh-run-stat added">${r.tracks_added || 0}<i>added</i></span>
                </span>
            </button>
            <div class="wlh-run-body" style="display: none;"></div>
        </div>`;
    }).join('');
}

async function toggleWatchlistHistoryRun(runId, btn) {
    const runEl = btn.closest('.wlh-run');
    const bodyEl = runEl.querySelector('.wlh-run-body');
    const caret = btn.querySelector('.origin-group-caret');
    const open = bodyEl.style.display !== 'none';
    if (open) {
        bodyEl.style.display = 'none';
        if (caret) caret.textContent = '▸';
        return;
    }
    bodyEl.style.display = '';
    if (caret) caret.textContent = '▾';

    if (!_wlhEventsCache.has(runId)) {
        bodyEl.innerHTML = '<div class="origin-modal-loading">Loading…</div>';
        try {
            const resp = await fetch(`/api/watchlist/scan/history/${encodeURIComponent(runId)}/tracks`);
            const data = await resp.json();
            _wlhEventsCache.set(runId, data.success ? (data.events || []) : []);
        } catch (e) {
            _wlhEventsCache.set(runId, []);
        }
    }
    bodyEl.innerHTML = _wlhRenderEvents(_wlhEventsCache.get(runId));
}

function _wlhRenderEvents(events) {
    if (!events.length) {
        return '<div class="origin-modal-empty wlh-empty">No new tracks were found by this scan.</div>';
    }
    const added = events.filter(e => e.status === 'added');
    const skipped = events.filter(e => e.status !== 'added');

    const row = (e) => `
        <div class="watchlist-live-addition-item wlh-track">
            <img src="${escapeHtml(e.album_image_url || '')}" alt="" onerror="this.style.display='none';" />
            <div class="watchlist-live-addition-item-info">
                <div class="watchlist-live-addition-item-track">${escapeHtml(e.track_name || '')}</div>
                <div class="watchlist-live-addition-item-artist">${escapeHtml(e.artist_name || '')}${e.album_name ? ' — ' + escapeHtml(e.album_name) : ''}</div>
            </div>
            ${e.status === 'added'
                ? '<span class="watchlist-scan-track-badge added">added</span>'
                : '<span class="watchlist-scan-track-badge skipped">skipped</span>'}
        </div>`;

    const section = (label, list) => list.length
        ? `<div class="watchlist-scan-tracks-section">${label} (${list.length})</div>${list.map(row).join('')}`
        : '';

    return section('Added to wishlist', added)
        + section('Found but skipped — already queued or blocklisted', skipped);
}

function _wlhFormatDate(ts) {
    if (!ts) return 'Unknown time';
    try {
        const d = new Date(ts);
        if (isNaN(d.getTime())) return ts;
        return d.toLocaleString(undefined, {
            month: 'short', day: 'numeric',
            hour: 'numeric', minute: '2-digit',
        });
    } catch (e) {
        return ts;
    }
}

window.openWatchlistHistoryModal = openWatchlistHistoryModal;
window.closeWatchlistHistoryModal = closeWatchlistHistoryModal;
window.toggleWatchlistHistoryRun = toggleWatchlistHistoryRun;
