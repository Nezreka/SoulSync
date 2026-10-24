import { trackDuration } from '@/components/track/track-format';

import type { AlbumAssignment, ReviewFinding } from '../-review.api';

export function shortDuration(value: number | null | undefined): string {
  return trackDuration(value, '');
}

export function assignmentKey(row: AlbumAssignment): string {
  return `${row.file_path || 'missing'}:${row.slot_disc_number || 1}:${row.slot_track_number || row.current_track_number || 0}`;
}

export function findingHeading(finding: ReviewFinding): string {
  const details = finding.details && typeof finding.details === 'object' ? finding.details : {};
  if (details.version_conflict) {
    return `Compare versions: ${textValue(details.current_title || finding.title.replace(/^Unassigned audio:\s*/i, ''))}`;
  }
  const changes =
    details.changes && typeof details.changes === 'object' && !Array.isArray(details.changes)
      ? Object.keys(details.changes as Record<string, unknown>)
      : [];
  const expected = textValue(details.expected_title || finding.title.replace(/^Retag\s+/i, ''));
  if (details.audio_conflict === 'different_recording') return finding.title;
  if (changes.length === 1 && changes[0] === 'filename') return `Rename file: ${expected}`;
  if (finding.finding_type === 'reconciliation_unresolved' && changes.length === 0) {
    return `Confirm assignment: ${expected.replace(/^Confirm:\s*/i, '')}`;
  }
  if (/^Retag\s+/i.test(finding.title)) return `Update metadata: ${expected}`;
  return finding.title;
}

export function evidenceLabel(evidence: unknown): string {
  if (evidence === 'audio') return 'Audio-supported match';
  if (evidence === 'metadata') return 'Matched from metadata';
  if (evidence === 'user') return 'User-confirmed assignment';
  if (evidence === 'conflicting') return 'Needs review';
  return 'Assignment settled';
}

/** Assignment confidence does not approve the metadata edits attached to it. */
export function metadataNeedsReview(row: AlbumAssignment): boolean {
  return (
    row.status === 'change' &&
    !row.numbering_preview &&
    ['manual', 'destructive', 'blocked'].includes(row.action_state || row.review_tier || '')
  );
}

export function statusLabel(status: string, row?: AlbumAssignment) {
  if (row?.fulfillment_state === 'downloaded') return 'Checking download';
  if (row && metadataNeedsReview(row))
    return row.planned_choice?.category === 'numbering' && row.planned_choice.action === 'dismiss'
      ? 'Review metadata · numbering kept'
      : 'Review metadata';
  if (row?.planned_choice?.action === 'dismiss')
    return row.planned_choice.category === 'numbering'
      ? 'Keep numbering planned'
      : ['extra_files', 'audio_conflicts', 'duplicates'].includes(row.planned_choice.category)
        ? 'Keep files planned'
        : 'Keep values planned';
  if (row?.planned_choice?.action === 'fix') {
    if (row.planned_choice.fix_action === 'replace') return 'Replacement planned';
    if (row.planned_choice.fix_action === 'quarantine') return 'Quarantine planned';
    if (row.planned_choice.fix_action === 'request_missing') return 'Request planned';
    if (row.planned_choice.category === 'duplicates') return 'Copy choice planned';
    return 'Change planned';
  }
  if (row?.fulfillment_state === 'downloading') return 'Downloading';
  if (row?.fulfillment_state === 'wishlisted') return 'Wishlisted';
  if (row?.numbering_preview && status === 'change') return 'Numbering proposal';
  if (row?.audio_conflict === 'different_recording') return 'Audio conflicts with this track';
  if (row?.relationship === 'version_conflict') return 'Compare versions';
  if (row?.companion_of && status === 'confirmed') return 'Intentional companion';
  return (
    {
      confirmed: evidenceLabel(row?.assignment_evidence),
      change: 'Change',
      uncertain: 'Review',
      missing: 'Missing',
      not_owned: 'Not collected',
      extra: 'Extra',
      duplicate: 'Duplicate',
      settled: 'Previously reviewed',
    }[status] || status
  );
}

export function changeFieldLabel(field: string, locations?: string[]): string {
  const name =
    {
      artist: 'Album artist',
      album_artist: 'Album artist',
      track_artist: 'Track artists',
      library_track_artist: 'Track artists',
      album: 'Album title',
      library_album_title: 'Album title',
      library_album_assignment: 'Album grouping',
      title: 'Track title',
      library_title: 'Track title',
      track_number: 'Track number',
      disc_number: 'Disc number',
      library_track_number: 'Track number',
      library_disc_number: 'Disc number',
      library_recording_disambiguation: 'Recording description',
      filename: 'Filename',
      musicbrainz_recording_id: 'Recording ID',
      musicbrainz_release_id: 'MusicBrainz release ID',
      library_recording_mbid: 'Recording ID',
    }[field] || field.replaceAll('_', ' ');
  if (!locations || field === 'filename') return name;
  const base = name.replace(/ · (file tag|library)$/, '');
  const scope = locations.includes('library')
    ? locations.includes('file tag')
      ? 'file and library'
      : 'library'
    : 'file tag';
  return `${base} · ${scope}`;
}

export function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

export function records(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value) ? value.map(record).filter((item) => Object.keys(item).length) : [];
}

export function textValue(value: unknown): string {
  return typeof value === 'string' || typeof value === 'number' ? String(value) : '';
}

export function changeValue(value: unknown): string {
  if (value == null) return '';
  if (Array.isArray(value)) return value.map(changeValue).join(', ');
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

export function changeOperation(old: unknown, next: unknown) {
  const before = changeValue(old);
  const after = changeValue(next);
  if (before === after) return 'unchanged';
  if (!before.trim() && after.trim()) return 'add';
  if (before.trim() && !after.trim()) return 'remove';
  return 'replace';
}

export function isIdentifierField(field: string): boolean {
  return /(?:_id|_mbid)$/.test(field);
}

/** Merge identical file/library edits for display; preserve differing old or new values. */
export function displayChanges(changes: unknown) {
  const rows: Array<{ field: string; old: string; next: string; locations: string[] }> = [];
  for (const [field, raw] of Object.entries(record(changes))) {
    const value = record(raw);
    const old = changeValue(value.old_label ?? value.old);
    const next = changeValue(value.new_label ?? value.new);
    const semantic =
      {
        library_album_title: 'album',
        library_title: 'title',
        library_track_artist: 'track_artist',
        library_recording_mbid: 'musicbrainz_recording_id',
        library_disc_number: 'disc_number',
        library_track_number: 'track_number',
      }[field] || field;
    const location = field.startsWith('library_') ? 'library' : 'file tag';
    const existing = rows.find(
      (row) => row.field === semantic && row.old === old && row.next === next,
    );
    if (existing) {
      if (!existing.locations.includes(location)) existing.locations.push(location);
    } else rows.push({ field: semantic, old, next, locations: [location] });
  }
  return rows;
}

export function libraryRelativePath(path: string, root?: string): string {
  const prefix = root?.replace(/\/+$/, '');
  return prefix && path.startsWith(`${prefix}/`) ? path.slice(prefix.length + 1) : path;
}
