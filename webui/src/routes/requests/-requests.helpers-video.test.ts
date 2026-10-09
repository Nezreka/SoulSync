import { describe, expect, it } from 'vitest';

import type { MusicRequestList } from './-requests.types';

import {
  buildRequestItems,
  countForTab,
  filterRequestItems,
  itemImage,
  itemKey,
  itemTitle,
  statusText,
  subLine,
  whoAsked,
} from './-requests.helpers';

function vpending(id: number, at: string) {
  return {
    id,
    profile_id: 3,
    requester_name: 'Kim',
    video_id: `vid${id}`,
    url: `https://youtu.be/vid${id}`,
    title: `Video ${id}`,
    channel: 'Warp',
    artist: 'Aphex Twin',
    thumbnail_url: `https://img/vid${id}.jpg`,
    status: 'pending' as const,
    created_at: at,
  };
}

function vhistory(id: number, status: 'approved' | 'available' | 'declined', at: string) {
  return {
    id,
    profile_id: 3,
    requester_name: 'Kim',
    video_id: `vid${id}`,
    url: `https://youtu.be/vid${id}`,
    title: `Video ${id}`,
    channel: 'Warp',
    artist: 'Aphex Twin',
    thumbnail_url: `https://img/vid${id}.jpg`,
    status,
    resolved_at: at,
  };
}

const list: MusicRequestList = {
  asksFirst: true,
  quota: null,
  counts: { pending: 2, approved: 1, available: 1, declined: 1, removed: 0 },
  pending: [
    {
      key: 'album:blue',
      profile_id: 3,
      requester_name: 'Kim',
      kind: 'album',
      title: 'Blue',
      artist: 'Joni Mitchell',
      album: 'Blue',
      track_ids: ['1', '2'],
      tracks: [
        { id: '1', title: 'A' },
        { id: '2', title: 'B' },
      ],
      track_count: 2,
      created_at: '2026-09-23 12:00:00',
      status: 'pending',
    },
  ],
  history: [
    {
      id: 1,
      profile_id: 3,
      requester_name: 'Kim',
      group_key: 'k1',
      kind: 'track',
      title: 'Song 1',
      artist: 'Someone',
      tracks: [{ id: '1', title: 'Song 1' }],
      track_count: 1,
      status: 'approved',
      resolved_at: '2026-09-22 10:00:00',
    },
  ],
  pendingVideos: [vpending(10, '2026-09-24 12:00:00')],
  videoHistory: [
    vhistory(11, 'available', '2026-09-21 10:00:00'),
    vhistory(12, 'declined', '2026-09-20 10:00:00'),
  ],
};

const NOW = Date.parse('2026-09-25T12:00:00Z');

describe('requests helpers — music videos', () => {
  it('merges video pending and history into the item list', () => {
    // waiting first (tracks, then videos), then history newest first across
    // both kinds — the existing ordering, extended to videos
    const items = buildRequestItems(list);
    expect(items.map(itemKey)).toEqual(['p:3:album:blue', 'v:10', 'h:1', 'v:11', 'v:12']);
  });

  it('puts video items in the right tabs', () => {
    const items = buildRequestItems(list);
    expect(countForTab(items, 'waiting')).toBe(2);
    expect(countForTab(items, 'on-the-way')).toBe(1);
    expect(countForTab(items, 'available')).toBe(1);
    expect(countForTab(items, 'declined')).toBe(1);
    expect(filterRequestItems(items, 'waiting').map(itemTitle).sort()).toEqual([
      'Blue',
      'Video 10',
    ]);
    expect(filterRequestItems(items, 'available').map(itemKey)).toEqual(['v:11']);
    expect(filterRequestItems(items, 'declined').map(itemKey)).toEqual(['v:12']);
  });

  it('titles and describes video items', () => {
    const items = buildRequestItems(list);
    const pending = items.find((i) => itemKey(i) === 'v:10')!;
    const available = items.find((i) => itemKey(i) === 'v:11')!;
    const declined = items.find((i) => itemKey(i) === 'v:12')!;
    expect(itemKey(pending)).toBe('v:10');
    expect(itemTitle(pending)).toBe('Video 10');
    expect(itemImage(pending)).toBe('https://img/vid10.jpg');
    // channel + "Music video" label, no track counts
    expect(subLine(pending)).toBe('Warp · Music video');
    expect(statusText(pending)).toBe('Waiting');
    expect(statusText(available)).toBe('In your library');
    expect(statusText(declined)).toBe('Declined');
    expect(whoAsked(pending, true, NOW)).toBe('Kim asked · 1 day ago');
    expect(whoAsked(pending, false, NOW)).toBe('You asked · 1 day ago');
  });
});
