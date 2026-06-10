/*
 * Quick-switch modal — active Metadata / Server / Download source selection.
 * Opens from the sidebar Service Status panel; styled after the Manage Workers
 * hub (topbar + rail + panel, brand-logo cards).
 *
 * Admin writes the GLOBAL active source/server/download (same as Settings).
 * Non-admins see it read-only for now (per-profile override is a later layer):
 * the backend reports `editable`, and the UI disables changes when false.
 *
 * Backend: GET /api/profiles/me/active-sources, POST /api/profiles/active-sources.
 */

const _SS_TABS = [
    { id: 'metadata', name: 'Metadata', emoji: '🎼' },
    { id: 'server',   name: 'Server',   emoji: '🖥️' },
    { id: 'download', name: 'Download', emoji: '⬇️' },
];

// Brand logos. Metadata pulls from SOURCE_LABELS (shared-helpers.js) when
// available; server + download have their own small maps.
const _SS_SERVER_INFO = {
    // `dark`: the logo is a white/light wordmark, so it needs a dark disc to be
    // visible (it'd vanish on the default white disc).
    plex:      { name: 'Plex',      logo: 'https://www.plex.tv/wp-content/themes/plex/assets/img/plex-logo.svg', dark: true },
    jellyfin:  { name: 'Jellyfin',  logo: 'https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/png/jellyfin.png' },
    navidrome: { name: 'Navidrome', logo: 'https://tweakers.net/ext/i/2007323764.png' },
    soulsync:  { name: 'SoulSync',  logo: '/static/trans2.png', dark: true },
};
const _SS_META_FALLBACK = {
    spotify_free: { text: 'Spotify (no auth)', icon: '🆓', logo: 'https://storage.googleapis.com/pr-newsroom-wp/1/2023/05/Spotify_Primary_Logo_RGB_Green.png' },
};
// Brand colors drive each card's logo ring + active glow (the Manage-Workers feel).
const _SS_BRAND = {
    spotify: '#1db954', spotify_free: '#1db954', itunes: '#fc5c7d', deezer: '#a238ff',
    discogs: '#ff5500', musicbrainz: '#ba478f', amazon: '#ff9900',
    plex: '#e5a00d', jellyfin: '#aa5cc3', navidrome: '#3b6cf6', soulsync: '#7c5cff',
    soulseek: '#22a7f0', youtube: '#ff0000', tidal: '#00cfe8', qobuz: '#0a6e9e',
    hifi: '#16c79a', torrent: '#8a2be2', usenet: '#e67e22',
};
function _ssBrand(id) { return _SS_BRAND[id] || 'var(--accent-light-rgb-hex, #7c5cff)'; }

let _ssState = { tab: 'metadata', data: null };

function _ssEsc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c =>
        ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function _ssMetaInfo(id) {
    if (typeof SOURCE_LABELS !== 'undefined' && SOURCE_LABELS[id]) return SOURCE_LABELS[id];
    if (_SS_META_FALLBACK[id]) return _SS_META_FALLBACK[id];
    return { text: id, icon: '🎵' };
}

function _ssDownloadInfo(id) {
    if (typeof HYBRID_SOURCES !== 'undefined') {
        const h = HYBRID_SOURCES.find(s => s.id === id);
        if (h) return { name: h.name, logo: h.icon, emoji: h.emoji };
    }
    return { name: id, emoji: '⬇️' };
}

function openServiceSwitchModal(tab) {
    _ssState.tab = _SS_TABS.some(t => t.id === tab) ? tab : 'metadata';
    let overlay = document.getElementById('service-switch-overlay');
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = 'service-switch-overlay';
        overlay.className = 'modal-overlay ss-overlay hidden';
        overlay.onclick = (e) => { if (e.target === overlay) closeServiceSwitchModal(); };
        overlay.innerHTML = `
            <div class="ss-modal" role="dialog" aria-modal="true" aria-label="Active Sources" tabindex="-1">
                <div class="ss-topbar">
                    <div class="ss-topbar-icon"><img src="/static/trans2.png" alt="SoulSync" class="ss-topbar-logo"></div>
                    <div class="ss-topbar-titles">
                        <h3 class="ss-topbar-title">Active Sources</h3>
                        <div class="ss-topbar-sub" id="ss-topbar-sub">What this profile uses for metadata, library, and downloads</div>
                    </div>
                    <button class="ss-icon-btn ss-icon-btn--close" title="Close" onclick="closeServiceSwitchModal()">&times;</button>
                </div>
                <div class="ss-body">
                    <div class="ss-rail" id="ss-rail"></div>
                    <div class="ss-panel" id="ss-panel"></div>
                </div>
            </div>`;
        document.body.appendChild(overlay);
    }
    overlay.classList.remove('hidden', 'ss-closing');
    const modal = overlay.querySelector('.ss-modal');
    if (modal) { modal.classList.remove('ss-in'); void modal.offsetWidth; modal.classList.add('ss-in'); }
    document.addEventListener('keydown', _ssOnKeydown);
    _ssLoad();
}

function closeServiceSwitchModal() {
    const o = document.getElementById('service-switch-overlay');
    if (o) o.classList.add('hidden');
    document.removeEventListener('keydown', _ssOnKeydown);
}

function _ssOnKeydown(e) { if (e.key === 'Escape') closeServiceSwitchModal(); }

async function _ssLoad() {
    _ssRenderRail();
    const panel = document.getElementById('ss-panel');
    if (panel) panel.innerHTML = '<div class="ss-empty">Loading…</div>';
    try {
        const res = await fetch('/api/profiles/me/active-sources');
        _ssState.data = await res.json();
    } catch (e) {
        _ssState.data = null;
    }
    _ssRenderRail();   // re-render now that we know each tab's active choice
    _ssRenderPanel();
}

function _ssRailCurrent(tabId) {
    // The active choice for a tab → {logo/emoji, label, brand} for the rail chip.
    const d = _ssState.data;
    if (!d || !d.success) return null;
    if (tabId === 'metadata') {
        const id = d.metadata.active; const info = _ssMetaInfo(id);
        return { logo: info.logo, emoji: info.icon, label: info.text || id, brand: _ssBrand(id) };
    }
    if (tabId === 'server') {
        const id = d.server.active; const info = _SS_SERVER_INFO[id] || { name: id };
        return { logo: info.logo, emoji: '🖥️', label: info.name, brand: _ssBrand(id), dark: info.dark };
    }
    const id = d.download.mode;
    if (id === 'hybrid') return { emoji: '🔀', label: 'Hybrid', brand: 'var(--accent-light-rgb-hex,#7c5cff)' };
    const info = _ssDownloadInfo(id);
    return { logo: info.logo, emoji: info.emoji, label: info.name, brand: _ssBrand(id) };
}

function _ssRenderRail() {
    const rail = document.getElementById('ss-rail');
    if (!rail) return;
    rail.innerHTML = _SS_TABS.map(t => {
        const cur = _ssRailCurrent(t.id);
        const media = cur
            ? (cur.logo
                ? `<img class="ss-tab-logo" src="${cur.logo}" onerror="this.outerHTML='<span class=\\'ss-tab-emoji\\'>${cur.emoji}</span>'">`
                : `<span class="ss-tab-emoji">${cur.emoji}</span>`)
            : `<span class="ss-tab-emoji">${t.emoji}</span>`;
        return `
            <button class="ss-tab${t.id === _ssState.tab ? ' active' : ''}" style="--ss-brand:${cur ? cur.brand : '#7c5cff'}"
                    onclick="switchServiceSwitchTab('${t.id}')">
                <span class="ss-tab-disc${cur && cur.dark ? ' ss-disc--dark' : ''}">${media}</span>
                <span class="ss-tab-text">
                    <span class="ss-tab-cat">${t.name}</span>
                    <span class="ss-tab-cur">${cur ? _ssEsc(cur.label) : '…'}</span>
                </span>
            </button>`;
    }).join('');
}

function switchServiceSwitchTab(tab) {
    _ssState.tab = tab;
    _ssRenderRail();
    _ssRenderPanel();
}

function _ssCard({ logo, emoji, label, active, available, onclick, badge, brand, dark }) {
    const dim = available === false ? ' ss-card--locked' : '';
    const act = active ? ' active' : '';
    const media = logo
        ? `<img class="ss-card-logo" src="${logo}" alt="" onerror="this.outerHTML='<span class=\\'ss-card-emoji\\'>${emoji || '🎵'}</span>'">`
        : `<span class="ss-card-emoji">${emoji || '🎵'}</span>`;
    return `
        <button class="ss-card${act}${dim}" style="--ss-brand:${brand || '#7c5cff'}" ${onclick ? `onclick="${onclick}"` : 'disabled'}>
            <span class="ss-card-disc${dark ? ' ss-disc--dark' : ''}">${media}</span>
            <span class="ss-card-label">${_ssEsc(label)}</span>
            ${badge ? `<span class="ss-card-badge">${_ssEsc(badge)}</span>` : ''}
            ${active ? '<span class="ss-card-check">✓</span>' : ''}
        </button>`;
}

const _SS_TAB_BLURB = {
    metadata: 'Where artist, album & track details come from.',
    server: 'The library backend SoulSync reads and writes.',
    download: 'Where SoulSync grabs tracks you don\'t have yet.',
};

function _ssHero(kind) {
    const cur = _ssRailCurrent(kind);
    if (!cur) return '';
    const media = cur.logo
        ? `<img class="ss-hero-logo" src="${cur.logo}" onerror="this.outerHTML='<span class=\\'ss-hero-emoji\\'>${cur.emoji}</span>'">`
        : `<span class="ss-hero-emoji">${cur.emoji}</span>`;
    const eyebrow = kind === 'metadata' ? 'Active metadata source'
        : kind === 'server' ? 'Active media server' : 'Active download source';
    return `
        <div class="ss-hero" style="--ss-brand:${cur.brand}">
            <div class="ss-hero-disc${cur.dark ? ' ss-disc--dark' : ''}">${media}</div>
            <div class="ss-hero-info">
                <div class="ss-hero-eyebrow">${eyebrow}</div>
                <div class="ss-hero-name">${_ssEsc(cur.label)}</div>
                <div class="ss-hero-sub">${_SS_TAB_BLURB[kind] || ''}</div>
            </div>
            <span class="ss-hero-pill">Active</span>
        </div>`;
}

function _ssRenderPanel() {
    const panel = document.getElementById('ss-panel');
    const d = _ssState.data;
    if (!panel) return;
    if (!d || !d.success) { panel.innerHTML = '<div class="ss-empty">Could not load active sources.</div>'; return; }
    const editable = !!d.editable;
    panel.style.setProperty('--ss-brand', (_ssRailCurrent(_ssState.tab) || {}).brand || '#7c5cff');
    const sub = document.getElementById('ss-topbar-sub');
    if (sub) sub.textContent = editable
        ? 'What this profile uses for metadata, library, and downloads'
        : 'Set by the admin — view only for now';

    if (_ssState.tab === 'metadata') {
        const cards = d.metadata.options.map(o => {
            const info = _ssMetaInfo(o.id);
            return _ssCard({
                logo: info.logo, emoji: info.icon, label: info.text || o.id, brand: _ssBrand(o.id),
                active: d.metadata.active === o.id, available: o.available,
                onclick: (editable && o.available) ? `setActiveSource('metadata','${o.id}')` : null,
            });
        }).join('');
        // Surface the EFFECTIVE source when it differs from the configured one
        // (e.g. configured Spotify but not authenticated → running on a fallback).
        const eff = d.metadata.effective;
        const note = (eff && eff !== d.metadata.active)
            ? `<div class="ss-effective-note">Configured source isn't connected — actually using <b>${_ssEsc((_ssMetaInfo(eff).text) || eff)}</b> right now.</div>`
            : '';
        panel.innerHTML = `${_ssHero('metadata')}<div class="ss-section-title">Choose source</div>${note}<div class="ss-grid">${cards}</div>`;
    } else if (_ssState.tab === 'server') {
        const cards = d.server.options.map(o => {
            const info = _SS_SERVER_INFO[o.id] || { name: o.id };
            return _ssCard({
                logo: info.logo, emoji: '🖥️', label: info.name, brand: _ssBrand(o.id), dark: info.dark,
                active: d.server.active === o.id, available: o.available,
                onclick: (editable && o.available) ? `setActiveSource('server','${o.id}')` : null,
            });
        }).join('');
        panel.innerHTML = `${_ssHero('server')}<div class="ss-section-title">Choose server</div><div class="ss-grid">${cards}</div>`;
    } else {
        _ssRenderDownloadPanel(panel, d, editable);
    }
}

function _ssRenderDownloadPanel(panel, d, editable) {
    const isHybrid = d.download.mode === 'hybrid';
    const toggle = `
        <div class="ss-seg">
            <button class="ss-seg-btn${!isHybrid ? ' active' : ''}" ${editable ? `onclick="setDownloadMode('single')"` : 'disabled'}>Single source</button>
            <button class="ss-seg-btn${isHybrid ? ' active' : ''}" ${editable ? `onclick="setDownloadMode('hybrid')"` : 'disabled'}>Hybrid</button>
        </div>`;

    let body;
    if (isHybrid) {
        const order = (d.download.hybrid_order && d.download.hybrid_order.length)
            ? d.download.hybrid_order
            : d.download.options.map(o => o.id);
        body = `<div class="ss-hint">Drag to set priority — SoulSync tries each in order.</div>
            <div class="ss-hybrid-list" id="ss-hybrid-list">` +
            order.map((id, i) => {
                const info = _ssDownloadInfo(id);
                return `<div class="ss-hybrid-item" draggable="${editable}" data-src="${id}">
                    <span class="ss-hybrid-rank">${i + 1}</span>
                    ${info.logo ? `<img class="ss-hybrid-logo" src="${info.logo}" onerror="this.outerHTML='<span class=\\'ss-card-emoji\\'>${info.emoji}</span>'">` : `<span class="ss-card-emoji">${info.emoji}</span>`}
                    <span class="ss-hybrid-name">${_ssEsc(info.name)}</span>
                </div>`;
            }).join('') + `</div>`;
    } else {
        const cards = d.download.options.map(o => {
            const info = _ssDownloadInfo(o.id);
            return _ssCard({
                logo: info.logo, emoji: info.emoji, label: info.name, brand: _ssBrand(o.id),
                active: d.download.mode === o.id, available: true,
                onclick: editable ? `setActiveSource('download','${o.id}')` : null,
            });
        }).join('');
        body = `<div class="ss-grid">${cards}</div>`;
    }
    panel.innerHTML = `${_ssHero('download')}<div class="ss-section-title">Choose source</div>${toggle}${body}`;
    if (isHybrid && editable) _ssWireHybridDrag();
}

function _ssWireHybridDrag() {
    const list = document.getElementById('ss-hybrid-list');
    if (!list) return;
    list.querySelectorAll('.ss-hybrid-item').forEach(item => {
        item.addEventListener('dragstart', (e) => {
            e.dataTransfer.effectAllowed = 'move';
            e.dataTransfer.setData('text/plain', item.dataset.src);
            item.classList.add('dragging');
        });
        item.addEventListener('dragend', () => item.classList.remove('dragging'));
        item.addEventListener('dragover', (e) => { e.preventDefault(); });
        item.addEventListener('drop', (e) => {
            e.preventDefault();
            const dragged = e.dataTransfer.getData('text/plain');
            if (dragged && dragged !== item.dataset.src) _ssReorderHybrid(dragged, item.dataset.src);
        });
    });
}

function _ssReorderHybrid(draggedId, targetId) {
    const order = (_ssState.data.download.hybrid_order && _ssState.data.download.hybrid_order.length)
        ? _ssState.data.download.hybrid_order.slice()
        : _ssState.data.download.options.map(o => o.id);
    const from = order.indexOf(draggedId);
    if (from < 0) return;
    order.splice(from, 1);
    const to = order.indexOf(targetId);
    order.splice(to < 0 ? order.length : to, 0, draggedId);
    _ssSave({ hybrid_order: order });
}

async function setActiveSource(kind, id) {
    const key = kind === 'metadata' ? 'metadata_source' : kind === 'server' ? 'media_server' : 'download_mode';
    await _ssSave({ [key]: id });
}

async function setDownloadMode(which) {
    if (which === 'hybrid') {
        await _ssSave({ download_mode: 'hybrid' });
    } else {
        // Switch to a single source — keep the current single choice if it was
        // already single, else default to the first option.
        const d = _ssState.data;
        const cur = d.download.mode;
        const single = (cur && cur !== 'hybrid') ? cur : (d.download.options[0] && d.download.options[0].id) || 'soulseek';
        await _ssSave({ download_mode: single });
    }
}

async function _ssSave(patch) {
    try {
        const res = await fetch('/api/profiles/active-sources', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(patch),
        });
        const data = await res.json();
        if (!data.success) {
            if (typeof showToast === 'function') showToast(data.error || 'Change failed', 'error');
            return;
        }
        if (typeof showToast === 'function') showToast('Updated', 'success');
        await _ssLoad();  // re-read + re-render with the new active state
        if (typeof fetchAndUpdateServiceStatus === 'function') fetchAndUpdateServiceStatus();
    } catch (e) {
        if (typeof showToast === 'function') showToast('Change failed', 'error');
    }
}
