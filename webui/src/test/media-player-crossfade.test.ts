import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it, vi } from 'vitest';

import { extractFunction } from './vanilla-extract';

/**
 * The crossfade handoff, run through the REAL functions. When the fade ends
 * the second audio is mid-song; the main player must join it there, not
 * start the song again from zero, and the second audio must stop once the
 * main player is playing (or as soon as the load fails).
 */
const source = readFileSync(resolve(process.cwd(), 'static/media-player.js'), 'utf8');
const functions = [
  'startAudioPlayback',
  'playQueueItem',
  'npStopHandoff',
  'npCancelCrossfade',
  'npFinishCrossfade',
]
  .map((name) => extractFunction(name, source))
  .join('\n');

function fakeAudio(over: Record<string, unknown> = {}) {
  return Object.assign(new EventTarget(), {
    paused: false,
    readyState: 3,
    currentTime: 0,
    src: 'x',
    volume: 1,
    pause: vi.fn(),
    load: vi.fn(),
    play: vi.fn(async () => {}),
    ...over,
  });
}

function harness(options: { serverFails?: boolean } = {}) {
  const main = fakeAudio({ paused: true, src: '' });
  const xfade = fakeAudio({ currentTime: 6.2, volume: 0.8 });
  const fetch = vi.fn(async () => ({
    json: async () => ({ success: !options.serverFails, error: 'server unavailable' }),
  }));
  const globals = source.slice(
    source.indexOf('let npLoadingQueueItem = false;'),
    source.indexOf('\n};', source.indexOf('window.cancelPendingPlayback =')) + 3,
  );
  const run = new Function(
    'audioPlayer',
    'xa',
    'fetch',
    'showToast',
    'window',
    'document',
    `
    ${globals}
    let npQueue = [{ file_path: '/m/a.flac', is_library: true }, { file_path: '/m/b.flac', is_library: true }];
    let npQueueIndex = 0, npRepeatMode = 'off', npRadioMode = false;
    let npXfadeAudio = xa, npXfadeActive = true, npXfadeTimer = null, npXfadeMainVol = 0.8, npXfadeHandoff = null;
    const setTrackInfo = () => {}, showLoadingAnimation = () => {}, hideLoadingAnimation = () => {}, renderNpQueue = () => {}, updateNpPrevNextButtons = () => {}, setPlayingState = () => {};
    const stopStream = async () => {};
    ${functions}
    return {
      finish: (restoreVol) => npFinishCrossfade(1, restoreVol),
      cancel: () => npCancelCrossfade(),
      state: () => ({ npXfadeHandoff, npXfadeActive, npQueueIndex }),
    };
  `,
  )(main, xfade, fetch, vi.fn(), {}, document) as {
    finish: (v: number) => void;
    cancel: () => void;
    state: () => { npXfadeHandoff: unknown; npXfadeActive: boolean; npQueueIndex: number };
  };
  return { run, main, xfade, fetch };
}

const flush = () => new Promise((r) => setTimeout(r, 0));

describe('crossfade handoff', () => {
  it('the main player joins the second audio where it is, then the second audio stops', async () => {
    const h = harness();
    // Positions read at play time, not at finish time: the second audio kept
    // moving through the load.
    h.main.play.mockImplementation(async () => {
      expect(h.main.currentTime).toBeCloseTo(6.2 + 0.15, 5);
      expect(h.xfade.pause).not.toHaveBeenCalled();
    });
    h.run.finish(0.8);
    expect(h.run.state().npXfadeHandoff).toBe(h.xfade);
    expect(h.xfade.pause).not.toHaveBeenCalled();
    await flush();
    await flush();
    expect(h.main.play).toHaveBeenCalledOnce();
    expect(h.xfade.pause).toHaveBeenCalledOnce();
    expect(h.xfade.src).toBe('');
    expect(h.run.state().npXfadeHandoff).toBeNull();
    expect(h.run.state().npQueueIndex).toBe(1);
    expect(h.main.volume).toBe(0.8);
  });

  it('stops the second audio when the next track fails to load', async () => {
    const h = harness({ serverFails: true });
    h.run.finish(0.8);
    for (let i = 0; i < 4; i++) await flush();
    expect(h.main.play).not.toHaveBeenCalled();
    expect(h.xfade.pause).toHaveBeenCalled();
    expect(h.run.state().npXfadeHandoff).toBeNull();
  });

  it('a skip during the handoff silences the second audio', () => {
    const h = harness();
    h.run.finish(0.8);
    expect(h.run.state().npXfadeHandoff).toBe(h.xfade);
    h.run.cancel();
    expect(h.xfade.pause).toHaveBeenCalled();
    expect(h.run.state().npXfadeHandoff).toBeNull();
  });
});
