/**
 * My Account - one modal for everything that's yours on this install.
 *
 * used to be two: My Accounts (streaming logins) and My Settings (who you are
 * on the media server, vanilla init.js). two icons, no obvious difference, and
 * after #1293 your navidrome login and your last.fm sat behind different ones.
 * now it's one: who you are on the media server, then your music services.
 *
 * rows stay calm until you touch them. a connect or a manage opens that row in
 * place, nothing else on the page moves. the listening card up top says whose
 * history your stats read (#1293).
 *
 * the admin never gets this, it uses Settings (the sidebar hides the button).
 *
 * Backend: GET /api/profiles/me/connections, the per-service save/OAuth
 * routes, POST /api/profiles/me/connections/<service>/disconnect, and the
 * media-server routes the old My Settings used.
 */

import { escapeHtml, toast } from './html';

type ServiceId = 'spotify' | 'tidal' | 'listenbrainz' | 'lastfm';

interface MaService {
  id: ServiceId;
  name: string;
  logo: string;
  /** what the row says when you're not connected */
  blurb: string;
  /** oauth opens a popup; token and username open a text box in the row */
  kind: 'oauth' | 'token' | 'username';
  saveUrl?: string;
  label?: string;
  placeholder?: string;
  help?: string;
  /** imports listening history, so the row shows the import */
  history?: 'listenbrainz' | 'lastfm';
  connect?: (pid: number) => string;
}

const _MA_SERVICES: MaService[] = [
  {
    id: 'spotify',
    name: 'Spotify',
    logo: '/static/img/brands/spotify.png',
    blurb: 'Sync your Spotify playlists',
    kind: 'oauth',
    connect: (pid) => `/auth/spotify?profile_id=${pid}`,
  },
  {
    id: 'tidal',
    name: 'Tidal',
    logo: '/static/img/brands/tidal.png',
    blurb: 'Sync your Tidal playlists',
    kind: 'oauth',
    connect: (pid) => `/auth/tidal?profile_id=${pid}`,
  },
  {
    id: 'listenbrainz',
    name: 'ListenBrainz',
    logo: '/static/img/brands/listenbrainz.png',
    blurb: 'Your listening history and playlists',
    kind: 'token',
    saveUrl: '/api/profiles/me/listenbrainz',
    label: 'ListenBrainz token',
    placeholder: 'Paste your token',
    help: 'Find it at listenbrainz.org under your profile settings. Your stats will use your own listening.',
    history: 'listenbrainz',
  },
  {
    // just a username, scrobbles are public and the app's key reads them (#1293)
    id: 'lastfm',
    name: 'Last.fm',
    logo: '/static/img/brands/lastfm.png',
    blurb: 'Bring in your scrobbles',
    kind: 'username',
    saveUrl: '/api/profiles/me/lastfm',
    label: 'Last.fm username',
    placeholder: 'Your Last.fm username',
    help: 'Scrobbles are public, so your username is all SoulSync needs.',
    history: 'lastfm',
  },
];

interface MaConnections {
  connections?: Record<string, { connected?: boolean; account?: string | null }>;
  is_admin?: boolean;
  listening?: { scope?: string; sources?: string[] };
}

interface ImportStatus {
  own_account?: boolean;
  running?: boolean;
  status?: string;
  progress?: number | null;
  inserted?: number;
  imported?: number;
  total_scrobbles?: number | null;
  last_success_at?: string | null;
  error?: string | null;
}

interface PlexHomeUser {
  id: string;
  title: string;
  protected?: boolean;
}
interface NamedOption {
  id: string;
  name: string;
}

interface ServerModel {
  type: 'navidrome' | 'plex' | 'jellyfin' | 'none';
  navidromeUser?: string;
  plexLinkedUser?: string;
  plexHomeUserId?: string;
  plexHomeUsers?: PlexHomeUser[];
  plexLibraries?: string[];
  plexLibrary?: string;
  jellyUsers?: NamedOption[];
  jellyUser?: string;
  jellyLibraries?: NamedOption[];
  jellyLibrary?: string;
}

const SERVER_META: Record<ServerModel['type'], { name: string; logo: string }> = {
  navidrome: { name: 'Navidrome', logo: '/static/img/brands/navidrome.png' },
  plex: { name: 'Plex', logo: '/static/img/brands/plex.png' },
  jellyfin: { name: 'Jellyfin', logo: '/static/img/brands/jellyfin.png' },
  none: { name: 'Media server', logo: '' },
};

const _ma: {
  data: MaConnections | null;
  server: ServerModel | null;
  imports: Partial<Record<'listenbrainz' | 'lastfm', ImportStatus>>;
  open: string | null;
  poll: ReturnType<typeof setTimeout> | null;
} = { data: null, server: null, imports: {}, open: null, poll: null };

function _maProfile(): { id: number; name: string; color: string; avatar: string } {
  try {
    const ctx = window.getCurrentProfileContext?.();
    if (ctx) {
      return {
        id: ctx.profileId,
        name: ctx.name || '',
        color: ctx.avatarColor || '',
        avatar: ctx.avatarUrl || '',
      };
    }
  } catch {
    /* fall through */
  }
  return { id: 1, name: '', color: '', avatar: '' };
}

// ── open / close ────────────────────────────────────────────────────────────

export function openMyAccountsModal(): void {
  let overlay = document.getElementById('my-accounts-overlay');
  if (!overlay) {
    overlay = document.createElement('div');
    overlay.id = 'my-accounts-overlay';
    overlay.className = 'modal-overlay ma-overlay hidden';
    overlay.addEventListener('click', _maOnClick);
    overlay.addEventListener('change', _maOnChange);
    overlay.addEventListener('keydown', _maOnFieldKey);
    document.body.appendChild(overlay);
  }
  _ma.open = null;
  overlay.innerHTML = `
    <div class="ma-modal" role="dialog" aria-modal="true" aria-labelledby="ma-title" tabindex="-1">
      ${_maHeader()}
      <div class="ma-body" id="ma-body"><div class="ma-loading" aria-live="polite">Loading your account…</div></div>
    </div>`;
  overlay.classList.remove('hidden');
  const modal = overlay.querySelector<HTMLElement>('.ma-modal');
  if (modal) {
    modal.classList.remove('ma-in');
    void modal.offsetWidth;
    modal.classList.add('ma-in');
    modal.focus();
  }
  document.addEventListener('keydown', _maOnKeydown);
  void _maLoad();
}

export function closeMyAccountsModal(): void {
  const o = document.getElementById('my-accounts-overlay');
  if (o) o.classList.add('hidden');
  document.removeEventListener('keydown', _maOnKeydown);
  _maStopPoll();
}

/** the old My Settings entry point. everything it held lives here now. */
export function openPersonalSettings(): void {
  openMyAccountsModal();
}

function _maOnKeydown(e: KeyboardEvent): void {
  if (e.key === 'Escape') closeMyAccountsModal();
}

// ── loading ─────────────────────────────────────────────────────────────────

async function _maJson<T>(url: string): Promise<T | null> {
  try {
    const res = await fetch(url);
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

async function _maLoad(): Promise<void> {
  const [data, server] = await Promise.all([
    _maJson<MaConnections>('/api/profiles/me/connections'),
    _maLoadServer(),
  ]);
  _ma.data = data || { connections: {}, is_admin: false };
  _ma.server = server;
  _maRender();
  await _maLoadImports();
}

/** who you are on the media server. the ACTIVE server decides the card, a
 * jellyfin install with a leftover plex token must not get the plex card
 * (#1265). */
async function _maLoadServer(): Promise<ServerModel> {
  const [active, current] = await Promise.all([
    _maJson<{ server?: { active?: string } }>('/api/profiles/me/active-sources'),
    _maJson<Record<string, string | null>>('/api/profiles/me/server-library'),
  ]);
  const activeServer = active?.server?.active || '';
  const lib = current || {};

  if (activeServer === 'navidrome') {
    return { type: 'navidrome', navidromeUser: lib.navidrome_username || '' };
  }
  if (activeServer === 'plex' || activeServer === '') {
    const plex = await _maJson<{ libraries?: Array<string | { name?: string; title?: string }> }>(
      '/api/plex/music-libraries',
    );
    const libraries = (plex?.libraries || []).map((l) =>
      typeof l === 'string' ? l : l.name || l.title || '',
    );
    if (libraries.length) {
      const users = await _maJson<{ users?: PlexHomeUser[] }>('/api/profiles/me/plex-home-users');
      return {
        type: 'plex',
        plexLibraries: libraries.filter(Boolean),
        plexLibrary: lib.plex_library_id || '',
        plexLinkedUser: lib.plex_home_user_title || '',
        plexHomeUserId: lib.plex_home_user_id ? String(lib.plex_home_user_id) : '',
        plexHomeUsers: users?.users || [],
      };
    }
  }
  if (activeServer === 'jellyfin' || activeServer === 'emby' || activeServer === '') {
    const jelly = await _maJson<{
      libraries?: Array<{
        key?: string;
        id?: string;
        Id?: string;
        name?: string;
        Name?: string;
        title?: string;
      }>;
      users?: Array<{ id?: string; Id?: string; name?: string; Name?: string }>;
    }>('/api/jellyfin/music-libraries');
    if (jelly?.libraries?.length) {
      return {
        type: 'jellyfin',
        jellyLibraries: jelly.libraries.map((l) => ({
          id: String(l.key || l.id || l.Id || ''),
          name: String(l.name || l.Name || l.title || ''),
        })),
        jellyUsers: (jelly.users || []).map((u) => ({
          id: String(u.id || u.Id || ''),
          name: String(u.name || u.Name || ''),
        })),
        jellyUser: lib.jellyfin_user_id || '',
        jellyLibrary: lib.jellyfin_library_id || '',
      };
    }
  }
  return { type: 'none' };
}

/** the import behind each history service this profile connected. polls
 * while one is running so the row's progress moves. */
async function _maLoadImports(): Promise<void> {
  _maStopPoll();
  const conns = _ma.data?.connections || {};
  const wanted = (['listenbrainz', 'lastfm'] as const).filter((s) => conns[s]?.connected);
  const results = await Promise.all(
    wanted.map((s) => _maJson<ImportStatus>(`/api/${s}/listening-import/status`)),
  );
  wanted.forEach((s, i) => {
    const st = results[i];
    if (st && st.own_account) _ma.imports[s] = st;
    else delete _ma.imports[s];
    _maPaintImport(s);
  });
  const overlay = document.getElementById('my-accounts-overlay');
  const stillOpen = overlay && !overlay.classList.contains('hidden');
  if (stillOpen && wanted.some((s) => _ma.imports[s]?.running)) {
    _ma.poll = setTimeout(() => void _maLoadImports(), 2500);
  }
}

function _maStopPoll(): void {
  if (_ma.poll) clearTimeout(_ma.poll);
  _ma.poll = null;
}

// ── rendering ───────────────────────────────────────────────────────────────

const _CHEVRON = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg>`;
const _CLOSE = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>`;
const _PULSE = `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 12h3l3-8 4 16 3-8h5"/></svg>`;

function _maHeader(): string {
  const p = _maProfile();
  const initial = escapeHtml((p.name || '?').trim().charAt(0).toUpperCase() || '?');
  const avatar = p.avatar
    ? `<img class="ma-avatar-img" src="${escapeHtml(p.avatar)}" alt="">`
    : initial;
  const bg = p.color ? ` style="--ma-avatar:${escapeHtml(p.color)}"` : '';
  return `
    <div class="ma-head">
      <div class="ma-avatar"${bg}>${avatar}</div>
      <div class="ma-head-text">
        <h3 class="ma-title" id="ma-title">${escapeHtml(p.name || 'My Account')}</h3>
        <div class="ma-sub">Your account. Only you see this.</div>
      </div>
      <button type="button" class="ma-close" data-ma="close" aria-label="Close">${_CLOSE}</button>
    </div>`;
}

function _maRender(): void {
  const body = document.getElementById('ma-body');
  if (!body || !_ma.data) return;
  if (_ma.data.is_admin) {
    body.innerHTML = `<div class="ma-empty">Your accounts are the app's own. They're set up in Settings.</div>`;
    return;
  }
  body.innerHTML = `
    ${_maListeningCard()}
    <section class="ma-section" aria-labelledby="ma-server-h">
      <h4 class="ma-section-title" id="ma-server-h">Media server</h4>
      <div class="ma-group">${_maServerRow()}</div>
    </section>
    <section class="ma-section" aria-labelledby="ma-services-h">
      <h4 class="ma-section-title" id="ma-services-h">Music services</h4>
      <div class="ma-group">${_MA_SERVICES.map(_maServiceRow).join('')}</div>
    </section>
    <p class="ma-foot">The app's own accounts are set up by the admin in Settings. Anything you change here only affects you.</p>`;
  for (const s of ['listenbrainz', 'lastfm'] as const) _maPaintImport(s);
  const focus = body.querySelector<HTMLElement>('.ma-row.is-open input, .ma-row.is-open select');
  focus?.focus();
}

function _maListeningCard(): string {
  const listening = _ma.data?.listening || {};
  const names = (listening.sources || [])
    .map((s) => _MA_SERVICES.find((x) => x.id === s)?.name)
    .filter(Boolean) as string[];
  if (listening.scope === 'profile' && names.length) {
    return `
      <div class="ma-listening is-own">
        <div class="ma-listening-icon">${_PULSE}</div>
        <div>
          <div class="ma-listening-title">Your listening is your own</div>
          <div class="ma-listening-text">Stats, Recently Played and recommendations use your plays from ${escapeHtml(names.join(' and '))}.</div>
        </div>
      </div>`;
  }
  return `
    <div class="ma-listening">
      <div class="ma-listening-icon">${_PULSE}</div>
      <div>
        <div class="ma-listening-title">You're on the shared listening history</div>
        <div class="ma-listening-text">Connect ListenBrainz or Last.fm below and your stats and recommendations become yours.</div>
      </div>
    </div>`;
}

function _maLogo(src: string): string {
  return `<span class="ma-logo">${src ? `<img src="${src}" alt="" onerror="this.style.display='none'">` : ''}</span>`;
}

function _maServerRow(): string {
  const s = _ma.server || { type: 'none' as const };
  const meta = SERVER_META[s.type];
  if (s.type === 'none') {
    return `
      <div class="ma-row">
        <div class="ma-row-main">
          ${_maLogo('')}
          <div class="ma-row-text">
            <div class="ma-row-name">Media server</div>
            <div class="ma-row-sub">No media server is set up yet. Ask your admin.</div>
          </div>
        </div>
      </div>`;
  }
  const open = _ma.open === 'server';
  return `
    <div class="ma-row${open ? ' is-open' : ''}">
      <div class="ma-row-main">
        ${_maLogo(meta.logo)}
        <div class="ma-row-text">
          <div class="ma-row-name">${meta.name}</div>
          <div class="ma-row-sub">${_maServerLine(s)}</div>
        </div>
        <button type="button" class="ma-manage" data-ma="toggle" data-id="server" aria-expanded="${open}">
          ${open ? 'Done' : `Manage${_CHEVRON}`}
        </button>
      </div>
      ${open ? `<div class="ma-panel">${_maServerForm(s)}</div>` : ''}
    </div>`;
}

function _maServerLine(s: ServerModel): string {
  if (s.type === 'navidrome') {
    return s.navidromeUser
      ? `Signed in as ${escapeHtml(s.navidromeUser)}. Your playlists sync to your own account.`
      : "Using the app's account. Sign in so the playlists you sync are yours.";
  }
  if (s.type === 'plex') {
    const lib = s.plexLibrary ? `, syncing to ${escapeHtml(s.plexLibrary)}` : '';
    return s.plexLinkedUser
      ? `You're ${escapeHtml(s.plexLinkedUser)} on Plex${lib}.`
      : `Using the app's account${lib}. Pick who you are on Plex.`;
  }
  const user = s.jellyUsers?.find((u) => u.id === s.jellyUser)?.name;
  const lib = s.jellyLibraries?.find((l) => l.id === s.jellyLibrary)?.name;
  if (user || lib) {
    return `Syncing${user ? ` as ${escapeHtml(user)}` : ''}${lib ? ` to ${escapeHtml(lib)}` : ''}.`;
  }
  return "Using the admin's default user and library.";
}

function _maField(id: string, label: string, control: string, hint = ''): string {
  return `
    <div class="ma-field">
      <label for="${id}">${label}</label>
      ${control}
      ${hint ? `<div class="ma-hint">${hint}</div>` : ''}
    </div>`;
}

function _maOptions(
  opts: Array<{ value: string; label: string }>,
  selected: string,
  empty: string,
): string {
  return `<option value="">${escapeHtml(empty)}</option>${opts
    .map(
      (o) =>
        `<option value="${escapeHtml(o.value)}"${o.value === selected ? ' selected' : ''}>${escapeHtml(o.label)}</option>`,
    )
    .join('')}`;
}

function _maServerForm(s: ServerModel): string {
  if (s.type === 'navidrome') {
    return `
      <p class="ma-panel-text">Log in with your own Navidrome user and the playlists you sync will belong to you there.</p>
      ${_maField(
        'ma-nd-user',
        'Navidrome username',
        `<input class="ma-input" id="ma-nd-user" type="text" autocomplete="off" value="${escapeHtml(s.navidromeUser || '')}">`,
      )}
      ${_maField(
        'ma-nd-pass',
        'Navidrome password',
        `<input class="ma-input" id="ma-nd-pass" type="password" autocomplete="new-password" placeholder="${s.navidromeUser ? 'Saved' : ''}">`,
      )}
      <div class="ma-actions">
        ${s.navidromeUser ? '<button type="button" class="ma-btn ma-btn-quiet" data-ma="nd-clear">Use app account</button>' : ''}
        <button type="button" class="ma-btn ma-btn-primary" data-ma="nd-save">Save</button>
      </div>`;
  }
  if (s.type === 'plex') {
    const users = s.plexHomeUsers || [];
    const picked = users.find((u) => String(u.id) === s.plexHomeUserId);
    const who = users.length
      ? `
        ${_maField(
          'ma-plex-user',
          'Who you are on Plex',
          `<select class="ma-input" id="ma-plex-user">${_maOptions(
            users.map((u) => ({
              value: String(u.id),
              label: u.title + (u.protected ? ' (PIN)' : ''),
            })),
            s.plexHomeUserId || '',
            "Use the app's account",
          )}</select>`,
        )}
        <div class="ma-field" id="ma-plex-pin-field"${picked?.protected ? '' : ' hidden'}>
          <label for="ma-plex-pin">Plex profile PIN</label>
          <input class="ma-input" id="ma-plex-pin" type="password" inputmode="numeric" autocomplete="off" placeholder="Used once to link, never saved">
        </div>
        <div class="ma-actions">
          ${s.plexLinkedUser ? '<button type="button" class="ma-btn ma-btn-quiet" data-ma="plex-unlink">Use app account</button>' : ''}
          <button type="button" class="ma-btn ma-btn-primary" data-ma="plex-link">Link</button>
        </div>`
      : '<p class="ma-panel-text">No Plex Home users on this server, so playlists go to the app account.</p>';
    return `
      <p class="ma-panel-text">Pick who you are on Plex and the playlists you sync will belong to you there.</p>
      ${who}
      <div class="ma-divider"></div>
      ${_maField(
        'ma-plex-lib',
        'Music library',
        `<select class="ma-input" id="ma-plex-lib">${_maOptions(
          (s.plexLibraries || []).map((l) => ({ value: l, label: l })),
          s.plexLibrary || '',
          "Admin's default",
        )}</select>`,
      )}
      <div class="ma-actions"><button type="button" class="ma-btn ma-btn-primary" data-ma="plex-lib-save">Save library</button></div>`;
  }
  return `
    <p class="ma-panel-text">Choose which Jellyfin user and library your playlists sync to.</p>
    ${
      (s.jellyUsers || []).length
        ? _maField(
            'ma-jf-user',
            'User',
            `<select class="ma-input" id="ma-jf-user">${_maOptions(
              (s.jellyUsers || []).map((u) => ({ value: u.id, label: u.name })),
              s.jellyUser || '',
              "Admin's default",
            )}</select>`,
          )
        : ''
    }
    ${_maField(
      'ma-jf-lib',
      'Music library',
      `<select class="ma-input" id="ma-jf-lib">${_maOptions(
        (s.jellyLibraries || []).map((l) => ({ value: l.id, label: l.name })),
        s.jellyLibrary || '',
        "Admin's default",
      )}</select>`,
    )}
    <div class="ma-actions"><button type="button" class="ma-btn ma-btn-primary" data-ma="jf-save">Save</button></div>`;
}

function _maServiceRow(svc: MaService): string {
  const c = _ma.data?.connections?.[svc.id] || {};
  const connected = !!c.connected;
  const open = _ma.open === svc.id;
  const sub = connected
    ? `${escapeHtml(c.account || 'Connected')}${svc.history ? ' · listening history' : ' · your playlists'}`
    : escapeHtml(svc.blurb);

  let trailing: string;
  if (connected) {
    trailing = `
      <button type="button" class="ma-state" data-ma="toggle" data-id="${svc.id}" aria-expanded="${open}"
        aria-label="${svc.name} connected, show options">
        <span class="ma-dot"></span>Connected
      </button>`;
  } else if (open) {
    trailing = '';
  } else {
    trailing =
      svc.kind === 'oauth'
        ? `<button type="button" class="ma-btn ma-btn-outline" data-ma="oauth" data-id="${svc.id}">Connect</button>`
        : `<button type="button" class="ma-btn ma-btn-outline" data-ma="toggle" data-id="${svc.id}" aria-expanded="false">Connect</button>`;
  }

  let panel = '';
  if (open && connected) {
    panel = `
      <div class="ma-panel ma-panel-row">
        ${svc.history ? `<button type="button" class="ma-btn ma-btn-quiet" data-ma="sync" data-id="${svc.id}">Sync now</button>` : ''}
        <button type="button" class="ma-btn ma-btn-danger" data-ma="disconnect" data-id="${svc.id}">Disconnect</button>
      </div>`;
  } else if (open) {
    const type = svc.kind === 'username' ? 'text' : 'password';
    panel = `
      <div class="ma-panel">
        ${_maField(
          `ma-in-${svc.id}`,
          svc.label || svc.name,
          `<input class="ma-input" id="ma-in-${svc.id}" type="${type}" autocomplete="off" spellcheck="false"
             placeholder="${escapeHtml(svc.placeholder || '')}" data-submit="${svc.id}">`,
          escapeHtml(svc.help || ''),
        )}
        <div class="ma-actions">
          <button type="button" class="ma-btn ma-btn-quiet" data-ma="toggle" data-id="${svc.id}">Cancel</button>
          <button type="button" class="ma-btn ma-btn-primary" data-ma="save" data-id="${svc.id}">Connect</button>
        </div>
      </div>`;
  }

  return `
    <div class="ma-row${open ? ' is-open' : ''}${!connected && open ? ' is-connecting' : ''}" style="--ma-brand-soft:${_maBrandSoft(svc.id)}">
      <div class="ma-row-main">
        ${_maLogo(svc.logo)}
        <div class="ma-row-text">
          <div class="ma-row-name">${svc.name}</div>
          <div class="ma-row-sub" id="ma-sub-${svc.id}">${sub}</div>
        </div>
        ${trailing}
      </div>
      ${svc.history ? `<div class="ma-import" id="ma-import-${svc.id}" hidden></div>` : ''}
      ${panel}
    </div>`;
}

function _maBrandSoft(id: ServiceId): string {
  return {
    spotify: 'rgba(29,185,84,0.06)',
    tidal: 'rgba(0,207,232,0.06)',
    listenbrainz: 'rgba(235,116,59,0.06)',
    lastfm: 'rgba(213,16,7,0.06)',
  }[id];
}

/** the import line and bar, patched in place so a poll never wipes what
 * someone is typing in another row */
function _maPaintImport(s: 'listenbrainz' | 'lastfm'): void {
  const box = document.getElementById(`ma-import-${s}`);
  const subEl = document.getElementById(`ma-sub-${s}`);
  const st = _ma.imports[s];
  const account = _ma.data?.connections?.[s]?.account || '';
  if (!box || !subEl) return;
  if (!st) {
    box.hidden = true;
    return;
  }
  if (st.running) {
    const pct = Math.max(0, Math.min(100, Math.round(Number(st.progress) || 0)));
    const done = st.imported || 0;
    const total = st.total_scrobbles || 0;
    subEl.textContent = `${account} · importing your history`;
    box.hidden = false;
    box.innerHTML = `
      <div class="ma-bar" role="progressbar" aria-label="Import progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}">
        <span style="width:${pct}%"></span>
      </div>
      <div class="ma-import-text"><span>${total ? `${done.toLocaleString()} of ${total.toLocaleString()} plays` : 'Getting started'}. Your stats fill in as it goes.</span><span class="ma-pct">${pct}%</span></div>`;
    return;
  }
  box.hidden = true;
  if (st.status === 'error') {
    subEl.innerHTML = `${escapeHtml(account)} · <span class="ma-warn">last import hit a problem, it tries again hourly</span>`;
  } else {
    const when = _maAgo(st.last_success_at);
    subEl.textContent = `${account} · listening history${when ? `, synced ${when}` : ''}`;
  }
}

function _maAgo(stamp: string | null | undefined): string {
  if (!stamp) return '';
  // importer stamps are utc without a zone
  const t = Date.parse(`${stamp.replace(' ', 'T')}Z`);
  if (Number.isNaN(t)) return '';
  const mins = Math.max(0, Math.round((Date.now() - t) / 60000));
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours} hr ago`;
  const days = Math.round(hours / 24);
  return `${days} day${days === 1 ? '' : 's'} ago`;
}

// ── events ──────────────────────────────────────────────────────────────────

function _maOnClick(e: Event): void {
  const overlay = e.currentTarget as HTMLElement;
  if (e.target === overlay) {
    closeMyAccountsModal();
    return;
  }
  const btn = (e.target as HTMLElement).closest<HTMLElement>('[data-ma]');
  if (!btn) return;
  const id = btn.dataset.id || '';
  switch (btn.dataset.ma) {
    case 'close':
      closeMyAccountsModal();
      break;
    case 'toggle':
      _ma.open = _ma.open === id ? null : id;
      _maRender();
      break;
    case 'oauth':
      connectMyAccount(id);
      break;
    case 'save':
      void saveMyAccountToken(id);
      break;
    case 'disconnect':
      void disconnectMyAccount(id);
      break;
    case 'sync':
      void _maSyncNow(id as 'listenbrainz' | 'lastfm');
      break;
    case 'nd-save':
      void _maNavidromeSave();
      break;
    case 'nd-clear':
      void _maServerCall(
        '/api/profiles/me/navidrome-login',
        'DELETE',
        null,
        "Navidrome login removed, using the app's account",
      );
      break;
    case 'plex-link':
      void _maPlexLink();
      break;
    case 'plex-unlink':
      void _maServerCall(
        '/api/profiles/me/plex-home-user',
        'DELETE',
        null,
        "Plex user unlinked, using the app's account",
      );
      break;
    case 'plex-lib-save':
      void _maServerCall(
        '/api/profiles/me/server-library',
        'POST',
        {
          server_type: 'plex',
          library_id: _maValue('ma-plex-lib') || null,
        },
        'Plex library saved',
      );
      break;
    case 'jf-save':
      void _maServerCall(
        '/api/profiles/me/server-library',
        'POST',
        {
          server_type: 'jellyfin',
          user_id: _maValue('ma-jf-user') || null,
          library_id: _maValue('ma-jf-lib') || null,
        },
        'Jellyfin settings saved',
      );
      break;
  }
}

function _maOnChange(e: Event): void {
  const el = e.target as HTMLElement;
  if (el.id !== 'ma-plex-user') return;
  const users = _ma.server?.plexHomeUsers || [];
  const picked = users.find((u) => String(u.id) === (el as HTMLSelectElement).value);
  const pin = document.getElementById('ma-plex-pin-field');
  if (pin) pin.hidden = !picked?.protected;
}

/** enter in a connect box connects */
function _maOnFieldKey(e: KeyboardEvent): void {
  const el = e.target as HTMLElement;
  if (e.key !== 'Enter' || !el.dataset.submit) return;
  e.preventDefault();
  void saveMyAccountToken(el.dataset.submit);
}

function _maValue(id: string): string {
  const el = document.getElementById(id) as HTMLInputElement | HTMLSelectElement | null;
  return (el?.value || '').trim();
}

// ── actions ─────────────────────────────────────────────────────────────────

export function connectMyAccount(serviceId: string): void {
  const svc = _MA_SERVICES.find((s) => s.id === serviceId);
  if (!svc || !svc.connect) return;
  const popup = window.open(
    svc.connect(_maProfile().id),
    'soulsync-connect-' + serviceId,
    'width=560,height=720,menubar=no,toolbar=no',
  );
  // reload once the popup closes, give the callback a moment to persist
  const timer = setInterval(() => {
    if (!popup || popup.closed) {
      clearInterval(timer);
      setTimeout(() => void _maLoad(), 600);
    }
  }, 800);
}

export async function saveMyAccountToken(serviceId: string): Promise<void> {
  const svc = _MA_SERVICES.find((s) => s.id === serviceId);
  if (!svc || !svc.saveUrl) return;
  const value = _maValue(`ma-in-${serviceId}`);
  if (!value) {
    toast(svc.kind === 'username' ? 'Type your username first' : 'Paste your token first', 'info');
    return;
  }
  const btn = document.querySelector<HTMLButtonElement>(`[data-ma="save"][data-id="${serviceId}"]`);
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Connecting…';
  }
  try {
    const res = await fetch(svc.saveUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(svc.kind === 'username' ? { username: value } : { token: value }),
    });
    const data = (await res.json()) as { success?: boolean; error?: string };
    if (data.success) {
      toast(
        svc.history
          ? `${svc.name} connected, importing your listening history`
          : `${svc.name} connected`,
        'success',
      );
      if (serviceId === 'listenbrainz') window._invalidateListenBrainzCache?.();
      _ma.open = null;
      await _maLoad();
      return;
    }
    toast(data.error || 'Could not connect', 'error');
  } catch {
    toast('Could not connect', 'error');
  }
  if (btn) {
    btn.disabled = false;
    btn.textContent = 'Connect';
  }
}

export async function disconnectMyAccount(serviceId: string): Promise<void> {
  const svc = _MA_SERVICES.find((s) => s.id === serviceId);
  const name = svc?.name || serviceId;
  const ok = window.showConfirmDialog
    ? await window.showConfirmDialog({
        title: `Disconnect ${name}?`,
        message: svc?.history
          ? 'Your stats go back to the shared history unless your other service is still connected. Plays already imported stay put if you reconnect.'
          : 'Playlists go back to the app account.',
        confirmText: 'Disconnect',
        destructive: true,
      })
    : true;
  if (!ok) return;
  try {
    const res = await fetch(`/api/profiles/me/connections/${serviceId}/disconnect`, {
      method: 'POST',
    });
    const data = (await res.json()) as { success?: boolean; error?: string };
    if (data.success) {
      toast(`${name} disconnected`, 'success');
      if (serviceId === 'listenbrainz') window._invalidateListenBrainzCache?.();
      _ma.open = null;
      delete _ma.imports[serviceId as 'listenbrainz' | 'lastfm'];
      await _maLoad();
    } else {
      toast(data.error || 'Disconnect failed', 'error');
    }
  } catch {
    toast('Disconnect failed', 'error');
  }
}

async function _maSyncNow(service: 'listenbrainz' | 'lastfm'): Promise<void> {
  try {
    const res = await fetch(`/api/${service}/listening-import/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    });
    const data = (await res.json()) as { success?: boolean; error?: string; status?: string };
    if (!data.success) {
      toast(data.error || 'Could not start the sync', 'error');
      return;
    }
    toast(data.status === 'skipped' ? 'Already syncing' : 'Syncing your listening history', 'info');
    _ma.open = null;
    _maRender();
    await _maLoadImports();
  } catch {
    toast('Could not start the sync', 'error');
  }
}

async function _maServerCall(
  url: string,
  method: string,
  body: unknown,
  done: string,
): Promise<boolean> {
  try {
    const res = await fetch(url, {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = (await res.json()) as { success?: boolean; error?: string };
    if (data.success === false) {
      toast(data.error || 'Could not save that', 'error');
      return false;
    }
    toast(done, 'success');
    _ma.open = null;
    await _maLoad();
    return true;
  } catch {
    toast('Could not save that', 'error');
    return false;
  }
}

async function _maNavidromeSave(): Promise<void> {
  const username = _maValue('ma-nd-user');
  const password = (document.getElementById('ma-nd-pass') as HTMLInputElement | null)?.value || '';
  if (!username || !password) {
    toast('Enter your Navidrome username and password', 'error');
    return;
  }
  await _maServerCall(
    '/api/profiles/me/navidrome-login',
    'POST',
    { username, password },
    `Playlists you sync will belong to ${username} in Navidrome`,
  );
}

async function _maPlexLink(): Promise<void> {
  const userId = _maValue('ma-plex-user');
  if (!userId) {
    toast('Pick your Plex user first', 'error');
    return;
  }
  const pin = (document.getElementById('ma-plex-pin') as HTMLInputElement | null)?.value || '';
  await _maServerCall(
    '/api/profiles/me/plex-home-user',
    'POST',
    { user_id: userId, pin },
    'Plex user linked, the playlists you sync belong to you there',
  );
}
