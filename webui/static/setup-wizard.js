// ============================================
// SoulSync Setup Wizard — First-Run Experience
// ============================================

const WIZARD_STEPS = ['welcome', 'metadata', 'download-source', 'paths', 'watchlist', 'first-download', 'done'];
let _wizardStep = 0;
let _wizardSettings = {
    metadata_source: 'deezer',
    download_source: 'soulseek',
    slskd_url: '',
    slskd_api_key: '',
    download_path: '/app/downloads',
    transfer_path: '/app/Transfer',
    media_server: 'none',
    server_url: '',
    server_token: '',
    server_user: '',
    server_pass: '',
    server_api_key: '',
    // Tidal/Qobuz/Deezer download creds
    tidal_client_id: '',
    tidal_client_secret: '',
    qobuz_quality: 'lossless',
    deezer_arl: '',
};
let _wizardAddedArtists = [];  // [{id, name, image}]
let _wizardDownloadedTrack = null;
let _wizardSearchTimeout = null;
let _wizardPathLocks = { download: true, transfer: true };

// ---- Open / Close ----

function openSetupWizard() {
    const overlay = document.getElementById('setup-wizard-overlay');
    if (!overlay) return;
    overlay.style.display = 'flex';
    _wizardStep = 0;
    _wizardSettings = {
        metadata_source: 'deezer',
        download_source: 'soulseek',
        slskd_url: '',
        slskd_api_key: '',
        download_path: '/app/downloads',
        transfer_path: '/app/Transfer',
        media_server: 'none',
        server_url: '',
        server_token: '',
        server_user: '',
        server_pass: '',
        server_api_key: '',
        tidal_client_id: '',
        tidal_client_secret: '',
        qobuz_quality: 'lossless',
        deezer_arl: '',
    };
    _wizardAddedArtists = [];
    _wizardDownloadedTrack = null;
    _wizardPathLocks = { download: true, transfer: true };
    _renderWizard();
}

function closeSetupWizard() {
    const overlay = document.getElementById('setup-wizard-overlay');
    if (overlay) overlay.style.display = 'none';

    // Mark as complete so it doesn't show again (server + client)
    localStorage.setItem('soulsync_setup_complete', 'true');
    fetch('/api/setup/complete', { method: 'POST' }).catch(() => {});

    // Continue app initialization if wizard was shown on first run
    if (typeof window._onSetupWizardComplete === 'function') {
        window._onSetupWizardComplete();
        window._onSetupWizardComplete = null;
    }
}

// ---- Navigation ----

function wizardNext() {
    if (!_validateWizardStep()) return;

    // Save settings for the current step before advancing
    _saveWizardStepSettings();

    if (_wizardStep < WIZARD_STEPS.length - 1) {
        _wizardStep++;
        _renderWizard();
    }
}

function wizardBack() {
    if (_wizardStep > 0) {
        _wizardStep--;
        _renderWizard();
    }
}

function wizardSkipStep() {
    if (_wizardStep < WIZARD_STEPS.length - 1) {
        _wizardStep++;
        _renderWizard();
    }
}

// ---- Validation ----

function _validateWizardStep() {
    const step = WIZARD_STEPS[_wizardStep];
    if (step === 'download-source' && _wizardSettings.download_source === 'soulseek') {
        if (!_wizardSettings.slskd_url || !_wizardSettings.slskd_api_key) {
            if (typeof showToast === 'function') showToast('Please fill in the slskd URL and API key', 'error');
            return false;
        }
    }
    return true;
}

// ---- Save settings per step to backend (same as settings page) ----

async function _saveWizardStepSettings() {
    const step = WIZARD_STEPS[_wizardStep];
    const settings = {};

    if (step === 'metadata') {
        settings.metadata = { fallback_source: _wizardSettings.metadata_source };
    } else if (step === 'download-source') {
        settings.download_source = { mode: _wizardSettings.download_source };
        if (_wizardSettings.download_source === 'soulseek' || _wizardSettings.slskd_url) {
            settings.soulseek = {
                slskd_url: _wizardSettings.slskd_url,
                api_key: _wizardSettings.slskd_api_key,
            };
        }
        if (_wizardSettings.download_source === 'tidal') {
            settings.tidal = {
                client_id: _wizardSettings.tidal_client_id,
                client_secret: _wizardSettings.tidal_client_secret,
            };
        }
        if (_wizardSettings.download_source === 'deezer_dl') {
            settings.deezer_download = { arl: _wizardSettings.deezer_arl };
        }
    } else if (step === 'paths') {
        settings.soulseek = Object.assign(settings.soulseek || {}, {
            download_path: _wizardSettings.download_path,
            transfer_path: _wizardSettings.transfer_path,
        });
        if (_wizardSettings.media_server !== 'none') {
            settings.active_media_server = _wizardSettings.media_server;
            if (_wizardSettings.media_server === 'plex') {
                settings.plex = { base_url: _wizardSettings.server_url, token: _wizardSettings.server_token };
            } else if (_wizardSettings.media_server === 'jellyfin') {
                settings.jellyfin = { base_url: _wizardSettings.server_url, api_key: _wizardSettings.server_api_key };
            } else if (_wizardSettings.media_server === 'navidrome') {
                settings.navidrome = { base_url: _wizardSettings.server_url, username: _wizardSettings.server_user, password: _wizardSettings.server_pass };
            }
        }
    }

    if (Object.keys(settings).length > 0) {
        try {
            await fetch('/api/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(settings)
            });
        } catch (e) {
            console.error('Wizard step save error:', e);
        }
    }
}

// ---- Main Render ----

function _renderWizard() {
    const container = document.getElementById('setup-wizard-content');
    if (!container) return;

    // Update stepper
    const stepper = document.getElementById('setup-wizard-stepper');
    if (stepper) {
        stepper.innerHTML = WIZARD_STEPS.map((s, i) => {
            const dotClass = i === _wizardStep ? 'active' : (i < _wizardStep ? 'completed' : '');
            const lineClass = i < _wizardStep ? 'completed' : '';
            let html = `<div class="setup-step-dot ${dotClass}"></div>`;
            if (i < WIZARD_STEPS.length - 1) html += `<div class="setup-step-line ${lineClass}"></div>`;
            return html;
        }).join('');
    }

    // Render step content
    const step = WIZARD_STEPS[_wizardStep];
    switch (step) {
        case 'welcome': _renderWelcome(container); break;
        case 'metadata': _renderMetadata(container); break;
        case 'download-source': _renderDownloadSource(container); break;
        case 'paths': _renderPaths(container); break;
        case 'watchlist': _renderWatchlist(container); break;
        case 'first-download': _renderFirstDownload(container); break;
        case 'done': _renderDone(container); break;
    }
}

// ---- Step 1: Welcome ----

function _renderWelcome(el) {
    el.innerHTML = `
        <div class="setup-card">
            <img src="/static/trans2.png" alt="SoulSync" class="setup-welcome-logo">
            <h2>Welcome to SoulSync</h2>
            <p class="setup-welcome-tagline">Intelligent Music Discovery & Automation</p>
            <div class="setup-feature-list">
                <div class="setup-feature-item">
                    <div class="setup-feature-icon">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
                    </div>
                    <span class="setup-feature-text">Search and download music from 6 sources — Soulseek, YouTube, Tidal, Qobuz, HiFi, and Deezer</span>
                </div>
                <div class="setup-feature-item">
                    <div class="setup-feature-icon">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="16 3 21 3 21 8"/><line x1="4" y1="20" x2="21" y2="3"/></svg>
                    </div>
                    <span class="setup-feature-text">Mirror playlists from Spotify, Tidal, Deezer, YouTube, Beatport, and ListenBrainz</span>
                </div>
                <div class="setup-feature-item">
                    <div class="setup-feature-icon">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polygon points="16.24 7.76 14.12 14.12 7.76 16.24 9.88 9.88"/></svg>
                    </div>
                    <span class="setup-feature-text">Watch artists and auto-download new releases as they drop</span>
                </div>
                <div class="setup-feature-item">
                    <div class="setup-feature-icon">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>
                    </div>
                    <span class="setup-feature-text">Organize your library and serve to Plex, Jellyfin, or Navidrome</span>
                </div>
                <div class="setup-feature-item">
                    <div class="setup-feature-icon">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
                    </div>
                    <span class="setup-feature-text">Build automations that scan, download, and notify on your schedule</span>
                </div>
            </div>
            <p style="text-align:center;font-size:0.82rem;color:rgba(255,255,255,0.35);margin-bottom:20px;">This wizard will walk you through the essentials. Everything can be changed later in Settings.</p>
            <button class="setup-btn setup-btn-primary setup-btn-big" onclick="wizardNext()">Get Started</button>
            <button class="setup-skip-link" onclick="closeSetupWizard()">Skip Setup</button>
        </div>
    `;
}

// ---- Step 2: Metadata Source ----

function _renderMetadata(el) {
    const sources = [
        { id: 'deezer', name: 'Deezer', badge: 'Recommended', desc: 'No authentication required. Rich metadata with album art.' },
        { id: 'spotify', name: 'Spotify', badge: '', desc: 'Requires API credentials. Best for playlist sync.' },
        { id: 'itunes', name: 'iTunes', badge: '', desc: 'No authentication required. Apple Music catalog.' },
    ];

    el.innerHTML = `
        <div class="setup-card">
            <h2>Metadata Source</h2>
            <p class="setup-subtitle">Where should SoulSync look up track info, album art, and metadata?</p>
            <div class="setup-info-box">
                <strong>What is a metadata source?</strong> When you search for music or sync a playlist, SoulSync needs a catalog to look up track names, artists, album art, track numbers, and release dates. This source provides that information — it does not affect where music is downloaded from.
            </div>
            <div class="setup-option-grid" style="grid-template-columns: 1fr 1fr 1fr;">
                ${sources.map(s => `
                    <div class="setup-option-card ${_wizardSettings.metadata_source === s.id ? 'selected' : ''}"
                         onclick="_wizardSelectMetadata('${s.id}')">
                        <div class="setup-option-name">${s.name}</div>
                        ${s.badge ? `<div class="setup-option-badge">${s.badge}</div>` : ''}
                        <div class="setup-option-desc">${s.desc}</div>
                    </div>
                `).join('')}
            </div>
            <div class="setup-btn-row">
                <button class="setup-btn setup-btn-secondary" onclick="wizardBack()">Back</button>
                <button class="setup-btn setup-btn-primary" onclick="wizardNext()">Next</button>
            </div>
        </div>
    `;
}

function _wizardSelectMetadata(id) {
    _wizardSettings.metadata_source = id;
    _renderWizard();
}

// ---- Step 3: Download Source ----

function _renderDownloadSource(el) {
    const sources = [
        { id: 'soulseek', name: 'Soulseek', desc: 'P2P network via slskd. Best quality and selection.', needsConfig: true },
        { id: 'youtube', name: 'YouTube', desc: 'No setup required. Good availability.', needsConfig: false },
        { id: 'hifi', name: 'HiFi', desc: 'No setup required. Lossless quality.', needsConfig: false },
        { id: 'tidal', name: 'Tidal', desc: 'Requires Tidal credentials. Lossless streaming.', needsConfig: true },
        { id: 'qobuz', name: 'Qobuz', desc: 'No setup required. Hi-res audio.', needsConfig: false },
        { id: 'deezer_dl', name: 'Deezer', desc: 'Requires ARL token. FLAC downloads.', needsConfig: true },
    ];

    const sel = _wizardSettings.download_source;

    // Build inline config based on selected source
    let inlineConfig = '';
    if (sel === 'soulseek') {
        inlineConfig = `
            <div class="setup-inline-config visible">
                <div class="setup-input-group">
                    <label>slskd URL</label>
                    <input class="setup-input" type="url" id="setup-slskd-url"
                           placeholder="http://localhost:5030"
                           value="${_escHtml(_wizardSettings.slskd_url)}"
                           oninput="_wizardSettings.slskd_url = this.value">
                </div>
                <div class="setup-input-group">
                    <label>slskd API Key</label>
                    <input class="setup-input" type="password" id="setup-slskd-key"
                           placeholder="Your slskd API key"
                           value="${_escHtml(_wizardSettings.slskd_api_key)}"
                           oninput="_wizardSettings.slskd_api_key = this.value">
                </div>
                <button class="setup-test-btn" id="setup-test-slskd" onclick="_wizardTestConnection('soulseek')">Test Connection</button>
            </div>`;
    } else if (sel === 'tidal') {
        inlineConfig = `
            <div class="setup-inline-config visible">
                <div class="setup-input-group">
                    <label>Tidal Client ID</label>
                    <input class="setup-input" type="text" placeholder="Client ID"
                           value="${_escHtml(_wizardSettings.tidal_client_id)}"
                           oninput="_wizardSettings.tidal_client_id = this.value">
                </div>
                <div class="setup-input-group">
                    <label>Tidal Client Secret</label>
                    <input class="setup-input" type="password" placeholder="Client Secret"
                           value="${_escHtml(_wizardSettings.tidal_client_secret)}"
                           oninput="_wizardSettings.tidal_client_secret = this.value">
                </div>
                <button class="setup-test-btn" onclick="_wizardTestConnection('tidal')">Test Connection</button>
            </div>`;
    } else if (sel === 'deezer_dl') {
        inlineConfig = `
            <div class="setup-inline-config visible">
                <div class="setup-input-group">
                    <label>Deezer ARL Token</label>
                    <input class="setup-input" type="password" placeholder="Your Deezer ARL token"
                           value="${_escHtml(_wizardSettings.deezer_arl)}"
                           oninput="_wizardSettings.deezer_arl = this.value">
                </div>
            </div>`;
    }

    el.innerHTML = `
        <div class="setup-card">
            <h2>Download Source</h2>
            <p class="setup-subtitle">Choose where SoulSync downloads music files from.</p>
            <div class="setup-info-box">
                <strong>How downloads work:</strong> When you search for a track, SoulSync uses your metadata source to identify it, then searches your download source for the actual audio file. The matching engine automatically finds the best quality match.
                <br><br>
                <strong>Hybrid mode</strong> (available later in Settings) lets you set a priority order — if your primary source doesn't have a track, SoulSync automatically tries the next source in line.
            </div>
            <div class="setup-option-grid" style="grid-template-columns: 1fr 1fr 1fr;">
                ${sources.map(s => `
                    <div class="setup-option-card ${sel === s.id ? 'selected' : ''}"
                         onclick="_wizardSelectDownload('${s.id}')">
                        <div class="setup-option-name">${s.name}</div>
                        <div class="setup-option-desc">${s.desc}</div>
                    </div>
                `).join('')}
            </div>
            ${inlineConfig}
            <div class="setup-btn-row">
                <button class="setup-btn setup-btn-secondary" onclick="wizardBack()">Back</button>
                <button class="setup-btn setup-btn-primary" onclick="wizardNext()">Next</button>
            </div>
        </div>
    `;
}

function _wizardSelectDownload(id) {
    _wizardSettings.download_source = id;
    _renderWizard();
}

async function _wizardTestConnection(service) {
    // Find the test button in the current inline config
    const btns = document.querySelectorAll('.setup-test-btn');
    const btn = btns[btns.length - 1];
    if (btn) {
        btn.innerHTML = '<span class="setup-spinner"></span>Testing...';
        btn.className = 'setup-test-btn';
    }

    // Save relevant settings first so the backend can test them
    const settings = {};
    if (service === 'soulseek') {
        settings.soulseek = { slskd_url: _wizardSettings.slskd_url, api_key: _wizardSettings.slskd_api_key };
    } else if (service === 'tidal') {
        settings.tidal = { client_id: _wizardSettings.tidal_client_id, client_secret: _wizardSettings.tidal_client_secret };
    } else if (service === 'plex') {
        settings.plex = { base_url: _wizardSettings.server_url, token: _wizardSettings.server_token };
        settings.active_media_server = 'plex';
    } else if (service === 'jellyfin') {
        settings.jellyfin = { base_url: _wizardSettings.server_url, api_key: _wizardSettings.server_api_key };
        settings.active_media_server = 'jellyfin';
    } else if (service === 'navidrome') {
        settings.navidrome = { base_url: _wizardSettings.server_url, username: _wizardSettings.server_user, password: _wizardSettings.server_pass };
        settings.active_media_server = 'navidrome';
    }

    try {
        await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(settings)
        });

        const resp = await fetch('/api/test-connection', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ service })
        });
        const result = await resp.json();

        if (btn) {
            if (result.success) {
                btn.textContent = 'Connected';
                btn.classList.add('success');
            } else {
                btn.textContent = 'Failed — check credentials';
                btn.classList.add('failed');
            }
        }
    } catch {
        if (btn) {
            btn.textContent = 'Connection error';
            btn.classList.add('failed');
        }
    }
}

// ---- Step 4: Paths & Media Server ----

function _renderPaths(el) {
    const server = _wizardSettings.media_server;
    const showServerConfig = server !== 'none';

    let serverFields = '';
    if (server === 'plex') {
        serverFields = `
            <div class="setup-input-group">
                <label>Plex URL</label>
                <input class="setup-input" type="url" placeholder="http://localhost:32400"
                       value="${_escHtml(_wizardSettings.server_url)}" oninput="_wizardSettings.server_url = this.value">
            </div>
            <div class="setup-input-group">
                <label>Plex Token</label>
                <input class="setup-input" type="password" placeholder="Your Plex token"
                       value="${_escHtml(_wizardSettings.server_token)}" oninput="_wizardSettings.server_token = this.value">
            </div>
            <button class="setup-test-btn" onclick="_wizardTestConnection('plex')">Test Connection</button>
        `;
    } else if (server === 'jellyfin') {
        serverFields = `
            <div class="setup-input-group">
                <label>Jellyfin URL</label>
                <input class="setup-input" type="url" placeholder="http://localhost:8096"
                       value="${_escHtml(_wizardSettings.server_url)}" oninput="_wizardSettings.server_url = this.value">
            </div>
            <div class="setup-input-group">
                <label>API Key</label>
                <input class="setup-input" type="password" placeholder="Your Jellyfin API key"
                       value="${_escHtml(_wizardSettings.server_api_key)}" oninput="_wizardSettings.server_api_key = this.value">
            </div>
            <button class="setup-test-btn" onclick="_wizardTestConnection('jellyfin')">Test Connection</button>
        `;
    } else if (server === 'navidrome') {
        serverFields = `
            <div class="setup-input-group">
                <label>Navidrome URL</label>
                <input class="setup-input" type="url" placeholder="http://localhost:4533"
                       value="${_escHtml(_wizardSettings.server_url)}" oninput="_wizardSettings.server_url = this.value">
            </div>
            <div class="setup-input-group">
                <label>Username</label>
                <input class="setup-input" type="text" placeholder="admin"
                       value="${_escHtml(_wizardSettings.server_user)}" oninput="_wizardSettings.server_user = this.value">
            </div>
            <div class="setup-input-group">
                <label>Password</label>
                <input class="setup-input" type="password" placeholder="Password"
                       value="${_escHtml(_wizardSettings.server_pass)}" oninput="_wizardSettings.server_pass = this.value">
            </div>
            <button class="setup-test-btn" onclick="_wizardTestConnection('navidrome')">Test Connection</button>
        `;
    }

    const dlLocked = _wizardPathLocks.download;
    const trLocked = _wizardPathLocks.transfer;

    el.innerHTML = `
        <div class="setup-card">
            <h2>Paths & Media Server</h2>
            <p class="setup-subtitle">Where should downloaded music go?</p>
            <div class="setup-info-box">
                <strong>Two-folder system:</strong> Music downloads to the <strong>Input Folder</strong> first as raw files. After post-processing (metadata tagging, file organization), finished tracks are moved to the <strong>Output Folder</strong> organized into Artist/Album subfolders. Point your media server at the output folder.
            </div>
            <div class="setup-input-group">
                <label>Input Folder (where raw downloads land)</label>
                <div class="setup-path-row">
                    <input class="setup-input setup-path-input" type="text" id="setup-download-path"
                           placeholder="/app/downloads"
                           value="${_escHtml(_wizardSettings.download_path)}"
                           ${dlLocked ? 'readonly' : ''}
                           oninput="_wizardSettings.download_path = this.value">
                    <button class="setup-lock-btn ${dlLocked ? 'locked' : ''}" onclick="_wizardTogglePathLock('download')">
                        ${dlLocked ? 'Unlock' : 'Lock'}
                    </button>
                </div>
            </div>
            <div class="setup-input-group">
                <label>Music Library / Output Folder (organized music)</label>
                <div class="setup-path-row">
                    <input class="setup-input setup-path-input" type="text" id="setup-transfer-path"
                           placeholder="/app/Transfer"
                           value="${_escHtml(_wizardSettings.transfer_path)}"
                           ${trLocked ? 'readonly' : ''}
                           oninput="_wizardSettings.transfer_path = this.value">
                    <button class="setup-lock-btn ${trLocked ? 'locked' : ''}" onclick="_wizardTogglePathLock('transfer')">
                        ${trLocked ? 'Unlock' : 'Lock'}
                    </button>
                </div>
            </div>

            <p class="setup-subtitle" style="margin-top: 20px; margin-bottom: 8px;">Connect a media server</p>
            <div class="setup-info-box" style="margin-bottom: 12px;">
                Connecting a media server lets SoulSync trigger library scans after downloads, import your existing library, and display what you already own when searching. Select <strong>None</strong> if you don't use one.
            </div>
            <div class="setup-server-grid">
                ${['plex', 'jellyfin', 'navidrome', 'none'].map(s => `
                    <div class="setup-server-card ${server === s ? 'selected' : ''}"
                         onclick="_wizardSelectServer('${s}')">
                        <div class="setup-server-name">${s === 'none' ? 'None' : s.charAt(0).toUpperCase() + s.slice(1)}</div>
                    </div>
                `).join('')}
            </div>

            <div class="setup-inline-config ${showServerConfig ? 'visible' : ''}">
                ${serverFields}
            </div>

            <div class="setup-btn-row">
                <button class="setup-btn setup-btn-secondary" onclick="wizardBack()">Back</button>
                <button class="setup-btn setup-btn-primary" onclick="wizardNext()">Next</button>
            </div>
        </div>
    `;
}

function _wizardTogglePathLock(pathType) {
    _wizardPathLocks[pathType] = !_wizardPathLocks[pathType];
    _renderWizard();
    if (!_wizardPathLocks[pathType]) {
        const id = pathType === 'download' ? 'setup-download-path' : 'setup-transfer-path';
        const input = document.getElementById(id);
        if (input) input.focus();
    }
}

function _wizardSelectServer(id) {
    _wizardSettings.media_server = id;
    _wizardSettings.server_url = '';
    _wizardSettings.server_token = '';
    _wizardSettings.server_user = '';
    _wizardSettings.server_pass = '';
    _wizardSettings.server_api_key = '';
    _renderWizard();
}

// ---- Step 5: Add Artists to Watchlist ----

function _renderWatchlist(el) {
    const chips = _wizardAddedArtists.map((a, i) => `
        <div class="setup-added-chip">
            ${_escHtml(a.name)}
            <span class="remove" onclick="_wizardRemoveArtist(${i})">&times;</span>
        </div>
    `).join('');

    el.innerHTML = `
        <div class="setup-card">
            <h2>Add Your First Artists</h2>
            <p class="setup-subtitle">Search for artists to add to your watchlist.</p>
            <div class="setup-info-box">
                <strong>What is the Watchlist?</strong> Artists on your watchlist are monitored for new releases. When a watched artist drops a new album, EP, or single, it appears on your <strong>Discover</strong> page and can be auto-downloaded.
                <ul>
                    <li><strong>Discover page</strong> — populated by new releases from watched artists, similar artists, and seasonal picks</li>
                    <li><strong>Watchlist Scanner</strong> — runs automatically on a schedule to check for new releases</li>
                    <li><strong>Filters</strong> — per-artist controls for albums, EPs, singles, remixes, live recordings, and more</li>
                </ul>
                You can always add or remove artists later from the Artists page.
            </div>
            <div class="setup-added-artists" id="setup-added-artists">${chips}</div>
            <div class="setup-search-wrapper">
                <span class="setup-search-icon">
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
                </span>
                <input class="setup-search-input" type="text" id="setup-artist-search"
                       placeholder="Search for an artist..." autocomplete="off"
                       oninput="_wizardArtistSearch(this.value)">
            </div>
            <div class="setup-artist-results" id="setup-artist-results"></div>
            <div class="setup-btn-row">
                <button class="setup-btn setup-btn-secondary" onclick="wizardBack()">Back</button>
                <div>
                    <button class="setup-btn setup-btn-secondary" onclick="wizardSkipStep()" style="margin-right: 8px;">Skip</button>
                    <button class="setup-btn setup-btn-primary" onclick="wizardNext()">Next</button>
                </div>
            </div>
        </div>
    `;
}

function _wizardArtistSearch(query) {
    clearTimeout(_wizardSearchTimeout);
    if (query.length < 2) {
        const results = document.getElementById('setup-artist-results');
        if (results) results.innerHTML = '';
        return;
    }
    _wizardSearchTimeout = setTimeout(async () => {
        const results = document.getElementById('setup-artist-results');
        if (!results) return;
        results.innerHTML = '<div style="text-align:center;padding:12px;color:rgba(255,255,255,0.4);"><span class="setup-spinner"></span> Searching...</div>';

        try {
            // Use the discover artist search endpoint — works with whatever metadata source is active
            const resp = await fetch(`/api/discover/build-playlist/search-artists?query=${encodeURIComponent(query)}`);
            const data = await resp.json();
            const artists = data.artists || [];

            if (artists.length === 0) {
                results.innerHTML = '<div style="text-align:center;padding:12px;color:rgba(255,255,255,0.3);">No artists found</div>';
                return;
            }

            // Check which artists are already in watchlist
            const artistIds = artists.map(a => String(a.id));
            let watchlistStatus = {};
            try {
                const wResp = await fetch('/api/watchlist/check-batch', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ artist_ids: artistIds })
                });
                const wData = await wResp.json();
                if (wData.success) watchlistStatus = wData.results || {};
            } catch { /* ignore */ }

            results.innerHTML = artists.slice(0, 8).map((a, i) => {
                const img = a.image_url || '';
                const name = a.name || '';
                const id = String(a.id || '');
                const isInWatchlist = watchlistStatus[id] || _wizardAddedArtists.some(w => String(w.id) === id);
                return `
                    <div class="setup-artist-row ${isInWatchlist ? 'added' : ''}" data-artist-id="${_escHtml(id)}"
                         onclick="${isInWatchlist ? `_wizardRemoveArtistById('${_escHtml(id)}','${_escHtml(name)}')` : `_wizardAddArtistDirect('${_escHtml(id)}','${_escHtml(name)}','${_escHtml(img)}')`}">
                        ${img ? `<img class="setup-artist-img" src="${_escHtml(img)}" alt="" onerror="this.style.display='none'">` : '<div class="setup-artist-img"></div>'}
                        <div class="setup-artist-info">
                            <div class="setup-artist-name">${_escHtml(name)}</div>
                        </div>
                        <div class="setup-artist-check">${isInWatchlist ? '&#10003; Watching' : '+ Add'}</div>
                    </div>
                `;
            }).join('');
        } catch (e) {
            console.error('Wizard artist search error:', e);
            results.innerHTML = '<div style="text-align:center;padding:12px;color:#ef4444;">Search failed</div>';
        }
    }, 400);
}

async function _wizardAddArtistDirect(id, name, image) {
    if (_wizardAddedArtists.some(a => String(a.id) === id)) return;

    try {
        const resp = await fetch('/api/watchlist/add', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ artist_id: id, artist_name: name, image_url: image })
        });
        const result = await resp.json();

        if (result.success || result.status === 'already_watching') {
            _wizardAddedArtists.push({ id, name, image });
            // Re-render chips but preserve search results
            const chipsEl = document.getElementById('setup-added-artists');
            if (chipsEl) {
                chipsEl.innerHTML = _wizardAddedArtists.map((a, i) => `
                    <div class="setup-added-chip">
                        ${_escHtml(a.name)}
                        <span class="remove" onclick="_wizardRemoveArtist(${i})">&times;</span>
                    </div>
                `).join('');
            }
            // Update the row in results to show "watching"
            const row = document.querySelector(`.setup-artist-row[data-artist-id="${id}"]`);
            if (row) {
                row.classList.add('added');
                row.setAttribute('onclick', `_wizardRemoveArtistById('${_escHtml(id)}','${_escHtml(name)}')`);
                const check = row.querySelector('.setup-artist-check');
                if (check) check.innerHTML = '&#10003; Watching';
            }
            if (typeof showToast === 'function') showToast(`Added ${name} to watchlist`, 'success');
        } else {
            if (typeof showToast === 'function') showToast(result.error || 'Failed to add artist', 'error');
        }
    } catch (e) {
        console.error('Wizard add artist error:', e);
    }
}

async function _wizardRemoveArtistById(id, name) {
    try {
        await fetch('/api/watchlist/remove', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ artist_id: id })
        });
        _wizardAddedArtists = _wizardAddedArtists.filter(a => String(a.id) !== id);
        // Re-render chips
        const chipsEl = document.getElementById('setup-added-artists');
        if (chipsEl) {
            chipsEl.innerHTML = _wizardAddedArtists.map((a, i) => `
                <div class="setup-added-chip">
                    ${_escHtml(a.name)}
                    <span class="remove" onclick="_wizardRemoveArtist(${i})">&times;</span>
                </div>
            `).join('');
        }
        // Update the row in results
        const row = document.querySelector(`.setup-artist-row[data-artist-id="${id}"]`);
        if (row) {
            row.classList.remove('added');
            const img = _wizardAddedArtists.find(a => String(a.id) === id)?.image || '';
            row.setAttribute('onclick', `_wizardAddArtistDirect('${_escHtml(id)}','${_escHtml(name)}','${_escHtml(img)}')`);
            const check = row.querySelector('.setup-artist-check');
            if (check) check.innerHTML = '+ Add';
        }
        if (typeof showToast === 'function') showToast(`Removed ${name} from watchlist`, 'info');
    } catch (e) {
        console.error('Wizard remove artist error:', e);
    }
}

function _wizardRemoveArtist(index) {
    const artist = _wizardAddedArtists[index];
    if (artist) {
        _wizardRemoveArtistById(String(artist.id), artist.name);
    }
}

// ---- Step 6: First Download ----

function _renderFirstDownload(el) {
    el.innerHTML = `
        <div class="setup-card">
            <h2>Your First Download</h2>
            <p class="setup-subtitle">Try searching for a track to see the full pipeline in action.</p>
            <div class="setup-info-box">
                <strong>How it works:</strong> Type a song name below. SoulSync searches your metadata source for the track, then finds the best matching audio file from your download source. The track is tagged with full metadata (artist, album, track number, artwork) and organized into your output folder.
            </div>
            <div class="setup-search-wrapper">
                <span class="setup-search-icon">
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
                </span>
                <input class="setup-search-input" type="text" id="setup-track-search"
                       placeholder="Search for a song..." autocomplete="off"
                       oninput="_wizardTrackSearch(this.value)">
            </div>
            <div class="setup-track-results" id="setup-track-results"></div>
            <div class="setup-btn-row">
                <button class="setup-btn setup-btn-secondary" onclick="wizardBack()">Back</button>
                <div>
                    <button class="setup-btn setup-btn-secondary" onclick="wizardSkipStep()" style="margin-right: 8px;">Skip</button>
                    <button class="setup-btn setup-btn-primary" onclick="wizardNext()">Next</button>
                </div>
            </div>
        </div>
    `;
}

function _wizardTrackSearch(query) {
    clearTimeout(_wizardSearchTimeout);
    if (query.length < 2) {
        const results = document.getElementById('setup-track-results');
        if (results) results.innerHTML = '';
        return;
    }
    _wizardSearchTimeout = setTimeout(async () => {
        const results = document.getElementById('setup-track-results');
        if (!results) return;
        results.innerHTML = '<div style="text-align:center;padding:12px;color:rgba(255,255,255,0.4);"><span class="setup-spinner"></span> Searching...</div>';

        try {
            // Use enhanced-search which searches the configured metadata source
            const resp = await fetch('/api/enhanced-search', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ query })
            });
            const data = await resp.json();
            // Tracks are in spotify_tracks (backward compat key, actual source may be Deezer)
            const tracks = data.spotify_tracks || [];

            if (tracks.length === 0) {
                results.innerHTML = '<div style="text-align:center;padding:12px;color:rgba(255,255,255,0.3);">No tracks found</div>';
                return;
            }

            // Store for download reference
            window._wizardTrackResults = tracks;

            results.innerHTML = tracks.slice(0, 8).map((t, i) => {
                const art = t.image_url || '';
                const title = t.name || '';
                const artist = t.artist || '';
                const album = t.album || '';
                return `
                    <div class="setup-track-row" id="setup-track-${i}"
                         onclick="_wizardDownloadTrack(${i})">
                        ${art ? `<img class="setup-track-art" src="${_escHtml(art)}" alt="" onerror="this.style.display='none'">` : '<div class="setup-track-art"></div>'}
                        <div class="setup-track-info">
                            <div class="setup-track-title">${_escHtml(title)}</div>
                            <div class="setup-track-artist">${_escHtml(artist)}${album ? ' &middot; ' + _escHtml(album) : ''}</div>
                        </div>
                        <div class="setup-track-status" id="setup-track-status-${i}">Click to download</div>
                    </div>
                `;
            }).join('');
        } catch (e) {
            console.error('Wizard track search error:', e);
            results.innerHTML = '<div style="text-align:center;padding:12px;color:#ef4444;">Search failed</div>';
        }
    }, 400);
}

async function _wizardDownloadTrack(index) {
    const row = document.getElementById(`setup-track-${index}`);
    const status = document.getElementById(`setup-track-status-${index}`);
    if (!row || !status) return;
    if (row.classList.contains('downloading') || row.classList.contains('downloaded')) return;

    const tracks = window._wizardTrackResults;
    if (!tracks || !tracks[index]) return;

    const track = tracks[index];
    row.classList.add('downloading');
    status.innerHTML = '<span class="setup-spinner"></span>Searching...';

    try {
        // Step 1: Search for the best match via the configured download source
        const searchResp = await fetch('/api/enhanced-search/stream-track', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                track_name: track.name || '',
                artist_name: track.artist || '',
                album_name: track.album || '',
                duration_ms: track.duration_ms || 0,
            })
        });
        const searchResult = await searchResp.json();

        if (!searchResult.success || !searchResult.result) {
            row.classList.remove('downloading');
            status.textContent = searchResult.error || 'No match found';
            return;
        }

        // Step 2: Start matched download with full metadata context from the search result
        status.innerHTML = '<span class="setup-spinner"></span>Downloading...';

        const artistName = track.artist || 'Unknown Artist';
        const dlResp = await fetch('/api/download/matched', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                search_result: searchResult.result,
                spotify_artist: {
                    name: artistName,
                    id: track.artist_id || track.id || '',
                },
                spotify_track: {
                    name: track.name || '',
                    artists: [artistName],
                    album: {
                        name: track.album || 'Unknown Album',
                        images: track.image_url ? [{ url: track.image_url }] : [],
                    },
                    track_number: track.track_number || 1,
                    disc_number: track.disc_number || 1,
                    duration_ms: track.duration_ms || 0,
                    release_date: track.release_date || '',
                    isrc: track.isrc || '',
                    image_url: track.image_url || '',
                    external_urls: track.external_urls || {},
                },
                is_single_track: true,
            })
        });
        const dlResult = await dlResp.json();

        if (dlResult.success) {
            _wizardDownloadedTrack = track;
            row.classList.remove('downloading');
            row.classList.add('downloaded');
            status.textContent = 'Download started';
        } else {
            row.classList.remove('downloading');
            status.textContent = dlResult.error || 'Download failed';
        }
    } catch (e) {
        console.error('Wizard download error:', e);
        row.classList.remove('downloading');
        status.textContent = 'Error';
    }
}

// ---- Step 7: Done ----

function _renderDone(el) {
    const summaryRows = [];
    const cap = s => s.charAt(0).toUpperCase() + s.slice(1);
    summaryRows.push({ label: 'Metadata Source', value: cap(_wizardSettings.metadata_source) });
    const dlName = _wizardSettings.download_source === 'deezer_dl' ? 'Deezer' : cap(_wizardSettings.download_source);
    summaryRows.push({ label: 'Download Source', value: dlName });
    if (_wizardSettings.download_path) summaryRows.push({ label: 'Input Folder', value: _wizardSettings.download_path });
    if (_wizardSettings.transfer_path) summaryRows.push({ label: 'Music Library', value: _wizardSettings.transfer_path });
    if (_wizardSettings.media_server !== 'none') summaryRows.push({ label: 'Media Server', value: cap(_wizardSettings.media_server) });
    if (_wizardAddedArtists.length > 0) summaryRows.push({ label: 'Artists Added', value: _wizardAddedArtists.length.toString() });

    el.innerHTML = `
        <div class="setup-card">
            <div class="setup-done-icon">
                <svg width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="rgb(var(--accent-rgb))" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                    <polyline points="20 6 9 17 4 12"/>
                </svg>
            </div>
            <h2>You're All Set!</h2>
            <p class="setup-subtitle">SoulSync is configured and ready to go. Here's a quick overview of what's available.</p>
            <div class="setup-summary">
                ${summaryRows.map(r => `
                    <div class="setup-summary-row">
                        <span class="setup-summary-label">${r.label}</span>
                        <span class="setup-summary-value">${_escHtml(r.value)}</span>
                    </div>
                `).join('')}
            </div>
            <div class="setup-tips-grid">
                <div class="setup-tip-card">
                    <div class="setup-tip-title">Sync Page</div>
                    <div class="setup-tip-text">Mirror playlists from Spotify, Tidal, Deezer, YouTube, Beatport, and ListenBrainz. SoulSync matches and downloads missing tracks automatically.</div>
                </div>
                <div class="setup-tip-card">
                    <div class="setup-tip-title">Wishlist</div>
                    <div class="setup-tip-text">Tracks that can't be found are saved to your Wishlist and retried automatically. Check it on the Dashboard to see what's pending.</div>
                </div>
                <div class="setup-tip-card">
                    <div class="setup-tip-title">Automations</div>
                    <div class="setup-tip-text">Build event-driven workflows: scan watchlists, process wishlists, sync playlists on a schedule, and get notified via Discord, Telegram, or Pushbullet.</div>
                </div>
                <div class="setup-tip-card">
                    <div class="setup-tip-title">Notifications</div>
                    <div class="setup-tip-text">The bell icon (top-right) shows download completions, new releases, sync results, and errors. Configure external alerts in Settings.</div>
                </div>
                <div class="setup-tip-card">
                    <div class="setup-tip-title">Interactive Help</div>
                    <div class="setup-tip-text">Click the <strong>?</strong> button (bottom-right) anytime for context-aware help. It explains any section of the UI you click on.</div>
                </div>
                <div class="setup-tip-card">
                    <div class="setup-tip-title">Settings</div>
                    <div class="setup-tip-text">Everything from this wizard plus much more — file organization templates, quality preferences, tag embedding, and advanced options.</div>
                </div>
            </div>
            <button class="setup-btn setup-btn-primary setup-btn-big" onclick="_wizardFinish()">Start Using SoulSync</button>
        </div>
    `;
}

async function _wizardFinish() {
    // Final save — all settings were saved per-step, but do a final pass
    const settings = {
        metadata: { fallback_source: _wizardSettings.metadata_source },
        download_source: { mode: _wizardSettings.download_source },
        soulseek: {
            download_path: _wizardSettings.download_path,
            transfer_path: _wizardSettings.transfer_path,
        },
    };

    if (_wizardSettings.slskd_url) {
        settings.soulseek.slskd_url = _wizardSettings.slskd_url;
        settings.soulseek.api_key = _wizardSettings.slskd_api_key;
    }

    if (_wizardSettings.media_server !== 'none') {
        settings.active_media_server = _wizardSettings.media_server;
        if (_wizardSettings.media_server === 'plex') {
            settings.plex = { base_url: _wizardSettings.server_url, token: _wizardSettings.server_token };
        } else if (_wizardSettings.media_server === 'jellyfin') {
            settings.jellyfin = { base_url: _wizardSettings.server_url, api_key: _wizardSettings.server_api_key };
        } else if (_wizardSettings.media_server === 'navidrome') {
            settings.navidrome = { base_url: _wizardSettings.server_url, username: _wizardSettings.server_user, password: _wizardSettings.server_pass };
        }
    }

    try {
        await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(settings)
        });
    } catch (e) {
        console.error('Wizard final save error:', e);
    }

    // Mark setup complete on both server and client
    try {
        await fetch('/api/setup/complete', { method: 'POST' });
    } catch (e) {
        console.error('Failed to mark setup complete on server:', e);
    }
    localStorage.setItem('soulsync_setup_complete', 'true');

    const overlay = document.getElementById('setup-wizard-overlay');
    if (overlay) overlay.style.display = 'none';

    // Reload settings into the main UI
    if (typeof loadSettings === 'function') loadSettings();
    if (typeof showToast === 'function') showToast('Setup complete — welcome to SoulSync!', 'success');

    // Continue app initialization if wizard was shown on first run
    if (typeof window._onSetupWizardComplete === 'function') {
        window._onSetupWizardComplete();
        window._onSetupWizardComplete = null;
    }
}

// ---- Utility ----

function _escHtml(str) {
    if (!str) return '';
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// ---- Dev Trigger ----
// Open wizard manually: openSetupWizard() from console, or ?setup=1 URL param
// First-run auto-detection is handled in script.js DOMContentLoaded

// Expose globally
window.openSetupWizard = openSetupWizard;
window.closeSetupWizard = closeSetupWizard;
