import { afterEach, describe, expect, it, vi } from 'vitest';

import type { EnhancedAlbum } from './-artist-detail.enhanced';
import type { RedownloadCandidate } from './-artist-detail.redownload';

import {
  bestCandidateIndex,
  msClock,
  pollRedownloadProgress,
  redownloadAlbumFlow,
  scoreClass,
  stopRedownloadProgress,
  streamRedownloadSources,
  trackFormatBadge,
} from './-artist-detail.redownload';

/**
 * The redownload layer: the pure label helpers, the NDJSON source stream, the
 * 1.5s progress poller, and the #911 canonical-source album flow.
 */

afterEach(() => {
  stopRedownloadProgress();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  delete window.showToast;
  delete window.openDownloadMissingModalForArtistAlbum;
  delete window.registerArtistDownload;
});

describe('pure helpers', () => {
  it('badges only known audio extensions', () => {
    expect(trackFormatBadge('/music/a.flac')).toBe('FLAC');
    expect(trackFormatBadge('/music/a.mp3')).toBe('MP3');
    expect(trackFormatBadge('/music/a.txt')).toBe('');
    expect(trackFormatBadge(undefined)).toBe('');
  });

  it('bands scores at 90/70 and formats m:ss', () => {
    expect(scoreClass(95)).toBe('high');
    expect(scoreClass(70)).toBe('medium');
    expect(scoreClass(69)).toBe('low');
    expect(msClock(83_000)).toBe('1:23');
    expect(msClock(0)).toBe('');
    expect(msClock(undefined)).toBe('');
  });

  it('the best candidate is the highest-confidence NON-blacklisted one', () => {
    const candidates = [
      { confidence: 0.99, blacklisted: true, _globalIdx: 0 },
      { confidence: 0.7, _globalIdx: 1 },
      { confidence: 0.9, _globalIdx: 2 },
    ] as RedownloadCandidate[];
    expect(bestCandidateIndex(candidates)).toBe(2);
    expect(bestCandidateIndex([])).toBe(-1);
  });
});

describe('the source stream', () => {
  function ndjsonResponse(chunks: string[]) {
    const encoder = new TextEncoder();
    let i = 0;
    const body = new ReadableStream({
      pull(controller) {
        if (i < chunks.length) controller.enqueue(encoder.encode(chunks[i++]));
        else controller.close();
      },
    });
    return new Response(body);
  }

  it('parses per-source lines, assigns global indices, skips junk', async () => {
    // The second line is split across chunks to exercise the buffer stitch.
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        ndjsonResponse([
          '{"source":"soulseek","candidates":[{"display_name":"a"},{"display_name":"b"}]}\n',
          'not json\n{"source":"tidal","candi',
          'dates":[{"display_name":"c"}]}\n{"done":true}\n',
        ]),
      ),
    );
    const seen: [string, number][] = [];
    const all = await streamRedownloadSources(9, {}, (source, candidates) => {
      seen.push([source, candidates.length]);
    });
    expect(seen).toEqual([
      ['soulseek', 2],
      ['tidal', 1],
    ]);
    expect(all.map((c) => c._globalIdx)).toEqual([0, 1, 2]);
    expect(all[2].display_name).toBe('c');
  });
});

describe('the progress poller', () => {
  it('ticks transfer progress, then completes when the batch leaves', async () => {
    vi.useFakeTimers();
    let batchGone = false;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === '/api/downloads/status') {
          return new Response(
            JSON.stringify({
              transfers: batchGone
                ? []
                : [
                    {
                      state: 'InProgress',
                      percentComplete: 40,
                      bytesTransferred: 1048576,
                      size: 2097152,
                    },
                  ],
            }),
          );
        }
        return new Response(
          JSON.stringify({
            active_processes: batchGone ? [] : [{ batch_id: 'redownload_batch_1' }],
          }),
        );
      }),
    );
    window.showToast = vi.fn() as never;
    const ticks: { pct: number; text: string }[] = [];
    const onComplete = vi.fn();
    pollRedownloadProgress({ onTick: (p) => ticks.push(p), onComplete, onTimeout: vi.fn() });

    await vi.advanceTimersByTimeAsync(1500);
    expect(ticks[0]).toEqual({ pct: 40, text: 'Downloading... 40% (1.0 / 2.0 MB)' });
    expect(onComplete).not.toHaveBeenCalled();

    batchGone = true;
    await vi.advanceTimersByTimeAsync(1500);
    expect(ticks.at(-1)).toEqual({ pct: 100, text: 'Complete! File replaced successfully.' });
    expect(window.showToast).toHaveBeenCalledWith('Track redownloaded successfully', 'success');
    expect(onComplete).toHaveBeenCalledTimes(1);

    // Completed: no further polls.
    const calls = (fetch as ReturnType<typeof vi.fn>).mock.calls.length;
    await vi.advanceTimersByTimeAsync(10_000);
    expect((fetch as ReturnType<typeof vi.fn>).mock.calls.length).toBe(calls);
  });

  it('gives up after five minutes with the dashboard hint', async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async (input: RequestInfo | URL) =>
          new Response(
            JSON.stringify(
              String(input) === '/api/downloads/status'
                ? { transfers: [] }
                : { active_processes: [{ batch_id: 'redownload_batch_1' }] },
            ),
          ),
      ),
    );
    const onTimeout = vi.fn();
    pollRedownloadProgress({ onTick: vi.fn(), onComplete: vi.fn(), onTimeout });
    await vi.advanceTimersByTimeAsync(300_000);
    expect(onTimeout).toHaveBeenCalledTimes(1);
  });
});

describe('redownloadAlbumFlow (#911)', () => {
  it('pulls the canonical edition and hands off to the shared modal', async () => {
    const fetchSpy = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) =>
        new Response(
          JSON.stringify({
            success: true,
            album: { id: 'dz7', name: 'Selected Ambient Works' },
            tracks: [
              { id: 't1', name: 'Xtal' },
              { id: 't2', name: 'Tha' },
            ],
          }),
        ),
    );
    vi.stubGlobal('fetch', fetchSpy);
    const openModal = vi.fn();
    const register = vi.fn();
    window.openDownloadMissingModalForArtistAlbum = openModal as never;
    window.registerArtistDownload = register as never;

    // deezer_id only — canonical resolution must pick deezer, not Spotify.
    await redownloadAlbumFlow(
      { id: 7, title: 'Selected Ambient Works', deezer_id: 'dz7' } as EnhancedAlbum,
      'Aphex Twin',
    );

    const url = String(fetchSpy.mock.calls[0]?.[0]);
    expect(url).toContain('/api/album/dz7/tracks');
    expect(url).toContain('source=deezer');
    expect(openModal).toHaveBeenCalledTimes(1);
    const args = openModal.mock.calls[0];
    expect(args?.[0]).toBe('library_redownload_dz7');
    expect(args?.[1]).toBe('[Aphex Twin] Selected Ambient Works');
    // Every track carries the album context the modal needs.
    expect((args?.[2] as { album: { name: string } }[])[0].album.name).toBe(
      'Selected Ambient Works',
    );
    expect(register).toHaveBeenCalledWith(
      expect.objectContaining({ name: 'Aphex Twin' }),
      expect.objectContaining({ id: 'dz7' }),
      'library_redownload_dz7',
      'album',
    );
  });

  it('refuses outright when there is nothing to identify the album by', async () => {
    const fetchSpy = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) => new Response('{}'),
    );
    vi.stubGlobal('fetch', fetchSpy);
    window.showToast = vi.fn() as never;
    await redownloadAlbumFlow({ id: 7, title: '' } as EnhancedAlbum, '');
    expect(window.showToast).toHaveBeenCalledWith(
      'No album ID or name available for redownload',
      'warning',
    );
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
