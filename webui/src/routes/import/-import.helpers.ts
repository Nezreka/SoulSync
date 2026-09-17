import type { ImportAlbumMatch, ImportQueueEntry, ImportStagingFile } from './-import.types';

export const IMPORT_PLACEHOLDER_IMAGE = '/static/placeholder.png';

const IMPORT_SOURCE_LABELS: Record<string, string> = {
  amazon: 'Amazon Music',
  bandcamp: 'Bandcamp',
  deezer: 'Deezer',
  discogs: 'Discogs',
  hydrabase: 'Hydrabase',
  itunes: 'Apple Music',
  jiosaavn: 'JioSaavn',
  musicbrainz: 'MusicBrainz',
  playlist: 'Playlist',
  soulseek: 'Basic Search',
  spotify: 'Spotify',
  youtube_videos: 'Music Videos',
};

export function formatImportBytes(bytes: number): string {
  if (bytes > 1_073_741_824) return `${(bytes / 1_073_741_824).toFixed(1)} GB`;
  if (bytes > 1_048_576) return `${(bytes / 1_048_576).toFixed(0)} MB`;
  return `${(bytes / 1024).toFixed(0)} KB`;
}

export function getImportSourceLabel(source: string | null | undefined): string {
  if (!source) return '';
  return IMPORT_SOURCE_LABELS[source.toLowerCase()] || source;
}

/**
 * Label a fallback result row so it is obvious which provider actually returned it.
 */
export function getImportSourceBadgeText(
  resultSource: string | null | undefined,
  lookupSource: string | null | undefined,
): string {
  if (!resultSource || !lookupSource) return '';
  if (resultSource.toLowerCase() === lookupSource.toLowerCase()) return '';
  return `via ${getImportSourceLabel(resultSource)}`;
}

export function getTrackDisplayInfo(match: ImportAlbumMatch, index: number) {
  const track = match.track || match.spotify_track || {};
  const rawTrackNumber = track.track_number ?? track.trackNumber ?? null;
  const trackNumber =
    rawTrackNumber === null || rawTrackNumber === undefined || rawTrackNumber === ''
      ? null
      : String(rawTrackNumber).split('/')[0]?.trim() || null;

  return {
    track,
    name: track.name || track.title || `Track ${index + 1}`,
    trackNumber,
    displayTrackNumber: trackNumber || String(index + 1),
  };
}

export function getEffectiveAlbumMatches(
  matches: ImportAlbumMatch[],
  stagingFiles: ImportStagingFile[],
  overrides: Record<number, number>,
): ImportAlbumMatch[] {
  return matches.flatMap((match, index) => {
    if (Object.hasOwn(overrides, index)) {
      const override = overrides[index];
      if (override === -1) return [];
      const stagingFile = stagingFiles[override];
      return stagingFile ? [{ ...match, staging_file: stagingFile, confidence: 1 }] : [];
    }
    return match.staging_file ? [match] : [];
  });
}

export function getDisplayedMatchFile(
  match: ImportAlbumMatch,
  index: number,
  stagingFiles: ImportStagingFile[],
  overrides: Record<number, number>,
): { file: ImportStagingFile | null; confidence: number; isOverride: boolean } {
  if (Object.hasOwn(overrides, index)) {
    const override = overrides[index];
    if (override === -1) return { file: null, confidence: match.confidence, isOverride: false };
    return {
      file: stagingFiles[override] ?? null,
      confidence: 1,
      isOverride: true,
    };
  }

  if (!match.staging_file) {
    return { file: null, confidence: match.confidence, isOverride: false };
  }

  const autoFileName = match.staging_file.filename;
  const reassigned = Object.entries(overrides).some(([trackIndex, stagingFileIndex]) => {
    const file = stagingFiles[stagingFileIndex];
    return file && file.filename === autoFileName && Number(trackIndex) !== index;
  });

  return {
    file: reassigned ? null : match.staging_file,
    confidence: match.confidence,
    isOverride: false,
  };
}

export function getUnmatchedStagingFiles(
  matches: ImportAlbumMatch[],
  stagingFiles: ImportStagingFile[],
  overrides: Record<number, number>,
): Array<{ file: ImportStagingFile; index: number }> {
  return stagingFiles.flatMap((file, index) => {
    if (Object.values(overrides).includes(index)) return [];

    const autoUsed = matches.some((match, matchIndex) => {
      if (Object.hasOwn(overrides, matchIndex)) return false;
      return match.staging_file?.filename === file.filename;
    });

    return autoUsed ? [] : [{ file, index }];
  });
}

export function getQueueProgressPercent(entry: ImportQueueEntry): number {
  if (entry.status === 'done' || entry.status === 'error') return 100;
  if (entry.total <= 0) return 0;
  return Math.round((entry.processed / entry.total) * 100);
}

export function getQueueStatusText(entry: ImportQueueEntry): string {
  if (entry.status === 'running') return `${entry.processed}/${entry.total}`;
  if (entry.status === 'done') {
    return entry.errors.length > 0
      ? `${entry.processed}/${entry.total} (${entry.errors.length} err)`
      : 'Done';
  }
  return 'Failed';
}

export function formatDuration(durationMs: number | null | undefined): string {
  if (!durationMs) return '';
  const minutes = Math.floor(durationMs / 60_000);
  const seconds = String(Math.floor((durationMs % 60_000) / 1000)).padStart(2, '0');
  return `${minutes}:${seconds}`;
}
