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
import type { Tracks, TrackPrefs } from './tracks';
import type { Chapter, ItemType, MediaKind, Part, PlayItem, RepeatMode } from './types';
import type { Cue } from './vtt';

import * as Q from './queue';
import { bookTime, locate, nextChapterStart, previousChapterStart } from './timeline';
import { pickAudio, pickSubtitle } from './tracks';
import { LONG_FORM, MEDIA_KIND } from './types';
import { parseVtt } from './vtt';

export type Status = 'idle' | 'loading' | 'playing' | 'paused' | 'buffering' | 'ended' | 'error';

export interface Verdict {
  verdict: 'yes' | 'maybe' | 'no';
  reasons?: string[];
  /** what was judged: '.mkv', 'hevc', 'ac3' */
  container?: string;
  video?: string;
  audio?: string;
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
  /** the file version playing, and its facts (for stats for nerds) */
  file_id?: number;
  file?: FileFacts;
  mime?: string;
  duration?: number | null;
  parts?: Part[];
  chapters?: Chapter[];
  verdict?: Verdict | null;
}

export interface SavedPosition {
  position: number;
  finished: boolean;
}

export interface EngineDeps {
  createMedia: (kind: MediaKind) => HTMLMediaElement;
  playable: (item: PlayItem, quality: Quality, pick: TrackPick) => Promise<Playable>;
  /** a video's audio and subtitle tracks */
  tracks: (item: PlayItem) => Promise<Tracks>;
  /** a text subtitle as WebVTT */
  subtitle: (item: PlayItem, id: string) => Promise<string>;
  progress: (item: PlayItem) => Promise<SavedPosition | null>;
  /**
   * where a converted stream with copied video really starts if asked for
   * ``t`` (a keyframe), and the ``t`` to request so it lands there
   */
  transcodeStart: (url: string, t: number) => Promise<{ start: number; seek: number }>;
  save: (item: PlayItem, position: number, duration: number | null, finished: boolean) => void;
  /** a movie or episode is into its credits: mark it watched (library + media server) */
  watched?: (item: PlayItem) => void;
  store: { get: (key: string) => string | null; set: (key: string, value: string) => void };
  now: () => number;
  /** amplify an element past full volume (a web audio gain); absent: no boost */
  boost?: (el: HTMLMediaElement, gain: number) => void;
  /** what's playing as sound, for visuals that follow it; absent or null: none */
  analyser?: (el: HTMLMediaElement) => AnalyserNode | null;
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
  /** this video's tracks (null until read, or for audio) */
  tracks: Tracks | null;
  /** the audio track playing (its number among the file's audio) */
  audioTrack: number;
  /** the subtitle showing ('s0', 'x1'), or null */
  subtitle: string | null;
  /** a text subtitle's cues, drawn by the player */
  cues: Cue[];
  /** reading a subtitle (an embedded one means the whole file, once) */
  subtitleLoading: boolean;
  /** audio sync for this item, in ms (later +, earlier -) */
  audioDelay: number;
  /** subtitle sync for this item, in ms (later +, earlier -) */
  subtitleDelay: number;
  /** the last movie or episode marked watched ("type:id"), so pages can refresh */
  watchedKey: string | null;
  /** episodes played on their own, one after another: is anyone still there? */
  stillWatching: boolean;
  /** a video plays on into the next (the profile's setting) */
  autoplayNext: boolean;
}

/** this many videos in a row with nobody touching anything, then ask before the next */
export const STILL_WATCHING_AFTER = 3;

/** a movie or episode counts as watched this far through (into the credits, like plex) */
export const WATCHED_AT = 0.92;

/** the audio track, and a picture subtitle drawn in by the converter */
export interface TrackPick {
  audio: number;
  burn: number | null;
  /** audio sync: the sound moved this many ms (later +, earlier -) */
  delay?: number;
}

/** how loud it can go: past 1, the sound is amplified (a gain node) */
export const MAX_VOLUME = 1.5;

export type Phase = 'locating' | 'opening' | 'resuming' | 'seeking' | 'buffering' | null;

export interface SourceInfo {
  via: string;
  tookMs: number | null;
  /** the file version playing, and what stats for nerds shows about it */
  fileId?: number | null;
  file?: FileFacts | null;
  mime?: string | null;
}

export interface FileFacts {
  height?: number | null;
  size_bytes?: number | null;
  dynamic_range?: string | null;
  runtime_seconds?: number | null;
}

export interface PlayOptions {
  /** start here (seconds on the item's clock) instead of where it was left */
  startAt?: number;
  /** a guide channel: no progress saved */
  live?: boolean;
  /** the same item again (a track, a quality): keep its tracks and picks */
  keepTracks?: boolean;
}

const SAVE_EVERY_MS = 10_000;
/** within this of the start, there's nothing worth resuming */
const RESUME_MIN = 15;

function clampVolume(v: number) {
  return Math.min(MAX_VOLUME, Math.max(0, Number.isFinite(v) ? v : 1));
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
  /** what the converter is asked for (another audio track, a subtitle drawn in) */
  private pick: TrackPick = { audio: 0, burn: null };
  /** the item the tracks above were read for */
  private tracksFor: string | null = null;

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
      tracks: null,
      audioTrack: 0,
      subtitle: null,
      cues: [],
      subtitleLoading: false,
      audioDelay: 0,
      subtitleDelay: 0,
      watchedKey: null,
      stillWatching: false,
      autoplayNext: true,
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
    this.chosen();
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
    this.chosen();
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
    for (const el of Object.values(this.media)) if (el) this.applyVolume(el, v);
    this.deps.store.set('volume', String(v));
    this.set({ volume: v });
    // turning it up is turning it on
    if (v > 0 && this.state.muted) this.setMuted(false);
  }

  /** the playing element's analyser (made on first ask, from a click), or null */
  analyser(): AnalyserNode | null {
    const el = this.active();
    return el && this.deps.analyser ? this.deps.analyser(el) : null;
  }

  /** an element at ``v``: up to full on the element, past it through the boost */
  private applyVolume(el: HTMLMediaElement, v: number) {
    el.volume = Math.min(1, v);
    if (v > 1 || this.boosted.has(el)) {
      this.deps.boost?.(el, Math.max(1, v));
      this.boosted.add(el);
    }
  }

  private readonly boosted = new Set<HTMLMediaElement>();

  /** audio sync for this video: kept for it, applied at the same spot (through the converter) */
  setAudioDelay(ms: number) {
    const item = this.state.item;
    if (!item || this.state.kind !== 'video') return;
    const delay = Math.max(-10_000, Math.min(10_000, Math.round(ms)));
    if (delay === (this.pick.delay ?? 0)) return;
    this.deps.store.set(`delay:${item.type}:${item.id}`, String(delay));
    this.pick = { ...this.pick, delay };
    this.set({ audioDelay: delay });
    void this.load({ startAt: this.state.position, live: this.state.live, keepTracks: true });
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

  private prefs(): TrackPrefs {
    return { audio: this.deps.store.get('audioLang'), subtitle: this.deps.store.get('subLang') };
  }

  /** another audio track: through the converter (a browser plays the first only), same spot */
  setAudioTrack(index: number) {
    const track = this.state.tracks?.audio.find((t) => t.index === index);
    if (!track || index === this.state.audioTrack) return;
    if (track.language) this.deps.store.set('audioLang', track.language);
    this.pick = { ...this.pick, audio: index };
    this.set({ audioTrack: index });
    void this.load({ startAt: this.state.position, live: this.state.live, keepTracks: true });
  }

  /** a subtitle, or none: text is drawn by the player, pictures by the converter */
  setSubtitle(id: string | null) {
    const subs = this.state.tracks?.subtitles ?? [];
    const pick = id ? subs.find((s) => s.id === id) : null;
    if (id && !pick) return;
    this.deps.store.set('subLang', pick?.language ?? 'off');
    const burn = pick?.kind === 'image' && !pick.external ? Number(pick.id.slice(1)) : null;
    const reload = burn !== this.pick.burn;
    this.pick = { ...this.pick, burn };
    this.set({ subtitle: pick?.id ?? null, cues: [] });
    if (reload)
      void this.load({ startAt: this.state.position, live: this.state.live, keepTracks: true });
    if (pick?.kind === 'text') void this.loadCues(pick.id);
  }

  private async loadCues(id: string) {
    const item = this.state.item;
    if (!item) return;
    this.set({ subtitleLoading: true });
    try {
      const cues = parseVtt(await this.deps.subtitle(item, id));
      if (this.state.item === item && this.state.subtitle === id) this.set({ cues });
    } catch {
      if (this.state.item === item && this.state.subtitle === id)
        this.set({ subtitle: null, cues: [] });
    } finally {
      if (this.state.item === item) this.set({ subtitleLoading: false });
    }
  }

  /** subtitle sync: the player draws them, so it's only a shift of its own clock. kept for the title */
  setSubtitleDelay(ms: number) {
    const item = this.state.item;
    if (!item) return;
    const delay = Math.max(-30_000, Math.min(30_000, Math.round(ms)));
    this.deps.store.set(`subdelay:${item.type}:${item.id}`, String(delay));
    this.set({ subtitleDelay: delay });
  }

  /**
   * read the tracks again (a subtitle was just downloaded beside the file).
   * ``language``: switch to the newest subtitle file in it
   */
  async refreshTracks(language?: string) {
    const item = this.state.item;
    if (!item || this.state.kind !== 'video') return;
    const tracks = await this.deps.tracks(item).catch(() => null);
    if (!tracks || this.state.item !== item) return;
    this.set({ tracks });
    if (language) {
      const fresh = tracks.subtitles.filter((t) => t.external && t.language === language).at(-1);
      if (fresh) this.setSubtitle(fresh.id);
    }
  }

  /** the video quality: kept on this device, applied now at the same spot */
  setQuality(quality: Quality) {
    if (quality === this.state.quality) return;
    this.deps.store.set('quality', quality);
    this.set({ quality });
    if (this.state.kind === 'video' && this.state.item)
      void this.load({ startAt: this.state.position, live: this.state.live, keepTracks: true });
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
      stillWatching: false,
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
      tracks: null,
      audioTrack: 0,
      subtitle: null,
      cues: [],
      subtitleLoading: false,
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
      // the same item again (a track or quality change): its length is still its length
      duration: item.duration ?? (options.keepTracks ? this.state.duration : 0),
      buffered: 0,
      parts: [],
      chapters: [],
      verdict: null,
      live: !!options.live,
      source: null,
      transcode: null,
      ...(options.keepTracks
        ? {}
        : { tracks: null, audioTrack: 0, subtitle: null, cues: [], subtitleLoading: false }),
      rate: this.isLongForm(item) ? this.rateFor(item.type) : 1,
    });
    this.setPhase('locating');
    const key = `${item.type}:${item.id}:${item.part ?? ''}`;
    if (!options.keepTracks || this.tracksFor !== key) {
      const saved = Number(this.deps.store.get(`delay:${item.type}:${item.id}`)) || 0;
      this.pick = { audio: 0, burn: null, delay: kind === 'video' ? saved : 0 };
      this.tracksFor = null;
      this.set({
        audioDelay: this.pick.delay ?? 0,
        subtitleDelay: Number(this.deps.store.get(`subdelay:${item.type}:${item.id}`)) || 0,
      });
    }
    // a video's tracks first: the saved language picks which audio to ask for
    if (kind === 'video' && this.tracksFor !== key) {
      const tracks = await this.deps.tracks(item).catch(() => null);
      if (token !== this.loadToken) return;
      this.tracksFor = key;
      if (tracks) {
        const prefs = this.prefs();
        const audio = pickAudio(tracks.audio, prefs);
        const audioLang = tracks.audio.find((t) => t.index === audio)?.language ?? null;
        const subtitle = pickSubtitle(tracks.subtitles, prefs, audioLang);
        const sub = tracks.subtitles.find((t) => t.id === subtitle);
        this.pick = {
          ...this.pick,
          audio,
          burn: sub?.kind === 'image' && !sub.external ? Number(sub.id.slice(1)) : null,
        };
        this.set({ tracks, audioTrack: audio, subtitle });
        if (sub?.kind === 'text') void this.loadCues(sub.id);
      }
    }
    let playable: Playable;
    let saved: SavedPosition | null = null;
    try {
      [playable, saved] = await Promise.all([
        this.deps.playable(item, this.state.quality, this.pick),
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
    const duration =
      playable.duration ?? item.duration ?? (options.keepTracks ? this.state.duration : 0);
    const start =
      options.startAt ??
      (saved && !saved.finished && saved.position > RESUME_MIN ? saved.position : 0);
    this.set({
      parts: playable.parts ?? [],
      chapters: playable.chapters ?? [],
      verdict: playable.verdict ?? null,
      duration,
      position: start,
      source: {
        via: playable.via ?? 'disk',
        tookMs: playable.took_ms ?? null,
        fileId: playable.file_id ?? null,
        file: playable.file ?? null,
        mime: playable.mime ?? null,
      },
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
    this.applyVolume(el, this.state.volume);
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
      // the file's own length beats a listed runtime (a cut without ads runs short);
      // a converted stream's element only knows what's arrived, so not that
      const real = Number.isFinite(el.duration) && el.duration > 0;
      const direct = !this.playable?.transcode && this.state.parts.length <= 1;
      if (real && (!this.state.duration || direct)) this.set({ duration: el.duration });
    });
    el.addEventListener('timeupdate', () => {
      if (!mine()) return;
      const position =
        this.streamOffset + bookTime(this.state.parts, this.partIndex, el.currentTime);
      this.set({ position });
      this.checkWatched(position);
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

  /** once per play: into the credits of a movie or episode, it's watched */
  private checkWatched(position: number) {
    const item = this.state.item;
    const duration = this.state.duration;
    if (!item || (item.type !== 'movie' && item.type !== 'episode')) return;
    if (!duration || position < duration * WATCHED_AT) return;
    const key = `${item.type}:${item.id}`;
    if (this.state.watchedKey === key) return;
    this.set({ watchedKey: key });
    this.deps.watched?.(item);
  }

  /** someone moved, clicked or pressed a key: they're still there */
  activity() {
    this.autoRun = 0;
  }

  /** a person picked what plays next: the count starts over, any question is answered */
  private chosen() {
    this.autoRun = 0;
    if (this.state.stillWatching) this.set({ stillWatching: false });
  }

  private autoRun = 0;

  /** the profile's playback settings (player settings): a video plays on into the next, and when to ask */
  private prefsAuto = { autoplayNext: true, stillWatchingAfter: STILL_WATCHING_AFTER };

  configure(next: { autoplayNext?: boolean; stillWatchingAfter?: number }) {
    this.prefsAuto = { ...this.prefsAuto, ...next };
    this.set({ autoplayNext: this.prefsAuto.autoplayNext });
  }

  /** the answer to "are you still watching?": yes, play the next one */
  keepWatching() {
    const to = Q.nextIndex(this.state.queue, true);
    this.chosen();
    if (to === null) return;
    this.set({ queue: Q.goTo(this.state.queue, to) });
    void this.load();
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
    if (this.state.kind === 'video') {
      // autoplay off: stop here, the end screen offers the next one
      if (!this.prefsAuto.autoplayNext) {
        this.set({ status: 'ended' });
        return;
      }
      // a run of videos nobody has touched: ask before the next one
      const ask = this.prefsAuto.stillWatchingAfter;
      if (ask > 0 && this.autoRun >= ask) {
        this.set({ status: 'ended', stillWatching: true });
        return;
      }
      this.autoRun++;
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
