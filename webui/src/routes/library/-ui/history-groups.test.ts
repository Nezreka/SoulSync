import { describe, expect, it } from 'vitest';

import type { LibraryV2HistoryEntry } from '../-library-v2.api';

import { groupHistoryEvents, historyOutcomeSummary, historySubject } from './history-groups';

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
