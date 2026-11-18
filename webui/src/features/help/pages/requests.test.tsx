/**
 * Requests answers, music and video, from the payloads the list routes
 * really send (api/music_requests.py, api/video/requests.py): a member's
 * waiting and approved asks, a decline with and without a reason, the
 * quota, why approval; an admin's waiting list; nothing to say for a member
 * with no asks who can download.
 */

import { fireEvent, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';

import { answer, asProfile, renderAnswers, titles } from './answers.test-utils';
import { pageHelpFor } from './index';
import { useMusicRequestAnswers, useVideoRequestAnswers, videoRequestAnswers } from './requests';

const utc = (msAgo: number) =>
  new Date(Date.now() - msAgo).toISOString().slice(0, 19).replace('T', ' ');
const H = 3_600_000;

const MUSIC_EMPTY = {
  success: true,
  pending: [],
  history: [],
  pending_videos: [],
  video_history: [],
  counts: {},
  asks_first: false,
  quota: null,
};

const HEROES = {
  key: 'album:heroes',
  profile_id: 4,
  requester_name: 'Sam',
  kind: 'album',
  title: 'Heroes',
  artist: 'David Bowie',
  image_url: 'https://img/heroes.jpg',
  track_ids: ['1', '2'],
  tracks: [
    { id: '1', title: 'Beauty and the Beast' },
    { id: '2', title: 'Joe the Lion' },
  ],
  track_count: 10,
  created_at: utc(2 * H),
  status: 'pending',
};
const LOW = {
  id: 7,
  profile_id: 4,
  group_key: 'album:low',
  kind: 'album',
  title: 'Low',
  artist: 'David Bowie',
  tracks: [],
  track_count: 11,
  status: 'approved',
  resolved_at: utc(3 * H),
};
const LODGER = {
  id: 8,
  profile_id: 4,
  group_key: 'album:lodger',
  kind: 'album',
  title: 'Lodger',
  tracks: [],
  track_count: 10,
  status: 'declined',
  admin_response: 'Already in the library as a deluxe',
  resolved_at: utc(20 * H),
};

let music: Record<string, unknown> = MUSIC_EMPTY;
let video: Record<string, unknown> = { success: true, requests: [], quota: null };
const headers: string[] = [];

beforeEach(() => {
  music = MUSIC_EMPTY;
  video = { success: true, requests: [], quota: null };
  headers.length = 0;
  server.use(
    http.get('/api/requests/music', ({ request }) => {
      headers.push(request.headers.get('X-Profile-Id') || '');
      return HttpResponse.json(music);
    }),
    http.get('/api/video/requests', () => HttpResponse.json(video)),
  );
});
afterEach(() => {
  delete window.getCurrentProfileContext;
});

describe('requests answers (music)', () => {
  it('registers on both requests pages', () => {
    expect(pageHelpFor('requests').useAnswers).toBe(useMusicRequestAnswers);
    expect(pageHelpFor('video-requests').useAnswers).toBe(useVideoRequestAnswers);
  });

  it('a member who can download with no asks gets nothing', async () => {
    asProfile(4, false);
    const { container } = renderAnswers(useMusicRequestAnswers, { isAdmin: false });
    await waitFor(() => expect(headers).toEqual(['4']));
    await new Promise((r) => setTimeout(r, 30));
    expect(titles(container)).toEqual([]);
  });

  it('a member hears where each ask is, the quota, a decline, and why approval', async () => {
    asProfile(4, false);
    music = {
      ...MUSIC_EMPTY,
      pending: [HEROES],
      history: [LOW, LODGER],
      asks_first: true,
      quota: { limit: 5, days: 7, used: 2, remaining: 3 },
    };
    const { container } = renderAnswers(useMusicRequestAnswers, { isAdmin: false });
    await waitFor(() =>
      expect(titles(container)).toEqual([
        'When will Heroes arrive?',
        'When will Low arrive?',
        'Why was Lodger declined?',
        'Why do my requests need approval?',
        'Where do I see all of mine?',
      ]),
    );
    const first = answer(container, 'wait:album:heroes');
    expect(first).toHaveAttribute('open');
    expect(first.textContent).toContain(
      "It's waiting for your admin to say yes. Once they do, it goes on your wishlist and downloads from there, like Low, which is on its way now.",
    );
    expect(first.textContent).toContain("You've used 2 of your 5 music requests in the last 7 days.");
    expect(answer(container, 'onway:Low').textContent).toContain(
      'Your admin said yes 3 hours ago.',
    );
    expect(answer(container, 'declined').textContent).toContain(
      'Your admin said: Already in the library as a deluxe',
    );
    expect(container.textContent).not.toContain('Sam');
  });

  it("a used-up quota says why you can't ask", async () => {
    asProfile(4, false);
    music = {
      ...MUSIC_EMPTY,
      asks_first: true,
      quota: { limit: 5, days: 7, used: 5, remaining: 0 },
    };
    const { container } = renderAnswers(useMusicRequestAnswers, { isAdmin: false });
    await waitFor(() => expect(titles(container)).toContain("Why can't I ask for more?"));
    expect(answer(container, 'quota').textContent).toContain(
      "You've used all 5 music requests for the last 7 days.",
    );
  });

  it('an admin sees who is waiting and can jump to them', async () => {
    asProfile(1, true);
    music = {
      ...MUSIC_EMPTY,
      pending: [
        HEROES,
        {
          ...HEROES,
          key: 'track:x',
          requester_name: 'Ana',
          kind: 'track',
          title: 'Ashes to Ashes',
          created_at: utc(H),
        },
      ],
    };
    const nav = vi.fn(() => Promise.resolve(true));
    window.SoulSyncWebRouter = { navigateToHref: nav } as never;
    const { container } = renderAnswers(useMusicRequestAnswers);
    await waitFor(() => expect(titles(container)[0]).toBe('2 requests are waiting on you'));
    const a = answer(container, 'admin-waiting');
    expect(a.textContent).toContain(
      'Ana and Sam asked, and nothing downloads until you say yes or no.',
    );
    expect(a.textContent).toContain('Ana · track · asked 1 hour ago');
    expect(a.textContent).toContain('Sam · 10 tracks · asked 2 hours ago');
    fireEvent.click(within(a).getByRole('button', { name: 'Show waiting' }));
    expect(nav).toHaveBeenCalledWith('/requests?tab=waiting');
    expect(titles(container)).toContain('What happens when I approve?');
    delete window.SoulSyncWebRouter;
  });
});

describe('requests answers (video)', () => {
  const DUNE = {
    id: 31,
    profile_id: 4,
    requester_name: 'Sam',
    kind: 'movie',
    title: 'Dune: Part Two',
    year: 2024,
    status: 'pending',
    created_at: utc(H),
  };
  const INTERSTELLAR = {
    id: 32,
    profile_id: 4,
    kind: 'movie',
    title: 'Interstellar',
    status: 'approved',
    resolved_at: utc(2 * H),
    state: 'on_the_way' as const,
    progress: { owned: 0, wanted: 1, failed: 0, total: 1 },
  };
  const SHOGUN = {
    id: 33,
    profile_id: 4,
    kind: 'show',
    title: 'Shogun',
    status: 'approved',
    resolved_at: utc(30 * H),
    state: 'partial' as const,
    progress: { owned: 6, wanted: 3, failed: 1, total: 10 },
  };

  it('a member: waiting, on the way, part here, quota and why approval', async () => {
    asProfile(4, false);
    video = {
      success: true,
      requests: [DUNE, INTERSTELLAR, SHOGUN],
      quota: { limit: 5, days: 7, used: 2, remaining: 3 },
    };
    const { container } = renderAnswers(useVideoRequestAnswers, { isAdmin: false });
    await waitFor(() =>
      expect(titles(container)).toEqual([
        'When will Dune: Part Two arrive?',
        'When will Interstellar arrive?',
        'Why is only part of Shogun here?',
        'Why do my requests need approval?',
      ]),
    );
    expect(answer(container, 'wait:31').textContent).toContain(
      "It's waiting for your admin to approve it. Once they do, SoulSync starts looking for it, like Interstellar, which is on its way now.",
    );
    expect(answer(container, 'wait:31').textContent).toContain(
      "You've used 2 of your 5 music requests in the last 7 days.",
    );
    const part = answer(container, 'partial:33');
    expect(part.textContent).toContain(
      "6 of 10 episodes are in your library. 3 episodes are still being looked for. 1 episode couldn't be found yet.",
    );
    expect(within(part).getByRole('progressbar')).toHaveAttribute('aria-valuenow', '60');
  });

  it('an admin sees waiting titles grouped with everyone who asked', () => {
    const answers = videoRequestAnswers(
      { requests: [DUNE, { ...DUNE, id: 34, requester_name: 'Ana' }, INTERSTELLAR] },
      { page: 'video-requests', isAdmin: true },
    );
    expect(answers.map((a) => a.title)).toEqual([
      'Dune: Part Two is waiting on you',
      'What happens when I approve?',
    ]);
  });

  it('nothing to say to an admin but what approving does', () => {
    expect(
      videoRequestAnswers({ requests: [] }, { page: 'p', isAdmin: true }).map((a) => a.id),
    ).toEqual(['admin-approve']);
  });

  it('a failed load says nothing', () => {
    expect(videoRequestAnswers(undefined, { page: 'p', isAdmin: false })).toEqual([]);
  });
});
