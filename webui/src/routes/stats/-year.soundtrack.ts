import type { PlayableTrack } from './-year.api';

/**
 * The story's soundtrack: the year's top songs you own, played in full
 * through the app's own player (never a preview), each dropped in at a good
 * point and faded into the next.
 *
 * The player owns the queue and the audio element; this only drives them.
 * It plays one song at a time through `window.playTrackList`, seeks to the
 * server's start point, and ramps the element's volume for the fades. When
 * the story closes it fades out and stops, but only if the player is still on
 * our song: if the reader started something else, that is left alone.
 */

export interface SoundtrackTrack extends PlayableTrack {
  start_ms: number;
  duration_ms: number;
  plays?: number;
}

/** How long each song plays before fading into the next. */
export const SEGMENT_MS = 40_000;
export const FADE_MS = 1_800;

export type SoundtrackState = 'idle' | 'starting' | 'playing' | 'blocked' | 'stopped';

export interface SoundtrackDeps {
  play: (tracks: unknown[], context: string) => Promise<unknown> | unknown;
  audio: () => HTMLAudioElement | null;
  setTimer?: (fn: () => void, ms: number) => unknown;
  clearTimer?: (handle: unknown) => void;
  frame?: (fn: (now: number) => void) => unknown;
  cancelFrame?: (handle: unknown) => void;
}

/** The volume `elapsed` ms into a fade from `from` to `to`. Eased, clamped. */
export function fadeVolume(elapsed: number, ms: number, from: number, to: number): number {
  if (ms <= 0 || elapsed >= ms) return clampVolume(to);
  const t = Math.max(0, elapsed) / ms;
  const eased = t * t * (3 - 2 * t);
  return clampVolume(from + (to - from) * eased);
}

function clampVolume(v: number): number {
  return Math.max(0, Math.min(1, Number.isFinite(v) ? v : 0));
}

/** A start point the element can actually seek to: inside the song, in seconds. */
export function seekSeconds(track: Pick<SoundtrackTrack, 'start_ms'>, duration: number): number {
  const at = Math.max(0, (track.start_ms || 0) / 1000);
  if (!Number.isFinite(duration) || duration <= 0) return at;
  return at < duration - 5 ? at : 0;
}

/** Start straight away only when the player is idle: never cut off what's on. */
export function shouldAutostart(audio: Pick<HTMLAudioElement, 'paused' | 'src'> | null): boolean {
  return !audio || audio.paused || !audio.src;
}

export class StorySoundtrack {
  state: SoundtrackState = 'idle';
  index = -1;
  muted = false;
  private tracks: SoundtrackTrack[] = [];
  private baseVolume = 1;
  private ourSrc = '';
  private timer: unknown = null;
  private fadeHandle: unknown = null;
  private listeners = new Set<() => void>();
  private readonly deps: Required<SoundtrackDeps>;

  constructor(deps: SoundtrackDeps) {
    this.deps = {
      setTimer: (fn, ms) => setTimeout(fn, ms),
      clearTimer: (h) => clearTimeout(h as ReturnType<typeof setTimeout>),
      frame: (fn) => requestAnimationFrame(fn),
      cancelFrame: (h) => cancelAnimationFrame(h as number),
      ...deps,
    };
  }

  subscribe(fn: () => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  private emit() {
    for (const fn of this.listeners) fn();
  }

  get current(): SoundtrackTrack | null {
    return this.tracks[this.index] ?? null;
  }

  /** Begin with the first song. False when there is nothing to play or the
   *  browser refused to start audio (the reader can press play instead). */
  async start(tracks: SoundtrackTrack[]): Promise<boolean> {
    this.tracks = tracks.filter((t) => t && t.file_path);
    if (!this.tracks.length) return false;
    const audio = this.deps.audio();
    this.baseVolume = audio && audio.volume > 0 ? audio.volume : 1;
    return this.playAt(0);
  }

  private async playAt(index: number): Promise<boolean> {
    const track = this.tracks[index];
    if (!track) return false;
    this.index = index;
    this.state = 'starting';
    this.emit();
    let result: unknown;
    try {
      result = await this.deps.play([track], 'Your Year');
    } catch {
      result = { status: 'error' };
    }
    const status = (result as { status?: string } | undefined)?.status;
    const audio = this.deps.audio();
    // the player answers 'played' once the song is going
    if ((status && status !== 'played') || !audio) {
      // a refused autoplay, or the player could not load it
      if (!this.stopped()) this.state = 'blocked';
      this.emit();
      return false;
    }
    // closed while the player was loading: it's our song, so stop it
    if (this.stopped()) {
      audio.pause();
      audio.volume = this.baseVolume;
      return false;
    }
    this.ourSrc = audio.currentSrc || audio.src;
    audio.muted = this.muted;
    this.seekWhenReady(audio, track);
    this.fade(audio, 0, this.baseVolume);
    this.state = 'playing';
    this.emit();
    this.schedule();
    return true;
  }

  /** read through a call: TypeScript would otherwise keep the state it saw
   *  before the await, and a stop() can land during it */
  private stopped(): boolean {
    return this.state === 'stopped';
  }

  private seekWhenReady(audio: HTMLAudioElement, track: SoundtrackTrack) {
    const seek = () => {
      try {
        audio.currentTime = seekSeconds(track, audio.duration);
      } catch {
        // a stream that can't seek plays from the top
      }
    };
    if (audio.readyState >= 1) seek();
    else audio.addEventListener('loadedmetadata', seek, { once: true });
  }

  private schedule() {
    this.deps.clearTimer(this.timer);
    if (this.index >= this.tracks.length - 1) return; // the last song plays on
    this.timer = this.deps.setTimer(() => void this.advance(), SEGMENT_MS);
  }

  private async advance() {
    const audio = this.deps.audio();
    if (!audio || !this.isOurs(audio) || this.state !== 'playing') return;
    await this.fadeTo(audio, 0);
    if (this.state !== 'playing') return;
    await this.playAt(this.index + 1);
  }

  private isOurs(audio: HTMLAudioElement): boolean {
    return Boolean(this.ourSrc) && (audio.currentSrc || audio.src) === this.ourSrc;
  }

  private fade(audio: HTMLAudioElement, from: number, to: number) {
    void this.fadeTo(audio, to, from);
  }

  private fadeTo(audio: HTMLAudioElement, to: number, from = audio.volume): Promise<void> {
    this.deps.cancelFrame(this.fadeHandle);
    return new Promise((resolve) => {
      let start: number | null = null;
      const step = (now: number) => {
        if (start === null) start = now;
        const elapsed = now - start;
        audio.volume = fadeVolume(elapsed, FADE_MS, from, to);
        if (elapsed < FADE_MS) this.fadeHandle = this.deps.frame(step);
        else resolve();
      };
      audio.volume = clampVolume(from);
      this.fadeHandle = this.deps.frame(step);
    });
  }

  setMuted(muted: boolean) {
    this.muted = muted;
    const audio = this.deps.audio();
    if (audio && this.isOurs(audio)) audio.muted = muted;
    this.emit();
  }

  /** Fade out and stop, if the player is still on our song; put the volume
   *  and mute back the way the reader had them either way. */
  async stop(): Promise<void> {
    const was = this.state;
    this.state = 'stopped';
    this.deps.clearTimer(this.timer);
    this.emit();
    const audio = this.deps.audio();
    if (!audio || (was !== 'playing' && was !== 'starting')) return;
    if (this.isOurs(audio)) {
      await this.fadeTo(audio, 0);
      audio.pause();
      audio.muted = false;
    }
    audio.volume = this.baseVolume;
  }
}
