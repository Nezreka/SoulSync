/** music display rules shared by the artist and album pages */

import type { LibRelease, LibTrackFile } from '../-player.types';

/** the player shows what you own: library releases with at least one track present */
export function ownedReleases(releases: LibRelease[] | undefined): LibRelease[] {
  return (releases ?? []).filter((r) => r.origin !== 'discography' || (r.tracks_present ?? 0) > 0);
}

/** FLAC 24/96, MP3 320: a quiet quality line from a track's file */
export function fileQuality(file: LibTrackFile | null | undefined): string {
  if (!file?.format) return '';
  const format = file.format.toUpperCase();
  if (file.bit_depth && file.sample_rate) {
    const khz = Math.round(file.sample_rate / 100) / 10;
    return `${format} ${file.bit_depth}/${khz % 1 ? khz : Math.round(khz)}`;
  }
  if (file.bitrate)
    return `${format} ${Math.round(file.bitrate > 2000 ? file.bitrate / 1000 : file.bitrate)}`;
  return format;
}
