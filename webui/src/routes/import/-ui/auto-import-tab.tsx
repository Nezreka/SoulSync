import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import clsx from 'clsx';
import { useEffect, useState } from 'react';

import {
  Button,
  OptionButton,
  OptionButtonGroup,
  RangeInput,
  Select,
  Switch,
} from '@/components/form/form';
import { Badge } from '@/components/primitives';

import type {
  ImportAutoFilter,
  ImportAutoImportResult,
  ImportAutoImportStatusPayload,
} from '../-import.types';

import {
  approveAllAutoImportResults,
  approveAutoImportResult,
  autoImportResultsQueryOptions,
  autoImportSettingsQueryOptions,
  autoImportStatusQueryOptions,
  clearCompletedAutoImportResults,
  invalidateAutoImportQueries,
  rejectAutoImportResult,
  saveAutoImportSettings,
  toggleAutoImport,
  triggerAutoImportScan,
} from '../-import.api';
import {
  filterAutoImportResults,
  getActiveImportLines,
  getAutoImportCounts,
  getAutoImportStatusMeta,
  getAutoImportStatusText,
  getAutoImportStatusTone,
  getAutoImportTimeAgo,
  getConfidenceClass,
  parseAutoImportMatchData,
} from '../-import.helpers';
import styles from './import-page.module.css';
import { fallbackImage, getErrorMessage, RefreshIcon } from './import-shared';

export function AutoImportPanel({
  autoFilter,
  onFilterChange,
}: {
  autoFilter: ImportAutoFilter;
  onFilterChange: (filter: ImportAutoFilter) => void;
}) {
  const queryClient = useQueryClient();
  const [confidence, setConfidence] = useState(90);
  const [interval, setInterval] = useState(60);
  const [expandedRows, setExpandedRows] = useState<Set<number>>(() => new Set());

  const statusQuery = useQuery({
    ...autoImportStatusQueryOptions(),
    refetchInterval: 5000,
  });
  const settingsQuery = useQuery({
    ...autoImportSettingsQueryOptions(),
  });
  const resultsQuery = useQuery({
    ...autoImportResultsQueryOptions(),
    refetchInterval: 5000,
  });

  useEffect(() => {
    const settings = settingsQuery.data;
    if (!settings) return;
    setConfidence(Math.round((settings.confidence_threshold ?? 0.9) * 100));
    setInterval(settings.scan_interval ?? 60);
  }, [settingsQuery.data]);

  const invalidateAutoImport = () => {
    void invalidateAutoImportQueries(queryClient);
  };

  const toggleMutation = useMutation({
    mutationFn: toggleAutoImport,
    onSuccess: (_, enabled) => {
      window.showToast?.(enabled ? 'Auto-import enabled' : 'Auto-import disabled', 'success');
      invalidateAutoImport();
    },
    onError: showMutationError,
  });
  const saveSettingsMutation = useMutation({
    mutationFn: saveAutoImportSettings,
    onSuccess: () => {
      window.showToast?.('Settings saved', 'success');
      invalidateAutoImport();
    },
    onError: showMutationError,
  });
  const scanMutation = useMutation({
    mutationFn: triggerAutoImportScan,
    onSuccess: () => {
      window.showToast?.('Scan triggered', 'success');
      invalidateAutoImport();
    },
    onError: showMutationError,
  });
  const approveMutation = useMutation({
    mutationFn: approveAutoImportResult,
    onSuccess: () => {
      window.showToast?.('Approved', 'success');
      invalidateAutoImport();
    },
    onError: showMutationError,
  });
  const rejectMutation = useMutation({
    mutationFn: rejectAutoImportResult,
    onSuccess: () => {
      window.showToast?.('Dismissed', 'success');
      invalidateAutoImport();
    },
    onError: showMutationError,
  });
  const approveAllMutation = useMutation({
    mutationFn: async () => {
      const confirmed = await confirmAction({
        title: 'Approve All',
        message: 'Approve and import all pending review items?',
        confirmText: 'Approve All',
      });
      if (!confirmed) return null;
      return await approveAllAutoImportResults();
    },
    onSuccess: (count) => {
      if (count === null) return;
      window.showToast?.(`Approved ${count} items`, 'success');
      invalidateAutoImport();
    },
    onError: showMutationError,
  });
  const clearMutation = useMutation({
    mutationFn: clearCompletedAutoImportResults,
    onSuccess: (count) => {
      window.showToast?.(`Cleared ${count} imported items`, 'success');
      invalidateAutoImport();
    },
    onError: showMutationError,
  });

  const allResults = resultsQuery.data?.results ?? [];
  const results = filterAutoImportResults(allResults, autoFilter);
  const counts = getAutoImportCounts(allResults);
  const activeLines = getActiveImportLines(statusQuery.data);
  const statusTone = getAutoImportStatusTone(statusQuery.data);

  if (statusQuery.error) {
    return (
      <div className={styles.autoImportEmpty}>
        Auto-import is unavailable: {getErrorMessage(statusQuery.error)}
      </div>
    );
  }

  return (
    <>
      <div className={styles.autoImportControls}>
        <div className={styles.autoImportToggleRow}>
          <div className={styles.autoImportToggleLabel}>
            <Switch
              checked={Boolean(statusQuery.data?.running)}
              disabled={toggleMutation.isPending}
              aria-labelledby="auto-import-toggle-label"
              id="auto-import-enabled"
              onCheckedChange={(checked) => toggleMutation.mutate(checked)}
            />
            <span id="auto-import-toggle-label">Auto-Import</span>
          </div>
          <Badge id="auto-import-status-text" tone={statusTone}>
            {getAutoImportStatusText(statusQuery.data)}
          </Badge>
          <div className={styles.importPageFlexSpacer} />
          {statusQuery.data?.running ? (
            <Button
              variant="secondary"
              id="auto-import-scan-now"
              title="Scan import folder now"
              disabled={scanMutation.isPending}
              onClick={() => scanMutation.mutate()}
            >
              <RefreshIcon />
              Scan Now
            </Button>
          ) : null}
        </div>
        {statusQuery.data?.running ? (
          <div className={styles.autoImportSettingsRow} id="auto-import-settings-row">
            <div className={styles.autoImportSetting}>
              <span>Confidence:</span>
              <RangeInput
                label="Confidence"
                min={50}
                max={100}
                value={confidence}
                onValueChange={setConfidence}
              />
              <span className={styles.autoImportConfidenceValue} id="auto-import-conf-val">
                {confidence}%
              </span>
            </div>
            <div className={styles.autoImportSetting}>
              <span>Interval:</span>
              <Select
                id="auto-import-interval"
                size="sm"
                value={interval}
                onChange={(event) => setInterval(Number(event.target.value))}
              >
                <option value="30">30s</option>
                <option value="60">60s</option>
                <option value="120">2m</option>
                <option value="300">5m</option>
              </Select>
            </div>
            <Button
              variant="primary"
              size="sm"
              disabled={saveSettingsMutation.isPending}
              onClick={() =>
                saveSettingsMutation.mutate({
                  confidenceThreshold: confidence / 100,
                  scanInterval: interval,
                })
              }
            >
              Save
            </Button>
          </div>
        ) : null}
        {activeLines.length > 0 ? (
          <div className={styles.autoImportProgress} id="auto-import-progress">
            <div className={styles.autoImportProgressText} id="auto-import-progress-text">
              {activeLines.length === 1
                ? `Processing ${activeLines[0]}`
                : `Processing ${activeLines.length} imports:`}
              {activeLines.length > 1
                ? activeLines.map((line) => <div key={line}>{line}</div>)
                : null}
            </div>
            <div className={styles.autoImportProgressBar}>
              <div className={styles.autoImportProgressFill} />
            </div>
          </div>
        ) : null}
      </div>

      {allResults.length > 0 ? (
        <OptionButtonGroup size="sm" className={styles.autoImportFilters}>
          {(['all', 'pending', 'imported', 'failed'] as const).map((filter) => (
            <OptionButton
              key={filter}
              selected={autoFilter === filter}
              variant={autoFilter === filter ? 'default' : 'ghost'}
              onClick={() => onFilterChange(filter)}
            >
              <span>{getAutoImportFilterLabel(filter)}</span>
              <Badge tone={getAutoImportFilterTone(filter)}>
                {getAutoImportFilterCount(filter, counts, allResults.length)}
              </Badge>
            </OptionButton>
          ))}
          <div className={styles.importPageFlexSpacer} />
          {counts.review > 0 ? (
            <Button
              variant="secondary"
              id="auto-import-approve-all"
              disabled={approveAllMutation.isPending}
              onClick={() => approveAllMutation.mutate()}
            >
              Approve All
            </Button>
          ) : null}
          {counts.imported + counts.failed > 0 ? (
            <Button
              variant="ghost"
              id="auto-import-clear-completed"
              disabled={clearMutation.isPending}
              size="sm"
              onClick={() => clearMutation.mutate()}
            >
              Clear History
            </Button>
          ) : null}
        </OptionButtonGroup>
      ) : null}

      <div className={styles.autoImportResults} id="auto-import-results">
        {resultsQuery.error ? (
          <div className={styles.autoImportEmpty}>
            Failed to load imports: {getErrorMessage(resultsQuery.error)}
          </div>
        ) : allResults.length === 0 ? (
          <div className={styles.autoImportEmpty}>
            <p>No imports yet. Drop album folders or single tracks into your import folder.</p>
          </div>
        ) : results.length === 0 ? (
          <div className={styles.autoImportEmpty}>
            <p>No {autoFilter === 'pending' ? 'pending review' : autoFilter} items.</p>
          </div>
        ) : (
          results.map((result, index) => (
            <AutoImportResultCard
              key={result.id}
              expanded={expandedRows.has(result.id)}
              index={index}
              approvePending={approveMutation.isPending}
              rejectPending={rejectMutation.isPending}
              result={result}
              status={statusQuery.data}
              onApprove={() => approveMutation.mutate(result.id)}
              onReject={() => rejectMutation.mutate(result.id)}
              onToggle={() => {
                setExpandedRows((current) => {
                  const next = new Set(current);
                  if (next.has(result.id)) next.delete(result.id);
                  else next.add(result.id);
                  return next;
                });
              }}
            />
          ))
        )}
      </div>
    </>
  );
}

function AutoImportResultCard({
  approvePending,
  expanded,
  index,
  rejectPending,
  result,
  status,
  onApprove,
  onReject,
  onToggle,
}: {
  approvePending: boolean;
  expanded: boolean;
  index: number;
  rejectPending: boolean;
  result: ImportAutoImportResult;
  status: ImportAutoImportStatusPayload | undefined;
  onApprove: () => void;
  onReject: () => void;
  onToggle: () => void;
}) {
  const confidencePercent = Math.round((result.confidence || 0) * 100);
  const confidenceClass = getConfidenceClass(confidencePercent);
  const statusMeta = getAutoImportStatusMeta(result.status);
  const liveActive = status?.active_imports?.find(
    (item) => item.folder_hash === result.folder_hash,
  );
  const isLiveProcessing = result.status === 'processing' && liveActive?.status === 'processing';
  const liveTrackIndex = isLiveProcessing ? liveActive?.track_index || 0 : 0;
  const liveTrackTotal = isLiveProcessing ? liveActive?.track_total || 0 : 0;
  const liveTrackName = isLiveProcessing ? liveActive?.track_name || '' : '';
  const matchData = parseAutoImportMatchData(result.match_data);
  const trackDetails =
    matchData.matches?.map((match) => ({
      name: match.track_name || match.track?.name || 'Unknown',
      file: match.file ? match.file.split(/[/\\]/).pop() || '?' : '?',
      confidence: Math.round((match.confidence || 0) * 100),
    })) ?? [];
  const matchSummary =
    isLiveProcessing && liveTrackTotal > 0
      ? `track ${liveTrackIndex}/${liveTrackTotal}: ${liveTrackName}`
      : matchData.total_tracks && matchData.total_tracks > 0
        ? `${matchData.matched_count || 0}/${matchData.total_tracks} tracks`
        : `${result.total_files || 0} files`;
  const methodLabel = getMethodLabel(result.identification_method);
  const timeAgo = getAutoImportTimeAgo(result.created_at);
  const statusCardClass = getAutoImportCardClass(statusMeta.className);
  const statusBadgeClass = getAutoImportBadgeClass(statusMeta.className);
  const confidenceFillClass = getAutoImportConfidenceClass(confidenceClass);

  return (
    <div
      className={clsx(styles.autoImportCard, statusCardClass)}
      role="button"
      tabIndex={0}
      onClick={onToggle}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          onToggle();
        }
      }}
    >
      <div className={styles.autoImportCardTop}>
        <div className={styles.autoImportCardLeft}>
          {result.image_url ? (
            <img
              className={styles.autoImportCardArt}
              src={result.image_url}
              alt=""
              onError={fallbackImage}
            />
          ) : (
            <div className={styles.autoImportCardArtFallback}>💿</div>
          )}
        </div>
        <div className={styles.autoImportCardCenter}>
          <div className={styles.autoImportCardAlbum}>
            {result.album_name || result.folder_name}
          </div>
          <div className={styles.autoImportCardArtist}>
            {result.artist_name || 'Unknown Artist'}
          </div>
          <div className={styles.autoImportCardMeta}>
            <span>{matchSummary}</span>
            {methodLabel ? (
              <span className={styles.autoImportMethodBadge}>{methodLabel}</span>
            ) : null}
            {timeAgo ? <span>{timeAgo}</span> : null}
          </div>
          {result.error_message ? (
            <div className={styles.autoImportCardError}>{result.error_message}</div>
          ) : null}
        </div>
        <div className={styles.autoImportCardRight}>
          <div className={clsx(styles.autoImportStatusBadge, statusBadgeClass)}>
            {statusMeta.icon} {statusMeta.label}
          </div>
          <div className={styles.autoImportConfidenceBar}>
            <div
              className={clsx(styles.autoImportConfidenceFill, confidenceFillClass)}
              style={{ width: `${confidencePercent}%` }}
            />
          </div>
          <div className={styles.autoImportConfidenceText}>{confidencePercent}% confidence</div>
          {result.status === 'pending_review' ? (
            <div className={styles.autoImportActions}>
              <Button
                variant="primary"
                disabled={approvePending}
                onClick={(event) => {
                  event.stopPropagation();
                  onApprove();
                }}
              >
                Approve & Import
              </Button>
              <Button
                variant="secondary"
                disabled={rejectPending}
                onClick={(event) => {
                  event.stopPropagation();
                  onReject();
                }}
              >
                Dismiss
              </Button>
            </div>
          ) : null}
        </div>
      </div>
      <div className={styles.autoImportCardFolderPath}>{result.folder_name}</div>
      {trackDetails.length > 0 ? (
        <div
          className={clsx(styles.autoImportTrackList, {
            [styles.expanded]: expanded,
          })}
          id={`auto-import-tracks-${index}`}
        >
          <div className={styles.autoImportTrackListHeader}>
            <span>Track</span>
            <span>Matched File</span>
            <span>Conf</span>
          </div>
          {trackDetails.map((track, trackIndex) => {
            const rowClassName = clsx(styles.autoImportTrackRow, {
              [styles.autoImportTrackRowActive]:
                isLiveProcessing && liveTrackIndex > 0 && trackIndex + 1 === liveTrackIndex,
              [styles.autoImportTrackRowDone]:
                isLiveProcessing && liveTrackIndex > 0 && trackIndex + 1 < liveTrackIndex,
            });
            return (
              <div key={`${track.name}-${track.file}-${trackIndex}`} className={rowClassName}>
                <span className={styles.autoImportTrackName}>{track.name}</span>
                <span className={styles.autoImportTrackFile}>{track.file}</span>
                <span
                  className={clsx(
                    styles.autoImportTrackConf,
                    getAutoImportConfidenceClass(getConfidenceClass(track.confidence)),
                  )}
                >
                  {track.confidence}%
                </span>
              </div>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}

function showMutationError(error: unknown) {
  window.showToast?.(getErrorMessage(error), 'error');
}

function getAutoImportCardClass(status: string): string {
  const classes: Record<string, string> = {
    completed: styles.autoImportCompleted,
    review: styles.autoImportReview,
    failed: styles.autoImportFailed,
    processing: styles.autoImportProcessing,
  };

  return classes[status] ?? '';
}

function getAutoImportBadgeClass(status: string): string {
  const classes: Record<string, string> = {
    completed: styles.autoImportBadgeCompleted,
    review: styles.autoImportBadgeReview,
    failed: styles.autoImportBadgeFailed,
    neutral: styles.autoImportBadgeNeutral,
    processing: styles.autoImportBadgeProcessing,
  };

  return classes[status] ?? '';
}

function getAutoImportConfidenceClass(status: string): string {
  const classes: Record<string, string> = {
    high: styles.autoImportConfHigh,
    medium: styles.autoImportConfMedium,
    low: styles.autoImportConfLow,
  };

  return classes[status] ?? '';
}

function getAutoImportFilterLabel(filter: ImportAutoFilter): string {
  switch (filter) {
    case 'all':
      return 'All';
    case 'pending':
      return 'Needs Review';
    case 'imported':
      return 'Imported';
    case 'failed':
      return 'Failed';
  }
}

function getAutoImportFilterCount(
  filter: ImportAutoFilter,
  counts: ReturnType<typeof getAutoImportCounts>,
  totalCount: number,
): number {
  switch (filter) {
    case 'all':
      return totalCount;
    case 'pending':
      return counts.review;
    case 'imported':
      return counts.imported;
    case 'failed':
      return counts.failed;
  }
}

function getAutoImportFilterTone(
  filter: ImportAutoFilter,
): 'neutral' | 'warning' | 'success' | 'danger' {
  switch (filter) {
    case 'pending':
      return 'warning';
    case 'imported':
      return 'success';
    case 'failed':
      return 'danger';
    case 'all':
      return 'neutral';
  }
}

function getMethodLabel(method: string | null | undefined): string {
  const labels: Record<string, string> = {
    tags: 'Tags',
    folder_name: 'Folder Name',
    acoustid: 'AcoustID',
    filename: 'Filename',
  };
  return method ? labels[method] || method : '';
}

async function confirmAction({
  title,
  message,
  confirmText,
}: {
  title: string;
  message: string;
  confirmText: string;
}): Promise<boolean> {
  if (window.showConfirmDialog) {
    return await window.showConfirmDialog({ title, message, confirmText });
  }
  return window.confirm(message);
}
