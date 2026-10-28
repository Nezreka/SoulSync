import { useState } from 'react';

import { DialogBody, DialogFooter, DialogFrame, DialogHeader } from '@/components/dialog';
import { Button, TextInput } from '@/components/form/form';

import type { ReviewFinding } from '../-review.api';
import type { FindingTypeInfo } from '../../tools/-tools.groups';

import { findingTypeLabel } from '../../tools/-tools.core';
import { CompletenessChoice } from './completeness-choice';
import { PlaylistReferences } from './playlist-references';
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
  onReset,
  onRecheck,
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
  onReset?: () => void;
  onRecheck?: (cleared: boolean) => void;
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
  const canAssign = Boolean(finding.file_path) && !numberingPreview && !waitingForDownload;
  const proposedSlot =
    canAssign && !wrongAudio && !duplicateChoice
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
  const planned = record(details.planned_choice);
  const [editing, setEditing] = useState(false);
  const hasPlan = Boolean(
    planned.action && (planned.category !== 'numbering' || details.plan_keep),
  );
  const assignmentConfirmed = Boolean(details.saved_assignment);
  const plannedAction = textValue(planned.fix_action);
  const requestState = finding.requests?.[0]?.state || textValue(details.fulfillment_state);
  const plannedLabel =
    planned.action === 'dismiss'
      ? 'Leave as-is planned'
      : plannedAction === 'replace' || plannedAction.startsWith('existing:')
        ? 'Replacement planned'
        : plannedAction === 'quarantine'
          ? 'Removal planned'
          : plannedAction === 'request_missing'
            ? 'New copy requested in this plan'
            : 'Changes planned';
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
      <PlaylistReferences
        caseId={caseId}
        path={finding.file_path}
        names={Array.isArray(details.playlists) ? details.playlists.map(String) : []}
        removal={plannedAction === 'quarantine' || plannedAction.startsWith('existing:quarantine')}
        lastError={finding.last_error}
        onRecheck={onRecheck}
        findingId={finding.id}
        evidenceToken={finding.evidence_token}
        overrideSaved={Array.isArray(details.playlist_override)}
      />
      {planned.category === 'numbering' && !hasPlan && (
        <div className={styles.confirmedStatus}>
          <strong>Numbering choice saved</strong>
          <span>Review the remaining metadata changes below.</span>
        </div>
      )}
      {assignmentConfirmed && !hasPlan && (
        <div className={styles.confirmedStatus} role="status">
          <strong>Match confirmed</strong>
          <span>
            {finding.review_tier === 'ready'
              ? 'The proposed changes below are ready to apply.'
              : 'Review the proposed metadata changes below.'}
          </span>
        </div>
      )}
      {hasPlan && (
        <div className={styles.plannedStatus} role="status">
          <strong>{plannedLabel}</strong>
          <span>
            {plannedAction === 'replace'
              ? ['wishlisted', 'downloading'].includes(requestState)
                ? 'The replacement is requested. Applying starts a verified handoff: the current file stays playable until the download and playlist references are ready.'
                : requestState === 'downloaded'
                  ? 'A download has arrived. Review its audio match before applying the saved file action.'
                  : `Request ${textValue(replacement.name || details.expected_title)} from Plan first. Applying then schedules a replacement while keeping this copy playable.`
              : 'Saved in this album plan. Files change when you apply it.'}
          </span>
          <div className={styles.inlineActions}>
            <Button disabled={busy} onClick={() => setEditing(!editing)}>
              {editing ? 'Done editing' : 'Edit choice'}
            </Button>
            {onReset && (
              <Button disabled={busy} onClick={onReset}>
                Undo choice
              </Button>
            )}
          </div>
        </div>
      )}
      <p className={styles.description}>
        {versionConflict
          ? 'The local audio and release track may be different versions. Listen before choosing how to keep them.'
          : compact
            ? compactReason || 'Review the supporting evidence.'
            : finding.description || 'Review the evidence below.'}
      </p>
      {!compact && (
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
            {!waitingForDownload && !intentChoice && (!hasPlan || editing) && (
              <>
                <div className={styles.actions}>
                  {directRepair && !wrongAudio && !versionConflict ? (
                    <Button
                      type="button"
                      variant="primary"
                      disabled={busy}
                      onClick={() => onAct('fix')}
                    >
                      {busy ? 'Saving…' : 'Accept changes'}
                    </Button>
                  ) : (
                    proposedSlot && (
                      <Button
                        type="button"
                        variant="primary"
                        disabled={busy}
                        onClick={() => onAct('fix', `assign:${textValue(proposedSlot.key)}`)}
                      >
                        {versionConflict ? 'This is the correct track' : 'Accept match'}
                      </Button>
                    )
                  )}
                  <Button
                    type="button"
                    disabled={busy || numberingPreview}
                    onClick={() => onAct('dismiss')}
                  >
                    Leave as-is
                  </Button>
                  {canApply &&
                    canAssign &&
                    (Boolean(replacement.source_track_id) ||
                      (['reconciliation_extra', 'reconciliation_unresolved'].includes(
                        finding.finding_type,
                      ) &&
                        Boolean(details.track_id) &&
                        !duplicateChoice &&
                        !records(details.competing_copies).length)) && (
                      <ReplacementChoice
                        key={JSON.stringify([
                          finding.id,
                          finding.evidence_token,
                          finding.file_path,
                          details.source,
                          details.release_id,
                          replacement,
                          details.replacement_files,
                        ])}
                        target={replacement}
                        canRemove={['reconciliation_extra', 'reconciliation_unresolved'].includes(
                          finding.finding_type,
                        )}
                        files={records(details.replacement_files)}
                        busy={busy}
                        onAct={onAct}
                      />
                    )}
                  {(!hasPlan || editing) && canAssign && assignmentOptions.length > 0 && (
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
                </div>
                <p className={styles.actionNote}>
                  Choices are saved in this plan. Files change when you apply it.
                </p>
              </>
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
        Change match…
      </Button>
      <DialogFrame open={open} onOpenChange={changeOpen}>
        <DialogHeader title="Change match">
          Keep {local} and choose which release track it represents.
        </DialogHeader>
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

function ReplacementChoice({
  target,
  files,
  busy,
  onAct,
  canRemove,
}: {
  target: Record<string, unknown>;
  files: Record<string, unknown>[];
  busy: boolean;
  canRemove: boolean;
  onAct: (action: 'fix', choice: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const canRequest = Boolean(target.source_track_id);
  const [source, setSource] = useState(canRequest ? 'new' : 'remove');
  const [file, setFile] = useState('');
  const changeOpen = (next: boolean) => {
    setOpen(next);
    if (!next) {
      setSource(canRequest ? 'new' : 'remove');
      setFile('');
    }
  };
  const choice =
    source === 'existing'
      ? `existing:quarantine:${file}`
      : source === 'remove'
        ? 'quarantine'
        : 'replace';
  return (
    <>
      <Button variant="danger" disabled={busy} onClick={() => changeOpen(true)}>
        Replace file…
      </Button>
      <DialogFrame open={open} onOpenChange={changeOpen}>
        <DialogHeader title="Replace file">
          Choose different audio for {textValue(target.name || target.title)} on this release.
        </DialogHeader>
        <DialogBody>
          <fieldset className={styles.remediationChoices}>
            <legend>Replacement</legend>
            {canRequest && (
              <label>
                <input
                  type="radio"
                  name="replacement-source"
                  checked={source === 'new'}
                  onChange={() => setSource('new')}
                />
                Find another copy<small>Request this exact track from the selected release.</small>
              </label>
            )}
            {files.length > 0 && (
              <label>
                <input
                  type="radio"
                  name="replacement-source"
                  checked={source === 'existing'}
                  onChange={() => setSource('existing')}
                />
                Use an existing file
                <small>Use another unassigned file already in this album.</small>
              </label>
            )}
            {canRemove && (
              <label>
                <input
                  type="radio"
                  name="replacement-source"
                  checked={source === 'remove'}
                  onChange={() => setSource('remove')}
                />
                Remove without replacement<small>This file does not belong in the album.</small>
              </label>
            )}
          </fieldset>
          {source === 'existing' && (
            <div className={styles.assignmentChoices} aria-label="Existing album files">
              {files.map((candidate) => (
                <Button
                  key={textValue(candidate.track_id)}
                  aria-pressed={file === textValue(candidate.track_id)}
                  onClick={() => setFile(textValue(candidate.track_id))}
                >
                  <span aria-hidden="true">♪</span>
                  <span>
                    <strong>{textValue(candidate.title)}</strong>
                    <small>{textValue(candidate.file_path).split('/').at(-1)}</small>
                  </span>
                  <small>{shortDuration(Number(candidate.duration_ms))}</small>
                </Button>
              ))}
            </div>
          )}
          <p className={styles.remediationSummary}>
            {source === 'new'
              ? 'Request a new copy. Keep the current file playable until the replacement and playlist handoff are verified, then move it to recoverable quarantine.'
              : source === 'existing'
                ? 'Use the selected file and move the current file to recoverable quarantine when you apply the plan.'
                : 'Move the current file to recoverable quarantine when you apply the plan.'}
          </p>
        </DialogBody>
        <DialogFooter>
          <Button onClick={() => changeOpen(false)}>Cancel</Button>
          <Button
            variant="danger"
            disabled={busy || (source === 'existing' && !file)}
            onClick={() => {
              changeOpen(false);
              onAct('fix', choice);
            }}
          >
            Save choice
          </Button>
        </DialogFooter>
      </DialogFrame>
    </>
  );
}
