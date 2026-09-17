import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate } from '@tanstack/react-router';
import clsx from 'clsx';
import { useEffect, useState } from 'react';

import { Button, Checkbox, Switch } from '@/components/form/form';
import { Notice } from '@/components/primitives';
import { browserSafeImageUrl } from '@/platform/artwork-thumb';

import type {
  ImportInboxFilter,
  ImportInboxItem,
  ImportInboxPayload,
  ImportStagingFile,
} from '../-import.types';

import {
  approveAutoImportResult,
  autoImportSettingsQueryOptions,
  clearCompletedAutoImportResults,
  importInboxQueryOptions,
  invalidateAutoImportQueries,
  invalidateImportStagingQueries,
  rejectAutoImportResult,
  retryAutoImportResult,
  toggleAutoImport,
  triggerAutoImportScan,
} from '../-import.api';
import { formatImportBytes } from '../-import.helpers';
import {
  confidencePercent,
  confidenceTone,
  countInbox,
  describeFile,
  describeItemFiles,
  describeItemMatch,
  filterInboxItems,
  INBOX_STATUS_META,
  inboxActions,
  itemSubtitle,
  methodLabel,
  secondsToNextScan,
  timeAgo,
} from '../-import.inbox';
import styles from './import-page.module.css';
import {
  confirmAction,
  DiscIcon,
  fallbackImage,
  FolderIcon,
  getErrorMessage,
  NoteIcon,
  RefreshIcon,
  useImportQueueActions,
} from './import-shared';
import { UploadZone } from './upload-zone';

/** live states poll fast; a quiet inbox with the worker off can wait */
function pollInterval(payload: ImportInboxPayload | undefined): number | false {
  if (!payload) return false;
  if (payload.scanning) return 1500;
  const busy = payload.items?.some((item) =>
    ['identifying', 'importing', 'queued'].includes(item.status),
  );
  if (busy || payload.worker?.current_status !== 'idle') return 3000;
  return payload.worker?.running ? 8000 : 20000;
}

export function Inbox({
  filter,
  onFilterChange,
}: {
  filter: ImportInboxFilter;
  onFilterChange: (next: ImportInboxFilter) => void;
}) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const { addQueueJob } = useImportQueueActions();
  const inbox = useQuery({
    ...importInboxQueryOptions(),
    refetchInterval: (query) => pollInterval(query.state.data),
  });
  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  const [focused, setFocused] = useState<string | null>(null);

  const payload = inbox.data;
  const items = payload?.items ?? [];
  const workerRunning = Boolean(payload?.worker?.running);
  const visible = filterInboxItems(items, filter, workerRunning);
  const counts = countInbox(items, workerRunning);

  // a selection that outlives its rows (the worker imported them) is dropped
  useEffect(() => {
    setSelected((current) => {
      const keep = new Set([...current].filter((key) => visible.some((item) => item.key === key)));
      return keep.size === current.size ? current : keep;
    });
  }, [visible]);

  const refreshAll = () => {
    void invalidateImportStagingQueries(queryClient);
    void invalidateAutoImportQueries(queryClient);
  };
  const onError = (error: unknown) => window.showToast?.(getErrorMessage(error), 'error');

  const approve = useMutation({
    mutationFn: async (ids: number[]) => {
      for (const id of ids) await approveAutoImportResult(id);
      return ids.length;
    },
    onSuccess: (n) => {
      window.showToast?.(n === 1 ? 'Approved, importing' : `Approved ${n}, importing`, 'success');
      setSelected(new Set());
      refreshAll();
    },
    onError,
  });
  const dismiss = useMutation({
    mutationFn: async (ids: number[]) => {
      for (const id of ids) await rejectAutoImportResult(id);
      return ids.length;
    },
    onSuccess: (n) => {
      window.showToast?.(n === 1 ? 'Dismissed' : `Dismissed ${n}`, 'success');
      setSelected(new Set());
      refreshAll();
    },
    onError,
  });
  const clearHistory = useMutation({
    mutationFn: async () => {
      const ok = await confirmAction({
        title: 'Clear history',
        message:
          'Forget every imported, failed and dismissed record? Items still in the import folder are kept.',
        confirmText: 'Clear',
      });
      return ok ? await clearCompletedAutoImportResults() : null;
    },
    onSuccess: (n) => {
      if (n === null) return;
      window.showToast?.(`Cleared ${n} ${n === 1 ? 'record' : 'records'}`, 'success');
      refreshAll();
    },
    onError,
  });
  const retry = useMutation({
    mutationFn: retryAutoImportResult,
    onSuccess: () => {
      window.showToast?.('Will be looked at again on the next scan', 'success');
      refreshAll();
    },
    onError,
  });

  const selectedItems = visible.filter((item) => selected.has(item.key));
  // loose files with no worker verdict can go in straight from their own tags,
  // which is what the old singles tab did with one button
  const selectedSingles = selectedItems.filter(
    (item) => item.kind === 'single' && item.status === 'waiting' && item.files.length === 1,
  );
  const importSinglesFromTags = () => {
    if (selectedSingles.length === 0) return;
    const files: ImportStagingFile[] = selectedSingles.map((item) => {
      const f = item.files[0];
      return {
        filename: f.filename,
        rel_path: f.rel_path,
        full_path: f.full_path,
        title: f.title,
        artist: f.artist,
        album: f.album,
        track_number: f.track_number,
        disc_number: f.disc_number,
        extension: f.extension,
        size: f.size,
        duration_ms: f.duration_ms,
        bitrate: f.bitrate,
      };
    });
    addQueueJob({
      type: 'singles',
      label: `${files.length} ${files.length === 1 ? 'single' : 'singles'} from tags`,
      sublabel:
        files
          .map((f) => f.title || f.filename)
          .slice(0, 3)
          .join(', ') + (files.length > 3 ? '…' : ''),
      imageUrl: null,
      items: files,
    });
    setSelected(new Set());
  };

  // j/k move, enter opens the matcher, a approves, d dismisses, x ticks.
  // only while nothing else has the keyboard.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)) return;
      if (target?.isContentEditable || event.metaKey || event.ctrlKey || event.altKey) return;
      if (visible.length === 0) return;
      const index = focused ? visible.findIndex((item) => item.key === focused) : -1;
      const current = index >= 0 ? visible[index] : null;
      switch (event.key) {
        case 'j':
        case 'ArrowDown':
          event.preventDefault();
          setFocused(visible[Math.min(visible.length - 1, index + 1)].key);
          break;
        case 'k':
        case 'ArrowUp':
          event.preventDefault();
          setFocused(visible[Math.max(0, index - 1)].key);
          break;
        case 'Enter':
          if (current && inboxActions(current).includes('identify')) {
            event.preventDefault();
            void navigate({ to: '/import/match/$key', params: { key: current.key } });
          }
          break;
        case 'a':
          if (current && inboxActions(current).includes('approve') && current.history_id != null) {
            approve.mutate([current.history_id]);
          }
          break;
        case 'd':
          if (current && inboxActions(current).includes('dismiss') && current.history_id != null) {
            void (async () => {
              const ok = await confirmAction({
                title: 'Dismiss',
                message: `Dismiss "${current.name}"? Its files stay in the import folder.`,
                confirmText: 'Dismiss',
              });
              if (ok) dismiss.mutate([current.history_id!]);
            })();
          }
          break;
        case 'x':
          if (current && inboxActions(current).length > 0) {
            setSelected((prev) => {
              const next = new Set(prev);
              if (next.has(current.key)) next.delete(current.key);
              else next.add(current.key);
              return next;
            });
          }
          break;
        default:
          return;
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [visible, focused, navigate, approve, dismiss]);
  useEffect(() => {
    if (!focused) return;
    const el = document.querySelector(`[data-inbox-key="${CSS.escape(focused)}"]`);
    // jsdom has no scrollIntoView; a missing method must not take the page down
    if (el && typeof el.scrollIntoView === 'function') el.scrollIntoView({ block: 'nearest' });
  }, [focused]);
  const selectedApprovable = selectedItems.filter(
    (item) => item.status === 'needs_review' && item.history_id != null,
  );
  const selectedDismissable = selectedItems.filter(
    (item) => inboxActions(item).includes('dismiss') && item.history_id != null,
  );
  const selectable = visible.filter((item) => inboxActions(item).length > 0);

  return (
    <>
      <StatusStrip payload={payload} error={inbox.error} loading={inbox.isLoading} />

      {payload?.problems?.length ? (
        <Notice tone="warning" role="alert">
          {payload.problems.length === 1 ? 'A folder' : `${payload.problems.length} folders`} in the
          import folder could not be read, so files inside will not appear:{' '}
          {payload.problems
            .slice(0, 3)
            .map((problem) => `${problem.path} (${problem.error})`)
            .join('; ')}
          {payload.problems.length > 3 ? '; …' : ''}. If this is a bind mount, check the folder's
          owner against the container's PUID/PGID.
        </Notice>
      ) : null}

      <div className={styles.toolbar}>
        <div className={styles.pills} role="tablist" aria-label="Show">
          <FilterPill
            active={filter === 'attention'}
            count={counts.attention}
            warn={counts.attention > 0}
            onClick={() => onFilterChange('attention')}
          >
            Needs attention
          </FilterPill>
          <FilterPill
            active={filter === 'all'}
            count={counts.all}
            onClick={() => onFilterChange('all')}
          >
            Everything
          </FilterPill>
          <FilterPill
            active={filter === 'history'}
            count={counts.history}
            onClick={() => onFilterChange('history')}
          >
            History
          </FilterPill>
        </div>
        <div className={styles.toolbarSpacer} />
        {filter === 'history' && counts.history > 0 ? (
          <Button
            variant="ghost"
            size="sm"
            id="auto-import-clear-completed"
            disabled={clearHistory.isPending}
            onClick={() => clearHistory.mutate()}
          >
            Clear history
          </Button>
        ) : null}
        {selectable.length > 1 ? (
          <div className={styles.toolbarActions}>
            {selected.size > 0 ? (
              <span className={styles.selectionNote}>{selected.size} selected</span>
            ) : null}
            <Button
              variant="ghost"
              size="sm"
              onClick={() =>
                setSelected(
                  selected.size === selectable.length
                    ? new Set()
                    : new Set(selectable.map((item) => item.key)),
                )
              }
            >
              {selected.size === selectable.length ? 'Select none' : 'Select all'}
            </Button>
            {selectedApprovable.length > 0 ? (
              <Button
                variant="primary"
                size="sm"
                disabled={approve.isPending}
                onClick={() => approve.mutate(selectedApprovable.map((item) => item.history_id!))}
              >
                Approve {selectedApprovable.length}
              </Button>
            ) : null}
            {selectedSingles.length > 0 ? (
              <Button
                variant={selectedApprovable.length > 0 ? 'secondary' : 'primary'}
                size="sm"
                title="Import these files as they are, tagged from what they carry"
                onClick={importSinglesFromTags}
              >
                Import {selectedSingles.length} from tags
              </Button>
            ) : null}
            {selectedDismissable.length > 0 ? (
              <Button
                variant="secondary"
                size="sm"
                disabled={dismiss.isPending}
                onClick={async () => {
                  const ok = await confirmAction({
                    title: 'Dismiss',
                    message: `Dismiss ${selectedDismissable.length} items? Their files stay in the import folder.`,
                    confirmText: 'Dismiss',
                  });
                  if (ok) dismiss.mutate(selectedDismissable.map((item) => item.history_id!));
                }}
              >
                Dismiss {selectedDismissable.length}
              </Button>
            ) : null}
          </div>
        ) : null}
      </div>

      <UploadZone>
        {inbox.error && !payload ? (
          <Notice tone="danger" role="alert">
            {getErrorMessage(inbox.error)}
          </Notice>
        ) : payload?.scanning ? (
          <div className={styles.empty}>
            <div className={styles.emptyTitle}>Reading the import folder…</div>
            {payload.progress && payload.progress.total > 0
              ? `${payload.progress.scanned} of ${payload.progress.total} files`
              : null}
          </div>
        ) : !inbox.isLoading && visible.length === 0 ? (
          <EmptyState filter={filter} total={counts.all} stagingPath={payload?.staging_path} />
        ) : (
          <div className={styles.list} id="import-inbox-list">
            {visible.map((item) => (
              <InboxRow
                key={item.key}
                item={item}
                focused={focused === item.key}
                selectable={inboxActions(item).length > 0}
                selected={selected.has(item.key)}
                busy={approve.isPending || dismiss.isPending || retry.isPending}
                onSelectedChange={(next) =>
                  setSelected((current) => {
                    const copy = new Set(current);
                    if (next) copy.add(item.key);
                    else copy.delete(item.key);
                    return copy;
                  })
                }
                onApprove={() => item.history_id != null && approve.mutate([item.history_id])}
                onDismiss={async () => {
                  if (item.history_id == null) return;
                  const ok = await confirmAction({
                    title: 'Dismiss',
                    message: `Dismiss "${item.name}"? Its files stay in the import folder.`,
                    confirmText: 'Dismiss',
                  });
                  if (ok) dismiss.mutate([item.history_id]);
                }}
                onRetry={() => item.history_id != null && retry.mutate(item.history_id)}
              />
            ))}
          </div>
        )}
        {visible.length > 1 ? (
          <div className={styles.kbdHint}>
            <kbd>j</kbd> <kbd>k</kbd> move · <kbd>x</kbd> tick · <kbd>a</kbd> approve · <kbd>d</kbd>{' '}
            dismiss · <kbd>↵</kbd> open
          </div>
        ) : null}
      </UploadZone>
    </>
  );
}

function FilterPill({
  active,
  children,
  count,
  warn,
  onClick,
}: {
  active: boolean;
  children: string;
  count: number;
  warn?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      className={clsx(styles.pill, { [styles.active]: active })}
      onClick={onClick}
    >
      {children}
      <span className={clsx(styles.pillCount, { [styles.warn]: warn && !active })}>{count}</span>
    </button>
  );
}

/**
 * The strip under the header: where the folder is, what is in it, and the
 * worker's switch with its countdown. The switch is the same toggle the old
 * Auto tab had; the countdown is new, because "is it going to look at this
 * or not" was the question every waiting row raised.
 */
function StatusStrip({
  payload,
  error,
  loading,
}: {
  payload: ImportInboxPayload | undefined;
  error: unknown;
  loading: boolean;
}) {
  const queryClient = useQueryClient();
  const settings = useQuery(autoImportSettingsQueryOptions());
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const worker = payload?.worker;
  const toggle = useMutation({
    mutationFn: toggleAutoImport,
    onSuccess: (_, enabled) => {
      window.showToast?.(enabled ? 'Auto-import on' : 'Auto-import off', 'success');
      void invalidateAutoImportQueries(queryClient);
    },
    onError: (err) => window.showToast?.(getErrorMessage(err), 'error'),
  });
  const scan = useMutation({
    mutationFn: triggerAutoImportScan,
    onSuccess: () => {
      window.showToast?.('Scanning', 'success');
      void invalidateAutoImportQueries(queryClient);
    },
    onError: (err) => window.showToast?.(getErrorMessage(err), 'error'),
  });

  const live = worker && worker.current_status !== 'idle';
  const nextIn = worker?.running
    ? secondsToNextScan(worker.last_scan_time, settings.data?.scan_interval ?? 60, now)
    : null;
  const summary = payload?.summary;

  return (
    <div className={styles.strip} id="import-staging-bar">
      <span
        className={styles.stripPath}
        id="import-page-staging-path"
        title={payload?.staging_path}
      >
        <FolderIcon />
        {error && !payload
          ? `Import folder: ${getErrorMessage(error)}`
          : loading
            ? 'Reading import folder…'
            : payload?.staging_path || 'Import folder not configured'}
      </span>
      {summary ? (
        <span className={styles.stripStat} id="import-page-staging-stats">
          <strong>{summary.items}</strong> {summary.items === 1 ? 'item' : 'items'} ·{' '}
          <strong>{summary.files}</strong> {summary.files === 1 ? 'file' : 'files'}
          {summary.size ? (
            <>
              {' '}
              · <strong>{formatImportBytes(summary.size)}</strong>
            </>
          ) : null}
        </span>
      ) : null}
      {worker?.available ? (
        <span className={styles.stripWorker}>
          <span className={styles.stripWorkerLabel}>
            <Switch
              checked={worker.running}
              disabled={toggle.isPending}
              aria-label="Auto-import"
              id="auto-import-enabled"
              onCheckedChange={(checked) => toggle.mutate(checked)}
            />
            Auto-import
            {live ? <span className={styles.liveDot} aria-hidden="true" /> : null}
          </span>
          {worker.running ? (
            <span className={styles.stripCountdown} id="auto-import-status-text">
              {live
                ? worker.current_status === 'processing'
                  ? 'importing'
                  : 'scanning'
                : nextIn == null
                  ? 'waiting for first scan'
                  : nextIn === 0
                    ? 'scanning soon'
                    : `next scan in ${nextIn}s`}
            </span>
          ) : (
            <span className={styles.stripCountdown} id="auto-import-status-text">
              off
            </span>
          )}
          {worker.running ? (
            <button
              type="button"
              className={styles.iconButton}
              id="auto-import-scan-now"
              title="Scan now"
              aria-label="Scan now"
              aria-busy={scan.isPending || Boolean(live)}
              disabled={scan.isPending}
              onClick={() => scan.mutate()}
            >
              <RefreshIcon />
            </button>
          ) : null}
        </span>
      ) : null}
    </div>
  );
}

function EmptyState({
  filter,
  total,
  stagingPath,
}: {
  filter: ImportInboxFilter;
  total: number;
  stagingPath: string | undefined;
}) {
  if (total === 0) {
    return (
      <div className={styles.empty} id="import-inbox-empty">
        <div className={styles.emptyTitle}>Nothing to import</div>
        <div>Drop album folders or single tracks into the import folder and they show up here.</div>
        {stagingPath ? <div className={styles.emptyPath}>{stagingPath}</div> : null}
      </div>
    );
  }
  return (
    <div className={styles.empty} id="import-inbox-empty">
      <div className={styles.emptyTitle}>
        {filter === 'attention'
          ? 'Nothing needs you'
          : filter === 'history'
            ? 'No history yet'
            : 'Nothing here'}
      </div>
      {filter === 'attention' ? (
        <div>Everything in the import folder is being handled. Check Everything to watch it.</div>
      ) : null}
    </div>
  );
}

function InboxRow({
  item,
  focused,
  selectable,
  selected,
  busy,
  onSelectedChange,
  onApprove,
  onDismiss,
  onRetry,
}: {
  item: ImportInboxItem;
  focused: boolean;
  selectable: boolean;
  selected: boolean;
  busy: boolean;
  onSelectedChange: (next: boolean) => void;
  onApprove: () => void;
  onDismiss: () => void;
  onRetry: () => void;
}) {
  const navigate = useNavigate();
  const [expanded, setExpanded] = useState(false);
  const meta = INBOX_STATUS_META[item.status];
  const actions = inboxActions(item);
  const percent = confidencePercent(item);
  const matchLine = describeItemMatch(item);
  const when = timeAgo(item.processed_at || item.created_at);
  const method = methodLabel(item.identification_method);
  const isLive = item.status === 'importing' || item.status === 'identifying';
  const liveFraction =
    item.live && item.live.track_total > 0
      ? (item.live.track_index / item.live.track_total) * 100
      : null;
  const openMatcher = () => void navigate({ to: '/import/match/$key', params: { key: item.key } });

  return (
    <article
      className={clsx(styles.row, {
        [styles.selected]: selected,
        [styles.history]: !item.in_staging,
        [styles.focused]: focused,
      })}
      data-status={item.status}
      data-inbox-key={item.key}
      data-testid="import-inbox-row"
    >
      <div className={clsx(styles.rowCheck, { [styles.empty]: !selectable })}>
        <Checkbox
          checked={selected}
          disabled={!selectable}
          aria-label={`Select ${item.name}`}
          onCheckedChange={(next) => onSelectedChange(Boolean(next))}
        />
      </div>

      {item.image_url ? (
        <img
          className={styles.rowArt}
          src={browserSafeImageUrl(item.image_url)}
          alt=""
          loading="lazy"
          onError={fallbackImage}
        />
      ) : (
        <div className={styles.rowArtEmpty} aria-hidden="true">
          {item.kind === 'single' ? <NoteIcon /> : <DiscIcon />}
        </div>
      )}

      <div className={styles.rowBody}>
        <div className={styles.rowTitleLine}>
          <span className={styles.rowTitle} title={item.name}>
            {item.name || item.folder_name}
          </span>
        </div>
        {item.artist ? (
          <span className={styles.rowArtist} title={item.artist}>
            {item.artist}
          </span>
        ) : null}
        <div className={styles.rowMeta}>
          <span>{describeItemFiles(item)}</span>
          {item.in_staging ? <code title={item.folder_path}>{itemSubtitle(item)}</code> : null}
          {matchLine ? <span>{matchLine}</span> : null}
          {method ? <span>identified {method}</span> : null}
          {when ? <span>{when}</span> : null}
          {item.in_staging && item.files.length > 0 ? (
            <button
              type="button"
              className={styles.expandButton}
              aria-expanded={expanded}
              onClick={() => setExpanded((open) => !open)}
            >
              {expanded ? 'Hide files' : `Show ${item.files.length === 1 ? 'file' : 'files'}`}
            </button>
          ) : null}
        </div>
        {item.error_message ? (
          <div className={styles.rowError} title={item.error_message}>
            {item.error_message}
          </div>
        ) : null}
      </div>

      <div className={styles.rowSide}>
        <div className={styles.rowSideTop}>
          {percent != null && !isLive ? (
            <span className={styles.confidence} title="How sure the match is">
              <span className={styles.confidenceBar}>
                <span
                  className={styles.confidenceFill}
                  data-tone={confidenceTone(percent)}
                  style={{ width: `${percent}%`, display: 'block' }}
                />
              </span>
              {percent}%
            </span>
          ) : null}
          <span className={styles.status} data-tone={meta.tone} title={meta.hint}>
            {meta.label}
          </span>
        </div>
        {isLive ? (
          <div className={styles.progress} aria-hidden="true">
            <div
              className={clsx(styles.progressFill, {
                [styles.indeterminate]: liveFraction == null,
              })}
              style={liveFraction == null ? undefined : { width: `${liveFraction}%` }}
            />
          </div>
        ) : null}
        {actions.length > 0 ? (
          <div className={styles.rowActions}>
            {actions.includes('approve') ? (
              <Button variant="primary" size="sm" disabled={busy} onClick={onApprove}>
                Approve
              </Button>
            ) : null}
            {actions.includes('retry') ? (
              <Button variant="secondary" size="sm" disabled={busy} onClick={onRetry}>
                Retry
              </Button>
            ) : null}
            {actions.includes('identify') ? (
              <Button
                variant={actions.includes('approve') ? 'secondary' : 'primary'}
                size="sm"
                onClick={openMatcher}
              >
                {item.status === 'needs_review' || item.status === 'failed'
                  ? 'Fix match'
                  : 'Identify'}
              </Button>
            ) : null}
            {actions.includes('dismiss') ? (
              <Button variant="ghost" size="sm" disabled={busy} onClick={onDismiss}>
                Dismiss
              </Button>
            ) : null}
          </div>
        ) : null}
      </div>

      {expanded && item.files.length > 0 ? (
        <div className={styles.rowDetail}>
          <div className={styles.fileTable}>
            {item.files.map((file, index) => (
              <FileLine key={file.full_path} index={index} file={file} />
            ))}
          </div>
          {item.in_staging ? (
            <div style={{ marginTop: 8 }}>
              <Link
                to="/import/match/$key"
                params={{ key: item.key }}
                className={styles.matcherBack}
              >
                Open in the matcher →
              </Link>
            </div>
          ) : null}
        </div>
      ) : null}
    </article>
  );
}

function FileLine({ file, index }: { file: ImportInboxItem['files'][number]; index: number }) {
  const num = file.track_number ? String(file.track_number) : String(index + 1);
  return (
    <>
      <span className={styles.fileNum}>{num}</span>
      <span className={styles.fileName} title={file.full_path}>
        {file.title || file.filename}
        {file.title && file.title !== file.filename ? <small>{file.filename}</small> : null}
      </span>
      <span className={styles.fileFacts}>{describeFile(file)}</span>
    </>
  );
}
