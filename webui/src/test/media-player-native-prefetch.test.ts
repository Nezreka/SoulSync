import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { extractFunction } from './vanilla-extract';

const source = readFileSync(resolve(process.cwd(), 'static/media-player.js'), 'utf8');
const functions = [
  'npQueueIdentity',
  'npPrepareQueueTrack',
  'npPrepareQueueTracks',
  'npQueueTrackNeedsDownload',
  'npQueueStatusLabel',
  'npApplyQueuePrefetchState',
  'npPrefetchMissingQueueTracks',
  'npEnsureQueueTrackReady',
  'npPollQueuePrefetch',
  'npStopQueuePrefetchPolling',
  'startAudioPlayback',
  'playQueueItem',
  'playTrackList',
]
  .map((name) => extractFunction(name, source))
  .join('\n');

type QueueRow = Record<string, unknown>;
interface PlayerBridge {
  playList: (tracks: QueueRow[]) => Promise<{ status: string }>;
  play: (index: number) => Promise<{ status: string }>;
  prefetch: () => Promise<void>;
  poll: () => Promise<void>;
  identity: (track: QueueRow) => string;
  setQueue: (tracks: QueueRow[]) => void;
  rows: () => QueueRow[];
  setAuto: (value: boolean) => void;
  setBatch: (id: string) => void;
  batches: () => number;
}

function harness(auto = false) {
  const audio = Object.assign(new EventTarget(), {
    paused: true,
    readyState: 3,
    currentTime: 0,
    src: '',
    volume: 1,
    pause: vi.fn(),
    load: vi.fn(),
    play: vi.fn(async () => {}),
  });
  const toast = vi.fn();
  const fetch = vi.fn(async (url: string, options?: { body?: string }) => {
    if (url === '/api/playback/queue/prefetch') {
      const { tracks } = JSON.parse(options?.body ?? '{}');
      return new Response(
        JSON.stringify({
          success: true,
          queued: 0,
          batch_ids: [],
          items: tracks.map((track: Record<string, unknown>) => ({
            request_ids: [track._queue_request_id],
            state: 'ready',
            final_path: '/tmp/ready.flac',
            lib2_track_id: track.lib2_track_id,
            lib2_album_id: track.lib2_album_id,
            quality_profile_id: 42,
            release_edition_id: 8,
            profile_id: 7,
            library_owner_id: 7,
          })),
        }),
      );
    }
    return new Response(JSON.stringify({ success: true }));
  });
  const globals = source.slice(
    source.indexOf('let npLoadingQueueItem = false;'),
    source.indexOf('\n};', source.indexOf('window.cancelPendingPlayback =')) + 3,
  );
  const prefetchGlobals = source.slice(
    source.indexOf('let npAutoDownloadQueue = false;'),
    source.indexOf('function npQueueIdentity('),
  );
  // oxlint-disable-next-line typescript-eslint/no-implied-eval -- Exercise the real legacy functions.
  const bridge = new Function(
    'audioPlayer',
    'fetch',
    'showToast',
    'window',
    'document',
    `
    ${globals}
    let npQueue = [], npQueueIndex = -1, npRepeatMode = 'off', npRadioMode = false;
    ${prefetchGlobals}
    const npCancelCrossfade = () => {}, setTrackInfo = () => {}, showLoadingAnimation = () => {}, hideLoadingAnimation = () => {}, renderNpQueue = () => {}, updateNpPrevNextButtons = () => {}, setPlayingState = () => {}, clearTrack = () => {}, npSetPlayContext = () => {}, npScheduleQueuePrefetch = () => {}, npStartQueuePrefetchPolling = () => {};
    const clearQueue = () => { npQueue = []; };
    const stopStream = async () => {};
    ${functions}
    return { playList: playTrackList, play: playQueueItem, prefetch: npPrefetchMissingQueueTracks,
      poll: npPollQueuePrefetch, identity: npQueueIdentity,
      setQueue: tracks => { npQueue = npPrepareQueueTracks(tracks); },
      rows: () => npQueue, setAuto: value => { npAutoDownloadQueue = value; },
      setBatch: id => npQueuePrefetchBatchIds.add(id), batches: () => npQueuePrefetchBatchIds.size };
  `,
  )(audio, fetch, toast, {}, document) as PlayerBridge;
  bridge.setAuto(auto);
  return { ...bridge, fetch, audio, toast };
}

const missing = {
  id: null,
  lib2_track_id: 2,
  lib2_album_id: 5,
  lib2_artist_id: 7,
  title: 'Missing',
  artist: 'A',
  album: 'Album',
  file_path: '',
  is_library: false,
};
const owned = {
  ...missing,
  lib2_track_id: 1,
  title: 'Owned',
  file_path: '/tmp/owned.flac',
  is_library: true,
};

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('real native queue player functions', () => {
  it('defaults off, reports a missing row, and advances to owned audio without acquisition', async () => {
    vi.useFakeTimers();
    vi.spyOn(console, 'error').mockImplementation(() => {});
    const h = harness();
    expect((await h.playList([missing, owned])).status).toBe('skipped');
    expect(h.toast).toHaveBeenCalledWith(
      expect.stringContaining('Auto-download is disabled'),
      'error',
    );
    await vi.runAllTimersAsync();
    expect(h.audio.play).toHaveBeenCalledOnce();
    expect(h.fetch.mock.calls.map((call) => call[0])).toEqual(['/api/library/play']);
  });

  it('uses the existing prefetch then plays the returned path with typed native identity', async () => {
    const h = harness(true);
    expect((await h.playList([missing, owned])).status).toBe('played');
    const body = JSON.parse(h.fetch.mock.calls[0][1]?.body ?? '{}');
    expect(body.tracks).toHaveLength(1);
    expect(body.tracks[0]).toMatchObject({ lib2_track_id: 2, lib2_album_id: 5 });
    expect(h.rows()[0]).toMatchObject({
      quality_profile_id: 42,
      release_edition_id: 8,
      lib2_track_id: 2,
      lib2_album_id: 5,
      id: null,
      library_owner_id: 7,
    });
    const playBody = JSON.parse(h.fetch.mock.calls[1][1]?.body ?? '{}');
    expect(playBody).toMatchObject({
      track_id: null,
      lib2_track_id: 2,
      file_path: '/tmp/ready.flac',
    });
  });

  it('distinguishes native tracks sharing the same displayed metadata', () => {
    const h = harness();
    h.setQueue([missing, { ...missing, lib2_track_id: 3 }]);
    expect(h.rows()[0]._queue_request_id).not.toBe(h.rows()[1]._queue_request_id);
  });

  it('prefetches a bounded upcoming set from a long queue', async () => {
    const h = harness(true);
    h.setQueue(
      Array.from({ length: 300 }, (_, index) => ({ ...missing, lib2_track_id: index + 1 })),
    );
    await h.prefetch();
    const body = JSON.parse(h.fetch.mock.calls[0][1]?.body ?? '{}');
    expect(body.tracks).toHaveLength(25);
    expect(h.rows()[25].playback_status).toBe('missing');
  });

  it('keeps native identity and server context when a download completes through polling', async () => {
    const h = harness(true);
    h.setQueue([missing]);
    const requestId = h.rows()[0]._queue_request_id;
    h.setBatch('batch-1');
    h.fetch.mockImplementation(
      async () =>
        new Response(
          JSON.stringify({
            success: true,
            batches: {
              'batch-1': {
                phase: 'complete',
                tasks: [
                  {
                    status: 'completed',
                    final_file_path: '/tmp/completed.flac',
                    track_info: {
                      _queue_request_ids: [requestId],
                      lib2_track_id: 2,
                      lib2_album_id: 5,
                      release_edition_id: 8,
                      quality_profile_id: 42,
                      profile_id: 7,
                      library_owner_id: 7,
                    },
                  },
                ],
              },
            },
          }),
        ),
    );
    await h.poll();
    expect(h.rows()[0]).toMatchObject({
      file_path: '/tmp/completed.flac',
      is_library: true,
      lib2_track_id: 2,
      lib2_album_id: 5,
      quality_profile_id: 42,
      library_owner_id: 7,
      id: null,
    });
  });

  it('stops polling a batch the server no longer reports, e.g. after a library switch', async () => {
    const h = harness(true);
    h.setQueue([missing]);
    h.setBatch('batch-1');
    h.fetch.mockImplementation(
      async () => new Response(JSON.stringify({ success: true, batches: {} })),
    );
    await h.poll();
    expect(h.batches()).toBe(0);
  });
});
