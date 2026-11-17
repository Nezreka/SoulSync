/**
 * Library answers: a scan running with its phase and count, a failed scan,
 * tracks under Unknown Artist with the page's own Review, the media server
 * down (Health for the admin, /status for a member); the video library's
 * folder and drive findings only. Nothing on a quiet library.
 */

import { fireEvent, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';

import { answer, asProfile, renderAnswers, titles } from './answers.test-utils';
import { pageHelpFor } from './index';
import { libraryAnswers, useLibraryAnswers, useVideoLibraryAnswers } from './library';

const IDLE = { status: 'idle', phase: '', progress: 0, processed: 0, total: 0 };
let scan: Record<string, unknown> = IDLE;
let unmatched: Record<string, unknown> = { success: true, count: 0, artist_id: null, ready: 0 };
let findings: unknown[] = [];

beforeEach(() => {
  scan = IDLE;
  unmatched = { success: true, count: 0, artist_id: null, ready: 0 };
  findings = [];
  asProfile(1, true);
  server.use(
    http.get('/api/database/update/status', () => HttpResponse.json(scan)),
    http.get('/api/library/unmatched-summary', () => HttpResponse.json(unmatched)),
    http.get('/api/help/health', () => HttpResponse.json({ status: 'ok', findings })),
  );
});
afterEach(() => {
  delete window.getCurrentProfileContext;
  document.querySelector('.library-unmatched-btn')?.remove();
});

describe('library answers', () => {
  it('registers on both library pages', () => {
    expect(pageHelpFor('library').useAnswers).toBe(useLibraryAnswers);
    expect(pageHelpFor('video-library').useAnswers).toBe(useVideoLibraryAnswers);
  });

  it('a quiet library says nothing', async () => {
    const { container } = renderAnswers(useLibraryAnswers);
    await new Promise((r) => setTimeout(r, 60));
    expect(titles(container)).toEqual([]);
  });

  it('a running scan says what it is doing', async () => {
    scan = {
      status: 'running',
      phase: 'Reading albums from Plex',
      progress: 42.4,
      processed: 1270,
      total: 3000,
    };
    const { container } = renderAnswers(useLibraryAnswers);
    await waitFor(() => expect(titles(container)).toEqual(['What is the library scan doing?']));
    const a = answer(container, 'scan');
    expect(a.textContent).toContain('Reading albums from Plex. 1,270 of 3,000 items so far.');
    expect(within(a).getByRole('progressbar')).toHaveAttribute('aria-valuenow', '42');
  });

  it('a failed scan says why', () => {
    const out = libraryAnswers(
      { scan: { status: 'error', error_message: 'Plex returned 401 Unauthorized' } },
      { page: 'library', isAdmin: true },
    );
    expect(out.map((a) => a.title)).toEqual(['Why did the last library scan fail?']);
    expect(out[0].tone).toBe('warn');
  });

  it('Unknown Artist tracks, with the page own Review button', async () => {
    unmatched = { success: true, count: 23, artist_id: 9, ready: 5 };
    document.body.insertAdjacentHTML(
      'beforeend',
      '<button class="library-unmatched-btn">Review</button>',
    );
    const review = vi.fn();
    document.querySelector('.library-unmatched-btn')!.addEventListener('click', review);
    const { container } = renderAnswers(useLibraryAnswers);
    await waitFor(() =>
      expect(titles(container)).toEqual(['Why are 23 tracks under Unknown Artist?']),
    );
    const a = answer(container, 'unmatched');
    expect(a.textContent).toContain(
      '5 have a strong match ready, found by listening to the audio.',
    );
    fireEvent.click(within(a).getByRole('button', { name: 'Review them' }));
    expect(review).toHaveBeenCalled();
  });

  it('Unknown Artist is the admin review, even with the count in hand', () => {
    const data = { unmatched: { count: 23, artist_id: 9, ready: 5 } };
    expect(libraryAnswers(data, { page: 'library', isAdmin: false })).toEqual([]);
    expect(libraryAnswers(data, { page: 'library', isAdmin: true }).map((a) => a.id)).toEqual([
      'unmatched',
    ]);
  });

  it('the media server down: Health with its fix for the admin', async () => {
    findings = [
      {
        id: 'services.media_server',
        severity: 'error',
        area: 'services',
        title: "Plex isn't answering",
        fix: [{ label: 'Open Connections', kind: 'settings', tab: 'connections' }],
      },
    ];
    const { container } = renderAnswers(useLibraryAnswers);
    await waitFor(() => expect(titles(container)).toEqual(["Plex isn't answering"]));
  });

  it('a member: the server down from /status, no Unknown Artist, no Health', async () => {
    asProfile(4, false);
    unmatched = { success: true, count: 23, artist_id: 9, ready: 5 };
    let healthAsked = 0;
    server.use(
      http.get('/status', () =>
        HttpResponse.json({ media_server: { type: 'jellyfin', connected: false } }),
      ),
      http.get('/api/help/health', () => ((healthAsked += 1), HttpResponse.json({ findings: [] }))),
    );
    const { container } = renderAnswers(useLibraryAnswers, { isAdmin: false });
    await waitFor(() => expect(titles(container)).toEqual(["Why isn't new music showing up?"]));
    expect(answer(container, 'server-down').textContent).toContain(
      "Jellyfin isn't answering right now",
    );
    expect(healthAsked).toBe(0);
  });

  it('video library: folders and drives only, admin only', async () => {
    findings = [
      {
        id: 'video.movies_path',
        severity: 'error',
        area: 'video',
        title: 'Movies folder is unreachable',
      },
      {
        id: 'video.tv_space',
        severity: 'warning',
        area: 'video',
        title: 'TV drive is nearly full',
      },
      { id: 'video.indexers', severity: 'error', area: 'video', title: 'Indexers needs a look' },
      {
        id: 'video.repair_errors',
        severity: 'warning',
        area: 'video',
        title: 'Repair needs a look',
      },
    ];
    const { container } = renderAnswers(useVideoLibraryAnswers);
    await waitFor(() =>
      expect(titles(container)).toEqual([
        'Movies folder is unreachable',
        'TV drive is nearly full',
      ]),
    );
    const member = renderAnswers(useVideoLibraryAnswers, { isAdmin: false });
    await new Promise((r) => setTimeout(r, 30));
    expect(titles(member.container)).toEqual([]);
  });
});
