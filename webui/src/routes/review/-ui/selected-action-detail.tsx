import { useState } from 'react';

import { Button, Select } from '@/components/form/form';

import type { ReviewFinding } from '../-review.api';
import type { FindingTypeInfo } from '../../tools/-tools.groups';

import { findingTypeLabel } from '../../tools/-tools.core';
import { CompletenessChoice } from './completeness-choice';
import { ReconciliationEvidence } from './reconciliation-evidence';
import { ReviewAudio } from './review-audio';
import styles from './review-page.module.css';
import { formatTrackPosition } from './review-position';
import { findingHeading, record, records, textValue, shortDuration } from './review-presentation';

export function SelectedActionDetail({
  finding,
  itemIndex,
  itemCount,
  typeInfo,
  busy,
  onAct,
  onSelect,
  previousId,
  nextId,
  comparisonPath,
  comparisonDurationMs,
  compact = false,
  numberingPreview = false,
}: {
  finding: ReviewFinding;
  itemIndex: number;
  itemCount: number;
  typeInfo?: FindingTypeInfo;
  busy: boolean;
  onAct: (action: 'fix' | 'dismiss', choice?: string) => void;
  onSelect: (findingId: number | null) => void;
  previousId?: number;
  nextId?: number;
  comparisonPath?: string;
  comparisonDurationMs?: number;
  compact?: boolean;
  numberingPreview?: boolean;
}) {
  const canApply =
    Boolean(typeInfo?.fixable) && finding.finding_type !== 'suspect_album_tag' && !numberingPreview;
  const assignmentOptions = records(record(finding.details).assignment_options);
  const duplicateChoice = finding.finding_type === 'reconciliation_duplicate';
  const wrongAudio = finding.details?.audio_conflict === 'different_recording';
  const versionConflict = finding.details?.version_conflict === true;
  const waitingForDownload = ['wishlisted', 'downloading', 'downloaded'].includes(
    textValue(finding.details?.fulfillment_state),
  );
  const replacement = record(finding.details?.replacement_track);
  const canAssign = Boolean(finding.file_path) && !numberingPreview && !waitingForDownload;
  const proposedSlot =
    canAssign && !wrongAudio && !versionConflict && !duplicateChoice
      ? assignmentOptions.find(
          (slot) =>
            replacement.source_track_id &&
            textValue(slot.key) ===
              `id:${textValue(replacement.source_track_id)}:${textValue(replacement.disc_number || 1)}:${textValue(replacement.track_number || 0)}`,
        )
      : undefined;
  const canReplaceWrongAudio = Boolean(wrongAudio && replacement?.source_track_id);
  const suggested = finding.review_tier === 'ready';
  const compactReason = finding.review_reason || finding.description;
  const details = record(finding.details);
  const localDurationMs = Number(
    details.file_duration_ms || record(details.current_tags).duration_ms || 0,
  );
  const releaseDurationMs = Number(
    details.expected_duration_ms || record(details.replacement_track).duration_ms || 0,
  );
  const durationDifference =
    localDurationMs && releaseDurationMs ? Math.abs(localDurationMs - releaseDurationMs) : 0;
  return (
    <section
      className={styles.findingDetail}
      data-compact={compact || undefined}
      id="selected-review-action"
      aria-label="Selected action detail"
    >
      {!compact && (
        <div className={styles.decisionHeader}>
          <span>
            {suggested ? 'Ready change' : 'Needs review'} · {findingTypeLabel(finding.finding_type)}{' '}
            · Item {itemIndex + 1} of {itemCount}
          </span>
          <span className={styles.severity}>{finding.severity}</span>
        </div>
      )}
      <h3>{findingHeading(finding)}</h3>
      <p className={styles.description}>
        {versionConflict
          ? 'The local audio and release track may be different versions. Listen before choosing how to keep them.'
          : compact
            ? compactReason || 'Review the supporting evidence.'
            : finding.description || 'Review the evidence below.'}
      </p>
      <nav className={styles.decisionNavigation} aria-label="Decision navigation">
        <Button
          type="button"
          disabled={busy || previousId == null}
          onClick={() => onSelect(previousId ?? null)}
        >
          ← Previous decision
        </Button>
        <Button
          type="button"
          disabled={busy || nextId == null}
          onClick={() => onSelect(nextId ?? null)}
        >
          Next decision →
        </Button>
      </nav>
      {Array.isArray(details.competing_copies) && details.competing_copies.length > 0 && (
        <p className={styles.competingFileNotice}>
          Other local file{details.competing_copies.length === 1 ? '' : 's'} fitting this slot:{' '}
          {details.competing_copies.map((path) => textValue(path).split('/').pop()).join(', ')}
        </p>
      )}
      {wrongAudio && Boolean(details.acoustid_title) && (
        <div className={styles.identityWarning}>
          <strong>
            Fingerprint identifies {textValue(details.acoustid_title)}
            {details.acoustid_artist ? ` by ${textValue(details.acoustid_artist)}` : ''}
            {typeof details.acoustid_score === 'number'
              ? ` · ${Math.round(details.acoustid_score * 100)}%`
              : ''}
          </strong>
          <span>
            Expected release track:{' '}
            {textValue(details.expected_title || replacement.name) || 'No matching slot'}. Review
            the audio before changing this file.
          </span>
        </div>
      )}
      {durationDifference > 3000 && (
        <div className={styles.durationWarning}>
          <strong>Length differs by {(durationDifference / 1000).toFixed(1)} seconds</strong>
          <span>
            Local {shortDuration(localDurationMs)} · Release {shortDuration(releaseDurationMs)}
          </span>
        </div>
      )}
      {!compact && (
        <section
          className={styles.suggestion}
          data-tone={suggested ? 'good' : 'warning'}
          aria-label="Suggested resolution"
        >
          <span className={styles.suggestionIcon} aria-hidden="true">
            {suggested ? '✓' : '!'}
          </span>
          <div>
            <div className={styles.eyebrow}>Suggested resolution</div>
            <strong>
              {wrongAudio
                ? canReplaceWrongAudio
                  ? 'Replace with the expected release track'
                  : 'Move the wrong file to quarantine'
                : duplicateChoice
                  ? 'Choose the copy to keep'
                  : suggested
                    ? typeInfo?.verb || 'Apply repair'
                    : canApply
                      ? 'Choose the correct action after reviewing the evidence'
                      : 'Review manually'}
            </strong>
            <p>
              {wrongAudio
                ? canReplaceWrongAudio
                  ? 'Requests the exact track from the selected release, then moves this file to recoverable quarantine.'
                  : 'The audio is wrong for this release slot, but no exact replacement source is available.'
                : duplicateChoice
                  ? 'Both files identify as the same release track. Playlist membership is shown with each copy.'
                  : suggested
                    ? 'Evidence supports a direct repair.'
                    : finding.review_reason || 'The evidence still requires a decision.'}
            </p>
          </div>
        </section>
      )}
      <ReconciliationEvidence
        finding={finding}
        busy={busy}
        prominent={
          finding.finding_type === 'reconciliation_unresolved' || wrongAudio || versionConflict
        }
        onKeepCopy={duplicateChoice ? (trackId) => onAct('fix', trackId) : undefined}
        controls={
          <>
            {finding.finding_type === 'reconciliation_intent' && (
              <CompletenessChoice
                key={finding.id}
                finding={finding}
                busy={busy}
                onChoose={(choice) => onAct('fix', choice)}
              />
            )}
            {waitingForDownload && (
              <p className={styles.waitingNotice} role="status">
                {finding.details?.fulfillment_state === 'downloaded'
                  ? 'This track has downloaded. Reconciliation is checking its library assignment.'
                  : 'This track is on the wishlist. The album is waiting for it to download and join the library.'}
              </p>
            )}
            {numberingPreview && (
              <p className={styles.waitingNotice} role="status">
                This numbering change is a preview. Choose the album numbering on Plan first.
              </p>
            )}
            {!waitingForDownload && (
              <div className={styles.actions}>
                {proposedSlot && (
                  <Button
                    type="button"
                    variant="primary"
                    className={styles.primary}
                    disabled={busy}
                    onClick={() => onAct('fix', `assign:${textValue(proposedSlot.key)}`)}
                  >
                    Use assignment in plan: {textValue(proposedSlot.title)}
                  </Button>
                )}
                {finding.finding_type === 'reconciliation_intent' ? null : versionConflict &&
                  canApply ? (
                  <>
                    {replacement.source_track_id && (
                      <>
                        <Button
                          type="button"
                          disabled={busy}
                          variant="primary"
                          className={styles.primary}
                          onClick={() => onAct('fix', 'request_missing')}
                        >
                          Plan to keep local and request release version
                        </Button>
                        <Button
                          type="button"
                          disabled={busy}
                          onClick={() => onAct('fix', 'replace')}
                        >
                          Plan replacement with release version
                        </Button>
                      </>
                    )}
                  </>
                ) : wrongAudio && canApply ? (
                  <>
                    {canReplaceWrongAudio && (
                      <Button
                        type="button"
                        disabled={busy}
                        variant="primary"
                        className={styles.primary}
                        onClick={() => onAct('fix', 'replace')}
                      >
                        {busy ? 'Saving…' : 'Plan replacement with correct track'}
                      </Button>
                    )}
                    <Button
                      type="button"
                      disabled={busy}
                      onClick={() => onAct('fix', 'quarantine')}
                    >
                      Plan quarantine of wrong file
                    </Button>
                  </>
                ) : (
                  <>
                    {canApply &&
                      finding.finding_type !== 'reconciliation_release' &&
                      finding.finding_type !== 'reconciliation_duplicate' &&
                      finding.finding_type !== 'reconciliation_unresolved' &&
                      finding.finding_type !== 'reconciliation_extra' && (
                        <Button
                          type="button"
                          disabled={busy}
                          variant="primary"
                          className={styles.primary}
                          onClick={() => onAct('fix')}
                        >
                          {finding.finding_type === 'reconciliation_metadata'
                            ? busy
                              ? 'Saving…'
                              : 'Use proposed change'
                            : busy
                              ? 'Applying…'
                              : suggested
                                ? typeInfo?.verb || 'Apply repair'
                                : finding.finding_type === 'reconciliation_extra'
                                  ? 'Plan quarantine'
                                  : 'Apply this change'}
                        </Button>
                      )}
                  </>
                )}
                {finding.finding_type !== 'reconciliation_intent' && (
                  <Button
                    type="button"
                    disabled={busy || numberingPreview}
                    onClick={() => onAct('dismiss')}
                  >
                    {versionConflict ? 'Keep local version' : 'Keep current values'}
                  </Button>
                )}
              </div>
            )}

            {canAssign && assignmentOptions.length > 0 && (
              <AssignmentChoice
                key={JSON.stringify([
                  finding.id,
                  finding.file_path,
                  details.source,
                  details.release_id,
                  assignmentOptions.map((slot) => slot.key),
                ])}
                options={assignmentOptions}
                numberingMode={textValue(details.numbering_mode)}
                saved={details.saved_assignment === true}
                busy={busy}
                onAct={onAct}
              />
            )}

            {['reconciliation_extra', 'reconciliation_unresolved'].includes(finding.finding_type) &&
              finding.file_path &&
              Boolean(details.track_id) &&
              details.album_id != null &&
              !wrongAudio &&
              !versionConflict &&
              !duplicateChoice &&
              !(details.competing_copies as unknown[] | undefined)?.length && (
                <details className={styles.extraAction}>
                  <summary>Move this file out of the album</summary>
                  <span>
                    If this copy does not belong here, move it to recoverable quarantine and remove
                    its library entry.
                  </span>
                  <Button type="button" disabled={busy} onClick={() => onAct('fix', 'quarantine')}>
                    Plan move to quarantine
                  </Button>
                </details>
              )}
          </>
        }
        audio={
          <>
            {finding.file_path &&
              [
                'reconciliation_metadata',
                'reconciliation_unresolved',
                'reconciliation_extra',
              ].includes(finding.finding_type) && (
                <ReviewAudio
                  localPath={finding.file_path}
                  localTitle={
                    textValue(details.current_title || record(details.current_tags).title) ||
                    finding.title
                  }
                  localDurationMs={localDurationMs}
                  comparisonPath={comparisonPath}
                  comparisonTitle={
                    textValue(details.expected_title || record(details.replacement_track).name) ||
                    'Release recording'
                  }
                  comparisonDurationMs={comparisonDurationMs || releaseDurationMs}
                  previewUrl={textValue(
                    details.expected_preview_url || record(details.replacement_track).preview_url,
                  )}
                  artist={textValue(details.expected_artist || record(details.current_tags).artist)}
                  previewSource={textValue(details.source)}
                  previewTrackId={textValue(record(details.replacement_track).source_track_id)}
                />
              )}
          </>
        }
      />
    </section>
  );
}

/** Reset a draft selection whenever its file, release, or available slots change. */
function AssignmentChoice({
  options,
  numberingMode,
  saved,
  busy,
  onAct,
}: {
  options: Record<string, unknown>[];
  numberingMode: string;
  saved: boolean;
  busy: boolean;
  onAct: (action: 'fix', choice: string) => void;
}) {
  const [slot, setSlot] = useState('');
  return (
    <details className={styles.actionDisclosure}>
      <summary>Change track assignment</summary>
      <div className={styles.assignmentCorrection}>
        <label>
          Assign this file to another release track
          <Select value={slot} disabled={busy} onChange={(event) => setSlot(event.target.value)}>
            <option value="">Choose a release track…</option>
            {options.map((option, index) => (
              <option value={textValue(option.key)} key={`${textValue(option.key)}-${index}`}>
                {formatTrackPosition(
                  textValue(option.disc_number),
                  textValue(option.track_number),
                  numberingMode,
                  textValue(option.display_track_number) || undefined,
                )}{' '}
                {textValue(option.title)}
              </option>
            ))}
          </Select>
        </label>
        <Button
          type="button"
          disabled={busy || !slot}
          onClick={() => onAct('fix', `assign:${slot}`)}
        >
          Save plan assignment
        </Button>
        {saved && (
          <Button type="button" disabled={busy} onClick={() => onAct('fix', 'assign:auto')}>
            Reset plan assignment
          </Button>
        )}
        <small>
          This saves your plan choice and recalculates proposed edits. Files stay unchanged until
          you apply them.
        </small>
      </div>
    </details>
  );
}
