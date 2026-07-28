/**
 * The Enhanced Management view's data layer, ported from toggleEnhancedView /
 * _libraryViewModeKey / renderEnhancedView / renderEnhancedStatsBar /
 * extractFormat (library.js:2785-2965, 5902).
 *
 * Enhanced is admin-only and library-only: it edits per-track metadata, which
 * needs owned files and a DB row to write back to.
 */

export interface EnhancedTrack {
  duration?: number;
  file_path?: string;
  [key: string]: unknown;
}

export interface EnhancedAlbum {
  record_type?: string;
  tracks?: EnhancedTrack[];
  [key: string]: unknown;
}

export interface EnhancedData {
  albums?: EnhancedAlbum[];
  artist?: Record<string, unknown>;
  server_type?: string | null;
  [key: string]: unknown;
}

/**
 * The persisted Standard/Enhanced choice, scoped to the active profile so two
 * admins can keep different defaults.
 *
 * The unsuffixed key is the fallback, not a bug: it is what pre-multi-profile
 * installs already have in localStorage.
 */
export function libraryViewModeKey(profileId: number | string | null | undefined): string {
  return profileId != null
    ? `soulsync-library-view-mode:${profileId}`
    : 'soulsync-library-view-mode';
}

export function readEnhancedViewMode(profileId: number | string | null | undefined): boolean {
  try {
    return localStorage.getItem(libraryViewModeKey(profileId)) === 'enhanced';
  } catch {
    return false;
  }
}

export function writeEnhancedViewMode(
  profileId: number | string | null | undefined,
  enabled: boolean,
): void {
  try {
    localStorage.setItem(libraryViewModeKey(profileId), enabled ? 'enhanced' : 'standard');
  } catch {
    // The toggle still works for this page view; it just will not persist.
  }
}

/**
 * Enhanced is offered only to an admin on a LIBRARY artist.
 *
 * Forcing it on a source-only artist showed an empty Enhanced pane and hid the
 * discography — there is no DB record behind it to edit.
 */
export function showsEnhancedToggle(isAdmin: boolean, isSourceArtist: boolean): boolean {
  return isAdmin && !isSourceArtist;
}

/** File extension to display format; anything unmapped is uppercased as-is. */
export function extractFormat(filePath: unknown): string {
  if (!filePath) return '-';
  const ext = String(filePath).split('.').pop()?.toLowerCase() ?? '';
  const map: Record<string, string> = {
    mp3: 'MP3',
    flac: 'FLAC',
    m4a: 'AAC',
    ogg: 'OGG',
    opus: 'OPUS',
    wav: 'WAV',
    wma: 'WMA',
    aac: 'AAC',
  };
  return map[ext] || ext.toUpperCase();
}

/**
 * Albums grouped for the three Enhanced sections.
 *
 * record_type is lowercased HERE but not in the stats bar below — so an album
 * typed "EP" lands in the EPs section while the stats bar still counts it as
 * neither. Reproduced rather than harmonised; changing it would move counts
 * Boulder has been reading for months.
 */
export function groupAlbumsByType(albums: EnhancedAlbum[] = []): Record<string, EnhancedAlbum[]> {
  const grouped: Record<string, EnhancedAlbum[]> = { album: [], ep: [], single: [] };
  for (const album of albums) {
    const type = (album.record_type || 'album').toLowerCase();
    if (grouped[type]) grouped[type].push(album);
    else grouped[type] = [album];
  }
  return grouped;
}

/** Only these three render, in this order — any other bucket is grouped but unused. */
export const ENHANCED_SECTIONS: { type: string; label: string }[] = [
  { type: 'album', label: 'Albums' },
  { type: 'ep', label: 'EPs' },
  { type: 'single', label: 'Singles' },
];

export interface EnhancedStatItem {
  value: string | number;
  label: string;
}

export interface EnhancedFormatBadge {
  format: string;
  count: number;
  /** flac / mp3 / other — drives the badge colour. */
  className: string;
}

export interface EnhancedStats {
  items: EnhancedStatItem[];
  badges: EnhancedFormatBadge[];
}

/**
 * The stats bar.
 *
 * Note the asymmetry in the counts, verbatim from the vanilla: an album with NO
 * record_type counts as an album, but the EP and Single counts are strict
 * equality, so a record_type of "EP" counts as none of the three.
 *
 * The vanilla's statsItems also carried an `icon` per row that its template
 * never rendered. Dead data, so it is not carried over — emitting it would ADD
 * icons the page has never shown.
 */
export function enhancedStats(data: EnhancedData): EnhancedStats {
  const albums = data.albums ?? [];

  const totalAlbums = albums.filter((a) => (a.record_type || 'album') === 'album').length;
  const totalEps = albums.filter((a) => a.record_type === 'ep').length;
  const totalSingles = albums.filter((a) => a.record_type === 'single').length;
  const totalTracks = albums.reduce((sum, a) => sum + (a.tracks ? a.tracks.length : 0), 0);

  let totalDurationMs = 0;
  const formatCounts: Record<string, number> = {};
  for (const album of albums) {
    for (const track of album.tracks ?? []) {
      totalDurationMs += track.duration || 0;
      const format = extractFormat(track.file_path);
      // '-' means the track has no path at all; it is not a format.
      if (format !== '-') formatCounts[format] = (formatCounts[format] || 0) + 1;
    }
  }

  const hours = Math.floor(totalDurationMs / 3600000);
  const minutes = Math.floor((totalDurationMs % 3600000) / 60000);

  return {
    items: [
      { value: totalAlbums, label: 'Albums' },
      { value: totalEps, label: 'EPs' },
      { value: totalSingles, label: 'Singles' },
      { value: totalTracks, label: 'Tracks' },
      { value: hours > 0 ? `${hours}h ${minutes}m` : `${minutes}m`, label: 'Duration' },
    ],
    badges: Object.entries(formatCounts)
      // Commonest format first, so the badge row leads with what the library
      // mostly is.
      .sort((a, b) => b[1] - a[1])
      .map(([format, count]) => ({
        format,
        count,
        className: format === 'FLAC' ? 'flac' : format === 'MP3' ? 'mp3' : 'other',
      })),
  };
}

/** m:ss, or a dash for a track with no known duration. */
export function formatDurationMs(ms: unknown): string {
  const value = Number(ms);
  if (!value) return '-';
  const totalSeconds = Math.floor(value / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${String(seconds).padStart(2, '0')}`;
}

/** "3 releases · 40 tracks" — singular for exactly one release. */
export function sectionCountLabel(albumCount: number, trackCount: number): string {
  return `${albumCount} release${albumCount !== 1 ? 's' : ''} \u00B7 ${trackCount} tracks`;
}

export function sectionTrackTotal(albums: EnhancedAlbum[]): number {
  return albums.reduce((sum, album) => sum + (album.tracks ? album.tracks.length : 0), 0);
}

export interface AlbumRowMeta {
  trackCount: number;
  /** "1992 · 12 tracks · 48:20 · Warp" — absent parts are simply skipped. */
  metaLine: string;
  /** The album's commonest format, or '' when no track has a path. */
  primaryFormat: string;
  formatClass: string;
}

/**
 * The collapsed album row's derived fields.
 *
 * The meta line drops each part it has nothing for rather than printing a
 * placeholder — including the duration, which is skipped when formatDurationMs
 * returns its '-' sentinel rather than showing a bare dash between separators.
 */
export function albumRowMeta(album: EnhancedAlbum): AlbumRowMeta {
  const tracks = album.tracks ?? [];
  const trackCount = tracks.length;

  let durationMs = 0;
  const formats: Record<string, number> = {};
  for (const track of tracks) {
    durationMs += track.duration || 0;
    const format = extractFormat(track.file_path);
    if (format !== '-') formats[format] = (formats[format] || 0) + 1;
  }
  const duration = formatDurationMs(durationMs);
  const primaryFormat = Object.keys(formats).sort((a, b) => formats[b] - formats[a])[0] || '';

  const parts: string[] = [];
  if (album.year) parts.push(String(album.year));
  parts.push(`${trackCount} track${trackCount !== 1 ? 's' : ''}`);
  if (duration !== '-') parts.push(duration);
  if (album.label) parts.push(String(album.label));

  return {
    trackCount,
    metaLine: parts.join(' \u00B7 '),
    primaryFormat,
    formatClass: primaryFormat === 'FLAC' ? 'flac' : primaryFormat === 'MP3' ? 'mp3' : 'other',
  };
}
