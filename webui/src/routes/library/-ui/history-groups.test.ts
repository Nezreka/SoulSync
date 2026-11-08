import { describe, expect, it } from 'vitest';

import type { LibraryV2HistoryEntry } from '../-library-v2.api';

import {
  groupHistoryEvents,
  historyOutcomeSummary,
  historySteps,
  historySubject,
  historyTree,
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
  // writes with no verdict say what they wrote
  expect(historyOutcomeSummary([event, event])).toBe('Audio verified');
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

describe('history tree', () => {
  const at = (date: string, patch: Partial<LibraryV2HistoryEntry>): LibraryV2HistoryEntry => ({
    ...event,
    date,
    track_id: 7,
    track_title: 'Love The Way You Lie',
    album_id: 3,
    album_title: 'Recovery',
    ...patch,
  });
  // newest first, like the feed
  const track = [
    at('2026-10-29T19:25:00Z', {
      event_type: 'verification_status_updated',
      title: 'Acoustic ID status updated',
      status: 'Verified',
    }),
    at('2026-10-14T19:12:00Z', {
      event_type: 'verification_status_updated',
      title: 'Acoustic ID status updated',
      status: 'Verified',
    }),
    at('2026-10-12T18:59:00Z', { event_type: 'applied_tags', title: 'Applied tags' }),
    at('2026-10-12T13:51:00Z', {
      event_type: 'downloaded',
      category: 'imported',
      title: 'Downloaded',
    }),
    at('2026-10-12T13:40:00Z', { event_type: 'grab_failed', category: 'failed' }),
    at('2026-10-12T13:30:00Z', { event_type: 'grab_failed', category: 'failed' }),
    at('2026-10-12T13:00:00Z', { event_type: 'grab_submitted', category: 'grabbed' }),
  ];

  it('gives each track one node, then the release and what no track owns', () => {
    const nodes = historyTree([
      ...track,
      { ...event, album_id: 3, album_title: 'Recovery', event_type: 'catalogue_completed' },
      { ...event, event_type: 'search_started' },
    ]);
    expect(nodes.map((n) => [n.title, n.subtitle, n.entries.length])).toEqual([
      ['Love The Way You Lie', 'Recovery', 7],
      ['Recovery', 'Whole release', 1],
      ['Searches & other activity', null, 1],
    ]);
  });

  it('names a track from any of its events, a download names none', () => {
    const download = { ...event, track_id: 7, event_type: 'downloaded' };
    expect(historyTree([download, track[0]!]).map((n) => [n.title, n.subtitle])).toEqual([
      ['Love The Way You Lie', 'Recovery'],
    ]);
  });

  it('reads the pipeline in order, with the retries as a count and the newest check', () => {
    expect(historySteps(track).map((s) => [s.label, s.tone])).toEqual([
      ['Grabbed', 'neutral'],
      ['Downloaded after 3 tries', 'ok'],
      ['Metadata written', 'ok'],
      ['Verified', 'ok'],
    ]);
    expect(historySteps([track[4]!]).map((s) => s.label)).toEqual(['Download failed']);
  });

  it('folds every event of one kind together whenever it happened', () => {
    const groups = groupHistoryEvents(track, { byKind: true });
    expect(groups.map((g) => [g.entries[0]!.event_type, g.entries.length])).toEqual([
      ['verification_status_updated', 2],
      ['applied_tags', 1],
      ['downloaded', 1],
      ['grab_failed', 2],
      ['grab_submitted', 1],
    ]);
  });
});
