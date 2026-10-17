import { fetchLibraryV2ArtistPlaybackFiles } from './-library-v2.api';
import { artistQueueRows } from './-library-v2.play';

/** Native file/scope reader and existing player/radio engine, shared by discovery. */
export async function playLibraryV2ArtistRadio(artistId: number, artistName: string) {
  try {
    let owned: ReturnType<typeof artistQueueRows> = [];
    for (let page = 1; ; page += 1) {
      const batch = await fetchLibraryV2ArtistPlaybackFiles(artistId, { page, limit: 100 });
      owned = artistQueueRows(batch.files, artistName).filter((row) => row.file_path);
      if (owned.length || page >= (batch.pagination?.total_pages ?? 1)) break;
    }
    if (!owned.length) {
      window.showToast?.(`Nothing by ${artistName} is on disk yet`, 'info');
      return false;
    }
    if (!window.playTrackList || !window.npSetRadioMode) {
      throw new Error('The audio player is not ready');
    }
    await window.playTrackList(
      [owned[Math.floor(Math.random() * owned.length)]!],
      `${artistName} Radio`,
    );
    window.npSetRadioMode(true, { toast: false, fetchIfNeeded: true });
    return true;
  } catch (error) {
    window.showToast?.(
      error instanceof Error ? error.message : 'Could not start artist radio',
      'error',
    );
    return false;
  }
}
