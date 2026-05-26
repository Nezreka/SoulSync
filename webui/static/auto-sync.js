// Auto-Sync: schedule board + mirrored-playlist pipeline runs
// ─────────────────────────────────────────────────────────────────────
// Extracted from stats-automations.js (Cin review feedback). All
// references rely on globals available at runtime — `_esc`, `_escAttr`,
// `_autoParseUTC`, `_autoFormatTrigger`, `showToast`, `showConfirmDialog`,
// `loadMirroredPlaylists`, `updateMirroredCardPhase`,
// `openMirroredPlaylistModal`, `closeMirroredModal`, `youtubePlaylistStates`
// all live in stats-automations.js (or earlier helpers). This file
// declares the auto-sync-specific state + render/event functions on top.

const mirroredPipelinePollers = {};
const AUTO_SYNC_BUCKETS = [1, 2, 4, 8, 12, 16, 24, 48, 72, 168];
let _autoSyncStatusPoller = null;
let _autoSyncIsDragging = false;
let _autoSyncScheduleState = {
    playlists: [],
    automations: [],
    playlistSchedules: {},
    automationPipelines: [],
    runHistory: [],
    runHistoryTotal: 0,
};
let _autoSyncActiveTab = 'schedule';
let _autoSyncSidebarFilter = '';
let _autoSyncHistoryFilter = 'all';  // 'all' | 'error' | 'completed' | 'skipped'
let _autoSyncHistoryLimit = 50;

function getMirroredSourceRef(p) {
    if (p && p.source_ref) return String(p.source_ref);
    const desc = (p && p.description) ? String(p.description).trim() : '';
    if ((p.source === 'spotify_public' || p.source === 'youtube') && /^https?:\/\//i.test(desc)) {
        return desc;
    }
    return (p && p.source_playlist_id) ? String(p.source_playlist_id) : '';
}

function autoSyncTriggerForHours(hours) {
    const h = parseInt(hours, 10) || 24;
    if (h >= 24 && h % 24 === 0) {
        return { interval: h / 24, unit: 'days' };
    }
    return { interval: h, unit: 'hours' };
}

function autoSyncHoursFromTrigger(config) {
    const interval = parseInt(config?.interval, 10) || 0;
    const unit = config?.unit || 'hours';
    if (!interval) return null;
    if (unit === 'minutes') return Math.max(1, Math.round(interval / 60));
    if (unit === 'days') return interval * 24;
    if (unit === 'weeks') return interval * 168;
    return interval;
}

function autoSyncBucketLabel(hours) {
    if (hours === 168) return 'Weekly';
    if (hours >= 24) return `${hours / 24}d`;
    return `${hours}h`;
}

function autoSyncIntervalLabel(hours) {
    if (hours === 168) return 'Every week';
    if (hours >= 24) {
        const days = hours / 24;
        return `Every ${days} day${days === 1 ? '' : 's'}`;
    }
    return `Every ${hours} hour${hours === 1 ? '' : 's'}`;
}

function autoSyncSourceLabel(source) {
    const labels = {
        spotify: 'Spotify',
        spotify_public: 'Spotify Link',
        tidal: 'Tidal',
        youtube: 'YouTube',
        deezer: 'Deezer',
        qobuz: 'Qobuz',
        beatport: 'Beatport',
        file: 'File Imports',
        itunes_link: 'iTunes Link',
        listenbrainz: 'ListenBrainz',
        lastfm: 'Last.fm Radio',
        soulsync_discovery: 'SoulSync Discovery',
    };
    return labels[source] || source || 'Other';
}

function autoSyncCanSchedulePlaylist(playlist) {
    return playlist && !['file', 'beatport'].includes(playlist.source || '');
}

function autoSyncIsPipelineAutomation(auto) {
    return auto && auto.action_type === 'playlist_pipeline';
}

function autoSyncPlaylistIdFromAutomation(auto) {
    if (!autoSyncIsPipelineAutomation(auto)) return null;
    const cfg = auto.action_config || {};
    if (cfg.all === true || cfg.all === 'true') return null;
    const raw = cfg.playlist_id;
    if (raw === undefined || raw === null || raw === '') return null;
    const id = parseInt(raw, 10);
    return Number.isFinite(id) ? id : null;
}

function autoSyncIsScheduleOwned(auto) {
    // Primary signal: the explicit owned_by flag the board writes on every
    // schedule it creates. Falls back to the legacy name/group convention
    // so rows created before the column existed (or hand-edited from the
    // Automations page) still get recognized after backfill.
    if (auto?.owned_by === 'auto_sync') return true;
    const group = auto?.group_name || '';
    const name = auto?.name || '';
    return group === 'Playlist Auto-Sync' || name.startsWith('Auto-Sync:');
}

function buildAutoSyncScheduleState(playlists, automations, historyData = {}) {
    const playlistSchedules = {};
    const automationPipelines = [];
    const pipelineAutomations = automations.filter(autoSyncIsPipelineAutomation);
    pipelineAutomations.forEach(auto => {
        const playlistId = autoSyncPlaylistIdFromAutomation(auto);
        const hours = auto.trigger_type === 'schedule' ? autoSyncHoursFromTrigger(auto.trigger_config || {}) : null;
        if (playlistId && hours && autoSyncIsScheduleOwned(auto)) {
            playlistSchedules[playlistId] = {
                automation_id: auto.id,
                automation_name: auto.name,
                hours,
                enabled: auto.enabled !== false && auto.enabled !== 0,
                owned: true,
                next_run: auto.next_run,
                trigger_config: auto.trigger_config || {},
            };
        } else {
            automationPipelines.push(auto);
        }
    });
    return {
        playlists,
        automations,
        playlistSchedules,
        automationPipelines,
        runHistory: historyData.history || [],
        runHistoryTotal: historyData.total || 0,
    };
}

async function openAutoSyncScheduleModal() {
    let overlay = document.getElementById('auto-sync-schedule-modal');
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = 'auto-sync-schedule-modal';
        overlay.className = 'auto-sync-overlay';
        document.body.appendChild(overlay);
    }
    overlay.innerHTML = `
        <div class="auto-sync-modal">
            <div class="auto-sync-header">
                <div>
                    <h3>Auto-Sync Schedule</h3>
                    <p>Drop mirrored playlists onto an interval to schedule refresh, discovery, sync, and wishlist processing.</p>
                </div>
                <button class="auto-sync-close" onclick="closeAutoSyncScheduleModal()">&times;</button>
            </div>
            <div class="auto-sync-loading">Loading schedule...</div>
        </div>
    `;
    overlay.style.display = 'flex';
    overlay.onclick = e => { if (e.target === overlay) closeAutoSyncScheduleModal(); };
    await refreshAutoSyncScheduleModal();
}

function closeAutoSyncScheduleModal() {
    const overlay = document.getElementById('auto-sync-schedule-modal');
    stopAutoSyncStatusPolling();
    if (overlay) overlay.remove();
}

async function refreshAutoSyncScheduleModal() {
    const overlay = document.getElementById('auto-sync-schedule-modal');
    if (!overlay) return;
    try {
        const [playlistRes, automationRes, historyRes] = await Promise.all([
            fetch('/api/mirrored-playlists'),
            fetch('/api/automations'),
            fetch(`/api/playlist-pipeline/history?limit=${_autoSyncHistoryLimit}`),
        ]);
        const playlists = await playlistRes.json();
        const automations = await automationRes.json();
        const historyData = await historyRes.json();
        if (!playlistRes.ok || playlists.error) throw new Error(playlists.error || 'Failed to load mirrored playlists');
        if (!automationRes.ok || automations.error) throw new Error(automations.error || 'Failed to load automations');
        if (!historyRes.ok || historyData.error) throw new Error(historyData.error || 'Failed to load pipeline run history');
        _autoSyncScheduleState = buildAutoSyncScheduleState(playlists, automations, historyData);
        renderAutoSyncScheduleModal();
        manageAutoSyncStatusPolling();
    } catch (err) {
        overlay.innerHTML = `
            <div class="auto-sync-modal">
                <div class="auto-sync-header">
                    <div><h3>Auto-Sync Schedule</h3><p>Could not load schedule data.</p></div>
                    <button class="auto-sync-close" onclick="closeAutoSyncScheduleModal()">&times;</button>
                </div>
                <div class="auto-sync-error">${_esc((err && err.message) || String(err))}</div>
            </div>
        `;
    }
}

function renderAutoSyncScheduleModal() {
    const overlay = document.getElementById('auto-sync-schedule-modal');
    if (!overlay) return;

    const { playlists, playlistSchedules, automationPipelines, runHistory, runHistoryTotal } = _autoSyncScheduleState;
    const scheduledCount = Object.keys(playlistSchedules).length;
    const enabledCount = Object.values(playlistSchedules).filter(s => s.enabled).length;
    const pipelineCount = automationPipelines.length;
    const totalTracks = playlists.reduce((sum, p) => sum + (parseInt(p.track_count, 10) || 0), 0);
    const scheduleActive = _autoSyncActiveTab === 'schedule';
    const automationsActive = _autoSyncActiveTab === 'automations';
    const historyActive = _autoSyncActiveTab === 'history';

    const schedulePanel = renderAutoSyncSchedulePanel(playlists, playlistSchedules);
    const automationPanel = renderAutoSyncAutomationPanel(automationPipelines, playlists);
    const historyPanel = renderAutoSyncHistoryPanel(runHistory, runHistoryTotal);
    const monitor = renderAutoSyncPipelineMonitor(playlists);

    overlay.innerHTML = `
        <div class="auto-sync-modal">
            <div class="auto-sync-header">
                <div>
                    <div class="auto-sync-eyebrow">Playlist automation</div>
                    <h3>Auto-Sync Manager</h3>
                    <p>Schedule mirrored playlists through the same playlist-pipeline engine used by Automations.</p>
                </div>
                <button class="auto-sync-close" onclick="closeAutoSyncScheduleModal()">&times;</button>
            </div>
            <div class="auto-sync-summary">
                <div><span>${scheduledCount}</span><small>scheduled playlists</small></div>
                <div><span>${enabledCount}</span><small>active schedules</small></div>
                <div><span>${pipelineCount}</span><small>automation pipelines</small></div>
                <div><span>${totalTracks}</span><small>mirrored tracks</small></div>
            </div>
            ${monitor}
            <div class="auto-sync-tabs">
                <button class="${scheduleActive ? 'active' : ''}" onclick="setAutoSyncTab('schedule')">Schedule Board</button>
                <button class="${automationsActive ? 'active' : ''}" onclick="setAutoSyncTab('automations')">Automation Pipelines</button>
                <button class="${historyActive ? 'active' : ''}" onclick="setAutoSyncTab('history')">
                    Run History
                    ${(() => {
                        const errs = (runHistory || []).filter(h => h.status === 'error' || h.status === 'skipped').length;
                        return errs ? `<span class="auto-sync-tab-badge error">${errs}</span>` : '';
                    })()}
                </button>
            </div>
            <div class="auto-sync-tab-panel ${scheduleActive ? 'active' : ''}" id="auto-sync-schedule-panel">${schedulePanel}</div>
            <div class="auto-sync-tab-panel ${automationsActive ? 'active' : ''}" id="auto-sync-automation-panel">${automationPanel}</div>
            <div class="auto-sync-tab-panel ${historyActive ? 'active' : ''}" id="auto-sync-history-panel">${historyPanel}</div>
        </div>
    `;
    populateAutoSyncHistoryList(overlay);
    bindAutoSyncHistoryCardInteractions(overlay);
}

function setAutoSyncTab(tab) {
    _autoSyncActiveTab = ['automations', 'history'].includes(tab) ? tab : 'schedule';
    renderAutoSyncScheduleModal();
}

function renderAutoSyncSchedulePanel(playlists, playlistSchedules) {
    const filter = (_autoSyncSidebarFilter || '').trim().toLowerCase();
    const matchesFilter = (p) => !filter || (p.name || '').toLowerCase().includes(filter)
        || autoSyncSourceLabel(p.source || '').toLowerCase().includes(filter);
    const schedulablePlaylists = playlists.filter(p => autoSyncCanSchedulePlaylist(p) && matchesFilter(p));
    const unavailablePlaylists = playlists.filter(p => !autoSyncCanSchedulePlaylist(p) && matchesFilter(p));
    const grouped = schedulablePlaylists.reduce((acc, p) => {
        const key = p.source || 'other';
        if (!acc[key]) acc[key] = [];
        acc[key].push(p);
        return acc;
    }, {});
    const sourceKeys = Object.keys(grouped).sort((a, b) => autoSyncSourceLabel(a).localeCompare(autoSyncSourceLabel(b)));

    const sidebarHtml = sourceKeys.length ? sourceKeys.map(source => `
        <div class="auto-sync-source-group">
            <div class="auto-sync-source-group-head">
                <span class="auto-sync-source-title">${_esc(autoSyncSourceLabel(source))}</span>
                <button type="button" class="auto-sync-source-bulk-btn"
                        onclick="event.stopPropagation(); openAutoSyncBulkMenu(event, '${_escAttr(source)}')"
                        title="Schedule all ${_escAttr(autoSyncSourceLabel(source))} playlists at the same interval">
                    Bulk
                </button>
            </div>
            ${grouped[source].map(p => {
                const schedule = playlistSchedules[p.id];
                const assigned = schedule ? autoSyncIntervalLabel(schedule.hours) : 'Unscheduled';
                return `
                    <div class="auto-sync-playlist ${schedule ? 'scheduled' : ''}" draggable="true" data-playlist-id="${p.id}" ondragstart="autoSyncDragStart(event)" ondragend="autoSyncDragEnd()">
                        <div class="auto-sync-playlist-name">${_esc(p.name)}</div>
                        <div class="auto-sync-playlist-meta">${p.track_count || 0} tracks &middot; ${_esc(assigned)}</div>
                    </div>
                `;
            }).join('')}
        </div>
    `).join('') : '<div class="auto-sync-empty">No refreshable mirrored playlists yet.</div>';

    const unavailableHtml = unavailablePlaylists.length ? `
        <div class="auto-sync-source-group auto-sync-source-group-disabled">
            <div class="auto-sync-source-title">Not schedulable</div>
            ${unavailablePlaylists.map(p => `
                <div class="auto-sync-playlist unavailable">
                    <div class="auto-sync-playlist-name">${_esc(p.name)}</div>
                    <div class="auto-sync-playlist-meta">${_esc(autoSyncSourceLabel(p.source))} &middot; refresh not supported</div>
                </div>
            `).join('')}
        </div>
    ` : '';

    // Merge standard buckets with any custom intervals that are already in
    // use, so a 6h or 36h schedule (created via Automations page or the
    // custom-interval prompt) still renders as its own column instead of
    // disappearing from the board.
    const customHours = Object.values(playlistSchedules)
        .map(s => parseInt(s?.hours, 10))
        .filter(h => Number.isFinite(h) && h > 0 && !AUTO_SYNC_BUCKETS.includes(h));
    const allBuckets = [...new Set([...AUTO_SYNC_BUCKETS, ...customHours])].sort((a, b) => a - b);
    const bucketHtml = allBuckets.map(hours => {
        const assigned = schedulablePlaylists.filter(p => playlistSchedules[p.id]?.hours === hours);
        const isCustom = !AUTO_SYNC_BUCKETS.includes(hours);
        return `
            <div class="auto-sync-column ${isCustom ? 'custom' : ''}" data-hours="${hours}" ondragover="autoSyncDragOver(event)" ondragleave="autoSyncDragLeave(event)" ondrop="autoSyncDrop(event, ${hours})">
                <div class="auto-sync-column-head">
                    <span>${autoSyncBucketLabel(hours)}${isCustom ? ' <em>custom</em>' : ''}</span>
                    <small>${assigned.length} playlist${assigned.length === 1 ? '' : 's'}</small>
                </div>
                <div class="auto-sync-column-list">
                    ${assigned.length ? assigned.map(p => autoSyncScheduledCardHtml(p, playlistSchedules[p.id])).join('') : '<div class="auto-sync-drop-hint"><strong>Drop here</strong><span>Schedule playlists at this interval</span></div>'}
                </div>
            </div>
        `;
    }).join('');

    const filterValue = _esc(_autoSyncSidebarFilter || '');
    return `
        <div class="auto-sync-board-intro">
            <div>
                <strong>Drag playlists into an interval</strong>
                <span>Each placement creates or updates an Auto-Sync-owned playlist-pipeline automation.</span>
            </div>
            <button onclick="refreshAutoSyncScheduleModal()">Refresh</button>
        </div>
            <div class="auto-sync-body">
                <aside class="auto-sync-sidebar">
                    <div class="auto-sync-sidebar-title">Mirrored playlists</div>
                    <div class="auto-sync-sidebar-filter">
                        <input type="search" class="auto-sync-sidebar-search" placeholder="Filter playlists…"
                               value="${filterValue}" oninput="setAutoSyncSidebarFilter(this.value)" />
                        ${_autoSyncSidebarFilter ? `<button type="button" class="auto-sync-sidebar-filter-clear" onclick="setAutoSyncSidebarFilter('')" aria-label="Clear filter">&times;</button>` : ''}
                    </div>
                    <div class="auto-sync-source-list">${sidebarHtml}${unavailableHtml}</div>
                </aside>
                <main class="auto-sync-board">${bucketHtml}</main>
            </div>
    `;
}

function setAutoSyncSidebarFilter(value) {
    _autoSyncSidebarFilter = String(value || '');
    // Only re-render the sidebar/board portion to keep input focus.
    const panel = document.getElementById('auto-sync-schedule-panel');
    if (!panel) return;
    const { playlists, playlistSchedules } = _autoSyncScheduleState;
    panel.innerHTML = renderAutoSyncSchedulePanel(playlists, playlistSchedules);
    // Restore focus to the search input + caret position at end.
    const input = panel.querySelector('.auto-sync-sidebar-search');
    if (input) {
        input.focus();
        try { input.setSelectionRange(value.length, value.length); } catch (_) {}
    }
}

function getAutoSyncPipelinePlaylists(playlists) {
    return playlists
        .map(p => ({ playlist: p, state: p.pipeline_state || null }))
        .filter(item => item.state && item.state.status && item.state.status !== 'idle')
        .sort((a, b) => {
            const aRunning = a.state.status === 'running' ? 1 : 0;
            const bRunning = b.state.status === 'running' ? 1 : 0;
            if (aRunning !== bRunning) return bRunning - aRunning;
            return (b.state.finished_at || b.state.started_at || 0) - (a.state.finished_at || a.state.started_at || 0);
        });
}

function autoSyncPipelineStatusLabel(status) {
    if (status === 'running') return 'Running';
    if (status === 'finished') return 'Completed';
    if (status === 'skipped') return 'Skipped';
    if (status === 'error') return 'Needs attention';
    return 'Idle';
}

function autoSyncPipelineStatusClass(status) {
    if (status === 'running') return 'running';
    if (status === 'finished') return 'finished';
    if (status === 'error' || status === 'skipped') return 'error';
    return 'idle';
}

function renderAutoSyncPipelineMonitor(playlists) {
    const pipelineItems = getAutoSyncPipelinePlaylists(playlists);
    const running = pipelineItems.filter(item => item.state.status === 'running');
    const recent = pipelineItems.filter(item => item.state.status !== 'running').slice(0, 2);
    const visible = [...running, ...recent].slice(0, 4);
    const title = running.length
        ? `${running.length} pipeline${running.length === 1 ? '' : 's'} running`
        : 'No pipelines running';
    const detail = running.length
        ? 'Live status refreshes while this modal is open.'
        : 'Use Run now on a scheduled playlist when you want the pipeline immediately.';

    return `
        <section class="auto-sync-monitor">
            <div class="auto-sync-monitor-head">
                <div>
                    <span class="auto-sync-monitor-kicker">Live pipeline monitor</span>
                    <strong>${_esc(title)}</strong>
                    <small>${_esc(detail)}</small>
                </div>
                <button onclick="refreshAutoSyncScheduleModal()">Refresh</button>
            </div>
            ${visible.length ? `
                <div class="auto-sync-monitor-list">
                    ${visible.map(({ playlist, state }) => autoSyncPipelineMonitorCardHtml(playlist, state)).join('')}
                </div>
            ` : '<div class="auto-sync-monitor-empty"><span>Ready</span><small>Scheduled playlists appear here while the all-in-one pipeline runs.</small></div>'}
        </section>
    `;
}

function autoSyncPipelineMonitorCardHtml(playlist, state) {
    const status = state.status || 'idle';
    const progress = Math.max(0, Math.min(100, parseInt(state.progress, 10) || 0));
    const latest = Array.isArray(state.log) && state.log.length ? state.log[state.log.length - 1].message : '';
    const phase = state.phase || autoSyncPipelineStatusLabel(status);
    return `
        <article class="auto-sync-monitor-card ${autoSyncPipelineStatusClass(status)}">
            <div class="auto-sync-monitor-card-main">
                <div class="auto-sync-monitor-title-row">
                    <strong>${_esc(playlist.name || `Playlist #${playlist.id}`)}</strong>
                    <span>${_esc(autoSyncPipelineStatusLabel(status))}</span>
                </div>
                <div class="auto-sync-monitor-phase">${_esc(phase)}</div>
                <div class="auto-sync-monitor-progress" aria-label="${progress}% complete">
                    <div style="width: ${progress}%"></div>
                </div>
                ${latest ? `<small>${_esc(latest)}</small>` : ''}
            </div>
            <button onclick="event.stopPropagation(); openMirroredPlaylistModal(${playlist.id})">Details</button>
        </article>
    `;
}

function renderAutoSyncAutomationPanel(automationPipelines, playlists) {
    if (!automationPipelines.length) {
        return '<div class="auto-sync-automation-empty">No Automations-page playlist pipelines found.</div>';
    }
    return `
        <div class="auto-sync-automation-intro">
            <strong>Read-only Automations-page pipelines</strong>
            <span>These use the playlist pipeline but are managed from the Automations page, so this modal only displays them.</span>
        </div>
        <div class="auto-sync-automation-list">
            ${automationPipelines.map(auto => autoSyncAutomationCardHtml(auto, playlists)).join('')}
        </div>
    `;
}

function renderAutoSyncHistoryPanel(history, total) {
    if (!history.length) {
        return `
            <div class="auto-sync-history-empty">
                <strong>No playlist pipeline runs yet</strong>
                <span>Future Auto-Sync and playlist pipeline runs will record before/after playlist snapshots here.</span>
            </div>
        `;
    }
    const filter = _autoSyncHistoryFilter || 'all';
    const tabs = [
        ['all', 'All', history.length],
        ['error', 'Errors', history.filter(h => h.status === 'error' || h.status === 'skipped').length],
        ['completed', 'Completed', history.filter(h => h.status === 'completed' || h.status === 'finished').length],
    ];
    const filterTabsHtml = tabs.map(([key, label, count]) => `
        <button class="auto-sync-history-filter-btn ${filter === key ? 'active' : ''} ${key === 'error' && count > 0 ? 'has-errors' : ''}"
                onclick="setAutoSyncHistoryFilter('${key}')" type="button">
            ${_esc(label)} <em>${count}</em>
        </button>
    `).join('');
    const canLoadMore = total > history.length;
    return `
        <div class="auto-sync-history-intro">
            <div>
                <strong>Playlist pipeline run history</strong>
                <span>Each run records what changed on the mirrored playlist before and after refresh, discovery, sync, and wishlist processing.</span>
            </div>
            <div class="auto-sync-history-intro-controls">
                <div class="auto-sync-history-filters">${filterTabsHtml}</div>
                <button onclick="refreshAutoSyncScheduleModal()">Refresh</button>
            </div>
        </div>
        <div class="auto-sync-history-list" data-renderer="pending">
            <div class="auto-sync-history-loading">Preparing run history...</div>
        </div>
        ${canLoadMore ? `
            <div class="auto-sync-history-load-more-row">
                <button type="button" class="auto-sync-history-load-more" onclick="loadMoreAutoSyncHistory()">
                    Load more (${history.length} of ${total})
                </button>
            </div>
        ` : ''}
    `;
}

function setAutoSyncHistoryFilter(key) {
    _autoSyncHistoryFilter = ['error', 'completed'].includes(key) ? key : 'all';
    renderAutoSyncScheduleModal();
}

function loadMoreAutoSyncHistory() {
    _autoSyncHistoryLimit = Math.min(500, _autoSyncHistoryLimit + 50);
    refreshAutoSyncScheduleModal();
}

function openAutoSyncBulkMenu(event, source) {
    // Build a transient popover with all the standard buckets + a "Custom…"
    // entry. Position relative to the button that triggered it.
    closeAutoSyncBulkMenu();
    const anchor = event.currentTarget;
    if (!anchor) return;
    const menu = document.createElement('div');
    menu.className = 'auto-sync-bulk-menu';
    menu.id = 'auto-sync-bulk-menu';
    const buckets = [...AUTO_SYNC_BUCKETS];
    const buttons = buckets.map(h => `
        <button type="button" onclick="bulkScheduleAutoSyncSource('${_escAttr(source)}', ${h})">
            ${_esc(autoSyncIntervalLabel(h))}
        </button>
    `).join('');
    menu.innerHTML = `
        <div class="auto-sync-bulk-menu-title">Schedule all ${_esc(autoSyncSourceLabel(source))}</div>
        <div class="auto-sync-bulk-menu-buckets">${buttons}</div>
        <button type="button" class="auto-sync-bulk-menu-custom"
                onclick="promptAutoSyncBulkCustom('${_escAttr(source)}')">
            Custom interval…
        </button>
        <button type="button" class="auto-sync-bulk-menu-unschedule"
                onclick="bulkUnscheduleAutoSyncSource('${_escAttr(source)}')">
            Unschedule all
        </button>
    `;
    document.body.appendChild(menu);
    const rect = anchor.getBoundingClientRect();
    menu.style.top = `${rect.bottom + 4}px`;
    menu.style.left = `${Math.max(8, rect.right - menu.offsetWidth)}px`;
    // Close on outside click
    setTimeout(() => {
        document.addEventListener('click', _autoSyncBulkMenuOutsideClick, { once: true });
    }, 0);
}

function _autoSyncBulkMenuOutsideClick(event) {
    const menu = document.getElementById('auto-sync-bulk-menu');
    if (menu && !menu.contains(event.target)) closeAutoSyncBulkMenu();
}

function closeAutoSyncBulkMenu() {
    const existing = document.getElementById('auto-sync-bulk-menu');
    if (existing) existing.remove();
}

function promptAutoSyncBulkCustom(source) {
    closeAutoSyncBulkMenu();
    const raw = window.prompt('Custom interval in hours (e.g. 6, 36, 96):', '6');
    if (raw === null) return;
    const hours = parseInt(raw, 10);
    if (!Number.isFinite(hours) || hours < 1) {
        showToast('Interval must be a whole number of hours, 1 or greater', 'error');
        return;
    }
    bulkScheduleAutoSyncSource(source, hours);
}

async function bulkScheduleAutoSyncSource(source, hours) {
    closeAutoSyncBulkMenu();
    const { playlists } = _autoSyncScheduleState;
    const targets = (playlists || []).filter(p => p.source === source && autoSyncCanSchedulePlaylist(p));
    if (!targets.length) {
        showToast(`No schedulable ${autoSyncSourceLabel(source)} playlists`, 'info');
        return;
    }
    if (!await showConfirmDialog({
        title: `Schedule ${targets.length} ${autoSyncSourceLabel(source)} playlist${targets.length === 1 ? '' : 's'}`,
        message: `Every ${autoSyncIntervalLabel(hours).toLowerCase().replace(/^every /, '')}. Existing schedules in this source will be updated.`,
    })) return;
    let ok = 0, fail = 0;
    for (const playlist of targets) {
        try {
            await saveAutoSyncPlaylistScheduleSilent(playlist.id, hours);
            ok++;
        } catch (_err) {
            fail++;
        }
    }
    showToast(`Scheduled ${ok} ${autoSyncSourceLabel(source)} playlist${ok === 1 ? '' : 's'} at ${autoSyncBucketLabel(hours)}${fail ? ` (${fail} failed)` : ''}`, fail ? 'warning' : 'success');
    await refreshAutoSyncScheduleModal();
}

async function bulkUnscheduleAutoSyncSource(source) {
    closeAutoSyncBulkMenu();
    const { playlists, playlistSchedules } = _autoSyncScheduleState;
    const targets = (playlists || []).filter(p => p.source === source && playlistSchedules[p.id]);
    if (!targets.length) {
        showToast(`No scheduled ${autoSyncSourceLabel(source)} playlists to unschedule`, 'info');
        return;
    }
    if (!await showConfirmDialog({
        title: `Unschedule ${targets.length} ${autoSyncSourceLabel(source)} playlist${targets.length === 1 ? '' : 's'}`,
        message: 'Removes the Auto-Sync schedules. Mirrored playlists themselves stay.',
    })) return;
    let ok = 0, fail = 0;
    for (const playlist of targets) {
        const schedule = playlistSchedules[playlist.id];
        if (!schedule) continue;
        try {
            const res = await fetch(`/api/automations/${schedule.automation_id}`, { method: 'DELETE' });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            ok++;
        } catch (_err) {
            fail++;
        }
    }
    showToast(`Removed ${ok} schedule${ok === 1 ? '' : 's'}${fail ? ` (${fail} failed)` : ''}`, fail ? 'warning' : 'success');
    await refreshAutoSyncScheduleModal();
}

async function saveAutoSyncPlaylistScheduleSilent(playlistId, hours) {
    // Like saveAutoSyncPlaylistSchedule but without toasts/refresh — caller
    // batches feedback. Re-uses the existing automation row when one already
    // exists for the playlist.
    const playlist = _autoSyncScheduleState.playlists.find(p => parseInt(p.id, 10) === parseInt(playlistId, 10));
    if (!playlist) throw new Error('playlist not found');
    const existing = _autoSyncScheduleState.playlistSchedules[playlistId];
    const payload = {
        name: `Auto-Sync: ${playlist.name}`,
        trigger_type: 'schedule',
        trigger_config: autoSyncTriggerForHours(hours),
        action_type: 'playlist_pipeline',
        action_config: { playlist_id: String(playlistId), all: false },
        then_actions: [],
        group_name: 'Playlist Auto-Sync',
        owned_by: 'auto_sync',
    };
    const res = await fetch(existing ? `/api/automations/${existing.automation_id}` : '/api/automations', {
        method: existing ? 'PUT' : 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok || data.error) throw new Error(data.error || 'Failed');
    return data;
}

function populateAutoSyncHistoryList(root = document) {
    const list = root.querySelector('.auto-sync-history-list');
    if (!list) return;
    const allHistory = Array.isArray(_autoSyncScheduleState.runHistory) ? _autoSyncScheduleState.runHistory : [];
    const total = _autoSyncScheduleState.runHistoryTotal || 0;
    const filter = _autoSyncHistoryFilter || 'all';
    const history = allHistory.filter(h => {
        if (filter === 'error') return h.status === 'error' || h.status === 'skipped';
        if (filter === 'completed') return h.status === 'completed' || h.status === 'finished';
        return true;
    });
    list.innerHTML = '';
    list.dataset.renderer = 'dom-cards';
    list.dataset.renderedCount = '0';
    if (filter !== 'all' && !history.length) {
        const note = document.createElement('div');
        note.className = 'auto-sync-history-empty';
        const strong = document.createElement('strong');
        strong.textContent = filter === 'error' ? 'No failed runs in the loaded window' : 'No completed runs in the loaded window';
        const small = document.createElement('span');
        small.textContent = 'Switch filters or load more history.';
        note.append(strong, small);
        list.appendChild(note);
        return;
    }
    history.forEach((entry, index) => {
        try {
            list.appendChild(createAutoSyncHistoryEntryElement(entry, index));
        } catch (err) {
            list.appendChild(createAutoSyncHistoryErrorElement(entry, index, err));
        }
    });
    const renderedCount = list.querySelectorAll('.auto-sync-history-entry').length;
    list.dataset.renderedCount = String(renderedCount);
    if (history.length && !renderedCount) {
        list.appendChild(createAutoSyncHistoryListFallback(history.length));
    }
    if (total > history.length) {
        const totalEl = document.createElement('div');
        totalEl.className = 'auto-sync-history-total';
        totalEl.textContent = `Showing ${history.length} of ${total} runs`;
        list.appendChild(totalEl);
    }
}

function createAutoSyncHistoryListFallback(count) {
    const fallback = document.createElement('div');
    fallback.className = 'auto-sync-history-empty';
    const title = document.createElement('strong');
    title.textContent = 'Run history could not render';
    const detail = document.createElement('span');
    detail.textContent = `${count} playlist pipeline run${count === 1 ? '' : 's'} loaded, but the card renderer did not complete. Refresh the page to reload the latest Auto-Sync assets.`;
    fallback.append(title, detail);
    return fallback;
}

function createAutoSyncHistoryErrorElement(entry, index, err) {
    const card = document.createElement('article');
    card.className = 'auto-sync-history-entry auto-sync-history-entry-error';
    const row = document.createElement('div');
    row.className = 'auto-sync-history-row';
    const head = document.createElement('div');
    head.className = 'auto-sync-history-card-head';
    const titleBlock = document.createElement('div');
    titleBlock.className = 'auto-sync-history-title-block';
    const title = document.createElement('div');
    title.className = 'auto-sync-history-title-row';
    const dot = document.createElement('span');
    dot.className = 'auto-sync-card-status-dot disabled';
    const name = document.createElement('strong');
    name.textContent = entry?.playlist_name || `Run #${entry?.id || index + 1}`;
    const badge = document.createElement('span');
    badge.className = 'auto-sync-history-status error';
    badge.textContent = 'Render error';
    title.append(dot, name, badge);
    const summary = document.createElement('small');
    summary.textContent = err?.message || 'This run history row could not be rendered.';
    titleBlock.append(title, summary);
    head.appendChild(titleBlock);
    row.appendChild(head);
    card.appendChild(row);
    return card;
}

function createAutoSyncHistoryEntryElement(entry, index = 0) {
    entry = autoSyncNormalizeHistoryEntry(entry, index);
    const status = entry.status || 'completed';
    const before = entry.before_json || {};
    const after = entry.after_json || {};
    const result = entry.result_json || {};
    const started = entry.started_at ? _autoTimeAgo(entry.started_at) : '';
    const duration = entry.duration_seconds ? autoSyncDurationLabel(entry.duration_seconds) : '';
    const trackDelta = autoSyncDelta(after.track_count, before.track_count);
    const discoveredDelta = autoSyncDelta(after.discovered_count, before.discovered_count);
    const wishlistDelta = autoSyncDelta(after.wishlisted_count, before.wishlisted_count);
    const libraryDelta = autoSyncDelta(after.in_library_count, before.in_library_count);
    const entryId = `auto-sync-history-${entry.id}`;
    const playlistName = entry.playlist_name || after.name || before.name || `Playlist #${entry.playlist_id || 'unknown'}`;
    const triggerSource = entry.trigger_source || 'pipeline';

    const card = document.createElement('article');
    card.className = `auto-sync-history-entry ${status === 'completed' || status === 'finished' ? '' : 'auto-sync-history-entry-' + status}`.trim();
    card.id = `${entryId}-card`;
    card.dataset.historyEntry = entryId;

    const row = document.createElement('div');
    row.className = 'auto-sync-history-row';
    row.setAttribute('role', 'button');
    row.tabIndex = 0;
    row.setAttribute('aria-expanded', 'false');
    row.setAttribute('aria-controls', entryId);
    row.dataset.historyToggle = entryId;

    const dot = document.createElement('span');
    dot.className = `auto-sync-card-status-dot ${autoSyncHistoryStatusClass(status)}`;

    const info = document.createElement('div');
    info.className = 'auto-sync-history-info';

    const name = document.createElement('div');
    name.className = 'auto-sync-history-name';
    name.textContent = playlistName;

    const flow = document.createElement('div');
    flow.className = 'auto-sync-history-flow';
    autoSyncAppendFlowChip(flow, triggerSource, 'flow-trigger');
    autoSyncAppendFlowArrow(flow);
    autoSyncAppendFlowChip(flow, 'Refresh', 'flow-action');
    autoSyncAppendFlowArrow(flow);
    autoSyncAppendFlowChip(flow, 'Discover', 'flow-action');
    autoSyncAppendFlowArrow(flow);
    autoSyncAppendFlowChip(flow, 'Sync + wishlist', 'flow-notify');

    const metaRow = document.createElement('div');
    metaRow.className = 'auto-sync-history-meta-inline';
    const statusBadge = document.createElement('span');
    statusBadge.className = `auto-sync-history-status ${status}`;
    statusBadge.textContent = autoSyncHistoryStatusLabel(status);
    metaRow.appendChild(statusBadge);
    if (started) {
        const t = document.createElement('span');
        t.className = 'auto-sync-history-time';
        t.textContent = started;
        metaRow.appendChild(t);
    }
    if (duration) {
        const d = document.createElement('span');
        d.className = 'auto-sync-history-duration';
        d.textContent = duration;
        metaRow.appendChild(d);
    }
    const trackChip = document.createElement('span');
    const trackClass = trackDelta > 0 ? 'pos' : trackDelta < 0 ? 'neg' : 'zero';
    trackChip.className = `auto-sync-history-delta ${trackClass}`;
    trackChip.textContent = autoSyncDeltaLabel(after.track_count, trackDelta, 'tracks');
    metaRow.appendChild(trackChip);

    info.append(name, flow, metaRow);

    const actions = document.createElement('div');
    actions.className = 'auto-sync-history-actions';
    const expand = document.createElement('button');
    expand.type = 'button';
    expand.className = 'auto-sync-history-expand-btn';
    expand.dataset.historyToggleButton = entryId;
    expand.setAttribute('aria-label', 'Toggle details');
    expand.innerHTML = '<span class="auto-sync-history-expand-icon">&#9662;</span>';
    actions.appendChild(expand);

    row.append(dot, info, actions);

    const detail = document.createElement('div');
    detail.id = entryId;
    detail.className = 'auto-sync-history-detail';
    detail.innerHTML = autoSyncHistoryDetailHtml(entry, before, after, result, { trackDelta, discoveredDelta, wishlistDelta, libraryDelta });

    card.append(row, detail);
    return card;
}

function autoSyncDeltaLabel(after, delta, unit) {
    const a = parseInt(after, 10) || 0;
    if (!delta) return `${a} ${unit}`;
    const sign = delta > 0 ? '+' : '';
    return `${a} ${unit} (${sign}${delta})`;
}

function autoSyncAppendFlowChip(parent, text, className) {
    const span = document.createElement('span');
    span.className = className;
    span.textContent = text;
    parent.appendChild(span);
}

function autoSyncAppendFlowArrow(parent) {
    const span = document.createElement('span');
    span.className = 'flow-arrow';
    span.textContent = '->';
    parent.appendChild(span);
}

function autoSyncNormalizeHistoryEntry(entry, index) {
    if (!entry || typeof entry !== 'object') {
        return {
            id: `unknown-${index}`,
            status: 'completed',
            playlist_name: 'Playlist pipeline run',
            trigger_source: 'pipeline',
            summary: 'Run history entry did not include detailed metadata.',
            before_json: {},
            after_json: {},
            result_json: {},
        };
    }
    return {
        ...entry,
        id: entry.id ?? `history-${index}`,
        before_json: autoSyncParseHistoryObject(entry.before_json),
        after_json: autoSyncParseHistoryObject(entry.after_json),
        result_json: autoSyncParseHistoryObject(entry.result_json),
    };
}

function bindAutoSyncHistoryCardInteractions(root = document) {
    root.querySelectorAll('[data-history-toggle]').forEach(row => {
        const entryId = row.dataset.historyToggle;
        row.addEventListener('click', () => autoSyncToggleHistoryEntry(entryId));
        row.addEventListener('keydown', event => autoSyncHistoryEntryKeydown(event, entryId));
    });
    root.querySelectorAll('[data-history-toggle-button]').forEach(button => {
        const entryId = button.dataset.historyToggleButton;
        button.addEventListener('click', event => {
            event.stopPropagation();
            autoSyncToggleHistoryEntry(entryId);
        });
    });
}

function autoSyncParseHistoryObject(value) {
    if (!value) return {};
    if (typeof value === 'object') return value;
    if (typeof value !== 'string') return {};
    try {
        const parsed = JSON.parse(value);
        return parsed && typeof parsed === 'object' ? parsed : {};
    } catch (_err) {
        return {};
    }
}

function autoSyncHistoryFallbackSummary(before, after, status) {
    const beforeTracks = parseInt(before.track_count, 10) || 0;
    const afterTracks = parseInt(after.track_count, 10) || 0;
    return `${autoSyncHistoryStatusLabel(status)} | ${beforeTracks} -> ${afterTracks} tracks`;
}

function autoSyncToggleHistoryEntry(entryId) {
    const el = document.getElementById(entryId);
    const card = document.getElementById(`${entryId}-card`);
    const row = card?.querySelector('.auto-sync-history-row');
    if (!el) return;
    const expanded = el.classList.toggle('expanded');
    if (card) card.classList.toggle('expanded', expanded);
    if (row) row.setAttribute('aria-expanded', expanded ? 'true' : 'false');
}

function autoSyncHistoryEntryKeydown(event, entryId) {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    event.preventDefault();
    autoSyncToggleHistoryEntry(entryId);
}

function autoSyncHistoryStatusLabel(status) {
    if (status === 'completed' || status === 'finished') return 'Completed';
    if (status === 'error') return 'Error';
    if (status === 'skipped') return 'Skipped';
    return status || 'Run';
}

function autoSyncHistoryStatusClass(status) {
    if (status === 'completed' || status === 'finished') return 'enabled';
    if (status === 'error' || status === 'skipped') return 'disabled';
    return 'enabled';
}

function autoSyncDurationLabel(seconds) {
    const total = Math.max(0, Math.round(parseFloat(seconds) || 0));
    if (total < 60) return `${total}s`;
    const mins = Math.floor(total / 60);
    const secs = total % 60;
    return `${mins}m ${secs}s`;
}

function autoSyncDelta(after, before) {
    const a = parseInt(after, 10) || 0;
    const b = parseInt(before, 10) || 0;
    return a - b;
}

function autoSyncHistoryStatHtml(label, before, after, delta) {
    const beforeValue = parseInt(before, 10) || 0;
    const afterValue = parseInt(after, 10) || 0;
    const deltaText = delta ? ` (${delta > 0 ? '+' : ''}${delta})` : '';
    return `
        <div>
            <span>${_esc(label)}</span>
            <strong>${beforeValue} -> ${afterValue}${_esc(deltaText)}</strong>
        </div>
    `;
}

function autoSyncHistoryPreviewPill(label, before, after, delta) {
    return `<span>${_esc(autoSyncHistoryPreviewText(label, before, after, delta))}</span>`;
}

function autoSyncHistoryPreviewText(label, before, after, delta) {
    const beforeValue = parseInt(before, 10) || 0;
    const afterValue = parseInt(after, 10) || 0;
    const deltaText = delta ? ` ${delta > 0 ? '+' : ''}${delta}` : '';
    return `${label} ${beforeValue}->${afterValue}${deltaText}`;
}

function autoSyncHistoryResultPill(label, value) {
    if (value === undefined || value === null || value === '') return '';
    return `<span>${_esc(label)}: ${_esc(String(value))}</span>`;
}

function autoSyncHistoryDetailHtml(entry, before, after, result, deltas) {
    const stats = [
        ['Tracks', before.track_count, after.track_count, deltas.trackDelta],
        ['Discovered', before.discovered_count, after.discovered_count, deltas.discoveredDelta],
        ['Wishlisted', before.wishlisted_count, after.wishlisted_count, deltas.wishlistDelta],
        ['In library', before.in_library_count, after.in_library_count, deltas.libraryDelta],
    ];
    const statsHtml = stats.map(([label, b, a, d]) => autoSyncHistoryStatCardHtml(label, b, a, d)).join('');
    const factsHtml = [
        ['Started', autoSyncFormatDateTime(entry.started_at)],
        ['Finished', autoSyncFormatDateTime(entry.finished_at)],
        ['Duration', entry.duration_seconds ? autoSyncDurationLabel(entry.duration_seconds) : '—'],
        ['Source', entry.source || after.source || before.source || '—'],
    ].map(([k, v]) => `<div class="auto-sync-history-fact"><span>${_esc(k)}</span><strong>${_esc(autoSyncValueLabel(v))}</strong></div>`).join('');
    // `tracks_discovered` was a status STRING (e.g. "completed"), not a
    // count — kept it out of the pills so the panel doesn't show a
    // confusing "Discovered: completed" chip. Same data is already
    // surfaced as a before/after stat card above.
    const resultPills = [
        ['Refreshed', result.playlists_refreshed],
        ['Synced', result.tracks_synced],
        ['Skipped', result.sync_skipped],
        ['Wishlisted', result.wishlist_queued],
    ].filter(([, v]) => v !== undefined && v !== null && v !== '')
     .map(([k, v]) => `<span class="auto-sync-history-result-pill"><em>${_esc(k)}</em>${_esc(String(v))}</span>`).join('');
    const errorBlock = result.error ? `<div class="auto-sync-history-error">${_esc(result.error)}</div>` : '';
    const logsHtml = autoSyncHistoryLogsCompactHtml(entry.log_lines);
    const playlistId = entry.playlist_id || after.playlist_id || before.playlist_id || '';
    const playlistName = entry.playlist_name || after.name || before.name || '';
    const runAgainHtml = playlistId
        ? `<div class="auto-sync-history-detail-actions">
              <button type="button" class="auto-sync-history-run-again"
                  onclick="event.stopPropagation(); runMirroredPlaylistPipeline(${parseInt(playlistId, 10)}, '${_escAttr(playlistName)}')">
                  Run pipeline again
              </button>
           </div>`
        : '';
    return `
        <div class="auto-sync-history-stats-grid">${statsHtml}</div>
        <div class="auto-sync-history-facts-row">${factsHtml}</div>
        ${resultPills ? `<div class="auto-sync-history-result-row">${resultPills}</div>` : ''}
        ${runAgainHtml}
        ${errorBlock}
        ${logsHtml}
    `;
}

function autoSyncHistoryStatCardHtml(label, before, after, delta) {
    const a = parseInt(after, 10) || 0;
    const b = parseInt(before, 10) || 0;
    const deltaClass = delta > 0 ? 'pos' : delta < 0 ? 'neg' : 'zero';
    const deltaText = delta ? `${delta > 0 ? '+' : ''}${delta}` : '';
    return `
        <div class="auto-sync-history-stat">
            <div class="auto-sync-history-stat-label">${_esc(label)}</div>
            <div class="auto-sync-history-stat-value">
                <span class="stat-before">${b}</span>
                <span class="stat-arrow">&rarr;</span>
                <span class="stat-after">${a}</span>
                ${deltaText ? `<span class="stat-delta ${deltaClass}">${deltaText}</span>` : ''}
            </div>
        </div>
    `;
}

function autoSyncHistoryLogsCompactHtml(logLines) {
    if (!Array.isArray(logLines) || !logLines.length) return '';
    const lines = logLines.slice(-20).map(line => {
        const text = typeof line === 'string' ? line : (line.message || line.log_line || JSON.stringify(line));
        const type = typeof line === 'object' ? (line.type || line.log_type || 'info') : 'info';
        return `<div class="auto-sync-history-log-line auto-sync-history-log-${_escAttr(type)}">${_esc(text)}</div>`;
    }).join('');
    return `<div class="auto-sync-history-log-section">${lines}</div>`;
}

function autoSyncHistoryFactHtml(label, value) {
    return `
        <div>
            <span>${_esc(label)}</span>
            <strong>${_esc(autoSyncValueLabel(value))}</strong>
        </div>
    `;
}

function autoSyncHistorySnapshotHtml(title, snapshot) {
    const fields = [
        ['Name', snapshot.name],
        ['Source', snapshot.source],
        ['Tracks', snapshot.track_count],
        ['Discovered', snapshot.discovered_count],
        ['Wishlisted', snapshot.wishlisted_count],
        ['In library', snapshot.in_library_count],
    ];
    return `
        <section class="auto-sync-history-section auto-sync-history-snapshot">
            <div class="auto-sync-history-section-title">${_esc(title)}</div>
            <div class="auto-sync-history-facts compact">
                ${fields.map(([label, value]) => autoSyncHistoryFactHtml(label, value)).join('')}
            </div>
        </section>
    `;
}

function autoSyncHistoryObjectHtml(title, obj, options = {}) {
    if (!obj || typeof obj !== 'object') return '';
    const entries = Object.entries(obj)
        .filter(([key, value]) => !(options.skipPrivate && key.startsWith('_')) && value !== undefined && value !== null && value !== '')
        .slice(0, 24);
    if (!entries.length) return '';
    return `
        <section class="auto-sync-history-section">
            <div class="auto-sync-history-section-title">${_esc(title)}</div>
            <div class="auto-sync-history-payload">
                ${entries.map(([key, value]) => `
                    <div>
                        <span>${_esc(autoSyncHumanizeKey(key))}</span>
                        <strong>${_esc(autoSyncValueLabel(value))}</strong>
                    </div>
                `).join('')}
            </div>
        </section>
    `;
}

function autoSyncHistoryLogsHtml(logLines) {
    if (!Array.isArray(logLines) || !logLines.length) return '';
    return `
        <section class="auto-sync-history-section">
            <div class="auto-sync-history-section-title">Run Log</div>
            <div class="auto-sync-history-logs">
                ${logLines.slice(-12).map(line => {
                    const text = typeof line === 'string' ? line : (line.message || line.log_line || JSON.stringify(line));
                    const type = typeof line === 'object' ? (line.type || line.log_type || 'info') : 'info';
                    return `<div class="${_escAttr(type)}">${_esc(text)}</div>`;
                }).join('')}
            </div>
        </section>
    `;
}

function autoSyncFormatDateTime(value) {
    if (!value) return '';
    const ts = _autoParseUTC(value);
    if (!Number.isFinite(ts)) return value;
    return new Date(ts).toLocaleString();
}

function autoSyncHumanizeKey(key) {
    return String(key || '')
        .replace(/^_+/, '')
        .replace(/_/g, ' ')
        .replace(/\b\w/g, ch => ch.toUpperCase());
}

function autoSyncValueLabel(value) {
    if (value === undefined || value === null || value === '') return 'Not recorded';
    if (typeof value === 'boolean') return value ? 'Yes' : 'No';
    if (Array.isArray(value)) return value.length ? value.map(autoSyncValueLabel).join(', ') : 'None';
    if (typeof value === 'object') return JSON.stringify(value);
    return String(value);
}

function autoSyncAutomationCardHtml(auto, playlists) {
    const cfg = auto.action_config || {};
    const playlistId = autoSyncPlaylistIdFromAutomation(auto);
    const playlist = playlistId ? playlists.find(p => parseInt(p.id, 10) === playlistId) : null;
    const target = cfg.all === true || cfg.all === 'true'
        ? 'All refreshable mirrored playlists'
        : playlist ? playlist.name : playlistId ? `Playlist #${playlistId}` : 'Custom pipeline target';
    const trigger = _autoFormatTrigger(auto.trigger_type, auto.trigger_config || {});
    const enabled = auto.enabled !== false && auto.enabled !== 0;
    const next = auto.next_run ? autoSyncNextRunLabel(auto.next_run) : 'not scheduled';
    const sourceLabel = playlist ? autoSyncSourceLabel(playlist.source) : (cfg.all === true || cfg.all === 'true' ? 'All sources' : 'Pipeline');
    return `
        <div class="auto-sync-automation-card">
            <span class="auto-sync-card-status-dot ${enabled ? 'enabled' : 'disabled'}"></span>
            <div class="auto-sync-automation-main">
                <div class="auto-sync-automation-title-row">
                    <strong>${_esc(auto.name || 'Playlist Pipeline')}</strong>
                </div>
                <div class="auto-sync-card-flow">
                    <span class="flow-trigger">${_esc(trigger)}</span>
                    <span class="flow-arrow">&rarr;</span>
                    <span class="flow-action">Playlist pipeline</span>
                    <span class="flow-arrow">&rarr;</span>
                    <span class="flow-notify">Refresh + sync</span>
                </div>
                <div class="auto-sync-automation-meta">
                    <span class="auto-sync-status ${enabled ? 'enabled' : 'disabled'}">${enabled ? 'Enabled' : 'Disabled'}</span>
                    <span>${_esc(sourceLabel)}</span>
                    <span>${_esc(target)}</span>
                    <span>${_esc(next)}</span>
                </div>
            </div>
            <div class="auto-sync-automation-lock">Read only</div>
        </div>
    `;
}

function autoSyncScheduledCardHtml(playlist, schedule) {
    const enabled = schedule?.enabled !== false;
    const nextLabel = schedule?.next_run ? autoSyncNextRunLabel(schedule.next_run) : '';
    const isRunning = playlist.pipeline_state?.status === 'running';
    const health = autoSyncPlaylistHealth(playlist.id);
    const healthClass = health.level === 'failing' ? 'failing'
        : health.level === 'warning' ? 'warning'
        : '';
    return `
        <div class="auto-sync-scheduled-card ${enabled ? '' : 'disabled'} ${healthClass}" draggable="true" data-playlist-id="${playlist.id}" ondragstart="autoSyncDragStart(event)" ondragend="autoSyncDragEnd()">
            <div class="auto-sync-scheduled-main">
                <div class="auto-sync-scheduled-name">
                    ${health.level !== 'ok' ? `<span class="auto-sync-scheduled-health ${healthClass}" title="${_escAttr(health.tooltip)}">${health.level === 'failing' ? '!' : '⚠'}</span>` : ''}
                    ${_esc(playlist.name)}
                </div>
                <div class="auto-sync-scheduled-meta">${_esc(autoSyncSourceLabel(playlist.source))} &middot; ${playlist.track_count || 0} tracks</div>
                <div class="auto-sync-scheduled-timing">
                    <span>${_esc(autoSyncIntervalLabel(schedule?.hours || 24))}</span>
                    ${nextLabel ? `<small>${_esc(nextLabel)}</small>` : ''}
                </div>
            </div>
            <div class="auto-sync-scheduled-actions">
                <button class="run" onclick="event.stopPropagation(); runAutoSyncScheduledPlaylist(${playlist.id})" title="Run the playlist pipeline now" ${isRunning ? 'disabled' : ''}>${isRunning ? 'Running' : 'Run now'}</button>
                <button onclick="event.stopPropagation(); unscheduleAutoSyncPlaylist(${playlist.id})" title="Remove this Auto-Sync schedule">&times;</button>
            </div>
        </div>
    `;
}

function autoSyncPlaylistHealth(playlistId) {
    // Look at the last 3 runs for this playlist in the loaded history.
    // 3-in-a-row errors = failing (red dot). 1+ recent error = warning.
    const history = _autoSyncScheduleState.runHistory || [];
    const id = parseInt(playlistId, 10);
    const recent = history
        .filter(h => parseInt(h.playlist_id, 10) === id)
        .slice(0, 3);
    if (!recent.length) return { level: 'ok', tooltip: '' };
    const errored = recent.filter(h => h.status === 'error' || h.status === 'skipped');
    if (errored.length >= 3) {
        return { level: 'failing', tooltip: `Last ${recent.length} runs failed — check Run History tab` };
    }
    if (errored.length) {
        return { level: 'warning', tooltip: `${errored.length} of last ${recent.length} runs failed` };
    }
    return { level: 'ok', tooltip: '' };
}

function autoSyncNextRunLabel(nextRun) {
    if (!nextRun) return '';
    const ts = _autoParseUTC(nextRun);
    if (!Number.isFinite(ts)) return '';
    const diff = ts - Date.now();
    if (diff <= 0) return 'due now';
    const mins = Math.ceil(diff / 60000);
    if (mins < 60) return `next in ${mins}m`;
    const hours = Math.ceil(mins / 60);
    if (hours < 24) return `next in ${hours}h`;
    return `next in ${Math.ceil(hours / 24)}d`;
}

function autoSyncDragStart(event) {
    const playlistId = event.currentTarget?.dataset?.playlistId;
    if (!playlistId) return;
    _autoSyncIsDragging = true;
    event.dataTransfer.setData('text/plain', playlistId);
    event.dataTransfer.effectAllowed = 'move';
}

function autoSyncDragOver(event) {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
    const col = event.currentTarget;
    if (col && !col.classList.contains('drag-over')) {
        col.classList.add('drag-over');
    }
}

function autoSyncDragLeave(event) {
    const col = event.currentTarget;
    if (!col) return;
    if (col.contains(event.relatedTarget)) return;
    col.classList.remove('drag-over');
}

async function autoSyncDrop(event, hours) {
    event.preventDefault();
    _autoSyncIsDragging = false;
    const col = event.currentTarget;
    if (col) col.classList.remove('drag-over');
    const playlistId = parseInt(event.dataTransfer.getData('text/plain'), 10);
    if (!playlistId) return;
    await saveAutoSyncPlaylistSchedule(playlistId, hours);
}

function autoSyncDragEnd() {
    _autoSyncIsDragging = false;
}

async function saveAutoSyncPlaylistSchedule(playlistId, hours) {
    const playlist = _autoSyncScheduleState.playlists.find(p => parseInt(p.id, 10) === parseInt(playlistId, 10));
    if (!playlist) return;
    if (!autoSyncCanSchedulePlaylist(playlist)) {
        showToast('That playlist source cannot be refreshed by Auto-Sync.', 'info');
        return;
    }
    const existing = _autoSyncScheduleState.playlistSchedules[playlistId];
    const payload = {
        name: `Auto-Sync: ${playlist.name}`,
        trigger_type: 'schedule',
        trigger_config: autoSyncTriggerForHours(hours),
        action_type: 'playlist_pipeline',
        action_config: { playlist_id: String(playlistId), all: false },
        then_actions: [],
        group_name: 'Playlist Auto-Sync',
        owned_by: 'auto_sync',
    };
    try {
        const res = await fetch(existing ? `/api/automations/${existing.automation_id}` : '/api/automations', {
            method: existing ? 'PUT' : 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await res.json();
        if (!res.ok || data.error) throw new Error(data.error || 'Failed to save Auto-Sync schedule');
        showToast(`${playlist.name} scheduled every ${autoSyncBucketLabel(hours)}`, 'success');
        await refreshAutoSyncScheduleModal();
    } catch (err) {
        showToast(`Error: ${err.message}`, 'error');
    }
}

async function unscheduleAutoSyncPlaylist(playlistId) {
    const schedule = _autoSyncScheduleState.playlistSchedules[playlistId];
    const playlist = _autoSyncScheduleState.playlists.find(p => parseInt(p.id, 10) === parseInt(playlistId, 10));
    if (!schedule) return;
    if (!await showConfirmDialog({ title: 'Remove Auto-Sync', message: `Remove Auto-Sync schedule for "${playlist?.name || 'this playlist'}"?` })) return;
    try {
        const res = await fetch(`/api/automations/${schedule.automation_id}`, { method: 'DELETE' });
        const data = await res.json();
        if (!res.ok || data.error) throw new Error(data.error || 'Failed to remove Auto-Sync schedule');
        showToast('Auto-Sync schedule removed', 'success');
        await refreshAutoSyncScheduleModal();
    } catch (err) {
        showToast(`Error: ${err.message}`, 'error');
    }
}

async function runAutoSyncScheduledPlaylist(playlistId) {
    const playlist = _autoSyncScheduleState.playlists.find(p => parseInt(p.id, 10) === parseInt(playlistId, 10));
    if (!playlist) return;
    await runMirroredPlaylistPipeline(playlistId, playlist.name || `Playlist #${playlistId}`);
    await refreshAutoSyncScheduleModal();
}

function manageAutoSyncStatusPolling() {
    const overlay = document.getElementById('auto-sync-schedule-modal');
    if (!overlay) {
        stopAutoSyncStatusPolling();
        return;
    }
    const hasRunning = _autoSyncScheduleState.playlists.some(p => p.pipeline_state?.status === 'running');
    if (!hasRunning) {
        stopAutoSyncStatusPolling();
        return;
    }
    if (_autoSyncStatusPoller) return;
    _autoSyncStatusPoller = setInterval(() => {
        if (_autoSyncIsDragging) return;
        refreshAutoSyncScheduleModal();
    }, 3000);
}

function stopAutoSyncStatusPolling() {
    if (!_autoSyncStatusPoller) return;
    clearInterval(_autoSyncStatusPoller);
    _autoSyncStatusPoller = null;
}

async function parseMirroredPipelineResponse(res, fallbackMessage) {
    const text = await res.text();
    let data = {};
    if (text) {
        try {
            data = JSON.parse(text);
        } catch (_err) {
            const detail = res.status === 404
                ? 'Auto-Sync endpoint not found. Restart the SoulSync server so the new backend routes load.'
                : fallbackMessage;
            throw new Error(detail);
        }
    }
    if (!res.ok || data.error) {
        throw new Error(data.error || fallbackMessage);
    }
    return data;
}

async function editMirroredSourceRef(playlistId, name, source, currentRef) {
    const label = (source === 'spotify_public' || source === 'youtube')
        ? 'original playlist URL'
        : 'original playlist ID or URL';
    const nextRef = window.prompt(`Update ${label} for "${name}"`, currentRef || '');
    if (nextRef === null) return;
    const trimmed = nextRef.trim();
    if (!trimmed) {
        showToast('Source link or ID is required', 'error');
        return;
    }

    try {
        const res = await fetch(`/api/mirrored-playlists/${playlistId}/source-ref`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ source_ref: trimmed })
        });
        const data = await res.json();
        if (!res.ok || data.error) {
            throw new Error(data.error || 'Failed to update source reference');
        }
        showToast(`Updated source for ${name}`, 'success');
        loadMirroredPlaylists();
        const openModal = document.getElementById('mirrored-track-modal');
        if (openModal) {
            closeMirroredModal();
            openMirroredPlaylistModal(playlistId);
        }
    } catch (err) {
        showToast(`Error: ${err.message}`, 'error');
    }
}

function applyMirroredPipelineState(playlistId, state) {
    const hash = `mirrored_${playlistId}`;
    const existing = youtubePlaylistStates[hash] || {};
    const status = state.status || 'idle';
    let phase = existing.phase;
    if (status === 'running') phase = 'pipeline_running';
    else if (status === 'finished') phase = 'pipeline_complete';
    else if (status === 'error' || status === 'skipped') phase = 'pipeline_error';

    youtubePlaylistStates[hash] = {
        ...existing,
        phase,
        pipeline_status: status,
        pipeline_progress: state.progress || 0,
        pipeline_phase: state.phase || '',
        pipeline_error: state.error || '',
        pipeline_log: state.log || [],
        pipeline_result: state.result || null,
    };

    updateMirroredCardPhase(hash, phase);
}

async function runMirroredPlaylistPipeline(playlistId, name) {
    try {
        const res = await fetch(`/api/mirrored-playlists/${playlistId}/pipeline/run`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({})
        });
        const data = await parseMirroredPipelineResponse(res, 'Failed to start Auto-Sync');
        applyMirroredPipelineState(playlistId, data.state || { status: 'running', progress: 0, phase: 'Starting pipeline...' });
        showToast(`Auto-Sync started for ${name}`, 'success');
        _autoSyncScheduleState.playlists = _autoSyncScheduleState.playlists.map(p => (
            parseInt(p.id, 10) === parseInt(playlistId, 10)
                ? { ...p, pipeline_state: data.state || { status: 'running', progress: 0, phase: 'Starting pipeline...' } }
                : p
        ));
        renderAutoSyncScheduleModal();
        manageAutoSyncStatusPolling();
        pollMirroredPipelineStatus(playlistId, name);
    } catch (err) {
        showToast(`Error: ${err.message}`, 'error');
    }
}

function pollMirroredPipelineStatus(playlistId, name) {
    const key = `mirrored_${playlistId}`;
    if (mirroredPipelinePollers[key]) clearInterval(mirroredPipelinePollers[key]);

    const tick = async () => {
        try {
            const res = await fetch(`/api/mirrored-playlists/${playlistId}/pipeline/status`);
            const state = await parseMirroredPipelineResponse(res, 'Failed to read Auto-Sync status');
            applyMirroredPipelineState(playlistId, state);

            if (state.status === 'finished') {
                clearInterval(mirroredPipelinePollers[key]);
                delete mirroredPipelinePollers[key];
                showToast(`Auto-Sync complete for ${name}`, 'success');
                loadMirroredPlaylists();
                refreshAutoSyncScheduleModal();
            } else if (state.status === 'error' || state.status === 'skipped') {
                clearInterval(mirroredPipelinePollers[key]);
                delete mirroredPipelinePollers[key];
                showToast(state.error || `Pipeline stopped for ${name}`, 'error');
                loadMirroredPlaylists();
                refreshAutoSyncScheduleModal();
            } else if (state.status === 'idle') {
                clearInterval(mirroredPipelinePollers[key]);
                delete mirroredPipelinePollers[key];
            }
        } catch (err) {
            clearInterval(mirroredPipelinePollers[key]);
            delete mirroredPipelinePollers[key];
            showToast(`Pipeline status error: ${err.message}`, 'error');
        }
    };

    tick();
    mirroredPipelinePollers[key] = setInterval(tick, 2500);
}
