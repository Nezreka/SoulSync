/*
 * Service credential sets — admin manager (Settings) + per-profile quick-switch
 * modal (sidebar Service Status).
 *
 * Admin creates named credential "pills" per auth service; every profile picks
 * which one is active for it. Secrets are entered here but NEVER read back —
 * the API only ever returns id/label, so this UI shows names, never values.
 *
 * Backend: /api/credentials (admin CRUD) and /api/profiles/me/services[/select]
 * (per-profile selection, any profile).
 */

// Display order + labels for the supported services (mirrors the backend
// SERVICE_CREDENTIAL_SCHEMA). Each lists the fields the admin enters; `req`
// marks required (matches server validation), `pw` renders as a password input.
const CRED_SERVICES = [
    { id: 'spotify',   name: 'Spotify',   fields: [
        { key: 'client_id', label: 'Client ID', req: true },
        { key: 'client_secret', label: 'Client Secret', req: true, pw: true },
        { key: 'redirect_uri', label: 'Redirect URI (optional)' },
    ]},
    { id: 'tidal',     name: 'Tidal',     fields: [
        { key: 'access_token', label: 'Access Token', req: true, pw: true },
        { key: 'refresh_token', label: 'Refresh Token', req: true, pw: true },
    ]},
    { id: 'deezer',    name: 'Deezer',    fields: [
        { key: 'arl', label: 'ARL', req: true, pw: true },
    ]},
    { id: 'qobuz',     name: 'Qobuz',     fields: [
        { key: 'user_auth_token', label: 'User Auth Token', req: true, pw: true },
    ]},
    { id: 'plex',      name: 'Plex',      fields: [
        { key: 'base_url', label: 'Server URL', req: true },
        { key: 'token', label: 'Token', req: true, pw: true },
    ]},
    { id: 'jellyfin',  name: 'Jellyfin',  fields: [
        { key: 'base_url', label: 'Server URL', req: true },
        { key: 'api_key', label: 'API Key', req: true, pw: true },
    ]},
    { id: 'navidrome', name: 'Navidrome', fields: [
        { key: 'base_url', label: 'Server URL', req: true },
        { key: 'username', label: 'Username', req: true },
        { key: 'password', label: 'Password', req: true, pw: true },
    ]},
];

const _credServiceById = Object.fromEntries(CRED_SERVICES.map(s => [s.id, s]));

function _credEsc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c =>
        ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// ── Admin manager (rendered inside Settings) ─────────────────────────────────

async function loadCredentialSets() {
    const host = document.getElementById('credential-sets-container');
    if (!host) return;
    try {
        const res = await fetch('/api/credentials');
        if (res.status === 403) { host.innerHTML = ''; return; }  // not admin
        const data = await res.json();
        _renderCredentialSets(host, (data && data.services) || {});
    } catch (e) {
        host.innerHTML = '<div class="cred-empty">Could not load credential sets.</div>';
    }
}

function _renderCredentialSets(host, services) {
    host.innerHTML = CRED_SERVICES.map(svc => {
        const sets = services[svc.id] || [];
        const pills = sets.map(s => `
            <span class="cred-pill">
                <span class="cred-pill-label">${_credEsc(s.label)}</span>
                <button class="cred-pill-del" title="Delete"
                        onclick="deleteCredentialSet(${s.id}, '${_credEsc(svc.name)}')">✕</button>
            </span>`).join('');
        return `
            <div class="cred-svc-row" data-cred-svc="${svc.id}">
                <div class="cred-svc-head">
                    <span class="cred-svc-name">${svc.name}</span>
                    <button class="cred-add-btn" onclick="toggleAddCredentialForm('${svc.id}')">+ Add account</button>
                </div>
                <div class="cred-pills">${pills || '<span class="cred-empty">No saved accounts</span>'}</div>
                <div class="cred-add-form" id="cred-add-form-${svc.id}" hidden></div>
            </div>`;
    }).join('');
}

function toggleAddCredentialForm(serviceId) {
    const form = document.getElementById(`cred-add-form-${serviceId}`);
    if (!form) return;
    if (!form.hidden) { form.hidden = true; form.innerHTML = ''; return; }
    const svc = _credServiceById[serviceId];
    form.innerHTML = `
        <input type="text" class="cred-input" id="cred-new-label-${serviceId}" placeholder="Name (e.g. Brock's ${svc.name})">
        ${svc.fields.map(f => `
            <input type="${f.pw ? 'password' : 'text'}" class="cred-input"
                   id="cred-new-${serviceId}-${f.key}" placeholder="${_credEsc(f.label)}"
                   autocomplete="new-password">`).join('')}
        <div class="cred-form-actions">
            <button class="cred-save-btn" onclick="saveNewCredential('${serviceId}')">Save</button>
            <button class="cred-cancel-btn" onclick="toggleAddCredentialForm('${serviceId}')">Cancel</button>
        </div>
        <div class="cred-form-error" id="cred-form-error-${serviceId}"></div>`;
    form.hidden = false;
    const first = document.getElementById(`cred-new-label-${serviceId}`);
    if (first) first.focus();
}

async function saveNewCredential(serviceId) {
    const svc = _credServiceById[serviceId];
    const errEl = document.getElementById(`cred-form-error-${serviceId}`);
    const label = (document.getElementById(`cred-new-label-${serviceId}`).value || '').trim();
    if (!label) { if (errEl) errEl.textContent = 'Give this account a name.'; return; }
    const payload = {};
    for (const f of svc.fields) {
        const v = (document.getElementById(`cred-new-${serviceId}-${f.key}`).value || '').trim();
        if (v) payload[f.key] = v;
    }
    const missing = svc.fields.filter(f => f.req && !payload[f.key]).map(f => f.label);
    if (missing.length) { if (errEl) errEl.textContent = `Required: ${missing.join(', ')}`; return; }

    try {
        const res = await fetch('/api/credentials', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ service: serviceId, label, payload }),
        });
        const data = await res.json();
        if (!data.success) { if (errEl) errEl.textContent = data.error || 'Save failed.'; return; }
        if (typeof showToast === 'function') showToast(`Saved "${label}" for ${svc.name}`, 'success');
        loadCredentialSets();
    } catch (e) {
        if (errEl) errEl.textContent = 'Save failed.';
    }
}

async function deleteCredentialSet(id, serviceName) {
    if (!confirm(`Delete this ${serviceName} account? Profiles using it will fall back to the default.`)) return;
    try {
        const res = await fetch(`/api/credentials/${id}`, { method: 'DELETE' });
        const data = await res.json();
        if (data.success) {
            if (typeof showToast === 'function') showToast('Account deleted', 'success');
            loadCredentialSets();
        } else if (typeof showToast === 'function') {
            showToast(data.error || 'Delete failed', 'error');
        }
    } catch (e) { /* no-op */ }
}
