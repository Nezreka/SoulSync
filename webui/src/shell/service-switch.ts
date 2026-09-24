/**
 * Quick-switch modal - active Metadata / Server / Download source selection.
 * Ported from webui/static/service-switch.js.
 *
 * Opens from the sidebar Service Status panel; styled after the Manage
 * Workers hub (topbar + rail + panel, brand-logo cards). The metadata source
 * switches here (admin, global, same as Settings). The media server and the
 * download chain are shown, not switched (#1301): a server flip means a fresh
 * library scan, and the chain has its own editor in Settings that a second
 * copy here kept drifting from. Each gets one way into the right Settings spot.
 *
 * SOURCE_LABELS (shared-helpers.js) and HYBRID_SOURCES (settings.js) are
 * top-level consts in still-classic scripts - global LEXICAL bindings, not
 * window properties - so they are reached as bare names through the global
 * scope chain with the vanilla's typeof guards, declared ambiently below.
 * When those files port into the shell the declares migrate with them.
 */

import { escapeHtml, toast } from './html';

declare global {
  // eslint-disable-next-line no-var
  var SOURCE_LABELS: Record<string, { text?: string; icon?: string; logo?: string }> | undefined;
  // eslint-disable-next-line no-var
  var HYBRID_SOURCES: Array<{ id: string; name: string; icon?: string | null; emoji?: string }> | undefined;
  // the last /status frame (core.js), kept fresh by the poller and the socket
  // eslint-disable-next-line no-var
  var _lastStatusPayload: SsStatusPayload | null | undefined;
}

interface SsStatusPayload {
  media_server?: { connected?: boolean; response_time?: number; type?: string | null };
}

const _SS_TABS = [
  { id: 'metadata', name: 'Metadata', emoji: '🎼' },
  { id: 'server', name: 'Server', emoji: '🖥️' },
  { id: 'download', name: 'Download', emoji: '⬇️' },
] as const;

// Brand logos. Metadata pulls from SOURCE_LABELS (shared-helpers.js) when
// available; server + download have their own small maps.
const _SS_SERVER_INFO: Record<string, { name: string; logo?: string; dark?: boolean }> = {
  // `dark`: the logo is a white/light wordmark, so it needs a dark disc to be
  // visible (it'd vanish on the default white disc).
  plex: { name: 'Plex', logo: '/static/img/brands/plex.png', dark: true },
  jellyfin: { name: 'Jellyfin', logo: '/static/img/brands/jellyfin.png' },
  navidrome: { name: 'Navidrome', logo: '/static/img/brands/navidrome.png' },
  soulsync: { name: 'SoulSync', logo: '/static/trans2.png', dark: true },
};
const _SS_META_FALLBACK: Record<string, { text: string; icon: string; logo?: string }> = {
  spotify_free: { text: 'Spotify (no auth)', icon: '🆓', logo: '/static/img/brands/spotify.png' },
};
// Brand colors drive each card's logo ring + active glow (the Manage-Workers feel).
const _SS_BRAND: Record<string, string> = {
  spotify: '#1db954', spotify_free: '#1db954', itunes: '#fc5c7d', deezer: '#a238ff',
  discogs: '#ff5500', musicbrainz: '#ba478f', amazon: '#ff9900', jiosaavn: '#2bc5b4',
  plex: '#e5a00d', jellyfin: '#aa5cc3', navidrome: '#3b6cf6', soulsync: '#7c5cff',
  soulseek: '#22a7f0', youtube: '#ff0000', tidal: '#00cfe8', qobuz: '#0a6e9e',
  hifi: '#16c79a', torrent: '#8a2be2', usenet: '#e67e22',
  deezer_dl: '#a238ff', lidarr: '#3fbf6e', soundcloud: '#ff5500',
};
function _ssBrand(id: string): string {
  return _SS_BRAND[id] || 'var(--accent-light-rgb-hex, #7c5cff)';
}

interface SsActiveSources {
  success?: boolean;
  editable?: boolean;
  metadata: { active: string; effective?: string; options: Array<{ id: string; available?: boolean }> };
  server: { active: string; options: Array<{ id: string; available?: boolean }> };
  download: { mode: string; hybrid_order?: string[]; chain?: Array<{ id: string; ready?: boolean | null }> };
}

const _ssState: { tab: string; data: SsActiveSources | null } = { tab: 'metadata', data: null };

function _ssMetaInfo(id: string): { text?: string; icon?: string; logo?: string } {
  if (typeof SOURCE_LABELS !== 'undefined' && SOURCE_LABELS && SOURCE_LABELS[id]) return SOURCE_LABELS[id];
  if (_SS_META_FALLBACK[id]) return _SS_META_FALLBACK[id];
  return { text: id, icon: '🎵' };
}

// names for when settings.js (HYBRID_SOURCES) isn't loaded yet
const _SS_DL_FALLBACK: Record<string, string> = {
  soulseek: 'Soulseek', youtube: 'YouTube', tidal: 'Tidal', qobuz: 'Qobuz', hifi: 'HiFi',
  deezer_dl: 'Deezer', amazon: 'Amazon Music', lidarr: 'Lidarr', soundcloud: 'SoundCloud',
  torrent: 'Torrent', usenet: 'Usenet',
};

function _ssDownloadInfo(id: string): { name: string; logo?: string; emoji?: string } {
  if (typeof HYBRID_SOURCES !== 'undefined' && HYBRID_SOURCES) {
    const h = HYBRID_SOURCES.find((s) => s.id === id);
    if (h) return { name: h.name, logo: h.icon || undefined, emoji: h.emoji };
  }
  return { name: _SS_DL_FALLBACK[id] || id, emoji: '⬇️' };
}

function _ssServerStatus(): SsStatusPayload['media_server'] | null {
  if (typeof _lastStatusPayload === 'undefined' || !_lastStatusPayload) return null;
  return _lastStatusPayload.media_server || null;
}

export function openServiceSwitchModal(tab?: string): void {
  // Admin-only: active metadata source / media server / download source are
  // app-wide infrastructure. Non-admins manage their own playlist accounts
  // elsewhere (per-profile), not here.
  try {
    const ctx = window.getCurrentProfileContext?.();
    if (ctx && !ctx.isAdmin) {
      toast('Only the admin can change the active sources', 'info');
      return;
    }
  } catch {
    /* if context unknown, fall through (defaults to admin) */
  }
  _ssState.tab = _SS_TABS.some((t) => t.id === tab) ? (tab as string) : 'metadata';
  let overlay = document.getElementById('service-switch-overlay');
  if (!overlay) {
    overlay = document.createElement('div');
    overlay.id = 'service-switch-overlay';
    overlay.className = 'modal-overlay ss-overlay hidden';
    overlay.onclick = (e) => {
      if (e.target === overlay) closeServiceSwitchModal();
    };
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
  if (modal) {
    modal.classList.remove('ss-in');
    void (modal as HTMLElement).offsetWidth;
    modal.classList.add('ss-in');
  }
  document.addEventListener('keydown', _ssOnKeydown);
  window.addEventListener('ss:service-status', _ssOnStatus);
  void _ssLoad();
}

export function closeServiceSwitchModal(): void {
  const o = document.getElementById('service-switch-overlay');
  if (o) o.classList.add('hidden');
  document.removeEventListener('keydown', _ssOnKeydown);
  window.removeEventListener('ss:service-status', _ssOnStatus);
}

// a fresh /status frame while the server tab is up repaints its live pill
function _ssOnStatus(): void {
  if (_ssState.tab === 'server' && _ssState.data) _ssRenderPanel();
}

function _ssOnKeydown(e: KeyboardEvent): void {
  if (e.key === 'Escape') closeServiceSwitchModal();
}

async function _ssLoad(): Promise<void> {
  _ssRenderRail();
  const panel = document.getElementById('ss-panel');
  if (panel) panel.innerHTML = '<div class="ss-empty">Loading…</div>';
  try {
    const res = await fetch('/api/profiles/me/active-sources');
    _ssState.data = (await res.json()) as SsActiveSources;
  } catch {
    _ssState.data = null;
  }
  _ssRenderRail(); // re-render now that we know each tab's active choice
  _ssRenderPanel();
}

interface SsRailChip {
  logo?: string;
  emoji?: string;
  label: string;
  brand: string;
  dark?: boolean;
}

function _ssRailCurrent(tabId: string): SsRailChip | null {
  // The active choice for a tab → {logo/emoji, label, brand} for the rail chip.
  const d = _ssState.data;
  if (!d || !d.success) return null;
  if (tabId === 'metadata') {
    const id = d.metadata.active;
    const info = _ssMetaInfo(id);
    return { logo: info.logo, emoji: info.icon, label: info.text || id, brand: _ssBrand(id) };
  }
  if (tabId === 'server') {
    const id = d.server.active;
    const info = _SS_SERVER_INFO[id] || { name: id };
    return { logo: info.logo, emoji: '🖥️', label: info.name, brand: _ssBrand(id), dark: info.dark };
  }
  const id = d.download.mode;
  if (id === 'hybrid') return { emoji: '🔀', label: 'Hybrid', brand: 'var(--accent-light-rgb-hex,#7c5cff)' };
  const info = _ssDownloadInfo(id);
  return { logo: info.logo, emoji: info.emoji, label: info.name, brand: _ssBrand(id) };
}

function _ssRenderRail(): void {
  const rail = document.getElementById('ss-rail');
  if (!rail) return;
  rail.innerHTML = _SS_TABS.map((t) => {
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
                    <span class="ss-tab-cur">${cur ? escapeHtml(cur.label) : '…'}</span>
                </span>
            </button>`;
  }).join('');
}

export function switchServiceSwitchTab(tab: string): void {
  _ssState.tab = tab;
  _ssRenderRail();
  _ssRenderPanel();
}

interface SsCardArgs {
  logo?: string;
  emoji?: string;
  label: string;
  active?: boolean;
  available?: boolean;
  onclick?: string | null;
  badge?: string;
  brand?: string;
  dark?: boolean;
}

function _ssCard({ logo, emoji, label, active, available, onclick, badge, brand, dark }: SsCardArgs): string {
  const dim = available === false ? ' ss-card--locked' : '';
  const act = active ? ' active' : '';
  const media = logo
    ? `<img class="ss-card-logo" src="${logo}" alt="" onerror="this.outerHTML='<span class=\\'ss-card-emoji\\'>${emoji || '🎵'}</span>'">`
    : `<span class="ss-card-emoji">${emoji || '🎵'}</span>`;
  return `
        <button class="ss-card${act}${dim}" style="--ss-brand:${brand || '#7c5cff'}" ${onclick ? `onclick="${onclick}"` : 'disabled'}>
            <span class="ss-card-disc${dark ? ' ss-disc--dark' : ''}">${media}</span>
            <span class="ss-card-label">${escapeHtml(label)}</span>
            ${badge ? `<span class="ss-card-badge">${escapeHtml(badge)}</span>` : ''}
            ${active ? '<span class="ss-card-check">✓</span>' : ''}
        </button>`;
}

const _SS_TAB_BLURB: Record<string, string> = {
  metadata: 'Where artist, album & track details come from.',
  server: 'The library backend SoulSync reads and writes.',
  download: 'Where SoulSync grabs tracks you don\'t have yet.',
};

interface SsPill {
  label: string;
  tone?: 'ok' | 'bad' | 'wait';
}

function _ssHero(kind: string, pill: SsPill = { label: 'Active' }, sub?: string): string {
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
                <div class="ss-hero-name">${escapeHtml(cur.label)}</div>
                <div class="ss-hero-sub">${escapeHtml(sub ?? _SS_TAB_BLURB[kind] ?? '')}</div>
            </div>
            <span class="ss-hero-pill${pill.tone ? ` ss-hero-pill--${pill.tone}` : ''}">${pill.tone ? '<span class="ss-pulse"></span>' : ''}${escapeHtml(pill.label)}</span>
        </div>`;
}

function _ssRenderPanel(): void {
  const panel = document.getElementById('ss-panel');
  const d = _ssState.data;
  if (!panel) return;
  if (!d || !d.success) {
    panel.innerHTML = '<div class="ss-empty">Could not load active sources.</div>';
    return;
  }
  const editable = !!d.editable;
  panel.style.setProperty('--ss-brand', (_ssRailCurrent(_ssState.tab) || { brand: '#7c5cff' }).brand);
  const sub = document.getElementById('ss-topbar-sub');
  if (sub) sub.textContent = editable
    ? 'What this profile uses for metadata, library, and downloads'
    : 'Set by the admin — view only for now';

  if (_ssState.tab === 'metadata') {
    const cards = d.metadata.options.map((o) => {
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
      ? `<div class="ss-effective-note">Configured source isn't connected — actually using <b>${escapeHtml((_ssMetaInfo(eff).text) || eff)}</b> right now.</div>`
      : '';
    panel.innerHTML = `${_ssHero('metadata')}<div class="ss-section-title">Choose source</div>${note}<div class="ss-grid">${cards}</div>`;
  } else if (_ssState.tab === 'server') {
    panel.innerHTML = _ssServerPanel(d, editable);
  } else {
    panel.innerHTML = _ssDownloadPanel(d, editable);
  }
}

function _ssFoot(kind: 'server' | 'download', note: string, editable: boolean): string {
  const label = kind === 'server' ? 'Change in Settings' : 'Edit in Settings';
  return `
        <div class="ss-foot">
            <p class="ss-foot-note">${note}</p>
            ${editable ? `<button class="ss-cta" onclick="openServiceSwitchSettings('${kind}')">${label}<span class="ss-cta-arrow" aria-hidden="true">&rarr;</span></button>` : ''}
        </div>`;
}

function _ssServerPanel(d: SsActiveSources, editable: boolean): string {
  const status = _ssServerStatus();
  // only trust a status frame about THIS server; a stale one from before a
  // switch would claim the old server's health
  const known = !!status && (!status.type || status.type === d.server.active);
  let pill: SsPill = { label: 'Checking', tone: 'wait' };
  let health = 'Waiting for the next status check';
  if (known && status!.connected) {
    const ms = Math.round(status!.response_time || 0);
    pill = { label: 'Online', tone: 'ok' };
    health = ms > 0 ? `Connected, answered in ${ms} ms` : 'Connected';
  } else if (known) {
    pill = { label: 'Offline', tone: 'bad' };
    health = "Not answering. Check it's running and reachable";
  }
  const others = d.server.options
    .filter((o) => o.id !== d.server.active && o.available)
    .map((o) => (_SS_SERVER_INFO[o.id] || { name: o.id }).name);
  const facts = `
        <div class="ss-facts">
            <div class="ss-fact">
                <span class="ss-fact-k">Status</span>
                <span class="ss-fact-v"><span class="ss-dot ss-dot--${pill.tone}"></span>${escapeHtml(health)}</span>
            </div>
            <div class="ss-fact">
                <span class="ss-fact-k">Also set up</span>
                <span class="ss-fact-v${others.length ? '' : ' ss-fact-v--quiet'}">${others.length ? escapeHtml(others.join(', ')) : 'No other servers'}</span>
            </div>
        </div>`;
  return _ssHero('server', pill) + facts + _ssFoot('server',
    'Switching servers starts your library over with a fresh scan, so it lives in Settings.', editable);
}

function _ssDownloadPanel(d: SsActiveSources, editable: boolean): string {
  const chain = d.download.chain || [];
  const hybrid = chain.length > 1;
  const pill: SsPill = { label: hybrid ? `${chain.length} sources` : 'Single source' };
  const sub = hybrid ? 'Tried top to bottom until one has the file.'
    : chain.length ? 'Every download comes from here.' : 'No download source set yet.';
  const steps = chain.map((c, i) => {
    const info = _ssDownloadInfo(c.id);
    const media = info.logo
      ? `<img class="ss-chain-logo" src="${info.logo}" alt="" onerror="this.outerHTML='<span class=\'ss-card-emoji\'>${info.emoji || '⬇️'}</span>'">`
      : `<span class="ss-card-emoji">${info.emoji || '⬇️'}</span>`;
    const chip = c.ready === true ? '<span class="ss-chip ss-chip--ok">Ready</span>'
      : c.ready === false ? '<span class="ss-chip ss-chip--warn">Needs setup</span>' : '';
    return `
            <li class="ss-chain-step" style="--ss-brand:${_ssBrand(c.id)}">
                <span class="ss-chain-rank">${i + 1}</span>
                <span class="ss-chain-disc">${media}</span>
                <span class="ss-chain-name">${escapeHtml(info.name)}</span>
                ${chip}
            </li>`;
  }).join('');
  const list = chain.length
    ? `<div class="ss-section-title">${hybrid ? 'Download chain' : 'Download source'}</div><ol class="ss-chain">${steps}</ol>`
    : '';
  const note = hybrid
    ? 'Add, remove or reorder sources in Settings. Down to one and it goes back to single source.'
    : 'Add a second source in Settings and downloads go hybrid, trying each in turn.';
  return _ssHero('download', pill, sub) + list + _ssFoot('download', note, editable);
}

// where each "in Settings" button lands: the tab, the music side of it, and
// the section to bring into view
const _SS_SETTINGS_TARGET: Record<string, { tab: string; side: () => void; selector: string; group: boolean }> = {
  server: { tab: 'connections', side: () => window.switchServiceKind?.('music'), selector: '#plex-toggle', group: true },
  download: { tab: 'downloads', side: () => window.switchDownloadChain?.('music'), selector: '#download-chain-widget', group: false },
};

export function openServiceSwitchSettings(kind: string): void {
  const target = _SS_SETTINGS_TARGET[kind];
  if (!target) return;
  closeServiceSwitchModal();
  window.navigateToPage?.('settings');
  window.setTimeout(() => {
    try {
      window.switchSettingsTab?.(target.tab);
      target.side();
    } catch {
      /* best effort: the page is still open on settings */
    }
    window.setTimeout(() => {
      const el = document.querySelector(target.selector);
      const spot = (target.group ? el?.closest('.settings-group') : el) || el;
      if (!spot) return;
      spot.scrollIntoView({ behavior: 'smooth', block: 'start' });
      spot.classList.add('ss-landed');
      window.setTimeout(() => spot.classList.remove('ss-landed'), 2200);
    }, 140);
  }, 80);
}

export async function setActiveSource(kind: string, id: string): Promise<void> {
  // only the metadata source switches here; server + downloads live in Settings
  if (kind !== 'metadata') return;
  await _ssSave({ metadata_source: id });
}

async function _ssSave(patch: Record<string, unknown>): Promise<void> {
  try {
    const res = await fetch('/api/profiles/active-sources', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    });
    const data = (await res.json()) as { success?: boolean; error?: string };
    if (!data.success) {
      toast(data.error || 'Change failed', 'error');
      return;
    }
    toast('Updated', 'success');
    await _ssLoad(); // re-read + re-render with the new active state
    window.fetchAndUpdateServiceStatus?.();
  } catch {
    toast('Change failed', 'error');
  }
}
