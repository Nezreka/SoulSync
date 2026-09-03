import { describe, expect, it } from 'vitest';

import type { AdlBatch, AdlDownload } from './-adl.types';

import {
  batchSummary,
  historyAgo,
  historyDotColor,
  isBatchActive,
  isTerminalPhase,
  nowTrack,
  phaseDisplay,
  progressSegments,
  showTrackProgressBar,
  statChips,
  statLine,
  trackRowState,
} from './-adl.batch';

const batch = (over: Partial<AdlBatch> = {}): AdlBatch => ({
  batch_id: 'b1',
  playlist_id: 'p1',
  batch_name: 'Batch',
  source_page: 'wishlist',
  phase: 'downloading',
  total: 10,
  completed: 2,
  failed: 1,
  active: 3,
  queued: 4,
  ...over,
});

const track = (over: Partial<AdlDownload> = {}): AdlDownload =>
  ({ task_id: 't', status: 'queued', progress: 0, error: null, ...over }) as AdlDownload;

describe('phase display', () => {
  it('says Queued, not Analyzing, for a parked batch', () => {
    // The executor runs 3 workers; the rest park at `queued`. Labelling those
    // as analysing made 26 parked batches look like 26 running ones.
    expect(phaseDisplay(batch({ phase: 'queued' }))).toEqual({
      text: 'Queued',
      icon: 'hourglass',
    });
  });

  it('covers the remaining phases', () => {
    expect(phaseDisplay(batch({ phase: 'analysis' }))).toEqual({
      text: 'Analyzing...',
      icon: 'spinner',
    });
    expect(phaseDisplay(batch({ phase: 'downloading', completed: 4, total: 9 }))).toEqual({
      text: '4/9 tracks',
      icon: 'spinner',
    });
    expect(phaseDisplay(batch({ phase: 'complete', completed: 7 }))).toEqual({
      text: 'Done — 7 tracks',
      icon: 'check',
    });
    expect(phaseDisplay(batch({ phase: 'cancelled' })).text).toBe('Cancelled');
    expect(phaseDisplay(batch({ phase: 'error' })).text).toBe('Error');
  });

  it('drops the spinner when a downloading batch has nothing in flight', () => {
    expect(phaseDisplay(batch({ phase: 'downloading', active: 0 })).icon).toBeNull();
  });

  it('shows the release sentence while a bundle downloads', () => {
    const display = phaseDisplay(
      batch({
        phase: 'album_downloading',
        album_bundle: { state: 'downloading', source: 'torrent', progress_percent: 40 },
      }),
    );
    expect(display.text).toBe('Torrent downloading release 40%');
    expect(display.icon).toBe('spinner');
  });

  it('shows an unknown phase raw rather than blank', () => {
    expect(phaseDisplay(batch({ phase: 'brand_new_phase' })).text).toBe('brand_new_phase');
  });
});

describe('progress segments', () => {
  it('splits done / failed / active by share of the total', () => {
    const segments = progressSegments(batch({ total: 10, completed: 2, failed: 1, active: 3 }));
    expect(segments).toEqual({ done: 20, failed: 10, active: 30 });
  });

  it('never totals more than 100%, even when the counters disagree', () => {
    // Counters can briefly exceed the total while tasks move between states;
    // without clamping the bar overflows its track.
    const segments = progressSegments(batch({ total: 4, completed: 3, failed: 3, active: 3 }));
    const sum = segments.done + segments.failed + segments.active;
    expect(sum).toBeLessThanOrEqual(100);
    expect(segments.done).toBe(75);
    expect(segments.failed).toBe(25);
    expect(segments.active).toBe(0);
  });

  it('gives the whole bar to a release download', () => {
    expect(
      progressSegments(
        batch({ phase: 'album_downloading', album_bundle: { progress_percent: 66 } }),
      ),
    ).toEqual({ done: 0, failed: 0, active: 66 });
  });

  it('survives a zero total', () => {
    const segments = progressSegments(batch({ total: 0, completed: 0, failed: 0, active: 0 }));
    expect(Number.isFinite(segments.done)).toBe(true);
    expect(segments.done).toBe(0);
  });
});

describe('stat chips', () => {
  it('renders only the non-zero counters', () => {
    expect(
      statChips(batch({ completed: 2, failed: 0, active: 1, queued: 0 })).map((c) => c.text),
    ).toEqual(['✓ 2', '↓ 1']);
  });

  it('renders none for an untouched batch', () => {
    expect(statChips(batch({ completed: 0, failed: 0, active: 0, queued: 0 }))).toEqual([]);
  });
});

describe('stat line', () => {
  it('joins only the non-zero counters in done-failed-active-queued order', () => {
    expect(statLine(batch({ completed: 3, failed: 1, active: 1, queued: 5 }))).toBe(
      '3 done · 1 failed · 1 active · 5 queued',
    );
    expect(statLine(batch({ completed: 2, failed: 0, active: 1, queued: 0 }))).toBe(
      '2 done · 1 active',
    );
  });

  it('says nothing for an untouched batch', () => {
    expect(statLine(batch({ completed: 0, failed: 0, active: 0, queued: 0 }))).toBe('');
  });
});

describe('batch summary', () => {
  const noSamples = () => [] as never[];

  it('is absent when nothing is active', () => {
    expect(batchSummary([], noSamples, Date.now())).toBeNull();
  });

  it('pluralises batches and sums the counters', () => {
    const summary = batchSummary(
      [batch({ active: 2, queued: 3 }), batch({ batch_id: 'b2', active: 1, queued: 1 })],
      noSamples,
      Date.now(),
    );
    expect(summary?.main).toBe('2 batches · 3 downloading · 4 queued');

    const single = batchSummary([batch({ active: 0, queued: 0 })], noSamples, Date.now());
    expect(single?.main).toBe('1 batch');
  });

  it('sums rates across batches rather than averaging them', () => {
    // Two batches each finishing a track a second finish two a second between
    // them; the combined ETA has to reflect that.
    const store: Record<string, { t: number; done: number }[]> = {
      b1: [{ t: 0, done: 0 }],
      b2: [{ t: 0, done: 0 }],
    };
    const samplesFor = (id: string) => store[id];
    const summary = batchSummary(
      [
        batch({
          batch_id: 'b1',
          phase: 'downloading',
          total: 20,
          completed: 10,
          failed: 0,
          active: 1,
          queued: 9,
        }),
        batch({
          batch_id: 'b2',
          phase: 'downloading',
          total: 20,
          completed: 10,
          failed: 0,
          active: 1,
          queued: 9,
        }),
      ],
      samplesFor,
      10_000,
    );
    // 20 remaining at 2 tracks/sec ≈ 10s.
    expect(summary?.eta).toBe('~10s left');
  });

  it('shows the first release speed and does not try to add them', () => {
    const summary = batchSummary(
      [
        batch({
          phase: 'album_downloading',
          album_bundle: { speed: '1.2 MB/s' },
          active: 0,
          queued: 0,
        }),
        batch({
          batch_id: 'b2',
          phase: 'album_downloading',
          album_bundle: { speed: '900 KB/s' },
          active: 0,
          queued: 0,
        }),
      ],
      noSamples,
      Date.now(),
    );
    expect(summary?.main).toContain('1.2 MB/s');
    expect(summary?.main).not.toContain('900 KB/s');
  });

  it('has no ETA without a measurable rate', () => {
    expect(batchSummary([batch()], noSamples, Date.now())?.eta).toBe('');
  });
});

describe('now-playing track', () => {
  it('prefers a downloading track over a searching one', () => {
    const tracks = [
      track({ task_id: 's', status: 'searching' }),
      track({ task_id: 'd', status: 'downloading' }),
    ];
    expect(nowTrack(batch({ phase: 'downloading', active: 1 }), tracks)?.task_id).toBe('d');
  });

  it('falls back to a searching track', () => {
    const tracks = [track({ task_id: 's', status: 'searching' })];
    expect(nowTrack(batch({ phase: 'downloading', active: 1 }), tracks)?.task_id).toBe('s');
  });

  it('is null for an idle batch', () => {
    const tracks = [track({ status: 'downloading' })];
    expect(nowTrack(batch({ phase: 'downloading', active: 0 }), tracks)).toBeNull();
  });
});

describe('track row state', () => {
  it('shows a percentage only once there is progress', () => {
    expect(trackRowState(track({ status: 'downloading', progress: 42 }), 'active').text).toBe(
      '42%',
    );
    // 0% would read as stalled; the dot is the honest marker.
    expect(trackRowState(track({ status: 'downloading', progress: 0 }), 'active').text).toBe('·');
  });

  it('spins while searching', () => {
    expect(trackRowState(track({ status: 'searching' }), 'active')).toEqual({
      text: null,
      spinner: true,
    });
  });

  it('marks processing, done and failed', () => {
    expect(trackRowState(track({ status: 'post_processing' }), 'active').text).toBe('proc');
    expect(trackRowState(track({ status: 'completed' }), 'completed').text).toBe('✓');
    const failed = trackRowState(track({ status: 'failed', error: 'nope' }), 'failed');
    expect(failed.text).toBe('✗');
    expect(failed.title).toBe('nope');
  });

  it('titles a failed track even with no error text', () => {
    expect(trackRowState(track({ status: 'failed', error: null }), 'failed').title).toBe('Failed');
  });

  it('shows the mini bar only for real progress', () => {
    expect(showTrackProgressBar(track({ status: 'downloading', progress: 5 }))).toBe(true);
    expect(showTrackProgressBar(track({ status: 'downloading', progress: 0 }))).toBe(false);
    expect(showTrackProgressBar(track({ status: 'searching', progress: 50 }))).toBe(false);
  });
});

describe('batch phase predicates', () => {
  it('knows which phases are terminal', () => {
    expect(['complete', 'cancelled', 'error'].every(isTerminalPhase)).toBe(true);
    expect(['downloading', 'queued', 'analysis', 'album_downloading'].some(isTerminalPhase)).toBe(
      false,
    );
  });

  it('treats a downloading batch as active only while work is in flight', () => {
    expect(isBatchActive(batch({ phase: 'downloading', active: 1 }))).toBe(true);
    expect(isBatchActive(batch({ phase: 'downloading', active: 0 }))).toBe(false);
    // A release download is active by definition — one file, no per-track count.
    expect(isBatchActive(batch({ phase: 'album_downloading', active: 0 }))).toBe(true);
  });
});

describe('history formatting', () => {
  const now = new Date('2026-07-30T12:00:00Z').getTime();

  it('reports minutes as just now, then hours, then days', () => {
    expect(historyAgo('2026-07-30T11:45:00Z', now)).toBe('just now');
    expect(historyAgo('2026-07-30T07:00:00Z', now)).toBe('5h ago');
    expect(historyAgo('2026-07-27T12:00:00Z', now)).toBe('3d ago');
  });

  it('is empty for missing or unparseable timestamps', () => {
    expect(historyAgo(undefined, now)).toBe('');
    expect(historyAgo('', now)).toBe('');
    expect(historyAgo('not a date', now)).toBe('');
  });

  it('colours the dot by source, white for anything unknown', () => {
    expect(historyDotColor('wishlist')).toBe('168, 85, 247');
    expect(historyDotColor('sync')).toBe('59, 130, 246');
    expect(historyDotColor('album')).toBe('16, 185, 129');
    expect(historyDotColor('something-else')).toBe('255, 255, 255');
    expect(historyDotColor(undefined)).toBe('255, 255, 255');
  });
});
