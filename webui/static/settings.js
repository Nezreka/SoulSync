// SUPPORT MODAL
// ===============================

function showSupportModal() {
    const overlay = document.getElementById('support-modal-overlay');
    if (overlay) overlay.classList.remove('hidden');
}

function closeSupportModal() {
    const overlay = document.getElementById('support-modal-overlay');
    if (overlay) overlay.classList.add('hidden');
}

async function copyAddress(address, cryptoName) {
    try {
        // navigator.clipboard requires HTTPS — use fallback for HTTP (Docker)
        if (navigator.clipboard && window.isSecureContext) {
            await navigator.clipboard.writeText(address);
        } else {
            const textarea = document.createElement('textarea');
            textarea.value = address;
            textarea.style.position = 'fixed';
            textarea.style.opacity = '0';
            document.body.appendChild(textarea);
            textarea.select();
            document.execCommand('copy');
            document.body.removeChild(textarea);
        }
        showToast(`${cryptoName} address copied to clipboard`, 'success');
    } catch (error) {
        console.error('Failed to copy address:', error);
        // Show the address so user can copy manually
        showToast(`${cryptoName}: ${address}`, 'info');
    }
}

// ===============================
// SETTINGS FUNCTIONALITY
// ===============================

let settingsAutoSaveTimer = null;

function debouncedAutoSaveSettings() {
    if (settingsAutoSaveTimer) clearTimeout(settingsAutoSaveTimer);
    settingsAutoSaveTimer = setTimeout(() => saveSettings(true), 2000);
}

function handleManualSaveClick() {
    if (settingsAutoSaveTimer) clearTimeout(settingsAutoSaveTimer);
    saveSettings(false);
}

function initializeSettings() {
    // This function is called when the settings page is loaded.
    // It attaches event listeners to all interactive elements on the page.

    // Accent color listeners (live preview + custom picker toggle)
    initAccentColorListeners();

    // Main save button (manual save, non-quiet)
    // Uses named function reference so addEventListener deduplicates across repeated calls
    const saveButton = document.getElementById('save-settings');
    if (saveButton) {
        saveButton.addEventListener('click', handleManualSaveClick);
    }

    // Debounced auto-save on all settings inputs
    // Uses named function reference (debouncedAutoSaveSettings) so addEventListener deduplicates
    const settingsPage = document.getElementById('settings-page');
    if (settingsPage) {
        settingsPage.querySelectorAll('input[type="text"], input[type="url"], input[type="password"], input[type="number"], input[type="range"]').forEach(input => {
            input.addEventListener('input', debouncedAutoSaveSettings);
        });
        settingsPage.querySelectorAll('input[type="checkbox"], select').forEach(input => {
            input.addEventListener('change', debouncedAutoSaveSettings);
        });
    }

    // Server toggle buttons
    const plexToggle = document.getElementById('plex-toggle');
    if (plexToggle) {
        plexToggle.addEventListener('click', () => toggleServer('plex'));
    }
    const jellyfinToggle = document.getElementById('jellyfin-toggle');
    if (jellyfinToggle) {
        jellyfinToggle.addEventListener('click', () => toggleServer('jellyfin'));
    }

    // Auto-detect buttons
    const detectSlskdBtn = document.querySelector('#soulseek-url + .detect-button');
    if (detectSlskdBtn) {
        detectSlskdBtn.addEventListener('click', autoDetectSlskd);
    }
    const detectPlexBtn = document.querySelector('#plex-container .detect-button');
    if (detectPlexBtn) {
        detectPlexBtn.addEventListener('click', autoDetectPlex);
    }
    const detectJellyfinBtn = document.querySelector('#jellyfin-container .detect-button');
    if (detectJellyfinBtn) {
        detectJellyfinBtn.addEventListener('click', autoDetectJellyfin);
    }

    // Test connection buttons
    // Test button event listeners removed - they use onclick attributes in HTML to avoid double firing
}

function resetFileOrganizationTemplates() {
    // Reset templates to defaults
    const defaults = {
        album: '$albumartist/$albumartist - $album/$track - $title',
        single: '$artist/$artist - $title/$title',
        playlist: '$playlist/$artist - $title',
        video: '$artist/$title-video'
    };

    document.getElementById('template-album-path').value = defaults.album;
    document.getElementById('template-single-path').value = defaults.single;
    document.getElementById('template-playlist-path').value = defaults.playlist;
    document.getElementById('template-video-path').value = defaults.video;

    debouncedAutoSaveSettings();
}

function validateFileOrganizationTemplates() {
    const errors = [];

    // Valid variables for each template type
    const validVars = {
        album: ['$artist', '$albumartist', '$artistletter', '$album', '$albumtype', '$title', '$track', '$disc', '$discnum', '$cdnum', '$year', '$quality'],
        single: ['$artist', '$albumartist', '$artistletter', '$album', '$albumtype', '$title', '$track', '$year', '$quality'],
        playlist: ['$artist', '$artistletter', '$playlist', '$title', '$year', '$quality'],
        video: ['$artist', '$artistletter', '$title', '$year']
    };

    // Get template values
    const albumPath = document.getElementById('template-album-path').value.trim();
    const singlePath = document.getElementById('template-single-path').value.trim();
    const playlistPath = document.getElementById('template-playlist-path').value.trim();

    // Validate album template
    if (albumPath) {
        if (albumPath.endsWith('/')) {
            errors.push('Album template cannot end with /');
        }
        if (albumPath.startsWith('/')) {
            errors.push('Album template cannot start with /');
        }
        if (!albumPath.includes('/')) {
            errors.push('Album template must include at least one folder (use / separator)');
        }
        if (albumPath.includes('//')) {
            errors.push('Album template cannot have consecutive slashes //');
        }
        // Check for likely typos of valid variables (case-insensitive to catch $Album, $ARTIST, etc.)
        const albumVarPattern = /\$\{([a-zA-Z]+)\}|\$([a-zA-Z]+)/g;
        const foundVars = albumPath.match(albumVarPattern) || [];
        foundVars.forEach(v => {
            // Normalize ${var} to $var for validation
            const normalized = v.startsWith('${') ? '$' + v.slice(2, -1) : v;
            const lowerVar = normalized.toLowerCase();
            // Check if lowercase version exists in valid vars
            const isValid = validVars.album.some(validVar => validVar.toLowerCase() === lowerVar);
            if (!isValid) {
                errors.push(`Invalid variable "${normalized}" in album template. Valid: ${validVars.album.join(', ')}`);
            } else if (normalized !== lowerVar && validVars.album.includes(lowerVar)) {
                // Variable is valid but has wrong case
                errors.push(`Variable "${normalized}" should be lowercase: "${lowerVar}"`);
            }
        });
    }

    // Validate single template
    if (singlePath) {
        if (singlePath.endsWith('/')) {
            errors.push('Single template cannot end with /');
        }
        if (singlePath.startsWith('/')) {
            errors.push('Single template cannot start with /');
        }
        // Note: single template is allowed to have no slash (flat file: "$artist - $title")
        if (singlePath.includes('//')) {
            errors.push('Single template cannot have consecutive slashes //');
        }
        const singleVarPattern = /\$\{([a-zA-Z]+)\}|\$([a-zA-Z]+)/g;
        const foundVars = singlePath.match(singleVarPattern) || [];
        foundVars.forEach(v => {
            const normalized = v.startsWith('${') ? '$' + v.slice(2, -1) : v;
            const lowerVar = normalized.toLowerCase();
            const isValid = validVars.single.some(validVar => validVar.toLowerCase() === lowerVar);
            if (!isValid) {
                errors.push(`Invalid variable "${normalized}" in single template. Valid: ${validVars.single.join(', ')}`);
            } else if (normalized !== lowerVar && validVars.single.includes(lowerVar)) {
                errors.push(`Variable "${normalized}" should be lowercase: "${lowerVar}"`);
            }
        });
    }

    // Validate playlist template
    if (playlistPath) {
        if (playlistPath.endsWith('/')) {
            errors.push('Playlist template cannot end with /');
        }
        if (playlistPath.startsWith('/')) {
            errors.push('Playlist template cannot start with /');
        }
        if (!playlistPath.includes('/')) {
            errors.push('Playlist template must include at least one folder (use / separator)');
        }
        if (playlistPath.includes('//')) {
            errors.push('Playlist template cannot have consecutive slashes //');
        }
        const playlistVarPattern = /\$\{([a-zA-Z]+)\}|\$([a-zA-Z]+)/g;
        const foundVars = playlistPath.match(playlistVarPattern) || [];
        foundVars.forEach(v => {
            const normalized = v.startsWith('${') ? '$' + v.slice(2, -1) : v;
            const lowerVar = normalized.toLowerCase();
            const isValid = validVars.playlist.some(validVar => validVar.toLowerCase() === lowerVar);
            if (!isValid) {
                errors.push(`Invalid variable "${normalized}" in playlist template. Valid: ${validVars.playlist.join(', ')}`);
            } else if (normalized !== lowerVar && validVars.playlist.includes(lowerVar)) {
                errors.push(`Variable "${normalized}" should be lowercase: "${lowerVar}"`);
            }
        });
    }

    return errors;
}

// Settings redesign — tab switching + service accordions
function switchSettingsTab(tab) {
    // Update tab bar
    document.querySelectorAll('.stg-tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tab));
    // Show/hide settings groups and section headers by data-stg attribute
    document.querySelectorAll('#settings-page [data-stg]').forEach(g => {
        g.style.display = g.dataset.stg === tab ? '' : 'none';
    });
    // Re-apply collapsed state on section bodies (tab switch resets inline display)
    document.querySelectorAll('#settings-page .settings-section-body.collapsed').forEach(b => {
        b.style.display = 'none';
    });
    // Also hide/show the column wrappers if they're empty in this tab
    document.querySelectorAll('#settings-page .settings-left-column, #settings-page .settings-right-column, #settings-page .settings-third-column').forEach(col => {
        const hasVisible = Array.from(col.querySelectorAll('.settings-group[data-stg]')).some(g => g.style.display !== 'none');
        col.style.display = hasVisible ? '' : 'none';
    });
    // Re-apply conditional visibility (quality profile, source containers, etc.)
    if (typeof updateDownloadSourceUI === 'function') {
        try { updateDownloadSourceUI(); } catch (e) { }
    }
    // Load DB maintenance info when switching to Advanced tab
    if (tab === 'advanced' && typeof loadDbMaintenanceInfo === 'function') {
        try { loadDbMaintenanceInfo(); } catch (e) { }
    }
    // Initialize live log viewer when switching to Logs tab
    if (tab === 'logs') {
        _logViewerInit();
    } else {
        _logViewerStop();
    }
    // Refresh the green/yellow header gradient when arriving on Connections
    if (tab === 'connections') {
        try { applyServiceStatusGradients(); } catch (e) { }
    }
}

// ── Settings → Connections: per-service status gradient + verify wiring ──
// Gradient shows green when the user has filled in credentials, yellow when empty.
// It's based purely on config presence (cheap, no API calls). The verify layer —
// which runs on expand / Expand All — surfaces whether those credentials actually
// work, via an inline warning bar inside the expanded panel.

let _stgServiceStatusState = {};  // service -> {configured: bool}
let _stgServiceVerifyInFlight = {};  // service -> true while a verify call is running

async function applyServiceStatusGradients() {
    try {
        const resp = await fetch('/api/settings/config-status');
        if (!resp.ok) return;
        const data = await resp.json();
        _stgServiceStatusState = data || {};
        document.querySelectorAll('#settings-page .stg-service[data-service]').forEach(card => {
            const service = card.getAttribute('data-service');
            const header = card.querySelector('.stg-service-header');
            if (!service || !header) return;
            const configured = !!(data[service] && data[service].configured);
            header.classList.toggle('status-configured', configured);
            header.classList.toggle('status-missing', !configured);
            // Ensure the header has a spinner placeholder for the verify-checking state
            if (!header.querySelector('.stg-service-verify-spinner')) {
                const spinner = document.createElement('span');
                spinner.className = 'stg-service-verify-spinner';
                // Insert before the chevron on the right
                const chevron = header.querySelector('.stg-service-chevron');
                if (chevron) header.insertBefore(spinner, chevron);
                else header.appendChild(spinner);
            }
        });
    } catch (e) {
        console.warn('[Settings Status] Failed to apply gradients:', e);
    }
}

function _stgSetCheckingState(service, isChecking) {
    const card = document.querySelector(`#settings-page .stg-service[data-service="${service}"]`);
    if (!card) return;
    const header = card.querySelector('.stg-service-header');
    const body = card.querySelector('.stg-service-body');
    if (header) {
        header.classList.toggle('status-checking', !!isChecking);
        // Lazy-create the spinner element so it's there even if
        // applyServiceStatusGradients() hasn't run yet.
        if (!header.querySelector('.stg-service-verify-spinner')) {
            const spinner = document.createElement('span');
            spinner.className = 'stg-service-verify-spinner';
            const chevron = header.querySelector('.stg-service-chevron');
            if (chevron) header.insertBefore(spinner, chevron);
            else header.appendChild(spinner);
        }
    }
    if (!body) return;
    const existing = body.querySelector('.stg-service-verify-status');
    if (isChecking) {
        if (!existing) {
            const status = document.createElement('div');
            status.className = 'stg-service-verify-status';
            status.textContent = 'Testing connection…';
            body.insertBefore(status, body.firstChild);
        }
    } else if (existing) {
        existing.remove();
    }
}

function _stgShowVerifyWarning(service, message) {
    const card = document.querySelector(`#settings-page .stg-service[data-service="${service}"]`);
    if (!card) return;
    const body = card.querySelector('.stg-service-body');
    if (!body) return;
    const existing = body.querySelector('.stg-service-warning');
    if (existing) existing.remove();
    const warning = document.createElement('div');
    warning.className = 'stg-service-warning';
    warning.innerHTML = `
        <span class="stg-service-warning-icon">&#9888;</span>
        <span class="stg-service-warning-text"></span>
    `;
    warning.querySelector('.stg-service-warning-text').textContent =
        message || 'Connection test failed.';
    body.insertBefore(warning, body.firstChild);
}

function _stgClearVerifyWarning(service) {
    const card = document.querySelector(`#settings-page .stg-service[data-service="${service}"]`);
    if (!card) return;
    const existing = card.querySelector('.stg-service-warning');
    if (existing) existing.remove();
}

async function _stgRefreshAfterSave() {
    // Called after a successful settings save. Cheap gradient refresh always,
    // plus re-verify any cards the user currently has expanded (so they see
    // immediate feedback on credentials they just edited). Collapsed cards
    // keep their cached verify result until the user expands them.
    try {
        await applyServiceStatusGradients();
        const expandedServices = Array.from(
            document.querySelectorAll('#settings-page .stg-service.expanded[data-service]')
        )
            .map(card => card.getAttribute('data-service'))
            .filter(Boolean);
        if (expandedServices.length > 0) {
            _stgVerifyServices(expandedServices, { force: true });
        }
    } catch (e) {
        console.warn('[Settings Status] Post-save refresh failed:', e);
    }
}

async function _stgVerifyServices(services, { force = false } = {}) {
    if (!services || !services.length) return {};
    // Mark all as checking immediately so the user sees spinners/status lines
    services.forEach(svc => {
        _stgServiceVerifyInFlight[svc] = true;
        _stgSetCheckingState(svc, true);
        _stgClearVerifyWarning(svc);
    });
    try {
        const url = '/api/settings/verify' + (force ? '?force=true' : '');
        const resp = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ services })
        });
        const data = await resp.json();
        services.forEach(svc => {
            _stgServiceVerifyInFlight[svc] = false;
            _stgSetCheckingState(svc, false);
            const result = data[svc];
            if (result && result.success === false) {
                _stgShowVerifyWarning(svc, result.error || result.message || '');
            }
        });
        return data;
    } catch (e) {
        console.warn('[Settings Verify] Network error:', e);
        services.forEach(svc => {
            _stgServiceVerifyInFlight[svc] = false;
            _stgSetCheckingState(svc, false);
            _stgShowVerifyWarning(svc, 'Unable to reach the verification endpoint.');
        });
        return {};
    }
}

function toggleStgService(el) {
    const service = el.closest('.stg-service');
    if (service) {
        const wasExpanded = service.classList.contains('expanded');
        service.classList.toggle('expanded');
        // Fire verify when expanding a single card (not on collapse). The backend
        // caches per service for 5 min, so rapid expand/collapse won't re-ping.
        if (!wasExpanded) {
            const serviceName = service.getAttribute('data-service');
            if (serviceName && !_stgServiceVerifyInFlight[serviceName]) {
                _stgVerifyServices([serviceName]);
            }
        }
    }
}
function toggleAllServiceAccordions(btn) {
    const services = document.querySelectorAll('#settings-page .stg-service');
    const allExpanded = Array.from(services).every(s => s.classList.contains('expanded'));
    const willExpand = !allExpanded;
    services.forEach(s => s.classList.toggle('expanded', willExpand));
    btn.textContent = allExpanded ? 'Expand All' : 'Collapse All';

    // On Expand All, fire a single batched verify for every service that has a
    // data-service attribute. Backend caps concurrency at 3 to avoid rate limits.
    // Skipped on Collapse All.
    if (willExpand) {
        const serviceNames = Array.from(services)
            .map(s => s.getAttribute('data-service'))
            .filter(Boolean)
            .filter(name => !_stgServiceVerifyInFlight[name]);
        if (serviceNames.length > 0) {
            _stgVerifyServices(serviceNames);
        }
    }
}

// ── Hybrid source priority list (drag-and-drop) ──
const HYBRID_SOURCES = [
    { id: 'soulseek', name: 'Soulseek', icon: 'https://raw.githubusercontent.com/slskd/slskd/master/docs/icon.png', emoji: '🎵' },
    { id: 'youtube', name: 'YouTube', icon: 'https://www.svgrepo.com/show/13671/youtube.svg', emoji: '▶️' },
    { id: 'tidal', name: 'Tidal', icon: 'https://www.svgrepo.com/show/519734/tidal.svg', emoji: '🌊' },
    { id: 'qobuz', name: 'Qobuz', icon: 'https://www.svgrepo.com/show/504778/qobuz.svg', emoji: '🎧' },
    { id: 'hifi', name: 'HiFi', icon: null, emoji: '🎶' },
    { id: 'deezer_dl', name: 'Deezer', icon: 'https://www.svgrepo.com/show/519734/deezer.svg', emoji: '🎧' },
    { id: 'lidarr', name: 'Lidarr', icon: null, emoji: '📦' },
];

let _hybridSourceOrder = ['soulseek', 'youtube'];
let _hybridSourceEnabled = { soulseek: true, youtube: true, tidal: false, qobuz: false, hifi: false, deezer_dl: false, lidarr: false };
let _hybridVisualOrder = null; // Full visual order including disabled sources

function buildHybridSourceList() {
    const container = document.getElementById('hybrid-source-list');
    if (!container) return;

    container.innerHTML = '';
    // Build visual order: use persisted visual order, or enabled first + disabled at bottom
    if (!_hybridVisualOrder) {
        _hybridVisualOrder = [..._hybridSourceOrder];
        for (const src of HYBRID_SOURCES) {
            if (!_hybridVisualOrder.includes(src.id)) _hybridVisualOrder.push(src.id);
        }
    }
    const allIds = _hybridVisualOrder;

    allIds.forEach((srcId, idx) => {
        const src = HYBRID_SOURCES.find(s => s.id === srcId);
        if (!src) return;
        const enabled = _hybridSourceEnabled[srcId] !== false;
        const isInOrder = _hybridSourceOrder.includes(srcId);
        const priorityNum = isInOrder && enabled ? _hybridSourceOrder.indexOf(srcId) + 1 : '';

        const item = document.createElement('div');
        item.className = `hybrid-source-item${enabled ? '' : ' disabled'}`;
        item.draggable = true;
        item.dataset.sourceId = srcId;

        item.innerHTML = `
            <span class="hybrid-source-arrows">
                <button class="hybrid-arrow-btn" onclick="moveHybridSource('${srcId}', -1)" title="Move up">▲</button>
                <button class="hybrid-arrow-btn" onclick="moveHybridSource('${srcId}', 1)" title="Move down">▼</button>
            </span>
            ${src.icon
                ? `<img class="hybrid-source-icon" src="${src.icon}" alt="${src.name}" onerror="this.outerHTML='<span class=\\'hybrid-source-icon emoji-icon\\'>${src.emoji}</span>'">`
                : `<span class="hybrid-source-icon emoji-icon">${src.emoji}</span>`
            }
            <span class="hybrid-source-name">${src.name}</span>
            <span class="hybrid-source-priority">${priorityNum}</span>
            <label class="hybrid-source-toggle">
                <input type="checkbox" ${enabled ? 'checked' : ''} onchange="toggleHybridSource('${srcId}', this.checked)">
                <span class="toggle-track"></span>
            </label>
        `;

        container.appendChild(item);
    });

    // Sync hidden selects for backward compat
    _syncHybridHiddenSelects();
}

function moveHybridSource(srcId, direction) {
    if (!_hybridVisualOrder) return;
    const idx = _hybridVisualOrder.indexOf(srcId);
    if (idx < 0) return;
    const newIdx = idx + direction;
    if (newIdx < 0 || newIdx >= _hybridVisualOrder.length) return;

    // Swap in visual order
    [_hybridVisualOrder[idx], _hybridVisualOrder[newIdx]] = [_hybridVisualOrder[newIdx], _hybridVisualOrder[idx]];

    // Rebuild enabled order from visual order
    _hybridSourceOrder = _hybridVisualOrder.filter(id => _hybridSourceEnabled[id] !== false);
    buildHybridSourceList();
    updateDownloadSourceUI();
    debouncedAutoSaveSettings();
}

function toggleHybridSource(srcId, enabled) {
    _hybridSourceEnabled[srcId] = enabled;
    // Rebuild enabled order from visual order so priority matches position
    if (_hybridVisualOrder) {
        _hybridSourceOrder = _hybridVisualOrder.filter(id => _hybridSourceEnabled[id] !== false);
    }
    buildHybridSourceList();
    updateDownloadSourceUI();
    debouncedAutoSaveSettings();
}

function _syncHybridOrderFromDOM() {
    const container = document.getElementById('hybrid-source-list');
    if (!container) return;
    const items = container.querySelectorAll('.hybrid-source-item');
    const newOrder = [];
    items.forEach(item => {
        const id = item.dataset.sourceId;
        if (_hybridSourceEnabled[id] !== false) {
            newOrder.push(id);
        }
    });
    _hybridSourceOrder = newOrder;
}

function _syncHybridHiddenSelects() {
    // Keep hidden selects in sync for backward compat with saveSettings
    const primary = document.getElementById('hybrid-primary-source');
    const secondary = document.getElementById('hybrid-secondary-source');
    if (primary && _hybridSourceOrder.length > 0) primary.value = _hybridSourceOrder[0];
    if (secondary && _hybridSourceOrder.length > 1) secondary.value = _hybridSourceOrder[1];
}

function getHybridOrder() {
    return _hybridSourceOrder.filter(s => _hybridSourceEnabled[s] !== false);
}

function loadHybridSourceOrder(settings) {
    const order = settings.download_source?.hybrid_order;
    const sourceStatus = settings._source_status || {};

    if (order && Array.isArray(order) && order.length > 0) {
        _hybridSourceOrder = order;
        _hybridSourceEnabled = {};
        for (const src of HYBRID_SOURCES) {
            _hybridSourceEnabled[src.id] = order.includes(src.id);
        }
    } else {
        // Legacy: fall back to primary/secondary
        const primary = settings.download_source?.hybrid_primary || 'soulseek';
        const secondary = settings.download_source?.hybrid_secondary || 'youtube';
        _hybridSourceOrder = [primary, secondary];
        _hybridSourceEnabled = {};
        for (const src of HYBRID_SOURCES) {
            _hybridSourceEnabled[src.id] = src.id === primary || src.id === secondary;
        }
    }

    // Auto-disable sources that aren't configured on the server
    let changed = false;
    for (const src of HYBRID_SOURCES) {
        if (_hybridSourceEnabled[src.id] && sourceStatus[src.id] === false) {
            _hybridSourceEnabled[src.id] = false;
            changed = true;
        }
    }
    if (changed) {
        _hybridSourceOrder = _hybridSourceOrder.filter(id => _hybridSourceEnabled[id] !== false);
    }

    _hybridVisualOrder = null; // Reset so buildHybridSourceList rebuilds it
    buildHybridSourceList();
}

function updateLossyBitrateOptions() {
    const codec = document.getElementById('lossy-copy-codec')?.value || 'mp3';
    const bitrateSelect = document.getElementById('lossy-copy-bitrate');
    if (!bitrateSelect) return;
    const opt320 = bitrateSelect.querySelector('option[value="320"]');
    if (codec === 'opus') {
        // Opus max is 256kbps per channel — hide 320 option
        if (opt320) opt320.disabled = true;
        if (bitrateSelect.value === '320') bitrateSelect.value = '256';
    } else {
        if (opt320) opt320.disabled = false;
    }
}

function updatePlexConfigurationButtons() {
    const plexUrl = document.getElementById('plex-url');
    const plexToken = document.getElementById('plex-token');
    const hasPlexConfig = Boolean((plexUrl?.value || '').trim() || (plexToken?.value || '').trim());
    const plexViewConfigButton = document.getElementById('plex-view-config-button');
    const plexLinkToPlexButton = document.getElementById('plex-link-to-plex-button');
    const plexManualConfigButton = document.getElementById('plex-manual-config-button');
    const plexUrlActions = document.getElementById('plex-url-actions');
    const plexTokenActions = document.getElementById('plex-token-actions');
    const plexPinAuthFlow = document.getElementById('plex-pin-auth-flow');

    if (plexViewConfigButton) plexViewConfigButton.style.display = hasPlexConfig ? '' : 'none';
    if (plexLinkToPlexButton) plexLinkToPlexButton.style.display = hasPlexConfig ? 'none' : '';
    if (plexManualConfigButton) plexManualConfigButton.style.display = hasPlexConfig ? 'none' : '';
    if (plexUrlActions) plexUrlActions.style.display = hasPlexConfig ? 'none' : 'flex';
    if (plexTokenActions) plexTokenActions.style.display = hasPlexConfig ? 'none' : 'flex';
    if (plexPinAuthFlow) plexPinAuthFlow.style.display = 'none';
}

async function loadSettingsData() {
    try {
        const response = await fetch(API.settings);
        const settings = await response.json();

        // Populate Spotify settings
        document.getElementById('spotify-client-id').value = settings.spotify?.client_id || '';
        document.getElementById('spotify-client-secret').value = settings.spotify?.client_secret || '';
        document.getElementById('spotify-redirect-uri').value = settings.spotify?.redirect_uri || 'http://127.0.0.1:8888/callback';
        document.getElementById('spotify-callback-display').textContent = settings.spotify?.redirect_uri || 'http://127.0.0.1:8888/callback';

        // Populate Tidal settings
        document.getElementById('tidal-client-id').value = settings.tidal?.client_id || '';
        document.getElementById('tidal-client-secret').value = settings.tidal?.client_secret || '';
        document.getElementById('tidal-redirect-uri').value = settings.tidal?.redirect_uri || 'http://127.0.0.1:8889/tidal/callback';
        document.getElementById('tidal-callback-display').textContent = settings.tidal?.redirect_uri || 'http://127.0.0.1:8889/tidal/callback';

        // Populate Deezer OAuth settings
        document.getElementById('deezer-app-id').value = settings.deezer?.app_id || '';
        document.getElementById('deezer-app-secret').value = settings.deezer?.app_secret || '';
        document.getElementById('deezer-redirect-uri').value = settings.deezer?.redirect_uri || 'http://127.0.0.1:8008/deezer/callback';
        document.getElementById('deezer-callback-display').textContent = settings.deezer?.redirect_uri || 'http://127.0.0.1:8008/deezer/callback';

        // Add event listeners to update display URLs when input changes
        document.getElementById('spotify-redirect-uri').addEventListener('input', function () {
            document.getElementById('spotify-callback-display').textContent = this.value || 'http://127.0.0.1:8888/callback';
        });

        document.getElementById('tidal-redirect-uri').addEventListener('input', function () {
            document.getElementById('tidal-callback-display').textContent = this.value || 'http://127.0.0.1:8889/tidal/callback';
        });

        document.getElementById('deezer-redirect-uri').addEventListener('input', function () {
            document.getElementById('deezer-callback-display').textContent = this.value || 'http://127.0.0.1:8008/deezer/callback';
        });

        // Populate Plex settings
        const plexUrlInput = document.getElementById('plex-url');
        const plexTokenInput = document.getElementById('plex-token');
        if (plexUrlInput) plexUrlInput.value = settings.plex?.base_url || '';
        if (plexTokenInput) plexTokenInput.value = settings.plex?.token || '';
        if (plexUrlInput) plexUrlInput.addEventListener('input', updatePlexConfigurationButtons);
        if (plexTokenInput) plexTokenInput.addEventListener('input', updatePlexConfigurationButtons);
        updatePlexConfigurationButtons();

        // Populate Jellyfin settings
        document.getElementById('jellyfin-url').value = settings.jellyfin?.base_url || '';
        document.getElementById('jellyfin-api-key').value = settings.jellyfin?.api_key || '';
        document.getElementById('jellyfin-timeout').value = settings.jellyfin?.api_timeout || 120;

        // Populate Navidrome settings
        document.getElementById('navidrome-url').value = settings.navidrome?.base_url || '';
        document.getElementById('navidrome-username').value = settings.navidrome?.username || '';
        document.getElementById('navidrome-password').value = settings.navidrome?.password || '';

        // Set active server and toggle visibility
        const activeServer = settings.active_media_server || 'plex';
        toggleServer(activeServer);

        // Load Plex music libraries if Plex is the active server
        if (activeServer === 'plex') {
            loadPlexMusicLibraries();
        }

        // Load Jellyfin users and music libraries if Jellyfin is the active server
        if (activeServer === 'jellyfin') {
            loadJellyfinUsers().then(() => loadJellyfinMusicLibraries());
        }

        // Load Navidrome music folders if Navidrome is the active server
        if (activeServer === 'navidrome') {
            loadNavidromeMusicFolders();
        }

        // Populate Soulseek settings
        document.getElementById('soulseek-url').value = settings.soulseek?.slskd_url || '';
        document.getElementById('soulseek-api-key').value = settings.soulseek?.api_key || '';
        document.getElementById('soulseek-search-timeout').value = settings.soulseek?.search_timeout || 60;
        document.getElementById('soulseek-search-timeout-buffer').value = settings.soulseek?.search_timeout_buffer || 15;
        document.getElementById('soulseek-min-peer-speed').value = settings.soulseek?.min_peer_upload_speed || 0;
        document.getElementById('soulseek-max-peer-queue').value = settings.soulseek?.max_peer_queue || 0;
        document.getElementById('soulseek-download-timeout').value = Math.round((settings.soulseek?.download_timeout || 600) / 60);
        document.getElementById('soulseek-auto-clear-searches').checked = settings.soulseek?.auto_clear_searches !== false;

        // Populate ListenBrainz settings
        document.getElementById('listenbrainz-base-url').value = settings.listenbrainz?.base_url || '';
        document.getElementById('listenbrainz-token').value = settings.listenbrainz?.token || '';

        // Populate AcoustID settings
        document.getElementById('acoustid-api-key').value = settings.acoustid?.api_key || '';
        document.getElementById('acoustid-enabled').checked = settings.acoustid?.enabled || false;

        // Populate Last.fm settings
        document.getElementById('lastfm-api-key').value = settings.lastfm?.api_key || '';
        document.getElementById('lastfm-api-secret').value = settings.lastfm?.api_secret || '';
        document.getElementById('lastfm-scrobble-enabled').checked = settings.lastfm?.scrobble_enabled === true;
        const lfmStatus = document.getElementById('lastfm-scrobble-status');
        if (lfmStatus) {
            lfmStatus.textContent = settings.lastfm?.session_key ? 'Authorized' : 'Not authorized';
        }

        // Populate ListenBrainz scrobble toggle
        document.getElementById('listenbrainz-scrobble-enabled').checked = settings.listenbrainz?.scrobble_enabled === true;

        // Populate Genius settings
        document.getElementById('genius-access-token').value = settings.genius?.access_token || '';

        // Populate iTunes settings
        document.getElementById('itunes-country').value = settings.itunes?.country || 'US';

        // Populate Discogs settings
        document.getElementById('discogs-token').value = settings.discogs?.token || '';

        // Populate Metadata source setting
        document.getElementById('metadata-fallback-source').value = settings.metadata?.fallback_source || 'itunes';

        // Populate Hydrabase settings
        const hbConfig = settings.hydrabase || {};
        document.getElementById('hydrabase-url').value = hbConfig.url || '';
        document.getElementById('hydrabase-api-key').value = hbConfig.api_key || '';
        document.getElementById('hydrabase-auto-connect').checked = hbConfig.auto_connect || false;
        // Check live connection status + add Hydrabase to fallback dropdown if connected
        fetch('/api/hydrabase/status').then(r => r.json()).then(s => {
            const btn = document.getElementById('hydrabase-connect-btn');
            const statusEl = document.getElementById('hydrabase-settings-status');
            if (s.connected) {
                if (btn) btn.textContent = 'Disconnect';
                if (statusEl) { statusEl.textContent = 'Connected'; statusEl.style.color = '#4caf50'; }
                // Add Hydrabase to fallback source dropdown
                const fbSelect = document.getElementById('metadata-fallback-source');
                if (fbSelect && !fbSelect.querySelector('option[value="hydrabase"]')) {
                    const opt = document.createElement('option');
                    opt.value = 'hydrabase';
                    opt.textContent = 'Hydrabase (P2P)';
                    fbSelect.appendChild(opt);
                }
                // Restore selection if it was hydrabase
                if ((settings.metadata?.fallback_source) === 'hydrabase') {
                    fbSelect.value = 'hydrabase';
                }
            }
        }).catch(() => { });

        // Populate Download settings (right column)
        document.getElementById('download-path').value = settings.soulseek?.download_path || './downloads';
        document.getElementById('transfer-path').value = settings.soulseek?.transfer_path || './Transfer';
        document.getElementById('staging-path').value = settings.import?.staging_path || './Staging';
        document.getElementById('music-videos-path').value = settings.library?.music_videos_path || './MusicVideos';

        // Populate Download Source settings
        document.getElementById('download-source-mode').value = settings.download_source?.mode || 'soulseek';
        document.getElementById('stream-source').value = settings.download_source?.stream_source || 'youtube';
        document.getElementById('max-concurrent-downloads').value = settings.download_source?.max_concurrent || '3';
        loadHybridSourceOrder(settings);
        document.getElementById('tidal-download-quality').value = settings.tidal_download?.quality || 'lossless';
        document.getElementById('tidal-allow-fallback').checked = settings.tidal_download?.allow_fallback !== false;
        document.getElementById('qobuz-quality').value = settings.qobuz?.quality || 'lossless';
        document.getElementById('qobuz-allow-fallback').checked = settings.qobuz?.allow_fallback !== false;
        document.getElementById('hifi-download-quality').value = settings.hifi_download?.quality || 'lossless';
        document.getElementById('hifi-allow-fallback').checked = settings.hifi_download?.allow_fallback !== false;
        loadHiFiInstances();
        document.getElementById('deezer-download-quality').value = settings.deezer_download?.quality || 'flac';
        document.getElementById('deezer-allow-fallback').checked = settings.deezer_download?.allow_fallback !== false;
        document.getElementById('deezer-download-arl').value = settings.deezer_download?.arl || '';
        document.getElementById('lidarr-url').value = settings.lidarr_download?.url || '';
        document.getElementById('lidarr-api-key').value = settings.lidarr_download?.api_key || '';
        // Sync ARL to connections tab field + bidirectional listeners
        const _connArl = document.getElementById('deezer-connection-arl');
        const _dlArl = document.getElementById('deezer-download-arl');
        if (_connArl) _connArl.value = settings.deezer_download?.arl || '';
        if (_connArl && _dlArl) {
            _connArl.addEventListener('input', () => { _dlArl.value = _connArl.value; });
            _dlArl.addEventListener('input', () => { _connArl.value = _dlArl.value; });
        }

        // Populate YouTube settings
        document.getElementById('youtube-cookies-browser').value = settings.youtube?.cookies_browser || '';
        document.getElementById('youtube-download-delay').value = settings.youtube?.download_delay ?? 3;

        // Update UI based on download source mode
        updateDownloadSourceUI();

        // Populate Database settings
        document.getElementById('max-workers').value = settings.database?.max_workers || '5';

        // Populate Post-Processing settings
        document.getElementById('metadata-enabled').checked = settings.metadata_enhancement?.enabled !== false;
        document.getElementById('embed-album-art').checked = settings.metadata_enhancement?.embed_album_art !== false;
        document.getElementById('cover-art-download').checked = settings.metadata_enhancement?.cover_art_download !== false;
        document.getElementById('prefer-caa-art').checked = settings.metadata_enhancement?.prefer_caa_art === true;
        document.getElementById('lrclib-enabled').checked = settings.metadata_enhancement?.lrclib_enabled !== false;
        document.getElementById('replaygain-enabled').checked = settings.post_processing?.replaygain_enabled === true;
        // Load service master toggles
        document.getElementById('embed-spotify').checked = settings.spotify?.embed_tags !== false;
        document.getElementById('embed-itunes').checked = settings.itunes?.embed_tags !== false;
        document.getElementById('embed-musicbrainz').checked = settings.musicbrainz?.embed_tags !== false;
        document.getElementById('embed-deezer').checked = settings.deezer?.embed_tags !== false;
        document.getElementById('embed-audiodb').checked = settings.audiodb?.embed_tags !== false;
        document.getElementById('embed-tidal').checked = settings.tidal?.embed_tags !== false;
        document.getElementById('embed-qobuz').checked = settings.qobuz?.embed_tags !== false;
        document.getElementById('embed-lastfm').checked = settings.lastfm?.embed_tags !== false;
        document.getElementById('embed-genius').checked = settings.genius?.embed_tags !== false;
        // Load per-tag toggles from data-config attributes
        document.querySelectorAll('[data-config]').forEach(cb => {
            const path = cb.dataset.config.split('.');
            let val = settings;
            for (const key of path) { val = val?.[key]; }
            cb.checked = val !== false;
        });
        // Apply service disabled state to child tags
        ['spotify', 'itunes', 'musicbrainz', 'deezer', 'audiodb', 'tidal', 'qobuz', 'lastfm', 'genius'].forEach(svc => {
            const master = document.getElementById('embed-' + svc);
            if (master) toggleServiceTags(master, svc);
        });
        document.getElementById('post-processing-options').style.display = settings.metadata_enhancement?.enabled !== false ? 'block' : 'none';

        // Populate File Organization settings
        document.getElementById('file-organization-enabled').checked = settings.file_organization?.enabled !== false;
        document.getElementById('template-album-path').value = settings.file_organization?.templates?.album_path || '$albumartist/$albumartist - $album/$track - $title';
        document.getElementById('template-single-path').value = settings.file_organization?.templates?.single_path || '$artist/$artist - $title/$title';
        document.getElementById('template-playlist-path').value = settings.file_organization?.templates?.playlist_path || '$playlist/$artist - $title';
        document.getElementById('template-video-path').value = settings.file_organization?.templates?.video_path || '$artist/$title-video';
        document.getElementById('disc-label').value = settings.file_organization?.disc_label || 'Disc';
        document.getElementById('collab-artist-mode').value = settings.file_organization?.collab_artist_mode || 'first';
        document.getElementById('artist-separator').value = settings.metadata_enhancement?.tags?.artist_separator || ', ';
        document.getElementById('write-multi-artist').checked = settings.metadata_enhancement?.tags?.write_multi_artist || false;
        document.getElementById('feat-in-title').checked = settings.metadata_enhancement?.tags?.feat_in_title || false;
        document.getElementById('allow-duplicate-tracks').checked = settings.wishlist?.allow_duplicate_tracks !== false;

        // Populate Playlist Sync settings
        document.getElementById('create-backup').checked = settings.playlist_sync?.create_backup !== false;

        // Populate Post-Download Conversion settings
        document.getElementById('downsample-hires').checked = settings.lossy_copy?.downsample_hires === true;
        document.getElementById('lossy-copy-enabled').checked = settings.lossy_copy?.enabled === true;
        document.getElementById('lossy-copy-codec').value = settings.lossy_copy?.codec || 'mp3';
        document.getElementById('lossy-copy-bitrate').value = settings.lossy_copy?.bitrate || '320';
        updateLossyBitrateOptions();
        document.getElementById('lossy-copy-delete-original').checked = settings.lossy_copy?.delete_original === true;

        // Populate Listening Stats settings
        document.getElementById('listening-stats-enabled').checked = settings.listening_stats?.enabled === true;
        document.getElementById('listening-stats-interval').value = settings.listening_stats?.poll_interval || 30;
        document.getElementById('lossy-copy-options').style.display =
            settings.lossy_copy?.enabled ? 'block' : 'none';

        // Populate Music Library Paths
        const _musicPaths = settings.library?.music_paths || [];
        renderMusicPaths(_musicPaths);

        // Populate Content Filter settings
        document.getElementById('allow-explicit').checked = settings.content_filter?.allow_explicit !== false;

        // Populate Genre Whitelist
        const gwEnabled = settings.genre_whitelist?.enabled === true;
        document.getElementById('genre-whitelist-enabled').checked = gwEnabled;
        const gwContainer = document.getElementById('genre-whitelist-container');
        if (gwContainer) gwContainer.style.display = gwEnabled ? '' : 'none';
        if (gwEnabled) {
            _genreWhitelistRender(settings.genre_whitelist?.genres || []);
        }

        // Populate Import settings
        document.getElementById('import-replace-lower-quality').checked = settings.import?.replace_lower_quality === true;

        // Populate M3U Export settings
        document.getElementById('m3u-export-enabled').checked = settings.m3u_export?.enabled === true;
        document.getElementById('m3u-entry-base-path').value = settings.m3u_export?.entry_base_path || '';

        // Populate UI Appearance settings
        const accentPreset = settings.ui_appearance?.accent_preset || '#1db954';
        const accentCustom = settings.ui_appearance?.accent_color || '#1db954';
        const presetSelect = document.getElementById('accent-preset');
        const customPicker = document.getElementById('accent-custom-color');
        const customGroup = document.getElementById('custom-color-group');
        if (presetSelect) {
            // Check if the saved preset matches a dropdown option
            const presetOptions = Array.from(presetSelect.options).map(o => o.value);
            if (presetOptions.includes(accentPreset)) {
                presetSelect.value = accentPreset;
            } else {
                presetSelect.value = 'custom';
            }
            if (presetSelect.value === 'custom') {
                if (customGroup) customGroup.style.display = '';
                if (customPicker) customPicker.value = accentCustom;
                applyAccentColor(accentCustom);
            } else {
                if (customGroup) customGroup.style.display = 'none';
                applyAccentColor(accentPreset);
            }
        }

        // Sidebar visualizer type
        const vizType = settings.ui_appearance?.sidebar_visualizer || 'bars';
        const vizSelect = document.getElementById('sidebar-visualizer-type');
        if (vizSelect) vizSelect.value = vizType;
        sidebarVisualizerType = vizType;

        // Background particles toggle
        const particlesEnabled = settings.ui_appearance?.particles_enabled !== false; // default true
        const particlesCheckbox = document.getElementById('particles-enabled');
        if (particlesCheckbox) particlesCheckbox.checked = particlesEnabled;
        applyParticlesSetting(particlesEnabled);

        // Worker orbs toggle
        const workerOrbsEnabled = settings.ui_appearance?.worker_orbs_enabled !== false; // default true
        const workerOrbsCheckbox = document.getElementById('worker-orbs-enabled');
        if (workerOrbsCheckbox) workerOrbsCheckbox.checked = workerOrbsEnabled;
        applyWorkerOrbsSetting(workerOrbsEnabled);

        // Reduce effects toggle
        const reduceEffects = settings.ui_appearance?.reduce_effects === true; // default false
        const reduceCheckbox = document.getElementById('reduce-effects-enabled');
        if (reduceCheckbox) reduceCheckbox.checked = reduceEffects;
        applyReduceEffects(reduceEffects);

        // Populate Logging information
        const logLevelSelect = document.getElementById('log-level-select');
        if (logLevelSelect) logLevelSelect.value = settings.logging?.level || 'INFO';
        document.getElementById('log-path-display').textContent = settings.logging?.path || 'logs/app.log';

        // Load Discovery Lookback Period setting
        try {
            const lookbackResponse = await fetch('/api/discovery/lookback-period');
            const lookbackData = await lookbackResponse.json();
            if (lookbackData.period) {
                document.getElementById('discovery-lookback-period').value = lookbackData.period;
            }
        } catch (error) {
            console.error('Error loading discovery lookback period:', error);
        }

        // Load Hemisphere setting
        try {
            const hemiResponse = await fetch('/api/discovery/hemisphere');
            const hemiData = await hemiResponse.json();
            if (hemiData.hemisphere) {
                document.getElementById('discovery-hemisphere').value = hemiData.hemisphere;
            }
        } catch (error) {
            console.error('Error loading hemisphere setting:', error);
        }

        // Load current log level
        try {
            const logLevelResponse = await fetch('/api/settings/log-level');
            const logLevelData = await logLevelResponse.json();
            if (logLevelData.success && logLevelData.level) {
                document.getElementById('log-level-select').value = logLevelData.level;
            }
        } catch (error) {
            console.error('Error loading log level:', error);
        }

        // Load security settings
        try {
            const requirePin = settings.security?.require_pin_on_launch || false;
            document.getElementById('security-require-pin').checked = requirePin;

            // CORS origins — stored verbatim as the user typed (string).
            const corsOrigins = settings.security?.cors_origins || '';
            const corsField = document.getElementById('security-cors-origins');
            if (corsField) corsField.value = corsOrigins;

            // Check if admin has a PIN set
            const profilesRes = await fetch('/api/profiles');
            const profilesData = await profilesRes.json();
            const adminProfile = (profilesData.profiles || []).find(p => p.is_admin);
            const adminHasPin = adminProfile?.has_pin || false;

            // Show/hide PIN setup vs change sections
            document.getElementById('security-pin-setup').style.display = adminHasPin ? 'none' : 'block';
            document.getElementById('security-change-pin-section').style.display = adminHasPin ? 'block' : 'none';

            // If no PIN, disable the toggle
            if (!adminHasPin) {
                document.getElementById('security-require-pin').checked = false;
                document.getElementById('security-require-pin').disabled = true;
            }
        } catch (error) {
            console.error('Error loading security settings:', error);
        }

        // Check dev mode status
        try {
            const devResponse = await fetch('/api/dev-mode');
            const devData = await devResponse.json();
            if (devData.enabled) {
                document.getElementById('dev-mode-status').textContent = 'Active';
                document.getElementById('dev-mode-status').style.color = 'rgb(var(--accent-light-rgb))';
                document.getElementById('hydrabase-nav').style.display = '';
                document.getElementById('hydrabase-button-container').style.display = '';
            }
        } catch (error) {
            console.error('Error checking dev mode:', error);
        }

    } catch (error) {
        console.error('Error loading settings:', error);
        showToast('Failed to load settings', 'error');
    }
}

async function changeLogLevel() {
    const selector = document.getElementById('log-level-select');
    const level = selector.value;

    try {
        const response = await fetch('/api/settings/log-level', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ level: level })
        });

        const data = await response.json();

        if (data.success) {
            showToast(`Log level changed to ${level}`, 'success');
            console.log(`Log level changed to: ${level}`);
        } else {
            showToast(`Failed to change log level: ${data.error}`, 'error');
        }
    } catch (error) {
        console.error('Error changing log level:', error);
        showToast('Failed to change log level', 'error');
    }
}

function updateMediaServerFields() {
    const serverType = document.getElementById('media-server-type').value;
    const urlInput = document.getElementById('media-server-url');
    const tokenInput = document.getElementById('media-server-token');

    if (serverType === 'plex') {
        urlInput.placeholder = 'http://localhost:32400';
        tokenInput.placeholder = 'Plex Token';
    } else {
        urlInput.placeholder = 'http://localhost:8096';
        tokenInput.placeholder = 'Jellyfin API Key';
    }
}

let _plexPinAuthRequestId = null;
let _plexPinAuthPollInterval = null;

function showPlexConfiguration(disableFields = false, isManualConfig = false) {
    stopPlexPinAuthPolling();
    const plexConfig = document.getElementById('plex-configuration');
    const plexSetup = document.getElementById('plex-setup');
    const plexPinAuthFlow = document.getElementById('plex-pin-auth-flow');
    const plexUrl = document.getElementById('plex-url');
    const plexToken = document.getElementById('plex-token');
    const plexLibraryContainer = document.getElementById('plex-library-selector-container');

    if (plexConfig) plexConfig.style.display = '';
    if (plexSetup) plexSetup.style.display = 'none';
    if (plexPinAuthFlow) plexPinAuthFlow.style.display = 'none';
    if (plexUrl) plexUrl.disabled = disableFields;
    if (plexToken) plexToken.disabled = disableFields;
    if (plexLibraryContainer && isManualConfig) {
        plexLibraryContainer.style.display = 'none';
    }
    setPlexConfigActionButton(isManualConfig);
    updatePlexConfigurationButtons();
}

function showPlexSetup() {
    const plexConfig = document.getElementById('plex-configuration');
    const plexSetup = document.getElementById('plex-setup');
    const plexPinAuthFlow = document.getElementById('plex-pin-auth-flow');
    const plexLibraryContainer = document.getElementById('plex-library-selector-container');

    if (plexConfig) plexConfig.style.display = 'none';
    if (plexSetup) plexSetup.style.display = '';
    if (plexPinAuthFlow) plexPinAuthFlow.style.display = 'none';
    if (plexLibraryContainer) plexLibraryContainer.style.display = 'none';
    setPlexConfigActionButton(false);
}

function setPlexConfigActionButton(isManualConfig) {
    const actionButton = document.getElementById('plex-config-action-button');
    if (!actionButton) return;

    if (isManualConfig) {
        actionButton.textContent = 'Cancel';
        actionButton.onclick = showPlexSetup;
        actionButton.title = 'Cancel manual Plex configuration';
    } else {
        actionButton.textContent = 'Clear Configuration';
        actionButton.onclick = clearPlexConfiguration;
        actionButton.title = 'Clear saved Plex configuration';
    }
}

async function startPlexPinAuth() {
    const setupButtons = document.getElementById('plex-setup-buttons');
    const authFlow = document.getElementById('plex-pin-auth-flow');
    const statusEl = document.getElementById('plex-pin-status');
    if (setupButtons) setupButtons.style.display = 'none';
    if (authFlow) authFlow.style.display = '';
    if (statusEl) statusEl.textContent = 'Starting Plex authorization...';

    try {
        showLoadingOverlay('Starting Plex authorization...');
        const response = await fetch('/api/plex/pin/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });
        const result = await response.json();
        if (!result.success) {
            throw new Error(result.error || 'Failed to start Plex PIN flow');
        }

        _plexPinAuthRequestId = result.request_id;
        const pinCodeEl = document.getElementById('plex-pin-code');
        if (pinCodeEl) pinCodeEl.textContent = result.code || '';
        if (statusEl) {
            statusEl.textContent = result.expires_in
                ? `Enter this code at plex.tv/link. Code expires in ${result.expires_in} seconds.`
                : 'Enter this code at plex.tv/link. Waiting for authorization...';
        }

        startPlexPinAuthPolling();
    } catch (error) {
        console.error('Plex PIN auth start failed:', error);
        showToast(error.message || 'Failed to start Plex authorization', 'error');
        cancelPlexPinAuth();
    } finally {
        hideLoadingOverlay();
    }
}

function startPlexPinAuthPolling() {
    stopPlexPinAuthPolling();
    if (!_plexPinAuthRequestId) return;
    _plexPinAuthPollInterval = setInterval(pollPlexPinAuthStatus, 5000);
    pollPlexPinAuthStatus();
}

function stopPlexPinAuthPolling() {
    if (_plexPinAuthPollInterval) {
        clearInterval(_plexPinAuthPollInterval);
        _plexPinAuthPollInterval = null;
    }
}

async function pollPlexPinAuthStatus() {
    if (!_plexPinAuthRequestId) return;
    try {
        const response = await fetch(`/api/plex/pin/status?request_id=${encodeURIComponent(_plexPinAuthRequestId)}`);
        const result = await response.json();
        const statusEl = document.getElementById('plex-pin-status');

        if (!result.success && result.expired) {
            if (statusEl) statusEl.textContent = 'PIN code expired. Generate a new code to continue.';
            stopPlexPinAuthPolling();
            return;
        }

        if (result.success) {
            stopPlexPinAuthPolling();
            if (statusEl) statusEl.textContent = 'Authorization complete! Saving Plex configuration...';
            document.getElementById('plex-url').value = result.found_url || '';
            document.getElementById('plex-token').value = result.token || '';
            if (typeof saveSettings === 'function') {
                await saveSettings(true);
            }
            showToast('Plex successfully linked', 'success');
            showPlexConfiguration(true);
            await testConnection('plex');
            return;
        }

        if (result.status) {
            if (statusEl) statusEl.textContent = result.status;
            return;
        }

        if (result.error) {
            if (statusEl) statusEl.textContent = result.error;
            return;
        }
    } catch (error) {
        console.error('Error polling Plex PIN status:', error);
        const statusEl = document.getElementById('plex-pin-status');
        if (statusEl) statusEl.textContent = 'Unable to contact Plex auth status. Retrying...';
    }
}

function cancelPlexPinAuth() {
    stopPlexPinAuthPolling();
    _plexPinAuthRequestId = null;
    const setupButtons = document.getElementById('plex-setup-buttons');
    const authFlow = document.getElementById('plex-pin-auth-flow');
    if (setupButtons) setupButtons.style.display = '';
    if (authFlow) authFlow.style.display = 'none';
}

function restartPlexPinAuth() {
    cancelPlexPinAuth();
    startPlexPinAuth();
}

async function clearPlexConfiguration() {
    cancelPlexPinAuth();
    const plexUrl = document.getElementById('plex-url');
    const plexToken = document.getElementById('plex-token');
    const plexConfig = document.getElementById('plex-configuration');
    const plexSetup = document.getElementById('plex-setup');
    const plexSetupButtons = document.getElementById('plex-setup-buttons');
    const plexViewConfigButton = document.getElementById('plex-view-config-button');
    const plexLinkToPlexButton = document.getElementById('plex-link-to-plex-button');
    const plexManualConfigButton = document.getElementById('plex-manual-config-button');

    if (plexUrl) plexUrl.value = '';
    if (plexToken) plexToken.value = '';
    if (plexConfig) plexConfig.style.display = 'none';
    if (plexSetup) plexSetup.style.display = '';
    if (plexSetupButtons) plexSetupButtons.style.display = '';
    if (plexViewConfigButton) plexViewConfigButton.style.display = 'none';
    if (plexLinkToPlexButton) plexLinkToPlexButton.style.display = '';
    if (plexManualConfigButton) plexManualConfigButton.style.display = '';

    const plexLibraryContainer = document.getElementById('plex-library-selector-container');
    const plexLibrarySelect = document.getElementById('plex-music-library');
    if (plexLibrarySelect) {
        plexLibrarySelect.innerHTML = '<option value="">Select a music library</option>';
    }
    if (plexLibraryContainer) {
        plexLibraryContainer.style.display = 'none';
    }

    updatePlexConfigurationButtons();

    try {
        await fetch('/api/plex/clear-library', { method: 'POST' });
    } catch (e) {
        console.warn('Failed to clear Plex library preference:', e);
    }

    if (typeof saveSettings === 'function') {
        saveSettings(true);
    }
    if (typeof showToast === 'function') {
        showToast('Plex configuration cleared', 'success');
    }
}

function toggleServer(serverType) {
    // Update toggle buttons
    document.getElementById('plex-toggle').classList.remove('active');
    document.getElementById('jellyfin-toggle').classList.remove('active');
    document.getElementById('navidrome-toggle').classList.remove('active');
    document.getElementById('soulsync-toggle')?.classList.remove('active');
    document.getElementById(`${serverType}-toggle`)?.classList.add('active');

    // Show/hide server containers
    document.getElementById('plex-container').classList.toggle('hidden', serverType !== 'plex');
    document.getElementById('jellyfin-container').classList.toggle('hidden', serverType !== 'jellyfin');
    document.getElementById('navidrome-container').classList.toggle('hidden', serverType !== 'navidrome');
    document.getElementById('soulsync-container')?.classList.toggle('hidden', serverType !== 'soulsync');

    // Show Plex setup when Plex is selected; otherwise hide both Plex panels
    const plexConfig = document.getElementById('plex-configuration');
    const plexSetup = document.getElementById('plex-setup');
    if (plexConfig) plexConfig.style.display = serverType === 'plex' ? 'none' : '';
    if (plexSetup) plexSetup.style.display = serverType === 'plex' ? '' : 'none';

    // Load Plex music libraries when switching to Plex
    if (serverType === 'plex') {
        loadPlexMusicLibraries();
    }

    // Load Jellyfin users and music libraries when switching to Jellyfin
    if (serverType === 'jellyfin') {
        loadJellyfinUsers().then(() => loadJellyfinMusicLibraries());
    }

    // Load Navidrome music folders when switching to Navidrome
    if (serverType === 'navidrome') {
        loadNavidromeMusicFolders();
    }

    // Auto-save after server toggle change
    debouncedAutoSaveSettings();
}

function updateDownloadSourceUI() {
    const mode = document.getElementById('download-source-mode').value;
    const hybridContainer = document.getElementById('hybrid-settings-container');
    const soulseekContainer = document.getElementById('soulseek-settings-container');
    const tidalContainer = document.getElementById('tidal-download-settings-container');
    const qobuzContainer = document.getElementById('qobuz-settings-container');
    const youtubeContainer = document.getElementById('youtube-settings-container');
    const hifiContainer = document.getElementById('hifi-download-settings-container');
    const deezerDlContainer = document.getElementById('deezer-download-settings-container');
    const lidarrContainer = document.getElementById('lidarr-download-settings-container');

    hybridContainer.style.display = mode === 'hybrid' ? 'block' : 'none';

    // Determine which sources are active
    let activeSources = new Set();
    if (mode === 'hybrid') {
        const order = getHybridOrder();
        for (const src of order) activeSources.add(src);
        // Fallback: if no sources enabled, at least show soulseek
        if (activeSources.size === 0) activeSources.add('soulseek');
    } else {
        activeSources.add(mode);
    }

    soulseekContainer.style.display = activeSources.has('soulseek') ? 'block' : 'none';
    tidalContainer.style.display = activeSources.has('tidal') ? 'block' : 'none';
    qobuzContainer.style.display = activeSources.has('qobuz') ? 'block' : 'none';
    youtubeContainer.style.display = activeSources.has('youtube') ? 'block' : 'none';
    hifiContainer.style.display = activeSources.has('hifi') ? 'block' : 'none';
    if (deezerDlContainer) deezerDlContainer.style.display = activeSources.has('deezer_dl') ? 'block' : 'none';
    if (lidarrContainer) lidarrContainer.style.display = activeSources.has('lidarr') ? 'block' : 'none';

    // Quality profile is Soulseek-only and downloads-tab-only
    const qualityProfileSection = document.getElementById('quality-profile-section');
    if (qualityProfileSection) {
        const activeTab = document.querySelector('.stg-tab.active');
        const onDownloadsTab = activeTab && activeTab.dataset.tab === 'downloads';
        qualityProfileSection.style.display = (activeSources.has('soulseek') && onDownloadsTab) ? '' : 'none';
    }

    if (activeSources.has('tidal')) {
        checkTidalDownloadAuthStatus();
    }
    if (activeSources.has('qobuz')) {
        checkQobuzAuthStatus();
    }
    if (activeSources.has('hifi')) {
        testHiFiConnection();
    }
}

function updateHybridSecondaryOptions() {
    const primary = document.getElementById('hybrid-primary-source').value;
    const secondary = document.getElementById('hybrid-secondary-source');
    const currentValue = secondary.value;
    const allSources = [
        { value: 'soulseek', label: 'Soulseek' },
        { value: 'youtube', label: 'YouTube' },
        { value: 'tidal', label: 'Tidal' },
        { value: 'qobuz', label: 'Qobuz' },
        { value: 'hifi', label: 'HiFi' },
    ];

    secondary.innerHTML = '';
    for (const source of allSources) {
        if (source.value === primary) continue;
        const opt = document.createElement('option');
        opt.value = source.value;
        opt.textContent = source.label;
        secondary.appendChild(opt);
    }

    // Restore previous selection if still valid, otherwise pick first available
    if (currentValue !== primary) {
        secondary.value = currentValue;
    }

    // Refresh source-specific settings visibility based on new primary/secondary
    updateDownloadSourceUI();
}

// ===============================
// QUALITY PROFILE FUNCTIONS
// ===============================

let currentQualityProfile = null;

async function loadQualityProfile() {
    try {
        const response = await fetch('/api/quality-profile');
        const data = await response.json();

        if (data.success) {
            currentQualityProfile = data.profile;
            populateQualityProfileUI(currentQualityProfile);
        }
    } catch (error) {
        console.error('Error loading quality profile:', error);
    }
}

function populateQualityProfileUI(profile) {
    // Update preset buttons
    document.querySelectorAll('.preset-button').forEach(btn => {
        btn.classList.remove('active');
    });
    const activePresetBtn = document.querySelector(`.preset-button[onclick*="${profile.preset}"]`);
    if (activePresetBtn) {
        activePresetBtn.classList.add('active');
    }

    // Populate each quality tier
    const qualities = ['flac', 'mp3_320', 'mp3_256', 'mp3_192'];
    qualities.forEach(quality => {
        const config = profile.qualities[quality];
        if (config) {
            // Set enabled checkbox
            const enabledCheckbox = document.getElementById(`quality-${quality}-enabled`);
            if (enabledCheckbox) {
                enabledCheckbox.checked = config.enabled;
            }

            // Set min/max sliders
            const minSlider = document.getElementById(`${quality}-min`);
            const maxSlider = document.getElementById(`${quality}-max`);
            if (minSlider && maxSlider) {
                minSlider.value = config.min_kbps;
                maxSlider.value = config.max_kbps;
                updateQualityRange(quality);
            }

            // Set priority display
            const prioritySpan = document.getElementById(`priority-${quality}`);
            if (prioritySpan) {
                prioritySpan.textContent = `Priority: ${config.priority}`;
            }

            // Toggle sliders visibility
            const sliders = document.getElementById(`sliders-${quality}`);
            if (sliders) {
                if (config.enabled) {
                    sliders.classList.remove('disabled');
                } else {
                    sliders.classList.add('disabled');
                }
            }

            // FLAC-specific: restore bit depth selector and fallback toggle
            if (quality === 'flac') {
                const bitDepthValue = config.bit_depth || 'any';
                document.querySelectorAll('.bit-depth-btn').forEach(btn => {
                    btn.classList.toggle('active', btn.getAttribute('data-value') === bitDepthValue);
                });
                const bitDepthSelector = document.getElementById('flac-bit-depth-selector');
                if (bitDepthSelector) {
                    if (config.enabled) {
                        bitDepthSelector.classList.remove('disabled');
                    } else {
                        bitDepthSelector.classList.add('disabled');
                    }
                }
                // Show/hide and restore fallback toggle
                const fallbackToggle = document.getElementById('flac-fallback-toggle');
                if (fallbackToggle) {
                    fallbackToggle.style.display = bitDepthValue === 'any' ? 'none' : 'block';
                }
                const fallbackCb = document.getElementById('flac-bit-depth-fallback');
                if (fallbackCb) {
                    fallbackCb.checked = config.bit_depth_fallback !== false;
                }
            }
        }
    });

    // Set fallback checkbox
    const fallbackCheckbox = document.getElementById('quality-fallback-enabled');
    if (fallbackCheckbox) {
        fallbackCheckbox.checked = profile.fallback_enabled;
    }
}

function updateQualityRange(quality) {
    const minSlider = document.getElementById(`${quality}-min`);
    const maxSlider = document.getElementById(`${quality}-max`);
    const minValue = document.getElementById(`${quality}-min-value`);
    const maxValue = document.getElementById(`${quality}-max-value`);

    if (!minSlider || !maxSlider || !minValue || !maxValue) return;

    let min = parseInt(minSlider.value);
    let max = parseInt(maxSlider.value);

    // Ensure min doesn't exceed max
    if (min > max) {
        min = max;
        minSlider.value = min;
    }

    // Ensure max doesn't go below min
    if (max < min) {
        max = min;
        maxSlider.value = max;
    }

    minValue.textContent = `${min} kbps`;
    maxValue.textContent = `${max} kbps`;
}

function toggleQuality(quality) {
    const checkbox = document.getElementById(`quality-${quality}-enabled`);
    const sliders = document.getElementById(`sliders-${quality}`);

    if (checkbox && sliders) {
        if (checkbox.checked) {
            sliders.classList.remove('disabled');
        } else {
            sliders.classList.add('disabled');
        }
    }

    // Also toggle FLAC bit depth selector
    if (quality === 'flac') {
        const bitDepthSelector = document.getElementById('flac-bit-depth-selector');
        if (bitDepthSelector && checkbox) {
            if (checkbox.checked) {
                bitDepthSelector.classList.remove('disabled');
            } else {
                bitDepthSelector.classList.add('disabled');
            }
        }
    }

    // Mark preset as custom when manually changing
    if (currentQualityProfile) {
        currentQualityProfile.preset = 'custom';
        document.querySelectorAll('.preset-button').forEach(btn => {
            btn.classList.remove('active');
        });
    }
}

function setFlacBitDepth(value) {
    document.querySelectorAll('.bit-depth-btn').forEach(btn => {
        btn.classList.toggle('active', btn.getAttribute('data-value') === value);
    });

    // Show/hide fallback toggle — only relevant when a specific bit depth is selected
    const fallbackToggle = document.getElementById('flac-fallback-toggle');
    if (fallbackToggle) {
        fallbackToggle.style.display = value === 'any' ? 'none' : 'block';
    }

    // Mark preset as custom when manually changing
    if (currentQualityProfile) {
        currentQualityProfile.preset = 'custom';
        document.querySelectorAll('.preset-button').forEach(btn => {
            btn.classList.remove('active');
        });
    }

    debouncedAutoSaveSettings();
}

function setFlacBitDepthFallback(enabled) {
    if (currentQualityProfile) {
        currentQualityProfile.preset = 'custom';
        document.querySelectorAll('.preset-button').forEach(btn => {
            btn.classList.remove('active');
        });
    }
    debouncedAutoSaveSettings();
}

async function applyQualityPreset(presetName) {
    try {
        showLoadingOverlay(`Applying ${presetName} preset...`);

        const response = await fetch(`/api/quality-profile/preset/${presetName}`, {
            method: 'POST'
        });

        const data = await response.json();

        if (data.success) {
            currentQualityProfile = data.profile;
            populateQualityProfileUI(currentQualityProfile);
            showToast(`Applied '${presetName}' preset`, 'success');
        } else {
            showToast(`Failed to apply preset: ${data.error}`, 'error');
        }
    } catch (error) {
        console.error('Error applying quality preset:', error);
        showToast('Failed to apply preset', 'error');
    } finally {
        hideLoadingOverlay();
    }
}

function collectQualityProfileFromUI() {
    const profile = {
        version: 2,
        preset: 'custom', // Will be overridden if a preset is active
        qualities: {},
        fallback_enabled: document.getElementById('quality-fallback-enabled')?.checked ?? true
    };

    const qualities = ['flac', 'mp3_320', 'mp3_256', 'mp3_192'];

    qualities.forEach((quality, index) => {
        const enabled = document.getElementById(`quality-${quality}-enabled`)?.checked || false;
        const minSlider = document.getElementById(`${quality}-min`);
        const maxSlider = document.getElementById(`${quality}-max`);

        // Preserve priority from the currently loaded profile instead of using array order
        const existingPriority = currentQualityProfile?.qualities?.[quality]?.priority ?? (index + 1);

        profile.qualities[quality] = {
            enabled: enabled,
            min_kbps: parseInt(minSlider?.value || 0),
            max_kbps: parseInt(maxSlider?.value || 99999),
            priority: existingPriority
        };

        // Add FLAC-specific bit_depth and fallback settings
        if (quality === 'flac') {
            const activeBtn = document.querySelector('.bit-depth-btn.active');
            profile.qualities[quality].bit_depth = activeBtn ? activeBtn.getAttribute('data-value') : 'any';
            const fallbackCb = document.getElementById('flac-bit-depth-fallback');
            profile.qualities[quality].bit_depth_fallback = fallbackCb ? fallbackCb.checked : true;
        }
    });

    // Check if current profile matches a preset
    if (currentQualityProfile && currentQualityProfile.preset !== 'custom') {
        profile.preset = currentQualityProfile.preset;
    }

    return profile;
}

async function saveQualityProfile() {
    try {
        const profile = collectQualityProfileFromUI();

        const response = await fetch('/api/quality-profile', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(profile)
        });

        const data = await response.json();

        if (data.success) {
            currentQualityProfile = profile;
            console.log('Quality profile saved successfully');
            return true;
        } else {
            console.error('Failed to save quality profile:', data.error);
            return false;
        }
    } catch (error) {
        console.error('Error saving quality profile:', error);
        return false;
    }
}

// ===============================
// END QUALITY PROFILE FUNCTIONS
// ===============================

async function toggleHydrabaseFromSettings() {
    const statusEl = document.getElementById('hydrabase-settings-status');
    const btn = document.getElementById('hydrabase-connect-btn');
    const url = document.getElementById('hydrabase-url').value.trim();
    const apiKey = document.getElementById('hydrabase-api-key').value.trim();

    if (!url || !apiKey) {
        if (statusEl) statusEl.textContent = 'URL and API Key required';
        return;
    }

    // Save settings first
    await saveSettings(true);

    try {
        // Check current status
        const statusRes = await fetch('/api/hydrabase/status');
        const statusData = await statusRes.json();

        if (statusData.connected) {
            // Disconnect
            await fetch('/api/hydrabase/disconnect', { method: 'POST' });
            if (btn) btn.textContent = 'Connect';
            if (statusEl) { statusEl.textContent = 'Disconnected'; statusEl.style.color = 'rgba(255,255,255,0.4)'; }
            // Remove from fallback dropdown + reset to iTunes if was selected
            const fbSel2 = document.getElementById('metadata-fallback-source');
            if (fbSel2) {
                const hbOpt = fbSel2.querySelector('option[value="hydrabase"]');
                if (hbOpt) {
                    if (fbSel2.value === 'hydrabase') fbSel2.value = 'itunes';
                    hbOpt.remove();
                }
            }
            showToast('Hydrabase disconnected', 'info');
        } else {
            // Connect
            const res = await fetch('/api/hydrabase/connect', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url, api_key: apiKey })
            });
            const data = await res.json();
            if (data.success) {
                if (btn) btn.textContent = 'Disconnect';
                if (statusEl) { statusEl.textContent = 'Connected'; statusEl.style.color = '#4caf50'; }
                // Add to fallback dropdown
                const fbSel = document.getElementById('metadata-fallback-source');
                if (fbSel && !fbSel.querySelector('option[value="hydrabase"]')) {
                    const opt = document.createElement('option');
                    opt.value = 'hydrabase';
                    opt.textContent = 'Hydrabase (P2P)';
                    fbSel.appendChild(opt);
                }
                showToast('Hydrabase connected', 'success');
            } else {
                if (statusEl) statusEl.textContent = data.error || 'Connection failed';
                showToast('Hydrabase connection failed', 'error');
            }
        }
    } catch (e) {
        if (statusEl) statusEl.textContent = 'Error';
        showToast('Hydrabase connection error', 'error');
    }
}

// ── Music Library Paths ──
function renderMusicPaths(paths) {
    const container = document.getElementById('music-paths-list');
    if (!container) return;
    if (!paths || paths.length === 0) {
        container.innerHTML = '<div style="color: rgba(255,255,255,0.3); font-size: 0.85em; padding: 4px 0;">No paths configured. Click "Add Path" to add your music folder(s).</div>';
        return;
    }
    container.innerHTML = paths.map((p, i) => `
        <div class="form-group music-path-row" style="margin-bottom: 4px;">
            <input type="text" class="music-path-input" value="${escapeHtml(p)}" placeholder="/music or C:\\Music" style="flex:1;">
            <button class="test-button" onclick="_removeMusicPathRow(this)" style="padding: 8px 12px; color: #ef5350; border-color: rgba(239,83,80,0.3);">&times;</button>
        </div>
    `).join('');
    // Attach auto-save to dynamically rendered inputs
    container.querySelectorAll('.music-path-input').forEach(input => {
        input.addEventListener('change', () => { if (typeof debouncedAutoSaveSettings === 'function') debouncedAutoSaveSettings(); });
    });
}

function addMusicPathRow() {
    const container = document.getElementById('music-paths-list');
    if (!container) return;
    // Clear the "no paths" message if present
    const placeholder = container.querySelector('div[style*="color: rgba"]');
    if (placeholder && !container.querySelector('.music-path-row')) placeholder.remove();
    const row = document.createElement('div');
    row.className = 'form-group music-path-row';
    row.style.marginBottom = '4px';
    row.innerHTML = `
        <input type="text" class="music-path-input" value="" placeholder="/music or C:\\Music" style="flex:1;">
        <button class="test-button" onclick="_removeMusicPathRow(this)" style="padding: 8px 12px; color: #ef5350; border-color: rgba(239,83,80,0.3);">&times;</button>
    `;
    container.appendChild(row);
    const input = row.querySelector('input');
    input.focus();
    // Auto-save when the user finishes typing a path
    input.addEventListener('change', () => { if (typeof debouncedAutoSaveSettings === 'function') debouncedAutoSaveSettings(); });
}

function _removeMusicPathRow(btn) {
    btn.closest('.music-path-row').remove();
    // Auto-save after removing a path
    if (typeof debouncedAutoSaveSettings === 'function') debouncedAutoSaveSettings();
}

function collectMusicPaths() {
    const inputs = document.querySelectorAll('.music-path-input');
    const paths = [];
    inputs.forEach(input => {
        const val = input.value.trim();
        if (val) paths.push(val);
    });
    return paths;
}

// ── Genre Whitelist ──
let _genreWhitelistCache = [];

function _genreWhitelistRender(genres) {
    _genreWhitelistCache = genres && genres.length ? genres : [];
    const container = document.getElementById('genre-whitelist-chips');
    const countEl = document.getElementById('genre-whitelist-count');
    if (!container) return;
    if (!_genreWhitelistCache.length) {
        container.innerHTML = '<div style="color:rgba(255,255,255,0.3);font-size:13px;padding:4px 0;">No genres configured. Click "Reset to Defaults" to load the default whitelist.</div>';
        if (countEl) countEl.textContent = '';
        return;
    }
    const searchVal = (document.getElementById('genre-whitelist-search')?.value || '').toLowerCase();
    const filtered = searchVal ? _genreWhitelistCache.filter(g => g.toLowerCase().includes(searchVal)) : _genreWhitelistCache;
    container.innerHTML = filtered.map(g =>
        `<span class="genre-chip">${escapeHtml(g)}<button class="genre-chip-x" onclick="_genreWhitelistRemove('${escapeHtml(g.replace(/'/g, "\\'"))}')">&times;</button></span>`
    ).join('');
    if (countEl) countEl.textContent = `${_genreWhitelistCache.length} genres`;
}

function _genreWhitelistRemove(genre) {
    _genreWhitelistCache = _genreWhitelistCache.filter(g => g !== genre);
    _genreWhitelistRender(_genreWhitelistCache);
    if (typeof debouncedAutoSaveSettings === 'function') debouncedAutoSaveSettings();
}

function _genreWhitelistAdd(genre) {
    genre = genre.trim();
    if (!genre) return;
    if (_genreWhitelistCache.some(g => g.toLowerCase() === genre.toLowerCase())) return;
    _genreWhitelistCache.push(genre);
    _genreWhitelistCache.sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase()));
    _genreWhitelistRender(_genreWhitelistCache);
    if (typeof debouncedAutoSaveSettings === 'function') debouncedAutoSaveSettings();
}

async function _genreWhitelistReset() {
    try {
        const resp = await fetch('/api/genre-whitelist/defaults');
        const data = await resp.json();
        if (data.genres) {
            _genreWhitelistCache = data.genres;
            _genreWhitelistRender(_genreWhitelistCache);
            if (typeof debouncedAutoSaveSettings === 'function') debouncedAutoSaveSettings();
            showToast(`Loaded ${data.genres.length} default genres`, 'success');
        }
    } catch (e) {
        showToast('Failed to load defaults', 'error');
    }
}

// Toggle whitelist container visibility + init
document.addEventListener('change', (e) => {
    if (e.target.id === 'genre-whitelist-enabled') {
        const container = document.getElementById('genre-whitelist-container');
        if (container) container.style.display = e.target.checked ? '' : 'none';
        // Auto-populate with defaults on first enable if empty
        if (e.target.checked && _genreWhitelistCache.length === 0) {
            _genreWhitelistReset();
        }
    }
});

// Search/add handler
document.addEventListener('keydown', (e) => {
    if (e.target.id === 'genre-whitelist-search' && e.key === 'Enter') {
        e.preventDefault();
        _genreWhitelistAdd(e.target.value);
        e.target.value = '';
    }
});
document.addEventListener('input', (e) => {
    if (e.target.id === 'genre-whitelist-search') {
        _genreWhitelistRender(_genreWhitelistCache);
    }
});

function _collectGenreWhitelist() {
    return _genreWhitelistCache;
}

// ── Live Log Viewer ──
let _logViewerActive = false;
let _logViewerFilter = '';
let _logViewerSource = 'app';
let _logViewerSearch = '';
const _LOG_MAX_LINES = 2000;

function _logClassify(line) {
    // Exact logger format first
    if (line.includes(' - DEBUG - ')) return 'DEBUG';
    if (line.includes(' - INFO - ')) return 'INFO';
    if (line.includes(' - WARNING - ')) return 'WARNING';
    if (line.includes(' - ERROR - ') || line.includes(' - CRITICAL - ')) return 'ERROR';
    // Heuristic for print() output
    const ll = line.toLowerCase();
    if (ll.includes('error') || ll.includes('traceback') || ll.includes('exception') || ll.includes('failed')) return 'ERROR';
    if (ll.includes('warning') || ll.includes('warn')) return 'WARNING';
    if (ll.includes('debug')) return 'DEBUG';
    return 'INFO';
}

function _logClassToCSS(level) {
    return { DEBUG: 'log-debug', INFO: 'log-info', WARNING: 'log-warning', ERROR: 'log-error' }[level] || 'log-plain';
}

async function _logViewerInit() {
    if (_logViewerActive) return;
    _logViewerActive = true;
    _logViewerSource = document.getElementById('log-viewer-source')?.value || 'app';

    // Fetch initial tail
    try {
        const params = new URLSearchParams({ source: _logViewerSource, lines: 300 });
        if (_logViewerFilter) params.set('level', _logViewerFilter);
        if (_logViewerSearch) params.set('search', _logViewerSearch);
        const resp = await fetch(`/api/logs/tail?${params}`);
        const data = await resp.json();
        if (data.lines) {
            const container = document.getElementById('log-viewer-lines');
            if (container) {
                container.innerHTML = '';
                _logViewerAppendLines(data.lines);
            }
        }
    } catch (e) {
        console.warn('Failed to load initial logs:', e);
    }

    // Subscribe to live updates
    if (typeof socket !== 'undefined' && socket && socket.connected) {
        socket.emit('logs:subscribe', { source: _logViewerSource });
        socket.on('logs:live', _logViewerOnLive);
    }
}

function _logViewerStop() {
    if (!_logViewerActive) return;
    _logViewerActive = false;
    if (typeof socket !== 'undefined' && socket) {
        socket.off('logs:live', _logViewerOnLive);
        socket.emit('logs:unsubscribe', {});
    }
}

function _logViewerOnLive(data) {
    if (!_logViewerActive || !data.lines) return;
    if (data.source !== _logViewerSource) return;
    let lines = data.lines;
    // Apply level filter client-side for live lines
    if (_logViewerFilter) {
        lines = lines.filter(l => _logClassify(l) === _logViewerFilter);
    }
    // Apply search filter
    if (_logViewerSearch) {
        const s = _logViewerSearch.toLowerCase();
        lines = lines.filter(l => l.toLowerCase().includes(s));
    }
    if (lines.length > 0) _logViewerAppendLines(lines);
}

function _logViewerAppendLines(lines) {
    const container = document.getElementById('log-viewer-lines');
    if (!container) return;
    const autoScroll = document.getElementById('log-viewer-autoscroll')?.checked;
    const terminal = document.getElementById('log-viewer-terminal');

    const frag = document.createDocumentFragment();
    for (const line of lines) {
        const div = document.createElement('div');
        div.className = 'log-line ' + _logClassToCSS(_logClassify(line));
        div.textContent = line;
        frag.appendChild(div);
    }
    container.appendChild(frag);

    // Trim old lines
    while (container.children.length > _LOG_MAX_LINES) {
        container.removeChild(container.firstChild);
    }

    // Update count
    const countEl = document.getElementById('log-viewer-line-count');
    if (countEl) countEl.textContent = `${container.children.length} lines`;

    // Auto-scroll
    if (autoScroll && terminal) {
        terminal.scrollTop = terminal.scrollHeight;
    }
}

async function _logViewerChangeSource() {
    _logViewerStop();
    _logViewerSource = document.getElementById('log-viewer-source')?.value || 'app';
    const container = document.getElementById('log-viewer-lines');
    if (container) container.innerHTML = '<div class="log-line log-info">Loading...</div>';
    await _logViewerInit();
}

function _logViewerFilterLevel(btn) {
    document.querySelectorAll('.log-filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    _logViewerFilter = btn.dataset.level || '';
    _logViewerReload();
}

let _logSearchDebounce = null;
function _logViewerOnSearch(input) {
    clearTimeout(_logSearchDebounce);
    _logSearchDebounce = setTimeout(() => {
        _logViewerSearch = (input.value || '').trim();
        _logViewerReload();
    }, 300);
}

function _logViewerReload() {
    _logViewerStop();
    const container = document.getElementById('log-viewer-lines');
    if (container) container.innerHTML = '<div class="log-line log-info">Loading...</div>';
    _logViewerInit();
}

function _logViewerCopy() {
    const container = document.getElementById('log-viewer-lines');
    if (!container) return;
    const text = Array.from(container.children).map(el => el.textContent).join('\n');
    if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(text).then(() => showToast('Logs copied', 'success'));
    } else {
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.cssText = 'position:fixed;left:-9999px';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
        showToast('Logs copied', 'success');
    }
}

function _logViewerClear() {
    const container = document.getElementById('log-viewer-lines');
    if (container) container.innerHTML = '';
    const countEl = document.getElementById('log-viewer-line-count');
    if (countEl) countEl.textContent = '0 lines';
}

// ── Database Maintenance ──
async function loadDbMaintenanceInfo() {
    try {
        const resp = await fetch('/api/database/maintenance/info');
        const data = await resp.json();
        if (!data.success) return;
        const sizeEl = document.getElementById('db-size-display');
        const freeEl = document.getElementById('db-freepages-display');
        const vacEl = document.getElementById('db-autovacuum-display');
        if (sizeEl) sizeEl.textContent = data.total_size_display;
        if (freeEl) freeEl.textContent = data.free_pages > 0
            ? `${data.free_pages.toLocaleString()} (${data.free_size_display} reclaimable)`
            : 'None — database is fully compacted';
        if (vacEl) vacEl.textContent = data.auto_vacuum_label;
        // Hide enable button if already incremental
        const incBtn = document.getElementById('db-incvacuum-btn');
        if (incBtn && data.auto_vacuum === 2) {
            incBtn.textContent = 'Incremental Vacuum Enabled';
            incBtn.disabled = true;
            incBtn.style.opacity = '0.5';
        }
    } catch (e) { console.error('Error loading DB maintenance info:', e); }
}

async function runDatabaseVacuum() {
    const btn = document.getElementById('db-vacuum-btn');
    const status = document.getElementById('db-vacuum-status');
    if (!confirm('This will compact the database by rewriting it. The database will be locked during this operation. For large databases this may take over a minute. Continue?')) return;
    btn.disabled = true;
    btn.textContent = 'Compacting...';
    if (status) { status.style.display = 'block'; status.style.background = 'rgba(255,255,255,0.04)'; status.style.color = 'rgba(255,255,255,0.6)'; status.textContent = 'Running VACUUM — this may take a while...'; }
    try {
        const resp = await fetch('/api/database/maintenance/vacuum', { method: 'POST' });
        const data = await resp.json();
        if (data.success) {
            showToast(`Database compacted in ${data.elapsed_seconds}s — saved ${data.saved_display}`, 'success');
            if (status) { status.style.color = '#4caf50'; status.textContent = `Done in ${data.elapsed_seconds}s. Saved ${data.saved_display}.`; }
            loadDbMaintenanceInfo();
        } else {
            showToast('Vacuum failed: ' + (data.error || 'Unknown error'), 'error');
            if (status) { status.style.color = '#ef5350'; status.textContent = 'Failed: ' + (data.error || 'Unknown error'); }
        }
    } catch (e) {
        showToast('Vacuum failed: ' + e.message, 'error');
        if (status) { status.style.color = '#ef5350'; status.textContent = 'Failed: ' + e.message; }
    } finally {
        btn.disabled = false;
        btn.textContent = 'Compact Database (VACUUM)';
    }
}

async function enableIncrementalVacuum() {
    const btn = document.getElementById('db-incvacuum-btn');
    const status = document.getElementById('db-vacuum-status');
    if (!confirm('This will enable incremental vacuum mode. It requires a one-time full VACUUM to activate, which locks the database and may take over a minute on large databases. Continue?')) return;
    btn.disabled = true;
    btn.textContent = 'Enabling...';
    if (status) { status.style.display = 'block'; status.style.background = 'rgba(255,255,255,0.04)'; status.style.color = 'rgba(255,255,255,0.6)'; status.textContent = 'Enabling incremental vacuum — this may take a while...'; }
    try {
        const resp = await fetch('/api/database/maintenance/enable-incremental-vacuum', { method: 'POST' });
        const data = await resp.json();
        if (data.success) {
            const msg = data.already_enabled ? 'Already enabled' : `Enabled in ${data.elapsed_seconds}s — saved ${data.saved_display}`;
            showToast(msg, 'success');
            if (status) { status.style.color = '#4caf50'; status.textContent = msg; }
            loadDbMaintenanceInfo();
        } else {
            showToast('Failed: ' + (data.error || 'Unknown error'), 'error');
            if (status) { status.style.color = '#ef5350'; status.textContent = 'Failed: ' + (data.error || 'Unknown error'); }
        }
    } catch (e) {
        showToast('Failed: ' + e.message, 'error');
        if (status) { status.style.color = '#ef5350'; status.textContent = 'Failed: ' + e.message; }
    } finally {
        btn.disabled = false;
        btn.textContent = 'Enable Incremental Vacuum';
    }
}

async function activateDevMode() {
    const password = document.getElementById('dev-mode-password').value;
    try {
        const response = await fetch('/api/dev-mode', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password })
        });
        const data = await response.json();
        if (data.success) {
            document.getElementById('dev-mode-status').textContent = 'Active';
            document.getElementById('dev-mode-status').style.color = 'rgb(var(--accent-light-rgb))';
            document.getElementById('hydrabase-nav').style.display = '';
            document.getElementById('hydrabase-button-container').style.display = '';
            document.getElementById('dev-mode-password').value = '';
            showToast('Dev mode activated', 'success');
        } else {
            showToast('Invalid password', 'error');
        }
    } catch (e) {
        showToast('Failed to activate dev mode', 'error');
    }
}

// ── Hydrabase Functions ──

let _hydrabaseConnected = false;

async function hydrabaseToggleConnection() {
    if (_hydrabaseConnected) {
        await hydrabaseDisconnect();
    } else {
        await hydrabaseConnect();
    }
}

async function hydrabaseConnect() {
    const url = document.getElementById('hydra-ws-url').value.trim();
    const apiKey = document.getElementById('hydra-api-key').value.trim();
    if (!url || !apiKey) {
        showToast('URL and API key required', 'error');
        return;
    }
    const statusEl = document.getElementById('hydra-connection-status');
    const btn = document.getElementById('hydra-connect-btn');
    statusEl.textContent = 'Connecting...';
    statusEl.style.color = '#f0ad4e';
    try {
        const response = await fetch('/api/hydrabase/connect', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url, api_key: apiKey })
        });
        const data = await response.json();
        if (data.success) {
            _hydrabaseConnected = true;
            statusEl.textContent = 'Connected';
            statusEl.style.color = 'rgb(var(--accent-light-rgb))';
            btn.textContent = 'Disconnect';
            showToast('Connected to Hydrabase', 'success');
        } else {
            statusEl.textContent = 'Failed';
            statusEl.style.color = '#f44336';
            showToast(data.error || 'Connection failed', 'error');
        }
    } catch (e) {
        statusEl.textContent = 'Error';
        statusEl.style.color = '#f44336';
        showToast('Connection error', 'error');
    }
}

async function hydrabaseDisconnect() {
    try {
        await fetch('/api/hydrabase/disconnect', { method: 'POST' });
    } catch (e) { }
    _hydrabaseConnected = false;
    document.getElementById('hydra-connection-status').textContent = 'Disconnected';
    document.getElementById('hydra-connection-status').style.color = '#888';
    document.getElementById('hydra-connect-btn').textContent = 'Connect';
    // Dev mode is disabled on disconnect — hide Hydrabase nav and update settings status
    document.getElementById('hydrabase-nav').style.display = 'none';
    document.getElementById('hydrabase-button-container').style.display = 'none';
    const devStatus = document.getElementById('dev-mode-status');
    if (devStatus) {
        devStatus.textContent = 'Inactive';
        devStatus.style.color = '#888';
    }
    showToast('Disconnected — dev mode disabled', 'success');
    navigateToPage('settings');
}

async function loadHydrabaseComparisons() {
    const container = document.getElementById('hydra-comparisons-container');
    if (!container) return;
    try {
        const response = await fetch('/api/hydrabase/comparisons');
        const data = await response.json();
        if (!data.success || !data.comparisons?.length) {
            container.innerHTML = '<p style="color: #666; font-size: 13px;">No comparisons yet. Search with Hydrabase active to generate comparisons.</p>';
            return;
        }
        let html = '';
        for (const comp of data.comparisons) {
            const time = new Date(comp.timestamp * 1000).toLocaleTimeString();
            html += `<div style="background: rgba(30, 30, 30, 0.6); border-radius: 8px; padding: 10px; margin-bottom: 8px;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                    <strong style="color: #fff;">"${comp.query}"</strong>
                    <span style="color: #666; font-size: 11px;">${time}</span>
                </div>
                <div style="display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; font-size: 12px;">
                    <div style="padding: 6px 8px; border-radius: 6px; background: rgba(99, 102, 241, 0.15); border: 1px solid rgba(99, 102, 241, 0.3);">
                        <div style="color: rgba(139, 92, 246, 1); font-weight: 600; margin-bottom: 2px;">Hydrabase</div>
                        <div style="color: #aaa;">${comp.hydrabase?.tracks || 0}T / ${comp.hydrabase?.artists || 0}A / ${comp.hydrabase?.albums || 0}Al</div>
                    </div>
                    <div style="padding: 6px 8px; border-radius: 6px; background: rgba(29, 185, 84, 0.15); border: 1px solid rgba(29, 185, 84, 0.3);">
                        <div style="color: rgb(var(--accent-light-rgb)); font-weight: 600; margin-bottom: 2px;">Spotify</div>
                        <div style="color: #aaa;">${comp.spotify?.tracks || 0}T / ${comp.spotify?.artists || 0}A / ${comp.spotify?.albums || 0}Al</div>
                    </div>
                    <div style="padding: 6px 8px; border-radius: 6px; background: rgba(251, 93, 93, 0.15); border: 1px solid rgba(251, 93, 93, 0.3);">
                        <div style="color: #fb5d5d; font-weight: 600; margin-bottom: 2px;">${comp.fallback_source === 'deezer' ? 'Deezer' : 'iTunes'}</div>
                        <div style="color: #aaa;">${(comp.fallback || comp.itunes)?.tracks || 0}T / ${(comp.fallback || comp.itunes)?.artists || 0}A / ${(comp.fallback || comp.itunes)?.albums || 0}Al</div>
                    </div>
                </div>
            </div>`;
        }
        container.innerHTML = html;
    } catch (e) {
        container.innerHTML = '<p style="color: #f44336; font-size: 13px;">Failed to load comparisons.</p>';
    }
}

async function hydrabaseSendRaw(textareaId) {
    const textarea = document.getElementById(textareaId);
    const raw = textarea.value.trim();
    if (!raw) {
        showToast('Payload is empty', 'error');
        return;
    }
    if (!_hydrabaseConnected) {
        showToast('Not connected to Hydrabase', 'error');
        return;
    }
    let payload;
    try {
        payload = JSON.parse(raw);
    } catch (e) {
        showToast('Invalid JSON payload', 'error');
        return;
    }
    // Auto-inject a fresh nonce if not set or zero
    if (!payload.nonce) {
        payload.nonce = Date.now();
    }
    const responseArea = document.getElementById('hydra-response');
    responseArea.textContent = 'Sending...';
    try {
        const response = await fetch('/api/hydrabase/send', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ payload })
        });
        const data = await response.json();
        if (data.success) {
            responseArea.textContent = JSON.stringify(data.data, null, 2);
        } else {
            responseArea.textContent = 'Error: ' + (data.error || 'Unknown error');
            if (data.error && data.error.includes('Not connected')) {
                _hydrabaseConnected = false;
                document.getElementById('hydra-connection-status').textContent = 'Disconnected';
                document.getElementById('hydra-connection-status').style.color = '#888';
                document.getElementById('hydra-connect-btn').textContent = 'Connect';
            }
        }
    } catch (e) {
        responseArea.textContent = 'Error: ' + e.message;
    }
}

// ── Tag embedding accordion helpers ──
function toggleTagGroup(header) {
    const body = header.nextElementSibling;
    const arrow = header.querySelector('.tag-group-arrow');
    if (body.style.display === 'none') {
        body.style.display = 'block';
        arrow.classList.add('open');
    } else {
        body.style.display = 'none';
        arrow.classList.remove('open');
    }
}

function toggleServiceTags(masterCheckbox, serviceName) {
    const group = masterCheckbox.closest('.tag-service-group');
    if (!group) return;
    const body = group.querySelector('.tag-service-body');
    if (!body) return;
    const childCheckboxes = body.querySelectorAll('input[type="checkbox"]');
    childCheckboxes.forEach(cb => {
        const label = cb.closest('.checkbox-label');
        if (masterCheckbox.checked) {
            if (label) label.classList.remove('disabled-tag');
            cb.disabled = false;
        } else {
            if (label) label.classList.add('disabled-tag');
            cb.disabled = true;
        }
    });
}

function _collectServiceTags(serviceName) {
    const tags = {};
    document.querySelectorAll(`[data-config^="${serviceName}.tags."]`).forEach(cb => {
        const key = cb.dataset.config.split('.').pop();
        tags[key] = cb.checked;
    });
    return tags;
}

function _getTagConfig(path) {
    const el = document.querySelector(`[data-config="${path}"]`);
    return el ? el.checked : true;
}

async function saveSettings(quiet = false) {
    // Validate file organization templates before saving
    const validationErrors = validateFileOrganizationTemplates();
    if (validationErrors.length > 0) {
        if (!quiet) showToast('Template validation failed: ' + validationErrors.join(', '), 'error');
        return;
    }

    // Determine active server from toggle buttons
    let activeServer = 'plex';
    if (document.getElementById('jellyfin-toggle').classList.contains('active')) {
        activeServer = 'jellyfin';
    } else if (document.getElementById('navidrome-toggle').classList.contains('active')) {
        activeServer = 'navidrome';
    } else if (document.getElementById('soulsync-toggle')?.classList.contains('active')) {
        activeServer = 'soulsync';
    }

    const settings = {
        active_media_server: activeServer,
        spotify: {
            client_id: document.getElementById('spotify-client-id').value,
            client_secret: document.getElementById('spotify-client-secret').value,
            redirect_uri: document.getElementById('spotify-redirect-uri').value,
            embed_tags: document.getElementById('embed-spotify').checked,
            tags: _collectServiceTags('spotify')
        },
        tidal: {
            client_id: document.getElementById('tidal-client-id').value,
            client_secret: document.getElementById('tidal-client-secret').value,
            redirect_uri: document.getElementById('tidal-redirect-uri').value,
            embed_tags: document.getElementById('embed-tidal').checked,
            tags: _collectServiceTags('tidal')
        },
        plex: {
            base_url: document.getElementById('plex-url').value,
            token: document.getElementById('plex-token').value
        },
        jellyfin: {
            base_url: document.getElementById('jellyfin-url').value,
            api_key: document.getElementById('jellyfin-api-key').value,
            api_timeout: parseInt(document.getElementById('jellyfin-timeout').value) || 30
        },
        navidrome: {
            base_url: document.getElementById('navidrome-url').value,
            username: document.getElementById('navidrome-username').value,
            password: document.getElementById('navidrome-password').value
        },
        soulseek: {
            slskd_url: document.getElementById('soulseek-url').value,
            api_key: document.getElementById('soulseek-api-key').value,
            download_path: document.getElementById('download-path').value,
            transfer_path: document.getElementById('transfer-path').value,
            search_timeout: parseInt(document.getElementById('soulseek-search-timeout').value) || 60,
            search_timeout_buffer: parseInt(document.getElementById('soulseek-search-timeout-buffer').value) || 15,
            min_peer_upload_speed: parseInt(document.getElementById('soulseek-min-peer-speed').value) || 0,
            max_peer_queue: parseInt(document.getElementById('soulseek-max-peer-queue').value) || 0,
            download_timeout: (parseInt(document.getElementById('soulseek-download-timeout').value) || 10) * 60,
            auto_clear_searches: document.getElementById('soulseek-auto-clear-searches').checked
        },
        listenbrainz: {
            base_url: document.getElementById('listenbrainz-base-url').value,
            token: document.getElementById('listenbrainz-token').value,
            scrobble_enabled: document.getElementById('listenbrainz-scrobble-enabled').checked,
        },
        acoustid: {
            api_key: document.getElementById('acoustid-api-key').value,
            enabled: document.getElementById('acoustid-enabled').checked
        },
        lastfm: {
            api_key: document.getElementById('lastfm-api-key').value,
            api_secret: document.getElementById('lastfm-api-secret').value,
            scrobble_enabled: document.getElementById('lastfm-scrobble-enabled').checked,
            embed_tags: document.getElementById('embed-lastfm').checked,
            tags: _collectServiceTags('lastfm')
        },
        genius: {
            access_token: document.getElementById('genius-access-token').value,
            embed_tags: document.getElementById('embed-genius').checked,
            tags: _collectServiceTags('genius')
        },
        itunes: {
            country: document.getElementById('itunes-country').value || 'US',
            embed_tags: document.getElementById('embed-itunes').checked,
            tags: _collectServiceTags('itunes')
        },
        discogs: {
            token: document.getElementById('discogs-token').value,
        },
        metadata: {
            fallback_source: document.getElementById('metadata-fallback-source').value || 'itunes'
        },
        hydrabase: {
            url: document.getElementById('hydrabase-url').value,
            api_key: document.getElementById('hydrabase-api-key').value,
            auto_connect: document.getElementById('hydrabase-auto-connect').checked
        },
        download_source: {
            mode: document.getElementById('download-source-mode').value,
            hybrid_primary: document.getElementById('hybrid-primary-source').value,
            hybrid_secondary: document.getElementById('hybrid-secondary-source').value,
            hybrid_order: getHybridOrder(),
            stream_source: document.getElementById('stream-source').value,
            max_concurrent: parseInt(document.getElementById('max-concurrent-downloads').value) || 3,
        },
        tidal_download: {
            quality: document.getElementById('tidal-download-quality').value || 'lossless',
            allow_fallback: document.getElementById('tidal-allow-fallback').checked,
        },
        hifi_download: {
            quality: document.getElementById('hifi-download-quality').value || 'lossless',
            allow_fallback: document.getElementById('hifi-allow-fallback').checked,
        },
        deezer_download: {
            quality: document.getElementById('deezer-download-quality').value || 'flac',
            arl: document.getElementById('deezer-download-arl').value || '',
            allow_fallback: document.getElementById('deezer-allow-fallback').checked,
        },
        lidarr_download: {
            url: document.getElementById('lidarr-url').value || '',
            api_key: document.getElementById('lidarr-api-key').value || '',
        },
        qobuz: {
            quality: document.getElementById('qobuz-quality').value || 'lossless',
            embed_tags: document.getElementById('embed-qobuz').checked,
            tags: _collectServiceTags('qobuz'),
            allow_fallback: document.getElementById('qobuz-allow-fallback').checked,
        },
        database: {
            max_workers: parseInt(document.getElementById('max-workers').value)
        },
        metadata_enhancement: {
            enabled: document.getElementById('metadata-enabled').checked,
            embed_album_art: document.getElementById('embed-album-art').checked,
            cover_art_download: document.getElementById('cover-art-download').checked,
            prefer_caa_art: document.getElementById('prefer-caa-art').checked,
            lrclib_enabled: document.getElementById('lrclib-enabled').checked,
            tags: {
                quality_tag: _getTagConfig('metadata_enhancement.tags.quality_tag'),
                genre_merge: _getTagConfig('metadata_enhancement.tags.genre_merge'),
                artist_separator: document.getElementById('artist-separator').value,
                write_multi_artist: document.getElementById('write-multi-artist').checked,
                feat_in_title: document.getElementById('feat-in-title').checked
            }
        },
        musicbrainz: {
            embed_tags: document.getElementById('embed-musicbrainz').checked,
            tags: _collectServiceTags('musicbrainz')
        },
        deezer: {
            app_id: document.getElementById('deezer-app-id').value,
            app_secret: document.getElementById('deezer-app-secret').value,
            redirect_uri: document.getElementById('deezer-redirect-uri').value,
            embed_tags: document.getElementById('embed-deezer').checked,
            tags: _collectServiceTags('deezer')
        },
        audiodb: {
            embed_tags: document.getElementById('embed-audiodb').checked,
            tags: _collectServiceTags('audiodb')
        },
        file_organization: {
            enabled: document.getElementById('file-organization-enabled').checked,
            disc_label: document.getElementById('disc-label').value,
            collab_artist_mode: document.getElementById('collab-artist-mode').value,
            templates: {
                album_path: document.getElementById('template-album-path').value,
                single_path: document.getElementById('template-single-path').value,
                playlist_path: document.getElementById('template-playlist-path').value,
                video_path: document.getElementById('template-video-path').value
            }
        },
        wishlist: {
            allow_duplicate_tracks: document.getElementById('allow-duplicate-tracks').checked
        },
        playlist_sync: {
            create_backup: document.getElementById('create-backup').checked
        },
        content_filter: {
            allow_explicit: document.getElementById('allow-explicit').checked
        },
        genre_whitelist: {
            enabled: document.getElementById('genre-whitelist-enabled').checked,
            genres: _collectGenreWhitelist(),
        },
        post_processing: {
            replaygain_enabled: document.getElementById('replaygain-enabled').checked,
        },
        library: {
            music_paths: collectMusicPaths(),
            music_videos_path: document.getElementById('music-videos-path').value || './MusicVideos'
        },
        import: {
            replace_lower_quality: document.getElementById('import-replace-lower-quality').checked,
            staging_path: document.getElementById('staging-path').value || './Staging'
        },
        lossy_copy: {
            enabled: document.getElementById('lossy-copy-enabled').checked,
            codec: document.getElementById('lossy-copy-codec').value,
            bitrate: document.getElementById('lossy-copy-bitrate').value,
            delete_original: document.getElementById('lossy-copy-delete-original').checked,
            downsample_hires: document.getElementById('downsample-hires').checked
        },
        listening_stats: {
            enabled: document.getElementById('listening-stats-enabled').checked,
            poll_interval: parseInt(document.getElementById('listening-stats-interval').value) || 30,
        },
        m3u_export: {
            enabled: document.getElementById('m3u-export-enabled').checked,
            entry_base_path: document.getElementById('m3u-entry-base-path').value || ''
        },
        ui_appearance: {
            accent_preset: document.getElementById('accent-preset')?.value || '#1db954',
            accent_color: document.getElementById('accent-custom-color')?.value || '#1db954',
            sidebar_visualizer: document.getElementById('sidebar-visualizer-type')?.value || 'bars',
            particles_enabled: document.getElementById('particles-enabled')?.checked !== false,
            worker_orbs_enabled: document.getElementById('worker-orbs-enabled')?.checked !== false,
            reduce_effects: document.getElementById('reduce-effects-enabled')?.checked === true
        },
        youtube: {
            cookies_browser: document.getElementById('youtube-cookies-browser').value,
            download_delay: parseInt(document.getElementById('youtube-download-delay').value) || 3,
        },
        security: {
            require_pin_on_launch: document.getElementById('security-require-pin')?.checked || false,
            cors_origins: document.getElementById('security-cors-origins')?.value?.trim() || '',
        }
    };

    // Validate cors_origins entries — backend silently filters malformed
    // values, so warn the user up-front if any line doesn't look like a
    // URL (or the special '*' token). One-shot toast; doesn't block save.
    const corsRaw = settings.security.cors_origins;
    if (corsRaw) {
        const entries = corsRaw.replace(/\n/g, ',').split(',')
            .map(s => s.trim())
            .filter(s => s);
        const invalid = entries.filter(e => {
            if (e === '*') return false;
            // Accept scheme://host[:port] only — no path, query, or fragment.
            // Engineio compares Origin against {scheme}://{host} exactly.
            return !/^https?:\/\/[^\s/?#]+$/i.test(e);
        });
        if (invalid.length) {
            showToast(
                `Allowed Origins: ${invalid.length} entr${invalid.length === 1 ? 'y looks' : 'ies look'} malformed (need full URL like https://soulsync.example.com, no trailing slash). Saving anyway — they\'ll be ignored.`,
                'warning'
            );
        }
    }

    try {
        if (!quiet) showLoadingOverlay('Saving settings...');

        // Save main settings
        const response = await fetch(API.settings, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(settings)
        });

        const result = await response.json();

        // Save quality profile
        const qualityProfileSaved = await saveQualityProfile();

        // Save discovery lookback period
        let lookbackSaved = true;
        try {
            const lookbackPeriod = document.getElementById('discovery-lookback-period').value;
            const lookbackResponse = await fetch('/api/discovery/lookback-period', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ period: lookbackPeriod })
            });
            const lookbackResult = await lookbackResponse.json();
            lookbackSaved = lookbackResult.success === true;
        } catch (error) {
            console.error('Error saving discovery lookback period:', error);
            lookbackSaved = false;
        }

        // Save hemisphere setting
        try {
            const hemisphere = document.getElementById('discovery-hemisphere').value;
            await fetch('/api/discovery/hemisphere', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ hemisphere })
            });
        } catch (error) {
            console.error('Error saving hemisphere setting:', error);
        }

        if (result.success && qualityProfileSaved && lookbackSaved) {
            showToast(quiet ? 'Settings auto-saved' : 'Settings saved successfully', 'success');
            _forceServiceStatusRefresh();
            _stgRefreshAfterSave();
        } else if (result.success && qualityProfileSaved && !lookbackSaved) {
            showToast('Settings saved, but discovery lookback period failed to save', 'warning');
            _forceServiceStatusRefresh();
            _stgRefreshAfterSave();
        } else if (result.success && !qualityProfileSaved) {
            showToast('Settings saved, but quality profile failed to save', 'warning');
            _forceServiceStatusRefresh();
            _stgRefreshAfterSave();
        } else {
            showToast(`Failed to save settings: ${result.error}`, 'error', 'set-services');
        }
    } catch (error) {
        console.error('Error saving settings:', error);
        showToast('Failed to save settings', 'error', 'set-services');
    } finally {
        if (!quiet) hideLoadingOverlay();
    }
}

async function authorizeLastfmScrobbling() {
    try {
        // Save settings first so API secret is stored
        await saveSettings();
        const resp = await fetch('/api/lastfm/auth-url');
        const data = await resp.json();
        if (data.success && data.url) {
            window.open(data.url, '_blank', 'width=600,height=500');
            showToast('Authorize SoulSync in the Last.fm window that opened', 'info');
        } else {
            showToast(data.error || 'Could not generate auth URL', 'error');
        }
    } catch (e) {
        showToast('Failed to start Last.fm authorization', 'error');
    }
}

async function testConnection(service) {
    try {
        showLoadingOverlay(`Testing ${service} connection...`);

        const response = await fetch(API.testConnection, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ service })
        });

        const result = await response.json();

        if (result.success) {
            // Use backend's message which contains dynamic source name
            showToast(result.message || `${service} connection successful`, 'success');

            // Load music libraries after successful connection
            if (service === 'plex') {
                loadPlexMusicLibraries();
            } else if (service === 'jellyfin') {
                loadJellyfinUsers().then(() => loadJellyfinMusicLibraries());
            } else if (service === 'navidrome') {
                loadNavidromeMusicFolders();
            }
        } else {
            showToast(`${service} connection failed: ${result.error}`, 'error', 'gs-connecting');
        }
    } catch (error) {
        console.error(`Error testing ${service} connection:`, error);
        showToast(`Failed to test ${service} connection`, 'error', 'gs-connecting');
    } finally {
        hideLoadingOverlay();
    }
}

async function clearQuarantine() {
    if (!await showConfirmDialog({ title: 'Clear Quarantine', message: 'Delete all files in the quarantine folder? This cannot be undone.', confirmText: 'Delete', destructive: true })) return;
    try {
        showLoadingOverlay('Clearing quarantine folder...');
        const response = await fetch('/api/quarantine/clear', { method: 'POST' });
        const result = await response.json();
        if (result.success) {
            showToast(result.message || 'Quarantine cleared', 'success');
        } else {
            showToast(`Failed to clear quarantine: ${result.error}`, 'error');
        }
    } catch (error) {
        console.error('Error clearing quarantine:', error);
        showToast('Failed to clear quarantine', 'error');
    } finally {
        hideLoadingOverlay();
    }
}

// ======================== API Key Management ========================

async function loadApiKeys() {
    const container = document.getElementById('api-keys-list');
    if (!container) return;

    try {
        const response = await fetch('/api/v1/api-keys-internal');
        if (response.ok) {
            const data = await response.json();
            renderApiKeys(data.data?.keys || []);
        } else {
            container.innerHTML = '<div style="color: #666; font-size: 13px;">No API keys configured.</div>';
        }
    } catch (e) {
        container.innerHTML = '<div style="color: #666; font-size: 13px;">No API keys configured.</div>';
    }
}

function renderApiKeys(keys) {
    const container = document.getElementById('api-keys-list');
    if (!container) return;

    if (!keys || keys.length === 0) {
        container.innerHTML = '<div style="color: #666; font-size: 13px; padding: 4px 0;">No API keys yet. Generate one below.</div>';
        return;
    }

    container.innerHTML = keys.map(k => `
        <div style="display: flex; align-items: center; justify-content: space-between; padding: 8px 10px; margin-bottom: 4px; background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.06); border-radius: 6px;">
            <div style="flex: 1; min-width: 0;">
                <div style="font-size: 13px; color: #e0e0e0; font-weight: 500;">${k.label || 'Unnamed'}</div>
                <div style="font-size: 11px; color: #666; margin-top: 2px;">
                    <code>${k.key_prefix || 'sk_...'}...</code>
                    &middot; Created ${k.created_at ? new Date(k.created_at).toLocaleDateString() : 'unknown'}
                    ${k.last_used_at ? '&middot; Last used ' + new Date(k.last_used_at).toLocaleDateString() : ''}
                </div>
            </div>
            <button onclick="revokeApiKey('${k.id}', '${(k.label || 'this key').replace(/'/g, "\\'")}')"
                style="padding: 4px 10px; background: rgba(255,82,82,0.1); border: 1px solid rgba(255,82,82,0.2); color: #ff5252; border-radius: 4px; cursor: pointer; font-size: 11px; white-space: nowrap;">
                Revoke
            </button>
        </div>
    `).join('');
}

async function generateApiKey() {
    const labelInput = document.getElementById('api-key-label');
    const label = labelInput ? labelInput.value.trim() : '';

    try {
        const response = await fetch('/api/v1/api-keys-internal/generate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ label: label || 'Default' })
        });
        const data = await response.json();

        if (data.success && data.data?.key) {
            const keyDisplay = document.getElementById('api-key-generated');
            const keyValue = document.getElementById('api-key-value');
            if (keyDisplay && keyValue) {
                keyValue.textContent = data.data.key;
                keyDisplay.style.display = 'block';
            }
            if (labelInput) labelInput.value = '';
            showToast('API key generated! Copy it now.', 'success');
            loadApiKeys();
        } else {
            showToast(data.error?.message || 'Failed to generate API key', 'error');
        }
    } catch (error) {
        console.error('Error generating API key:', error);
        showToast('Failed to generate API key', 'error');
    }
}

function copyApiKey() {
    const keyValue = document.getElementById('api-key-value');
    if (keyValue) {
        navigator.clipboard.writeText(keyValue.textContent).then(() => {
            showToast('API key copied to clipboard', 'success');
        }).catch(() => {
            // Fallback for older browsers
            const range = document.createRange();
            range.selectNode(keyValue);
            window.getSelection().removeAllRanges();
            window.getSelection().addRange(range);
            document.execCommand('copy');
            showToast('API key copied', 'success');
        });
    }
}

async function revokeApiKey(keyId, label) {
    if (!await showConfirmDialog({ title: 'Revoke API Key', message: `Revoke API key "${label}"? Any apps using this key will stop working.`, confirmText: 'Revoke', destructive: true })) return;

    try {
        const response = await fetch(`/api/v1/api-keys-internal/revoke/${keyId}`, { method: 'DELETE' });
        const data = await response.json();
        if (data.success) {
            showToast('API key revoked', 'success');
            loadApiKeys();
        } else {
            showToast(data.error?.message || 'Failed to revoke key', 'error');
        }
    } catch (error) {
        console.error('Error revoking API key:', error);
        showToast('Failed to revoke key', 'error');
    }
}

// Dashboard-specific test functions that create activity items
async function testDashboardConnection(service) {
    try {
        showLoadingOverlay(`Testing ${service} service...`);

        const response = await fetch(API.testDashboardConnection, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ service })
        });

        const result = await response.json();

        if (result.success) {
            // Use backend's message which contains dynamic source name
            showToast(result.message || `${service} service verified`, 'success');
            // Refresh status indicators immediately so UI reflects the new state
            fetchAndUpdateServiceStatus();
        } else {
            showToast(`${service} service check failed: ${result.error}`, 'error');
        }
    } catch (error) {
        console.error(`Error testing ${service} service:`, error);
        showToast(`Failed to test ${service} service`, 'error');
    } finally {
        hideLoadingOverlay();
    }
}

// Individual Auto-detect functions - same as GUI
async function autoDetectPlex() {
    try {
        showLoadingOverlay('Auto-detecting Plex server...');

        const response = await fetch('/api/detect-media-server', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ server_type: 'plex' })
        });

        const result = await response.json();

        if (result.success) {
            document.getElementById('plex-url').value = result.found_url;
            showToast(`Plex server detected: ${result.found_url}`, 'success');
        } else {
            showToast(result.error, 'error');
        }

    } catch (error) {
        console.error('Error auto-detecting Plex:', error);
        showToast('Failed to auto-detect Plex server', 'error');
    } finally {
        hideLoadingOverlay();
    }
}

async function autoDetectJellyfin() {
    try {
        showLoadingOverlay('Auto-detecting Jellyfin server...');

        const response = await fetch('/api/detect-media-server', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ server_type: 'jellyfin' })
        });

        const result = await response.json();

        if (result.success) {
            document.getElementById('jellyfin-url').value = result.found_url;
            showToast(`Jellyfin server detected: ${result.found_url}`, 'success');
        } else {
            showToast(result.error, 'error');
        }

    } catch (error) {
        console.error('Error auto-detecting Jellyfin:', error);
        showToast('Failed to auto-detect Jellyfin server', 'error');
    } finally {
        hideLoadingOverlay();
    }
}

async function autoDetectNavidrome() {
    try {
        showLoadingOverlay('Auto-detecting Navidrome server...');

        const response = await fetch('/api/detect-media-server', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ server_type: 'navidrome' })
        });

        const result = await response.json();

        if (result.success) {
            document.getElementById('navidrome-url').value = result.found_url;
            showToast(`Navidrome server detected: ${result.found_url}`, 'success');
        } else {
            showToast(result.error, 'error');
        }

    } catch (error) {
        console.error('Error auto-detecting Navidrome:', error);
        showToast('Failed to auto-detect Navidrome server', 'error');
    } finally {
        hideLoadingOverlay();
    }
}

async function autoDetectSlskd() {
    try {
        showLoadingOverlay('Auto-detecting Soulseek (slskd) server...');

        const response = await fetch('/api/detect-soulseek', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });

        const result = await response.json();

        if (result.success) {
            document.getElementById('soulseek-url').value = result.found_url;
            showToast(`Soulseek server detected: ${result.found_url}`, 'success');
        } else {
            showToast(result.error, 'error');
        }

    } catch (error) {
        console.error('Error auto-detecting Soulseek:', error);
        showToast('Failed to auto-detect Soulseek server', 'error');
    } finally {
        hideLoadingOverlay();
    }
}


function cancelDetection(service) {
    const progressDiv = document.getElementById(`${service}-detection-progress`);
    progressDiv.classList.add('hidden');
    showToast(`${service} detection cancelled`, 'error');
}

function updateStatusDisplays() {
    // Update status displays based on current service status
    // This would be called after status updates
    const services = ['spotify', 'media-server', 'soulseek'];
    services.forEach(service => {
        const display = document.getElementById(`${service}-status-display`);
        if (display) {
            // Status will be updated by the regular status monitoring
        }
    });
}

async function authenticateSpotify() {
    try {
        showLoadingOverlay('Saving credentials and starting Spotify authentication...');
        // Save settings first to ensure client_id/client_secret are persisted
        await saveSettings();
        showToast('Spotify authentication started', 'success');
        window.open('/auth/spotify', '_blank');
    } catch (error) {
        console.error('Error authenticating Spotify:', error);
        showToast('Failed to start Spotify authentication', 'error', 'gs-connecting');
    } finally {
        hideLoadingOverlay();
    }
}

async function disconnectSpotify() {
    const fallbackName = currentMusicSourceName !== 'Spotify' ? currentMusicSourceName : 'the configured fallback source';
    if (!await showConfirmDialog({ title: 'Disconnect Spotify', message: `Disconnect Spotify? The app will switch to ${fallbackName} for metadata.` })) {
        return;
    }
    try {
        showLoadingOverlay('Disconnecting Spotify...');
        const response = await fetch('/api/spotify/disconnect', { method: 'POST' });
        const data = await response.json();
        if (data.success) {
            showToast(`Spotify disconnected. Now using ${fallbackName}.`, 'success');
            // Immediately refresh status to update UI
            await fetchAndUpdateServiceStatus();
        } else {
            showToast(`Failed to disconnect: ${data.error}`, 'error');
        }
    } catch (error) {
        console.error('Error disconnecting Spotify:', error);
        showToast('Failed to disconnect Spotify', 'error');
    } finally {
        hideLoadingOverlay();
    }
}

async function clearSpotifyCacheAndFallback() {
    const fallbackName = currentMusicSourceName !== 'Spotify' ? currentMusicSourceName : 'the configured fallback source';
    if (!await showConfirmDialog({
        title: 'Clear Spotify Cache',
        message: `This will clear the Spotify token cache and switch metadata to ${fallbackName}. You can re-authenticate later.`
    })) return;
    try {
        showLoadingOverlay('Clearing Spotify cache...');
        const response = await fetch('/api/spotify/disconnect', { method: 'POST' });
        const data = await response.json();
        if (data.success) {
            showToast(data.message || `Switched to ${fallbackName}`, 'success');
            await fetchAndUpdateServiceStatus();
        } else {
            showToast(`Failed: ${data.error}`, 'error');
        }
    } catch (error) {
        showToast('Failed to clear Spotify cache', 'error');
    } finally {
        hideLoadingOverlay();
    }
}

// ── Spotify Rate Limit Handling ───────────────────────────────────────────
let _spotifyRateLimitShown = false;
let _spotifyInCooldown = false;
let _rateLimitModalOpen = false;
let _rateLimitCountdownInterval = null;
let _rateLimitExpiresAt = 0;

function handleSpotifyRateLimit(rateLimitInfo) {
    if (!rateLimitInfo || !rateLimitInfo.active) {
        if (_spotifyRateLimitShown) {
            _spotifyRateLimitShown = false;
            closeRateLimitModal();
            showToast('Spotify access restored', 'success');
            // Refresh discover page if user is on it — data source switched back to Spotify
            if (currentPage === 'discover') {
                console.log('Spotify restored — refreshing discover page data');
                loadDiscoverPage();
            }
        }
        return;
    }
    // Update countdown if modal is open (status pushes every 10s keep it accurate)
    if (_rateLimitModalOpen && rateLimitInfo.remaining_seconds) {
        _rateLimitExpiresAt = Date.now() + (rateLimitInfo.remaining_seconds * 1000);
    }
    if (!_spotifyRateLimitShown) {
        _spotifyRateLimitShown = true;
        _spotifyInCooldown = false;
        showRateLimitModal(rateLimitInfo);
        // Refresh discover page if user is on it — data source switched to iTunes
        if (currentPage === 'discover') {
            console.log('Spotify rate limited — refreshing discover page with iTunes data');
            loadDiscoverPage();
        }
    }
}

function showRateLimitModal(rateLimitInfo) {
    const overlay = document.getElementById('rate-limit-modal-overlay');
    if (!overlay) return;

    // Populate details
    const banDuration = document.getElementById('rate-limit-ban-duration');
    const endpoint = document.getElementById('rate-limit-endpoint');
    const countdown = document.getElementById('rate-limit-countdown');

    banDuration.textContent = formatRateLimitDuration(rateLimitInfo.retry_after || rateLimitInfo.remaining_seconds);
    endpoint.textContent = rateLimitInfo.endpoint || 'unknown';
    countdown.textContent = formatRateLimitDuration(rateLimitInfo.remaining_seconds);

    // Set expiry for live countdown
    _rateLimitExpiresAt = Date.now() + (rateLimitInfo.remaining_seconds * 1000);

    // Start live countdown timer
    if (_rateLimitCountdownInterval) clearInterval(_rateLimitCountdownInterval);
    _rateLimitCountdownInterval = setInterval(() => {
        const remaining = Math.max(0, Math.round((_rateLimitExpiresAt - Date.now()) / 1000));
        countdown.textContent = formatRateLimitDuration(remaining);
        if (remaining <= 0) {
            clearInterval(_rateLimitCountdownInterval);
            _rateLimitCountdownInterval = null;
        }
    }, 1000);

    overlay.classList.remove('hidden');
    _rateLimitModalOpen = true;
}

function closeRateLimitModal() {
    const overlay = document.getElementById('rate-limit-modal-overlay');
    if (overlay) overlay.classList.add('hidden');
    if (_rateLimitCountdownInterval) {
        clearInterval(_rateLimitCountdownInterval);
        _rateLimitCountdownInterval = null;
    }
    _rateLimitModalOpen = false;
}

async function disconnectSpotifyFromRateLimit() {
    closeRateLimitModal();
    try {
        showLoadingOverlay('Disconnecting Spotify...');
        const response = await fetch('/api/spotify/disconnect', { method: 'POST' });
        const data = await response.json();
        if (data.success) {
            _spotifyRateLimitShown = false;
            showToast(`Spotify disconnected. Now using ${currentMusicSourceName}.`, 'success');
            await fetchAndUpdateServiceStatus();
            if (currentPage === 'discover') {
                loadDiscoverPage();
            }
        } else {
            showToast(`Failed to disconnect: ${data.error}`, 'error');
        }
    } catch (error) {
        console.error('Error disconnecting Spotify:', error);
        showToast('Failed to disconnect Spotify', 'error');
    } finally {
        hideLoadingOverlay();
    }
}

function formatRateLimitDuration(seconds) {
    if (!seconds || seconds <= 0) return '0s';
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = seconds % 60;
    if (h > 0) return `${h}h ${m}m`;
    if (m > 0) return `${m}m ${s}s`;
    return `${s}s`;
}

async function authenticateTidal() {
    try {
        showLoadingOverlay('Saving credentials and starting Tidal authentication...');
        // Save settings first to ensure credentials are persisted
        await saveSettings();
        showToast('Tidal authentication started', 'success');
        window.open('/auth/tidal', '_blank');
    } catch (error) {
        console.error('Error authenticating Tidal:', error);
        showToast('Failed to start Tidal authentication', 'error');
    } finally {
        hideLoadingOverlay();
    }
}

async function authenticateDeezer() {
    try {
        showLoadingOverlay('Saving credentials and starting Deezer authentication...');
        await saveSettings();
        showToast('Deezer authentication started', 'success');
        window.open('/auth/deezer', '_blank');
    } catch (error) {
        console.error('Error authenticating Deezer:', error);
        showToast('Failed to start Deezer authentication', 'error');
    } finally {
        hideLoadingOverlay();
    }
}

// ===== Tidal Download Auth (Device Flow) =====

async function testHiFiConnection() {
    const statusEl = document.getElementById('hifi-connection-status');
    const btn = document.getElementById('hifi-test-btn');
    if (!statusEl) return;
    statusEl.textContent = 'Checking...';
    statusEl.style.color = '#aaa';
    try {
        const resp = await fetch('/api/hifi/status');
        const data = await resp.json();
        if (data.available) {
            statusEl.textContent = `Connected (v${data.version || '?'})`;
            statusEl.style.color = '#4caf50';
        } else {
            statusEl.textContent = 'No instances reachable';
            statusEl.style.color = '#ff9800';
        }
    } catch (e) {
        statusEl.textContent = 'Connection error';
        statusEl.style.color = '#f44336';
    }
}

async function testLidarrConnection() {
    const statusEl = document.getElementById('lidarr-connection-status');
    if (!statusEl) return;
    statusEl.textContent = 'Checking...';
    statusEl.style.color = '#aaa';
    try {
        // Save settings first so the backend has the URL/key
        await saveSettings();
        const resp = await fetch('/api/test-connection', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ service: 'lidarr' })
        });
        const data = await resp.json();
        if (data.success) {
            statusEl.textContent = 'Connected';
            statusEl.style.color = '#4caf50';
        } else {
            statusEl.textContent = data.error || 'Connection failed';
            statusEl.style.color = '#f44336';
        }
    } catch (e) {
        statusEl.textContent = 'Connection error';
        statusEl.style.color = '#f44336';
    }
}

async function loadHiFiInstances() {
    const listEl = document.getElementById('hifi-instances-list');
    if (!listEl) return;
    try {
        const resp = await fetch('/api/hifi/instances/list');
        const data = await resp.json();
        if (!data.instances || data.instances.length === 0) {
            listEl.innerHTML = '<div style="color: rgba(255,255,255,0.4); font-size: 0.85em;">No instances configured.</div>';
            return;
        }
        listEl.innerHTML = data.instances.map((inst, i) => {
            const enabledClass = inst.enabled ? '' : 'opacity:0.4;';
            const checkHtml = inst.enabled
                ? `<span style="color:#4caf50;cursor:pointer;" onclick="toggleHiFiInstance('${escapeHtml(inst.url)}')" title="Click to disable">&#x2714;</span>`
                : `<span style="color:#666;cursor:pointer;" onclick="toggleHiFiInstance('${escapeHtml(inst.url)}')" title="Click to enable">&#x2718;</span>`;
            return `<div style="display:flex;align-items:center;gap:8px;padding:4px 0;font-size:0.82em;${enabledClass}">
                <span style="color:rgba(255,255,255,0.4);cursor:default;user-select:none;">&#x2630;</span>
                <span style="flex:1;color:rgba(255,255,255,0.7);font-family:monospace;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${escapeHtml(inst.url)}</span>
                ${checkHtml}
                <span style="color:#f44336;cursor:pointer;font-size:0.9em;" onclick="removeHiFiInstance('${escapeHtml(inst.url)}')" title="Remove instance">&#x2716;</span>
            </div>`;
        }).join('');
    } catch (e) {
        listEl.innerHTML = `<div style="color:#f44336;font-size:0.85em;">Error loading instances: ${escapeHtml(e.message)}</div>`;
    }
}

async function addHiFiInstance() {
    const input = document.getElementById('hifi-new-instance');
    if (!input) return;
    const url = input.value.trim();
    if (!url) return;
    if (!url.startsWith('http://') && !url.startsWith('https://')) {
        alert('URL must start with http:// or https://');
        return;
    }
    try {
        const resp = await fetch('/api/hifi/instances', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url })
        });
        const data = await resp.json();
        if (data.success) {
            input.value = '';
            loadHiFiInstances();
        } else {
            alert(data.error || 'Failed to add instance');
        }
    } catch (e) {
        alert(`Error: ${e.message}`);
    }
}

async function removeHiFiInstance(url) {
    try {
        const resp = await fetch(`/api/hifi/instances/${encodeURIComponent(url)}`, {
            method: 'DELETE'
        });
        const data = await resp.json();
        if (data.success) {
            loadHiFiInstances();
        } else {
            alert(data.error || 'Failed to remove instance');
        }
    } catch (e) {
        alert(`Error: ${e.message}`);
    }
}

async function checkHiFiInstances() {
    const panel = document.getElementById('hifi-instances-status-panel');
    const btn = document.getElementById('hifi-instances-check-btn');
    if (!panel) return;
    panel.style.display = 'block';
    panel.innerHTML = '<div style="color: rgba(255,255,255,0.4); font-size: 0.85em; padding: 8px 0;">Checking instances...</div>';
    if (btn) { btn.disabled = true; btn.textContent = 'Checking...'; }
    try {
        const resp = await fetch('/api/hifi/instances');
        const data = await resp.json();
        if (!data.instances || data.instances.length === 0) {
            panel.innerHTML = '<div style="color: #ff9800; font-size: 0.85em;">No instances configured.</div>';
            return;
        }
        const _statusIcon = (inst) => {
            if (inst.can_download) return '<span style="color:#4caf50">● Download</span>';
            if (inst.can_search) return '<span style="color:#ff9800">● Search only</span>';
            if (inst.status === 'online') return '<span style="color:#ff9800">● Online (limited)</span>';
            if (inst.status === 'ssl_error') return '<span style="color:#f44336">● SSL error</span>';
            if (inst.status === 'timeout') return '<span style="color:#f44336">● Timeout</span>';
            if (inst.status === 'offline') return '<span style="color:#f44336">● Offline</span>';
            return `<span style="color:#f44336">● ${escapeHtml(inst.status)}</span>`;
        };
        panel.innerHTML = data.instances.map(inst => {
            const isActive = inst.url === data.active;
            const ver = inst.version ? ` v${inst.version}` : '';
            const activeTag = isActive ? ' <span style="color:rgb(var(--accent-rgb));font-weight:600;font-size:0.75em;">(ACTIVE)</span>' : '';
            return `<div style="display:flex;justify-content:space-between;align-items:center;padding:5px 0;border-bottom:1px solid rgba(255,255,255,0.04);font-size:0.82em;">
                <span style="color:rgba(255,255,255,0.6);font-family:monospace;overflow:hidden;text-overflow:ellipsis;">${escapeHtml(inst.url)}${ver}${activeTag}</span>
                <span style="flex-shrink:0;margin-left:12px;">${_statusIcon(inst)}</span>
            </div>`;
        }).join('');
    } catch (e) {
        panel.innerHTML = `<div style="color:#f44336;font-size:0.85em;">Error checking instances: ${escapeHtml(e.message)}</div>`;
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = 'Check All Instances'; }
    }
}

async function testDeezerDownloadConnection() {
    const statusEl = document.getElementById('deezer-download-status');
    if (!statusEl) return;
    statusEl.textContent = 'Checking...';
    statusEl.style.color = '#aaa';
    try {
        // Save the ARL first so the backend can use it
        const arl = document.getElementById('deezer-download-arl')?.value || '';
        if (!arl) {
            statusEl.textContent = 'No ARL token provided';
            statusEl.style.color = '#ff9800';
            return;
        }
        const resp = await fetch('/api/deezer-download/test', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ arl }),
        });
        const data = await resp.json();
        if (data.success) {
            statusEl.textContent = `Connected as ${data.user || 'Unknown'} (${data.tier || 'Free'})`;
            statusEl.style.color = '#4caf50';
        } else {
            statusEl.textContent = data.error || 'Authentication failed';
            statusEl.style.color = '#f44336';
        }
    } catch (e) {
        statusEl.textContent = 'Connection error';
        statusEl.style.color = '#f44336';
    }
}

async function checkTidalDownloadAuthStatus() {
    const statusEl = document.getElementById('tidal-download-auth-status');
    const btn = document.getElementById('tidal-download-auth-btn');
    try {
        const resp = await fetch('/api/tidal/download/auth/status');
        const data = await resp.json();
        if (data.authenticated) {
            statusEl.textContent = 'Authenticated';
            statusEl.style.color = '#4caf50';
            btn.textContent = 'Re-link Tidal Account';
        } else {
            statusEl.textContent = 'Not authenticated';
            statusEl.style.color = '#ff9800';
            btn.textContent = 'Link Tidal Account';
        }
    } catch (e) {
        statusEl.textContent = '';
    }
}

let _tidalAuthPollTimer = null;

async function startTidalDownloadAuth() {
    const btn = document.getElementById('tidal-download-auth-btn');
    const statusEl = document.getElementById('tidal-download-auth-status');
    const codeEl = document.getElementById('tidal-download-auth-code');

    btn.disabled = true;
    btn.textContent = 'Starting...';
    statusEl.textContent = '';

    try {
        const resp = await fetch('/api/tidal/download/auth/start', { method: 'POST' });
        const data = await resp.json();

        if (!resp.ok || !data.success) {
            throw new Error(data.error || 'Failed to start auth');
        }

        // Show the link/code to the user
        const uri = data.verification_uri || '';
        const code = data.user_code || '';
        codeEl.style.display = 'block';
        codeEl.innerHTML = `Go to <a href="${uri}" target="_blank" style="color:rgb(var(--accent-rgb));">${uri}</a> and enter code: <strong>${code}</strong>`;
        btn.textContent = 'Waiting for approval...';
        statusEl.textContent = 'Waiting...';
        statusEl.style.color = '#ff9800';

        // Poll for completion
        if (_tidalAuthPollTimer) clearInterval(_tidalAuthPollTimer);
        _tidalAuthPollTimer = setInterval(async () => {
            try {
                const checkResp = await fetch('/api/tidal/download/auth/check');
                const checkData = await checkResp.json();

                if (checkData.status === 'completed') {
                    clearInterval(_tidalAuthPollTimer);
                    _tidalAuthPollTimer = null;
                    codeEl.style.display = 'none';
                    statusEl.textContent = 'Authenticated';
                    statusEl.style.color = '#4caf50';
                    btn.disabled = false;
                    btn.textContent = 'Re-link Tidal Account';
                    showToast('Tidal download account linked successfully', 'success');
                } else if (checkData.status === 'error') {
                    clearInterval(_tidalAuthPollTimer);
                    _tidalAuthPollTimer = null;
                    codeEl.style.display = 'none';
                    statusEl.textContent = 'Auth failed';
                    statusEl.style.color = '#f44336';
                    btn.disabled = false;
                    btn.textContent = 'Link Tidal Account';
                    showToast('Tidal auth failed: ' + (checkData.message || 'Unknown error'), 'error');
                }
                // status === 'pending' — keep polling
            } catch (pollErr) {
                console.error('Tidal auth poll error:', pollErr);
            }
        }, 3000);

    } catch (error) {
        console.error('Tidal download auth error:', error);
        showToast('Failed to start Tidal auth: ' + error.message, 'error');
        btn.disabled = false;
        btn.textContent = 'Link Tidal Account';
        codeEl.style.display = 'none';
    }
}

// ===============================
// QOBUZ AUTH FUNCTIONS
// ===============================

async function checkQobuzAuthStatus() {
    try {
        const resp = await fetch('/api/qobuz/auth/status');
        const data = await resp.json();

        // Update downloads tab section
        const formEl = document.getElementById('qobuz-auth-form');
        const loggedInEl = document.getElementById('qobuz-auth-logged-in');
        const userInfoEl = document.getElementById('qobuz-auth-user-info');

        // Update connections tab section
        const connFormEl = document.getElementById('qobuz-connection-form');
        const connLoggedInEl = document.getElementById('qobuz-connection-logged-in');
        const connUserInfoEl = document.getElementById('qobuz-connection-user-info');

        if (data.authenticated) {
            const user = data.user || {};
            const label = `Connected: ${user.display_name || 'Qobuz User'} (${user.subscription || 'Active'})`;

            if (userInfoEl) { userInfoEl.textContent = label; }
            if (loggedInEl) loggedInEl.style.display = 'flex';
            if (formEl) formEl.style.display = 'none';

            if (connUserInfoEl) { connUserInfoEl.textContent = label; }
            if (connLoggedInEl) connLoggedInEl.style.display = 'flex';
            if (connFormEl) connFormEl.style.display = 'none';
        } else {
            if (loggedInEl) loggedInEl.style.display = 'none';
            if (formEl) formEl.style.display = 'block';

            if (connLoggedInEl) connLoggedInEl.style.display = 'none';
            if (connFormEl) connFormEl.style.display = 'block';
        }
    } catch (e) {
        console.error('Qobuz auth status check failed:', e);
    }
}

async function loginQobuzFromConnections() {
    const btn = document.getElementById('qobuz-connection-login-btn');
    const statusEl = document.getElementById('qobuz-connection-status');
    const email = document.getElementById('qobuz-connection-email').value.trim();
    const password = document.getElementById('qobuz-connection-password').value;

    if (!email || !password) {
        showToast('Please enter your Qobuz email and password', 'warning');
        return;
    }

    btn.disabled = true;
    btn.textContent = 'Connecting...';
    statusEl.textContent = '';

    try {
        const resp = await fetch('/api/qobuz/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, password }),
        });
        const data = await resp.json();

        if (data.success) {
            showToast('Qobuz connected successfully!', 'success');
            document.getElementById('qobuz-connection-password').value = '';
            checkQobuzAuthStatus();
        } else {
            statusEl.textContent = data.error || 'Login failed';
            statusEl.style.color = '#ff5555';
            showToast(data.error || 'Qobuz login failed', 'error');
        }
    } catch (error) {
        console.error('Qobuz login error:', error);
        statusEl.textContent = 'Connection error';
        statusEl.style.color = '#ff5555';
        showToast('Failed to connect to Qobuz', 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = 'Connect Qobuz';
    }
}

async function loginQobuzWithToken() {
    const btn = document.getElementById('qobuz-token-login-btn');
    const statusEl = document.getElementById('qobuz-token-status');
    const token = document.getElementById('qobuz-connection-token').value.trim();

    if (!token) {
        showToast('Please paste your Qobuz auth token', 'warning');
        return;
    }

    btn.disabled = true;
    btn.textContent = 'Connecting...';
    if (statusEl) statusEl.textContent = '';

    try {
        const resp = await fetch('/api/qobuz/auth/token', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ token }),
        });
        const data = await resp.json();

        if (data.success) {
            showToast('Qobuz connected via token!', 'success');
            document.getElementById('qobuz-connection-token').value = '';
            checkQobuzAuthStatus();
        } else {
            if (statusEl) { statusEl.textContent = data.error || 'Token login failed'; statusEl.style.color = '#ff5555'; }
            showToast(data.error || 'Qobuz token login failed', 'error');
        }
    } catch (error) {
        console.error('Qobuz token login error:', error);
        if (statusEl) { statusEl.textContent = 'Connection error'; statusEl.style.color = '#ff5555'; }
        showToast('Failed to connect to Qobuz', 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = 'Connect with Token';
    }
}

async function loginQobuzWithTokenFromDownloads() {
    const btn = document.getElementById('qobuz-download-token-btn');
    const statusEl = document.getElementById('qobuz-download-token-status');
    const token = document.getElementById('qobuz-download-token').value.trim();

    if (!token) {
        showToast('Please paste your Qobuz auth token', 'warning');
        return;
    }

    btn.disabled = true;
    btn.textContent = 'Connecting...';
    if (statusEl) statusEl.textContent = '';

    try {
        const resp = await fetch('/api/qobuz/auth/token', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ token }),
        });
        const data = await resp.json();

        if (data.success) {
            showToast('Qobuz connected via token!', 'success');
            document.getElementById('qobuz-download-token').value = '';
            checkQobuzAuthStatus();
        } else {
            if (statusEl) { statusEl.textContent = data.error || 'Token login failed'; statusEl.style.color = '#ff5555'; }
            showToast(data.error || 'Qobuz token login failed', 'error');
        }
    } catch (error) {
        console.error('Qobuz token login error:', error);
        if (statusEl) { statusEl.textContent = 'Connection error'; statusEl.style.color = '#ff5555'; }
        showToast('Failed to connect to Qobuz', 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = 'Connect with Token';
    }
}

async function loginQobuz() {
    const btn = document.getElementById('qobuz-login-btn');
    const statusEl = document.getElementById('qobuz-auth-status');
    const email = document.getElementById('qobuz-email').value.trim();
    const password = document.getElementById('qobuz-password').value;

    if (!email || !password) {
        showToast('Please enter your Qobuz email and password', 'warning');
        return;
    }

    btn.disabled = true;
    btn.textContent = 'Connecting...';
    statusEl.textContent = '';

    try {
        const resp = await fetch('/api/qobuz/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, password }),
        });

        const data = await resp.json();

        if (data.success) {
            showToast('Qobuz connected successfully!', 'success');
            // Clear password field
            document.getElementById('qobuz-password').value = '';
            checkQobuzAuthStatus();
        } else {
            statusEl.textContent = data.error || 'Login failed';
            statusEl.style.color = '#ff5555';
            showToast(data.error || 'Qobuz login failed', 'error');
        }
    } catch (error) {
        console.error('Qobuz login error:', error);
        statusEl.textContent = 'Connection error';
        statusEl.style.color = '#ff5555';
        showToast('Failed to connect to Qobuz', 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = 'Connect Qobuz';
    }
}

async function logoutQobuz() {
    try {
        await fetch('/api/qobuz/auth/logout', { method: 'POST' });
        showToast('Qobuz disconnected', 'success');
        checkQobuzAuthStatus();
    } catch (e) {
        console.error('Qobuz logout error:', e);
    }
}

const PATH_INPUT_IDS = {
    download: 'download-path',
    transfer: 'transfer-path',
    staging: 'staging-path',
    'music-videos': 'music-videos-path',
    'm3u-entry-base': 'm3u-entry-base-path'
};

function togglePathLock(pathType, btn) {
    const input = document.getElementById(PATH_INPUT_IDS[pathType]);
    if (!input) return;
    const isLocked = input.hasAttribute('readonly');
    if (isLocked) {
        input.removeAttribute('readonly');
        input.focus();
        btn.textContent = 'Lock';
        btn.classList.remove('locked');
    } else {
        input.setAttribute('readonly', '');
        btn.textContent = 'Unlock';
        btn.classList.add('locked');
    }
}


// ===============================

