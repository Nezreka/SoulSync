import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  _resetReorganizePolling,
  classifyPreviewTrack,
  classifyReorganizeOutcome,
  formatReorganizeResultMessage,
  queueReorganizeAllRequest,
  queueReorganizeRequest,
  refreshReorganizeQueue,
  reorganizeStateForAlbum,
  reorgDisplayLabel,
  startReorganizeQueuePolling,
  stopReorganizeQueuePolling,
  summarizeReorganizePreview,
} from './-artist-detail.reorganize';

/**
 * The reorganize layer. Pins the preview classification table (6024-6078),
 * the #377 outcome split, the queued-toast wording, and the poll controller's
 * fast/slow cadence + the debounced view reload after a completion.
 */

afterEach(() => {
  _resetReorganizePolling();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe('preview row classification', () => {
  it('classifies each row state with the vanilla precedence', () => {
    // Collision wins over everything.
    expect(
      classifyPreviewTrack({ file_exists: true, collision: true, new_path: 'x' }),
    ).toMatchObject({ rowClass: 'reorganize-row-collision', arrow: '!!' });
    // Missing file: no new cell at all.
    expect(classifyPreviewTrack({ file_exists: false })).toMatchObject({
      rowClass: 'reorganize-row-missing',
      arrow: '⊘',
      newCell: { kind: 'none' },
      currentMissing: true,
    });
    // Unmatched: reason with a default.
    expect(classifyPreviewTrack({ file_exists: true, matched: false })).toMatchObject({
      newCell: { kind: 'reason', text: 'The library cannot name this track' },
    });
    // Matched but no path computed.
    expect(classifyPreviewTrack({ file_exists: true, matched: true })).toMatchObject({
      rowClass: 'reorganize-row-missing',
      newCell: { kind: 'reason', text: "Couldn't compute destination path" },
    });
    expect(
      classifyPreviewTrack({ file_exists: true, unchanged: true, new_path: 'x' }),
    ).toMatchObject({ rowClass: 'reorganize-row-unchanged', arrow: '=' });
    expect(classifyPreviewTrack({ file_exists: true, new_path: '/n.flac' })).toMatchObject({
      rowClass: 'reorganize-row-changed',
      arrow: '→',
      newCell: { kind: 'path', text: '/n.flac', collision: false },
    });
  });

  it('summarizes counts into the chip row and gates apply on collisions', () => {
    const tracks = [
      { file_exists: true, new_path: '/a' }, // will move
      { file_exists: true, unchanged: true, new_path: '/b' }, // unchanged
      { file_exists: false }, // missing on disk
      { file_exists: true, matched: false }, // the library cannot name it
      { file_exists: true, matched: true }, // no destination
    ];
    const summary = summarizeReorganizePreview(tracks);
    expect(summary.chips.map((c) => c.text)).toEqual([
      '1 will move',
      '1 unchanged',
      '1 the library cannot name',
      "1 couldn't compute destination",
      '1 missing on disk',
    ]);
    expect(summary.canApply).toBe(true);

    const colliding = summarizeReorganizePreview([
      ...tracks,
      { file_exists: true, collision: true, new_path: '/c' },
    ]);
    expect(colliding.chips.at(-1)?.text).toBe('1 collision — likely a source data issue');
    expect(colliding.canApply).toBe(false);

    // Nothing movable → no apply, even with no collisions.
    expect(summarizeReorganizePreview([{ file_exists: true, unchanged: true }]).canApply).toBe(
      false,
    );
  });
});

describe('outcome classification (#377)', () => {
  it('non-completed statuses and failures are warnings, clean runs succeed', () => {
    expect(classifyReorganizeOutcome({})).toBe('success');
    expect(classifyReorganizeOutcome({ result_status: 'completed' })).toBe('success');
    expect(classifyReorganizeOutcome({ result_status: 'no_source_id' })).toBe('warning');
    expect(classifyReorganizeOutcome({ failed: 1 })).toBe('warning');
  });

  it('formats each skip status and the moved/skipped/failed line', () => {
    expect(formatReorganizeResultMessage({ result_status: 'no_album' })).toBe(
      'Reorganize skipped — album not found in DB.',
    );
    expect(formatReorganizeResultMessage({ result_status: 'setup_failed' })).toBe(
      "Reorganize failed — couldn't compute destinations.",
    );
    expect(
      formatReorganizeResultMessage({
        moved: 3,
        skipped: 1,
        failed: 2,
        errors: [{ error: 'disk full' }],
      }),
    ).toBe('Reorganized: 3 moved, 1 skipped, 2 failed (disk full)');
    expect(formatReorganizeResultMessage({})).toBe('Reorganized: 0 moved');
  });
});

describe('queue requests', () => {
  function stubFetch(body: unknown) {
    const spy = vi.fn(
      async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify(body)),
    );
    vi.stubGlobal('fetch', spy);
    return spy;
  }

  it('single album: queued with position, already queued, and the fallback', async () => {
    const spy = stubFetch({ success: true, queued: true, position: 3 });
    expect(await queueReorganizeRequest(7, 'SAW 85-92')).toBe('Queued: SAW 85-92 (#3 in queue)');
    // No body at all: there is no source to pick and no mode to choose.
    expect(spy.mock.calls[0]?.[1]?.body).toBeUndefined();

    stubFetch({ success: true, queued: true, position: 1 });
    expect(await queueReorganizeRequest(7, 'SAW 85-92')).toBe('Queued: SAW 85-92');

    stubFetch({ success: true, reason: 'already_queued' });
    expect(await queueReorganizeRequest(7, 'SAW 85-92')).toBe('Already queued: SAW 85-92');
  });

  it('reorganize-all: the four toast combos', async () => {
    stubFetch({ success: true, enqueued: 3, already_queued: 2 });
    expect(await queueReorganizeAllRequest(42, 'Aphex Twin')).toEqual({
      message: 'Queued 3 albums; 2 already in queue',
      tone: 'info',
    });
    stubFetch({ success: true, enqueued: 1 });
    expect((await queueReorganizeAllRequest(42, 'Aphex Twin')).message).toBe(
      'Queued 1 album for Aphex Twin',
    );
    stubFetch({ success: true, already_queued: 4 });
    expect((await queueReorganizeAllRequest(42, 'Aphex Twin')).message).toBe(
      'All 4 albums already in queue',
    );
    stubFetch({ success: true });
    expect(await queueReorganizeAllRequest(42, 'Aphex Twin')).toEqual({
      message: 'No albums to queue',
      tone: 'warning',
    });
  });
});

describe('the queue poll controller', () => {
  const IDLE = { active: null, queued: [], recent: [] };

  function stubQueue(bodies: unknown[]) {
    let i = 0;
    const spy = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => {
      const body = bodies[Math.min(i, bodies.length - 1)];
      i += 1;
      return new Response(JSON.stringify(body));
    });
    vi.stubGlobal('fetch', spy);
    return spy;
  }

  beforeEach(() => vi.useFakeTimers());

  it('polls fast while work is in flight and slow when idle', async () => {
    const spy = stubQueue([
      { active: { album_id: 7, queue_id: 'q1', artist_id: 42 }, queued: [], recent: [] },
      IDLE,
      IDLE,
    ]);
    const snapshots: unknown[] = [];
    startReorganizeQueuePolling('42', { onSnapshot: (s) => snapshots.push(s), onReload: vi.fn() });
    await vi.advanceTimersByTimeAsync(0);
    expect(spy).toHaveBeenCalledTimes(1);
    expect(reorganizeStateForAlbum(7)).toBe('running');

    // Active → next tick lands at the FAST cadence (1.5s).
    await vi.advanceTimersByTimeAsync(1500);
    expect(spy).toHaveBeenCalledTimes(2);
    expect(reorganizeStateForAlbum(7)).toBeNull();

    // Idle → the next tick waits the SLOW 8s; nothing at 1.5s.
    await vi.advanceTimersByTimeAsync(1500);
    expect(spy).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(6500);
    expect(spy).toHaveBeenCalledTimes(3);
    expect(snapshots).toHaveLength(3);
  });

  it('keeps the last snapshot through a network blip', async () => {
    let fail = false;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => {
        if (fail) throw new Error('offline');
        return new Response(
          JSON.stringify({ active: null, queued: [{ album_id: 9, queue_id: 'q9' }], recent: [] }),
        );
      }),
    );
    startReorganizeQueuePolling('42', { onSnapshot: vi.fn(), onReload: vi.fn() });
    await vi.advanceTimersByTimeAsync(0);
    expect(reorganizeStateForAlbum(9)).toBe('queued');
    fail = true;
    await vi.advanceTimersByTimeAsync(1500);
    expect(reorganizeStateForAlbum(9)).toBe('queued');
  });

  it('cross-artist items get the "(other artist)" label', async () => {
    stubQueue([IDLE]);
    startReorganizeQueuePolling('42', { onSnapshot: vi.fn(), onReload: vi.fn() });
    await vi.advanceTimersByTimeAsync(0);
    expect(reorgDisplayLabel({ album_title: 'X', artist_id: 42 })).toBe('X');
    expect(
      reorgDisplayLabel({ album_title: 'X', artist_id: 99, artist_name: 'Squarepusher' }),
    ).toBe('X (Squarepusher)');
  });

  it('reloads once, debounced, when OUR artist finishes and the queue idles', async () => {
    const now = () => Date.now() / 1000;
    const spy = stubQueue([
      // Our album running.
      { active: { album_id: 7, queue_id: 'q1', artist_id: 42 }, queued: [], recent: [] },
      // Just finished, another of ours still queued → hold the reload.
      {
        active: null,
        queued: [{ album_id: 8, artist_id: 42, queue_id: 'q2' }],
        recent: [{ queue_id: 'q1', artist_id: 42, finished_at: now(), status: 'done' }],
      },
      // The queued one runs.
      {
        active: { album_id: 8, queue_id: 'q2', artist_id: 42 },
        queued: [],
        recent: [{ queue_id: 'q1', artist_id: 42, finished_at: now(), status: 'done' }],
      },
      // Fully idle for our artist → the debounced reload arms.
      {
        active: null,
        queued: [],
        recent: [{ queue_id: 'q2', artist_id: 42, finished_at: now(), status: 'done' }],
      },
    ]);
    const onReload = vi.fn();
    startReorganizeQueuePolling('42', { onSnapshot: vi.fn(), onReload });
    await vi.advanceTimersByTimeAsync(0); // running
    await vi.advanceTimersByTimeAsync(1500); // finished + queued → pending, held
    await vi.advanceTimersByTimeAsync(1500); // next item running → still held
    expect(onReload).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1500); // idle → 1.5s debounce arms
    expect(onReload).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1500);
    expect(onReload).toHaveBeenCalledTimes(1);
    expect(spy.mock.calls.length).toBeGreaterThanOrEqual(4);
  });

  it('a cross-artist completion never triggers a reload', async () => {
    const onReload = vi.fn();
    stubQueue([
      { active: { album_id: 7, queue_id: 'q1', artist_id: 99 }, queued: [], recent: [] },
      {
        active: null,
        queued: [],
        recent: [{ queue_id: 'q1', artist_id: 99, finished_at: Date.now() / 1000, status: 'done' }],
      },
    ]);
    startReorganizeQueuePolling('42', { onSnapshot: vi.fn(), onReload });
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(1500);
    await vi.advanceTimersByTimeAsync(10_000);
    expect(onReload).not.toHaveBeenCalled();
  });

  it('stopping cancels the pending tick', async () => {
    const spy = stubQueue([IDLE]);
    startReorganizeQueuePolling('42', { onSnapshot: vi.fn(), onReload: vi.fn() });
    await vi.advanceTimersByTimeAsync(0);
    stopReorganizeQueuePolling();
    await vi.advanceTimersByTimeAsync(20_000);
    expect(spy).toHaveBeenCalledTimes(1);
    // And the wake no-ops once stopped.
    await refreshReorganizeQueue();
    expect(spy).toHaveBeenCalledTimes(1);
  });
});
