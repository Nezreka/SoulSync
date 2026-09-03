/**
 * My Accounts - per-profile self-auth for playlist services. Ported from
 * webui/static/my-accounts.js.
 *
 * Each profile connects its OWN streaming accounts (their token, the app's
 * shared client). Used for that profile's playlist operations; the global/
 * admin auth keeps running the background app.
 *
 * Backend: GET /api/profiles/me/connections, the per-service OAuth popups,
 * and POST /api/profiles/me/connections/<service>/disconnect.
 *
 * NOTE: keeps the vanilla's native confirm() on disconnect - 1:1 port,
 * showConfirmDialog conversion is its own follow-up.
 */

import { escapeHtml, toast } from './html';

interface MaService {
  id: string;
  name: string;
  brand: string;
  logo: string;
  dark?: boolean;
  type?: 'token';
  saveUrl?: string;
  hint?: string;
  connect?: (pid: number) => string;
}

// Playlist services shown in My Accounts. `connect` returns the OAuth URL for
// a given profile id (popup); services are wired in over time.
const _MA_SERVICES: MaService[] = [
  {
    id: 'spotify', name: 'Spotify', brand: '#1db954',
    logo: '/static/img/brands/spotify.png',
    connect: (pid) => `/auth/spotify?profile_id=${pid}`,
  },
  {
    id: 'tidal', name: 'Tidal', brand: '#00cfe8',
    logo: '/static/img/brands/tidal.png',
    connect: (pid) => `/auth/tidal?profile_id=${pid}`,
  },
  {
    id: 'listenbrainz', name: 'ListenBrainz', brand: '#eb743b', dark: true,
    logo: '/static/img/brands/listenbrainz.png',
    type: 'token',
    saveUrl: '/api/profiles/me/listenbrainz',
    hint: 'Paste your token from listenbrainz.org/profile',
  },
];

function _maProfileId(): number {
  try {
    const ctx = window.getCurrentProfileContext?.();
    return ctx ? ctx.profileId : 1;
  } catch {
    return 1;
  }
}

export function openMyAccountsModal(): void {
  let overlay = document.getElementById('my-accounts-overlay');
  if (!overlay) {
    overlay = document.createElement('div');
    overlay.id = 'my-accounts-overlay';
    overlay.className = 'modal-overlay ma-overlay hidden';
    overlay.onclick = (e) => {
      if (e.target === overlay) closeMyAccountsModal();
    };
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
  if (modal) {
    modal.classList.remove('ma-in');
    void (modal as HTMLElement).offsetWidth;
    modal.classList.add('ma-in');
  }
  document.addEventListener('keydown', _maOnKeydown);
  void _maLoad();
}

export function closeMyAccountsModal(): void {
  const o = document.getElementById('my-accounts-overlay');
  if (o) o.classList.add('hidden');
  document.removeEventListener('keydown', _maOnKeydown);
}

function _maOnKeydown(e: KeyboardEvent): void {
  if (e.key === 'Escape') closeMyAccountsModal();
}

interface MaConnections {
  connections?: Record<string, { connected?: boolean; account?: string }>;
  is_admin?: boolean;
}

async function _maLoad(): Promise<void> {
  const body = document.getElementById('ma-body');
  if (body) body.innerHTML = '<div class="ma-empty">Loading…</div>';
  let data: MaConnections | null = null;
  try {
    data = (await (await fetch('/api/profiles/me/connections')).json()) as MaConnections;
  } catch {
    /* render disconnected */
  }
  _maRender(body, data || { connections: {}, is_admin: false });
}

function _maRender(body: HTMLElement | null, data: MaConnections): void {
  if (!body) return;
  const conns = data.connections || {};
  const isAdmin = !!data.is_admin;
  const rows = _MA_SERVICES.map((svc) => {
    const c = conns[svc.id] || {};
    const connected = !!c.connected;
    // Admin uses the global app account (set up in Settings) for every
    // service — not a personal connection here.
    const adminNote = isAdmin;
    let action: string;
    if (adminNote) {
      action = `<span class="ma-note">Managed in Settings (app account)</span>`;
    } else if (connected) {
      action = `
                <span class="ma-account">${escapeHtml(c.account || 'Connected')}</span>
                <button class="ma-btn ma-btn--ghost" onclick="disconnectMyAccount('${svc.id}')">Disconnect</button>`;
    } else if (svc.type === 'token') {
      action = `
                <input type="password" class="ma-token-input" id="ma-token-${svc.id}" placeholder="Paste token"
                       title="${escapeHtml(svc.hint || '')}">
                <button class="ma-btn ma-btn--connect" onclick="saveMyAccountToken('${svc.id}')">Save</button>`;
    } else {
      action = `<button class="ma-btn ma-btn--connect" onclick="connectMyAccount('${svc.id}')">Connect</button>`;
    }
    return `
            <div class="ma-row" style="--ma-brand:${svc.brand}">
                <span class="ma-disc${svc.dark ? ' ma-disc--dark' : ''}"><img class="ma-logo" src="${svc.logo}" alt=""
                      onerror="this.style.display='none'"></span>
                <div class="ma-row-info">
                    <div class="ma-row-name">${escapeHtml(svc.name)}</div>
                    <div class="ma-row-status ${connected ? 'is-on' : ''}">${connected ? 'Connected' : (adminNote ? '' : 'Not connected')}</div>
                </div>
                <div class="ma-row-action">${action}</div>
            </div>`;
  }).join('');
  body.innerHTML = rows || '<div class="ma-empty">No services available.</div>';
}

let _maPollTimer: ReturnType<typeof setInterval> | null = null;

export function connectMyAccount(serviceId: string): void {
  const svc = _MA_SERVICES.find((s) => s.id === serviceId);
  if (!svc || !svc.connect) return;
  const pid = _maProfileId();
  const popup = window.open(svc.connect(pid), 'soulsync-connect-' + serviceId,
    'width=560,height=720,menubar=no,toolbar=no');
  // Poll for the popup closing, then refresh status.
  if (_maPollTimer) clearInterval(_maPollTimer);
  _maPollTimer = setInterval(() => {
    if (!popup || popup.closed) {
      if (_maPollTimer) clearInterval(_maPollTimer);
      _maPollTimer = null;
      setTimeout(() => void _maLoad(), 600); // give the callback a moment to persist
    }
  }, 800);
}

export async function saveMyAccountToken(serviceId: string): Promise<void> {
  const svc = _MA_SERVICES.find((s) => s.id === serviceId);
  if (!svc || !svc.saveUrl) return;
  const input = document.getElementById(`ma-token-${serviceId}`) as HTMLInputElement | null;
  const token = ((input && input.value) || '').trim();
  if (!token) {
    toast('Paste a token first', 'info');
    return;
  }
  try {
    const res = await fetch(svc.saveUrl, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token }),
    });
    const data = (await res.json()) as { success?: boolean; error?: string };
    if (data.success) {
      toast(`${svc.name} connected`, 'success');
      void _maLoad();
    } else {
      toast(data.error || 'Could not connect', 'error');
    }
  } catch {
    toast('Could not connect', 'error');
  }
}

export async function disconnectMyAccount(serviceId: string): Promise<void> {
  if (!confirm(`Disconnect your ${serviceId} account from this profile?`)) return;
  try {
    const res = await fetch(`/api/profiles/me/connections/${serviceId}/disconnect`, { method: 'POST' });
    const data = (await res.json()) as { success?: boolean; error?: string };
    if (data.success) {
      toast('Disconnected', 'success');
      void _maLoad();
    } else {
      toast(data.error || 'Disconnect failed', 'error');
    }
  } catch {
    /* no-op */
  }
}
