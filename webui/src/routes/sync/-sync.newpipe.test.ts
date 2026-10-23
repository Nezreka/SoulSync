import { describe, expect, it } from 'vitest';

import {
  isNewPipeFile,
  newpipeImportable,
  newpipeMirrorPayload,
  type NewPipePlaylist,
} from './-sync.newpipe';

const PL: NewPipePlaylist = {
  id: 'newpipe_4',
  name: 'Gym',
  image_url: 'http://t/a.jpg',
  track_count: 1,
  tracks: [
    { track_name: 'Sanctuary', artist_name: 'TAYA.', duration_ms: 437000, source_track_id: 'AAA' },
  ],
};

describe('newpipe helpers', () => {
  it('takes the export zip and the bare db, case-blind, and nothing else', () => {
    expect(isNewPipeFile('NewPipeData-2026.zip')).toBe(true);
    expect(isNewPipeFile('newpipe.DB')).toBe(true);
    expect(isNewPipeFile('list.csv')).toBe(false);
    expect(isNewPipeFile('zip')).toBe(false);
  });

  it('mirrors as source newpipe under the stable uid key', () => {
    const payload = newpipeMirrorPayload(PL);
    expect(payload).toMatchObject({
      source: 'newpipe',
      source_playlist_id: 'newpipe_4',
      name: 'Gym',
      owner: 'NewPipe',
      image_url: 'http://t/a.jpg',
    });
    expect(payload.tracks[0]).toMatchObject({
      track_name: 'Sanctuary',
      artist_name: 'TAYA.',
      album_name: '',
      source_track_id: 'AAA',
    });
  });

  it('an empty playlist is not importable', () => {
    expect(newpipeImportable(PL)).toBe(true);
    expect(newpipeImportable({ ...PL, track_count: 0, tracks: [] })).toBe(false);
  });
});
