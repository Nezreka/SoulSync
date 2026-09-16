import { describe, expect, it } from 'vitest';

import type { ImportInboxItem, ImportInboxStatus } from './-import.types';

import {
  countInbox,
  describeFile,
  describeItemFiles,
  describeItemMatch,
  filterInboxItems,
  inboxActions,
  secondsToNextScan,
  sortInbox,
  timeAgo,
} from './-import.inbox';

function item(over: Partial<ImportInboxItem> & { status: ImportInboxStatus }): ImportInboxItem {
  return {
    key: over.status,
    kind: 'album',
    name: 'Album',
    artist: 'Artist',
    folder_name: 'Artist - Album',
    folder_path: '/Staging/Artist - Album',
    rel_path: 'Artist - Album',
    in_staging: true,
    files: [],
    file_count: 12,
    total_duration_ms: 0,
    total_size: 0,
    formats: ['FLAC'],
    confidence: null,
    match: null,
    history_id: null,
    live: null,
    ...over,
  };
}

describe('filterInboxItems', () => {
  const rows = [
    item({ status: 'imported', in_staging: false, key: 'a', processed_at: '2026-09-01' }),
    item({ status: 'waiting', key: 'b' }),
    item({ status: 'needs_review', key: 'c' }),
    item({ status: 'importing', key: 'd' }),
    item({ status: 'failed', key: 'e', in_staging: true, created_at: '2026-09-02' }),
    item({ status: 'dismissed', key: 'f' }),
  ];

  it('attention is what a person has to act on, in urgency order', () => {
    // auto-import off: waiting needs a person
    expect(filterInboxItems(rows, 'attention', false).map((r) => r.key)).toEqual(['c', 'e', 'b']);
    // auto-import on: a fresh drop is about to be picked up, not a problem
    expect(filterInboxItems(rows, 'attention', true).map((r) => r.key)).toEqual(['c', 'e']);
  });

  it('history is what already happened, newest first', () => {
    expect(filterInboxItems(rows, 'history', true).map((r) => r.key)).toEqual(['e', 'a', 'f']);
  });

  it('all puts live work first and history last', () => {
    expect(sortInbox(rows).map((r) => r.key)).toEqual(['d', 'c', 'e', 'b', 'a', 'f']);
  });

  it('counts each pill', () => {
    expect(countInbox(rows, false)).toEqual({ attention: 3, all: 6, history: 3 });
    expect(countInbox(rows, true)).toEqual({ attention: 2, all: 6, history: 3 });
  });
});

describe('inboxActions', () => {
  it('gives each state the actions a person can take', () => {
    expect(inboxActions(item({ status: 'needs_review' }))).toEqual([
      'approve',
      'identify',
      'dismiss',
    ]);
    expect(inboxActions(item({ status: 'needs_identify' }))).toEqual(['identify', 'dismiss']);
    expect(inboxActions(item({ status: 'failed' }))).toEqual(['retry', 'identify']);
    expect(inboxActions(item({ status: 'waiting' }))).toEqual(['identify']);
    expect(inboxActions(item({ status: 'importing' }))).toEqual([]);
  });

  it('history rows earn nothing', () => {
    expect(inboxActions(item({ status: 'failed', in_staging: false }))).toEqual([]);
  });
});

describe('descriptions', () => {
  it('describes the files, dropping zero parts', () => {
    expect(
      describeItemFiles(
        item({ status: 'waiting', total_duration_ms: 2_892_000, total_size: 432_013_312 }),
      ),
    ).toBe('12 tracks · FLAC · 48:12 · 412 MB');
    expect(describeItemFiles(item({ status: 'waiting', formats: [] }))).toBe('12 tracks');
    expect(describeItemFiles(item({ status: 'waiting', kind: 'single', file_count: 1 }))).toBe(
      '1 file · FLAC',
    );
  });

  it('describes one file with its bitrate', () => {
    expect(
      describeFile({
        filename: 'a.flac',
        full_path: '/a.flac',
        rel_path: 'a.flac',
        title: '',
        artist: '',
        album: '',
        extension: '.flac',
        format: 'FLAC',
        duration_ms: 221_000,
        bitrate: 1_013_000,
        size: 32_505_856,
      }),
    ).toBe('FLAC · 3:41 · 1,013 kbps · 31 MB');
  });

  it('prefers the live track over the stored match', () => {
    const stored = item({
      status: 'importing',
      match: { matched_count: 9, total_tracks: 12, matches: [] },
    });
    expect(describeItemMatch(stored)).toBe('9/12 tracks matched');
    expect(
      describeItemMatch({
        ...stored,
        live: { track_index: 3, track_total: 12, track_name: 'Three' },
      }),
    ).toBe('Track 3/12: Three');
  });
});

describe('time', () => {
  const now = Date.parse('2026-09-16T12:00:00Z');
  it('rounds to the unit a person would say', () => {
    expect(timeAgo('2026-09-16T11:59:40Z', now)).toBe('just now');
    expect(timeAgo('2026-09-16T11:48:00Z', now)).toBe('12m ago');
    expect(timeAgo('2026-09-16T09:00:00Z', now)).toBe('3h ago');
    expect(timeAgo('2026-09-13T12:00:00Z', now)).toBe('3d ago');
    expect(timeAgo(null, now)).toBe('');
    expect(timeAgo('nope', now)).toBe('');
  });

  it('counts down to the next scan and never goes negative', () => {
    expect(secondsToNextScan('2026-09-16T11:59:30Z', 60, now)).toBe(30);
    expect(secondsToNextScan('2026-09-16T11:00:00Z', 60, now)).toBe(0);
    expect(secondsToNextScan(null, 60, now)).toBeNull();
  });
});
