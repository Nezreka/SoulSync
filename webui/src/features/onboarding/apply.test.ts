/**
 * The ready step's save-then-check. The old wizard's lifecycle tests
 * (src/test/setup-wizard-lifecycle.test.ts, removed with it) pinned that a
 * failed save never marks setup complete; these pin the same for the new
 * flow, plus the preview's promise that nothing is written.
 */

import { beforeEach, describe, expect, it } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';

import type { Draft } from './types';

import { applyDraft, previewEffects, realEffects, SAVE_FAILED, type ReadyRow } from './apply';
import { emptyDraft } from './plan';

const WRITES =
  /\/api\/(settings|profiles|watchlist\/add|setup\/complete|quality-profile|video\/|plex\/select|jellyfin\/select|navidrome\/select|auth\/login)/;

let calls: { method: string; path: string; body: unknown }[];

function record() {
  calls = [];
  const log = async ({ request }: { request: Request }) => {
    const url = new URL(request.url);
    let body: unknown = null;
    try {
      body = await request.clone().json();
    } catch {
      body = null;
    }
    calls.push({ method: request.method, path: url.pathname, body });
  };
  server.use(
    http.post('*/api/onboarding/check-paths', async (info) => {
      await log(info);
      const body = (await info.request.json()) as { paths: Record<string, string> };
      return HttpResponse.json({
        results: Object.fromEntries(
          Object.keys(body.paths).map((k) => [
            k,
            { state: 'ok', message: '', free_bytes: 1, path: '', resolved: '' },
          ]),
        ),
      });
    }),
    http.post('*/api/onboarding/probe', async (info) => {
      await log(info);
      return HttpResponse.json({ ok: true, message: 'Connected', detail: {} });
    }),
    http.get('*/api/discover/build-playlist/search-artists', async (info) => {
      await log(info);
      const q = new URL(info.request.url).searchParams.get('query') ?? '';
      return HttpResponse.json({ artists: [{ id: `id-${q}`, name: q, image_url: '' }] });
    }),
    http.all('*/api/*', async (info) => {
      await log(info);
      return HttpResponse.json({ success: true });
    }),
  );
}

function draft(fn?: (d: Draft) => void): Draft {
  const d = emptyDraft({
    folders: {
      download: './downloads',
      library: './Transfer',
      staging: './Staging',
      movies: '',
      tv: '',
      youtube: '',
      podcasts: '',
      audiobooks: '',
    },
  });
  d.account.name = 'Boulder';
  d.account.pin = '1234';
  d.server.kind = 'soulsync';
  d.follows = [{ name: 'Radiohead', id: 'stale-id-from-another-catalogue' }];
  fn?.(d);
  return d;
}

beforeEach(record);

describe('the preview', () => {
  it('writes nothing, but still runs every check', async () => {
    const fx = previewEffects();
    const rows = await applyDraft(draft(), fx, () => undefined);
    expect(calls.filter((c) => WRITES.test(c.path))).toEqual([]);
    expect(calls.some((c) => c.path.endsWith('/onboarding/check-paths'))).toBe(true);
    expect(fx.log).toContain('settings');
    expect(rows.every((r) => r.state === 'ok')).toBe(true);
  });
});

describe('a real install', () => {
  it('saves settings first, then follows by name in the new catalogue', async () => {
    await applyDraft(draft(), realEffects, () => undefined);
    const writes = calls.filter((c) => WRITES.test(c.path));
    expect(writes[0].path).toBe('/api/settings');
    const follow = writes.find((c) => c.path === '/api/watchlist/add');
    // the id picked earlier came from whatever catalogue was active then
    expect(follow?.body).toMatchObject({ artist_id: 'id-Radiohead', artist_name: 'Radiohead' });
  });

  it('never marks setup complete on its own, that waits for Open SoulSync', async () => {
    await applyDraft(draft(), realEffects, () => undefined);
    expect(calls.some((c) => c.path === '/api/setup/complete')).toBe(false);
    expect(calls.some((c) => c.path.startsWith('/api/profiles/1/set-pin'))).toBe(false);
  });

  it.each([400, 200])(
    'a rejected settings save (HTTP %s) stops everything else',
    async (status) => {
      server.use(
        http.post('*/api/settings', () =>
          HttpResponse.json({ success: false, error: 'Settings rejected' }, { status }),
        ),
      );
      const rows = await applyDraft(draft(), realEffects, () => undefined);
      expect(rows).toEqual([
        { id: SAVE_FAILED, name: "Couldn't save", state: 'warn', detail: 'Settings rejected' },
      ]);
      expect(
        calls.some((c) => c.path === '/api/watchlist/add' || c.path.startsWith('/api/profiles')),
      ).toBe(false);
    },
  );

  it('rows go out as checking first, then settle', async () => {
    const seen: ReadyRow[] = [];
    await applyDraft(draft(), realEffects, (r) => seen.push({ ...r }));
    const first = seen.find((r) => r.id === 'folders');
    const last = [...seen].reverse().find((r) => r.id === 'folders');
    expect(first?.state).toBe('checking');
    expect(last?.state).toBe('ok');
  });

  it('a failing check turns its row amber and points at the step that fixes it', async () => {
    server.use(
      http.post('*/api/onboarding/probe', () =>
        HttpResponse.json({ ok: false, message: 'slskd refused that API key' }),
      ),
    );
    const rows = await applyDraft(
      draft((d) => {
        d.chain = ['soulseek'];
        d.slskd = { url: 'http://s:5030', apiKey: 'x', connected: true };
      }),
      realEffects,
      () => undefined,
    );
    expect(rows.find((r) => r.id === 'sources')).toMatchObject({
      state: 'warn',
      detail: 'slskd refused that API key',
      fix: 'sources',
    });
  });
});

describe('locking the install', () => {
  it('a pin is set, the lock turned on, then this browser unlocked, in that order', async () => {
    await realEffects.lock(draft().account);
    expect(calls.map((c) => c.path)).toEqual([
      '/api/profiles/1/set-pin',
      '/api/settings',
      '/api/profiles/verify-launch-pin',
    ]);
    expect(calls[1].body).toEqual({ security: { require_pin_on_launch: true } });
  });

  it('a password signs this browser in as the profile name', async () => {
    const d = draft((x) => {
      x.account.lock = 'password';
      x.account.password = 'correct horse';
    });
    await realEffects.lock(d.account);
    expect(calls.map((c) => c.path)).toEqual([
      '/api/profiles/1/set-password',
      '/api/settings',
      '/api/auth/login',
    ]);
    expect(calls[2].body).toEqual({ username: 'Boulder', password: 'correct horse' });
  });

  it('an open install sends nothing', async () => {
    await realEffects.lock({ ...draft().account, lock: 'open' });
    expect(calls).toEqual([]);
  });

  it('a refused completion throws instead of looking finished', async () => {
    server.use(
      http.post('*/api/setup/complete', () =>
        HttpResponse.json({ success: false, error: 'Try again' }, { status: 503 }),
      ),
    );
    await expect(realEffects.complete(['music'])).rejects.toThrow();
  });
});
