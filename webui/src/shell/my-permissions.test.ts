import { HttpResponse, http } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { server } from '@/test/msw';
import { ADULT, LISTEN_ONLY, TEEN } from '@/test/perms';

import {
  PERMS_CHANGED_EVENT,
  _resetMyPermissions,
  getMyPermissions,
  installMyPermissions,
  loadMyPermissions,
  onProfileChanged,
} from './my-permissions';

type Win = Window & {
  canDownload?: () => boolean;
  getCurrentProfileContext?: () => { profileId: number; isAdmin: boolean } | null;
};

let answer: { status: number; body: Record<string, unknown> } = { status: 200, body: {} };
let calls = 0;

function serve(effective: Record<string, unknown>, profileId = 7, isAdmin = false) {
  answer = {
    status: 200,
    body: {
      success: true,
      profile_id: profileId,
      is_admin: isAdmin,
      source: 'template',
      effective,
    },
  };
}

beforeEach(() => {
  _resetMyPermissions();
  calls = 0;
  document.body.className = '';
  delete document.body.dataset.musicGet;
  (window as Win).getCurrentProfileContext = () => ({ profileId: 7, isAdmin: false });
  (window as Win).canDownload = () => false;
  server.use(
    http.get('/api/profiles/me/permissions', () => {
      calls += 1;
      return HttpResponse.json(answer.body, { status: answer.status });
    }),
  );
});

afterEach(() => {
  _resetMyPermissions();
  Reflect.deleteProperty(window, 'SoulSyncPerms');
  vi.useRealTimers();
});

describe('the my-permissions store', () => {
  it('loads the server map and marks the page for css', async () => {
    serve(TEEN);
    const perms = await loadMyPermissions({ force: true });
    expect(perms.live).toBe(true);
    expect(document.body.dataset.musicGet).toBe('ask');
    expect(document.body.dataset.booksGet).toBe('none');
    // the old class still works for the code that hides on it
    expect(document.body.classList.contains('downloads-disabled')).toBe(true);
  });

  it('an admin flipping a member to download lifts the old class too', async () => {
    serve(TEEN);
    await loadMyPermissions({ force: true });
    serve(ADULT);
    await loadMyPermissions({ force: true });
    expect(document.body.dataset.musicGet).toBe('download');
    expect(document.body.classList.contains('downloads-disabled')).toBe(false);
  });

  it('tells listeners only when something changed', async () => {
    const seen: string[] = [];
    const on = (e: Event) => seen.push(String((e as CustomEvent).detail?.effective?.['music.get']));
    window.addEventListener(PERMS_CHANGED_EVENT, on);
    serve(TEEN);
    await loadMyPermissions({ force: true });
    await loadMyPermissions({ force: true });
    serve(LISTEN_ONLY);
    await loadMyPermissions({ force: true });
    window.removeEventListener(PERMS_CHANGED_EVENT, on);
    expect(seen).toEqual(['ask', 'none']);
  });

  it('keeps the last good map when the server cannot build one', async () => {
    serve(TEEN);
    await loadMyPermissions({ force: true });
    answer = { status: 503, body: { success: false, error: 'permissions_unavailable' } };
    const perms = await loadMyPermissions({ force: true });
    expect(perms.live).toBe(true);
    expect(perms.effective['music.get']).toBe('ask');
  });

  it('falls back to the profile columns before the server answers', async () => {
    answer = { status: 401, body: { success: false, error: 'profile_required' } };
    const perms = await loadMyPermissions({ force: true });
    expect(perms.live).toBe(false);
    expect(perms.source).toBe('columns');
    expect(document.body.dataset.musicGet).toBe('ask');
  });

  it('a profile switch forgets the last profile at once and drops its late answer', async () => {
    serve(ADULT, 7);
    await loadMyPermissions({ force: true });
    expect(getMyPermissions().live).toBe(true);
    // switch to profile 9, whose columns say no downloads
    (window as Win).getCurrentProfileContext = () => ({ profileId: 9, isAdmin: false });
    serve(LISTEN_ONLY, 9);
    onProfileChanged();
    expect(getMyPermissions().live).toBe(false);
    expect(getMyPermissions().profileId).toBe(9);
    await vi.waitFor(() => expect(getMyPermissions().live).toBe(true));
    expect(getMyPermissions().effective['music.get']).toBe('none');
  });

  it('installs window.SoulSyncPerms and polls lightly while visible', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    serve(TEEN);
    installMyPermissions();
    await vi.waitFor(() => expect(window.SoulSyncPerms?.live()).toBe(true));
    expect(window.SoulSyncPerms?.mode('music')).toBe('ask');
    expect(window.SoulSyncPerms?.limit()).toEqual({ n: 5, days: 7 });
    expect(window.SoulSyncPerms?.on('requests.moderate')).toBe(false);
    const before = calls;
    serve(ADULT);
    await vi.advanceTimersByTimeAsync(120_000);
    await vi.waitFor(() => expect(window.SoulSyncPerms?.mode('music')).toBe('download'));
    expect(calls).toBeGreaterThan(before);
  });
});
