import { useState } from 'react';

import { DialogBody, DialogFooter, DialogFrame, DialogHeader } from '@/components/dialog';
import { Button, TextInput } from '@/components/form/form';

import type { ReviewFinding } from '../-review.api';
import type { FindingTypeInfo } from '../../tools/-tools.groups';

import { findingTypeLabel } from '../../tools/-tools.core';
import { CompletenessChoice } from './completeness-choice';
import { ReconciliationEvidence, type ReleaseEvidence } from './reconciliation-evidence';
import { ReviewAudio } from './review-audio';
import styles from './review-page.module.css';
import { formatTrackPosition } from './review-position';
import { findingHeading, record, records, textValue, shortDuration } from './review-presentation';

export function SelectedActionDetail({
  finding,
  caseId,
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
  release,
}: {
  finding: ReviewFinding;
  caseId?: string;
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
  release?: ReleaseEvidence;
}) {
  const canApply =
    Boolean(typeInfo?.fixable) && finding.finding_type !== 'suspect_album_tag' && !numberingPreview;
  const assignmentOptions = records(record(finding.details).assignment_options);
  const duplicateChoice = finding.finding_type === 'reconciliation_duplicate';
  const intentChoice = finding.finding_type === 'reconciliation_intent';
  const directRepair =
    canApply &&
    ![
      'reconciliation_intent',
      'reconciliation_release',
      'reconciliation_duplicate',
      'reconciliation_unresolved',
      'reconciliation_extra',
    ].includes(finding.finding_type);
  const wrongAudio = finding.details?.audio_conflict === 'different_recording';
  const versionConflict = finding.details?.version_conflict === true;
  const waitingForDownload = ['wishlisted', 'downloading', 'downloaded'].includes(
    textValue(finding.details?.fulfillment_state),
  );
  const replacement = record(finding.details?.replacement_track);
  const localLength = Number(
    finding.details?.file_duration_ms || record(finding.details?.current_tags).duration_ms || 0,
  );
  const releaseLength = Number(
    finding.details?.expected_duration_ms || replacement.duration_ms || 0,
  );
  const lengthConflict =
    localLength > 0 && releaseLength > 0 && Math.abs(localLength - releaseLength) > 15000;
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
  const compactReason =
    finding.finding_type === 'reconciliation_extra'
      ? finding.description || finding.review_reason
      : finding.review_reason || finding.description;
  const details = record(finding.details);
  const localDurationMs = Number(
    details.file_duration_ms || record(details.current_tags).duration_ms || 0,
  );
  const releaseDurationMs = Number(
    details.expected_duration_ms || record(details.replacement_track).duration_ms || 0,
  );
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
        caseId={caseId}
        release={release}
        busy={busy}
        prominent={
          finding.finding_type === 'reconciliation_unresolved' || wrongAudio || versionConflict
        }
        onKeepCopy={duplicateChoice ? (trackId) => onAct('fix', trackId) : undefined}
        controls={
          <>
            {intentChoice && (
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
                    disabled={busy}
                    onClick={() => onAct('fix', `assign:${textValue(proposedSlot.key)}`)}
                  >
                    Use assignment in plan: {textValue(proposedSlot.title)}
                  </Button>
                )}
                {!intentChoice &&
                  (versionConflict || lengthConflict) &&
                  canApply &&
                  Boolean(replacement.source_track_id) && (
                    <>
                      <Button
                        type="button"
                        disabled={busy}
                        variant="primary"
                        onClick={() => onAct('fix', 'request_missing')}
                      >
                        Plan to keep local and request release version
                      </Button>
                      <Button type="button" disabled={busy} onClick={() => onAct('fix', 'replace')}>
                        Plan replacement with release version
                      </Button>
                    </>
                  )}
                {!intentChoice && !versionConflict && !lengthConflict && wrongAudio && canApply && (
                  <>
                    {canReplaceWrongAudio && (
                      <Button
                        type="button"
                        disabled={busy}
                        variant="primary"
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
                )}
                {directRepair && !versionConflict && !wrongAudio && (
                  <Button
                    type="button"
                    disabled={busy}
                    variant="primary"
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
                          : 'Apply this change'}
                  </Button>
                )}
                {!intentChoice && (
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
                current={textValue(details.expected_title || replacement.name)}
                local={
                  finding.file_path?.split('/').at(-1) ||
                  textValue(record(details.current_tags).title)
                }
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
                  comparisonTitle={textValue(
                    details.expected_title ||
                      record(details.replacement_track).title ||
                      record(details.replacement_track).name,
                  )}
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

/** A fresh draft belongs to one file and one release (the caller keys this component). */
function AssignmentChoice({
  options,
  numberingMode,
  saved,
  busy,
  onAct,
  current,
  local,
}: {
  options: Record<string, unknown>[];
  numberingMode: string;
  saved: boolean;
  busy: boolean;
  current: string;
  local: string;
  onAct: (action: 'fix', choice: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [slot, setSlot] = useState('');
  const [query, setQuery] = useState('');
  const changeOpen = (value: boolean) => {
    setOpen(value);
    if (!value) {
      setSlot('');
      setQuery('');
    }
  };
  const optionsShown = options.filter((option) =>
    [option.title, option.artist, option.track_number, option.disc_number]
      .join(' ')
      .toLocaleLowerCase()
      .includes(query.trim().toLocaleLowerCase()),
  );
  const save = (choice: string) => {
    changeOpen(false);
    onAct('fix', choice);
  };
  return (
    <>
      <Button type="button" disabled={busy} onClick={() => changeOpen(true)}>
        Change track assignment
      </Button>
      <DialogFrame open={open} onOpenChange={changeOpen}>
        <DialogHeader title="Change track assignment">Local file: {local}</DialogHeader>
        <DialogBody>
          <p>
            {saved ? 'Planned' : 'Proposed'} release track:{' '}
            <strong>{current || 'No assigned track'}</strong>
          </p>
          <TextInput
            aria-label="Find a release track"
            placeholder="Find a release track or artist…"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <div className={styles.assignmentChoices} aria-label="Release tracks">
            {optionsShown.map((option) => (
              <Button
                key={textValue(option.key)}
                type="button"
                aria-pressed={slot === textValue(option.key)}
                onClick={() => setSlot(textValue(option.key))}
                disabled={busy}
              >
                <span className={styles.assignmentPosition}>
                  {formatTrackPosition(
                    textValue(option.disc_number),
                    textValue(option.track_number),
                    numberingMode,
                    textValue(option.display_track_number) || undefined,
                  )}
                </span>
                <span>
                  <strong>{textValue(option.title)}</strong>
                  <small>{textValue(option.artist)}</small>
                </span>
                <small>{shortDuration(Number(option.duration_ms))}</small>
              </Button>
            ))}
            {!optionsShown.length && <p>No release tracks match this search.</p>}
          </div>
          <p className={styles.muted}>
            Saves this assignment in the album plan and recalculates proposed edits. Files change
            only when you apply the plan.
          </p>
        </DialogBody>
        <DialogFooter>
          {saved && (
            <Button disabled={busy} onClick={() => save('assign:auto')}>
              Reset plan assignment
            </Button>
          )}
          <Button onClick={() => changeOpen(false)}>Cancel</Button>
          <Button variant="primary" disabled={busy || !slot} onClick={() => save(`assign:${slot}`)}>
            Save plan assignment
          </Button>
        </DialogFooter>
      </DialogFrame>
    </>
  );
}
