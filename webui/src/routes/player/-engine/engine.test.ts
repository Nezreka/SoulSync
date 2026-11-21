import { describe, expect, it } from 'vitest';

import type { EngineDeps, Playable, SavedPosition } from './engine';
import type { MediaKind, PlayItem } from './types';

import { PlayerEngine } from './engine';

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
  buffered = { length: 0, end: () => 0 };
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

function setup(opts: { playable?: (item: PlayItem) => Playable; saved?: SavedPosition | null }) {
  const media: Partial<Record<MediaKind, FakeMedia>> = {};
  const saves: { id: string; position: number; finished: boolean }[] = [];
  const intervals: (() => void)[] = [];
  const store = new Map<string, string>();
  let now = 1_000_000;
  const deps: EngineDeps = {
    createMedia: (kind) => (media[kind] = new FakeMedia(kind)) as unknown as HTMLMediaElement,
    playable: async (item) =>
      opts.playable?.(item) ?? { url: `/s/${item.type}/${item.id}`, duration: 100 },
    progress: async () => opts.saved ?? null,
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
    deps.playable = (item) => (item.id === 'slow' ? slow : fast(item));
    t.engine.playItems([movie('slow')]);
    t.engine.playItems([movie('fast')]);
    await t.flush();
    release({ url: '/s/slow' });
    await t.flush();
    expect(t.media.video!.src).toBe('/s/movie/fast');
    expect(t.engine.getState().item?.id).toBe('fast');
  });
});
