import { afterEach, describe, expect, it, vi } from 'vitest';

import type { EnhancedAlbum } from './-artist-detail.enhanced';
import type { RedownloadCandidate } from './-artist-detail.redownload';

import { decisionPill, rejectionSummary } from '../../features/downloads/decisions';
import {
  bestCandidateIndex,
  msClock,
  overrideBlockedReason,
  overrideConfirm,
  startRedownloadRequest,
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

describe('rejected candidates', () => {
  const rej = (
    code: string,
    stage: string,
    detail = '',
    extra: Partial<RedownloadCandidate> = {},
  ) =>
    ({
      _globalIdx: -1,
      decision: { accepted: false, code, stage, detail, score: 0.4 },
      ...extra,
    }) as RedownloadCandidate;

  it('keeps rejected rows out of the selectable list', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        async () =>
          new Response(
            `${JSON.stringify({
              source: 'soulseek',
              candidates: [{ display_name: 'ok', confidence: 0.9 }],
              rejected: [{ display_name: 'live', confidence: 0.99 }],
              rejected_total: 7,
              rejected_counts: { version_conflict: 5, match_weak: 2 },
            })}\n{"done":true}\n`,
          ),
      ),
    );
    let summary: {
      total: number;
      rows: RedownloadCandidate[];
      counts: Record<string, number>;
    } | null = null;
    const all = await streamRedownloadSources(1, {}, (_source, _fresh, _all, rejected) => {
      summary = rejected;
    });
    expect(all.map((c) => c.display_name)).toEqual(['ok']);
    // A rejected row can outscore everything and still never be the pick.
    expect(bestCandidateIndex(all)).toBe(0);
    expect(summary!.total).toBe(7);
    expect(summary!.rows[0]._globalIdx).toBe(-1);
    expect(rejectionSummary(summary!.counts)).toBe('5 wrong version · 2 weak match');
  });

  it('an old-shape line with no rejected fields still parses', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('{"source":"tidal","candidates":[]}\n')),
    );
    let total = -1;
    await streamRedownloadSources(1, {}, (_s, _f, _a, rejected) => {
      total = rejected.total;
    });
    expect(total).toBe(0);
  });

  it('turns codes into short pills', () => {
    expect(
      decisionPill(
        rej(
          'version_conflict',
          'version',
          'live version, asked for the original (match 0.58 before the penalty)',
        ),
      ),
    ).toBe('version: live');
    expect(
      decisionPill(
        rej('version_conflict', 'version', "asked for the live version, this isn't marked live"),
      ),
    ).toBe('not live');
    expect(
      decisionPill(rej('duration_mismatch', 'duration', '', { duration: 30_000 }), 238_000),
    ).toBe('too short');
    expect(
      decisionPill(rej('duration_mismatch', 'duration', '', { duration: 400_000 }), 238_000),
    ).toBe('too long');
    expect(decisionPill(rej('below_profile', 'quality'))).toBe('below your profile');
    expect(decisionPill(rej('some_future_code', 'policy'))).toBe('some future code');
    expect(decisionPill({})).toBe('');
  });

  it('refuses overrides the pipeline would undo anyway', () => {
    expect(overrideBlockedReason(rej('preview', 'preview'))).toMatch(/preview clips/);
    expect(overrideBlockedReason(rej('duration_mismatch', 'duration'))).toMatch(/length/);
    expect(overrideBlockedReason(rej('blacklisted', 'policy'))).toMatch(/blacklist/);
    expect(overrideBlockedReason(rej('match_weak', 'identity'))).toBeNull();
    expect(overrideBlockedReason(rej('below_profile', 'quality'))).toBeNull();
  });

  it('warns harder about identity than quality', () => {
    const want = { name: 'Fade Into You', artist: 'Mazzy Star' };
    const identity = overrideConfirm(
      rej('artist_mismatch', 'identity', 'Hope Sandoval vs Mazzy Star'),
      want,
    );
    expect(identity.destructive).toBe(true);
    expect(identity.message).toContain('"Fade Into You" by Mazzy Star');
    expect(identity.message).toContain('AcoustID');
    const version = overrideConfirm(
      rej('version_conflict', 'version', 'live version, asked for the original'),
      want,
    );
    expect(version.destructive).toBe(true);
    const quality = overrideConfirm(
      rej('below_profile', 'quality', "MP3 320 doesn't meet the quality profile"),
      want,
    );
    expect(quality.destructive).toBe(false);
    expect(quality.message).toContain('quality check after download is skipped for this one file');
    for (const opts of [identity, version, quality]) {
      expect(opts.message).toContain('Your settings stay as they are');
    }
  });

  it('sends the overridden rule with the start request, and nothing for a normal pick', async () => {
    const fetchSpy = vi.fn(
      async () => new Response(JSON.stringify({ success: true, task_id: 't' })),
    );
    vi.stubGlobal('fetch', fetchSpy);
    await startRedownloadRequest(1, {}, rej('below_profile', 'quality'), true);
    await startRedownloadRequest(
      1,
      {},
      {
        _globalIdx: 0,
        decision: { accepted: true, code: 'accepted', stage: 'decision', detail: '', score: 1 },
      },
      true,
    );
    const bodies = fetchSpy.mock.calls.map((call) =>
      JSON.parse(String((call as unknown as [string, RequestInit])[1].body)),
    );
    expect(bodies[0].override).toEqual({ code: 'below_profile', stage: 'quality' });
    expect(bodies[1].override).toBeUndefined();
  });
});
