// MUSICBRAINZ ENRICHMENT UI - PHASE 5 WEB UI
// ============================================================================
/**
 * Bundled status hydrate: every per-service status reader below goes through
 * this instead of its own /api/enrichment/<id>/status request — one
 * /status-all response serves all ~13 of them (the load-time flood fix).
 * Returns a Response-compatible object so the call sites stay untouched;
 * unknown ids / a failed bundle fall back to the real per-service request.
 */
let _enrStatusBundle = null;   // { t, promise }
function _enrichmentStatusFetch(id) {
    const now = Date.now();
    if (!_enrStatusBundle || now - _enrStatusBundle.t > 3000) {
        _enrStatusBundle = {
            t: now,
            promise: fetch('/api/enrichment/status-all')
                .then(r => (r.ok ? r.json() : null))
                .catch(() => null),
        };
    }
    return _enrStatusBundle.promise.then(bundle => {
        const payload = bundle && bundle.services ? bundle.services[id] : null;
        if (payload && !payload.error) {
            return new Response(JSON.stringify(payload), {
                status: 200, headers: { 'Content-Type': 'application/json' },
            });
        }
        return fetch(`/api/enrichment/${id}/status`);
    });
}

function updateMusicBrainzStatusFromData(data) {
    // Dispatch-only since the dashboard flip: the orb, its tooltip and the
    // status classes are React-rendered from this frame (-dash.header.ts).
    // Writing the old ids from here would fight React for its own nodes.
    // One canonical channel for all providers; the per-provider socket names
    // are a fifth, inconsistent registry the React side does not inherit.
    window.dispatchEvent(new CustomEvent('ss:enrich-status', { detail: { id: 'musicbrainz', data } }));
}

// ============================================================================
// AUDIODB ENRICHMENT UI
// ============================================================================

function updateAudioDBStatusFromData(data) {
    // Dispatch-only since the dashboard flip: the orb, its tooltip and the
    // status classes are React-rendered from this frame (-dash.header.ts).
    // Writing the old ids from here would fight React for its own nodes.
    // One canonical channel for all providers; the per-provider socket names
    // are a fifth, inconsistent registry the React side does not inherit.
    window.dispatchEvent(new CustomEvent('ss:enrich-status', { detail: { id: 'audiodb', data } }));
}

function updateDiscogsStatusFromData(data) {
    // Dispatch-only since the dashboard flip: the orb, its tooltip and the
    // status classes are React-rendered from this frame (-dash.header.ts).
    // Writing the old ids from here would fight React for its own nodes.
    // One canonical channel for all providers; the per-provider socket names
    // are a fifth, inconsistent registry the React side does not inherit.
    window.dispatchEvent(new CustomEvent('ss:enrich-status', { detail: { id: 'discogs', data } }));
}

// ===================================================================
// DEEZER ENRICHMENT STATUS
// ===================================================================

function updateDeezerStatusFromData(data) {
    // Dispatch-only since the dashboard flip: the orb, its tooltip and the
    // status classes are React-rendered from this frame (-dash.header.ts).
    // Writing the old ids from here would fight React for its own nodes.
    // One canonical channel for all providers; the per-provider socket names
    // are a fifth, inconsistent registry the React side does not inherit.
    window.dispatchEvent(new CustomEvent('ss:enrich-status', { detail: { id: 'deezer', data } }));
}

// ===================================================================
// JIOSAAVN ENRICHMENT STATUS
// ===================================================================

function updateJioSaavnStatusFromData(data) {
    // Dispatch-only since the dashboard flip: the orb, its tooltip and the
    // status classes are React-rendered from this frame (-dash.header.ts).
    // Writing the old ids from here would fight React for its own nodes.
    // One canonical channel for all providers; the per-provider socket names
    // are a fifth, inconsistent registry the React side does not inherit.
    window.dispatchEvent(new CustomEvent('ss:enrich-status', { detail: { id: 'jiosaavn', data } }));
}

// ===================================================================
// SPOTIFY ENRICHMENT STATUS
// ===================================================================

function updateSpotifyEnrichmentStatusFromData(data) {
    // Dispatch-only since the dashboard flip: the orb, its tooltip and the
    // status classes are React-rendered from this frame (-dash.header.ts).
    // Writing the old ids from here would fight React for its own nodes.
    // One canonical channel for all providers; the per-provider socket names
    // are a fifth, inconsistent registry the React side does not inherit.
    window.dispatchEvent(new CustomEvent('ss:enrich-status', { detail: { id: 'spotify', data } }));
}

// ===================================================================
// ITUNES ENRICHMENT STATUS
// ===================================================================

function updateiTunesEnrichmentStatusFromData(data) {
    // Dispatch-only since the dashboard flip: the orb, its tooltip and the
    // status classes are React-rendered from this frame (-dash.header.ts).
    // Writing the old ids from here would fight React for its own nodes.
    // One canonical channel for all providers; the per-provider socket names
    // are a fifth, inconsistent registry the React side does not inherit.
    window.dispatchEvent(new CustomEvent('ss:enrich-status', { detail: { id: 'itunes', data } }));
}

// ===================================================================
// LAST.FM ENRICHMENT STATUS
// ===================================================================

function updateLastFMEnrichmentStatusFromData(data) {
    // Dispatch-only since the dashboard flip: the orb, its tooltip and the
    // status classes are React-rendered from this frame (-dash.header.ts).
    // Writing the old ids from here would fight React for its own nodes.
    // One canonical channel for all providers; the per-provider socket names
    // are a fifth, inconsistent registry the React side does not inherit.
    window.dispatchEvent(new CustomEvent('ss:enrich-status', { detail: { id: 'lastfm', data } }));
}

// ===================================================================
// GENIUS ENRICHMENT STATUS
// ===================================================================

function updateGeniusEnrichmentStatusFromData(data) {
    // Dispatch-only since the dashboard flip: the orb, its tooltip and the
    // status classes are React-rendered from this frame (-dash.header.ts).
    // Writing the old ids from here would fight React for its own nodes.
    // One canonical channel for all providers; the per-provider socket names
    // are a fifth, inconsistent registry the React side does not inherit.
    window.dispatchEvent(new CustomEvent('ss:enrich-status', { detail: { id: 'genius', data } }));
}

// ===================================================================
// BANDCAMP ENRICHMENT WORKER
// ===================================================================
// Bandcamp is keyless (no access token) but opt-in experimental — the
// worker always reports authenticated:true; data.enabled instead reflects
// whether the user has turned it on in Settings > Advanced > Experimental
// (see core.metadata.registry.is_source_enabled). Progress is keyed by
// 'albums'/'tracks' (core/bandcamp_worker.py has no artist-level pass).

function updateBandcampEnrichmentStatusFromData(data) {
    // Dispatch-only since the dashboard flip: the orb, its tooltip and the
    // status classes are React-rendered from this frame (-dash.header.ts).
    // Writing the old ids from here would fight React for its own nodes.
    // One canonical channel for all providers; the per-provider socket names
    // are a fifth, inconsistent registry the React side does not inherit.
    window.dispatchEvent(new CustomEvent('ss:enrich-status', { detail: { id: 'bandcamp', data } }));
}

// ===================================================================
// TIDAL ENRICHMENT WORKER
// ===================================================================

function updateTidalEnrichmentStatusFromData(data) {
    // Dispatch-only since the dashboard flip: the orb, its tooltip and the
    // status classes are React-rendered from this frame (-dash.header.ts).
    // Writing the old ids from here would fight React for its own nodes.
    // One canonical channel for all providers; the per-provider socket names
    // are a fifth, inconsistent registry the React side does not inherit.
    window.dispatchEvent(new CustomEvent('ss:enrich-status', { detail: { id: 'tidal', data } }));
}

// ===================================================================
// QOBUZ ENRICHMENT WORKER
// ===================================================================

function updateQobuzEnrichmentStatusFromData(data) {
    // Dispatch-only since the dashboard flip: the orb, its tooltip and the
    // status classes are React-rendered from this frame (-dash.header.ts).
    // Writing the old ids from here would fight React for its own nodes.
    // One canonical channel for all providers; the per-provider socket names
    // are a fifth, inconsistent registry the React side does not inherit.
    window.dispatchEvent(new CustomEvent('ss:enrich-status', { detail: { id: 'qobuz', data } }));
}

// ===================================================================
// AMAZON MUSIC ENRICHMENT WORKER
// ===================================================================

function updateAmazonEnrichmentStatusFromData(data) {
    // Dispatch-only since the dashboard flip: the orb, its tooltip and the
    // status classes are React-rendered from this frame (-dash.header.ts).
    // Writing the old ids from here would fight React for its own nodes.
    // One canonical channel for all providers; the per-provider socket names
    // are a fifth, inconsistent registry the React side does not inherit.
    window.dispatchEvent(new CustomEvent('ss:enrich-status', { detail: { id: 'amazon', data } }));
}

// ===================================================================
// SIMILAR ARTISTS (MUSICMAP) WORKER — dashboard bubble
// ===================================================================

function updateSimilarArtistsEnrichmentStatusFromData(data) {
    // Dispatch-only since the dashboard flip: the orb, its tooltip and the
    // status classes are React-rendered from this frame (-dash.header.ts).
    // Writing the old ids from here would fight React for its own nodes.
    // One canonical channel for all providers; the per-provider socket names
    // are a fifth, inconsistent registry the React side does not inherit.
    window.dispatchEvent(new CustomEvent('ss:enrich-status', { detail: { id: 'similar_artists', data } }));
}

// ===================================================================
// HYDRABASE P2P MIRROR WORKER
// ===================================================================

function updateHydrabaseStatusFromData(data) {
    // Dispatch-only since the dashboard flip: the orb, its tooltip and the
    // status classes are React-rendered from this frame (-dash.header.ts).
    // Writing the old ids from here would fight React for its own nodes.
    // One canonical channel for all providers; the per-provider socket names
    // are a fifth, inconsistent registry the React side does not inherit.
    window.dispatchEvent(new CustomEvent('ss:enrich-status', { detail: { id: 'hydrabase', data } }));
}

// ===================================================================
// LIBRARY REPAIR WORKER
// ===================================================================

async function updateRepairStatus() {
    if (socketConnected) return; // WebSocket handles this
    if (document.hidden) return; // Skip polling when tab is not visible
    try {
        const response = await fetch('/api/repair/status');
        if (!response.ok) { console.warn('Repair status endpoint unavailable'); return; }
        const data = await response.json();
        updateRepairStatusFromData(data);
    } catch (error) {
        console.error('Error updating repair status:', error);
    }
}

function updateRepairStatusFromData(data) {
    // Dispatch-only since the dashboard flip. Two callers: the socket, and
    // updateRepairStatus()'s app-wide 5s HTTP poll — the ONLY live source on a
    // client with no websocket. The dashboard orb, tooltip and findings badge
    // are React-rendered from this frame, and the tools maintenance hero
    // consumes the same dispatch.
    window.dispatchEvent(new CustomEvent('ss:repair-status', { detail: data }));
}

// ── SoulID Worker Status ──

function updateSoulIDStatusFromData(data) {
    // Dispatch-only since the dashboard flip: the orb, its tooltip and the
    // status classes are React-rendered from this frame (-dash.header.ts).
    // Writing the old ids from here would fight React for its own nodes.
    // One canonical channel for all providers; the per-provider socket names
    // are a fifth, inconsistent registry the React side does not inherit.
    window.dispatchEvent(new CustomEvent('ss:enrich-status', { detail: { id: 'soulid', data } }));
}

/**
 * The dashboard worker orb's click target.
 *
 * P7: the Tools page is React now, and everything this used to do by hand — the
 * jobs tab, the master toggle, replaying in-flight progress — the maintenance
 * hero does for itself on mount. The vanilla tab renderers it used to drive
 * were UNSCOPED (`.repair-tab`, `.repair-tab-content`, `#repair-jobs-list`)
 * against selectors React now owns, so this deliberately does none of it; they
 * have since been deleted outright.
 *
 * The scroll survives because the port kept `#tools-page` and
 * `.tools-maintenance-hero` on the React markup.
 */
function openRepairModal() {
    navigateToPage('tools');
    setTimeout(() => {
        // Scope it to #tools-page: the VIDEO tools subpage carries the same hero
        // class and comes first in the document.
        const section = document.querySelector('#tools-page .tools-maintenance-hero');
        if (section) section.scrollIntoView({ behavior: 'smooth' });
    }, 100);
}

// The repair fallback poll — app-wide on purpose. updateRepairStatusFromData
// is dispatch-only since the dashboard flip, and BOTH consumers of
// ss:repair-status (the React dashboard pill and the tools maintenance hero)
// rely on this 5s HTTP poll when the websocket is down. The old
// #repair-button gate could never pass now (the orb renders after load), so
// the poll starts unconditionally; updateRepairStatus itself already skips
// ticks while the socket pushes or the tab is hidden.
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        updateRepairStatus();
        setInterval(updateRepairStatus, 5000);
    });
} else {
    updateRepairStatus();
    setInterval(updateRepairStatus, 5000);
}

// Turn a snake_case setting key into a human label. Handles acronym fix-ups
// (EP, ID, URL, MB, AC, OS) that the naive Title-Case would otherwise botch.
function _prettifyRepairSettingKey(key) {
    // Full-key label overrides — for settings whose plain prettified name
    // doesn't convey an important cost/behaviour (e.g. that it runs ffmpeg).
    const fullKeyLabels = {
        'deep_audio_verify': 'Deep Audio Verify (ffmpeg decode — CPU heavy)',
    };
    if (fullKeyLabels[key]) return fullKeyLabels[key];
    const words = key.replace(/^_+/, '').split('_');
    const acronyms = { 'eps': 'EPs', 'id': 'ID', 'url': 'URL', 'mb': 'MB',
                       'ac': 'AC', 'os': 'OS', 'api': 'API', 'mp3': 'MP3',
                       'flac': 'FLAC', 'cd': 'CD' };
    return words.map(w => acronyms[w.toLowerCase()] || (w.charAt(0).toUpperCase() + w.slice(1))).join(' ');
}

// ── Repair Job Live Progress ──

function updateRepairJobProgressFromData(data) {
    // Dispatch-only since the P7 flip. The maintenance hero is React and it
    // renders the SAME selectors this handler used to drive — .repair-job-card
    // [data-job-id], .repair-job-status, .repair-flow-badge, .repair-stop-btn
    // and its own .repair-job-progress panel — so the old body was live-mutating
    // React-managed nodes: appending duplicate log lines into React's log
    // container, replacing React's Stop button via outerHTML (a node React
    // still thinks it owns), and 30s after completion innerHTML-wiping
    // #repair-jobs-list out from under React. The hero renders every one of
    // those states itself from this same frame.
    window.dispatchEvent(new CustomEvent('ss:repair-progress', { detail: data }));
}

async function openCacheHealthModal() {
    if (document.getElementById('cache-health-modal-overlay')) return;

    const overlay = document.createElement('div');
    overlay.id = 'cache-health-modal-overlay';
    overlay.className = 'modal-overlay';
    overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };

    overlay.innerHTML = `
        <div class="cache-health-modal">
            <div class="cache-health-header">
                <div class="cache-health-header-content">
                    <div class="cache-health-header-icon">&#128202;</div>
                    <div>
                        <h2 class="cache-health-title">Cache Health</h2>
                        <p class="cache-health-subtitle">Metadata cache status across all sources</p>
                    </div>
                </div>
                <button class="watch-all-close" onclick="document.getElementById('cache-health-modal-overlay').remove()">&times;</button>
            </div>
            <div class="cache-health-body">
                <div class="cache-health-loading">
                    <div class="watch-all-loading-spinner"></div>
                    <div>Loading cache stats...</div>
                </div>
            </div>
            <div class="cache-health-footer">
                <button class="watch-all-btn watch-all-btn-cancel" onclick="document.getElementById('cache-health-modal-overlay').remove()">Close</button>
            </div>
        </div>
    `;
    document.body.appendChild(overlay);

    try {
        const response = await fetch('/api/repair/cache-health');
        if (!response.ok) throw new Error('Failed to load');
        const s = await response.json();

        const body = overlay.querySelector('.cache-health-body');
        const healthScore = s.junk_entities === 0 && s.stale_mb_nulls === 0 ? 'healthy' : s.junk_entities > 50 ? 'poor' : 'fair';
        const healthEmoji = healthScore === 'healthy' ? '&#10003;' : healthScore === 'fair' ? '&#9888;' : '&#10060;';
        const healthLabel = healthScore === 'healthy' ? 'Cache is healthy' : healthScore === 'fair' ? 'Minor issues detected' : 'Cleanup recommended';

        // "Cleanup recommended" told you a verdict and nothing else: not what
        // was wrong, not that a job already fixes it, not how to trigger it.
        // Boulder: "it says cleanup recommeneded. but how lol."
        //
        // Every one of these IS handled automatically by the Cache Maintenance
        // job (cache_evictor, every 6h by default) — it calls exactly the
        // routines that clear the two numbers this verdict is computed from. So
        // the honest answer is usually "nothing, it cleans itself" — unless the
        // job is switched off, which is the one case worth shouting about.
        let cleanupJob = null;
        try {
            const jr = await fetch('/api/repair/jobs');
            if (jr.ok) {
                const jobs = await jr.json();
                const list = Array.isArray(jobs) ? jobs : (jobs.jobs || []);
                cleanupJob = list.find(j => j.job_id === 'cache_evictor') || null;
            }
        } catch (_) { /* the panel still works without it */ }

        const problems = [];
        if (s.junk_entities > 0) {
            problems.push(`<li><strong>${s.junk_entities.toLocaleString()} junk entries</strong> —
                cached rows whose name came back empty or as a placeholder like
                "Unknown Artist". They take up space and can never match anything.</li>`);
        }
        if (s.stale_mb_nulls > 0) {
            problems.push(`<li><strong>${s.stale_mb_nulls.toLocaleString()} failed MusicBrainz lookups</strong> —
                searches that found nothing, remembered so they are not retried
                constantly. Ones older than 30 days are dropped so they get another chance.</li>`);
        }

        const jobOff = cleanupJob && cleanupJob.enabled === false;
        const explain = healthScore === 'healthy' ? '' : `
            <div class="cache-health-explain ${jobOff ? 'warn' : ''}">
                <div class="cache-health-explain-title">What this means</div>
                <ul class="cache-health-explain-list">${problems.join('')}</ul>
                <div class="cache-health-explain-fix">
                    ${jobOff
                ? `<strong>Cache Maintenance is switched off</strong>, so nothing is clearing these.
                       Turn it back on under Operations \u203A Jobs, or clean up once now.`
                : `<strong>Cache Maintenance</strong> clears all of this automatically${
                    cleanupJob && cleanupJob.interval_hours ? ` every ${cleanupJob.interval_hours}h` : ''}${
                    cleanupJob && cleanupJob.last_run ? `, last run ${_cacheAgoText(cleanupJob.last_run)}` : ''}.
                       Nothing is required from you \u2014 run it now if you would rather not wait.`}
                </div>
                <button class="watch-all-btn watch-all-btn-primary" id="cache-cleanup-now">Clean up now</button>
                <div class="cache-health-explain-note" id="cache-cleanup-note"></div>
            </div>`;

        body.innerHTML = `
            <div class="cache-health-status ${healthScore}">
                <div class="cache-health-status-icon">${healthEmoji}</div>
                <div class="cache-health-status-text">${healthLabel}</div>
            </div>
            ${explain}

            <div class="cache-health-cards">
                <div class="cache-health-card">
                    <div class="cache-health-card-value">${s.total_entities.toLocaleString()}</div>
                    <div class="cache-health-card-label">Total Entities</div>
                </div>
                <div class="cache-health-card">
                    <div class="cache-health-card-value">${s.total_searches.toLocaleString()}</div>
                    <div class="cache-health-card-label">Search Results</div>
                </div>
                <div class="cache-health-card">
                    <div class="cache-health-card-value ${s.junk_entities > 0 ? 'warn' : ''}">${s.junk_entities}</div>
                    <div class="cache-health-card-label">Junk Entries</div>
                </div>
                <div class="cache-health-card ${s.stale_mb_nulls > 0 ? 'clickable' : ''}" ${s.stale_mb_nulls > 0 ? 'onclick="openFailedMBLookupsModal()"' : ''}>
                    <div class="cache-health-card-value ${s.stale_mb_nulls > 10 ? 'warn' : ''}">${s.stale_mb_nulls}</div>
                    <div class="cache-health-card-label">Failed MB Lookups</div>
                    ${s.stale_mb_nulls > 0 ? '<div class="cache-health-card-action">Manage ›</div>' : ''}
                </div>
            </div>

            <div class="cache-health-section">
                <div class="cache-health-section-title">By Source</div>
                <div class="cache-health-source-bars">
                    ${(() => {
                const allSources = { ...(s.by_source || {}) };
                if (s.total_musicbrainz) allSources['musicbrainz'] = s.total_musicbrainz;
                const maxCount = Math.max(...Object.values(allSources), 1);
                return Object.entries(allSources).map(([src, count]) => {
                    const pct = Math.round(count / maxCount * 100);
                    const color = src === 'spotify' ? '#1DB954' : src === 'itunes' ? '#FC3C44' : src === 'deezer' ? '#A238FF' : src === 'musicbrainz' ? '#BA478F' : '#666';
                    return `<div class="cache-health-source-row">
                                <span class="cache-health-source-name">${src === 'musicbrainz' ? 'MusicBrainz' : src}</span>
                                <div class="cache-health-source-track"><div class="cache-health-source-fill" style="width:${pct}%;background:${color}"></div></div>
                                <span class="cache-health-source-count">${count.toLocaleString()}</span>
                            </div>`;
                }).join('');
            })()}
                </div>
            </div>

            <div class="cache-health-section">
                <div class="cache-health-section-title">By Type</div>
                <div class="cache-health-type-pills">
                    ${Object.entries(s.by_type || {}).map(([type, count]) => `<span class="cache-health-pill">${type}s <strong>${count.toLocaleString()}</strong></span>`).join('')}
                </div>
            </div>

            <div class="cache-health-section">
                <div class="cache-health-section-title">Metrics</div>
                <div class="cache-health-metrics">
                    <div class="cache-health-metric"><span class="cache-health-metric-label">Average Age</span><span class="cache-health-metric-value">${s.avg_age_days} days</span></div>
                    <div class="cache-health-metric"><span class="cache-health-metric-label">Total Cache Hits</span><span class="cache-health-metric-value">${s.total_access_hits.toLocaleString()}</span></div>
                    <div class="cache-health-metric"><span class="cache-health-metric-label">Expiring in 24h</span><span class="cache-health-metric-value">${s.expiring_24h}</span></div>
                    <div class="cache-health-metric"><span class="cache-health-metric-label">Expiring in 7 days</span><span class="cache-health-metric-value">${s.expiring_7d}</span></div>
                </div>
            </div>
        `;
        const cleanupBtn = overlay.querySelector('#cache-cleanup-now');
        if (cleanupBtn) {
            cleanupBtn.addEventListener('click', async () => {
                const note = overlay.querySelector('#cache-cleanup-note');
                cleanupBtn.disabled = true;
                cleanupBtn.textContent = 'Cleaning\u2026';
                try {
                    // respect_enabled is deliberately NOT sent: this is an
                    // explicit click, and refusing it because the schedule is
                    // off would be the least helpful possible answer.
                    const r = await fetch('/api/repair/jobs/cache_evictor/run', { method: 'POST' });
                    if (!r.ok) throw new Error('HTTP ' + r.status);
                    cleanupBtn.textContent = 'Cleanup started';
                    if (note) note.textContent = 'Running in the background. Reopen this panel in a moment to see the new numbers.';
                } catch (e) {
                    cleanupBtn.disabled = false;
                    cleanupBtn.textContent = 'Clean up now';
                    if (note) note.textContent = 'Could not start it: ' + e.message;
                }
            });
        }
    } catch (error) {
        const body = overlay.querySelector('.cache-health-body');
        body.innerHTML = '<div class="cache-health-loading">Failed to load cache stats</div>';
    }
}

/** "3 hours ago" for a job's last run. Bare timestamps make a reader do the
 *  subtraction, which is the whole question they are asking. */
function _cacheAgoText(value) {
    try {
        const then = new Date(String(value).replace(' ', 'T'));
        const mins = Math.floor((Date.now() - then.getTime()) / 60000);
        if (!isFinite(mins) || mins < 0) return 'recently';
        if (mins < 2) return 'just now';
        if (mins < 60) return mins + ' minutes ago';
        const hrs = Math.floor(mins / 60);
        if (hrs < 24) return hrs === 1 ? 'an hour ago' : hrs + ' hours ago';
        const days = Math.floor(hrs / 24);
        return days === 1 ? 'yesterday' : days + ' days ago';
    } catch (_) {
        return 'recently';
    }
}

// ── Failed MB Lookups Management Modal ──
let _failedMBState = { items: [], total: 0, page: 1, filter: '', typeFilter: '', typeCounts: {} };

async function openFailedMBLookupsModal() {
    if (document.getElementById('failed-mb-modal-overlay')) return;

    const overlay = document.createElement('div');
    overlay.id = 'failed-mb-modal-overlay';
    overlay.className = 'modal-overlay';
    overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };

    overlay.innerHTML = `
        <div class="failed-mb-modal">
            <div class="failed-mb-header">
                <div>
                    <h2 class="failed-mb-title">Failed MusicBrainz Lookups</h2>
                    <p class="failed-mb-subtitle">Tracks, albums, and artists that couldn't be matched automatically</p>
                </div>
                <button class="watch-all-close" onclick="document.getElementById('failed-mb-modal-overlay').remove()">&times;</button>
            </div>
            <div class="failed-mb-toolbar">
                <div class="failed-mb-tabs" id="failed-mb-tabs"></div>
                <div class="failed-mb-search-row">
                    <input type="text" id="failed-mb-search" class="failed-mb-search-input" placeholder="Filter by name...">
                    <button class="failed-mb-btn failed-mb-btn-danger" onclick="_failedMBClearAll()">Clear All Failed</button>
                </div>
            </div>
            <div class="failed-mb-body" id="failed-mb-body">
                <div class="cache-health-loading"><div class="watch-all-loading-spinner"></div><div>Loading...</div></div>
            </div>
            <div class="failed-mb-footer" id="failed-mb-footer"></div>
        </div>
    `;
    document.body.appendChild(overlay);

    // Search debounce
    const searchInput = overlay.querySelector('#failed-mb-search');
    let searchTimer = null;
    searchInput.addEventListener('input', () => {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(() => {
            _failedMBState.filter = searchInput.value;
            _failedMBState.page = 1;
            _loadFailedMBLookups();
        }, 300);
    });

    _failedMBState = { items: [], total: 0, page: 1, filter: '', typeFilter: '', typeCounts: {} };
    await _loadFailedMBLookups();
}

async function _loadFailedMBLookups() {
    const body = document.getElementById('failed-mb-body');
    if (!body) return;

    // Only fetch type_counts on first load — cache them for tab switches
    const needCounts = Object.keys(_failedMBState.typeCounts).length === 0;
    const params = new URLSearchParams({
        page: _failedMBState.page,
        limit: 50,
    });
    if (needCounts) params.set('counts', 'true');
    if (_failedMBState.typeFilter) params.set('entity_type', _failedMBState.typeFilter);
    if (_failedMBState.filter) params.set('search', _failedMBState.filter);

    try {
        const resp = await fetch(`/api/metadata-cache/failed-mb-lookups?${params}`);
        if (!resp.ok) throw new Error('Failed to load');
        const data = await resp.json();
        _failedMBState.items = data.items;
        _failedMBState.total = data.total;
        if (data.type_counts) _failedMBState.typeCounts = data.type_counts;

        // Render type filter tabs
        const tabsEl = document.getElementById('failed-mb-tabs');
        if (tabsEl) {
            const allCount = Object.values(_failedMBState.typeCounts).reduce((a, b) => a + b, 0);
            let tabsHTML = `<button class="failed-mb-tab ${!_failedMBState.typeFilter ? 'active' : ''}" onclick="_failedMBSetType('')">All (${allCount})</button>`;
            const typeLabels = { artist: 'Artists', release: 'Albums', recording: 'Tracks' };
            for (const [type, count] of Object.entries(_failedMBState.typeCounts)) {
                tabsHTML += `<button class="failed-mb-tab ${_failedMBState.typeFilter === type ? 'active' : ''}" onclick="_failedMBSetType('${type}')">${typeLabels[type] || type} (${count})</button>`;
            }
            tabsEl.innerHTML = tabsHTML;
        }

        // Render items
        if (data.items.length === 0) {
            body.innerHTML = `<div class="failed-mb-empty">${_failedMBState.filter ? 'No matches for your search' : 'No failed lookups — cache is clean!'}</div>`;
        } else {
            const typeIcons = { artist: '🎤', release: '💿', recording: '🎵' };
            body.innerHTML = data.items.map(item => `
                <div class="failed-mb-item" data-id="${item.id}">
                    <div class="failed-mb-item-icon">${typeIcons[item.entity_type] || '?'}</div>
                    <div class="failed-mb-item-info">
                        <div class="failed-mb-item-name">${escapeHtml(item.entity_name)}</div>
                        ${item.artist_name ? `<div class="failed-mb-item-artist">${escapeHtml(item.artist_name)}</div>` : ''}
                    </div>
                    <div class="failed-mb-item-meta">
                        <span class="failed-mb-item-type">${item.entity_type}</span>
                        <span class="failed-mb-item-date">${item.last_updated ? new Date(item.last_updated).toLocaleDateString() : ''}</span>
                    </div>
                    <div class="failed-mb-item-actions">
                        <button class="failed-mb-btn-sm failed-mb-btn-primary" onclick="_failedMBSearch(${item.id}, '${item.entity_type}', '${escapeForInlineJs(item.entity_name)}', '${escapeForInlineJs(item.artist_name || '')}')">Search MB</button>
                        <button class="failed-mb-btn-sm failed-mb-btn-ghost" onclick="_failedMBDelete(${item.id})">Remove</button>
                    </div>
                </div>
            `).join('');
        }

        // Pagination footer
        const footer = document.getElementById('failed-mb-footer');
        if (footer) {
            const totalPages = Math.ceil(data.total / 50);
            footer.innerHTML = totalPages > 1 ? `
                <div class="failed-mb-pagination">
                    <button class="failed-mb-btn-sm" ${_failedMBState.page <= 1 ? 'disabled' : ''} onclick="_failedMBPage(${_failedMBState.page - 1})">Prev</button>
                    <span>Page ${_failedMBState.page} of ${totalPages} (${data.total} total)</span>
                    <button class="failed-mb-btn-sm" ${_failedMBState.page >= totalPages ? 'disabled' : ''} onclick="_failedMBPage(${_failedMBState.page + 1})">Next</button>
                </div>
            ` : `<div class="failed-mb-pagination"><span>${data.total} entries</span></div>`;
        }
    } catch (err) {
        body.innerHTML = '<div class="failed-mb-empty">Failed to load data</div>';
    }
}

function _failedMBSetType(type) {
    _failedMBState.typeFilter = type;
    _failedMBState.page = 1;
    _loadFailedMBLookups();
}

function _failedMBPage(page) {
    _failedMBState.page = page;
    _loadFailedMBLookups();
}

async function _failedMBDelete(entryId) {
    try {
        const resp = await fetch(`/api/metadata-cache/mb-entry/${entryId}`, { method: 'DELETE' });
        if (resp.ok) {
            const row = document.querySelector(`.failed-mb-item[data-id="${entryId}"]`);
            if (row) {
                row.style.opacity = '0';
                setTimeout(() => {
                    row.remove();
                    _failedMBState.typeCounts = {};  // Force refresh counts
                    _loadFailedMBLookups();
                }, 200);
            }
        }
    } catch (err) {
        showToast('Failed to delete entry', 'error');
    }
}

async function _failedMBClearAll() {
    if (!await showConfirmDialog({
        title: 'Clear Failed Lookups',
        message: `Clear all ${_failedMBState.total} failed lookups? They will be retried on the next enrichment run.`,
        confirmText: 'Clear All',
        destructive: true
    })) return;
    try {
        const resp = await fetch('/api/metadata-cache/clear-musicbrainz?failed_only=true', { method: 'DELETE' });
        const data = await resp.json();
        if (data.success) {
            showToast(`Cleared ${data.cleared} failed lookups`, 'success');
            _failedMBState.page = 1;
            _failedMBState.typeCounts = {};  // Force refresh counts
            _loadFailedMBLookups();
        }
    } catch (err) {
        showToast('Failed to clear lookups', 'error');
    }
}

// ── MusicBrainz Search Sub-Modal ──
async function _failedMBSearch(entryId, entityType, entityName, artistName) {
    // Remove existing search modal if any
    const existing = document.getElementById('mb-search-modal-overlay');
    if (existing) existing.remove();

    const overlay = document.createElement('div');
    overlay.id = 'mb-search-modal-overlay';
    overlay.className = 'modal-overlay';
    overlay.style.zIndex = '10001';
    overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };

    const typeLabels = { artist: 'Artist', release: 'Album', recording: 'Track' };
    overlay.innerHTML = `
        <div class="mb-search-modal">
            <div class="mb-search-header">
                <div>
                    <h2 class="mb-search-title">Search MusicBrainz</h2>
                    <p class="mb-search-subtitle">Find a match for: <strong>${escapeHtml(entityName)}</strong>${artistName ? ` by ${escapeHtml(artistName)}` : ''}</p>
                </div>
                <button class="watch-all-close" onclick="document.getElementById('mb-search-modal-overlay').remove()">&times;</button>
            </div>
            <div class="mb-search-inputs">
                <div class="mb-search-input-row">
                    <label>Type</label>
                    <select id="mb-search-type" class="mb-search-select">
                        <option value="artist" ${entityType === 'artist' ? 'selected' : ''}>Artist</option>
                        <option value="release" ${entityType === 'release' ? 'selected' : ''}>Album / Release</option>
                        <option value="recording" ${entityType === 'recording' ? 'selected' : ''}>Track / Recording</option>
                    </select>
                </div>
                <div class="mb-search-input-row">
                    <label>Name</label>
                    <input type="text" id="mb-search-query" class="mb-search-input" value="${escapeHtml(entityName)}">
                </div>
                <div class="mb-search-input-row" id="mb-search-artist-row" ${entityType === 'artist' ? 'style="display:none"' : ''}>
                    <label>Artist</label>
                    <input type="text" id="mb-search-artist" class="mb-search-input" value="${escapeHtml(artistName)}">
                </div>
                <button class="failed-mb-btn failed-mb-btn-primary" id="mb-search-go-btn" onclick="_runMBSearch(${entryId})">Search</button>
            </div>
            <div class="mb-search-results" id="mb-search-results">
                <div class="failed-mb-empty">Enter a search query and click Search</div>
            </div>
        </div>
    `;
    document.body.appendChild(overlay);

    // Toggle artist row visibility based on type
    const typeSelect = overlay.querySelector('#mb-search-type');
    typeSelect.addEventListener('change', () => {
        const artistRow = overlay.querySelector('#mb-search-artist-row');
        artistRow.style.display = typeSelect.value === 'artist' ? 'none' : '';
    });

    // Enter to search
    overlay.querySelectorAll('.mb-search-input').forEach(input => {
        input.addEventListener('keydown', (e) => { if (e.key === 'Enter') _runMBSearch(entryId); });
    });

    // Auto-search on open
    _runMBSearch(entryId);
}

async function _runMBSearch(entryId) {
    const resultsEl = document.getElementById('mb-search-results');
    const typeEl = document.getElementById('mb-search-type');
    const queryEl = document.getElementById('mb-search-query');
    const artistEl = document.getElementById('mb-search-artist');
    const goBtn = document.getElementById('mb-search-go-btn');
    if (!resultsEl || !queryEl) return;

    const type = typeEl.value;
    const query = queryEl.value.trim();
    const artist = artistEl ? artistEl.value.trim() : '';
    if (!query) return;

    goBtn.disabled = true;
    goBtn.textContent = 'Searching...';
    resultsEl.innerHTML = '<div class="cache-health-loading"><div class="watch-all-loading-spinner"></div><div>Searching MusicBrainz...</div></div>';

    try {
        const params = new URLSearchParams({ type, q: query, limit: 10 });
        if (artist && type !== 'artist') params.set('artist', artist);

        const resp = await fetch(`/api/musicbrainz/search?${params}`);
        if (!resp.ok) throw new Error('Search failed');
        const data = await resp.json();

        if (!data.results || data.results.length === 0) {
            resultsEl.innerHTML = '<div class="failed-mb-empty">No results found. Try adjusting your search.</div>';
            return;
        }

        resultsEl.innerHTML = data.results.map((r, i) => {
            const scoreColor = r.score >= 90 ? '#4ade80' : r.score >= 70 ? '#fbbf24' : '#f87171';
            let detail = '';
            if (type === 'release') detail = [r.artist, r.date, r.track_count ? `${r.track_count} tracks` : ''].filter(Boolean).join(' · ');
            else if (type === 'recording') detail = [r.artist, r.album].filter(Boolean).join(' · ');
            else detail = [r.type, r.country].filter(Boolean).join(' · ');

            return `
                <div class="mb-search-result" onclick="_selectMBMatch(${entryId}, '${r.mbid}', '${escapeForInlineJs(r.name)}')">
                    <div class="mb-search-result-score" style="color:${scoreColor}">${r.score}%</div>
                    <div class="mb-search-result-info">
                        <div class="mb-search-result-name">${escapeHtml(r.name)}</div>
                        ${r.disambiguation ? `<div class="mb-search-result-disambig">${escapeHtml(r.disambiguation)}</div>` : ''}
                        ${detail ? `<div class="mb-search-result-detail">${escapeHtml(detail)}</div>` : ''}
                    </div>
                    <div class="mb-search-result-mbid" title="${r.mbid}">${r.mbid.substring(0, 8)}...</div>
                </div>
            `;
        }).join('');
    } catch (err) {
        resultsEl.innerHTML = `<div class="failed-mb-empty">Search error: ${err.message}</div>`;
    } finally {
        goBtn.disabled = false;
        goBtn.textContent = 'Search';
    }
}

async function _selectMBMatch(entryId, mbid, mbName) {
    try {
        const resp = await fetch('/api/metadata-cache/mb-match', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ entry_id: entryId, mbid, mb_name: mbName })
        });
        const data = await resp.json();
        if (data.success) {
            showToast(`Matched to: ${mbName}`, 'success');
            // Close search modal, refresh list with fresh counts
            const searchOverlay = document.getElementById('mb-search-modal-overlay');
            if (searchOverlay) searchOverlay.remove();
            _failedMBState.typeCounts = {};
            _loadFailedMBLookups();
        } else {
            showToast(data.error || 'Failed to save match', 'error');
        }
    } catch (err) {
        showToast('Failed to save match', 'error');
    }
}

// ===================================================================
