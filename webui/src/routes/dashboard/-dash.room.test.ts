/**
 * The dashboard's shared pictures: what the two count tiles show, and the
 * cover that lights the hero. They ride requests the page already makes.
 */

import { renderHook } from '@testing-library/react';
import { act } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { fetchWatchlistCount, fetchWishlistCount } from './-dash.api';
import { automationCardRows, upNextMarks } from './-dash.automations';
import {
  loadTilePictures,
  publishHeroArt,
  publishTilePictures,
  readTilePictures,
  resetDashRoom,
  useHeroArt,
  useTilePictures,
} from './-dash.room';

afterEach(() => {
  act(() => resetDashRoom());
  vi.unstubAllGlobals();
});

describe('readTilePictures', () => {
  it('keeps what a browser can load, five at most', () => {
    const many = Array.from({ length: 8 }, (_, n) => ({
      name: `Artist ${n}`,
      image_url: `https://cdn.example/${n}.jpg`,
    }));
    expect(readTilePictures(many)).toHaveLength(5);
    expect(
      readTilePictures([
        { name: 'Web', image_url: 'https://cdn.example/a.jpg' },
        { name: 'Own route', image_url: '/api/library/v2/artwork/artist/7' },
        { name: 'A path on the media server', image_url: '/library/metadata/1/thumb' },
        { name: 'No picture' },
        null,
      ]).map((p) => p.name),
    ).toEqual(['Web', 'Own route']);
  });

  it('is empty for anything that is not a list', () => {
    expect(readTilePictures(undefined)).toEqual([]);
    expect(readTilePictures('nope')).toEqual([]);
  });
});

describe('the tile pictures', () => {
  const serve = (body: unknown, ok = true) =>
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok, json: async () => body })),
    );

  it('one request fills both tiles', async () => {
    const faces = renderHook(() => useTilePictures('faces'));
    const sleeves = renderHook(() => useTilePictures('sleeves'));
    serve({
      success: true,
      faces: [{ name: 'Hotel Pools', image_url: 'https://cdn.example/hp.jpg' }],
      sleeves: [{ name: 'pillows', image_url: 'https://cdn.example/p.jpg' }],
    });
    await act(async () => {
      await loadTilePictures();
    });
    expect(vi.mocked(fetch)).toHaveBeenCalledWith('/api/dashboard/tile-pictures');
    expect(faces.result.current.map((p) => p.name)).toEqual(['Hotel Pools']);
    expect(sleeves.result.current.map((p) => p.name)).toEqual(['pillows']);
  });

  it('a failed request leaves what is showing', async () => {
    const faces = renderHook(() => useTilePictures('faces'));
    act(() =>
      publishTilePictures('faces', [{ name: 'A', image_url: 'https://cdn.example/a.jpg' }]),
    );
    serve({}, false);
    await act(async () => {
      await loadTilePictures();
    });
    expect(faces.result.current).toHaveLength(1);
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('down')));
    await act(async () => {
      await loadTilePictures();
    });
    expect(faces.result.current).toHaveLength(1);
  });

  it('the counts read as they always did', async () => {
    serve({ success: true, count: 58 });
    expect(await fetchWatchlistCount()).toEqual({ count: 58, next_run_in_seconds: undefined });
    serve({ count: 11 });
    expect(await fetchWishlistCount()).toBe(11);
  });

  it('the same pictures again do not wake the tile', () => {
    const { result } = renderHook(() => useTilePictures('faces'));
    const faces = [{ name: 'A', image_url: 'https://cdn.example/a.jpg' }];
    act(() => publishTilePictures('faces', faces));
    const first = result.current;
    act(() => publishTilePictures('faces', [...faces]));
    expect(result.current).toBe(first);
  });
});

describe('the hero cover', () => {
  it('is the last one published, and nothing when there is none', () => {
    const { result } = renderHook(() => useHeroArt());
    expect(result.current).toBeNull();
    act(() => publishHeroArt('/art/geometry.jpg'));
    expect(result.current).toBe('/art/geometry.jpg');
    act(() => publishHeroArt(''));
    expect(result.current).toBeNull();
  });
});

describe('upNextMarks: the line over Up next', () => {
  const NOW = Date.parse('2026-11-12T06:00:00Z');
  const at = (minutes: number) =>
    new Date(NOW + minutes * 60000).toISOString().slice(0, 19).replace('T', ' ');
  const api = (id: number, next: string | null, extra: Record<string, unknown> = {}) => ({
    id,
    name: `Job ${id}`,
    enabled: 1,
    trigger_type: 'schedule',
    trigger_config: { interval: 1, unit: 'hours' },
    action_type: 'clean_completed_downloads',
    next_run: next,
    ...extra,
  });

  it('places each run by how soon it is, within the hour', () => {
    const rows = automationCardRows(
      [api(1, at(15)), api(2, at(30)), api(3, at(60)), api(4, at(90)), api(5, null)],
      NOW,
    );
    expect(upNextMarks(rows, NOW).map((m) => [m.id, m.at])).toEqual([
      [1, 25],
      [2, 50],
      [3, 100],
    ]);
  });

  it('a run under way sits at the start, an overdue one too, a paused one nowhere', () => {
    const rows = automationCardRows(
      [api(1, at(20)), api(2, at(-5)), api(3, at(10), { enabled: 0 })],
      NOW,
      { '1': { status: 'running', phase: 'Working', progress: 40 } },
    );
    expect(upNextMarks(rows, NOW)).toEqual([
      { id: 1, name: 'Job 1', at: 0, running: true, count: 1, names: ['Job 1'], ids: [1] },
      { id: 2, name: 'Job 2', at: 0, running: false, count: 1, names: ['Job 2'], ids: [2] },
    ]);
  });
});
