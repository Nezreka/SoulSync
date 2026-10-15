import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it, vi } from 'vitest';

import { extractFunction } from './vanilla-extract';

// #1312: a missing queue song with auto-download off was skipped. with the
// stream source on deezer previews it plays its 30s clip instead. runs the
// real playQueueItem, not a copy of what it is believed to do.
const source = readFileSync(resolve(process.cwd(), 'static/media-player.js'), 'utf8');
const functions = ['startAudioPlayback', 'npFindQueuePreview', 'playQueueItem']
  .map((name) => extractFunction(name, source))
  .join('\n');

const CLIP = {
  username: 'deezer_preview',
  filename: '3135556||Harder, Better, Faster, Stronger',
  preview: true,
};

function harness(opts: { autoDownload?: boolean; clip?: boolean } = {}) {
  const audio = Object.assign(new EventTarget(), {
    paused: true,
    readyState: 3,
    currentTime: 0,
    src: '',
    volume: 1,
    loop: false,
    pause: vi.fn(),
    load: vi.fn(),
    play: vi.fn(async () => {}),
  });
  const calls: Array<{ url: string; body: Record<string, unknown> }> = [];
  const fetch = vi.fn(async (url: string, init?: { body?: string }) => {
    const body = init?.body ? JSON.parse(init.body) : {};
    calls.push({ url, body });
    if (url === '/api/enhanced-search/stream-track') {
      return {
        json: async () =>
          opts.clip === false ? { success: false } : { success: true, result: CLIP },
      };
    }
    return { json: async () => ({ success: true }) };
  });
  const infos: Array<Record<string, unknown>> = [];
  const toast = vi.fn();
  const run = new Function(
    'audioPlayer',
    'fetch',
    'showToast',
    'infos',
    `
    let npPlaybackGeneration = 0, npPlaybackAbort = new AbortController(), npPlaybackSetup = Promise.resolve();
    let npLoadingQueueItem = false, npXfadeHandoff = null, npRepeatMode = 'off';
    let npAutoDownloadQueue = ${opts.autoDownload ? 'true' : 'false'};
    const API = { stream: { start: '/api/stream/start' } };
    let npQueue = [{ title: 'Harder, Better, Faster, Stronger', artist: 'Daft Punk', album: 'Discovery', file_path: '', is_library: false }];
    let npQueueIndex = 0;
    const npCancelCrossfade = () => {}, showLoadingAnimation = () => {}, hideLoadingAnimation = () => {}, renderNpQueue = () => {}, updateNpPrevNextButtons = () => {}, setPlayingState = () => {}, clearTrack = () => {};
    const setTrackInfo = (t) => infos.push(t);
    const stopStream = async () => {};
    const npEnsureQueueTrackReady = async () => { throw new Error('Auto-download is disabled for missing queue tracks'); };
    ${functions}
    return () => playQueueItem(0);
  `,
  )(audio, fetch, toast, infos) as () => Promise<{ status: string }>;
  return { run, audio, calls, infos, toast };
}

describe('missing queue song with auto-download off', () => {
  it('plays the deezer clip instead of skipping', async () => {
    const h = harness();
    expect((await h.run()).status).toBe('played');
    expect(h.calls.map((c) => c.url)).toEqual([
      '/api/enhanced-search/stream-track',
      '/api/stream/start',
    ]);
    expect(h.calls[0].body.preview_only).toBe(true);
    expect(h.calls[1].body).toEqual(CLIP);
    expect(h.infos.at(-1)?.is_preview).toBe(true);
    expect(h.audio.play).toHaveBeenCalledOnce();
  });

  it('still skips when there is no clip', async () => {
    const h = harness({ clip: false });
    expect((await h.run()).status).toBe('failed');
    expect(h.calls.map((c) => c.url)).toEqual(['/api/enhanced-search/stream-track']);
    expect(h.audio.play).not.toHaveBeenCalled();
  });

  it('never asks for a clip when auto-download is on', async () => {
    const h = harness({ autoDownload: true });
    await h.run();
    expect(h.calls.some((c) => c.url === '/api/enhanced-search/stream-track')).toBe(false);
  });
});
