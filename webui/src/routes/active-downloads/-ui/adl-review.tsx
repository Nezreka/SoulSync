import { useState } from 'react';

import type { AdlDownload, AdlQuarantineEntry, AdlSubView } from '../-adl.types';
import type { QuarantineGroup } from '../-adl.use-verification';

import {
  qualityChipTitle,
  quarantineSourceLabel,
  quarantineTrigger,
  reasonBadge,
  unverifiedReasonText,
} from '../-adl.helpers';
import { RowArt } from './adl-row';

/** Relative time via the shared vanilla formatter; '' when unavailable. */
function timeAgo(iso: string | undefined): string {
  if (!iso || typeof window.formatHistoryTime !== 'function') return '';
  return window.formatHistoryTime(iso);
}

export interface ReviewActionHandlers {
  onPlay: () => void;
  onCompare: (setBusy: (busy: boolean) => void) => void;
  onAudit: () => void;
  /** Approve, or Recover for a legacy quarantine sidecar. */
  onApprove: () => void;
  onDelete: () => void;
}

/**
 * The five review buttons.
 *
 * Compare owns a busy flag because the search runs server-side and can take
 * seconds — without it the button looks dead and gets clicked again.
 */
function ReviewButtons({
  handlers,
  approveGlyph,
  approveTitle,
  playTitle,
  compareTitle,
  auditTitle,
  deleteTitle,
}: {
  handlers: ReviewActionHandlers;
  approveGlyph: string;
  approveTitle: string;
  playTitle: string;
  compareTitle: string;
  auditTitle: string;
  deleteTitle: string;
}) {
  const [comparing, setComparing] = useState(false);
  const [busy, setBusy] = useState(false);

  return (
    <>
      <button
        type="button"
        className="verif-act verif-act-play"
        title={playTitle}
        onClick={handlers.onPlay}
      >
        ▶
      </button>
      <button
        type="button"
        className="verif-act"
        title={compareTitle}
        disabled={comparing}
        onClick={() => handlers.onCompare(setComparing)}
      >
        {comparing ? '…' : '⇆'}
      </button>
      <button type="button" className="verif-act" title={auditTitle} onClick={handlers.onAudit}>
        🔍
      </button>
      <button
        type="button"
        className="verif-act verif-act-ok"
        title={approveTitle}
        disabled={busy}
        onClick={() => {
          setBusy(true);
          handlers.onApprove();
          setBusy(false);
        }}
      >
        {approveGlyph}
      </button>
      <button
        type="button"
        className="verif-act verif-act-del"
        title={deleteTitle}
        onClick={handlers.onDelete}
      >
        🗑
      </button>
    </>
  );
}

const PLAY_TITLE = 'Play the downloaded file in the media player';
const COMPARE_TITLE =
  'Find this track on Soulseek/streaming sources and play it in the media player — compare against your file';
const AUDIT_TITLE =
  'Open the full audit trail for this download (lifecycle, embedded tags, lyrics)';
const APPROVE_TITLE =
  'Approve: mark as human-verified (tag + DB). The AcoustID scanner will skip it.';
const DELETE_TITLE = 'Wrong file: delete it from disk and remove this entry';

export function AdlUnverifiedRow({
  dl,
  open,
  onToggle,
  handlers,
}: {
  dl: AdlDownload;
  open: boolean;
  onToggle: () => void;
  handlers: ReviewActionHandlers | null;
}) {
  const badge = reasonBadge(dl);
  const meta = [dl.artist, dl.album].filter(Boolean).join(' · ');
  const source = dl.batch_source || dl.batch_name || '';
  const ago = timeAgo(dl.created_at);

  return (
    <div
      className="adl-row adl-row-completed verif-quar-row"
      data-task-id={dl.task_id || ''}
      title="Click to show/hide details (why it was flagged, source, quality, file)"
      onClick={onToggle}
    >
      <RowArt artwork={dl.artwork} />
      <div className="adl-row-info">
        <div className="adl-row-title">{dl.title || 'Unknown Track'}</div>
        {meta ? <div className="adl-row-meta">{meta}</div> : null}
        {source ? <div className="adl-row-batch">{source}</div> : null}
        <div className="verif-quar-details" style={{ display: open ? '' : 'none' }}>
          <div>
            <span className="verif-detail-label">Why flagged:</span> {unverifiedReasonText(dl)}
          </div>
          {source ? (
            <div>
              <span className="verif-detail-label">Download source:</span> {source}
            </div>
          ) : null}
          {dl.quality ? (
            <div>
              <span className="verif-detail-label">Quality:</span> {dl.quality}
            </div>
          ) : null}
          {/* Only persistent-history rows carry these two. */}
          {dl.file_path ? (
            <div>
              <span className="verif-detail-label">File:</span> {dl.file_path}
            </div>
          ) : null}
          {dl.created_at ? (
            <div>
              <span className="verif-detail-label">Downloaded:</span> {dl.created_at}
            </div>
          ) : null}
        </div>
      </div>
      <div className="verif-actions" onClick={(event) => event.stopPropagation()}>
        {badge ? (
          <span className={badge.className} title={badge.title}>
            {badge.label}
          </span>
        ) : null}
        {dl.quality ? (
          <span className="adl-quality-chip" title={qualityChipTitle()}>
            {dl.quality}
          </span>
        ) : null}
        {ago ? <span className="verif-time">{ago}</span> : null}
        {/* No history id means no endpoint to act on — the row is display-only. */}
        {handlers ? (
          <ReviewButtons
            handlers={handlers}
            approveGlyph="✔"
            approveTitle={APPROVE_TITLE}
            playTitle={PLAY_TITLE}
            compareTitle={COMPARE_TITLE}
            auditTitle={AUDIT_TITLE}
            deleteTitle={DELETE_TITLE}
          />
        ) : null}
      </div>
    </div>
  );
}

export function AdlQuarantineRow({
  entry,
  open,
  onToggle,
  handlers,
  altSlot,
}: {
  entry: AdlQuarantineEntry;
  open: boolean;
  onToggle: () => void;
  handlers: ReviewActionHandlers;
  altSlot?: React.ReactNode;
}) {
  const [triggerLabel, triggerClass] = quarantineTrigger(entry.trigger);
  const title = entry.expected_track || entry.original_filename || entry.filename || 'Unknown file';
  const meta = [entry.expected_artist, entry.original_filename].filter(Boolean).join(' — ');
  const sourceLabel = quarantineSourceLabel(entry);
  const ago = timeAgo(entry.timestamp);

  const details = [
    entry.reason ? { label: 'Reason:', value: entry.reason } : null,
    entry.source_username ? { label: 'Source uploader:', value: entry.source_username } : null,
    entry.source_filename
      ? { label: 'Original Soulseek file:', value: entry.source_filename }
      : null,
    entry.timestamp ? { label: 'Quarantined:', value: entry.timestamp } : null,
  ].filter(Boolean) as { label: string; value: string }[];

  return (
    <div
      className="adl-row adl-row-failed verif-quar-row"
      data-quarantine-id={entry.id}
      title="Click to show/hide details (reason, source uploader, original filename)"
      onClick={onToggle}
    >
      <RowArt artwork={entry.thumb_url} />
      <div className="adl-row-info">
        <div className="adl-row-title">{title}</div>
        {meta ? <div className="adl-row-meta">{meta}</div> : null}
        {sourceLabel ? <div className="adl-row-batch">{sourceLabel}</div> : null}
        <div className="verif-quar-details" style={{ display: open ? '' : 'none' }}>
          {details.length
            ? details.map((row) => (
                <div key={row.label}>
                  <span className="verif-detail-label">{row.label}</span> {row.value}
                </div>
              ))
            : 'No further details in the sidecar.'}
        </div>
      </div>
      <div className="verif-actions" onClick={(event) => event.stopPropagation()}>
        <span className={`verif-reason-badge ${triggerClass}`} title={entry.reason || ''}>
          {triggerLabel}
        </span>
        {entry.quality ? (
          <span
            className="adl-quality-chip"
            title="Audio quality of the quarantined file (read from the file itself)"
          >
            {entry.quality}
          </span>
        ) : null}
        {ago ? <span className="verif-time">{ago}</span> : null}
        <ReviewButtons
          handlers={handlers}
          // A legacy sidecar has no embedded context to re-import from, so its
          // only route back is Recover-to-Staging.
          approveGlyph={entry.has_full_context ? '✔' : '⤴'}
          approveTitle={
            entry.has_full_context
              ? 'Approve: re-import this exact file into the library, marked human-verified'
              : 'Recover to Staging for a manual import (legacy entry without embedded context)'
          }
          playTitle="Play the quarantined file in the media player"
          compareTitle="Find the expected track on Soulseek/streaming sources and play it in the media player — compare against the quarantined file"
          auditTitle="Open the audit trail for this quarantined file (details, embedded tags, lyrics)"
          deleteTitle="Delete the quarantined file permanently"
        />
      </div>
      <div className="verif-quar-alt-slot" onClick={(event) => event.stopPropagation()}>
        {altSlot}
      </div>
    </div>
  );
}

/**
 * Quarantine rows, with alternative candidates for the same track folded
 * behind a toggle.
 *
 * The first candidate shows as a normal row; the rest hide until expanded, so
 * a track that produced six rejected candidates takes one row rather than six.
 */
export function AdlQuarantineList({
  groups,
  openDetails,
  openGroups,
  onToggleDetails,
  onToggleGroup,
  handlersFor,
}: {
  groups: QuarantineGroup[];
  openDetails: ReadonlySet<string>;
  openGroups: ReadonlySet<string>;
  onToggleDetails: (id: string) => void;
  onToggleGroup: (key: string) => void;
  handlersFor: (entry: AdlQuarantineEntry) => ReviewActionHandlers;
}) {
  return (
    <>
      {groups.map((group) => {
        const [first, ...rest] = group.members;
        if (!first) return null;
        if (rest.length === 0) {
          return (
            <AdlQuarantineRow
              key={first.id}
              entry={first}
              open={openDetails.has(first.id)}
              onToggle={() => onToggleDetails(first.id)}
              handlers={handlersFor(first)}
            />
          );
        }

        const groupKey = group.key || first.id;
        const isOpen = openGroups.has(groupKey);
        return (
          <div className="verif-quar-alt-wrapper" key={groupKey}>
            <AdlQuarantineRow
              entry={first}
              open={openDetails.has(first.id)}
              onToggle={() => onToggleDetails(first.id)}
              handlers={handlersFor(first)}
              altSlot={
                <button
                  type="button"
                  className={`verif-quar-alt-btn${isOpen ? ' open' : ''}`}
                  data-group-key={groupKey}
                  data-alt-count={rest.length}
                  title={`Show ${rest.length} more alternative candidate${rest.length === 1 ? '' : 's'} for this track`}
                  onClick={() => onToggleGroup(groupKey)}
                >
                  {isOpen ? '▴' : '▾'} {rest.length} more
                </button>
              }
            />
            <div className={`verif-quar-alt-members${isOpen ? ' vqg-open' : ''}`}>
              {rest.map((entry) => (
                <AdlQuarantineRow
                  key={entry.id}
                  entry={entry}
                  open={openDetails.has(entry.id)}
                  onToggle={() => onToggleDetails(entry.id)}
                  handlers={handlersFor(entry)}
                />
              ))}
            </div>
          </div>
        );
      })}
    </>
  );
}

/**
 * The sub-view switcher above the review list.
 *
 * The bulk buttons differ per sub-view because the operations differ: an
 * unverified file can be orphaned (its file deleted elsewhere), a quarantined
 * one cannot — it IS the file.
 */
export function AdlReviewBanner({
  subView,
  acoustidEnabled,
  unverifiedCount,
  quarantineCount,
  quarantineLoaded,
  onSubView,
  onApproveAll,
  onCleanOrphans,
  onDeleteAll,
  onQuarantineApproveAll,
  onQuarantineClearAll,
}: {
  subView: AdlSubView;
  acoustidEnabled: boolean;
  unverifiedCount: number;
  quarantineCount: number;
  quarantineLoaded: boolean;
  onSubView: (view: AdlSubView) => void;
  onApproveAll: () => void;
  onCleanOrphans: () => void;
  onDeleteAll: () => void;
  onQuarantineApproveAll: () => void;
  onQuarantineClearAll: () => void;
}) {
  return (
    <div className="adl-batch-filter-banner" id="verif-subview-banner">
      {/* Hidden entirely when no unverified queue can exist. */}
      {acoustidEnabled ? (
        <button
          type="button"
          className={`adl-pill${subView === 'unverified' ? ' active' : ''}`}
          title="Imported files that AcoustID could not hard-confirm"
          onClick={() => onSubView('unverified')}
        >
          ⚠ Unverified ({unverifiedCount})
        </button>
      ) : null}
      <button
        type="button"
        className={`adl-pill${subView === 'quarantine' ? ' active' : ''}`}
        title="Files that failed verification and were NOT imported"
        onClick={() => onSubView('quarantine')}
      >
        {/* The count only appears once it is known, rather than showing (0). */}🛡 Quarantine
        {quarantineLoaded ? ` (${quarantineCount})` : ''}
      </button>
      <span className="verif-banner-spacer" />
      {subView === 'quarantine' ? (
        <>
          <button
            type="button"
            className="adl-filter-banner-clear"
            title="Approve + re-import every quarantined file (marked human-verified)"
            onClick={onQuarantineApproveAll}
          >
            ✔ Approve all
          </button>
          <button
            type="button"
            className="adl-filter-banner-clear verif-bulk-danger"
            title="Permanently delete every quarantined file"
            onClick={onQuarantineClearAll}
          >
            🗑 Clear all
          </button>
        </>
      ) : (
        <>
          <button
            type="button"
            className="adl-filter-banner-clear"
            title="Mark every listed entry as human-verified"
            onClick={onApproveAll}
          >
            ✔ Approve all
          </button>
          <button
            type="button"
            className="adl-filter-banner-clear"
            title="Remove dead entries whose file no longer exists (deleted / replaced). Removes log rows only — never a file."
            onClick={onCleanOrphans}
          >
            🧹 Clean orphaned
          </button>
          <button
            type="button"
            className="adl-filter-banner-clear verif-bulk-danger"
            title="Delete every listed file from disk and remove its entry"
            onClick={onDeleteAll}
          >
            🗑 Delete all
          </button>
        </>
      )}
    </div>
  );
}
