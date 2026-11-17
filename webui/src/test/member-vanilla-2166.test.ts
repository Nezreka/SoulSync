import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ADULT, LISTEN_ONLY, MODERATOR, SEE_ALL, TEEN, clearPerms, stubPerms } from './perms';
import { extractFunction } from './vanilla-extract';

/**
 * the vanilla half of what a member sees (#2166, piece F): the sidebar's
 * Requests, the css that hides what a profile can't use, the download
 * modals' calm line and asks-left, and the refusal toast. all on a mocked
 * permission map.
 */

const read = (p: string) => readFileSync(resolve(process.cwd(), p), 'utf8').replace(/\r\n/g, '\n');
const NAV = read('static/profile-nav.js');
const CORE = read('static/core.js');
const INIT = read('static/init.js');
const CSS = read('static/style.css');
const HTML = read('index.html');

type Profile = { id: number; is_admin: boolean; can_download?: boolean; allowed_pages?: null };
type NavApi = { requestsVisible: (p: Profile, side: 'music' | 'video') => boolean };
type G = typeof globalThis & {
  SOULSYNC_BOOT_PROFILE?: Profile | null;
  SoulSyncProfileNav?: NavApi;
};
const g = globalThis as G;

function nav(): NavApi {
  g.SOULSYNC_BOOT_PROFILE = null;
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  new Function(NAV)();
  return g.SoulSyncProfileNav!;
}

const MEMBER: Profile = { id: 7, is_admin: false, can_download: false, allowed_pages: null };

afterEach(() => {
  clearPerms();
  vi.unstubAllGlobals();
});

describe('Requests in the sidebar', () => {
  it('before the map is in, the columns decide (today’s rule)', () => {
    expect(nav().requestsVisible(MEMBER, 'music')).toBe(true);
    expect(nav().requestsVisible({ ...MEMBER, can_download: true }, 'music')).toBe(false);
  });

  it('asks first: there; downloads: not; can’t request: not', () => {
    stubPerms(TEEN);
    expect(nav().requestsVisible(MEMBER, 'music')).toBe(true);
    stubPerms(ADULT);
    expect(nav().requestsVisible(MEMBER, 'music')).toBe(false);
    stubPerms(LISTEN_ONLY);
    expect(nav().requestsVisible(MEMBER, 'music')).toBe(false);
  });

  it('a moderator or see-everyone’s gets it even when they download', () => {
    stubPerms({ ...MODERATOR, 'music.get': 'download' });
    expect(nav().requestsVisible(MEMBER, 'music')).toBe(true);
    stubPerms({ ...SEE_ALL, 'music.get': 'download' });
    expect(nav().requestsVisible(MEMBER, 'music')).toBe(true);
  });

  it('video requests follow movies and tv, not music', () => {
    stubPerms({ ...ADULT, 'tv.get': 'ask' });
    expect(nav().requestsVisible(MEMBER, 'video')).toBe(true);
    expect(nav().requestsVisible(MEMBER, 'music')).toBe(false);
  });

  it('another profile’s map never decides', () => {
    stubPerms(TEEN, { profileId: 99 });
    expect(nav().requestsVisible({ ...MEMBER, can_download: true }, 'music')).toBe(false);
  });

  it('init.js asks the same rule and re-applies the nav on a change', () => {
    expect(INIT).toContain("nav.requestsVisible(currentProfile, 'music')");
    expect(INIT).toMatch(
      /addEventListener\('ss:perms-changed'[\s\S]{0,200}SoulSyncProfileNav\?\.apply\(currentProfile\)/,
    );
  });
});

describe('canDownload follows the live map', () => {
  type W = { currentProfile: unknown; SoulSyncPerms?: unknown };
  function canDownloadWith(profile: Record<string, unknown>): boolean {
    const fn = extractFunction('canDownload', INIT);
    // eslint-disable-next-line @typescript-eslint/no-implied-eval
    return new Function('currentProfile', 'window', `${fn}; return canDownload();`)(
      profile,
      window as unknown as W,
    ) as boolean;
  }

  it('the column until the map lands, then the map', () => {
    clearPerms();
    expect(canDownloadWith({ id: 7, can_download: false })).toBe(false);
    stubPerms(ADULT, { profileId: 7 });
    expect(canDownloadWith({ id: 7, can_download: false })).toBe(true);
    // a map for someone else is ignored
    stubPerms(ADULT, { profileId: 8 });
    expect(canDownloadWith({ id: 7, can_download: false })).toBe(false);
  });
});

describe('the css', () => {
  it('can’t request hides every Request control; asking keeps the discography one', () => {
    expect(CSS).toContain('body[data-music-get="none"] [data-request-action]');
    expect(CSS).toContain(
      'body.downloads-disabled[data-music-get="ask"] .discog-download-btn[data-request-action]',
    );
  });

  it('books and podcasts hide on their own permission, not music’s', () => {
    expect(CSS).toContain('body[data-books-get="none"] [data-books-action]');
    expect(CSS).toContain('body[data-podcasts-get="none"] [data-podcasts-action]');
  });

  it('the wishlist modal’s Download Now hides for askers, its Request for can’t', () => {
    expect(HTML).toMatch(/id="wishlist-download-now-btn" data-download-action/);
    expect(HTML).toMatch(/id="confirm-add-to-wishlist-btn" data-request-action/);
    expect(HTML).toContain('class="music-get-off-note"');
  });

  it('the download modals carry the calm line and the asks-left spot', () => {
    for (const f of ['downloads.js', 'shared-helpers.js']) {
      const src = read(`static/${f}`);
      expect(src).toContain('class="music-get-off-note"');
      expect(src).toContain('data-asks-left');
      expect(src).toContain('data-request-action id="add-to-wishlist-btn-');
      expect(src).toContain('window.fillMusicAsksLeft(modal)');
    }
  });
});

describe('core.js', () => {
  let toasts: string[] = [];
  beforeEach(() => {
    toasts = [];
    vi.stubGlobal('showToast', (m: string) => toasts.push(m));
  });

  function load(): Record<string, (...a: unknown[]) => unknown> {
    const fns = ['_musicAsksLeftLine', 'fillMusicAsksLeft', 'checkMusicRequestQuota'].map((n) =>
      extractFunction(n, CORE),
    );
    // eslint-disable-next-line @typescript-eslint/no-implied-eval
    return new Function(
      'canDownload',
      'showToast',
      `${fns.join('\n')}; return { _musicAsksLeftLine, fillMusicAsksLeft, checkMusicRequestQuota };`,
    )(
      () => false,
      (m: string) => toasts.push(m),
    ) as Record<string, (...a: unknown[]) => unknown>;
  }

  function quota(q: Record<string, unknown> | null) {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ success: true, quota: q }), { status: 200 })),
    );
  }

  it('fills "3 of 5 asks left this week" beside a Request', async () => {
    stubPerms(TEEN);
    quota({ limit: 5, days: 7, used: 2, remaining: 3 });
    const root = document.createElement('div');
    root.innerHTML = '<span data-asks-left></span>';
    load().fillMusicAsksLeft(root);
    await vi.waitFor(() => expect(root.textContent).toBe('3 of 5 asks left this week'));
  });

  it('leaves it empty for a downloader and for can’t request', async () => {
    quota({ limit: 5, days: 7, used: 2, remaining: 3 });
    for (const map of [ADULT, LISTEN_ONLY]) {
      stubPerms(map);
      const root = document.createElement('div');
      root.innerHTML = '<span data-asks-left>stale</span>';
      load().fillMusicAsksLeft(root);
      await new Promise((r) => setTimeout(r, 0));
      expect(root.textContent).toBe('');
    }
  });

  it('a stray add from a can’t-request page is a calm no', async () => {
    stubPerms(LISTEN_ONLY);
    const ok = await load().checkMusicRequestQuota();
    expect(ok).toBe(false);
    expect(toasts[0]).toBe(
      "Getting music isn't part of your profile. Ask your admin if you'd like it.",
    );
  });
});

describe('a refusal the page didn’t expect', () => {
  it('shows the server’s words and refreshes the map', () => {
    expect(INIT).toMatch(
      /body\.error === 'not_allowed'[\s\S]{0,200}SoulSyncPerms\.load\(\{ force: true \}\)[\s\S]{0,300}showToast\(body\.message/,
    );
  });
});
