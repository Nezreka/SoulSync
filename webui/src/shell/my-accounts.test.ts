/**
 * My Account: one modal for your media server login and your own music
 * services (it replaced My Accounts + the vanilla My Settings). these pin what
 * a non-admin actually sees and what each action sends.
 */

import { HttpResponse, http } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { server } from '@/test/msw';

import {
  _maLastfmScrobbleSignIn,
  _maPrivateUntil,
  _maPlexConnect,
  closeMyAccountsModal,
  openMyAccountsModal,
  openPersonalSettings,
} from './my-accounts';

type Conns = Record<string, { connected: boolean; account: string | null }>;

let toasts: string[] = [];
let posted: Array<{ url: string; body: unknown }> = [];

function conns(over: Partial<Conns> = {}): Conns {
  return {
    spotify: { connected: false, account: null },
    tidal: { connected: false, account: null },
    listenbrainz: { connected: false, account: null },
    lastfm: { connected: false, account: null },
    ...over,
  };
}

function mockAccount({
  connections = conns(),
  listening = { scope: 'shared', sources: [] as string[] },
  isAdmin = false,
  server: activeServer = 'navidrome',
  library = {} as Record<string, string>,
  imports = {} as Record<string, unknown>,
  scrobbling = {} as Record<string, unknown>,
  navidromeLibraries = { own_library: false, supported: true, folders: [] } as Record<
    string,
    unknown
  >,
  weather = WEATHER_OFF as Record<string, unknown>,
  perms = MEMBER_PERMS as Record<string, unknown>,
  asks = {} as Record<string, unknown>,
  privacy = { share_listening: false, asked: false, private_session: false } as Record<
    string,
    unknown
  >,
} = {}) {
  server.use(
    http.get('/api/profiles/me/permissions', () =>
      HttpResponse.json({
        success: true,
        profile_id: 7,
        is_admin: isAdmin,
        source: 'template',
        effective: perms,
        summary: { sentence: 'You use music.', lines: [], never: [] },
      }),
    ),
    http.get('/api/profiles', () =>
      HttpResponse.json({ success: true, profiles: [{ id: 1, name: 'Boulder', is_admin: true }] }),
    ),
    http.get('/api/profiles/me/asks', () => HttpResponse.json({ success: true, asks })),
    http.get('/api/weather', () => HttpResponse.json(weather)),
    http.get('/api/profiles/me/listening-privacy', () =>
      HttpResponse.json({ success: true, ...privacy }),
    ),
    http.get('/api/profiles/me/navidrome-libraries', () =>
      HttpResponse.json({ success: true, ...navidromeLibraries }),
    ),
    http.get('/api/profiles/me/scrobbling', () =>
      HttpResponse.json({ success: true, ...scrobbling }),
    ),
    http.get('/api/profiles/me/connections', () =>
      HttpResponse.json({ success: true, is_admin: isAdmin, connections, listening }),
    ),
    http.get('/api/profiles/me/active-sources', () =>
      HttpResponse.json({ server: { active: activeServer } }),
    ),
    http.get('/api/profiles/me/server-library', () => HttpResponse.json(library)),
    http.get('/api/listenbrainz/listening-import/status', () =>
      HttpResponse.json(imports.listenbrainz ?? { own_account: false }),
    ),
    http.get('/api/lastfm/listening-import/status', () =>
      HttpResponse.json(imports.lastfm ?? { own_account: false }),
    ),
  );
}

// a member on Adult: everything on, downloads right away, no limit
const MEMBER_PERMS: Record<string, unknown> = {
  'music.access': true,
  'music.get': 'download',
  'music.hide_explicit': false,
  'movies.access': true,
  'movies.get': 'download',
  'movies.max_rating': 'any',
  'tv.access': true,
  'tv.get': 'download',
  'tv.max_rating': 'follow',
  'books.access': true,
  'books.get': 'download',
  'podcasts.access': true,
  'podcasts.get': 'download',
  'requests.limit': { n: 0, days: 7 },
  'requests.see_all': false,
  'requests.moderate': false,
  'account.connect': true,
  'chat.use': true,
  'automations.personal': true,
};

// what /api/weather answers a member with no place of their own (#2163)
const WEATHER_OFF = {
  success: true,
  enabled: true,
  location: null,
  units: 'fahrenheit',
  snapshot: null,
  scene: null,
  holidays: true,
  own: true,
};

const WEATHER_BERLIN = {
  ...WEATHER_OFF,
  units: 'celsius',
  location: { query: 'Berlin', name: 'Berlin', latitude: 52.52, longitude: 13.41 },
  snapshot: { current: { temp: 11.4, condition: 'Light drizzle' } },
};

function capturePosts(url: string, reply: Record<string, unknown> = { success: true }) {
  server.use(
    http.post(url, async ({ request }) => {
      const text = await request.text();
      posted.push({ url, body: text ? JSON.parse(text) : null });
      return HttpResponse.json(reply);
    }),
  );
}

const modal = () => document.querySelector('#my-accounts-overlay .ma-modal') as HTMLElement;
const body = () => document.getElementById('ma-body') as HTMLElement;
const click = (sel: string) => (document.querySelector(sel) as HTMLElement).click();

async function opened() {
  openMyAccountsModal();
  await vi.waitFor(() => expect(body().querySelector('.ma-section')).not.toBeNull());
}

beforeEach(() => {
  document.body.innerHTML = '';
  toasts = [];
  posted = [];
  window.showToast = (m: string) => {
    toasts.push(m);
  };
  window.getCurrentProfileContext = () => ({
    profileId: 7,
    isAdmin: false,
    name: 'Kim',
    avatarColor: '#6d5ce8',
    avatarUrl: '',
  });
});

afterEach(() => {
  closeMyAccountsModal();
  Reflect.deleteProperty(window, 'showConfirmDialog');
});

describe('My Account', () => {
  it('closes the mobile nav drawer it was opened from (Oct 13 fold, M-04)', async () => {
    // The My Account button lives inside the sidebar drawer; drawer-first
    // is the natural phone flow, and the drawer (z-10001) would otherwise
    // stay open over the modal (z-10000) with its close button hidden.
    document.body.innerHTML = `
      <button id="hamburger-btn" class="active" aria-expanded="true"></button>
      <div id="mobile-overlay" class="active"></div>
      <aside class="sidebar mobile-open"></aside>`;
    document.body.classList.add('mobile-nav-open');
    mockAccount({});
    await opened();
    expect(document.querySelector('.sidebar')?.classList.contains('mobile-open')).toBe(false);
    expect(document.body.classList.contains('mobile-nav-open')).toBe(false);
    expect(document.getElementById('mobile-overlay')?.classList.contains('active')).toBe(false);
    const burger = document.getElementById('hamburger-btn');
    expect(burger?.classList.contains('active')).toBe(false);
    expect(burger?.getAttribute('aria-expanded')).toBe('false');
    // …and the modal itself is open.
    expect(document.getElementById('my-accounts-overlay')?.classList.contains('hidden')).toBe(
      false,
    );
  });

  it('is one modal with your media server and your music services', async () => {
    mockAccount({ library: { navidrome_username: 'kim' } });
    await opened();

    expect(modal().getAttribute('role')).toBe('dialog');
    expect(modal().textContent).toContain('Kim');
    const titles = [...body().querySelectorAll('.ma-section-title')].map((h) => h.textContent);
    expect(titles).toEqual([
      'What you can do',
      'Media server',
      'Music services',
      'Your listening',
      'Sidebar weather',
    ]);
    expect(body().textContent).toContain(
      'Signed in as kim. Your playlists sync to your own account.',
    );
    const names = [...body().querySelectorAll('.ma-row-name')].map((n) => n.textContent);
    expect(names).toEqual([
      'Navidrome',
      'Spotify',
      'Tidal',
      'ListenBrainz',
      'Last.fm',
      'Share my listening',
      'Private session',
      'Weather',
    ]);
  });

  it('the old My Settings entry point opens the same modal', async () => {
    mockAccount();
    openPersonalSettings();
    await vi.waitFor(() => expect(body().querySelector('.ma-section')).not.toBeNull());
    expect(document.querySelectorAll('#my-accounts-overlay')).toHaveLength(1);
  });

  it('says whose listening your stats read', async () => {
    mockAccount();
    await opened();
    expect(body().querySelector('.ma-listening')?.textContent).toContain(
      "You're on the shared listening history",
    );

    closeMyAccountsModal();
    mockAccount({
      connections: conns({ lastfm: { connected: true, account: 'kim_fm' } }),
      listening: { scope: 'profile', sources: ['lastfm'] },
    });
    await opened();
    const card = body().querySelector('.ma-listening.is-own');
    expect(card?.textContent).toContain('Your listening is your own');
    expect(card?.textContent).toContain('your plays from Last.fm');
  });

  it('connects last.fm with just a username, opened in its own row', async () => {
    mockAccount();
    capturePosts('/api/profiles/me/lastfm', { success: true, username: 'Kim_Fm' });
    await opened();

    click('[data-ma="toggle"][data-id="lastfm"]');
    const input = document.getElementById('ma-in-lastfm') as HTMLInputElement;
    expect(input.type).toBe('text');
    expect(document.querySelector('label[for="ma-in-lastfm"]')?.textContent).toBe(
      'Last.fm username',
    );
    // only this row opened
    expect(body().querySelectorAll('.ma-row.is-open')).toHaveLength(1);

    input.value = 'kim_fm';
    click('[data-ma="save"][data-id="lastfm"]');
    await vi.waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0].body).toEqual({ username: 'kim_fm' });
    await vi.waitFor(() =>
      expect(toasts).toContain('Last.fm connected, importing your listening history'),
    );
  });

  it('connects listenbrainz with a hidden token, and enter submits', async () => {
    mockAccount();
    capturePosts('/api/profiles/me/listenbrainz');
    await opened();

    click('[data-ma="toggle"][data-id="listenbrainz"]');
    const input = document.getElementById('ma-in-listenbrainz') as HTMLInputElement;
    expect(input.type).toBe('password');
    input.value = 'secret-token';
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    await vi.waitFor(() =>
      expect(posted).toEqual([
        { url: '/api/profiles/me/listenbrainz', body: { token: 'secret-token' } },
      ]),
    );
  });

  it('shows a running import as a moving bar in its row', async () => {
    mockAccount({
      connections: conns({ lastfm: { connected: true, account: 'kim_fm' } }),
      listening: { scope: 'profile', sources: ['lastfm'] },
      imports: {
        lastfm: {
          own_account: true,
          running: true,
          progress: 42,
          imported: 18402,
          total_scrobbles: 43810,
        },
      },
    });
    await opened();
    await vi.waitFor(() => expect(document.getElementById('ma-import-lastfm')?.hidden).toBe(false));

    const bar = document.querySelector('#ma-import-lastfm [role="progressbar"]');
    expect(bar?.getAttribute('aria-valuenow')).toBe('42');
    expect(document.getElementById('ma-import-lastfm')?.textContent).toContain(
      '18,402 of 43,810 plays',
    );
    expect(document.getElementById('ma-sub-lastfm')?.textContent).toBe(
      'kim_fm · importing your history',
    );
  });

  it('disconnect asks first with the soulsync dialog, never the browser one', async () => {
    mockAccount({ connections: conns({ spotify: { connected: true, account: 'kimmy' } }) });
    capturePosts('/api/profiles/me/connections/spotify/disconnect');
    const ask = vi.fn(async () => false);
    window.showConfirmDialog = ask;
    const native = vi.spyOn(window, 'confirm');
    await opened();

    click('[data-ma="toggle"][data-id="spotify"]');
    click('[data-ma="disconnect"][data-id="spotify"]');
    await vi.waitFor(() => expect(ask).toHaveBeenCalledTimes(1));
    expect(native).not.toHaveBeenCalled();
    expect(posted).toEqual([]);

    ask.mockResolvedValueOnce(true);
    click('[data-ma="disconnect"][data-id="spotify"]');
    await vi.waitFor(() =>
      expect(posted.map((p) => p.url)).toEqual(['/api/profiles/me/connections/spotify/disconnect']),
    );
  });

  it('manages the navidrome login inside the media server row', async () => {
    mockAccount();
    capturePosts('/api/profiles/me/navidrome-login');
    await opened();
    expect(body().textContent).toContain(
      "Using the app's account. Sign in so the playlists you sync are yours.",
    );

    click('[data-ma="toggle"][data-id="server"]');
    (document.getElementById('ma-nd-user') as HTMLInputElement).value = 'kim';
    (document.getElementById('ma-nd-pass') as HTMLInputElement).value = 'pw';
    click('[data-ma="nd-save"]');
    await vi.waitFor(() =>
      expect(posted).toEqual([
        { url: '/api/profiles/me/navidrome-login', body: { username: 'kim', password: 'pw' } },
      ]),
    );
  });

  it('an own library on navidrome picks its library from what the login sees (#2031)', async () => {
    mockAccount({
      library: { navidrome_username: 'kim' },
      navidromeLibraries: {
        own_library: true,
        supported: true,
        selected: '',
        folders: [
          { id: '1', name: 'Music', shared: true },
          { id: '2', name: 'Kim', shared: false },
        ],
      },
    });
    capturePosts('/api/profiles/me/server-library');
    await opened();
    click('[data-ma="toggle"][data-id="server"]');
    const pick = document.getElementById('ma-nd-lib') as HTMLSelectElement;
    expect([...pick.options].map((o) => o.textContent)).toEqual([
      'Pick your library',
      'Music (shared)',
      'Kim',
    ]);
    // nothing picked: nothing sent
    click('[data-ma="nd-lib-save"]');
    expect(toasts).toContain('Pick your library first');
    expect(posted).toEqual([]);
    pick.value = '2';
    click('[data-ma="nd-lib-save"]');
    await vi.waitFor(() =>
      expect(posted).toEqual([
        {
          url: '/api/profiles/me/server-library',
          body: { server_type: 'navidrome', library_id: '2' },
        },
      ]),
    );
  });

  it('an own library on an old navidrome says why instead of a picker (#2031)', async () => {
    mockAccount({
      library: { navidrome_username: 'kim' },
      navidromeLibraries: {
        own_library: true,
        supported: false,
        message:
          'Own libraries need Navidrome 0.58 or newer (multiple libraries). This server runs 0.57.0.',
        folders: [],
      },
    });
    await opened();
    click('[data-ma="toggle"][data-id="server"]');
    expect(document.getElementById('ma-nd-lib')).toBeNull();
    expect(document.getElementById('ma-nd-lib-note')?.textContent).toContain(
      'Navidrome 0.58 or newer',
    );
  });

  it('a shared-library profile on navidrome gets no library picker', async () => {
    mockAccount({ library: { navidrome_username: 'kim' } });
    await opened();
    click('[data-ma="toggle"][data-id="server"]');
    expect(document.getElementById('ma-nd-lib')).toBeNull();
    expect(document.getElementById('ma-nd-lib-note')).toBeNull();
  });

  it('plex asks for the pin only for a protected home user', async () => {
    mockAccount({ server: 'plex' });
    server.use(
      http.get('/api/plex/music-libraries', () => HttpResponse.json({ libraries: ['Music'] })),
      http.get('/api/profiles/me/plex-home-users', () =>
        HttpResponse.json({
          users: [
            { id: '1', title: 'Kids', protected: false },
            { id: '2', title: 'Kim', protected: true },
          ],
        }),
      ),
    );
    await opened();
    click('[data-ma="toggle"][data-id="server"]');
    const pick = document.getElementById('ma-plex-user') as HTMLSelectElement;
    const pin = document.getElementById('ma-plex-pin-field') as HTMLElement;
    expect(pin.hidden).toBe(true);
    pick.value = '2';
    pick.dispatchEvent(new Event('change', { bubbles: true }));
    expect(pin.hidden).toBe(false);
  });

  it('plex offers connect with plex to everyone, home users or not', async () => {
    // a shared friend: no plex home users on the server, still gets a way in
    mockAccount({ server: 'plex' });
    server.use(
      http.get('/api/plex/music-libraries', () => HttpResponse.json({ libraries: ['Music'] })),
      http.get('/api/profiles/me/plex-home-users', () => HttpResponse.json({ users: [] })),
    );
    await opened();
    click('[data-ma="toggle"][data-id="server"]');
    const primary = body().querySelectorAll('.ma-btn-primary[data-ma^="plex"]');
    expect([...primary].map((b) => (b as HTMLElement).dataset.ma)).toEqual(['plex-connect']);
    expect(document.getElementById('ma-plex-user')).toBeNull();
  });

  it('connect with plex waits for plex, then links and says who', async () => {
    mockAccount({ server: 'plex' });
    let checks = 0;
    server.use(
      http.get('/api/plex/music-libraries', () => HttpResponse.json({ libraries: [] })),
      http.get('/api/profiles/me/plex-home-users', () => HttpResponse.json({ users: [] })),
      http.post('/api/profiles/me/plex-connect/start', () =>
        HttpResponse.json({ success: true, url: 'https://app.plex.tv/auth#?x' }),
      ),
      http.post('/api/profiles/me/plex-connect/check', () => {
        checks += 1;
        return HttpResponse.json(
          checks < 2
            ? { success: true, pending: true }
            : { success: true, pending: false, title: 'maxnrose' },
        );
      }),
    );
    const popup = { location: { href: '' }, closed: false, close: vi.fn() };
    const ok = await _maPlexConnect(
      null,
      () => popup as unknown as Window,
      async () => {},
    );
    expect(ok).toBe(true);
    expect(popup.location.href).toBe('https://app.plex.tv/auth#?x');
    expect(popup.close).toHaveBeenCalled();
    expect(toasts).toContain('Connected to Plex as maxnrose');
  });

  it('connect with plex shows the server refusal', async () => {
    server.use(
      http.post('/api/profiles/me/plex-connect/start', () =>
        HttpResponse.json({ success: true, url: 'https://app.plex.tv/auth#?x' }),
      ),
      http.post('/api/profiles/me/plex-connect/check', () =>
        HttpResponse.json(
          { success: false, error: 'That Plex account is connected to another SoulSync profile' },
          { status: 403 },
        ),
      ),
    );
    const popup = { location: { href: '' }, closed: false, close: vi.fn() };
    expect(
      await _maPlexConnect(
        null,
        () => popup as unknown as Window,
        async () => {},
      ),
    ).toBe(false);
    expect(toasts).toContain('That Plex account is connected to another SoulSync profile');
  });

  it('connect with plex stops when plex is closed without approving', async () => {
    server.use(
      http.post('/api/profiles/me/plex-connect/start', () =>
        HttpResponse.json({ success: true, url: 'https://app.plex.tv/auth#?x' }),
      ),
      http.post('/api/profiles/me/plex-connect/check', () =>
        HttpResponse.json({ success: true, pending: true }),
      ),
    );
    const popup = { location: { href: '' }, closed: true, close: vi.fn() };
    expect(
      await _maPlexConnect(
        null,
        () => popup as unknown as Window,
        async () => {},
      ),
    ).toBe(false);
    expect(toasts).toContain('Connecting with Plex was cancelled');
  });

  it('the admin is pointed at Settings', async () => {
    mockAccount({ isAdmin: true });
    openMyAccountsModal();
    await vi.waitFor(() => expect(body().textContent).toContain("They're set up in Settings"));
    // their listening switches are their own, so they stay; nothing else does
    const titles = [...body().querySelectorAll('.ma-section-title')].map((h) => h.textContent);
    expect(titles).toEqual(['Your listening']);
  });
});

describe('My Account: sign in with oidc', () => {
  function mockOidc(status: Record<string, unknown>) {
    server.use(
      http.get('/api/profiles/me/oidc', () => HttpResponse.json({ success: true, ...status })),
    );
  }
  const signIn = () =>
    (body().querySelector('#ma-signin-h')?.closest('.ma-section') ?? null) as HTMLElement | null;

  it('has no sign-in card when the admin has not turned it on', async () => {
    mockAccount();
    mockOidc({ enabled: false, label: 'Sign in with OIDC', connected: false, last: null });
    await opened();
    expect(signIn()).toBeNull();
  });

  it("offers connect, named with the admin's label", async () => {
    mockAccount();
    mockOidc({ enabled: true, label: 'Sign in with Pocket ID', connected: false, last: null });
    await opened();
    await vi.waitFor(() => expect(signIn()).not.toBeNull());
    expect(signIn()!.textContent).toContain('Sign in with Pocket ID');
    expect(signIn()!.querySelector('[data-ma="oidc-connect"]')).not.toBeNull();
    expect(signIn()!.querySelector('[data-ma="oidc-disconnect"]')).toBeNull();
  });

  it('disconnect when connected, and it says so', async () => {
    mockAccount();
    mockOidc({ enabled: true, label: 'Sign in with Pocket ID', connected: true, last: null });
    let deleted = 0;
    server.use(
      http.delete('/api/profiles/me/oidc', () => {
        deleted++;
        return HttpResponse.json({ success: true });
      }),
    );
    await opened();
    await vi.waitFor(() =>
      expect(signIn()?.querySelector('[data-ma="oidc-disconnect"]')).not.toBeNull(),
    );
    click('[data-ma="oidc-disconnect"]');
    await vi.waitFor(() => expect(deleted).toBe(1));
    await vi.waitFor(() => expect(toasts).toContain('Disconnected'));
  });

  it('a refused disconnect shows the server reason', async () => {
    mockAccount();
    mockOidc({ enabled: true, label: 'Sign in with Pocket ID', connected: true, last: null });
    server.use(
      http.delete('/api/profiles/me/oidc', () =>
        HttpResponse.json(
          { success: false, error: 'This is the only way this profile can sign in.' },
          { status: 400 },
        ),
      ),
    );
    await opened();
    await vi.waitFor(() =>
      expect(signIn()?.querySelector('[data-ma="oidc-disconnect"]')).not.toBeNull(),
    );
    click('[data-ma="oidc-disconnect"]');
    await vi.waitFor(() => expect(toasts.join(' ')).toContain('only way'));
  });

  it('says how a connect that just came back went', async () => {
    mockAccount();
    mockOidc({
      enabled: true,
      label: 'Sign in with Pocket ID',
      connected: false,
      last: { connected: false, error: 'That account is connected to another SoulSync profile' },
    });
    await opened();
    await vi.waitFor(() => expect(toasts.join(' ')).toContain('another SoulSync profile'));
  });

  it('the admin, pointed at Settings, still gets the sign-in card', async () => {
    mockAccount({ isAdmin: true });
    mockOidc({
      enabled: true,
      label: 'Sign in with Pocket ID',
      connected: true,
      last: { connected: true },
    });
    openMyAccountsModal();
    await vi.waitFor(() => expect(body().textContent).toContain("They're set up in Settings"));
    await vi.waitFor(() => expect(signIn()).not.toBeNull());
    expect(toasts.join(' ')).toContain('Connected');
  });

  it("shows the admin's logo, or a plain key without one", async () => {
    mockAccount();
    mockOidc({
      enabled: true,
      label: 'Sign In With Pocket ID',
      icon: 'https://cdn.example.test/pocket-id.png',
      connected: true,
      last: null,
    });
    await opened();
    await vi.waitFor(() => expect(signIn()).not.toBeNull());
    expect(signIn()!.querySelector('.ma-logo img')!.getAttribute('src')).toBe(
      'https://cdn.example.test/pocket-id.png',
    );

    closeMyAccountsModal();
    mockOidc({
      enabled: true,
      label: 'Sign In With Pocket ID',
      icon: '',
      connected: true,
      last: null,
    });
    await opened();
    await vi.waitFor(() => expect(signIn()).not.toBeNull());
    expect(signIn()!.querySelector('.ma-logo img')).toBeNull();
    expect(signIn()!.querySelector('.ma-logo-key svg')).not.toBeNull();
  });

  it('the header catches up when the profile is known after it opened', async () => {
    window.getCurrentProfileContext = () => null;
    mockAccount();
    mockOidc({ enabled: true, label: 'Sign In With Pocket ID', connected: true, last: null });
    await opened();
    expect(modal().querySelector('.ma-title')!.textContent).toBe('My Account');
    window.getCurrentProfileContext = () => ({
      profileId: 7,
      isAdmin: false,
      name: 'Kim',
      avatarColor: '#6d5ce8',
      avatarUrl: '',
    });
    window.dispatchEvent(new CustomEvent('ss:webui-profile-context-changed'));
    expect(modal().querySelector('.ma-title')!.textContent).toBe('Kim');
  });
});

describe('My Account: scrobbling your own plays (Phonk_Ben_67)', () => {
  it('names a linked jellyfin user as where your listening comes from', async () => {
    mockAccount({ listening: { scope: 'profile', sources: ['jellyfin'] } });
    await opened();
    const card = body().querySelector('.ma-listening.is-own');
    expect(card?.textContent).toContain('your plays from Jellyfin');
  });

  it('a listenbrainz switch once your token is connected', async () => {
    mockAccount({
      connections: conns({ listenbrainz: { connected: true, account: 'kim_lb' } }),
      scrobbling: {
        listenbrainz: { available: true, enabled: false },
        lastfm: { available: false },
      },
    });
    capturePosts('/api/profiles/me/listenbrainz/scrobble', { success: true });
    await opened();
    const toggle = body().querySelector<HTMLInputElement>('[data-ma-scrobble="listenbrainz"]');
    expect(toggle?.checked).toBe(false);
    toggle!.checked = true;
    toggle!.dispatchEvent(new Event('change', { bubbles: true }));
    await vi.waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0].body).toEqual({ enabled: true });
    // last.fm isn't set up for scrobbling on this server: no line at all
    expect(body().textContent).not.toContain('Scrobble your plays to Last.fm');
  });

  it('last.fm offers sign in, and when signed in says where and can stop', async () => {
    mockAccount({ scrobbling: { lastfm: { available: true, connected: false } } });
    await opened();
    expect(body().querySelector('[data-ma="scrobble-signin"]')).not.toBeNull();

    closeMyAccountsModal();
    let deleted = false;
    mockAccount({ scrobbling: { lastfm: { available: true, connected: true, user: 'kimfm' } } });
    server.use(
      http.delete('/api/profiles/me/lastfm/scrobble', () => {
        deleted = true;
        return HttpResponse.json({ success: true });
      }),
    );
    await opened();
    expect(body().textContent).toContain('Scrobbling your plays to kimfm');
    click('[data-ma="scrobble-stop"]');
    await vi.waitFor(() => expect(deleted).toBe(true));
  });

  it('sign in waits for last.fm in a popup, then says who', async () => {
    let checks = 0;
    server.use(
      http.get('/api/profiles/me/lastfm/scrobble/auth-url', () =>
        HttpResponse.json({ success: true, url: 'https://www.last.fm/api/auth/?x' }),
      ),
      http.get('/api/profiles/me/scrobbling', () => {
        checks += 1;
        return HttpResponse.json({
          success: true,
          lastfm: { available: true, connected: checks >= 2, user: 'kimfm' },
        });
      }),
    );
    const popup = { location: { href: '' }, closed: false, close: vi.fn() };
    const ok = await _maLastfmScrobbleSignIn(
      null,
      () => popup as unknown as Window,
      async () => {},
    );
    expect(ok).toBe(true);
    expect(popup.location.href).toBe('https://www.last.fm/api/auth/?x');
    expect(popup.close).toHaveBeenCalled();
    expect(toasts).toContain('Scrobbling to kimfm');
  });

  it('sign in shows why the server refused', async () => {
    server.use(
      http.get('/api/profiles/me/lastfm/scrobble/auth-url', () =>
        HttpResponse.json({ success: false, error: 'ask the admin' }, { status: 400 }),
      ),
    );
    const popup = { location: { href: '' }, closed: false, close: vi.fn() };
    const ok = await _maLastfmScrobbleSignIn(
      null,
      () => popup as unknown as Window,
      async () => {},
    );
    expect(ok).toBe(false);
    expect(popup.close).toHaveBeenCalled();
    expect(toasts).toContain('ask the admin');
  });
});

describe('My Account: your own sidebar weather (#2163)', () => {
  let puts: Array<{ url: string; body: unknown }> = [];
  let current: Record<string, unknown> = WEATHER_OFF;

  function captureWeatherPuts() {
    puts = [];
    server.use(
      http.get('/api/weather', () => HttpResponse.json(current)),
      http.put('/api/weather/:what', async ({ request, params }) => {
        const body = JSON.parse(await request.text()) as Record<string, unknown>;
        puts.push({ url: `/api/weather/${String(params.what)}`, body });
        if (params.what === 'location') current = body.location ? WEATHER_BERLIN : WEATHER_OFF;
        return HttpResponse.json({ ...current, success: true });
      }),
    );
  }

  const row = () => document.getElementById('ma-sub-weather')?.textContent || '';

  beforeEach(() => {
    current = WEATHER_OFF;
    window.bootSidebarWeather = vi.fn(() => Promise.resolve());
  });
  afterEach(() => {
    Reflect.deleteProperty(window, 'bootSidebarWeather');
  });

  it('is off until you add your own place', async () => {
    mockAccount({});
    await opened();
    expect(row()).toBe('Off. Add your town to see its weather and time in the sidebar.');
    expect(body().querySelector('[data-ma="toggle"][data-id="weather"]')?.textContent).toBe(
      'Set up',
    );
  });

  it('saves your town, says so and refreshes the sidebar', async () => {
    mockAccount({});
    captureWeatherPuts();
    await opened();
    click('[data-ma="toggle"][data-id="weather"]');
    (document.getElementById('ma-wx-loc') as HTMLInputElement).value = 'Berlin';
    click('[data-ma="wx-save"]');
    await vi.waitFor(() => expect(row()).toBe('Berlin, 11°C, light drizzle'));
    expect(puts).toEqual([{ url: '/api/weather/location', body: { location: 'Berlin' } }]);
    expect(toasts).toContain('Weather set to Berlin');
    expect(window.bootSidebarWeather).toHaveBeenCalled();
  });

  it('asks for a town before saving an empty one', async () => {
    mockAccount({});
    captureWeatherPuts();
    await opened();
    click('[data-ma="toggle"][data-id="weather"]');
    click('[data-ma="wx-save"]');
    expect(toasts).toContain('Type your town or ZIP first');
    expect(puts).toEqual([]);
  });

  it('flips units and turns it off for you only', async () => {
    current = WEATHER_BERLIN;
    mockAccount({ weather: WEATHER_BERLIN });
    captureWeatherPuts();
    await opened();
    click('[data-ma="toggle"][data-id="weather"]');
    const celsius = document.querySelector('[data-ma-weather="celsius"]') as HTMLInputElement;
    expect(celsius.checked).toBe(true);
    celsius.click();
    await vi.waitFor(() =>
      expect(puts).toContainEqual({ url: '/api/weather/units', body: { use_celsius: false } }),
    );
    click('[data-ma="wx-clear"]');
    await vi.waitFor(() =>
      expect(puts).toContainEqual({ url: '/api/weather/location', body: { location: '' } }),
    );
    await vi.waitFor(() => expect(row()).toMatch(/^Off\./));
  });

  it('the admin keeps theirs in Settings, not here', async () => {
    mockAccount({ isAdmin: true, weather: WEATHER_BERLIN });
    openMyAccountsModal();
    await vi.waitFor(() => expect(body().querySelector('.ma-empty')).not.toBeNull());
    expect(document.getElementById('ma-sub-weather')).toBeNull();
  });
});

// what you can do (#2166): the member's own summary, read-only, from the
// server's map. no switches, and who to ask when something is off
describe('What you can do', () => {
  const card = () => document.getElementById('ma-cando') as HTMLElement;
  const row = (id: string) =>
    (document.querySelector(`#ma-cando [data-can="${id}"]`) as HTMLElement).textContent || '';

  it('a downloader: everything right away, nothing to ask about', async () => {
    mockAccount({});
    await opened();
    await vi.waitFor(() => expect(card()).not.toBeNull());
    expect(card().textContent).toContain(
      'Add music, movies, shows, audiobooks and podcast episodes whenever you like.',
    );
    expect(row('music')).toContain('Add anything you like');
    expect(row('requests')).toContain('Nothing to wait for: what you add comes right away');
    // #2303: no policy words, and no account of what isn't switched off
    expect(card().textContent).not.toContain('explicit shown');
    expect(card().textContent).not.toContain('Not part of your profile');
    expect(card().textContent).not.toContain('settings and people');
    expect(card().textContent).not.toContain('Ask Boulder');
    // read-only: nothing in it can be pressed or switched
    expect(card().querySelector('button, input, select')).toBeNull();
  });

  it('asks first, with the limit and how many are used', async () => {
    mockAccount({
      perms: {
        ...MEMBER_PERMS,
        'music.get': 'ask',
        'movies.get': 'ask',
        'movies.max_rating': 'PG-13',
        'tv.get': 'ask',
        'music.request_limit': { n: 5, days: 7 },
        'movies.request_limit': { n: 3, days: 7 },
        'tv.request_limit': { n: 0, days: 7 },
      },
      asks: {
        music: { kind: 'music', limit: 5, days: 7, used: 2, remaining: 3 },
        movies: { kind: 'movies', limit: 3, days: 7, used: 1, remaining: 2 },
      },
    });
    await opened();
    // each kind counts on its own, on its own row
    await vi.waitFor(() => expect(row('music')).toContain('5 asks a week, 2 used'));
    expect(row('movies')).toContain('3 asks a week, 1 used');
    expect(card().textContent).toContain(
      'You can ask for music, movies and shows, 5 music asks a week and 3 movie asks a week, and an admin approves it.',
    );
    expect(row('music')).toContain('Ask for it and an admin approves');
    expect(card().textContent).not.toContain('you see only your own');
    expect(row('requests')).toContain('5 music asks a week and 3 movie asks a week');
    expect(row('movies')).toContain('up to PG-13');
    expect(row('tv')).toContain('up to TV-14');
    expect(row('tv')).not.toContain('asks a week');
  });

  it('what is switched off is not listed as denied: there is who to ask (#2303)', async () => {
    mockAccount({
      perms: {
        ...MEMBER_PERMS,
        'books.access': false,
        'podcasts.access': false,
        'music.explorer': false,
        'chat.use': false,
      },
    });
    await opened();
    await vi.waitFor(() => expect(card()).not.toBeNull());
    await vi.waitFor(() => expect(card().textContent).toContain('Boulder'));
    // no row for something they don't have, and nothing called "not part of your profile"
    expect(document.querySelector('#ma-cando [data-can="books"]')).toBeNull();
    expect(card().textContent).not.toContain('Not part of your profile');
    expect(card().textContent).not.toContain('settings and people');
    // the rest is offered as something to ask for
    expect(card().textContent).toContain("There's more you can ask Boulder for:");
    expect(card().textContent).toContain('Playlist Explorer');
    expect(card().textContent).toContain('Chat & Arcade');
  });

  it('a moderator is told what they answer', async () => {
    mockAccount({
      perms: { ...MEMBER_PERMS, 'requests.moderate': true, 'requests.see_all': true },
    });
    await opened();
    await vi.waitFor(() => expect(row('requests')).toContain('moderator'));
    expect(row('requests')).toContain('you answer everyone’s asks but your own');
  });

  it('see everyone’s, without answering', async () => {
    mockAccount({ perms: { ...MEMBER_PERMS, 'requests.see_all': true } });
    await opened();
    await vi.waitFor(() => expect(row('requests')).toContain('you see everyone’s'));
    expect(row('requests')).not.toContain('moderator');
  });
});

describe('My Account: your listening', () => {
  it('share my listening starts off and says nobody gets a blend from you', async () => {
    mockAccount();
    capturePosts('/api/profiles/me/listening-privacy', {
      success: true,
      share_listening: true,
      asked: true,
      private_session: false,
    });
    await opened();
    const share = body().querySelector<HTMLInputElement>('[data-ma-privacy="share"]');
    expect(share?.checked).toBe(false);
    expect(body().querySelector('#ma-sub-share')?.textContent).toMatch(/^Off\./);
    share!.checked = true;
    share!.dispatchEvent(new Event('change', { bubbles: true }));
    await vi.waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0].body).toEqual({ share_listening: true });
    await vi.waitFor(() =>
      expect(body().querySelector('#ma-sub-share')?.textContent).toMatch(/^On\./),
    );
    expect(toasts).toContain('Sharing your listening for blends');
  });

  it('a private session says when it ends', async () => {
    mockAccount();
    capturePosts('/api/profiles/me/listening-privacy', {
      success: true,
      share_listening: false,
      private_session: true,
      private_until: '2026-11-20 18:00:00',
    });
    await opened();
    const priv = body().querySelector<HTMLInputElement>('[data-ma-privacy="private"]');
    priv!.checked = true;
    priv!.dispatchEvent(new Event('change', { bubbles: true }));
    await vi.waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0].body).toEqual({ private_session: true });
    const ends = _maPrivateUntil('2026-11-20 18:00:00');
    expect(ends).not.toBe('');
    await vi.waitFor(() =>
      expect(body().querySelector('#ma-sub-private')?.textContent).toContain(`On until ${ends}`),
    );
  });

  it('a refused save keeps the switch where it was', async () => {
    mockAccount();
    server.use(
      http.post('/api/profiles/me/listening-privacy', () =>
        HttpResponse.json({ success: false }, { status: 500 }),
      ),
    );
    await opened();
    const share = body().querySelector<HTMLInputElement>('[data-ma-privacy="share"]');
    share!.checked = true;
    share!.dispatchEvent(new Event('change', { bubbles: true }));
    await vi.waitFor(() => expect(toasts).toContain("Couldn't save that. Try again."));
    expect(body().querySelector<HTMLInputElement>('[data-ma-privacy="share"]')?.checked).toBe(
      false,
    );
  });

  it('the admin gets the switches too', async () => {
    mockAccount({ isAdmin: true });
    await opened();
    expect(body().querySelector('[data-ma-privacy="share"]')).not.toBeNull();
  });
});
