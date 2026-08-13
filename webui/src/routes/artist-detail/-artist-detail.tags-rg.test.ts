import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  _stopAllTagRgPollers,
  analyzeAlbumReplayGainRequest,
  analyzeTrackReplayGainRequest,
  batchWriteDoneMessage,
  fetchBatchTagPreview,
  fetchTagPreview,
  offersServerSync,
  pollBatchRgStatus,
  pollBatchWriteTagsStatus,
  serverSyncLabel,
  startBatchWriteTags,
  writeTagsRequest,
} from './-artist-detail.tags-rg';

/**
 * The tag-write + ReplayGain request layer. Pins the vanilla's exact toast
 * wording (executeWriteTags 5429, _pollBatchWriteTagsStatus 5672, ReplayGain
 * 5709-5834) and the poller cadences: 800ms first tick then 1s for the two
 * batch jobs, 1s then 1.2s for the album job.
 */

const toasts: [string, string][] = [];

/** Queue of JSON bodies; the last one repeats for any further calls. */
function stubFetchSequence(...bodies: unknown[]) {
  let i = 0;
  const spy = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => {
    const body = bodies[Math.min(i, bodies.length - 1)];
    i += 1;
    return new Response(JSON.stringify(body));
  });
  vi.stubGlobal('fetch', spy);
  return spy;
}

beforeEach(() => {
  toasts.length = 0;
  window.showToast = ((msg: string, tone: string) => toasts.push([msg, tone])) as never;
});

afterEach(() => {
  _stopAllTagRgPollers();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  delete window.showToast;
});

describe('single-track tag preview + write', () => {
  it('maps the preview payload and its failure shape', async () => {
    stubFetchSequence({
      success: true,
      diff: [{ field: 'title' }],
      has_changes: true,
      server_type: 'plex',
    });
    expect(await fetchTagPreview(9)).toEqual({
      diff: [{ field: 'title' }],
      hasChanges: true,
      serverType: 'plex',
    });
    stubFetchSequence({ success: false, error: 'no file' });
    expect(await fetchTagPreview(9)).toMatchObject({
      diff: [],
      hasChanges: false,
      error: 'no file',
    });
  });

  it('offers server sync for plex/jellyfin but never navidrome', () => {
    expect(offersServerSync('plex')).toBe(true);
    expect(offersServerSync('jellyfin')).toBe(true);
    expect(offersServerSync('navidrome')).toBe(false);
    expect(offersServerSync(null)).toBe(false);
    expect(serverSyncLabel('plex')).toBe('Sync to Plex');
    expect(serverSyncLabel('jellyfin')).toBe('Sync to Jellyfin');
  });

  it('shapes the write toast: field count, synced-to suffix, sync-failed suffix', async () => {
    const spy = stubFetchSequence({ success: true, written_fields: ['a', 'b', 'c'] });
    expect(await writeTagsRequest(9, true, false, null)).toBe(
      'Tags written successfully (3 fields)',
    );
    expect(JSON.parse(String(spy.mock.calls[0]?.[1]?.body))).toEqual({
      embed_cover: true,
      sync_to_server: false,
    });

    stubFetchSequence({ success: true, written_fields: [], server_sync: { synced: 2, failed: 0 } });
    expect(await writeTagsRequest(9, false, true, 'plex')).toBe(
      'Tags written successfully (0 fields) — synced to Plex',
    );

    stubFetchSequence({
      success: true,
      written_fields: ['a'],
      server_sync: { synced: 0, failed: 1 },
    });
    expect(await writeTagsRequest(9, false, true, 'jellyfin')).toBe(
      'Tags written successfully (1 fields) — server sync failed',
    );

    stubFetchSequence({ success: false, error: 'locked' });
    await expect(writeTagsRequest(9, true, false, null)).rejects.toThrow('locked');
  });
});

describe('batch preview + done-message shaping', () => {
  it('maps the batch preview payload and its failure shape', async () => {
    const spy = stubFetchSequence({
      success: true,
      tracks: [{ title: 'T' }],
      server_type: 'jellyfin',
    });
    expect(await fetchBatchTagPreview([1, 2])).toEqual({
      tracks: [{ title: 'T' }],
      serverType: 'jellyfin',
    });
    expect(JSON.parse(String(spy.mock.calls[0]?.[1]?.body))).toEqual({ track_ids: [1, 2] });
    stubFetchSequence({ success: false, error: 'nope' });
    expect(await fetchBatchTagPreview([1])).toMatchObject({ tracks: [], error: 'nope' });
  });

  it('builds every branch of the done toast (5672-5688)', () => {
    expect(batchWriteDoneMessage({ written: 5 })).toEqual({
      message: 'Tags written: 5 updated',
      tone: 'success',
    });
    expect(batchWriteDoneMessage({ written: 3, skipped: 2 }).message).toBe(
      'Tags written: 3 updated, 2 unchanged',
    );
    expect(
      batchWriteDoneMessage({
        written: 1,
        failed: 2,
        errors: [{ error: 'corrupt header' }, { error: 'other' }],
      }),
    ).toEqual({
      message: 'Tags written: 1 updated, 2 failed (corrupt header)',
      tone: 'warning',
    });
    expect(
      batchWriteDoneMessage({ written: 4, sync_phase: 'done', sync_server: 'plex', sync_synced: 4 })
        .message,
    ).toBe('Tags written: 4 updated — synced to Plex');
    expect(
      batchWriteDoneMessage({
        written: 4,
        sync_phase: 'done',
        sync_server: 'jellyfin',
        sync_synced: 3,
        sync_failed: 1,
      }),
    ).toEqual({
      message: 'Tags written: 4 updated — Jellyfin sync: 3 synced, 1 failed',
      tone: 'warning',
    });
  });
});

describe('the batch write poller', () => {
  it('starts the job, then polls at 800ms/1s into progress and done toasts', async () => {
    vi.useFakeTimers();
    const spy = stubFetchSequence(
      { success: true },
      {
        status: 'running',
        processed: 1,
        total: 4,
        current_track: 'Xtal',
      },
      { status: 'running', sync_phase: 'syncing', sync_server: 'plex' },
      { status: 'done', written: 4, skipped: 0, failed: 0 },
    );
    await startBatchWriteTags([1, 2, 3, 4], true, true);
    expect(toasts).toEqual([['Writing tags for 4 tracks...', 'info']]);
    expect(String(spy.mock.calls[0]?.[0])).toBe('/api/library/tracks/write-tags-batch');

    await vi.advanceTimersByTimeAsync(800);
    expect(String(spy.mock.calls[1]?.[0])).toBe('/api/library/tracks/write-tags-batch/status');
    expect(toasts[1]).toEqual(['Writing tags: 1/4 (25%) — Xtal', 'info']);

    await vi.advanceTimersByTimeAsync(1000);
    expect(toasts[2]).toEqual(['Syncing to Plex...', 'info']);

    await vi.advanceTimersByTimeAsync(1000);
    expect(toasts[3]).toEqual(['Tags written: 4 updated', 'success']);
    // Done: no further polls scheduled.
    await vi.advanceTimersByTimeAsync(5000);
    expect(spy.mock.calls).toHaveLength(4);
  });

  it('a refused start toasts the error and never polls', async () => {
    vi.useFakeTimers();
    const spy = stubFetchSequence({ success: false, error: 'another batch is running' });
    await startBatchWriteTags([1], false, false);
    expect(toasts).toEqual([['Failed to start tag write: another batch is running', 'error']]);
    await vi.advanceTimersByTimeAsync(5000);
    expect(spy.mock.calls).toHaveLength(1);
  });

  it('restarting the poller supersedes the pending tick instead of doubling it', async () => {
    vi.useFakeTimers();
    const spy = stubFetchSequence({ status: 'done', written: 1 });
    pollBatchWriteTagsStatus();
    pollBatchWriteTagsStatus();
    await vi.advanceTimersByTimeAsync(800);
    expect(spy.mock.calls).toHaveLength(1);
  });
});

describe('ReplayGain', () => {
  it('single track: success and failure toasts', async () => {
    stubFetchSequence({ success: true, track_gain: '-1.20 dB', lufs: -9.5 });
    await analyzeTrackReplayGainRequest(3);
    expect(toasts).toEqual([['ReplayGain written: -1.20 dB (-9.5 LUFS)', 'success']]);

    toasts.length = 0;
    stubFetchSequence({ success: false, error: 'no file' });
    await analyzeTrackReplayGainRequest(3);
    expect(toasts).toEqual([['ReplayGain failed: no file', 'error']]);
  });

  it('album job: polls at 1s/1.2s and re-enables the button through onDone', async () => {
    vi.useFakeTimers();
    const spy = stubFetchSequence(
      { success: true },
      { status: 'running', processed: 2, total: 8 },
      { status: 'done', analyzed: 8, failed: 0 },
    );
    const onDone = vi.fn();
    await analyzeAlbumReplayGainRequest(7, onDone);
    expect(toasts).toEqual([['Album ReplayGain analysis started…', 'info']]);

    await vi.advanceTimersByTimeAsync(1000);
    expect(String(spy.mock.calls[1]?.[0])).toBe('/api/library/album/7/analyze-replaygain/status');
    expect(toasts[1]).toEqual(['ReplayGain: 2/8 tracks (25%)', 'info']);
    expect(onDone).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(1200);
    expect(toasts[2]).toEqual(['ReplayGain done: 8 analyzed, 0 failed', 'success']);
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it('album job: a refused start calls onDone immediately', async () => {
    const onDone = vi.fn();
    stubFetchSequence({ success: false, error: 'no files' });
    await analyzeAlbumReplayGainRequest(7, onDone);
    expect(toasts).toEqual([['ReplayGain: no files', 'error']]);
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it('batch poller: 800ms first tick, warning tone when anything failed', async () => {
    vi.useFakeTimers();
    stubFetchSequence(
      { status: 'running', processed: 1, total: 2, current_track: 'Xtal' },
      { status: 'done', analyzed: 1, failed: 1 },
    );
    pollBatchRgStatus();
    await vi.advanceTimersByTimeAsync(800);
    expect(toasts[0]).toEqual(['ReplayGain: 1/2 (50%) — Xtal', 'info']);
    await vi.advanceTimersByTimeAsync(1000);
    expect(toasts[1]).toEqual(['ReplayGain done: 1 written, 1 failed', 'warning']);
  });

  it('_stopAllTagRgPollers cancels a pending tick', async () => {
    vi.useFakeTimers();
    const spy = stubFetchSequence({ status: 'done', analyzed: 1, failed: 0 });
    pollBatchRgStatus();
    _stopAllTagRgPollers();
    await vi.advanceTimersByTimeAsync(5000);
    expect(spy).not.toHaveBeenCalled();
  });
});
