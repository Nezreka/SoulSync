/**
 * the player core: one engine for everything the player plays. it owns one
 * <audio> and one <video> element, the queue, and the state every control
 * draws from. UI never touches a media element; it reads state and sends
 * commands.
 *
 * what it does, in the order things happen:
 *   load      ask the server how to play the item (playable), pick up where
 *             the profile left off (the progress store), set the element
 *   play      keep the state current from the element's own events; save
 *             progress every 10 seconds, on pause and when the page hides
 *   end       mark it finished, then the next part (an audiobook's files),
 *             then the next in the queue
 *
 * everything outside the browser comes in through EngineDeps, so the
 * engine is tested with fakes and nothing here knows about react.
 */

import type { Queue } from './queue';
import type { Chapter, ItemType, MediaKind, Part, PlayItem, RepeatMode } from './types';

import * as Q from './queue';
import { bookTime, locate, nextChapterStart, previousChapterStart } from './timeline';
import { LONG_FORM, MEDIA_KIND } from './types';

export type Status = 'idle' | 'loading' | 'playing' | 'paused' | 'buffering' | 'ended' | 'error';

export interface Verdict {
  verdict: 'yes' | 'maybe' | 'no';
  reasons?: string[];
}

/** what the server says about how to play an item (GET /api/player/playable/...) */
/** what the server converts for this browser (core/player/transcode.py) */
export interface TranscodeInfo {
  video: 'copy' | 'h264';
  audio: 'copy' | 'aac';
  reasons: string[];
  quality?: string;
  tonemap?: boolean;
  /** the encoder doing the video ('NVIDIA GPU', 'CPU'); null when the video is copied */
  encoder?: string | null;
}

export type Quality = 'original' | '1080p' | '720p' | '480p';
export const QUALITIES: Quality[] = ['original', '1080p', '720p', '480p'];

export interface Playable {
  url: string;
  /** set when the url is the converted stream: it starts at ?t= and can't seek by range */
  transcode?: TranscodeInfo | null;
  /** a 'maybe' file: the converted stream to switch to if the browser can't play it */
  fallback_url?: string;
  /** why a file that needs converting can't be (no ffmpeg) */
  transcode_unavailable?: string;
  /** where the bytes come from: 'disk', or the media server ('plex', 'jellyfin', 'navidrome') */
  via?: string;
  /** how long the server took to find the file (a sleeping drive shows here) */
  took_ms?: number;
  mime?: string;
  duration?: number | null;
  parts?: Part[];
  chapters?: Chapter[];
  verdict?: Verdict | null;
  file_id?: number;
}

export interface SavedPosition {
  position: number;
  finished: boolean;
}

export interface EngineDeps {
  createMedia: (kind: MediaKind) => HTMLMediaElement;
  playable: (item: PlayItem, quality: Quality) => Promise<Playable>;
  progress: (item: PlayItem) => Promise<SavedPosition | null>;
  /**
   * where a converted stream with copied video really starts if asked for
   * ``t`` (a keyframe), and the ``t`` to request so it lands there
   */
  transcodeStart: (url: string, t: number) => Promise<{ start: number; seek: number }>;
  save: (item: PlayItem, position: number, duration: number | null, finished: boolean) => void;
  store: { get: (key: string) => string | null; set: (key: string, value: string) => void };
  now: () => number;
  /** the lock screen / media keys (navigator.mediaSession), when there is one */
  mediaSession?: MediaSession | null;
  setInterval: (fn: () => void, ms: number) => unknown;
  clearInterval: (handle: unknown) => void;
}

export type Sleep = { until: number } | { endOfItem: true } | null;

export interface PlayerState {
  queue: Queue;
  item: PlayItem | null;
  kind: MediaKind | null;
  status: Status;
  error: string | null;
  /** seconds on the item's own clock (a book's, across its parts) */
  position: number;
  duration: number;
  buffered: number;
  volume: number;
  muted: boolean;
  rate: number;
  parts: Part[];
  chapters: Chapter[];
  verdict: Verdict | null;
  sleep: Sleep;
  /** joined a guide channel mid-way: nothing is saved as the item's own progress */
  live: boolean;
  /** what's happening before the picture or sound starts, and since when (describe.ts words it) */
  phase: Phase;
  phaseSince: number;
  /** where this item's bytes come from, once the server has said */
  source: SourceInfo | null;
  /** what's converted, while the server is converting it */
  transcode: TranscodeInfo | null;
  /** the picked video quality (original: as it is, converted only if it must be) */
  quality: Quality;
}

export type Phase = 'locating' | 'opening' | 'resuming' | 'seeking' | 'buffering' | null;

export interface SourceInfo {
  via: string;
  tookMs: number | null;
}

export interface PlayOptions {
  /** start here (seconds on the item's clock) instead of where it was left */
  startAt?: number;
  /** a guide channel: no progress saved */
  live?: boolean;
}

const SAVE_EVERY_MS = 10_000;
/** within this of the start, there's nothing worth resuming */
const RESUME_MIN = 15;

function clampVolume(v: number) {
  return Math.min(1, Math.max(0, Number.isFinite(v) ? v : 1));
}

export class PlayerEngine {
  private state: PlayerState;
  private readonly listeners = new Set<() => void>();
  private readonly media: Partial<Record<MediaKind, HTMLMediaElement>> = {};
  private loadToken = 0;
  private partIndex = 0;
  private playable: Playable | null = null;
  private saveTimer: unknown = null;
  private lastSaved = -1;
  private pendingSeek: number | null = null;
  /** a converted stream starts where it was asked to: its own 0 is this far in */
  private streamOffset = 0;

  constructor(private readonly deps: EngineDeps) {
    this.state = {
      queue: Q.EMPTY_QUEUE,
      item: null,
      kind: null,
      status: 'idle',
      error: null,
      position: 0,
      duration: 0,
      buffered: 0,
      volume: clampVolume(Number(deps.store.get('volume') ?? 1)),
      muted: deps.store.get('muted') === '1',
      rate: 1,
      parts: [],
      chapters: [],
      verdict: null,
      sleep: null,
      live: false,
      phase: null,
      phaseSince: 0,
      source: null,
      transcode: null,
      quality: (QUALITIES as string[]).includes(deps.store.get('quality') ?? '')
        ? (deps.store.get('quality') as Quality)
        : 'original',
    };
    this.bindMediaSession();
  }

  // ── state ────────────────────────────────────────────────────────────

  getState = (): PlayerState => this.state;

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  private set(patch: Partial<PlayerState>) {
    this.state = { ...this.state, ...patch };
    for (const l of this.listeners) l();
  }

  /** the element for a kind, made the first time it's needed */
  element(kind: MediaKind): HTMLMediaElement {
    let el = this.media[kind];
    if (!el) {
      el = this.deps.createMedia(kind);
      el.preload = 'auto';
      this.bind(el, kind);
      this.media[kind] = el;
    }
    return el;
  }

  private active(): HTMLMediaElement | null {
    return this.state.kind ? this.element(this.state.kind) : null;
  }

  // ── queue commands ───────────────────────────────────────────────────

  /** play these, from ``start`` (a whole album, a season, one movie) */
  playItems(items: PlayItem[], start = 0, options: PlayOptions = {}) {
    this.saveNow();
    this.set({ queue: Q.play(this.state.queue, items, start) });
    void this.load(options);
  }

  playNext(items: PlayItem[]) {
    const empty = !this.state.item;
    this.set({ queue: Q.playNext(this.state.queue, items) });
    if (empty) void this.load();
  }

  append(items: PlayItem[]) {
    const empty = !this.state.item;
    this.set({ queue: Q.append(this.state.queue, items) });
    if (empty) void this.load();
  }

  jump(index: number) {
    this.saveNow();
    this.set({ queue: Q.goTo(this.state.queue, index) });
    void this.load();
  }

  next() {
    const to = Q.nextIndex(this.state.queue, false);
    if (to !== null) this.jump(to);
  }

  /** a few seconds in, previous restarts the item; at its start, it goes back */
  previous() {
    if (this.state.position > 3 && !this.isLongForm()) {
      this.seek(0);
      return;
    }
    const to = Q.previousIndex(this.state.queue);
    if (to === null) this.seek(0);
    else this.jump(to);
  }

  remove(index: number) {
    this.set({ queue: Q.remove(this.state.queue, index) });
  }

  move(from: number, to: number) {
    this.set({ queue: Q.move(this.state.queue, from, to) });
  }

  setRepeat(repeat: RepeatMode) {
    this.set({ queue: Q.setRepeat(this.state.queue, repeat) });
  }

  cycleRepeat() {
    this.setRepeat(Q.REPEAT_CYCLE[this.state.queue.repeat]);
  }

  toggleShuffle() {
    this.set({ queue: Q.setShuffle(this.state.queue, !this.state.queue.shuffle, this.deps.now()) });
  }

  // ── transport commands ───────────────────────────────────────────────

  toggle() {
    if (this.state.status === 'playing' || this.state.status === 'buffering') this.pause();
    else this.resume();
  }

  pause() {
    this.active()?.pause();
  }

  resume() {
    const el = this.active();
    if (!el) return;
    if (this.state.status === 'ended') {
      this.seek(0);
    }
    void el.play().catch(() => this.set({ status: 'paused' }));
  }

  /** seconds on the item's clock; a book may move to another part */
  seek(to: number) {
    const el = this.active();
    if (!el || !this.state.item) return;
    const target = Math.max(0, Math.min(to, this.state.duration || to));
    const { index, offset } = locate(this.state.parts, target);
    if (this.playable?.transcode) {
      // a converted stream seeks inside what's arrived; past it, a new one starts there
      const inside = target >= this.streamOffset && target <= this.state.buffered;
      if (inside) el.currentTime = target - this.streamOffset;
      else void this.loadPart(0, target, !el.paused);
    } else if (this.state.parts.length > 1 && index !== this.partIndex) {
      void this.loadPart(index, offset, !el.paused);
    } else {
      el.currentTime = offset;
    }
    this.set({ position: target });
    if (!el.paused) this.setPhase('seeking');
  }

  skip(delta: number) {
    this.seek(this.state.position + delta);
  }

  nextChapter() {
    const to = nextChapterStart(this.state.chapters, this.state.position);
    if (to !== null) this.seek(to);
  }

  previousChapter() {
    this.seek(previousChapterStart(this.state.chapters, this.state.position));
  }

  setVolume(volume: number) {
    const v = clampVolume(volume);
    for (const el of Object.values(this.media)) if (el) el.volume = v;
    this.deps.store.set('volume', String(v));
    this.set({ volume: v });
    // turning it up is turning it on
    if (v > 0 && this.state.muted) this.setMuted(false);
  }

  setMuted(muted: boolean) {
    for (const el of Object.values(this.media)) if (el) el.muted = muted;
    this.deps.store.set('muted', muted ? '1' : '0');
    this.set({ muted });
  }

  /** speed, remembered per kind of item (books at 1.5x, films at 1x) */
  setRate(rate: number) {
    const r = Math.min(3, Math.max(0.5, rate));
    const el = this.active();
    if (el) el.playbackRate = r;
    if (this.state.item) this.deps.store.set(`rate:${this.state.item.type}`, String(r));
    this.set({ rate: r });
  }

  /** the video quality: kept on this device, applied now at the same spot */
  setQuality(quality: Quality) {
    if (quality === this.state.quality) return;
    this.deps.store.set('quality', quality);
    this.set({ quality });
    if (this.state.kind === 'video' && this.state.item)
      void this.load({ startAt: this.state.position, live: this.state.live });
  }

  /** stop after ``minutes``, or at the end of this item (null: off) */
  setSleep(minutes: number | 'end' | null) {
    this.set({
      sleep:
        minutes === null
          ? null
          : minutes === 'end'
            ? { endOfItem: true }
            : { until: this.deps.now() + minutes * 60_000 },
    });
  }

  /** everything off: save where it was, empty the queue */
  stop() {
    this.saveNow();
    this.loadToken++;
    for (const el of Object.values(this.media)) {
      if (!el) continue;
      el.pause();
      el.removeAttribute('src');
      el.load();
    }
    this.stopSaving();
    this.playable = null;
    this.set({
      queue: Q.EMPTY_QUEUE,
      item: null,
      kind: null,
      status: 'idle',
      error: null,
      position: 0,
      duration: 0,
      buffered: 0,
      parts: [],
      chapters: [],
      verdict: null,
      sleep: null,
      live: false,
      phase: null,
      phaseSince: 0,
      source: null,
      transcode: null,
    });
  }

  private setPhase(phase: Phase) {
    if (phase !== this.state.phase) this.set({ phase, phaseSince: this.deps.now() });
  }

  // ── loading ──────────────────────────────────────────────────────────

  private isLongForm(item = this.state.item) {
    return !!item && LONG_FORM.has(item.type);
  }

  private rateFor(type: ItemType): number {
    const saved = Number(this.deps.store.get(`rate:${type}`));
    return Number.isFinite(saved) && saved >= 0.5 && saved <= 3 ? saved : 1;
  }

  private async load(options: PlayOptions = {}) {
    const item = Q.current(this.state.queue);
    const token = ++this.loadToken;
    if (!item) {
      this.stop();
      return;
    }
    const kind = MEDIA_KIND[item.type];
    // the other element stops: one thing plays at a time
    for (const [k, el] of Object.entries(this.media))
      if (el && k !== kind) {
        el.pause();
      }
    this.lastSaved = -1;
    this.set({
      item,
      kind,
      status: 'loading',
      error: null,
      position: options.startAt ?? 0,
      duration: item.duration ?? 0,
      buffered: 0,
      parts: [],
      chapters: [],
      verdict: null,
      live: !!options.live,
      source: null,
      transcode: null,
      rate: this.isLongForm(item) ? this.rateFor(item.type) : 1,
    });
    this.setPhase('locating');
    let playable: Playable;
    let saved: SavedPosition | null = null;
    try {
      [playable, saved] = await Promise.all([
        this.deps.playable(item, this.state.quality),
        options.startAt !== undefined || options.live || !this.isLongForm(item)
          ? Promise.resolve(null)
          : this.deps.progress(item).catch(() => null),
      ]);
    } catch (error) {
      if (token !== this.loadToken) return;
      const timedOut = error instanceof Error && error.name === 'TimeoutError';
      this.set({
        status: 'error',
        phase: null,
        error: timedOut
          ? "The drive this is on didn't answer in time. Try again in a moment, it may still be waking up."
          : error instanceof Error
            ? error.message
            : String(error),
      });
      return;
    }
    if (token !== this.loadToken) return;
    if (playable.verdict?.verdict === 'no' && !playable.transcode) {
      // can't play as it is, and the server can't convert it: say so, never a silent picture
      this.set({
        status: 'error',
        phase: null,
        verdict: playable.verdict,
        error:
          playable.transcode_unavailable ??
          "This file's format can't play in a browser. Pick another version.",
      });
      return;
    }
    this.playable = playable;
    const parts = playable.parts?.length
      ? playable.parts
      : [{ index: 0, start: 0, duration: playable.duration ?? 0 }];
    const duration = playable.duration ?? item.duration ?? 0;
    const start =
      options.startAt ??
      (saved && !saved.finished && saved.position > RESUME_MIN ? saved.position : 0);
    this.set({
      parts: playable.parts ?? [],
      chapters: playable.chapters ?? [],
      verdict: playable.verdict ?? null,
      duration,
      position: start,
      source: { via: playable.via ?? 'disk', tookMs: playable.took_ms ?? null },
      transcode: playable.transcode ?? null,
    });
    this.setPhase(start > 0 ? 'resuming' : 'opening');
    const at = locate(parts, start);
    await this.loadPart(at.index, at.offset, true, token);
    this.setMediaSessionMetadata(item);
  }

  /** point the element at one part (the whole item, for most things) */
  private async loadPart(index: number, offset: number, autoplay: boolean, token = this.loadToken) {
    const item = this.state.item;
    const playable = this.playable;
    if (!item || !playable || !this.state.kind) return;
    const el = this.element(this.state.kind);
    this.partIndex = index;
    const multi = (playable.parts?.length ?? 0) > 1;
    el.volume = this.state.volume;
    el.muted = this.state.muted;
    if (playable.transcode) {
      // the converted stream is made from ``offset`` on: its 0 is that far in.
      // copied video can only start at a keyframe: ask where, start exactly there
      let start = offset;
      let seek = offset;
      if (offset > 0 && playable.transcode.video === 'copy') {
        try {
          ({ start, seek } = await this.deps.transcodeStart(playable.url, offset));
        } catch {
          /* unanswered: start where asked; the clock may be off by a keyframe */
        }
        if (token !== this.loadToken) return;
      }
      this.streamOffset = start;
      this.pendingSeek = null;
      el.src = withParam(playable.url, 't', seek > 0 ? seek.toFixed(2) : null);
    } else {
      this.streamOffset = 0;
      const src = multi ? withPart(playable.url, index) : playable.url;
      if (el.getAttribute('src') !== src) {
        el.src = src;
        this.pendingSeek = offset > 0 ? offset : null;
      } else {
        el.currentTime = offset;
      }
    }
    el.playbackRate = this.state.rate;
    if (!autoplay) return;
    try {
      await el.play();
    } catch {
      // the browser wants a click first (autoplay rules): it waits, paused
      if (token === this.loadToken) this.set({ status: 'paused' });
    }
  }

  // ── the element's own events ─────────────────────────────────────────

  private bind(el: HTMLMediaElement, kind: MediaKind) {
    const mine = () => this.state.kind === kind;
    el.addEventListener('loadedmetadata', () => {
      if (!mine()) return;
      if (this.pendingSeek !== null) {
        el.currentTime = this.pendingSeek;
        this.pendingSeek = null;
      }
      el.playbackRate = this.state.rate;
      if (!this.state.duration && Number.isFinite(el.duration)) this.set({ duration: el.duration });
    });
    el.addEventListener('timeupdate', () => {
      if (!mine()) return;
      const position =
        this.streamOffset + bookTime(this.state.parts, this.partIndex, el.currentTime);
      this.set({ position });
      this.checkSleep();
      this.updatePositionState();
    });
    el.addEventListener('progress', () => {
      if (!mine() || !el.buffered.length) return;
      this.set({
        buffered:
          this.streamOffset +
          bookTime(this.state.parts, this.partIndex, el.buffered.end(el.buffered.length - 1)),
      });
    });
    el.addEventListener('playing', () => {
      if (!mine()) return;
      this.set({ status: 'playing' });
      this.setPhase(null);
      this.startSaving();
    });
    el.addEventListener('waiting', () => {
      if (!mine()) return;
      this.set({ status: 'buffering' });
      if (
        this.state.phase !== 'seeking' &&
        this.state.phase !== 'resuming' &&
        this.state.phase !== 'opening'
      )
        this.setPhase('buffering');
    });
    el.addEventListener('seeked', () => {
      if (mine() && this.state.phase === 'seeking' && !el.paused) this.setPhase(null);
    });
    el.addEventListener('pause', () => {
      if (!mine() || el.ended) return;
      this.set({ status: 'paused' });
      this.setPhase(null);
      this.saveNow();
      this.stopSaving();
    });
    el.addEventListener('ended', () => mine() && this.onEnded());
    el.addEventListener('error', () => {
      if (!mine() || !this.state.item) return;
      // a 'maybe' file this browser couldn't play after all: the converted stream, same spot
      const p = this.playable;
      if (p?.fallback_url && !p.transcode) {
        this.playable = {
          ...p,
          url: p.fallback_url,
          fallback_url: undefined,
          transcode: {
            video: 'h264',
            audio: 'aac',
            reasons: ["this browser couldn't play it as it is"],
          },
        };
        this.set({ transcode: this.playable.transcode ?? null });
        this.setPhase('opening');
        void this.loadPart(0, this.state.position, true);
        return;
      }
      this.set({
        status: 'error',
        error: "This couldn't be played. The file may be gone or unreadable.",
      });
    });
  }

  private onEnded() {
    const parts = this.state.parts;
    if (parts.length > 1 && this.partIndex + 1 < parts.length) {
      void this.loadPart(this.partIndex + 1, 0, true);
      return;
    }
    this.save(true);
    this.stopSaving();
    if (this.state.sleep && 'endOfItem' in this.state.sleep) {
      this.set({ status: 'ended', sleep: null });
      return;
    }
    const to = Q.nextIndex(this.state.queue, true);
    if (to === null) {
      this.set({ status: 'ended' });
      return;
    }
    this.set({ queue: Q.goTo(this.state.queue, to) });
    void this.load();
  }

  private checkSleep() {
    const sleep = this.state.sleep;
    if (sleep && 'until' in sleep && this.deps.now() >= sleep.until) {
      this.set({ sleep: null });
      this.pause();
    }
  }

  // ── progress ─────────────────────────────────────────────────────────

  private startSaving() {
    if (this.saveTimer !== null || !this.isLongForm() || this.state.live) return;
    this.saveTimer = this.deps.setInterval(() => this.save(false), SAVE_EVERY_MS);
  }

  private stopSaving() {
    if (this.saveTimer === null) return;
    this.deps.clearInterval(this.saveTimer);
    this.saveTimer = null;
  }

  /** save now (pause, page hidden, another item): the caller doesn't wait */
  saveNow() {
    this.save(false);
  }

  private save(finished: boolean) {
    const { item, position, duration, live } = this.state;
    if (!item || live || !this.isLongForm(item)) return;
    // nothing new since the last save
    if (!finished && Math.abs(position - this.lastSaved) < 1) return;
    if (!finished && position < 1) return;
    this.lastSaved = position;
    this.deps.save(item, position, duration || null, finished);
  }

  // ── the lock screen and media keys ───────────────────────────────────

  private bindMediaSession() {
    const ms = this.deps.mediaSession;
    if (!ms) return;
    const on = (action: MediaSessionAction, fn: MediaSessionActionHandler) => {
      try {
        ms.setActionHandler(action, fn);
      } catch {
        /* this browser doesn't have that action */
      }
    };
    on('play', () => this.resume());
    on('pause', () => this.pause());
    on('previoustrack', () => this.previous());
    on('nexttrack', () => this.next());
    on('seekbackward', (d) => this.skip(-(d.seekOffset ?? 10)));
    on('seekforward', (d) => this.skip(d.seekOffset ?? 30));
    on('seekto', (d) => d.seekTime !== undefined && this.seek(d.seekTime));
    on('stop', () => this.stop());
  }

  private setMediaSessionMetadata(item: PlayItem) {
    const ms = this.deps.mediaSession;
    if (!ms || typeof MediaMetadata === 'undefined') return;
    const art = item.artwork ?? item.backdrop;
    ms.metadata = new MediaMetadata({
      title: item.title,
      artist: item.subtitle ?? '',
      album: item.parentTitle ?? '',
      artwork: art ? [{ src: art, sizes: '512x512' }] : [],
    });
  }

  private updatePositionState() {
    const ms = this.deps.mediaSession;
    const { duration, position, rate } = this.state;
    if (!ms?.setPositionState || !duration || position > duration) return;
    try {
      ms.setPositionState({ duration, position, playbackRate: rate });
    } catch {
      /* a stale duration: the next update fixes it */
    }
  }
}

function withParam(url: string, key: string, value: string | null) {
  const u = url.replace(new RegExp(`([?&])${key}=[^&]*`), '').replace(/[?&]$/, '');
  return value === null ? u : `${u}${u.includes('?') ? '&' : '?'}${key}=${value}`;
}

function withPart(url: string, index: number) {
  const u = url.replace(/([?&])part=[^&]*/, '').replace(/[?&]$/, '');
  return `${u}${u.includes('?') ? '&' : '?'}part=${index}`;
}
