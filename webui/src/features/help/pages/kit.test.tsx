/**
 * An answer's fix buttons run the Health view's runner (fixes.ts), so a fix
 * behaves the same from a page's answers as from Health: the video side for
 * a video fix, only the app's own /api/ routes posted, the server's words in
 * the toast, Health re-read once it has run.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';

import { useHelpStore } from '../store';
import { openSettings, runFix } from './kit';

let posted: { url: string; body: unknown }[] = [];
let fresh = 0;

beforeEach(() => {
  posted = [];
  fresh = 0;
  useHelpStore.setState(useHelpStore.getInitialState(), true);
  document.body.setAttribute('data-side', 'music');
  window.showToast = vi.fn() as never;
  window.navigateToPage = vi.fn() as never;
  window.switchSettingsTab = vi.fn();
  window._switchAppSide = vi.fn() as never;
  server.use(
    http.post('*/api/test-connection', async ({ request }) => {
      posted.push({ url: '/api/test-connection', body: await request.json() });
      return HttpResponse.json({ success: true, message: 'Connected to Jellyfin' });
    }),
    http.post('*/api/enrichment/spotify/resume', () =>
      HttpResponse.json({ success: false, error: 'Still rate limited' }),
    ),
    http.get('*/api/help/health', ({ request }) => {
      if (new URL(request.url).searchParams.get('fresh')) fresh += 1;
      return HttpResponse.json({ status: 'ok', findings: [] });
    }),
  );
});

afterEach(() => {
  delete window.showToast;
  delete window.navigateToPage;
  delete window.switchSettingsTab;
  delete window._switchAppSide;
  document.body.removeAttribute('data-side');
  document.body.innerHTML = '';
  vi.restoreAllMocks();
});

describe('a fix from an answer', () => {
  it('a video settings fix switches to the video side first', async () => {
    const nav = document.createElement('a');
    nav.className = 'nav-button';
    nav.dataset.videoPage = 'video-settings';
    const clicked = vi.fn();
    nav.addEventListener('click', clicked);
    document.body.appendChild(nav);
    await runFix({
      label: 'Open Library folders',
      kind: 'settings',
      tab: 'library',
      media: 'video',
    });
    expect(window._switchAppSide).toHaveBeenCalledWith('video');
    expect(clicked).toHaveBeenCalledTimes(1);
    expect(window.switchSettingsTab).toHaveBeenCalledWith('library');
  });

  it("a post says the server's own words, and the answers re-read Health", async () => {
    const done = vi.fn();
    await runFix(
      {
        label: 'Test again',
        kind: 'post',
        url: '/api/test-connection',
        body: { service: 'jellyfin' },
        recheck: true,
      },
      done,
    );
    expect(posted).toEqual([{ url: '/api/test-connection', body: { service: 'jellyfin' } }]);
    expect(window.showToast).toHaveBeenCalledWith('Connected to Jellyfin', 'success');
    expect(fresh).toBe(1);
    expect(done).toHaveBeenCalledTimes(1);
  });

  it('a refused post says why and checks nothing again', async () => {
    const done = vi.fn();
    await runFix(
      { label: 'Resume', kind: 'post', url: '/api/enrichment/spotify/resume', recheck: true },
      done,
    );
    expect(window.showToast).toHaveBeenCalledWith('Still rate limited', 'error');
    expect(fresh).toBe(0);
    expect(done).not.toHaveBeenCalled();
  });

  it('a post to anything but the app api is never sent', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch');
    await runFix({ label: 'Do it', kind: 'post', url: 'https://example.com/api/x' });
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(window.showToast).toHaveBeenCalledWith("That fix isn't one SoulSync knows.", 'error');
  });

  it('logs opens the Logs view in the sheet', async () => {
    useHelpStore.getState().setAdmin(true);
    useHelpStore.getState().openSheet();
    await runFix({ label: 'Open logs', kind: 'logs' });
    expect(useHelpStore.getState().stack.at(-1)?.view).toBe('logs');
  });

  it('openSettings, from an answer, gets the sheet out of the way on a phone', () => {
    const before = window.matchMedia;
    window.matchMedia = ((q: string) =>
      ({
        matches: q.includes('max-width'),
        media: q,
      }) as MediaQueryList) as typeof window.matchMedia;
    try {
      useHelpStore.getState().openSheet();
      openSettings('downloads');
      expect(useHelpStore.getState().open).toBe(false);
      expect(window.navigateToPage).toHaveBeenCalledWith('settings', undefined);
    } finally {
      window.matchMedia = before;
    }
  });
});
