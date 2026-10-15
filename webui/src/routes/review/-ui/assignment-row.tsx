import type { AlbumAssignment } from '../-review.api';

import styles from './review-page.module.css';
import { formatTrackPosition } from './review-position';
import {
  assignmentKey,
  changeFieldLabel,
  displayChanges,
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
  const evidence = row.audio_title
    ? `${row.audio_title}${row.audio_score ? ` · ${Math.round(row.audio_score * 100)}%` : ''}`
    : row.confidence || '';
  const visibleChanges = displayChanges(row.changes);
  const action =
    row.audio_conflict === 'different_recording'
      ? `Fingerprint identifies ${row.audio_title || 'a different recording'}`
      : row.relationship === 'version_conflict'
        ? 'Compare the local and release versions'
        : row.numbering_preview
          ? 'Numbering preview · choose the album numbering first'
          : row.action_state === 'preview'
            ? row.preview_reason || 'Resolve the album question first'
            : row.fulfillment_state === 'wishlisted'
              ? 'On wishlist · waiting for download'
              : row.status === 'settled'
                ? row.evidence_status === 'uncertain' || row.evidence_status === 'extra'
                  ? 'Previously reviewed · match remains unverified'
                  : 'Previously reviewed · no edit queued'
                : row.changes && Object.keys(row.changes).length
                  ? visibleChanges.map((change) => changeFieldLabel(change.field)).join(', ')
                  : row.status === 'uncertain'
                    ? 'Confirm assignment'
                    : row.title_difference === 'flavor'
                      ? 'Display wording only'
                      : row.title_difference === 'version_detail'
                        ? 'Version detail preserved'
                        : 'No metadata edit';
  return (
    <button
      type="button"
      className={styles.matrixRow}
      data-status={row.status}
      data-selected={selected || undefined}
      aria-pressed={selected}
      onClick={() => onSelect(row.finding_id ?? null, assignmentKey(row))}
    >
      <span className={styles.rowStatus}>
        <i>{statusLabel(row.status, row)}</i>
        {row.numbering_preview && row.status === 'settled' && <i>Numbering proposal pending</i>}
      </span>
      <span className={styles.rowPosition}>{position}</span>
      <span className={styles.trackComparison}>
        <span>
          <strong>{row.current_title || 'No local file'}</strong>
          <small>
            {row.file || 'Missing from this folder'}
            {row.file_duration_ms ? ` · ${shortDuration(row.file_duration_ms)}` : ''}
          </small>
        </span>
        <b aria-hidden="true">→</b>
        <span>
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
      </span>
      <span className={styles.rowResult}>
        <strong>{action}</strong>
        {visibleChanges.slice(0, 2).map((change) => (
          <small className={styles.rowChange} key={`${change.field}-${change.old}-${change.next}`}>
            {changeFieldLabel(change.field, change.locations)}: {change.old || 'empty'} →{' '}
            {change.next || 'empty'}
          </small>
        ))}
        {visibleChanges.length > 2 && <small>+{visibleChanges.length - 2} more changes</small>}
        {evidence ? <small>{evidence}</small> : null}
        {row.playlists?.length ? <small>Playlist: {row.playlists.join(', ')}</small> : null}
        {selected ? <em className={styles.rowAction}>Selected</em> : null}
      </span>
    </button>
  );
}
