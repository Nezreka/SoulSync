import { describe, expect, it } from 'vitest';

import type { EngineDeps, Playable, SavedPosition, TrackPick } from './engine';
import type { Tracks } from './tracks';
import type { MediaKind, PlayItem } from './types';

import { PlayerEngine, STILL_WATCHING_AFTER } from './engine';

/** just enough of a media element: src, time, play/pause and its events */
class FakeMedia {
  attrs: Record<string, string> = {};
  currentTime = 0;
  duration = Number.NaN;
  paused = true;
  ended = false;
  volume = 1;
  muted = false;
  playbackRate = 1;
  preload = '';
  buffered: { length: number; end: (i: number) => number } = { length: 0, end: () => 0 };
  private handlers: Record<string, (() => void)[]> = {};
  constructor(public kind: MediaKind) {}
  set src(v: string) {
    this.attrs.src = v;
  }
  get src() {
    return this.attrs.src ?? '';
  }
  getAttribute(k: string) {
    return this.attrs[k] ?? null;
  }
  removeAttribute(k: string) {
    delete this.attrs[k];
  }
  load() {}
  addEventListener(name: string, fn: () => void) {
    (this.handlers[name] ??= []).push(fn);
  }
  emit(name: string) {
    for (const fn of this.handlers[name] ?? []) fn();
  }
  async play() {
    this.paused = false;
    this.emit('loadedmetadata');
    this.emit('playing');
  }
  pause() {
    this.paused = true;
    this.emit('pause');
  }
  /** the browser moving the clock along */
  tick(to: number) {
    this.currentTime = to;
    this.emit('timeupdate');
  }
  finish() {
    this.ended = true;
    this.emit('ended');
    this.ended = false;
  }
}

function setup(opts: {
  playable?: (item: PlayItem, q?: string, pick?: TrackPick) => Playable;
  saved?: SavedPosition | null;
  tracks?: Tracks;
}) {
  const media: Partial<Record<MediaKind, FakeMedia>> = {};
  const saves: { id: string; position: number; finished: boolean }[] = [];
  const intervals: (() => void)[] = [];
  const store = new Map<string, string>();
  let now = 1_000_000;
  const deps: EngineDeps = {
    createMedia: (kind) => (media[kind] = new FakeMedia(kind)) as unknown as HTMLMediaElement,
    playable: async (item, q, pick) =>
      opts.playable?.(item, q, pick) ?? { url: `/s/${item.type}/${item.id}`, duration: 100 },
    progress: async () => opts.saved ?? null,
    tracks: async () => opts.tracks ?? { audio: [], subtitles: [] },
    subtitle: async () => 'WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nhi',
    // copied video starts at the keyframe before: here, the whole second below
    transcodeStart: async (_url, t) => ({ start: Math.floor(t), seek: Math.floor(t) + 0.3 }),
    save: (item, position, _d, finished) => saves.push({ id: item.id, position, finished }),
    store: { get: (k) => store.get(k) ?? null, set: (k, v) => void store.set(k, v) },
    now: () => now,
    mediaSession: null,
    setInterval: (fn) => intervals.push(fn),
    clearInterval: () => undefined,
  };
  const engine = new PlayerEngine(deps);
  return {
    engine,
    media,
    saves,
    store,
    flush: () => new Promise((r) => setTimeout(r, 0)),
    every10s: () => intervals.forEach((fn) => fn()),
    advance: (ms: number) => (now += ms),
  };
}

const movie = (id: string): PlayItem => ({ type: 'movie', id, title: id });
const track = (id: string): PlayItem => ({ type: 'track', id, title: id });

describe('player engine', () => {
  it('loads, resumes where it was left, and saves as it plays', async () => {
    const t = setup({ saved: { position: 42, finished: false } });
    t.engine.playItems([movie('1')]);
    await t.flush();
    const el = t.media.video!;
    expect(el.src).toBe('/s/movie/1');
    expect(el.currentTime).toBe(42);
    expect(t.engine.getState().status).toBe('playing');
    el.tick(55);
    t.every10s();
    expect(t.saves).toEqual([{ id: '1', position: 55, finished: false }]);
    t.every10s(); // nothing new: no save
    expect(t.saves.length).toBe(1);
    el.pause();
    el.tick(60);
    t.engine.saveNow();
    expect(t.saves.at(-1)).toEqual({ id: '1', position: 60, finished: false });
  });

  it('a finished item starts over, and an item barely begun starts at 0', async () => {
    const t = setup({ saved: { position: 90, finished: true } });
    t.engine.playItems([movie('1')]);
    await t.flush();
    expect(t.media.video!.currentTime).toBe(0);
  });

  it('marks the end finished and plays the next', async () => {
    const t = setup({});
    t.engine.playItems([movie('1'), movie('2')]);
    await t.flush();
    t.media.video!.finish();
    await t.flush();
    expect(t.saves).toContainEqual({ id: '1', position: 0, finished: true });
    expect(t.engine.getState().item?.id).toBe('2');
    t.media.video!.finish();
    await t.flush();
    expect(t.engine.getState().status).toBe('ended');
  });

  it('music never fills continue watching', async () => {
    const t = setup({});
    t.engine.playItems([track('1')]);
    await t.flush();
    t.media.audio!.tick(50);
    t.every10s();
    t.media.audio!.finish();
    expect(t.saves).toEqual([]);
  });

  it('a guide channel joined mid-way saves nothing of its own', async () => {
    const t = setup({
      saved: { position: 10, finished: false },
      playable: (item) => ({ url: `/s/${item.type}/${item.id}`, duration: 5400 }),
    });
    t.engine.playItems([movie('1')], 0, { startAt: 600, live: true });
    await t.flush();
    expect(t.media.video!.currentTime).toBe(600);
    t.media.video!.tick(700);
    t.every10s();
    expect(t.saves).toEqual([]);
  });

  it('a file a browser cannot play says so instead of playing', async () => {
    const t = setup({
      playable: () => ({ url: '/s', verdict: { verdict: 'no', reasons: ['hevc'] } }),
    });
    t.engine.playItems([movie('1')]);
    await t.flush();
    const s = t.engine.getState();
    expect(s.status).toBe('error');
    expect(s.error).toMatch(/can't play in a browser/);
    expect(s.error).not.toMatch(/transcoder lands/);
    expect(t.media.video?.src ?? '').toBe('');
  });

  it("plays an audiobook's parts back to back on the book's clock", async () => {
    const parts = [
      { index: 0, start: 0, duration: 100 },
      { index: 1, start: 100, duration: 50 },
    ];
    const t = setup({
      saved: { position: 120, finished: false },
      playable: () => ({
        url: '/s/audiobook/B0X?part=0',
        duration: 150,
        parts,
        chapters: [
          { start: 0, title: 'One' },
          { start: 100, title: 'Two' },
        ],
      }),
    });
    t.engine.playItems([{ type: 'audiobook', id: 'B0X', title: 'Book' }]);
    await t.flush();
    const el = t.media.audio!;
    expect(el.src).toBe('/s/audiobook/B0X?part=1'); // resumed into the second file
    expect(el.currentTime).toBe(20);
    el.tick(30);
    expect(t.engine.getState().position).toBe(130);
    t.engine.seek(10); // back into the first file
    await t.flush();
    expect(el.src).toBe('/s/audiobook/B0X?part=0');
    t.engine.nextChapter();
    await t.flush();
    expect(el.src).toBe('/s/audiobook/B0X?part=1');
    // the first part ending moves to the second, not to the next item
    t.engine.seek(50);
    await t.flush();
    el.finish();
    await t.flush();
    expect(el.src).toBe('/s/audiobook/B0X?part=1');
    expect(t.engine.getState().item?.id).toBe('B0X');
  });

  it('remembers speed per kind and volume across items', async () => {
    const t = setup({});
    t.engine.playItems([{ type: 'audiobook', id: 'B', title: 'B' }]);
    await t.flush();
    t.engine.setRate(1.5);
    t.engine.setVolume(0.4);
    t.engine.playItems([movie('1')]);
    await t.flush();
    expect(t.engine.getState().rate).toBe(1);
    expect(t.media.video!.volume).toBe(0.4);
    t.engine.playItems([{ type: 'audiobook', id: 'C', title: 'C' }]);
    await t.flush();
    expect(t.engine.getState().rate).toBe(1.5);
    expect(t.media.audio!.playbackRate).toBe(1.5);
  });

  it('the sleep timer pauses when its time is up', async () => {
    const t = setup({});
    t.engine.playItems([{ type: 'podcast_episode', id: 'e', title: 'E', url: 'https://x/e.mp3' }]);
    await t.flush();
    t.engine.setSleep(15);
    t.media.audio!.tick(5);
    expect(t.media.audio!.paused).toBe(false);
    t.advance(15 * 60_000);
    t.media.audio!.tick(6);
    expect(t.media.audio!.paused).toBe(true);
    expect(t.engine.getState().sleep).toBeNull();
  });

  it('a newer play wins over a slow older one', async () => {
    let release: (p: Playable) => void = () => undefined;
    const t = setup({});
    const slow = new Promise<Playable>((r) => (release = r));
    const deps = (t.engine as unknown as { deps: EngineDeps }).deps;
    const fast = deps.playable;
    deps.playable = (item, q, pick) => (item.id === 'slow' ? slow : fast(item, q, pick));
    t.engine.playItems([movie('slow')]);
    t.engine.playItems([movie('fast')]);
    await t.flush();
    release({ url: '/s/slow' });
    await t.flush();
    expect(t.media.video!.src).toBe('/s/movie/fast');
    expect(t.engine.getState().item?.id).toBe('fast');
  });
});

describe('what loading is doing', () => {
  it('locates, then opens or resumes, then plays; and says where it came from', async () => {
    const t = setup({
      saved: { position: 300, finished: false },
      playable: () => ({ url: '/s', duration: 1000, via: 'plex', took_ms: 24_000 }),
    });
    const phases: (string | null)[] = [t.engine.getState().phase];
    t.engine.subscribe(() => {
      const p = t.engine.getState().phase;
      if (phases.at(-1) !== p) phases.push(p);
    });
    t.engine.playItems([movie('1')]);
    await t.flush();
    expect(phases).toEqual([null, 'locating', 'resuming', null]);
    expect(t.engine.getState().source).toMatchObject({ via: 'plex', tookMs: 24_000 });
  });

  it('a timed out lookup says the drive did not answer', async () => {
    let fail: (e: Error) => void = () => undefined;
    const t = setup({});
    const deps = (t.engine as unknown as { deps: EngineDeps }).deps;
    deps.playable = () => new Promise((_r, reject) => (fail = reject));
    t.engine.playItems([movie('1')]);
    expect(t.engine.getState().phase).toBe('locating');
    await t.flush(); // the tracks first, then the lookup
    const timeout = new Error('Request timed out');
    timeout.name = 'TimeoutError';
    fail(timeout);
    await t.flush();
    const s = t.engine.getState();
    expect(s.status).toBe('error');
    expect(s.phase).toBeNull();
    expect(s.error).toMatch(/didn't answer in time/);
  });
});

describe('converted video', () => {
  const converted = {
    url: '/t/movie/1?q=original&part=7',
    duration: 6000,
    verdict: { verdict: 'no' as const },
    transcode: { video: 'copy' as const, audio: 'aac' as const, reasons: ['AC3 audio to AAC'] },
  };

  it('starts the converted stream where it resumes, and keeps the clock right', async () => {
    const t = setup({ saved: { position: 1200, finished: false }, playable: () => converted });
    t.engine.playItems([movie('1')]);
    await t.flush();
    const el = t.media.video!;
    expect(el.src).toBe('/t/movie/1?q=original&part=7&t=1200.30');
    el.tick(5);
    expect(t.engine.getState().position).toBe(1205);
    expect(t.engine.getState().transcode?.reasons).toEqual(['AC3 audio to AAC']);
  });

  it('seeks inside what has arrived, and restarts the stream past it', async () => {
    const t = setup({ playable: () => converted });
    t.engine.playItems([movie('1')]);
    await t.flush();
    const el = t.media.video!;
    el.buffered = { length: 1, end: () => 120 };
    el.emit('progress');
    t.engine.seek(60);
    expect(el.src).toBe('/t/movie/1?q=original&part=7');
    expect(el.currentTime).toBe(60);
    t.engine.seek(3000.6);
    await t.flush();
    // the stream starts at the keyframe (3000) and the clock knows it
    expect(el.src).toBe('/t/movie/1?q=original&part=7&t=3000.30');
    el.tick(1);
    expect(t.engine.getState().position).toBe(3001);
  });

  it('a maybe file the browser cannot play switches to the converted stream at the same spot', async () => {
    const t = setup({
      playable: () => ({
        url: '/s/movie/1',
        duration: 6000,
        fallback_url: '/t/movie/1?q=original',
      }),
    });
    t.engine.playItems([movie('1')]);
    await t.flush();
    const el = t.media.video!;
    el.tick(300);
    el.emit('error');
    await t.flush();
    // the fallback converts both streams: frame exact, no keyframe to find
    expect(el.src).toBe('/t/movie/1?q=original&t=300.00');
    expect(t.engine.getState().status).not.toBe('error');
    expect(t.engine.getState().transcode?.reasons[0]).toMatch(/couldn't play it as it is/);
  });

  it('a file that needs converting but cannot be says why', async () => {
    const t = setup({
      playable: () => ({
        url: '/s',
        verdict: { verdict: 'no' },
        transcode_unavailable: "SoulSync can't find ffmpeg",
      }),
    });
    t.engine.playItems([movie('1')]);
    await t.flush();
    expect(t.engine.getState().error).toBe("SoulSync can't find ffmpeg");
  });

  it('a quality change reloads at the same spot and is remembered', async () => {
    const asked: string[] = [];
    const t = setup({ playable: () => converted });
    const deps = (t.engine as unknown as { deps: EngineDeps }).deps;
    deps.playable = async (item, q) => {
      asked.push(q);
      return { ...converted, url: `/t/movie/${item.id}?q=${q}` };
    };
    t.engine.playItems([movie('1')]);
    await t.flush();
    t.media.video!.tick(100);
    t.engine.setQuality('720p');
    await t.flush();
    expect(asked).toEqual(['original', '720p']);
    expect(t.media.video!.src).toBe('/t/movie/1?q=720p&t=100.30');
    expect(t.store.get('quality')).toBe('720p');
  });
});

describe('tracks', () => {
  const tracks: Tracks = {
    audio: [
      {
        index: 0,
        language: 'eng',
        title: null,
        codec: 'aac',
        channels: 2,
        layout: null,
        default: true,
        plays: true,
      },
      {
        index: 1,
        language: 'jpn',
        title: null,
        codec: 'ac3',
        channels: 6,
        layout: null,
        default: false,
        plays: false,
      },
    ],
    subtitles: [
      {
        id: 's0',
        language: 'eng',
        title: null,
        codec: 'subrip',
        kind: 'text',
        forced: false,
        default: false,
        external: false,
      },
      {
        id: 's1',
        language: 'eng',
        title: null,
        codec: 'hdmv_pgs_subtitle',
        kind: 'image',
        forced: false,
        default: false,
        external: false,
      },
    ],
  };
  const playable = (_i: PlayItem, _q?: string, pick?: TrackPick) => ({
    url: `/s?audio=${pick?.audio ?? 0}&burn=${pick?.burn ?? ''}`,
    duration: 100,
  });

  it('starts on the saved language and draws text subtitles itself', async () => {
    const t = setup({ tracks, playable });
    t.store.set('audioLang', 'ja');
    t.store.set('subLang', 'en');
    t.engine.playItems([movie('1')]);
    await t.flush();
    await t.flush();
    const s = t.engine.getState();
    expect(s.audioTrack).toBe(1);
    expect(t.media.video!.src).toBe('/s?audio=1&burn=');
    expect(s.subtitle).toBe('s0');
    expect(s.cues).toEqual([{ start: 1, end: 2, text: 'hi' }]);
  });

  it('switching audio or a picture subtitle reloads at the same spot; text does not', async () => {
    const t = setup({ tracks, playable });
    t.engine.playItems([movie('1')]);
    await t.flush();
    t.media.video!.tick(40);
    t.engine.setAudioTrack(1);
    await t.flush();
    expect(t.media.video!.src).toBe('/s?audio=1&burn=');
    expect(t.store.get('audioLang')).toBe('jpn');
    t.engine.setSubtitle('s1');
    await t.flush();
    expect(t.media.video!.src).toBe('/s?audio=1&burn=1');
    t.engine.setSubtitle(null);
    await t.flush();
    expect(t.media.video!.src).toBe('/s?audio=1&burn=');
    expect(t.store.get('subLang')).toBe('off');
    const before = t.media.video!.src;
    t.engine.setSubtitle('s0');
    await t.flush();
    expect(t.media.video!.src).toBe(before);
    expect(t.engine.getState().cues.length).toBe(1);
  });
});

describe('louder than full, and audio sync', () => {
  it('goes to 150%: the element at full, the rest through the boost', async () => {
    const gains: number[] = [];
    const t = setup({});
    const deps = (t.engine as unknown as { deps: EngineDeps }).deps;
    deps.boost = (_el, g) => gains.push(g);
    t.engine.playItems([movie('1')]);
    await t.flush();
    t.engine.setVolume(1.4);
    expect(t.media.video!.volume).toBe(1);
    expect(gains.at(-1)).toBe(1.4);
    t.engine.setVolume(0.5);
    expect(t.media.video!.volume).toBe(0.5);
    expect(gains.at(-1)).toBe(1); // the boost stays wired, back to 1
    t.engine.setVolume(9);
    expect(t.engine.getState().volume).toBe(1.5);
  });

  it('audio sync asks for the moved sound at the same spot and is kept for the title', async () => {
    const asked: (number | undefined)[] = [];
    const t = setup({
      playable: (_i, _q, pick) => (asked.push(pick?.delay), { url: '/s', duration: 100 }),
    });
    t.engine.playItems([movie('1')]);
    await t.flush();
    t.media.video!.tick(30);
    t.engine.setAudioDelay(-150);
    await t.flush();
    expect(asked).toEqual([0, -150]);
    expect(t.engine.getState().audioDelay).toBe(-150);
    t.engine.playItems([movie('1')]);
    await t.flush();
    expect(asked.at(-1)).toBe(-150);
    t.engine.playItems([movie('2')]);
    await t.flush();
    expect(asked.at(-1)).toBe(0);
  });
});

describe('watched', () => {
  it('marks a movie watched once, into its credits', async () => {
    const marked: string[] = [];
    const t = setup({ playable: () => ({ url: '/s', duration: 100 }) });
    (t.engine as unknown as { deps: EngineDeps }).deps.watched = (i) => marked.push(i.id);
    t.engine.playItems([movie('1')]);
    await t.flush();
    t.media.video!.duration = 100;
    t.media.video!.tick(50);
    expect(marked).toEqual([]);
    t.media.video!.tick(93);
    t.media.video!.tick(95);
    expect(marked).toEqual(['1']);
    expect(t.engine.getState().watchedKey).toBe('movie:1');
  });
});

describe('are you still watching', () => {
  it('asks after a run of episodes nobody touched, and goes on when told', async () => {
    const t = setup({});
    const eps = ['1', '2', '3', '4', '5', '6'].map((id) => ({
      ...movie(id),
      type: 'episode' as const,
    }));
    t.engine.playItems(eps);
    await t.flush();
    for (let i = 0; i < STILL_WATCHING_AFTER; i++) {
      t.media.video!.finish();
      await t.flush();
    }
    expect(t.engine.getState().queue.index).toBe(STILL_WATCHING_AFTER);
    t.media.video!.finish();
    await t.flush();
    expect(t.engine.getState().stillWatching).toBe(true);
    expect(t.engine.getState().queue.index).toBe(STILL_WATCHING_AFTER);
    t.engine.keepWatching();
    await t.flush();
    expect(t.engine.getState().stillWatching).toBe(false);
    expect(t.engine.getState().queue.index).toBe(STILL_WATCHING_AFTER + 1);
  });

  it('anyone moving resets the count', async () => {
    const t = setup({});
    const eps = ['1', '2', '3', '4', '5', '6'].map((id) => ({
      ...movie(id),
      type: 'episode' as const,
    }));
    t.engine.playItems(eps);
    await t.flush();
    for (let i = 0; i < 5; i++) {
      t.engine.activity();
      t.media.video!.finish();
      await t.flush();
    }
    expect(t.engine.getState().stillWatching).toBe(false);
    expect(t.engine.getState().queue.index).toBe(5);
  });
});
