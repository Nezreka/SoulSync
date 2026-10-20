import type { AlbumAssignment } from '../-review.api';

import styles from './review-page.module.css';
import { formatTrackPosition } from './review-position';
import {
  assignmentKey,
  metadataNeedsReview,
  shortDuration,
  statusLabel,
} from './review-presentation';

export function AssignmentRow({
  row,
  onSelect,
  selected,
  numberingMode,
}: {
  row: AlbumAssignment;
  onSelect: (findingId: number | null, rowKey: string) => void;
  selected: boolean;
  numberingMode?: string;
}) {
  const pairedVersion = row.relationship === 'version_conflict' && row.status === 'extra';
  const currentPosition = formatTrackPosition(
    row.current_disc_number,
    row.current_track_number,
    numberingMode,
  );
  const releasePosition = formatTrackPosition(
    row.counterpart_disc_number || row.slot_disc_number,
    row.counterpart_track_number ?? row.slot_track_number,
    numberingMode,
    row.counterpart_display_track_number ?? row.display_track_number,
  );
  const position = pairedVersion
    ? `${currentPosition} → ${releasePosition}`
    : formatTrackPosition(
        row.slot_disc_number || row.current_disc_number,
        row.slot_track_number ?? row.current_track_number,
        numberingMode,
        row.display_track_number,
      );
  return (
    <button
      type="button"
      data-review-assignment="true"
      className={styles.matrixRow}
      data-status={
        row.status === 'settled' && ['extra', 'duplicate'].includes(row.evidence_status || '')
          ? row.evidence_status
          : metadataNeedsReview(row)
            ? 'uncertain'
            : row.status
      }
      data-selected={selected || undefined}
      aria-pressed={selected}
      onClick={() => onSelect(row.finding_id ?? null, assignmentKey(row))}
    >
      <span className={styles.rowPosition}>{position}</span>
      <span className={styles.compactLocal}>
        <strong>{row.current_title || row.file || 'No local file'}</strong>
        <small>
          Local {currentPosition}
          {row.file_duration_ms ? ` · ${shortDuration(row.file_duration_ms)}` : ''}
        </small>
      </span>
      <span className={styles.compactRelease}>
        <strong>
          {(pairedVersion ? row.counterpart_title : row.slot_title) || 'No release slot'}
        </strong>
        <small>
          Release{' '}
          {pairedVersion
            ? releasePosition
            : formatTrackPosition(
                row.slot_disc_number,
                row.slot_track_number,
                numberingMode,
                row.display_track_number,
              )}
          {row.slot_duration_ms ? ` · ${shortDuration(row.slot_duration_ms)}` : ''}
        </small>
      </span>
      <span className={styles.rowStatus}>
        <i>{statusLabel(row.status, row)}</i>
        {row.companion_of && row.status !== 'confirmed' && <small>Intentional companion</small>}
        {row.status === 'settled' && row.audio_conflict && <small>Previously reviewed</small>}
        {row.fulfillment_state === 'wishlisted' && <small>Waiting for download</small>}
        {row.fulfillment_state === 'cancelled' && <small>Request cancelled · still wanted</small>}
        {row.fulfillment_state === 'downloaded' && (
          <small>Downloaded · checking library assignment</small>
        )}
        {row.numbering_preview && <small>Choose numbering first</small>}
      </span>
    </button>
  );
}
