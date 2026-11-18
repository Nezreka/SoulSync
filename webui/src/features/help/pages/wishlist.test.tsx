/**
 * Wishlist answers, from /api/wishlist/tracks rows the way the service
 * sends them (spotify_data as a JSON string, retry_count, last_attempted,
 * failure_reason), the stats and the retry profile: a run going, the next
 * try, failing tracks by kind with their cooldown and the page's own bulk
 * retry, upgrade rows; nothing for a healthy list; nothing off the music tab.
 */

import { fireEvent, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';

import { answer, asProfile, renderAnswers, titles } from './answers.test-utils';
import { pageHelpFor } from './index';
import { cooldownFor, useWishlistAnswers } from './wishlist';

const utc = (msAgo: number) =>
  new Date(Date.now() - msAgo).toISOString().slice(0, 19).replace('T', ' ');

const row = (id: string, name: string, artist: string, extra: Record<string, unknown> = {}) => ({
  id,
  spotify_track_id: id,
  spotify_data: JSON.stringify({
    name,
    artists: [{ name: artist }],
    album: { name: 'Mezzanine', images: [{ url: `https://img/${id}.jpg` }] },
  }),
  retry_count: 0,
  last_attempted: null,
  failure_reason: null,
  ...extra,
});

const TEARDROP = row('sp-teardrop', 'Teardrop', 'Massive Attack', {
  retry_count: 6,
  last_attempted: utc(3 * 3_600_000),
  failure_reason: 'No matching track found on any source',
});
const ANGEL = row('sp-angel', 'Angel', 'Massive Attack', {
  retry_count: 4,
  last_attempted: utc(20 * 3_600_000),
  failure_reason: "AcoustID verification failed: mismatch: 'Angel (Remix)' by 'Massive Attack'",
});
const INERTIA = row('sp-inertia', 'Inertia Creeps', 'Massive Attack', { retry_count: 1 });
const UPGRADE = row('sp-dissolved', 'Dissolved Girl', 'Massive Attack', {
  source_info: JSON.stringify({ upgrade_check: true, original_quality: 'MP3 128' }),
});

const LADDER = {
  success: true,
  profile: {
    name: 'balanced',
    label: 'Balanced',
    description: '',
    ladder: { '3': 3600, '4': 21600, '5': 43200 },
    max_cooldown: 86400,
  },
};

let albums: unknown[] = [];
let singles: unknown[] = [];
let stats: Record<string, unknown> = {};

beforeEach(() => {
  albums = [];
  singles = [];
  stats = { singles: 0, albums: 0, total: 0, next_run_in_seconds: 0, is_auto_processing: false };
  asProfile(4, false);
  server.use(
    http.get('/api/wishlist/stats', () => HttpResponse.json(stats)),
    http.get('/api/wishlist/tracks', ({ request }) =>
      HttpResponse.json({
        success: true,
        tracks: new URL(request.url).searchParams.get('category') === 'albums' ? albums : singles,
      }),
    ),
    http.get('/api/wishlist/retry-profile', () => HttpResponse.json(LADDER)),
  );
});
afterEach(() => {
  delete window.getCurrentProfileContext;
  window.history.replaceState(null, '', '/');
});

describe('wishlist answers', () => {
  it('registers on the wishlist', () => {
    expect(pageHelpFor('wishlist').useAnswers).toBe(useWishlistAnswers);
  });

  it('an empty wishlist says nothing', async () => {
    const { container } = renderAnswers(useWishlistAnswers, { isAdmin: false });
    await new Promise((r) => setTimeout(r, 60));
    expect(titles(container)).toEqual([]);
  });

  it('a healthy list says when the next try is', async () => {
    albums = [INERTIA];
    stats = { total: 1, next_run_in_seconds: 1380, is_auto_processing: false };
    const { container } = renderAnswers(useWishlistAnswers, { isAdmin: false });
    await waitFor(() => expect(titles(container)).toEqual(['When does the wishlist try again?']));
    expect(answer(container, 'next').textContent).toContain('In 23 min.');
  });

  it('a run going says so', async () => {
    albums = [INERTIA];
    stats = { total: 1, next_run_in_seconds: 0, is_auto_processing: true };
    const { container } = renderAnswers(useWishlistAnswers, { isAdmin: false });
    await waitFor(() =>
      expect(titles(container)).toEqual(['Is the wishlist downloading right now?']),
    );
  });

  it('failing tracks: by kind, tries, cooldown, and the bulk retry', async () => {
    albums = [ANGEL, INERTIA];
    singles = [TEARDROP];
    stats = { total: 3, next_run_in_seconds: 600, is_auto_processing: false };
    const sent: unknown[] = [];
    server.use(
      http.post('/api/wishlist/bulk', async ({ request }) => {
        sent.push(await request.json());
        return HttpResponse.json({
          success: true,
          results: [
            { id: 'sp-teardrop', ok: true, message: '' },
            { id: 'sp-angel', ok: true, message: '' },
          ],
        });
      }),
    );
    window.showToast = vi.fn();
    const { container } = renderAnswers(useWishlistAnswers, { isAdmin: false });
    await waitFor(() => expect(titles(container)).toContain('Why do 2 tracks keep failing?'));
    const a = answer(container, 'failing');
    expect(a.textContent).toContain(
      '1 track downloaded, then got thrown out because the file came in bad.',
    );
    expect(a.textContent).toContain('1 track had no match on any of your sources yet.');
    expect(a.textContent).toMatch(/Massive Attack · 6 tries · last \w+ · No match found/);
    await waitFor(() =>
      expect(answer(container, 'failing').textContent).toContain(
        'Teardrop waits 24 hours between tries now, on your Balanced retry profile.',
      ),
    );
    fireEvent.click(within(a).getByRole('button', { name: 'Try them all now' }));
    await waitFor(() =>
      expect(sent).toEqual([{ action: 'retry', track_ids: ['sp-teardrop', 'sp-angel'] }]),
    );
    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith('Retry: 2/2 succeeded', 'success'),
    );
    expect(within(a).getByRole('button', { name: 'Show only these' })).toBeTruthy();
  });

  it('one failing track reads its own reason', async () => {
    albums = [ANGEL];
    const { container } = renderAnswers(useWishlistAnswers, { isAdmin: false });
    await waitFor(() => expect(titles(container)).toEqual(['Why does Angel keep failing?']));
    expect(answer(container, 'failing').textContent).toContain(
      'The audio fingerprint says it was "Angel (Remix)" by Massive Attack, not this track.',
    );
  });

  it('upgrade rows are explained as upgrades', async () => {
    albums = [UPGRADE];
    const { container } = renderAnswers(useWishlistAnswers, { isAdmin: false });
    await waitFor(() =>
      expect(titles(container)).toEqual(['Why is the wishlist full of tracks I already have?']),
    );
    expect(answer(container, 'upgrades').textContent).toContain(
      "It's an upgrade: the file you have is below what your quality profile asks for",
    );
  });

  it('says nothing on the audiobooks tab', async () => {
    window.history.replaceState(null, '', '/wishlist?media=audiobooks');
    albums = [TEARDROP];
    const { container } = renderAnswers(useWishlistAnswers, { isAdmin: false });
    await new Promise((r) => setTimeout(r, 60));
    expect(titles(container)).toEqual([]);
  });
});

describe('cooldownFor', () => {
  it('reads the rung, falls back to the cap, null without a profile', () => {
    expect(cooldownFor(4, LADDER)).toBe(21600);
    expect(cooldownFor(9, LADDER)).toBe(86400);
    expect(cooldownFor(4, undefined)).toBeNull();
  });
});
