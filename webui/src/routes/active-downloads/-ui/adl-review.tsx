/**
 * The music Review view: Unverified, Quarantine and Deleted, on the same row
 * grammar as Downloads (dl-parts). One clear action per row, everything else
 * behind ⋯, and a sheet that says why the file is here.
 */

import { useState } from 'react';

import type { AdlDeletedEntry, AdlDownload, AdlQuarantineEntry, AdlSubView } from '../-adl.types';
import type { QuarantineGroup } from '../-adl.use-verification';
import type { DlFact } from './dl-parts';

import { formatBytes, quarantineSourceLabel } from '../-adl.helpers';
import { formatTag, isHighQuality, musicJourney, parseStamp, relTime } from '../-dl.format';
import {
  DlAvatar,
  DlButton,
  DlFileBadge,
  DlIcon,
  DlLead,
  DlMore,
  DlSegmented,
  DlSelectionBar,
  DlSheet,
  fileExt,
} from './dl-parts';

export interface ReviewActionHandlers {
  onPlay: () => void;
  onCompare: (setBusy: (busy: boolean) => void) => void;
  onAudit: () => void;
  /** Approve, or Recover for a legacy quarantine sidecar. */
  onApprove: () => void;
  onDelete: () => void;
}

const CheckIcon = (
  <svg
    width="11"
    height="11"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="3.5"
    aria-hidden="true"
  >
    <polyline points="20 6 9 17 4 12" />
  </svg>
);

/** The select box that sits on a row's art corner. */
function SelectBox({ selected, onSelect }: { selected: boolean; onSelect: () => void }) {
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={selected}
      aria-label={selected ? 'Deselect' : 'Select'}
      className={`dlc-check${selected ? ' dlc-check-on' : ''}`}
      title="Select for bulk approve or delete"
      onClick={(e) => {
        e.stopPropagation();
        onSelect();
      }}
    >
      {selected ? CheckIcon : null}
    </button>
  );
}

/**
 * Compare runs a server-side search that can take seconds, so the action owns
 * a busy flag. without it the button looks dead and gets clicked again.
 */
function useCompare(handlers: ReviewActionHandlers | null) {
  const [busy, setBusy] = useState(false);
  return {
    busy,
    run: () => {
      if (!handlers || busy) return;
      handlers.onCompare(setBusy);
    },
  };
}

/** Asked for vs got, with the difference lit. Null when there's nothing to compare. */
function AskedVsGot({
  asked,
  got,
}: {
  asked: { title?: string; artist?: string };
  got: { title?: string; artist?: string };
}) {
  if (!asked.title) return null;
  const diff = (a?: string, b?: string) =>
    Boolean(a && b && a.trim().toLowerCase() !== b.trim().toLowerCase());
  return (
    <div className="dlc-compare">
      <span />
      <span className="dlc-compare-h">Asked for</span>
      <span className="dlc-compare-h">Got</span>
      <span className="dlc-compare-k">Title</span>
      <span>{asked.title}</span>
      <span className={diff(asked.title, got.title) ? 'dlc-compare-diff' : undefined}>
        {got.title || '—'}
      </span>
      {asked.artist ? (
        <>
          <span className="dlc-compare-k">Artist</span>
          <span>{asked.artist}</span>
          <span className={diff(asked.artist, got.artist) ? 'dlc-compare-diff' : undefined}>
            {got.artist || '—'}
          </span>
        </>
      ) : null}
    </div>
  );
}

// ── Unverified ────────────────────────────────────────────────────────────

/** the reason tag on an unverified row. */
function unverifiedTag(
  dl: AdlDownload,
): { text: string; tone: 'warn' | 'info'; title: string } | null {
  if (dl.verification_status === 'force_imported') {
    return {
      text: 'Force-imported',
      tone: 'info',
      title: 'Kept as the best available copy after the retry budget ran out',
    };
  }
  if (dl.verification_status === 'unverified') {
    return {
      text: 'Not confirmed',
      tone: 'warn',
      title: "AcoustID couldn't confirm it (ambiguous, cross-script, or no fingerprint match)",
    };
  }
  return null;
}

export function AdlUnverifiedRow({
  dl,
  open,
  onToggle,
  handlers,
  selected,
  onSelect,
}: {
  dl: AdlDownload;
  open: boolean;
  onToggle: () => void;
  handlers: ReviewActionHandlers | null;
  /** Multi-select for partial bulk approve/delete; absent = display-only row. */
  selected?: boolean;
  onSelect?: () => void;
}) {
  const compare = useCompare(handlers);
  const tag = unverifiedTag(dl);
  const quality = formatTag(dl.quality);
  const when = relTime(parseStamp(dl.created_at ?? null), Date.now());
  const who = [dl.artist, dl.album].filter(Boolean).join(' · ');
  const source = dl.download_source || dl.batch_source || dl.batch_name || '';

  const menu = handlers
    ? [
        { key: 'play', label: 'Play my copy', onSelect: handlers.onPlay },
        {
          key: 'compare',
          label: compare.busy ? 'Finding a stream…' : 'Compare with a stream',
          disabled: compare.busy,
          onSelect: compare.run,
        },
        { key: 'audit', label: 'Download details', onSelect: handlers.onAudit },
        { key: 'delete', label: 'Delete file', danger: true, onSelect: handlers.onDelete },
      ]
    : [];

  const facts: DlFact[] = [
    ['Source', source],
    ['Quality', quality],
    ['AcoustID', dl.acoustid_result || ''],
    ['File', dl.file_path || '', { mono: true, copy: dl.file_path || undefined }],
  ];

  return (
    <>
      <div
        className={`dlc-row dlc-row-completed${open ? ' dlc-row-open' : ''}${selected ? ' dlc-row-sel' : ''}`}
        data-task-id={dl.task_id || ''}
        role="button"
        tabIndex={0}
        aria-expanded={open}
        onClick={onToggle}
        onKeyDown={(e) => {
          if (e.target !== e.currentTarget) return;
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            onToggle();
          }
        }}
      >
        <div className="dlc-lead-wrap">
          <DlLead
            art={dl.artwork}
            avatar={dl.artist_image}
            onPlay={handlers?.onPlay}
            fallback={(dl.title || '?').charAt(0).toUpperCase()}
          />
          {onSelect ? <SelectBox selected={Boolean(selected)} onSelect={onSelect} /> : null}
        </div>
        <div className="dlc-main">
          <div className="dlc-title">{dl.title || 'Unknown Track'}</div>
          {who ? (
            <div className="dlc-sub">
              <DlAvatar src={dl.artist_image} />
              {who}
            </div>
          ) : null}
        </div>
        <div className="dlc-meta">
          {tag ? (
            <span className={`dlc-tag dlc-tag-${tag.tone}`} title={tag.title}>
              {tag.text}
            </span>
          ) : null}
          {quality ? (
            <span className={`dlc-tag${isHighQuality(quality) ? ' dlc-tag-hq' : ''}`}>
              {quality}
            </span>
          ) : null}
          {when ? <span className="dlc-when">{when}</span> : null}
        </div>
        <div className="dlc-acts">
          {/* no history id means no endpoint to act on: the row is display-only */}
          {handlers ? (
            <>
              <DlButton primary onClick={handlers.onApprove}>
                Approve
              </DlButton>
              <DlMore items={menu} />
            </>
          ) : null}
        </div>
      </div>
      {open ? (
        <DlSheet
          stepsTitle="Why it's here"
          steps={musicJourney(dl, null, Date.now())}
          factsTitle="Asked for vs got"
          facts={facts}
          aside={
            <AskedVsGot
              asked={{ title: dl.expected_title, artist: dl.expected_artist }}
              got={{ title: dl.title, artist: dl.artist }}
            />
          }
          actions={
            handlers ? (
              <>
                <DlButton primary onClick={handlers.onApprove}>
                  Approve
                </DlButton>
                <DlButton icon={DlIcon.play} onClick={handlers.onPlay}>
                  Play my copy
                </DlButton>
                <DlButton disabled={compare.busy} onClick={compare.run}>
                  {compare.busy ? 'Finding a stream…' : 'Compare with a stream'}
                </DlButton>
                <DlButton onClick={handlers.onAudit}>Download details</DlButton>
                <span className="dlc-spacer" />
                <DlButton danger onClick={handlers.onDelete}>
                  Delete file
                </DlButton>
              </>
            ) : null
          }
        />
      ) : null}
    </>
  );
}

// ── Quarantine ────────────────────────────────────────────────────────────

const TRIGGER_TAGS: Record<string, string> = {
  integrity: 'Failed integrity check',
  acoustid: 'AcoustID mismatch',
  acoustid_unverified: 'Not confirmed',
  bit_depth: 'Bit depth filter',
};

function triggerTag(trigger: string | undefined): string {
  return TRIGGER_TAGS[String(trigger ?? '')] ?? 'Quarantined';
}

function libraryLine(entry: AdlQuarantineEntry): string {
  const m = entry.library_match;
  return [m?.album, m?.quality, m?.verification_status?.replaceAll('_', ' ')]
    .filter(Boolean)
    .join(' · ');
}

function albumOrigin(entry: AdlQuarantineEntry): string {
  const source = entry.album_extra?.download_source.toLowerCase();
  const label = source === 'usenet' ? 'Usenet ' : source === 'torrent' ? 'torrent ' : '';
  return [`An extra track from a ${label}album import`, entry.album_extra?.album]
    .filter(Boolean)
    .join(' · ');
}

/** quarantine stamps are local isoformat; Date.parse reads a naive one as local. */
function quarantineTime(entry: AdlQuarantineEntry): string {
  const ms = entry.timestamp ? Date.parse(entry.timestamp) : NaN;
  return Number.isFinite(ms) ? relTime(ms, Date.now()) : '';
}

export function AdlQuarantineRow({
  entry,
  open,
  onToggle,
  handlers,
  inGroup,
}: {
  entry: AdlQuarantineEntry;
  open: boolean;
  onToggle: () => void;
  handlers: ReviewActionHandlers;
  /** one of several attempts under a group header: the header already names the track. */
  inGroup?: boolean;
}) {
  const compare = useCompare(handlers);
  const owned = entry.library_status === 'present';
  const legacy = !entry.has_full_context;
  const fileName = entry.original_filename || entry.filename || 'Unknown file';
  const title = inGroup ? fileName : entry.expected_track || fileName;
  const source = quarantineSourceLabel(entry);
  const uploader =
    entry.source_username && source === 'Soulseek' ? `from ${entry.source_username}` : '';
  const sub = inGroup
    ? [source, uploader].filter(Boolean).join(' · ')
    : [entry.expected_artist, source, uploader].filter(Boolean).join(' · ');
  const quality = formatTag(entry.quality);
  const when = quarantineTime(entry);
  const approveLabel = legacy ? 'Recover' : 'Approve';
  const approveTitle = legacy
    ? 'Recover to Staging for a manual import (an older entry without its import context)'
    : 'Import this exact file into the library, marked human-verified';

  // the track is already in the library: this copy is a spare, so Delete leads
  const primary = owned ? (
    <DlButton primary onClick={handlers.onDelete}>
      Delete
    </DlButton>
  ) : (
    <DlButton primary title={approveTitle} onClick={handlers.onApprove}>
      {approveLabel}
    </DlButton>
  );
  const menu = [
    { key: 'play', label: 'Play it', onSelect: handlers.onPlay },
    {
      key: 'compare',
      label: compare.busy ? 'Finding a stream…' : 'Compare with a stream',
      disabled: compare.busy,
      onSelect: compare.run,
    },
    { key: 'audit', label: 'Download details', onSelect: handlers.onAudit },
    owned
      ? {
          key: 'approve',
          label: `${approveLabel} anyway`,
          title: approveTitle,
          onSelect: handlers.onApprove,
        }
      : { key: 'delete', label: 'Delete file', danger: true, onSelect: handlers.onDelete },
  ];

  const facts: DlFact[] = [
    ['Uploader', entry.source_username || ''],
    ['Original file', entry.source_filename || entry.original_filename || '', { mono: true }],
    ['Quality', quality],
    [
      'Library',
      owned
        ? libraryLine(entry) || 'Already in your library'
        : entry.library_status === 'missing'
          ? 'No copy in your library'
          : entry.library_status === 'unknown'
            ? 'Library check unavailable'
            : '',
    ],
    ['Origin', entry.album_extra ? albumOrigin(entry) : ''],
    ['Size', entry.size_bytes ? formatBytes(entry.size_bytes) : ''],
  ];

  return (
    <>
      <div
        className={`dlc-row dlc-row-failed${open ? ' dlc-row-open' : ''}${inGroup ? ' dlc-row-compact' : ''}`}
        data-quarantine-id={entry.id}
        role="button"
        tabIndex={0}
        aria-expanded={open}
        onClick={onToggle}
        onKeyDown={(e) => {
          if (e.target !== e.currentTarget) return;
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            onToggle();
          }
        }}
      >
        <div className="dlc-lead-wrap">
          {entry.thumb_url && !inGroup ? (
            <DlLead art={entry.thumb_url} onPlay={handlers.onPlay} />
          ) : (
            <DlFileBadge
              ext={fileExt(entry.original_filename, entry.filename, entry.source_filename)}
            />
          )}
        </div>
        <div className="dlc-main">
          <div className="dlc-title">
            {title}
            {owned && !inGroup ? (
              <span className="dlc-owned" title={libraryLine(entry) || 'Already in your library'}>
                {DlIcon.check}
                In your library
              </span>
            ) : null}
          </div>
          {sub ? <div className="dlc-sub">{sub}</div> : null}
        </div>
        <div className="dlc-meta">
          {entry.album_extra ? (
            <span className="dlc-tag" title={albumOrigin(entry)}>
              Album extra
            </span>
          ) : null}
          <span className="dlc-tag dlc-tag-bad" title={entry.reason || ''}>
            {triggerTag(entry.trigger)}
          </span>
          {quality ? <span className="dlc-tag">{quality}</span> : null}
          {when ? <span className="dlc-when">{when}</span> : null}
        </div>
        <div className="dlc-acts">
          {primary}
          <DlMore items={menu} />
        </div>
      </div>
      {open ? (
        <DlSheet
          stepsTitle="Why it's here"
          steps={[
            {
              key: 'download',
              state: 'done',
              title: source
                ? `Downloaded from ${source === 'Soulseek' && entry.source_username ? entry.source_username : source}`
                : 'Downloaded',
              detail: entry.source_filename || entry.original_filename || undefined,
            },
            {
              key: 'check',
              state: 'fail',
              title: triggerTag(entry.trigger),
              detail: entry.reason || 'It failed its checks, so it was never imported.',
              time: when,
            },
          ]}
          factsTitle="Details"
          facts={facts}
          aside={
            <>
              {owned ? (
                <div className="dlc-callout">
                  {DlIcon.check}
                  <div>
                    <b>You already have this track.</b>
                    <p>
                      {libraryLine(entry) ? `${libraryLine(entry)}. ` : ''}Deleting this copy is
                      safe.
                    </p>
                  </div>
                </div>
              ) : null}
              <AskedVsGot
                asked={{ title: entry.expected_track, artist: entry.expected_artist }}
                // the file name, minus its extension and any leading track number
                got={{
                  title: (entry.original_filename || '')
                    .replace(/\.[a-z0-9]{2,5}$/i, '')
                    .replace(/^\d{1,3}[\s._-]+/, ''),
                }}
              />
            </>
          }
          actions={
            <>
              {primary}
              <DlButton icon={DlIcon.play} onClick={handlers.onPlay}>
                Play it
              </DlButton>
              <DlButton disabled={compare.busy} onClick={compare.run}>
                {compare.busy ? 'Finding a stream…' : 'Compare with a stream'}
              </DlButton>
              <DlButton onClick={handlers.onAudit}>Download details</DlButton>
              <span className="dlc-spacer" />
              {owned ? (
                <DlButton title={approveTitle} onClick={handlers.onApprove}>
                  {approveLabel} anyway
                </DlButton>
              ) : (
                <DlButton danger onClick={handlers.onDelete}>
                  Delete file
                </DlButton>
              )}
            </>
          }
        />
      ) : null}
    </>
  );
}

/**
 * Quarantine rows. A track that several downloads tried to be gets one
 * header (the track, how many attempts, why they failed, delete them all)
 * with its attempts folded under it, so six rejected candidates take one line.
 */
export function AdlQuarantineList({
  groups,
  partial,
  openDetails,
  openGroups,
  onToggleDetails,
  onToggleGroup,
  handlersFor,
  onDeleteGroup,
}: {
  groups: QuarantineGroup[];
  /** True when more pages are not loaded yet, so group counts are page-local. */
  partial?: boolean;
  openDetails: ReadonlySet<string>;
  openGroups: ReadonlySet<string>;
  onToggleDetails: (id: string) => void;
  onToggleGroup: (key: string) => void;
  handlersFor: (entry: AdlQuarantineEntry) => ReviewActionHandlers;
  /** Delete a whole group of candidates at once; absent = per-row deletes only. */
  onDeleteGroup?: (entry: AdlQuarantineEntry, count: number | null) => void;
}) {
  return (
    <div className="dlc-list">
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
        const n = group.members.length;
        const owned = group.members.some((m) => m.library_status === 'present');
        const reasons = [...new Set(group.members.map((m) => triggerTag(m.trigger)))].join(', ');
        const attemptsText = partial ? `${n}+ attempts` : `${n} attempts`;
        return (
          <div className="dlc-sub-group" key={groupKey}>
            <div
              className="dlc-qhead"
              role="button"
              tabIndex={0}
              aria-expanded={isOpen}
              data-group-key={groupKey}
              onClick={() => onToggleGroup(groupKey)}
              onKeyDown={(e) => {
                if (e.target !== e.currentTarget) return;
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  onToggleGroup(groupKey);
                }
              }}
            >
              <span className="dlc-fold-chev" aria-hidden="true">
                {DlIcon.fold}
              </span>
              {first.thumb_url ? (
                <img className="dlc-art dlc-qhead-art" src={first.thumb_url} alt="" />
              ) : (
                <DlFileBadge ext={fileExt(first.original_filename, first.filename)} />
              )}
              <div className="dlc-main">
                <div className="dlc-title">
                  {first.expected_track || first.original_filename || 'Unknown track'}
                  <span
                    className="dlc-attempts"
                    data-alt-count={n - 1}
                    title={
                      partial
                        ? `At least ${n} downloads tried to be this track (the list is paged)`
                        : `${n} downloads tried to be this track`
                    }
                  >
                    {attemptsText}
                  </span>
                </div>
                <div className="dlc-sub">
                  {[first.expected_artist, reasons].filter(Boolean).join(' · ')}
                </div>
              </div>
              <div className="dlc-acts">
                {owned ? (
                  <span className="dlc-owned">
                    {DlIcon.check}
                    In your library
                  </span>
                ) : null}
                {onDeleteGroup ? (
                  <DlButton
                    data-group-key={groupKey}
                    title={
                      partial
                        ? 'Delete every quarantined attempt at this track (the server finds the whole group, including ones not loaded yet)'
                        : `Delete all ${n} quarantined attempts at this track`
                    }
                    onClick={() => onDeleteGroup(first, partial ? null : n)}
                  >
                    {partial ? 'Delete all attempts' : `Delete all ${n}`}
                  </DlButton>
                ) : null}
              </div>
            </div>
            {isOpen
              ? group.members.map((entry) => (
                  <AdlQuarantineRow
                    key={entry.id}
                    entry={entry}
                    inGroup
                    open={openDetails.has(entry.id)}
                    onToggle={() => onToggleDetails(entry.id)}
                    handlers={handlersFor(entry)}
                  />
                ))
              : null}
          </div>
        );
      })}
    </div>
  );
}

/** "2 tracks · 7 files", or just "3 files" when every file is its own track. */
export function quarantineSummary(tracks: number, files: number): string {
  const f = `${files} file${files === 1 ? '' : 's'}`;
  if (tracks === files) return f;
  return `${tracks} track${tracks === 1 ? '' : 's'} · ${f}`;
}

// ── the sub-switch + bulk actions ─────────────────────────────────────────

/**
 * The sub-view switch above the review list, with the whole-list actions in
 * its ⋯. checked rows get their own bar at the bottom (AdlReviewSelectionBar)
 * instead of these buttons changing their labels under you.
 */
export function AdlReviewBanner({
  subView,
  acoustidEnabled,
  unverifiedCount,
  quarantineCount,
  quarantineLoaded,
  quarantineTrackCount = null,
  deletedCount,
  onSubView,
  onApproveAll,
  onCleanOrphans,
  onDeleteAll,
  onQuarantineApproveAll,
  onQuarantineClearAll,
  onRestoreAllDeleted,
  onEmptyDeleted,
}: {
  subView: AdlSubView;
  acoustidEnabled: boolean;
  unverifiedCount: number;
  quarantineCount: number;
  quarantineLoaded: boolean;
  /** distinct tracks behind the quarantined files, null until the list loads. */
  quarantineTrackCount?: number | null;
  deletedCount: number | null;
  onSubView: (view: AdlSubView) => void;
  onApproveAll: () => void;
  onCleanOrphans: () => void;
  onDeleteAll: () => void;
  onQuarantineApproveAll: () => void;
  onQuarantineClearAll: () => void;
  onRestoreAllDeleted: () => void;
  onEmptyDeleted: () => void;
}) {
  // bulk actions only when the view has something to act on: a bulk button
  // over an empty list is a promise the click can't keep
  const menu =
    subView === 'deleted'
      ? (deletedCount ?? 0) > 0
        ? [
            {
              key: 'restore',
              label: `Restore all (${deletedCount})`,
              onSelect: onRestoreAllDeleted,
            },
            { key: 'empty', label: 'Empty bin', danger: true, onSelect: onEmptyDeleted },
          ]
        : []
      : subView === 'quarantine'
        ? quarantineCount > 0
          ? [
              {
                key: 'approve',
                label: `Approve all (${quarantineCount})`,
                onSelect: onQuarantineApproveAll,
              },
              { key: 'clear', label: 'Delete all', danger: true, onSelect: onQuarantineClearAll },
            ]
          : []
        : unverifiedCount > 0
          ? [
              { key: 'approve', label: `Approve all (${unverifiedCount})`, onSelect: onApproveAll },
              {
                key: 'orphans',
                label: 'Clean orphaned entries',
                title: 'Remove entries whose file no longer exists. Log rows only, never a file.',
                onSelect: onCleanOrphans,
              },
              { key: 'delete', label: 'Delete all', danger: true, onSelect: onDeleteAll },
            ]
          : [];

  return (
    <div className="dlc-subbar" id="verif-subview-banner">
      <DlSegmented
        label="Review queue"
        value={subView}
        onChange={(v) => onSubView(v as AdlSubView)}
        segments={[
          // hidden entirely when no unverified queue can exist
          ...(acoustidEnabled
            ? [
                {
                  value: 'unverified',
                  label: 'Unverified',
                  count: unverifiedCount,
                  title: "Imported files AcoustID couldn't confirm",
                },
              ]
            : []),
          {
            value: 'quarantine',
            label: 'Quarantine',
            // only once it's known, rather than showing 0
            count: quarantineLoaded ? quarantineCount : null,
            title: 'Files that failed their checks and were never imported',
          },
          {
            value: 'deleted',
            label: 'Deleted',
            count: deletedCount,
            title:
              'Files removed by repair tools and the duplicate cleaner, restorable until purged',
          },
        ]}
      />
      <div className="dlc-subbar-side">
        {/* the count is files, the list is tracks. say both so "7" over two
            rows isn't a puzzle */}
        {subView === 'quarantine' && quarantineTrackCount != null && quarantineCount > 0 ? (
          <span className="dlc-subbar-note">
            {quarantineSummary(quarantineTrackCount, quarantineCount)}
          </span>
        ) : null}
        <span className="dlh-more">
          <DlMore items={menu} label="Review actions" />
        </span>
      </div>
    </div>
  );
}

/** Checked unverified rows: their actions float at the bottom of the list. */
export function AdlReviewSelectionBar({
  count,
  onApprove,
  onDelete,
  onClear,
}: {
  count: number;
  onApprove: () => void;
  onDelete: () => void;
  onClear: () => void;
}) {
  return (
    <DlSelectionBar count={count} onClear={onClear}>
      <DlButton primary onClick={onApprove}>
        Approve {count}
      </DlButton>
      <DlButton danger onClick={onDelete}>
        Delete {count}
      </DlButton>
    </DlSelectionBar>
  );
}

const EXPLAINERS: Record<AdlSubView, [string, string]> = {
  unverified: [
    "Imported, but AcoustID couldn't confirm them.",
    'Have a listen. Approve keeps the file and stops future scans asking. Delete removes the file.',
  ],
  quarantine: [
    'These failed their checks and were never imported.',
    'Approve imports one as if it passed. Nothing is renamed. Delete removes the file for good.',
  ],
  deleted: [
    'Removed by repair tools and the duplicate cleaner, hidden from your media server.',
    'Restore puts a file back exactly where it was.',
  ],
};

/** One plain line saying what the current sub-view is. Always there, never a box to dismiss. */
export function AdlReviewExplainer({ subView }: { subView: AdlSubView }) {
  const [lead, rest] = EXPLAINERS[subView];
  return (
    <p className="dlc-explain" data-subview={subView}>
      <b>{lead}</b> {rest}
    </p>
  );
}

// ── Deleted (the music recycle bin) ───────────────────────────────────────

export interface DeletedRowHandlers {
  onRestore: () => void;
  onPurge: () => void;
}

function deletedSource(source: string | null): string | null {
  switch (source) {
    case 'repair':
      return 'Repair tool';
    case 'duplicate-cleaner':
      return 'Duplicate cleaner';
    // an album download that stalled and never reached the library. the
    // startup sweep used to delete these outright (#1210)
    case 'album_bundle_orphan':
      return 'Stalled album download';
    // corrupt file detector: kept until the re-download replaces it
    case 'corrupt_audio':
      return 'Corrupt file (re-downloading)';
    default:
      return null;
  }
}

export function AdlDeletedRow({
  entry,
  handlers,
}: {
  entry: AdlDeletedEntry;
  handlers: DeletedRowHandlers;
}) {
  const [busy, setBusy] = useState(false);
  const source = deletedSource(entry.source);
  const ms = entry.deleted_at ? Date.parse(entry.deleted_at) : NaN;
  const ago = Number.isFinite(ms) ? relTime(ms, Date.now()) : 'age unknown';
  return (
    <div className="dlc-row dlc-row-completed" data-deleted-id={entry.id}>
      <DlFileBadge ext={fileExt(entry.name)} />
      <div className="dlc-main">
        <div className="dlc-title" title={entry.rel}>
          {entry.name}
        </div>
        <div className="dlc-sub dlc-mono" title={`will be restored to ${entry.original_path}`}>
          {entry.original_path}
        </div>
      </div>
      <div className="dlc-meta">
        {source ? <span className="dlc-tag">{source}</span> : null}
        <span title="File size">{formatBytes(entry.size) || '—'}</span>
        <span className="dlc-when" title={entry.deleted_at ?? 'deleted before tracking existed'}>
          {ago}
        </span>
      </div>
      <div className="dlc-acts">
        <DlButton
          primary
          title="Put this file back where it was removed from"
          disabled={busy}
          onClick={() => {
            setBusy(true);
            handlers.onRestore();
          }}
        >
          Restore
        </DlButton>
        <DlMore
          items={[
            {
              key: 'purge',
              label: 'Delete forever',
              danger: true,
              disabled: busy,
              onSelect: () => {
                setBusy(true);
                handlers.onPurge();
              },
            },
          ]}
        />
      </div>
    </div>
  );
}

const RETENTION_CHOICES: { value: number; label: string }[] = [
  { value: 0, label: 'Keep forever' },
  { value: 7, label: 'Keep for 7 days' },
  { value: 14, label: 'Keep for 14 days' },
  { value: 30, label: 'Keep for 30 days' },
  { value: 90, label: 'Keep for 90 days' },
];

export function RetentionSelect({
  keepDays,
  onKeepDays,
}: {
  keepDays: number;
  onKeepDays: (days: number) => void;
}) {
  return (
    <select
      className="dlc-select"
      aria-label="How long deleted files are kept"
      title="Files older than this are purged automatically. Files deleted before this feature shipped carry no age and are never auto-purged."
      value={RETENTION_CHOICES.some((c) => c.value === keepDays) ? keepDays : 0}
      onChange={(event) => onKeepDays(Number(event.target.value))}
    >
      {RETENTION_CHOICES.map((c) => (
        <option key={c.value} value={c.value}>
          {c.label}
        </option>
      ))}
    </select>
  );
}

/** A bin's summary: how much is in it, what it is, how long it's kept. */
export function BinStrip({
  size,
  count,
  line,
  control,
  warn,
}: {
  size: string;
  count: number;
  line: string;
  control?: React.ReactNode;
  warn?: string;
}) {
  return (
    <div className="dlc-strip">
      <div className="dlc-strip-big">
        <strong>{size}</strong>
        <span>
          {count} file{count === 1 ? '' : 's'}
        </span>
      </div>
      <div className="dlc-strip-line">
        {line}
        {warn ? <div className="dlc-strip-warn">{warn}</div> : null}
      </div>
      {control ?? <span />}
    </div>
  );
}

export function AdlDeletedList({
  entries,
  totalSize,
  loaded,
  keepDays,
  handlersFor,
  onKeepDays,
}: {
  entries: AdlDeletedEntry[];
  totalSize: number;
  loaded: boolean;
  keepDays: number;
  handlersFor: (entry: AdlDeletedEntry) => DeletedRowHandlers;
  onKeepDays: (days: number) => void;
}) {
  if (!loaded) {
    return <div className="dlc-loading">Loading deleted files…</div>;
  }
  return (
    <>
      <BinStrip
        size={formatBytes(totalSize) || '0 B'}
        count={entries.length}
        line="Removed by the duplicate cleaner and repair tools, hidden from your media server. Restore puts a file back exactly where it was."
        control={<RetentionSelect keepDays={keepDays} onKeepDays={onKeepDays} />}
      />
      {entries.length === 0 ? (
        <div className="dlc-empty-line" id="adl-empty">
          The bin is empty.
        </div>
      ) : (
        <div className="dlc-list">
          {entries.map((entry) => (
            <AdlDeletedRow key={entry.id} entry={entry} handlers={handlersFor(entry)} />
          ))}
        </div>
      )}
    </>
  );
}
