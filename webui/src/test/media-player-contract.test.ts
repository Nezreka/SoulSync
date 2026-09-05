import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { extractFunction } from './vanilla-extract';

const source = readFileSync(resolve(process.cwd(), 'static/media-player.js'), 'utf8');
const functions = ['startAudioPlayback', 'playQueueItem', 'playTrackList']
  .map((name) => extractFunction(name, source))
  .join('\n');

function playerHarness(options: { serverFails?: boolean; audioFails?: boolean } = {}) {
  const audio = Object.assign(new EventTarget(), {
    paused: true,
    readyState: 3,
    currentTime: 0,
    src: '',
    volume: 1,
    pause: vi.fn(),
    load: vi.fn(),
    play: vi.fn(async () => {
      if (options.audioFails) throw new Error('decode failed');
    }),
  });
  const toast = vi.fn();
  const fetch = vi.fn(async () => ({
    json: async () => ({ success: !options.serverFails, error: 'server unavailable' }),
  }));
  const globals = source.slice(
    source.indexOf('let npLoadingQueueItem = false;'),
    source.indexOf('\n};', source.indexOf('window.cancelPendingPlayback =')) + 3,
  );
  // Execute the actual legacy functions, including their real catch/return paths.
  const bridge = new Function(
    'audioPlayer',
    'fetch',
    'showToast',
    'window',
    'document',
    `
    ${globals}
    let npQueue = [], npQueueIndex = 0, npRepeatMode = 'off', npRadioMode = false;
    const npCancelCrossfade = () => {}, setTrackInfo = () => {}, showLoadingAnimation = () => {}, hideLoadingAnimation = () => {}, renderNpQueue = () => {}, updateNpPrevNextButtons = () => {}, setPlayingState = () => {}, clearTrack = () => {}, npSetPlayContext = () => {}, npScheduleQueuePrefetch = () => {};
    const clearQueue = () => { npQueue = []; };
    const npPrepareQueueTracks = tracks => tracks.map(t => ({ ...t, is_library: true }));
    const stopStream = async () => {};
    const npEnsureQueueTrackReady = async () => { throw new Error('not local'); };
    ${functions}
    return playTrackList;
  `,
  )(audio, fetch, toast, {}, document) as (tracks: unknown[]) => Promise<{ status: string }>;
  return { bridge, audio, toast, fetch };
}

afterEach(() => vi.useRealTimers());

describe('actual media-player playback acknowledgment', () => {
  const tracks = [{ file_path: '/music/a.flac', title: 'A', artist: 'X' }];
  it('returns failed when library/play fails rather than fulfilling as played', async () => {
    const h = playerHarness({ serverFails: true });
    expect((await h.bridge(tracks)).status).toBe('failed');
    expect(h.audio.play).not.toHaveBeenCalled();
  });
  it('returns failed when audio.play rejects after retries', async () => {
    vi.useFakeTimers();
    const h = playerHarness({ audioFails: true });
    const result = h.bridge(tracks);
    await vi.runAllTimersAsync();
    expect((await result).status).toBe('failed');
    expect(h.audio.play).toHaveBeenCalledTimes(3);
  });
  it('returns played only when the real audio start function succeeds', async () => {
    const h = playerHarness();
    expect((await h.bridge(tracks)).status).toBe('played');
    expect(h.audio.play).toHaveBeenCalledOnce();
  });
  it('returns empty for unusable metadata', async () => {
    const h = playerHarness();
    expect((await h.bridge([{}])).status).toBe('empty');
    expect(h.audio.play).not.toHaveBeenCalled();
  });
  it('reports readiness timeout as failure', async () => {
    vi.useFakeTimers();
    const h = playerHarness();
    h.audio.readyState = 0;
    const result = h.bridge(tracks);
    await vi.runAllTimersAsync();
    expect((await result).status).toBe('failed');
    expect(h.audio.play).not.toHaveBeenCalled();
  });
});
