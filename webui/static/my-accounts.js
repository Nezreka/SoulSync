/*
 * My Accounts — per-profile self-auth for playlist services.
 *
 * Each profile connects its OWN streaming accounts (their token, the app's
 * shared client). Used for that profile's playlist operations; the global/admin
 * auth keeps running the background app. Spotify is the first service; the others
 * follow the same pattern.
 *
 * Backend: GET /api/profiles/me/connections, the per-service OAuth popups, and
 * POST /api/profiles/me/connections/<service>/disconnect.
 */

// Playlist services shown in My Accounts. `connect` returns the OAuth URL for a
// given profile id (popup); services are wired in over time.
const _MA_SERVICES = [
    {
        id: 'spotify', name: 'Spotify', brand: '#1db954',
        logo: 'https://storage.googleapis.com/pr-newsroom-wp/1/2023/05/Spotify_Primary_Logo_RGB_Green.png',
        connect: (pid) => `/auth/spotify?profile_id=${pid}`,
    },
    {
        id: 'tidal', name: 'Tidal', brand: '#00cfe8', dark: true,
        logo: 'https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/png/tidal-light.png',
        connect: (pid) => `/auth/tidal?profile_id=${pid}`,
    },
    {
        id: 'listenbrainz', name: 'ListenBrainz', brand: '#eb743b', dark: true,
        logo: 'https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/png/listenbrainz.png',
        type: 'token',
        saveUrl: '/api/profiles/me/listenbrainz',
        hint: 'Paste your token from listenbrainz.org/profile',
    },
];

function _maEsc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c =>
        ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function _maProfileId() {
    try {
        const ctx = (typeof getCurrentProfileContext === 'function') ? getCurrentProfileContext() : null;
        return ctx ? ctx.profileId : 1;
    } catch (_e) { return 1; }
}

function openMyAccountsModal() {
    let overlay = document.getElementById('my-accounts-overlay');
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = 'my-accounts-overlay';
        overlay.className = 'modal-overlay ma-overlay hidden';
        overlay.onclick = (e) => { if (e.target === overlay) closeMyAccountsModal(); };
        overlay.innerHTML = `
            <div class="ma-modal" role="dialog" aria-modal="true" aria-label="My Accounts" tabindex="-1">
                <div class="ma-topbar">
                    <div class="ma-topbar-icon"><img src="/static/trans2.png" alt="SoulSync" class="ma-topbar-logo"></div>
                    <div class="ma-topbar-titles">
                        <h3 class="ma-topbar-title">My Accounts</h3>
                        <div class="ma-topbar-sub">Connect your own streaming accounts — used for your playlists, just for you.</div>
                    </div>
                    <button class="ma-icon-btn" title="Close" onclick="closeMyAccountsModal()">&times;</button>
                </div>
                <div class="ma-body" id="ma-body"></div>
            </div>`;
        document.body.appendChild(overlay);
    }
    overlay.classList.remove('hidden');
    const modal = overlay.querySelector('.ma-modal');
    if (modal) { modal.classList.remove('ma-in'); void modal.offsetWidth; modal.classList.add('ma-in'); }
    document.addEventListener('keydown', _maOnKeydown);
    _maLoad();
}

function closeMyAccountsModal() {
    const o = document.getElementById('my-accounts-overlay');
    if (o) o.classList.add('hidden');
    document.removeEventListener('keydown', _maOnKeydown);
}

function _maOnKeydown(e) { if (e.key === 'Escape') closeMyAccountsModal(); }

async function _maLoad() {
    const body = document.getElementById('ma-body');
    if (body) body.innerHTML = '<div class="ma-empty">Loading…</div>';
    let data = null;
    try {
        data = await (await fetch('/api/profiles/me/connections')).json();
    } catch (e) { /* render disconnected */ }
    _maRender(body, data || { connections: {}, is_admin: false });
}

function _maRender(body, data) {
    const conns = data.connections || {};
    const isAdmin = !!data.is_admin;
    const rows = _MA_SERVICES.map(svc => {
        const c = conns[svc.id] || {};
        const connected = !!c.connected;
        // Admin uses the global app account (set up in Settings) for every
        // service — not a personal connection here.
        const adminNote = isAdmin;
        let action;
        if (adminNote) {
            action = `<span class="ma-note">Managed in Settings (app account)</span>`;
        } else if (connected) {
            action = `
                <span class="ma-account">${_maEsc(c.account || 'Connected')}</span>
                <button class="ma-btn ma-btn--ghost" onclick="disconnectMyAccount('${svc.id}')">Disconnect</button>`;
        } else if (svc.type === 'token') {
            action = `
                <input type="password" class="ma-token-input" id="ma-token-${svc.id}" placeholder="Paste token"
                       title="${_maEsc(svc.hint || '')}">
                <button class="ma-btn ma-btn--connect" onclick="saveMyAccountToken('${svc.id}')">Save</button>`;
        } else {
            action = `<button class="ma-btn ma-btn--connect" onclick="connectMyAccount('${svc.id}')">Connect</button>`;
        }
        return `
            <div class="ma-row" style="--ma-brand:${svc.brand}">
                <span class="ma-disc${svc.dark ? ' ma-disc--dark' : ''}"><img class="ma-logo" src="${svc.logo}" alt=""
                      onerror="this.style.display='none'"></span>
                <div class="ma-row-info">
                    <div class="ma-row-name">${_maEsc(svc.name)}</div>
                    <div class="ma-row-status ${connected ? 'is-on' : ''}">${connected ? 'Connected' : (adminNote ? '' : 'Not connected')}</div>
                </div>
                <div class="ma-row-action">${action}</div>
            </div>`;
    }).join('');
    body.innerHTML = rows || '<div class="ma-empty">No services available.</div>';
}

let _maPollTimer = null;

function connectMyAccount(serviceId) {
    const svc = _MA_SERVICES.find(s => s.id === serviceId);
    if (!svc) return;
    const pid = _maProfileId();
    const popup = window.open(svc.connect(pid), 'soulsync-connect-' + serviceId,
        'width=560,height=720,menubar=no,toolbar=no');
    // Poll for the popup closing, then refresh status.
    if (_maPollTimer) clearInterval(_maPollTimer);
    _maPollTimer = setInterval(() => {
        if (!popup || popup.closed) {
            clearInterval(_maPollTimer);
            _maPollTimer = null;
            setTimeout(_maLoad, 600);  // give the callback a moment to persist
        }
    }, 800);
}

async function saveMyAccountToken(serviceId) {
    const svc = _MA_SERVICES.find(s => s.id === serviceId);
    if (!svc || !svc.saveUrl) return;
    const input = document.getElementById(`ma-token-${serviceId}`);
    const token = (input && input.value || '').trim();
    if (!token) { if (typeof showToast === 'function') showToast('Paste a token first', 'info'); return; }
    try {
        const res = await fetch(svc.saveUrl, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ token }),
        });
        const data = await res.json();
        if (data.success) {
            if (typeof showToast === 'function') showToast(`${svc.name} connected`, 'success');
            _maLoad();
        } else if (typeof showToast === 'function') {
            showToast(data.error || 'Could not connect', 'error');
        }
    } catch (e) {
        if (typeof showToast === 'function') showToast('Could not connect', 'error');
    }
}

async function disconnectMyAccount(serviceId) {
    if (!confirm(`Disconnect your ${serviceId} account from this profile?`)) return;
    try {
        const res = await fetch(`/api/profiles/me/connections/${serviceId}/disconnect`, { method: 'POST' });
        const data = await res.json();
        if (data.success) {
            if (typeof showToast === 'function') showToast('Disconnected', 'success');
            _maLoad();
        } else if (typeof showToast === 'function') {
            showToast(data.error || 'Disconnect failed', 'error');
        }
    } catch (e) { /* no-op */ }
}
