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

import { act, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import type { UrlTabPlaylist } from './-sync.url-tabs';

import {
  forgetAccountPlaylists,
  urlTabCacheKey,
  useRememberedPlaylists,
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

describe('urlTabCacheKey + useRememberedPlaylists (the paste-a-link tabs)', () => {
  afterEach(() => {
    forgetAccountPlaylists();
  });

  it('namespaces url-tab rows away from the account tab of the same name', () => {
    // The Deezer LINK tab and the Deezer-ARL ACCOUNT tab are different lists.
    expect(urlTabCacheKey('deezer')).toBe('url:deezer');
    expect(urlTabCacheKey('deezer')).not.toBe('deezer');
  });

  it('restores the rows a tab had loaded before the page was left', () => {
    // Boulder, live: went to Deezer Link, loaded a playlist, clicked sync, went
    // to Explorer, came back — the card was gone and only the history pill was
    // left, because /sync is a route and leaving it unmounts the tab.
    const first = renderHook(() => useRememberedPlaylists(urlTabCacheKey('deezer')));
    act(() => {
      first.result.current[1]([{ id: '1', name: 'Dz Mix' }]);
    });
    first.unmount();

    const second = renderHook(() => useRememberedPlaylists(urlTabCacheKey('deezer')));
    expect(second.result.current[0]).toEqual([{ id: '1', name: 'Dz Mix' }]);
  });

  it('supports functional updates — every call site uses setPlaylists(prev => …)', () => {
    const { result } = renderHook(() => useRememberedPlaylists(urlTabCacheKey('youtube')));
    act(() => {
      result.current[1]([{ id: 'a' }]);
    });
    act(() => {
      result.current[1]((prev) => [...prev, { id: 'b' }]);
    });
    expect(result.current[0]).toEqual([{ id: 'a' }, { id: 'b' }]);

    const remount = renderHook(() => useRememberedPlaylists(urlTabCacheKey('youtube')));
    expect(remount.result.current[0]).toHaveLength(2);
  });

  it('keeps separate tabs separate', () => {
    const dz = renderHook(() => useRememberedPlaylists(urlTabCacheKey('deezer')));
    act(() => {
      dz.result.current[1]([{ id: 'dz' }]);
    });
    const yt = renderHook(() => useRememberedPlaylists(urlTabCacheKey('youtube')));
    expect(yt.result.current[0]).toEqual([]);
  });

  it('starts empty for a tab that has never loaded anything', () => {
    const { result } = renderHook(() => useRememberedPlaylists(urlTabCacheKey('itunes-link')));
    expect(result.current[0]).toEqual([]);
  });
});
