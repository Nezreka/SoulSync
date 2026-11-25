import { describe, expect, it } from 'vitest';

import { ADULT, BOOKS_OFF, LISTEN_ONLY, MODERATOR, SEE_ALL, TEEN, permsOf } from '@/test/perms';

import {
  asksLeftLine,
  describePermissions,
  fallbackPermissions,
  fromResponse,
  isModerator,
  limitKindFor,
  modeFor,
  offTitle,
  refusalMessage,
  requestLimit,
  seesAllRequests,
  stillYours,
} from './my-permissions-core';

// what a member may do, from GET /api/profiles/me/permissions (#2166)
describe('my permissions: the map', () => {
  it('reads download, ask and none per kind of media', () => {
    expect(modeFor(permsOf(ADULT), 'music')).toBe('download');
    expect(modeFor(permsOf(TEEN), 'music')).toBe('ask');
    expect(modeFor(permsOf(TEEN), 'movies')).toBe('ask');
    expect(modeFor(permsOf(LISTEN_ONLY), 'music')).toBe('none');
  });

  it('a side that is off is none, whatever its mode says', () => {
    expect(modeFor(permsOf({ ...ADULT, 'music.access': false }), 'music')).toBe('none');
    expect(modeFor(permsOf(BOOKS_OFF), 'books')).toBe('none');
  });

  it('books and podcasts ask first like music (#2188)', () => {
    expect(modeFor(permsOf({ ...ADULT, 'books.get': 'ask' }), 'books')).toBe('ask');
    expect(modeFor(permsOf({ ...ADULT, 'podcasts.get': 'ask' }), 'podcasts')).toBe('ask');
    const can = describePermissions(
      permsOf({ ...ADULT, 'books.get': 'ask', 'podcasts.get': 'ask' }),
    );
    expect(can.rows.find((r) => r.id === 'books')?.lead).toBe(
      'Audiobooks on request, podcast episodes on request',
    );
  });

  it('admins download everything, an unknown mode is none', () => {
    expect(modeFor(permsOf({}, { isAdmin: true }), 'tv')).toBe('download');
    expect(modeFor(permsOf({ ...ADULT, 'music.get': 'whatever' }), 'music')).toBe('none');
  });

  it('a limit of 0 is no limit; admins have none', () => {
    expect(requestLimit(permsOf(ADULT))).toBeNull();
    expect(requestLimit(permsOf(TEEN))).toEqual({ n: 5, days: 7 });
    expect(requestLimit(permsOf(TEEN, { isAdmin: true }))).toBeNull();
  });

  it('a moderator sees everyone and answers; see_all only sees', () => {
    expect(isModerator(permsOf(MODERATOR))).toBe(true);
    expect(seesAllRequests(permsOf(MODERATOR))).toBe(true);
    expect(isModerator(permsOf(SEE_ALL))).toBe(false);
    expect(seesAllRequests(permsOf(SEE_ALL))).toBe(true);
    expect(seesAllRequests(permsOf(TEEN))).toBe(false);
  });

  it('the columns answer before the server does', () => {
    const asks = fallbackPermissions({ profileId: 7, isAdmin: false, canDownload: false });
    expect(asks.live).toBe(false);
    expect(modeFor(asks, 'music')).toBe('ask');
    // books follow the one column today and have no ask
    expect(modeFor(asks, 'books')).toBe('none');
    const gets = fallbackPermissions({ profileId: 7, isAdmin: false, canDownload: true });
    expect(modeFor(gets, 'books')).toBe('download');
  });

  it('only trusts a real answer', () => {
    expect(fromResponse(null)).toBeNull();
    expect(fromResponse({ success: false, error: 'permissions_unavailable' })).toBeNull();
    const live = fromResponse({
      success: true,
      profile_id: 7,
      effective: TEEN,
      source: 'template',
    });
    expect(live?.live).toBe(true);
    expect(live?.profileId).toBe(7);
  });
});

describe('my permissions: words', () => {
  it('says how many asks are left', () => {
    expect(asksLeftLine({ limit: 5, remaining: 3, days: 7 })).toBe('3 of 5 asks left this week');
    expect(asksLeftLine({ limit: 1, remaining: 1, days: 1 })).toBe('1 of 1 ask left today');
    expect(asksLeftLine({ limit: 5, remaining: 0, days: 30 })).toBe('No asks left this month');
    expect(asksLeftLine({ limit: 4, remaining: 2, days: 14 })).toBe(
      '2 of 4 asks left in the last 14 days',
    );
    expect(asksLeftLine(null)).toBe('');
  });

  it('shows the server refusal in its own words', () => {
    expect(
      refusalMessage({
        success: false,
        error: 'not_allowed',
        message: "Audiobooks isn't part of your profile. Ask an admin if you'd like it.",
      }),
    ).toContain('Audiobooks');
    expect(refusalMessage({ success: false, error: 'not_allowed' })).toContain(
      'part of your profile',
    );
    expect(refusalMessage({ success: false, error: 'Admin access required' })).toBeNull();
    expect(refusalMessage('nope')).toBeNull();
  });

  it('a whole side off reads differently from only getting off', () => {
    expect(offTitle(permsOf(BOOKS_OFF), 'books')).toBe('Audiobooks aren’t part of your profile');
    expect(offTitle(permsOf(TEEN), 'books')).toBe('Getting books isn’t part of your profile');
  });

  it('names what is still theirs', () => {
    expect(stillYours(permsOf(BOOKS_OFF), 'books')).toBe(
      'Music, movies, TV shows and podcasts are all still yours.',
    );
    const musicOnly = { 'music.access': true, 'movies.access': false, 'tv.access': false };
    expect(
      stillYours(
        permsOf({ ...musicOnly, 'books.access': false, 'podcasts.access': false }),
        'books',
      ),
    ).toBe('Music is still yours.');
  });
});

describe('what you can do', () => {
  it('a downloader gets everything right away, nothing to ask', () => {
    const can = describePermissions(permsOf(ADULT));
    expect(can.sentence).toBe(
      'You get music, movies, shows, audiobooks and podcast episodes right away.',
    );
    const music = can.rows.find((r) => r.id === 'music');
    expect(music?.lead).toBe('Downloads right away');
    expect(music?.strong).toBe(true);
    expect(music?.detail).toEqual(['explicit shown']);
    const req = can.rows.find((r) => r.id === 'requests');
    expect(req?.lead).toBe('Nothing to ask: it comes right away');
    expect(req?.detail).toEqual([]);
    expect(can.anyOff).toBe(false);
    expect(can.never).toEqual(['settings and people']);
  });

  it('an asker hears the limit and what is used', () => {
    // one limit for every kind that asks reads once
    const can = describePermissions(permsOf(TEEN), { used: { movies: 2 } });
    expect(can.sentence).toBe('You ask first for music, movies and shows, 5 asks a week each.');
    const req = can.rows.find((r) => r.id === 'requests');
    expect(req?.lead).toBe('5 asks a week each');
    expect(req?.detail).toEqual(['you see only your own']);
    // each kind's count sits on its own row
    expect(can.rows.find((r) => r.id === 'movies')?.detail).toEqual([
      'up to PG-13',
      '5 asks a week, 2 used',
    ]);
    // tv follows the movie rating, in tv words
    expect(can.rows.find((r) => r.id === 'tv')?.detail).toEqual(['up to TV-14', '5 asks a week']);
    expect(can.rows.find((r) => r.id === 'books')?.lead).toBe(
      'Audiobooks to browse, podcasts to follow and play',
    );
  });

  it('each kind has its own limit', () => {
    const perms = permsOf({
      ...TEEN,
      'music.request_limit': { n: 2, days: 1 },
      'movies.request_limit': { n: 5, days: 7 },
      'tv.request_limit': { n: 0, days: 7 },
    });
    expect(requestLimit(perms, 'music')).toEqual({ n: 2, days: 1 });
    expect(requestLimit(perms, 'movies')).toEqual({ n: 5, days: 7 });
    expect(requestLimit(perms, 'tv')).toBeNull();
    const can = describePermissions(perms);
    expect(can.sentence).toBe(
      'You ask first for music, movies and shows, 2 music asks a day and 5 movie asks a week.',
    );
    expect(can.rows.find((r) => r.id === 'tv')?.detail).toEqual(['up to TV-14']);
    // a youtube video rides with TV, or movies without TV
    expect(limitKindFor(perms, 'youtube')).toBe('tv');
    expect(limitKindFor(permsOf({ ...TEEN, 'tv.access': false }), 'youtube')).toBe('movies');
    expect(limitKindFor(perms, 'episode')).toBe('tv');
    expect(asksLeftLine({ limit: 5, remaining: 3, days: 7, kind: 'movies' })).toBe(
      '3 of 5 movie asks left this week',
    );
    expect(asksLeftLine({ limit: 2, remaining: 0, days: 1 }, 'tv')).toBe('No TV asks left today');
    // an older map with the one shared requests.limit reads it for every kind
    const old = permsOf({
      ...ADULT,
      'tv.request_limit': undefined,
      'requests.limit': { n: 3, days: 7 },
    });
    expect(requestLimit(old, 'tv')).toEqual({ n: 3, days: 7 });
  });

  it('books off says so and offers who to ask', () => {
    const can = describePermissions(permsOf({ ...BOOKS_OFF, 'podcasts.access': false }));
    const books = can.rows.find((r) => r.id === 'books');
    expect(books?.off).toBe(true);
    expect(books?.lead).toBe('Not part of your profile');
    expect(can.anyOff).toBe(true);
  });

  it('a moderator is told they answer everyone else', () => {
    const can = describePermissions(permsOf(MODERATOR));
    const req = can.rows.find((r) => r.id === 'requests');
    expect(req?.label).toBe('Requests · moderator');
    expect(req?.detail).toContain('you answer everyone’s asks but your own');
  });

  it('see everyone says so', () => {
    const req = describePermissions(permsOf(SEE_ALL)).rows.find((r) => r.id === 'requests');
    expect(req?.detail).toContain('you see everyone’s');
  });

  it('parts that are off are listed, and so is who to ask', () => {
    const can = describePermissions(
      permsOf({ ...ADULT, 'music.import': false, 'music.sample_studio': false, 'chat.use': false }),
    );
    expect(can.never).toEqual(['Import', 'Sample Studio', 'Chat & Arcade', 'settings and people']);
    expect(can.anyOff).toBe(true);
  });

  it('music apps that are off are listed (a Kids profile)', () => {
    const can = describePermissions(permsOf({ ...ADULT, 'music.apps': false }));
    expect(can.never).toEqual(['music apps', 'settings and people']);
  });

  it('can’t request music reads as listen only', () => {
    const can = describePermissions(permsOf(LISTEN_ONLY));
    expect(can.rows.find((r) => r.id === 'music')?.lead).toBe('Listen only');
    expect(can.sentence).not.toContain('music');
  });

  it('admins get one line', () => {
    const can = describePermissions(permsOf({}, { isAdmin: true }));
    expect(can.rows).toEqual([]);
    expect(can.sentence).toContain('admin');
  });
});
