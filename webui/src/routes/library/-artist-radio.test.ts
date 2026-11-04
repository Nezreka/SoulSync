import { afterEach, describe, expect, it, vi } from 'vitest';

import { playLibraryV2ArtistRadio } from './-artist-radio';
import { fetchLibraryV2ArtistPlaybackFiles } from './-library-v2.api';
vi.mock('./-library-v2.api', () => ({ fetchLibraryV2ArtistPlaybackFiles: vi.fn() }));
afterEach(() => {
  vi.resetAllMocks();
  delete window.playTrackList;
  delete window.npSetRadioMode;
});
describe('native artist radio', () => {
  it('plays a scoped native file before enabling the existing radio engine', async () => {
    vi.mocked(fetchLibraryV2ArtistPlaybackFiles).mockResolvedValue({
      files: [
        {
          track_id: 77,
          album_id: 8,
          artist_id: 9,
          title: 'Song',
          path: '/music/song.flac',
          file_state: 'active',
          artist_name: 'Artist',
          album_title: 'Album',
        },
      ],
      pagination: { total_pages: 1 },
    } as never);
    window.playTrackList = vi.fn();
    window.npSetRadioMode = vi.fn();
    expect(await playLibraryV2ArtistRadio(9, 'Artist')).toBe(true);
    expect(window.playTrackList).toHaveBeenCalledWith(
      [expect.objectContaining({ id: null, lib2_track_id: 77, file_path: '/music/song.flac' })],
      'Artist Radio',
    );
    expect(window.npSetRadioMode).toHaveBeenCalledWith(true, { toast: false, fetchIfNeeded: true });
  });
  it('finds an owned seed when the first queue page has only missing tracks', async () => {
    vi.mocked(fetchLibraryV2ArtistPlaybackFiles)
      .mockResolvedValueOnce({ files: [], pagination: { total_pages: 2 } } as never)
      .mockResolvedValueOnce({
        files: [
          {
            track_id: 88,
            album_id: 8,
            artist_id: 9,
            track_title: 'Owned seed',
            path: '/music/owned.flac',
            file_state: 'active',
          },
        ],
        pagination: { total_pages: 2 },
      } as never);
    window.playTrackList = vi.fn();
    window.npSetRadioMode = vi.fn();
    expect(await playLibraryV2ArtistRadio(9, 'Artist')).toBe(true);
    expect(window.playTrackList).toHaveBeenCalledWith(
      [expect.objectContaining({ lib2_track_id: 88 })],
      'Artist Radio',
    );
  });

  it('does not request missing files just to start radio', async () => {
    vi.mocked(fetchLibraryV2ArtistPlaybackFiles).mockResolvedValue({
      files: [],
      pagination: { total_pages: 1 },
    } as never);
    window.playTrackList = vi.fn();
    window.npSetRadioMode = vi.fn();
    expect(await playLibraryV2ArtistRadio(9, 'Artist')).toBe(false);
    expect(window.playTrackList).not.toHaveBeenCalled();
    expect(window.npSetRadioMode).not.toHaveBeenCalled();
  });
});
