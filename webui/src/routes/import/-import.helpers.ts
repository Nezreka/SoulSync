import type {
  ImportAlbumMatch,
  ImportAutoFilter,
  ImportAutoImportActiveItem,
  ImportAutoImportMatchData,
  ImportAutoImportResult,
  ImportAutoImportStatusPayload,
  ImportQueueEntry,
  ImportStagingFile,
} from './-import.types';

export const IMPORT_PLACEHOLDER_IMAGE = '/static/placeholder.png';

const IMPORT_SOURCE_LABELS: Record<string, string> = {
  amazon: 'Amazon Music',
  deezer: 'Deezer',
  discogs: 'Discogs',
  hydrabase: 'Hydrabase',
  itunes: 'Apple Music',
  musicbrainz: 'MusicBrainz',
  playlist: 'Playlist',
  soulseek: 'Basic Search',
  spotify: 'Spotify',
  youtube_videos: 'Music Videos',
};

export function getStagingFileKey(file: ImportStagingFile): string {
  return file.full_path;
}

export function formatImportBytes(bytes: number): string {
  if (bytes > 1_073_741_824) return `${(bytes / 1_073_741_824).toFixed(1)} GB`;
  if (bytes > 1_048_576) return `${(bytes / 1_048_576).toFixed(0)} MB`;
  return `${(bytes / 1024).toFixed(0)} KB`;
}

export function getStagingStatsText(files: ImportStagingFile[]): string {
  const totalSize = files.reduce((sum, file) => sum + (file.size || 0), 0);
  const fileLabel = `${files.length} file${files.length === 1 ? '' : 's'}`;
  return totalSize ? `${fileLabel} - ${formatImportBytes(totalSize)}` : fileLabel;
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

/**
 * Banner for a whole result set that came from a fallback provider rather than the lookup source.
 */
export function getImportSourceFallbackBanner(
  results: Array<{ source: string }> | null | undefined,
  lookupSource: string | null | undefined,
): string {
  if (!lookupSource || !results?.length) return '';
  const normalizedLookupSource = lookupSource.toLowerCase();
  if (
    !results.every(
      (result) => result.source && result.source.toLowerCase() !== normalizedLookupSource,
    )
  ) {
    return '';
  }

  const resultSource = results[0]?.source;
  if (!resultSource) return '';

  return `Showing ${getImportSourceLabel(resultSource)} results - not from your primary source (${getImportSourceLabel(lookupSource)}).`;
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

export function getAutoImportCounts(results: ImportAutoImportResult[]) {
  return {
    imported: results.filter(
      (result) => result.status === 'completed' || result.status === 'approved',
    ).length,
    review: results.filter((result) => result.status === 'pending_review').length,
    failed: results.filter(
      (result) => result.status === 'failed' || result.status === 'needs_identification',
    ).length,
  };
}

export function filterAutoImportResults(
  results: ImportAutoImportResult[],
  filter: ImportAutoFilter,
): ImportAutoImportResult[] {
  if (filter === 'pending') return results.filter((result) => result.status === 'pending_review');
  if (filter === 'imported') {
    return results.filter(
      (result) => result.status === 'completed' || result.status === 'approved',
    );
  }
  if (filter === 'failed') {
    return results.filter(
      (result) => result.status === 'failed' || result.status === 'needs_identification',
    );
  }
  return results;
}

export function getAutoImportStatusText(status: ImportAutoImportStatusPayload | undefined): string {
  if (!status) return 'Loading...';
  if (status.paused) return 'Paused';
  if (status.current_status === 'processing') return 'Processing...';
  if (status.current_status === 'scanning') return 'Scanning...';
  if (!status.running) return 'Disabled';

  if (status.last_scan_time) {
    const lastScan = new Date(status.last_scan_time);
    const diffSeconds = Math.floor((Date.now() - lastScan.getTime()) / 1000);
    if (Number.isFinite(diffSeconds) && diffSeconds >= 0 && diffSeconds < 60) {
      return `Watching (scanned ${diffSeconds}s ago)`;
    }
    if (Number.isFinite(diffSeconds) && diffSeconds < 3600) {
      return `Watching (scanned ${Math.floor(diffSeconds / 60)}m ago)`;
    }
  }

  return 'Watching';
}

export type AutoImportStatusTone = 'neutral' | 'info' | 'success';

export function getAutoImportStatusTone(
  status: ImportAutoImportStatusPayload | undefined,
): AutoImportStatusTone {
  if (!status?.running || status.paused) return 'neutral';
  if (status.current_status === 'scanning' || status.current_status === 'processing') return 'info';
  return 'success';
}

export function getActiveImportLines(status: ImportAutoImportStatusPayload | undefined): string[] {
  const active = Array.isArray(status?.active_imports) ? status.active_imports : [];
  if (active.length > 0) return active.map(getActiveImportLine);
  if (status?.current_status === 'scanning') {
    return [`Scanning... (${status.stats?.scanned || 0} processed)`];
  }
  return [];
}

function getActiveImportLine(item: ImportAutoImportActiveItem): string {
  const folder = item.folder_name || '...';
  const trackIndex = item.track_index || 0;
  const trackTotal = item.track_total || 0;
  const trackName = item.track_name || '';
  if (item.status === 'processing' && trackTotal > 0) {
    return `${folder} - track ${trackIndex}/${trackTotal}: ${trackName}`;
  }
  if (item.status === 'matching') return `${folder} - matching tracks...`;
  if (item.status === 'identifying') return `${folder} - identifying...`;
  return `${folder} - queued`;
}

export function parseAutoImportMatchData(
  matchData: ImportAutoImportResult['match_data'],
): ImportAutoImportMatchData {
  if (!matchData) return {};
  if (typeof matchData === 'object') return matchData;
  try {
    const parsed = JSON.parse(matchData) as ImportAutoImportMatchData;
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch {
    return {};
  }
}

export function getAutoImportTimeAgo(createdAt: string | null | undefined): string {
  if (!createdAt) return '';
  const created = new Date(createdAt);
  const diffMinutes = Math.floor((Date.now() - created.getTime()) / 60_000);
  if (!Number.isFinite(diffMinutes) || diffMinutes < 0) return '';
  if (diffMinutes < 1) return 'just now';
  if (diffMinutes < 60) return `${diffMinutes}m ago`;
  if (diffMinutes < 1440) return `${Math.floor(diffMinutes / 60)}h ago`;
  return `${Math.floor(diffMinutes / 1440)}d ago`;
}

export function getConfidenceClass(confidencePercent: number): 'high' | 'medium' | 'low' {
  if (confidencePercent >= 90) return 'high';
  if (confidencePercent >= 70) return 'medium';
  return 'low';
}

export function getAutoImportStatusMeta(status: string): {
  label: string;
  icon: string;
  className: 'completed' | 'review' | 'failed' | 'processing' | 'neutral';
} {
  const labels: Record<string, string> = {
    completed: 'Imported',
    pending_review: 'Needs Review',
    needs_identification: 'Unidentified',
    failed: 'Failed',
    scanning: 'Scanning...',
    matched: 'Matched',
    rejected: 'Dismissed',
    approved: 'Approved',
    processing: 'Processing',
  };

  const icons: Record<string, string> = {
    completed: '✓',
    pending_review: '⚠',
    needs_identification: '✗',
    failed: '✗',
    scanning: '⌛',
    matched: '✓',
    rejected: '✕',
    approved: '✓',
    processing: '⧗',
  };

  return {
    label: labels[status] || status,
    icon: icons[status] || '',
    className:
      status === 'completed'
        ? 'completed'
        : status === 'pending_review'
          ? 'review'
          : status === 'failed' || status === 'needs_identification'
            ? 'failed'
            : status === 'processing'
              ? 'processing'
              : 'neutral',
  };
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
