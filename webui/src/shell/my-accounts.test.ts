/**
 * My Account: one modal for your media server login and your own music
 * services (it replaced My Accounts + the vanilla My Settings). these pin what
 * a non-admin actually sees and what each action sends.
 */

import { HttpResponse, http } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { server } from '@/test/msw';

import {
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
} = {}) {
  server.use(
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
  it('is one modal with your media server and your music services', async () => {
    mockAccount({ library: { navidrome_username: 'kim' } });
    await opened();

    expect(modal().getAttribute('role')).toBe('dialog');
    expect(modal().textContent).toContain('Kim');
    const titles = [...body().querySelectorAll('.ma-section-title')].map((h) => h.textContent);
    expect(titles).toEqual(['Media server', 'Music services']);
    expect(body().textContent).toContain(
      'Signed in as kim. Your playlists sync to your own account.',
    );
    const names = [...body().querySelectorAll('.ma-row-name')].map((n) => n.textContent);
    expect(names).toEqual(['Navidrome', 'Spotify', 'Tidal', 'ListenBrainz', 'Last.fm']);
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
    expect(body().querySelector('.ma-section')).toBeNull();
  });
});
