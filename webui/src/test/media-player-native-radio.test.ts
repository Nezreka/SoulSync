import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it, vi } from 'vitest';

import { extractFunction } from './vanilla-extract';

const source = readFileSync(resolve(process.cwd(), 'static/media-player.js'), 'utf8');
const functions = ['npSetRadioMode', 'npMapRadioTrack', 'npFetchRadioTracks']
  .map((name) => extractFunction(name, source))
  .join('\n');
function player(seed: Record<string, unknown>) {
  const fetch = vi.fn(async (_url: string) => ({ ok: true, json: async () => ({ tracks: [] }) }));
  const helper = source.includes('function npRadioTrackId(')
    ? extractFunction('npRadioTrackId', source)
    : '';
  // oxlint-disable-next-line typescript-eslint/no-implied-eval -- Exercise the actual classic player.
  const bridge = new Function(
    'currentTrack',
    'fetch',
    `
    let npRadioMode = true, npLoadingQueueItem = false, npPlayContext = '', isPlaying = true;
    const npRecentlyPlayedIds = [], npQueue = [];
    const document = { getElementById: () => null };
    const showToast = () => {}, npQueueHasNext = () => false, npEnsureCurrentTrackInQueue = () => {};
    const npSetPlayContext = () => {}, renderNpQueue = () => {}, updateNpPrevNextButtons = () => {}, playNextInQueue = () => {};
    ${helper}\n${functions}
    return { refill: npFetchRadioTracks, map: npMapRadioTrack,
      enable: () => npSetRadioMode(true, { toast:false, fetchIfNeeded:true }) };
  `,
  )(seed, fetch) as {
    refill: () => Promise<void>;
    map: (row: Record<string, unknown>) => Record<string, unknown>;
    enable: () => void;
  };
  return { ...bridge, fetch };
}
describe('radio uses catalogue identity through the shared player', () => {
  it('refills by native id even if the server has a different numeric id', async () => {
    const radio = player({ id: 999, lib2_track_id: 11 });
    await radio.refill();
    expect(radio.fetch.mock.calls[0]?.[0]).toContain('track_id=11&');
  });
  it('enables refills for a native file without a server mapping', () => {
    const radio = player({ id: null, lib2_track_id: 11 });
    radio.enable();
    expect(radio.fetch).toHaveBeenCalled();
  });
  it('keeps catalogue ids out of the server-id slot on radio results', () => {
    const radio = player({});
    expect(
      radio.map({ id: 11, album_id: 12, artist_id: 13, file_path: '/music/t.flac' }),
    ).toMatchObject({ id: null, lib2_track_id: 11, lib2_album_id: 12, lib2_artist_id: 13 });
  });
});
