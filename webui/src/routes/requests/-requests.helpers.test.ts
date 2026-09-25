import { describe, expect, it } from 'vitest';

import type { MusicRequestList } from './-requests.types';

import {
  REQUEST_TABS,
  buildRequestItems,
  countForTab,
  filterRequestItems,
  parseServerTime,
  relativeTime,
  statusText,
  subLine,
  tabLabel,
  whoAsked,
} from './-requests.helpers';

const list: MusicRequestList = {
  asksFirst: false,
  counts: { pending: 1, approved: 1, available: 1, declined: 1, removed: 1 },
  pending: [
    {
      key: 'album:blue',
      profile_id: 3,
      requester_name: 'Kim',
      kind: 'album',
      title: 'Blue',
      artist: 'Joni Mitchell',
      album: 'Blue',
      track_ids: ['1', '2', '3'],
      tracks: [
        { id: '1', title: 'A' },
        { id: '2', title: 'B' },
        { id: '3', title: 'C' },
      ],
      track_count: 3,
      created_at: '2026-09-23 12:00:00',
      status: 'pending',
    },
  ],
  history: [
    row(1, 'approved', '2026-09-20 10:00:00'),
    row(2, 'available', '2026-09-22 10:00:00'),
    row(3, 'declined', '2026-09-21 10:00:00'),
    row(4, 'removed', '2026-09-19 10:00:00'),
  ],
};

function row(id: number, status: 'approved' | 'available' | 'declined' | 'removed', at: string) {
  return {
    id,
    profile_id: 3,
    requester_name: 'Kim',
    group_key: `k${id}`,
    kind: 'track' as const,
    title: `Song ${id}`,
    artist: 'Someone',
    tracks: [{ id: String(id), title: `Song ${id}` }],
    track_count: 1,
    status,
    resolved_at: at,
  };
}

const NOW = Date.parse('2026-09-25T12:00:00Z');

describe('requests helpers', () => {
  it('puts waiting first, then history newest first', () => {
    const items = buildRequestItems(list);
    expect(items.map((i) => (i.source === 'pending' ? i.group.key : i.row.id))).toEqual([
      'album:blue',
      2,
      3,
      1,
      4,
    ]);
  });

  it('filters and counts per tab', () => {
    const items = buildRequestItems(list);
    expect(countForTab(items, 'waiting')).toBe(1);
    expect(countForTab(items, 'on-the-way')).toBe(1);
    expect(countForTab(items, 'available')).toBe(1);
    expect(countForTab(items, 'declined')).toBe(1);
    // removed only shows under all
    expect(countForTab(items, 'all')).toBe(5);
    expect(filterRequestItems(items, 'declined')[0]?.status).toBe('declined');
  });

  it('only numbers the tabs that need attention', () => {
    const [waiting, onTheWay, available] = REQUEST_TABS;
    expect(tabLabel(waiting!, 3)).toBe('Waiting · 3');
    expect(tabLabel(waiting!, 0)).toBe('Waiting');
    expect(tabLabel(onTheWay!, 2)).toBe('On the way · 2');
    expect(tabLabel(available!, 5)).toBe('Available');
  });

  it('says where each ask is at', () => {
    const texts = buildRequestItems(list).map(statusText);
    expect(texts).toEqual([
      'Waiting',
      'In your library',
      'Declined',
      'On the way',
      'Removed before it arrived',
    ]);
  });

  it('builds the sub line and who asked', () => {
    const [album, track] = buildRequestItems(list);
    expect(subLine(album!)).toBe('Joni Mitchell · 3 tracks');
    expect(subLine(track!)).toBe('Someone');
    expect(whoAsked(album!, true, NOW)).toBe('Kim asked · 2 days ago');
    expect(whoAsked(album!, false, NOW)).toBe('You asked · 2 days ago');
  });

  it('reads sqlite utc timestamps', () => {
    expect(parseServerTime('2026-09-25 11:00:00')).toBe(Date.parse('2026-09-25T11:00:00Z'));
    expect(parseServerTime('2026-09-25T11:00:00+00:00')).toBe(
      Date.parse('2026-09-25T11:00:00Z'),
    );
    expect(parseServerTime('nope')).toBeNull();
    expect(relativeTime('2026-09-25 11:59:40', NOW)).toBe('just now');
    expect(relativeTime('2026-09-25 09:00:00', NOW)).toBe('3 hours ago');
    expect(relativeTime(null, NOW)).toBe('');
  });
});
