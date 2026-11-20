import { describe, expect, it, vi } from 'vitest';

import {
  fadeVolume,
  SEGMENT_MS,
  seekSeconds,
  shouldAutostart,
  StorySoundtrack,
  type SoundtrackTrack,
} from './-year.soundtrack';

function track(i: number, start = 60_000): SoundtrackTrack {
  return {
    id: i,
    title: `Song ${i}`,
    artist: 'A',
    album: 'Al',
    file_path: `/music/${i}.flac`,
    start_ms: start,
    duration_ms: 240_000,
  };
}

/** a stand-in for the player's audio element */
function fakeAudio() {
  return {
    paused: true,
    src: '',
    currentSrc: '',
    volume: 0.8,
    muted: false,
    readyState: 1,
    duration: 240,
    currentTime: 0,
    pause: vi.fn(function (this: { paused: boolean }) {
      this.paused = true;
    }),
    addEventListener: vi.fn(),
  };
}

/** frames run at once, timers are held for the test to fire */
function harness(result: unknown = { status: 'played' }) {
  const audio = fakeAudio();
  const timers: (() => void)[] = [];
  const play = vi.fn(async (tracks: unknown[]) => {
    const t = tracks[0] as SoundtrackTrack;
    audio.src = audio.currentSrc = `/stream/${t.id}`;
    audio.paused = false;
    return result;
  });
  let clock = 0;
  const ctl = new StorySoundtrack({
    play,
    audio: () => audio as unknown as HTMLAudioElement,
    setTimer: (fn) => timers.push(fn),
    clearTimer: () => undefined,
    frame: (fn) => {
      clock += 5_000;
      queueMicrotask(() => fn(clock));
      return 0;
    },
    cancelFrame: () => undefined,
  });
  return { audio, timers, play, ctl };
}

const settle = () => new Promise((r) => setTimeout(r, 0));

describe('the soundtrack', () => {
  it('plays the first song in full through the player, from its start point', async () => {
    const { audio, play, ctl } = harness();
    expect(await ctl.start([track(1), track(2)])).toBe(true);
    await settle();
    expect(play).toHaveBeenCalledWith([expect.objectContaining({ id: 1 })], 'Your Year');
    expect(audio.currentTime).toBe(60);
    expect(ctl.state).toBe('playing');
    // faded up to the volume the reader had
    expect(audio.volume).toBeCloseTo(0.8);
  });

  it('fades into the next song after a segment, and the last one plays on', async () => {
    const { timers, play, ctl } = harness();
    await ctl.start([track(1), track(2)]);
    expect(timers).toHaveLength(1);
    timers[0]();
    await settle();
    await settle();
    expect(play).toHaveBeenLastCalledWith([expect.objectContaining({ id: 2 })], 'Your Year');
    expect(ctl.current?.id).toBe(2);
    expect(timers).toHaveLength(1); // nothing scheduled after the last song
    expect(SEGMENT_MS).toBeGreaterThan(20_000);
  });

  it('mutes and unmutes without stopping', async () => {
    const { audio, ctl } = harness();
    await ctl.start([track(1)]);
    ctl.setMuted(true);
    expect(audio.muted).toBe(true);
    ctl.setMuted(false);
    expect(audio.muted).toBe(false);
  });

  it('closing fades out and stops our song, and puts the volume back', async () => {
    const { audio, ctl } = harness();
    await ctl.start([track(1)]);
    await ctl.stop();
    expect(audio.pause).toHaveBeenCalled();
    expect(audio.volume).toBeCloseTo(0.8);
  });

  it('leaves alone a song the reader started themselves', async () => {
    const { audio, ctl } = harness();
    await ctl.start([track(1)]);
    audio.src = audio.currentSrc = '/stream/something-else';
    await ctl.stop();
    expect(audio.pause).not.toHaveBeenCalled();
  });

  it('a refused start is blocked, so the reader can press play', async () => {
    const { ctl } = harness({ status: 'failed' });
    expect(await ctl.start([track(1)])).toBe(false);
    expect(ctl.state).toBe('blocked');
  });

  it('nothing owned: nothing to play', async () => {
    const { ctl, play } = harness();
    expect(await ctl.start([{ ...track(1), file_path: '' }])).toBe(false);
    expect(play).not.toHaveBeenCalled();
  });
});

describe('soundtrack helpers', () => {
  it('eases a fade and clamps it', () => {
    expect(fadeVolume(0, 1000, 0, 1)).toBe(0);
    expect(fadeVolume(500, 1000, 0, 1)).toBeCloseTo(0.5);
    expect(fadeVolume(2000, 1000, 0, 1)).toBe(1);
    expect(fadeVolume(0, 1000, 2, 1)).toBe(1);
  });

  it('seeks to the start point unless it is too near the end', () => {
    expect(seekSeconds({ start_ms: 72_000 }, 240)).toBe(72);
    expect(seekSeconds({ start_ms: 238_000 }, 240)).toBe(0);
    expect(seekSeconds({ start_ms: 30_000 }, Number.NaN)).toBe(30);
  });

  it('starts by itself only when nothing is playing', () => {
    expect(shouldAutostart(null)).toBe(true);
    expect(shouldAutostart({ paused: true, src: 'x' })).toBe(true);
    expect(shouldAutostart({ paused: false, src: 'x' })).toBe(false);
  });
});
