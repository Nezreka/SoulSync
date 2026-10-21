import { describe, expect, it } from 'vitest';

import type { AdlBatch, AdlDownload } from './-adl.types';
import type { VdlDownload } from './-vdl.types';

import {
  musicChipCounts,
  musicLive,
  musicQuiet,
  videoChipCounts,
  videoLive,
  videoQuiet,
} from './-dl.header-data';

const row = (over: Partial<AdlDownload> = {}): AdlDownload =>
  ({
    task_id: 't',
    title: 'Xtal',
    artist: 'Aphex Twin',
    album: 'SAW',
    artwork: '',
    status: 'completed',
    progress: 0,
    error: null,
    verification_status: null,
    batch_id: '',
    batch_name: '',
    batch_source: '',
    playlist_id: '',
    track_index: 0,
    batch_total: 1,
    timestamp: 0,
    priority: 0,
    quality: '',
    is_persistent_history: false,
    ...over,
  }) as AdlDownload;

const utc = (msAgo: number, now: number) =>
  new Date(now - msAgo).toISOString().slice(0, 19).replace('T', ' ');

describe('music header', () => {
  it('counts every chip the way its filter does (cancelled counts as failed)', () => {
    const counts = musicChipCounts([
      row({ status: 'downloading' }),
      row({ status: 'queued' }),
      row({ status: 'completed' }),
      row({ status: 'cancelled' }),
      row({ status: 'not_found' }),
    ]);
    expect(counts).toEqual({ all: 5, active: 1, queued: 1, completed: 1, failed: 2 });
  });

  it('has no live strip while nothing moves', () => {
    expect(musicLive([row()], [], 0, '')).toBeNull();
  });

  it('names what is downloading and counts the live batches, not the history tail', () => {
    const rows = [
      row({ status: 'searching', title: 'Night Bus' }),
      row({ status: 'downloading', title: 'Saudade', artist: 'MARCØS' }),
      // fifty history rows must not read as "50 done"
      ...Array.from({ length: 50 }, () => row({ is_persistent_history: true })),
    ];
    const batch = { completed: 2, active: 2, queued: 1, failed: 1 } as AdlBatch;
    const live = musicLive(rows, [batch], 1_900_000, 'about 4m left');
    expect(live).toMatchObject({
      speedText: '1.8 MB/s',
      downloading: 1,
      done: 2,
      active: 2,
      queued: 1,
      failed: 1,
      nowTitle: 'Saudade',
      nowSub: 'MARCØS',
      nowMore: 1,
      etaText: 'about 4m left',
    });
  });

  it('falls back to the live rows when there is no batch', () => {
    const rows = [
      row({ status: 'downloading' }),
      row({ status: 'completed' }),
      row({ is_persistent_history: true }),
    ];
    expect(musicLive(rows, [], 0, '')).toMatchObject({ done: 1, active: 1 });
  });

  it('says how many finished today and when the last one did', () => {
    const now = Date.now();
    const rows = [
      row({ is_persistent_history: true, created_at: utc(60_000, now) }),
      row({ is_persistent_history: true, created_at: utc(40 * 3_600_000, now) }),
      row({ status: 'failed', timestamp: now / 1000 }),
    ];
    expect(musicQuiet(rows, now)).toMatch(/^Nothing downloading\. 1 finished today, last at .+\.$/);
    expect(musicQuiet([], now)).toBe('Nothing downloading.');
  });
});

describe('video header', () => {
  const v = (over: Partial<VdlDownload> = {}): VdlDownload => ({
    id: 1,
    kind: 'movie',
    title: 'Dune',
    ...over,
  });

  it('counts the chips', () => {
    expect(
      videoChipCounts([
        v({ status: 'downloading' }),
        v({ status: 'completed' }),
        v({ status: 'import_failed' }),
      ]),
    ).toEqual({ all: 3, active: 1, completed: 1, failed: 1 });
  });

  it('puts the slowest download on the eta and the queue apart from the active', () => {
    const live = videoLive(
      [
        v({ status: 'downloading', eta_seconds: 300, username: 'cinephile' }),
        v({ status: 'downloading', title: 'Heat', eta_seconds: 4000 }),
        v({ status: 'queued' }),
      ],
      2_000_000,
    );
    expect(live).toMatchObject({
      downloading: 2,
      active: 2,
      queued: 1,
      nowTitle: 'Dune',
      nowSub: 'cinephile',
      nowMore: 2,
    });
    expect(live?.etaText).toBe('about 1h 7m left');
  });

  it('dates the quiet line by completed_at', () => {
    const now = Date.now();
    expect(
      videoQuiet([v({ status: 'completed', completed_at: utc(5 * 60_000, now) })], now),
    ).toMatch(/1 finished today/);
  });
});
