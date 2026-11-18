/**
 * Downloads (video) answers, from /api/video/downloads/active rows shaped
 * like the video_downloads table: a slot wait with the peer's numbers, a
 * transfer with no progress for 10 minutes, a failure with its backups and
 * the page's own Block and retry, an import failure pointing at Manual
 * import, a copy under the cutoff; nothing for a clean list; a member gets
 * the why without titles or buttons.
 */

import { act, fireEvent, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { VdlDownload } from '@/routes/active-downloads/-vdl.types';

import { videoDownloadsFeed } from '@/routes/active-downloads/-dl.feed';
import { HttpResponse, http, server } from '@/test/msw';

import { answer, asProfile, renderAnswers, titles } from './answers.test-utils';
import { pageHelpFor } from './index';
import { VIDEO_STALL_MS, useVideoDownloadAnswers, videoDownloadAnswers } from './video-downloads';

const utc = (ms: number) => new Date(ms).toISOString().slice(0, 19).replace('T', ' ');

const DUNE: VdlDownload = {
  id: 11,
  kind: 'movie',
  title: 'Dune: Part Two',
  year: 2024,
  status: 'queued',
  progress: 0,
  username: 'reelbox',
  filename: 'Dune.Part.Two.2024.2160p.mkv',
  release_title: 'Dune.Part.Two.2024.2160p.WEB-DL',
  source: 'slskd',
  search_ctx: JSON.stringify({ peer: { slots: 0, queue: 12, speed: 900000 } }),
  candidates: '[]',
};
const SHOGUN: VdlDownload = {
  id: 12,
  kind: 'episode',
  title: 'Shogun',
  status: 'downloading',
  progress: 41,
  source: 'torrent',
  username: 'qbittorrent',
  release_title: 'Shogun.S01E03.1080p',
  search_ctx: JSON.stringify({ season: 1, episode: 3 }),
  progress_at: utc(Date.now() - 25 * 60_000),
  candidates: JSON.stringify([{ title: 'a' }, { title: 'b' }]),
};
const ARRIVAL: VdlDownload = {
  id: 13,
  kind: 'movie',
  title: 'Arrival',
  year: 2016,
  status: 'failed',
  username: 'cinephile',
  filename: 'Arrival.2016.1080p.mkv',
  error: 'The uploader went offline',
  attempts: 2,
  source: 'slskd',
  candidates: JSON.stringify([{ title: 'x' }]),
};
const HEAT: VdlDownload = {
  id: 14,
  kind: 'movie',
  title: 'Heat',
  status: 'import_failed',
  error: 'No video file in the download folder',
};
const ALIEN: VdlDownload = {
  id: 15,
  kind: 'movie',
  title: 'Alien',
  status: 'completed',
  upgrade_watch: true,
};
const DONE: VdlDownload = { id: 16, kind: 'movie', title: 'Up', status: 'completed' };

let rows: VdlDownload[] = [];
let polls = 0;
beforeEach(() => {
  polls = 0;
  rows = [];
  asProfile(1, true);
  server.use(
    http.get('/api/video/downloads/active', () => {
      polls += 1;
      return HttpResponse.json({ downloads: rows });
    }),
    http.get('/api/help/health', () => HttpResponse.json({ status: 'ok', findings: [] })),
  );
});
afterEach(() => {
  delete window.getCurrentProfileContext;
});

describe('downloads answers (video)', () => {
  it('registers on both video download page ids, with the base title', () => {
    expect(pageHelpFor('video-downloads').useAnswers).toBe(useVideoDownloadAnswers);
    const alias = pageHelpFor('video-active-downloads');
    expect(alias.useAnswers).toBe(useVideoDownloadAnswers);
    expect(alias.title).toBe(pageHelpFor('video-downloads').title);
    expect(alias.title).not.toBe('');
  });

  it('a clean list says nothing', async () => {
    rows = [DONE];
    const { container } = renderAnswers(useVideoDownloadAnswers);
    await waitFor(() => expect(container.querySelector('[data-testid=answers]')).toBeTruthy());
    await new Promise((r) => setTimeout(r, 30));
    expect(titles(container)).toEqual([]);
  });

  it('a slot wait names the uploader, slots and queue', async () => {
    rows = [DUNE];
    const { container } = renderAnswers(useVideoDownloadAnswers);
    await waitFor(() => expect(titles(container)).toHaveLength(1));
    expect(titles(container)[0].replace(/\s+/g, ' ')).toBe('Why is Dune: Part Two (2024) waiting?');
    expect(answer(container, 'queued').textContent).toContain(
      "It's queued on the uploader's side, not in SoulSync. reelbox has no free upload slots and 12 people ahead of you.",
    );
  });

  it('a transfer with no progress for 10 minutes is stuck; backups and Cancel', async () => {
    rows = [SHOGUN];
    const cancelled: unknown[] = [];
    server.use(
      http.post('/api/video/downloads/cancel', async ({ request }) => {
        cancelled.push(await request.json());
        return HttpResponse.json({ ok: true });
      }),
    );
    const { container } = renderAnswers(useVideoDownloadAnswers);
    await waitFor(() => expect(titles(container)).toEqual(['Why is Shogun · S01E03 stuck?']));
    const a = answer(container, 'stuck');
    expect(a.textContent).toContain("It hasn't moved in a while (last progress 25 min ago).");
    expect(a.textContent).toContain('2 backup releases are lined up');
    fireEvent.click(within(a).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(cancelled).toEqual([{ id: 12 }]));
  });

  it('moving within the last 10 minutes is not stuck', () => {
    const now = Date.now();
    const fresh = { ...SHOGUN, progress_at: utc(now - VIDEO_STALL_MS + 60_000) };
    expect(
      videoDownloadAnswers({ downloads: [fresh], now }, { page: 'p', isAdmin: true }, () => {}),
    ).toEqual([]);
  });

  it('a failure says why, its tries and backups, and blocks and retries like the page', async () => {
    rows = [ARRIVAL];
    const calls: string[] = [];
    server.use(
      http.post('/api/video/downloads/blocklist', async ({ request }) => {
        calls.push(`block ${JSON.stringify(await request.json())}`);
        return HttpResponse.json({ ok: true });
      }),
      http.post('/api/video/downloads/retry', async ({ request }) => {
        calls.push(`retry ${JSON.stringify(await request.json())}`);
        return HttpResponse.json({ ok: true });
      }),
    );
    window.showToast = vi.fn();
    const { container } = renderAnswers(useVideoDownloadAnswers);
    await waitFor(() =>
      expect(titles(container)[0].replace(/\s+/g, ' ')).toBe('Why did Arrival (2016) fail?'),
    );
    const a = answer(container, 'failed');
    expect(a.textContent).toContain('The uploader went offline. It was tried 2 times.');
    expect(a.textContent).toContain('1 backup release is still lined up.');
    fireEvent.click(within(a).getByRole('button', { name: 'Block and retry' }));
    await waitFor(() =>
      expect(calls).toEqual(['block {"download_id":13}', 'retry {"id":13,"next":true}']),
    );
    expect(within(a).getByRole('button', { name: 'Retry' })).toBeTruthy();
  });

  it('an import failure points at Manual import', async () => {
    rows = [HEAT];
    const nav = vi.fn();
    document.addEventListener('soulsync:video-navigate', nav);
    document.body.setAttribute('data-side', 'video');
    const { container } = renderAnswers(useVideoDownloadAnswers);
    await waitFor(() => expect(titles(container)).toEqual(["Why didn't Heat import?"]));
    const a = answer(container, 'import-failed');
    expect(a.textContent).toContain('No video file in the download folder.');
    fireEvent.click(within(a).getByRole('button', { name: 'Manual import' }));
    expect(nav).toHaveBeenCalled();
    expect((nav.mock.calls[0][0] as CustomEvent).detail).toBe('video-import');
    document.removeEventListener('soulsync:video-navigate', nav);
    document.body.removeAttribute('data-side');
  });

  it('a copy under the cutoff is still being watched', async () => {
    rows = [ALIEN];
    const { container } = renderAnswers(useVideoDownloadAnswers);
    await waitFor(() => expect(titles(container)).toEqual(['Why is Alien still being watched?']));
    expect(answer(container, 'upgrade').textContent).toContain('below your quality cutoff');
  });

  it('puts a source Health problem first', async () => {
    rows = [];
    server.use(
      http.get('/api/help/health', () =>
        HttpResponse.json({
          status: 'error',
          findings: [
            {
              id: 'video.indexers',
              severity: 'error',
              area: 'video',
              title: 'Indexers needs a look',
              detail: 'No indexer answered the last search',
              fix: [
                {
                  label: 'Open Downloads settings',
                  kind: 'settings',
                  tab: 'downloads',
                  media: 'video',
                },
              ],
            },
            {
              id: 'video.movies_space',
              severity: 'warning',
              area: 'video',
              title: 'Movies drive is nearly full',
            },
          ],
        }),
      ),
    );
    const { container } = renderAnswers(useVideoDownloadAnswers);
    await waitFor(() => expect(titles(container)).toEqual(['Indexers needs a look']));
  });

  it('a member gets the why, no titles and no buttons', async () => {
    asProfile(4, false);
    rows = [DUNE, ARRIVAL, HEAT];
    const { container } = renderAnswers(useVideoDownloadAnswers, { isAdmin: false });
    await waitFor(() =>
      expect(titles(container)).toEqual([
        'Why is a download waiting?',
        'Why did a download fail?',
        "Why didn't a download import?",
      ]),
    );
    expect(container.textContent).not.toMatch(/Dune|Arrival|Heat|reelbox/);
    expect(container.querySelectorAll('button')).toHaveLength(0);
  });
});

describe('one poll, not two', () => {
  it('with the video view mounted, the answers read its poll and send none; without it, their own', async () => {
    const detach = videoDownloadsFeed.attach(vi.fn());
    videoDownloadsFeed.publish({ downloads: [DUNE] });
    rows = [DONE];
    const { container } = renderAnswers(useVideoDownloadAnswers);
    await waitFor(() => expect(titles(container)).toHaveLength(1));
    await new Promise((r) => setTimeout(r, 50));
    expect(polls).toBe(0);
    act(() => detach());
    await waitFor(() => expect(polls).toBeGreaterThan(0));
    await waitFor(() => expect(titles(container)).toEqual([]));
  });
});
