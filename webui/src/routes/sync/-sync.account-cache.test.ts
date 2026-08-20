/**
 * Account playlists surviving navigation (Specialmed).
 *
 *   "the playlists are not saved as they are saved in Deezer. I always have to
 *   make a refresh, as they disappear."
 *
 * They were never saved anywhere — the tab holds rows in component state and
 * the sync page's verticals are hooks, so leaving /sync drops all of it.
 * Deezer only looked persistent because its tab re-fetches on mount fast
 * enough to hide it.
 */

import { afterEach, describe, expect, it } from 'vitest';

import type { UrlTabPlaylist } from './-sync.url-tabs';

import {
  forgetAccountPlaylists,
  recallAccountPlaylists,
  rememberAccountPlaylists,
} from './-sync.account-cache';

afterEach(() => forgetAccountPlaylists());

const rows = (...names: string[]): UrlTabPlaylist[] =>
  names.map((name, i) => ({ id: `p${i}`, name })) as UrlTabPlaylist[];

describe('account playlist cache', () => {
  it('gives back what a source stored', () => {
    rememberAccountPlaylists('tidal', rows('My Mix 1', 'My Mix 2'));
    expect(recallAccountPlaylists('tidal')).toHaveLength(2);
  });

  it('reports never-loaded as null, not as empty', () => {
    // The tab shows "Click 'Refresh'..." for null and "No playlists found."
    // for [] — collapsing them would tell a first-time visitor their account
    // is empty.
    expect(recallAccountPlaylists('tidal')).toBeNull();
  });

  it('remembers a genuinely empty account', () => {
    rememberAccountPlaylists('tidal', []);
    expect(recallAccountPlaylists('tidal')).toEqual([]);
    expect(recallAccountPlaylists('tidal')).not.toBeNull();
  });

  it('keeps sources apart', () => {
    rememberAccountPlaylists('tidal', rows('Tidal Mix'));
    rememberAccountPlaylists('qobuz', rows('Qobuz Mix', 'Another'));
    expect(recallAccountPlaylists('tidal')).toHaveLength(1);
    expect(recallAccountPlaylists('qobuz')).toHaveLength(2);
  });

  it('a later load replaces the earlier one', () => {
    rememberAccountPlaylists('tidal', rows('Old'));
    rememberAccountPlaylists('tidal', rows('New', 'Newer'));
    const got = recallAccountPlaylists('tidal') ?? [];
    expect(got).toHaveLength(2);
    expect(got[0].name).toBe('New');
  });

  it('forgets one source without touching the others', () => {
    rememberAccountPlaylists('tidal', rows('T'));
    rememberAccountPlaylists('qobuz', rows('Q'));
    forgetAccountPlaylists('tidal');
    expect(recallAccountPlaylists('tidal')).toBeNull();
    expect(recallAccountPlaylists('qobuz')).toHaveLength(1);
  });

  it('ignores junk rather than caching it', () => {
    rememberAccountPlaylists('', rows('X'));
    rememberAccountPlaylists('tidal', null as unknown as UrlTabPlaylist[]);
    expect(recallAccountPlaylists('')).toBeNull();
    expect(recallAccountPlaylists('tidal')).toBeNull();
  });
});
