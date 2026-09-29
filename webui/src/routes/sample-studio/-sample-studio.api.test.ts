import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  deleteStashEntry,
  previewAudioUrl,
  requestPreview,
  requestStems,
  saveChop,
  stashAudioUrl,
  stashExportUrl,
  stemAudioUrl,
  studioAnalysisQueryOptions,
  studioStemsStatusQueryOptions,
  studioStreamUrl,
  studioTrackSearchQueryOptions,
} from './-sample-studio.api';

interface RecordedCall {
  url: string;
  method: string;
  body: unknown;
}

let calls: RecordedCall[];
let routes: Record<string, { status: number; body: unknown }>;

function ok(body: unknown, status = 200) {
  return { status, body };
}

beforeEach(() => {
  calls = [];
  routes = {};
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const req = input instanceof Request ? input : null;
      const url = req ? req.url : String(input);
      const method = req ? req.method : (init?.method ?? 'GET');
      let body: unknown;
      if (init?.body) body = JSON.parse(String(init.body));
      else if (req) {
        try {
          body = await req.clone().json();
        } catch {
          body = undefined;
        }
      }
      calls.push({ url, method, body });
      const route = Object.entries(routes).find(([key]) => url.includes(key))?.[1] ?? {
        status: 404,
        body: {},
      };
      return new Response(JSON.stringify(route.body), {
        status: route.status,
        headers: { 'Content-Type': 'application/json' },
      });
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('URL builders', () => {
  it('encodes paths and ids', () => {
    expect(studioStreamUrl('/music/my song.flac')).toBe(
      '/stream/library-audio?path=%2Fmusic%2Fmy%20song.flac',
    );
    expect(previewAudioUrl('p7_abc.wav')).toBe('/api/sample/preview/p7_abc.wav');
    expect(previewAudioUrl('p7/a b.wav')).toBe('/api/sample/preview/p7%2Fa%20b.wav');
    expect(stashAudioUrl(11)).toBe('/api/sample/stash/11/audio');
    expect(stashExportUrl()).toBe('/api/sample/stash/export');
    expect(stemAudioUrl(7, 'drums')).toBe('/api/sample/stems/7/drums/audio');
  });
});

describe('requestPreview', () => {
  it('posts the params and normalizes the response', async () => {
    routes['/api/sample/preview'] = ok({
      success: true,
      data: { preview_id: 'p7_x.wav', engine: 'librosa', duration_s: 2.5 },
      error: null,
    });
    const result = await requestPreview(7, {
      start: 0,
      end: 2.5,
      pitchSt: 2,
      targetBpm: 128,
      stem: 'drums',
    });
    expect(result).toEqual({ preview_id: 'p7_x.wav', engine: 'librosa', duration_s: 2.5 });
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toMatchObject({
      track_id: 7,
      start_s: 0,
      end_s: 2.5,
      pitch_st: 2,
      target_bpm: 128,
      stem: 'drums',
    });
  });

  it('sends stem: null when cutting the full mix', async () => {
    routes['/api/sample/preview'] = ok({
      success: true,
      data: { preview_id: 'p7_x.wav', engine: 'librosa', duration_s: 1 },
      error: null,
    });
    await requestPreview(7, { start: 0, end: 1, pitchSt: 0, targetBpm: null });
    expect(calls[0].body).toMatchObject({ stem: null, target_bpm: null });
  });

  it('throws the server message on failure', async () => {
    routes['/api/sample/preview'] = ok({ success: false, error: 'slice too long' }, 400);
    await expect(
      requestPreview(7, { start: 0, end: 70, pitchSt: 0, targetBpm: null }),
    ).rejects.toThrow('slice too long');
  });
});

describe('saveChop', () => {
  it('posts the full chop body and returns the entry', async () => {
    const entry = { id: 3, name: 'break', tags: ['a'] };
    routes['/api/sample/chop'] = ok({ success: true, data: entry, error: null }, 201);
    const result = await saveChop(7, {
      start: 1,
      end: 3,
      pitchSt: -2,
      targetBpm: null,
      stem: null,
      name: 'break',
      tags: ['a'],
      format: 'flac',
    });
    expect(result).toMatchObject({ id: 3, name: 'break' });
    expect(calls[0].body).toMatchObject({
      track_id: 7,
      start_s: 1,
      end_s: 3,
      pitch_st: -2,
      format: 'flac',
    });
  });
});

describe('stems + stash requests', () => {
  it('requestStems posts the track id', async () => {
    routes['/api/sample/stems'] = ok(
      { success: true, data: { track_id: 7, status: 'queued', stems: [] }, error: null },
      202,
    );
    const info = await requestStems(7);
    expect(info.status).toBe('queued');
    expect(calls[0].body).toMatchObject({ track_id: 7 });
  });

  it('deleteStashEntry issues a DELETE', async () => {
    routes['/api/sample/stash/11'] = ok({ success: true, data: { deleted: 11 }, error: null });
    await deleteStashEntry(11);
    expect(calls).toHaveLength(1);
    expect(calls[0].method).toBe('DELETE');
    expect(calls[0].url).toContain('/api/sample/stash/11');
  });
});

describe('studioTrackSearchQueryOptions', () => {
  it('empty query hits recently-added', async () => {
    routes['recently-added'] = ok({
      success: true,
      data: { items: [{ id: 1 }], type: 'tracks' },
      error: null,
    });
    const opts = studioTrackSearchQueryOptions('   ');
    expect(typeof opts.queryFn).toBe('function');
    const tracks = await opts.queryFn!({} as never);
    expect(tracks).toEqual([{ id: 1 }]);
    expect(calls[0].url).toContain('recently-added');
  });

  it('a query hits the track search', async () => {
    routes['library/tracks'] = ok({ success: true, data: { tracks: [{ id: 2 }] }, error: null });
    const opts = studioTrackSearchQueryOptions('rock');
    expect(typeof opts.queryFn).toBe('function');
    const tracks = await opts.queryFn!({} as never);
    expect(tracks).toEqual([{ id: 2 }]);
    expect(calls[0].url).toContain('library/tracks');
    expect(calls[0].url).toContain('q=rock');
  });

  it('normalizes a missing analysis payload', async () => {
    routes['/api/sample/analysis'] = ok({
      success: true,
      data: { track_id: 7, status: 'done', bpm: null, onsets: null, duration_s: null },
      error: null,
    });
    const opts = studioAnalysisQueryOptions(7);
    expect(typeof opts.queryFn).toBe('function');
    const analysis = await opts.queryFn!({} as never);
    expect(analysis).toEqual({
      track_id: 7,
      status: 'done',
      bpm: null,
      onsets: [],
      duration_s: null,
    });
  });
});

describe('refetch intervals', () => {
  const fakeQuery = (data: unknown) => ({ state: { data } }) as never;

  function intervalOf(opts: { refetchInterval?: unknown }): (query: never) => number | false {
    expect(typeof opts.refetchInterval).toBe('function');
    return opts.refetchInterval as (query: never) => number | false;
  }

  it('analysis polls while pending, stops when done or errored', () => {
    const interval = intervalOf(studioAnalysisQueryOptions(7));
    expect(interval(fakeQuery({ status: 'queued' }))).toBe(2500);
    expect(interval(fakeQuery({ status: 'analyzing' }))).toBe(2500);
    expect(interval(fakeQuery({ status: 'done' }))).toBe(false);
    expect(interval(fakeQuery({ status: 'error: boom' }))).toBe(false);
    expect(interval(fakeQuery(undefined))).toBe(false);
  });

  it('analysis query is disabled without a track', () => {
    expect(studioAnalysisQueryOptions(null).enabled).toBe(false);
  });

  it('stems status polls while active and unfinished', () => {
    const interval = intervalOf(studioStemsStatusQueryOptions(7, true));
    expect(interval(fakeQuery(undefined))).toBe(1500);
    expect(interval(fakeQuery({ status: 'queued' }))).toBe(1500);
    expect(interval(fakeQuery({ status: 'running' }))).toBe(1500);
    expect(interval(fakeQuery({ status: 'done' }))).toBe(false);
    expect(interval(fakeQuery({ status: 'error: boom' }))).toBe(false);

    const idle = intervalOf(studioStemsStatusQueryOptions(7, false));
    expect(idle(fakeQuery({ status: 'queued' }))).toBe(false);
  });
});
