import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  deleteStashEntry,
  lookupStudioTrack,
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
  trimSilence,
} from './-sample-studio.api';
import { DEFAULT_FX } from './-sample-studio.types';

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

  it('sends the full FX recipe on preview', async () => {
    routes['/api/sample/preview'] = ok({
      success: true,
      data: { preview_id: 'p7_x.wav', engine: 'librosa', duration_s: 2.5 },
      error: null,
    });
    await requestPreview(7, {
      start: 0,
      end: 2.5,
      pitchSt: 0,
      targetBpm: null,
      fx: {
        ...DEFAULT_FX,
        normalize: true,
        reverse: true,
        fadeMs: 10,
        space: 0.5,
        delay: { time: '1/8', feedback: 0.35, mix: 0.2 },
      },
    });
    expect(calls[0].body).toMatchObject({
      normalize: 'peak',
      fade_ms: 10,
      reverse: true,
      space: 0.5,
      delay: { time: '1/8', feedback: 0.35, mix: 0.2 },
    });
  });

  it('omits normalize and nulls space/delay on preview when FX are off', async () => {
    routes['/api/sample/preview'] = ok({
      success: true,
      data: { preview_id: 'p7_x.wav', engine: 'librosa', duration_s: 1 },
      error: null,
    });
    await requestPreview(7, { start: 0, end: 1, pitchSt: 0, targetBpm: null, fx: DEFAULT_FX });
    expect(calls[0].body).not.toHaveProperty('normalize');
    expect(calls[0].body).toMatchObject({ fade_ms: 5, reverse: false, space: null, delay: null });
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
      fx: { ...DEFAULT_FX, normalize: true, reverse: true },
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
      normalize: 'peak',
      reverse: true,
      fade_ms: 5,
      space: null,
      delay: null,
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
    expect(calls[0].body).toEqual({ track_id: 7 });
  });

  it('trimSilence posts the region and returns the adjusted bounds', async () => {
    routes['/api/sample/trim'] = ok({
      success: true,
      data: { track_id: 7, start_s: 0.12, end_s: 3.4 },
      error: null,
    });
    const bounds = await trimSilence(7, 0, 3.5);
    expect(bounds).toEqual({ track_id: 7, start_s: 0.12, end_s: 3.4 });
    expect(calls[0].body).toMatchObject({ track_id: 7, start_s: 0, end_s: 3.5, stem: null });
  });

  it('trimSilence trims against the stem being chopped', async () => {
    routes['/api/sample/trim'] = ok({ success: true, data: { start_s: 1, end_s: 2 }, error: null });
    await trimSilence(7, 0, 3.5, 'drums');
    expect(calls[0].body).toMatchObject({ stem: 'drums' });
  });

  it('lookupStudioTrack resolves by exact track id', async () => {
    routes['library/tracks'] = ok({
      success: true,
      data: {
        tracks: [
          { id: 7, title: 'Midnight Groove', artist_name: 'Test Artist' },
          { id: 9, title: 'Other Song', artist_name: 'Test Artist' },
        ],
      },
      error: null,
    });
    const track = await lookupStudioTrack(7, 'Midnight Groove', 'Test Artist');
    expect(track?.id).toBe(7);
    const missing = await lookupStudioTrack(42, 'Midnight Groove', 'Test Artist');
    expect(missing).toBeNull();
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

  it('converts track duration from milliseconds to seconds', async () => {
    // serialize_track ships the tracks.duration DB unit (ms); the panel's
    // length filter and the editor header work in seconds. 206000ms -> 206s,
    // not the "3433:20.0" a raw pass-through produced.
    routes['library/tracks'] = ok({
      success: true,
      data: {
        tracks: [
          { id: 2, duration: 206000 },
          { id: 3, duration: null },
        ],
      },
      error: null,
    });
    const opts = studioTrackSearchQueryOptions('rock');
    const tracks = await opts.queryFn!({} as never);
    expect(tracks).toEqual([
      { id: 2, duration: 206 },
      { id: 3, duration: null },
    ]);
  });

  it('converts recently-added durations from milliseconds to seconds', async () => {
    routes['recently-added'] = ok({
      success: true,
      data: { items: [{ id: 1, duration: 120000 }], type: 'tracks' },
      error: null,
    });
    const opts = studioTrackSearchQueryOptions('   ');
    const tracks = await opts.queryFn!({} as never);
    expect(tracks).toEqual([{ id: 1, duration: 120 }]);
  });

  it('analysis retry nonce adds ?retry=1 to clear a sticky worker error', async () => {
    routes['/api/sample/analysis'] = ok({
      success: true,
      data: { track_id: 7, status: 'pending' },
      error: null,
    });
    const plain = studioAnalysisQueryOptions(7);
    await plain.queryFn!({} as never);
    expect(calls[0].url).not.toContain('retry=');
    expect(plain.queryKey).toContain(0);

    const retry = studioAnalysisQueryOptions(7, 1);
    await retry.queryFn!({} as never);
    expect(calls[1].url).toContain('retry=1');
    expect(retry.queryKey).toContain(1);
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
      key: null,
      onsets: [],
      duration_s: null,
    });
  });

  it('normalizes the key from analysis — confident, uncertain, and missing', async () => {
    const opts = studioAnalysisQueryOptions(7);
    const fetchAnalysis = async (key: unknown) => {
      routes['/api/sample/analysis'] = ok({
        success: true,
        data: { track_id: 7, status: 'done', bpm: 99.4, key, onsets: [], duration_s: 206 },
        error: null,
      });
      return opts.queryFn!({} as never);
    };

    const confident = await fetchAnalysis({ name: 'C minor', confidence: 0.87 });
    expect(confident.key).toEqual({ name: 'C minor', confidence: 0.87 });

    // The confidence floor lives in display (formatKeyBpm shows "key
    // uncertain" below 0.5) — normalization keeps the raw key.
    const uncertain = await fetchAnalysis({ name: 'C minor', confidence: 0.3 });
    expect(uncertain.key).toEqual({ name: 'C minor', confidence: 0.3 });

    const missing = await fetchAnalysis(null);
    expect(missing.key).toBeNull();

    const malformed = await fetchAnalysis({ name: 42 });
    expect(malformed.key).toBeNull();
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
