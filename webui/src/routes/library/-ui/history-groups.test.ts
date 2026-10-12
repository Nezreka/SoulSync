import { describe, expect, it } from 'vitest';

import type { LibraryV2HistoryEntry } from '../-library-v2.api';

import {
  groupHistoryEvents,
  historyOutcomeSummary,
  historySubject,
  summarizeRecentHistory,
} from './history-groups';

const event: LibraryV2HistoryEntry = {
  date: '2026-09-05T18:42:03',
  category: 'maintenance',
  event_type: 'check',
  title: 'Audio checked',
  detail: 'Audio verified',
  source: 'maintenance',
};

describe('history groups', () => {
  it('collapses consecutive events from the same operation minute while retaining every result', () => {
    const failed = {
      ...event,
      date: '2026-09-05T18:42:01',
      detail: 'No match',
      status: 'failed' as const,
    };
    const groups = groupHistoryEvents([event, failed]);
    expect(groups).toHaveLength(1);
    expect(groups[0]!.entries).toEqual([event, failed]);
  });

  it('keeps different sources, minutes and intervening operations separate', () => {
    const events = [
      event,
      { ...event, source: 'import' },
      event,
      { ...event, date: '2026-09-05T18:41:59' },
      { ...event, date: null },
      { ...event, date: null },
    ];
    expect(groupHistoryEvents(events)).toHaveLength(6);
  });
});

it('shows actual outcome counts and preserves which track each result belongs to', () => {
  const entries = [
    { ...event, status: 'Verified', track_title: 'First track', album_title: 'Release' },
    { ...event, status: 'Mismatch', track_title: 'Second track', album_title: 'Release' },
    { ...event, status: 'Verified' },
  ];
  expect(historyOutcomeSummary(entries)).toBe('2 Verified · 1 Mismatch');
  expect(historySubject(entries[1]!)).toBe('Second track');
});

it('keeps different jobs and current verdicts separate from recorded results', () => {
  expect(
    groupHistoryEvents([
      { ...event, job_id: 'job-a' },
      { ...event, job_id: 'job-b' },
      { ...event, job_id: 'job-b', status_basis: 'current_file' },
    ]),
  ).toHaveLength(3);
});

describe('recent history summary', () => {
  it('folds a repeated event into one line with its count and latest date', () => {
    const failed = { ...event, title: 'Search failed', status: 'failed' };
    const entries = [
      { ...failed, date: '2026-10-12T10:00:00' },
      { ...failed, date: '2026-10-11T10:00:00' },
      { ...failed, date: '2026-10-10T10:00:00' },
      { ...event, title: 'Downloaded', date: '2026-10-09T10:00:00' },
      { ...failed, date: '2026-10-08T10:00:00' },
    ];
    expect(summarizeRecentHistory(entries)).toEqual([
      expect.objectContaining({ label: 'Search failed', count: 3, last: '2026-10-12T10:00:00' }),
      expect.objectContaining({ label: 'Downloaded', count: 1 }),
      expect.objectContaining({ label: 'Search failed', count: 1 }),
    ]);
  });

  it('keeps only the newest lines', () => {
    const entries = ['A', 'B', 'C', 'D'].map((title) => ({ ...event, title }));
    expect(summarizeRecentHistory(entries, 2).map((line) => line.label)).toEqual(['A', 'B']);
  });
});
