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
 * The one place a release's type is decided, used by BOTH the sections and the
 * stats bar (TheHomeGuy, Aug 2026).
 *
 * They used to decide separately: the grouping lowercased record_type, the
 * stats bar compared it strictly. So a row typed "EP" rendered under EPs and
 * counted as NONE of the three numbers above it. The two halves of the same
 * screen disagreed by construction, which is most of why the album count did
 * not match the standard view's.
 *
 * 'compile' is deezer's spelling of compilation, normalised here so the two
 * don't split into separate buckets.
 */
export function releaseType(album: EnhancedAlbum): string {
  const raw = (album.record_type || '').trim().toLowerCase();
  if (!raw) return 'album';
  return raw === 'compile' ? 'compilation' : raw;
}

/** Albums grouped for the Enhanced sections. */
export function groupAlbumsByType(albums: EnhancedAlbum[] = []): Record<string, EnhancedAlbum[]> {
  const grouped: Record<string, EnhancedAlbum[]> = { album: [], ep: [], single: [] };
  for (const album of albums) {
    const type = releaseType(album);
    if (grouped[type]) grouped[type].push(album);
    else grouped[type] = [album];
  }
  return grouped;
}

/** The named sections, in this order. Anything else follows via OTHER_SECTION_LABELS. */
export const ENHANCED_SECTIONS: { type: string; label: string }[] = [
  { type: 'album', label: 'Albums' },
  { type: 'ep', label: 'EPs' },
  { type: 'single', label: 'Singles' },
];

/** Titles for the buckets that used to be grouped and then silently dropped. */
const OTHER_SECTION_LABELS: Record<string, string> = {
  compilation: 'Compilations',
  live: 'Live',
  soundtrack: 'Soundtracks',
  remix: 'Remixes',
};

/**
 * Every section to render, named ones first.
 *
 * The renderer used to walk ENHANCED_SECTIONS only, so a release whose type was
 * anything else was fetched, grouped and then never shown. Compilations are the
 * common case and a greatest-hits-heavy artist could have several sitting in
 * the library with no way to see them here.
 */
export function enhancedSectionsFor(albums: EnhancedAlbum[] = []): { type: string; label: string }[] {
  const known = new Set(ENHANCED_SECTIONS.map((s) => s.type));
  const extras: { type: string; label: string }[] = [];
  const seen = new Set<string>();
  for (const album of albums) {
    const type = releaseType(album);
    if (known.has(type) || seen.has(type)) continue;
    seen.add(type);
    extras.push({
      type,
      label: OTHER_SECTION_LABELS[type] ?? type.charAt(0).toUpperCase() + type.slice(1),
    });
  }
  extras.sort((a, b) => a.label.localeCompare(b.label));
  return [...ENHANCED_SECTIONS, ...extras];
}

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
 * Counts come from `releaseType`, the same classifier the sections use. They
 * used to be computed separately with strict equality, so a row typed "EP"
 * counted as none of the three while still rendering under EPs.
 *
 * A release with no record_type still counts as an album. That is a guess, and
 * on a library where enrichment has not run it is the main reason this number
 * reads higher than the standard view's owned count: nothing sets record_type
 * during a media-server scan, only the enrichment workers do. Left as-is
 * because changing it moves a number people read; see the note in the report.
 *
 * The vanilla's statsItems also carried an `icon` per row that its template
 * never rendered. Dead data, so it is not carried over — emitting it would ADD
 * icons the page has never shown.
 */
export function enhancedStats(data: EnhancedData): EnhancedStats {
  const albums = data.albums ?? [];

  // Same classifier the sections use, so a number can never disagree with the
  // list under it again.
  const totalAlbums = albums.filter((a) => releaseType(a) === 'album').length;
  const totalEps = albums.filter((a) => releaseType(a) === 'ep').length;
  const totalSingles = albums.filter((a) => releaseType(a) === 'single').length;
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

export interface BulkEditValues {
  track_number: string;
  bpm: string;
  style: string;
  mood: string;
  explicit: string;
}

export const EMPTY_BULK_EDIT: BulkEditValues = {
  track_number: '',
  bpm: '',
  style: '',
  mood: '',
  explicit: '',
};

/**
 * Only the fields the user actually filled in.
 *
 * Every field is "leave blank to skip" — a batch edit writes the same value to
 * many tracks, so an empty box must mean "don't touch this column", never
 * "clear it on all of them".
 */
export function bulkEditUpdates(values: BulkEditValues): Record<string, unknown> {
  const updates: Record<string, unknown> = {};
  if (values.track_number !== '') updates.track_number = parseInt(values.track_number, 10);
  if (values.bpm !== '') updates.bpm = parseFloat(values.bpm);
  if (values.style !== '') updates.style = values.style;
  if (values.mood !== '') updates.mood = values.mood;
  // `!== ''` rather than a truthiness check for intent, though the two agree
  // here: the value is a STRING from a <select>, so '0' is already truthy.
  if (values.explicit !== '') updates.explicit = parseInt(values.explicit, 10);
  return updates;
}

/** "Batch Edit 3 Tracks" — singular for one. */
export function bulkEditTitle(count: number): string {
  return `Batch Edit ${count} Track${count !== 1 ? 's' : ''}`;
}
