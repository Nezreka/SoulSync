/**
 * Downloads (music) answers, from the rows /api/downloads/all really sends
 * (core/downloads/status.py + live_detail.py shapes): an uploader's queue,
 * a not-found search breakdown, a failure, a held import, the review
 * counts, the Health findings that stop downloads; nothing when nothing is
 * wrong; a member gets the why without names or actions.
 */

import { act, fireEvent, renderHook, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { AdlDownload } from '@/routes/active-downloads/-adl.types';

import { useAdlDownloads } from '@/routes/active-downloads/-adl.use-downloads';
import { musicDownloadsFeed } from '@/routes/active-downloads/-dl.feed';
import { HttpResponse, http, server } from '@/test/msw';

import { readSearchBreakdown, useMusicDownloadAnswers } from './active-downloads';
import { answer, asProfile, renderAnswers, titles } from './answers.test-utils';
import { pageHelpFor } from './index';

const base = (over: Partial<AdlDownload>): AdlDownload => ({
  task_id: 't',
  title: 'Track',
  artist: 'Tool',
  album: 'Lateralus',
  artwork: '',
  status: 'downloading',
  progress: 0,
  error: null,
  verification_status: null,
  batch_id: 'b1',
  batch_name: 'Lateralus',
  batch_source: 'library',
  playlist_id: 'album_lateralus',
  track_index: 0,
  batch_total: 13,
  timestamp: 1763415727,
  priority: 2,
  quality: '',
  is_persistent_history: false,
  ...over,
});

const queuedAt = (task: string, title: string, index: number, user = 'tapeworm_88') =>
  base({
    task_id: task,
    title,
    status: 'queued',
    track_index: index,
    download_source: 'Soulseek',
    live_detail: {
      source: 'Soulseek',
      username: user,
      filename: `Tool - Lateralus/${title}.flac`,
      picked: { quality: 'flac', queue_length: 41, free_upload_slots: 0, upload_speed: 812000 },
      slskd_state: 'Queued, Remotely',
      queued_seconds: 1080,
    },
  });

const SCHISM = queuedAt('t-schism', 'Schism', 5);
const PARABOLA = queuedAt('t-parabola', 'Parabola', 9);
const LATERALUS = queuedAt('t-lateralus', 'Lateralus', 8);
const TEARDROP = base({
  task_id: 't-teardrop',
  title: 'Teardrop',
  artist: 'Massive Attack',
  album: 'Mezzanine',
  status: 'not_found',
  batch_id: 'b2',
  playlist_id: 'wishlist',
  error:
    'No match found for "Teardrop" by Massive Attack after 3 queries. Breakdown: "Massive Attack Teardrop": 14 results but none passed quality/artist filters | "Teardrop": 8 results but none passed quality/artist filters | "Massive Attack Teardrop Mezzanine": no results found',
});
const FAILED = base({
  task_id: 't-fail',
  title: 'Angel',
  artist: 'Massive Attack',
  status: 'failed',
  batch_id: 'b2',
  error: 'Download timed out after 600s with no progress',
});
const HELD = base({
  task_id: 't-held',
  title: 'Ticks & Leeches',
  status: 'post_processing',
  live_detail: { held_reason: 'Waiting for the rest of the album before importing' },
});
const DONE = base({
  task_id: 'history-9',
  title: 'Pneuma',
  status: 'completed',
  is_persistent_history: true,
  verification_status: 'verified',
});

let rows: AdlDownload[] = [];
let review = { success: true, quarantine: 0, unverified: 0, total: 0 };
let findings: unknown[] = [];
const healthCalls = vi.fn();

let polls = 0;

beforeEach(() => {
  polls = 0;
  rows = [];
  review = { success: true, quarantine: 0, unverified: 0, total: 0 };
  findings = [];
  healthCalls.mockReset();
  asProfile(1, true);
  server.use(
    http.get('/api/downloads/all', () => {
      polls += 1;
      return HttpResponse.json({
        success: true,
        downloads: rows,
        batches: [
          {
            batch_id: 'b1',
            playlist_id: 'album_lateralus',
            batch_name: 'Lateralus',
            source_page: 'library',
            phase: 'downloading',
            total: 13,
            completed: 9,
            failed: 0,
            active: 4,
            queued: 0,
          },
          {
            batch_id: 'b2',
            playlist_id: 'wishlist',
            batch_name: 'Wishlist',
            source_page: 'wishlist',
            phase: 'downloading',
            total: 2,
            completed: 0,
            failed: 2,
            active: 0,
            queued: 0,
          },
        ],
      });
    }),
    http.get('/api/review-queue/summary', () => HttpResponse.json(review)),
    http.get('/api/downloads/task/:id/detail', ({ params }) =>
      HttpResponse.json({
        success: true,
        detail: {
          task_id: params.id,
          decision: {
            outcome: 'nothing_passed',
            rejected_counts: { duration_mismatch: 9, version_conflict: 4 },
          },
        },
      }),
    ),
    http.get('/api/help/health', () => {
      healthCalls();
      return HttpResponse.json({ status: 'ok', checked_at: 'x', findings });
    }),
  );
});

afterEach(() => {
  delete window.getCurrentProfileContext;
});

describe('downloads answers (music)', () => {
  it('registers on the active-downloads page', () => {
    expect(pageHelpFor('active-downloads').useAnswers).toBe(useMusicDownloadAnswers);
  });

  it('says nothing but where files go when nothing is stuck', async () => {
    rows = [DONE];
    const { container } = renderAnswers(useMusicDownloadAnswers);
    await waitFor(() => expect(titles(container)).toEqual(['Where do finished downloads go?']));
  });

  it("a member with nothing stuck gets nothing, and Health isn't asked", async () => {
    asProfile(4, false);
    rows = [DONE];
    const { container } = renderAnswers(useMusicDownloadAnswers, { isAdmin: false });
    await waitFor(() => expect(container.querySelector('[data-testid=answers]')).toBeTruthy());
    await new Promise((r) => setTimeout(r, 30));
    expect(titles(container)).toEqual([]);
    expect(healthCalls).not.toHaveBeenCalled();
  });

  it('names the uploader, its slots and queue, and groups the rest', async () => {
    rows = [SCHISM, PARABOLA, LATERALUS, DONE];
    const { container } = renderAnswers(useMusicDownloadAnswers);
    await waitFor(() => expect(titles(container)[0]).toBe('Why are 3 downloads stuck in Queued?'));
    const a = answer(container, 'queued-remote');
    expect(a).toHaveAttribute('open');
    expect(a.textContent).toContain("They're queued on the uploader's side, not in SoulSync.");
    expect(a.textContent).toContain(
      'tapeworm_88 has no free upload slots and 41 people ahead of you, so these can sit for hours.',
    );
    expect(a.textContent).toContain('Queued 18 min · 41 ahead · 0 free slots');
    expect(a.textContent).toContain('Parabola and Lateralus');
    expect(a.textContent).toContain('Same uploader, same queue');
  });

  it('cancels those rows with the page own cancel call', async () => {
    rows = [SCHISM, PARABOLA];
    const cancelled: unknown[] = [];
    server.use(
      http.post('/api/downloads/cancel_task_v2', async ({ request }) => {
        cancelled.push(await request.json());
        return HttpResponse.json({ success: true, task_info: { track_name: 'Schism' } });
      }),
    );
    window.showToast = vi.fn();
    const { container } = renderAnswers(useMusicDownloadAnswers);
    await waitFor(() => answer(container, 'queued-remote'));
    fireEvent.click(
      within(answer(container, 'queued-remote')).getByRole('button', { name: 'Cancel these 2' }),
    );
    await waitFor(() => expect(cancelled).toHaveLength(2));
    expect(cancelled).toEqual([
      { playlist_id: 'album_lateralus', track_index: 5 },
      { playlist_id: 'album_lateralus', track_index: 9 },
    ]);
    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith('Cancelled 2 downloads', 'info'),
    );
  });

  it('reads a not-found search breakdown and the recorded decision', async () => {
    rows = [TEARDROP];
    const { container } = renderAnswers(useMusicDownloadAnswers);
    await waitFor(() => expect(titles(container)).toContain("Why wasn't Teardrop found?"));
    const a = answer(container, 'not-found');
    expect(a.textContent).toContain(
      'SoulSync searched 3 ways and 22 results came back, but none matched closely enough to trust.',
    );
    await waitFor(() =>
      expect(answer(container, 'not-found').textContent).toContain(
        'Passed over: 9 wrong length · 4 wrong version.',
      ),
    );
    expect(within(a).getByRole('button', { name: 'See what every source has' })).toBeTruthy();
  });

  it('groups failures by reason and offers Clear failed', async () => {
    rows = [FAILED];
    const { container } = renderAnswers(useMusicDownloadAnswers);
    await waitFor(() => expect(titles(container)).toContain('Why did Angel fail?'));
    const a = answer(container, 'failed');
    expect(a.textContent).toContain('Download timed out after 600s with no progress');
    expect(within(a).getByRole('button', { name: 'Clear failed' })).toBeTruthy();
  });

  it('says why an import is held', async () => {
    rows = [HELD];
    const { container } = renderAnswers(useMusicDownloadAnswers);
    await waitFor(() => expect(titles(container)).toContain('Why is Ticks & Leeches held?'));
    expect(answer(container, 'held').textContent).toContain(
      'Waiting for the rest of the album before importing',
    );
  });

  it('counts Review and opens it with the page tab', async () => {
    rows = [DONE];
    review = { success: true, quarantine: 3, unverified: 1, total: 4 };
    document.body.insertAdjacentHTML(
      'beforeend',
      '<div id="adl-view-tabs"><button data-filter="unverified">Review</button></div>',
    );
    const tab = document.querySelector<HTMLButtonElement>('#adl-view-tabs button')!;
    const clicked = vi.fn();
    tab.addEventListener('click', clicked);
    const { container } = renderAnswers(useMusicDownloadAnswers);
    await waitFor(() =>
      expect(titles(container)).toContain('Why are 4 downloads waiting in Review?'),
    );
    const a = answer(container, 'review');
    expect(a.textContent).toContain('3 files failed a check after downloading');
    expect(a.textContent).toContain(
      "1 track is in your library, but AcoustID couldn't confirm it's the right song.",
    );
    fireEvent.click(within(a).getByRole('button', { name: 'Open Review' }));
    expect(clicked).toHaveBeenCalled();
    document.getElementById('adl-view-tabs')?.remove();
  });

  it('puts a Health finding that stops downloads first, with its fixes', async () => {
    rows = [];
    findings = [
      {
        id: 'paths.download',
        severity: 'error',
        area: 'paths',
        title: "Download folder isn't writable",
        detail: "/downloads: SoulSync can't write there",
        fix: [{ label: 'Open download folder', kind: 'settings', tab: 'downloads' }],
        docs: 'gs-docker',
      },
      {
        id: 'disk.movies',
        severity: 'warning',
        area: 'disk',
        title: 'Movies drive is nearly full',
      },
      { id: 'services.download.hifi', severity: 'ok', area: 'services', title: 'HiFi connected' },
    ];
    const { container } = renderAnswers(useMusicDownloadAnswers);
    await waitFor(() => expect(titles(container)[0]).toBe("Download folder isn't writable"));
    const a = answer(container, 'health:paths.download');
    expect(a.dataset.tone).toBe('warn');
    expect(a.textContent).toContain("/downloads: SoulSync can't write there.");
    expect(within(a).getByRole('button', { name: 'Open download folder' })).toBeTruthy();
    expect(within(a).getByRole('button', { name: 'Check again' })).toBeTruthy();
    expect(titles(container)).not.toContain('Movies drive is nearly full');
    expect(titles(container)).not.toContain('HiFi connected');
  });

  it('a member gets the why with no names, rows or buttons', async () => {
    asProfile(4, false);
    rows = [SCHISM, PARABOLA, TEARDROP];
    const { container } = renderAnswers(useMusicDownloadAnswers, { isAdmin: false });
    await waitFor(() =>
      expect(titles(container)).toEqual([
        'Why are 2 downloads stuck in Queued?',
        "Why wasn't a track found?",
      ]),
    );
    expect(container.textContent).not.toContain('tapeworm_88');
    expect(container.textContent).not.toContain('Teardrop');
    expect(container.querySelectorAll('button')).toHaveLength(0);
    expect(healthCalls).not.toHaveBeenCalled();
  });
});

describe('readSearchBreakdown', () => {
  it('sums the results that came back and reads the query count', () => {
    expect(readSearchBreakdown(TEARDROP.error)).toEqual({ queries: 3, results: 22 });
    expect(readSearchBreakdown('')).toEqual({ queries: null, results: 0 });
  });
});

describe('one poll, not two', () => {
  const SNAPSHOT = { success: true, downloads: [] as AdlDownload[], batches: [] };

  it('with the Downloads page mounted, the answers read its poll and send none', async () => {
    const pageRefresh = vi.fn();
    const detach = musicDownloadsFeed.attach(pageRefresh);
    try {
      musicDownloadsFeed.publish({ ...SNAPSHOT, downloads: [SCHISM, PARABOLA, LATERALUS] });
      const { container } = renderAnswers(useMusicDownloadAnswers);
      await waitFor(() =>
        expect(titles(container)[0]).toBe('Why are 3 downloads stuck in Queued?'),
      );
      await new Promise((r) => setTimeout(r, 50));
      expect(polls).toBe(0);
      // the page's next poll is the answers' next read
      act(() => musicDownloadsFeed.publish({ ...SNAPSHOT, downloads: [DONE] }));
      await waitFor(() => expect(titles(container)).toEqual(['Where do finished downloads go?']));
      expect(polls).toBe(0);
    } finally {
      detach();
    }
  });

  it('once the page goes, the answers poll for themselves', async () => {
    const detach = musicDownloadsFeed.attach(vi.fn());
    musicDownloadsFeed.publish({ ...SNAPSHOT, downloads: [DONE] });
    rows = [SCHISM, PARABOLA, LATERALUS];
    const { container } = renderAnswers(useMusicDownloadAnswers);
    await waitFor(() => expect(titles(container)).toEqual(['Where do finished downloads go?']));
    expect(polls).toBe(0);
    act(() => detach());
    await waitFor(() => expect(polls).toBeGreaterThan(0));
    await waitFor(() => expect(titles(container)[0]).toBe('Why are 3 downloads stuck in Queued?'));
  });

  it('the page publishes each poll while it is mounted, and stops when it unmounts', async () => {
    rows = [DONE];
    const page = renderHook(() => useAdlDownloads());
    expect(musicDownloadsFeed.snapshot().live).toBe(true);
    await waitFor(() => expect(musicDownloadsFeed.snapshot().data?.downloads).toEqual([DONE]));
    page.unmount();
    expect(musicDownloadsFeed.snapshot()).toEqual({ live: false, data: undefined });
  });
});
