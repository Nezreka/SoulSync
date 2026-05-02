import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { useEffect } from 'react';

import { Select } from '@/components/form';
import { getShellProfileContext } from '@/platform/shell/bridge';
import { useReactPageShell } from '@/platform/shell/route-controllers';

import type { IssueCounts, IssueRecord, IssueStatus } from '../-issues.types';

import { issueCountsQueryOptions, issueListQueryOptions } from '../-issues.api';
import {
  CLOSE_EVENT,
  REFRESH_EVENT,
  dispatchIssuesRefreshEvent,
  getEntityDetails,
  getEntityLabel,
  getEntityName,
  getIssueArtwork,
  getPriorityClassName,
  formatIssueDate,
  ISSUE_CATEGORY_META,
  ISSUE_STATUS_META,
  normalizeIssuesSearch,
  parseSnapshot,
} from '../-issues.helpers';
import { Route } from '../route';
import { IssueDetailModal } from './issue-detail-modal';
import styles from './issues-page.module.css';

type NavigateFunction = ReturnType<typeof useNavigate>;

function clearIssueSelection(navigate: NavigateFunction) {
  void navigate({
    to: Route.fullPath,
    search: (prev) => normalizeIssuesSearch({ ...prev, issueId: undefined }),
    replace: true,
  });
}

export function IssuesPage() {
  const bridge = useReactPageShell('issues');
  const queryClient = useQueryClient();
  const navigate = useNavigate({ from: Route.fullPath });
  const search = Route.useSearch();
  const normalizedSearch = normalizeIssuesSearch(search);
  const selectedIssueId = normalizedSearch.issueId ? Number(normalizedSearch.issueId) : null;

  const profile = getShellProfileContext(bridge);
  const profileId = profile?.profileId ?? 0;

  const openIssue = (issueId: number) => {
    void navigate({
      to: Route.fullPath,
      search: (prev) => normalizeIssuesSearch({ ...prev, issueId }),
    });
  };

  useEffect(() => {
    const handleRefresh = () => {
      void queryClient.invalidateQueries({ queryKey: ['issues'] });
    };

    const handleClose = () => {
      clearIssueSelection(navigate);
    };

    window.addEventListener(REFRESH_EVENT, handleRefresh);
    window.addEventListener(CLOSE_EVENT, handleClose);
    return () => {
      window.removeEventListener(REFRESH_EVENT, handleRefresh);
      window.removeEventListener(CLOSE_EVENT, handleClose);
    };
  }, [navigate, queryClient]);

  const countsQuery = useQuery({
    ...issueCountsQueryOptions(profileId),
    enabled: profileId > 0,
  });
  const issuesQuery = useQuery({
    ...issueListQueryOptions(profileId, normalizedSearch),
    enabled: profileId > 0,
  });

  if (!bridge || !profile || !bridge.isPageAllowed('issues')) {
    return null;
  }

  return (
    <>
      <IssueBoard
        categoryFilter={normalizedSearch.category}
        counts={countsQuery.data}
        isAdmin={profile.isAdmin}
        issues={issuesQuery.data?.issues ?? []}
        issuesError={issuesQuery.error}
        issuesLoading={issuesQuery.isLoading}
        onCategoryChange={(category) =>
          void navigate({
            to: Route.fullPath,
            search: (prev) =>
              normalizeIssuesSearch({
                ...prev,
                category,
              }),
            replace: true,
          })
        }
        onIssueSelect={openIssue}
        onStatusChange={(status) =>
          void navigate({
            to: Route.fullPath,
            search: (prev) =>
              normalizeIssuesSearch({
                ...prev,
                status,
              }),
            replace: true,
          })
        }
        statusFilter={normalizedSearch.status}
      />
      <IssueDetailModal
        isAdmin={profile.isAdmin}
        issueId={selectedIssueId}
        onClose={() => clearIssueSelection(navigate)}
        onMutationSuccess={() => {
          clearIssueSelection(navigate);
          dispatchIssuesRefreshEvent();
        }}
        profileId={profile.profileId}
      />
    </>
  );
}

function IssueBoard({
  categoryFilter,
  counts,
  isAdmin,
  issues,
  issuesError,
  issuesLoading,
  onCategoryChange,
  onIssueSelect,
  onStatusChange,
  statusFilter,
}: {
  categoryFilter: string;
  counts: IssueCounts | undefined;
  isAdmin: boolean;
  issues: IssueRecord[];
  issuesError: unknown;
  issuesLoading: boolean;
  onCategoryChange: (category: string) => void;
  onIssueSelect: (issueId: number) => void;
  onStatusChange: (status: IssueStatus | 'all') => void;
  statusFilter: IssueStatus | 'all';
}) {
  const safeCounts = counts ?? {
    open: 0,
    in_progress: 0,
    resolved: 0,
    dismissed: 0,
    total: 0,
  };

  return (
    <div className={styles.issuesContainer} data-testid="issues-board">
      <div className={styles.issuesHeader} id="issues-header">
        <div className={styles.issuesHeaderLeft}>
          <h2 className={styles.issuesTitle}>Issues</h2>
          <p className={styles.issuesSubtitle} id="issues-subtitle">
            {isAdmin
              ? 'Manage and resolve reported library problems'
              : 'Track and resolve library problems'}
          </p>
        </div>
        <div className={styles.issuesHeaderRight}>
          <div className={styles.issuesFilters} id="issues-filters">
            <Select
              id="issues-filter-status"
              aria-label="Status"
              value={statusFilter}
              onChange={(event) => onStatusChange(event.target.value as IssueStatus | 'all')}
            >
              <option value="open">Open</option>
              <option value="all">All Statuses</option>
              <option value="in_progress">In Progress</option>
              <option value="resolved">Resolved</option>
              <option value="dismissed">Dismissed</option>
            </Select>
            <Select
              id="issues-filter-category"
              aria-label="Category"
              value={categoryFilter}
              onChange={(event) => onCategoryChange(event.target.value)}
            >
              <option value="all">All Categories</option>
              <optgroup label="Track Issues">
                <option value="wrong_track">Wrong Track</option>
                <option value="wrong_artist">Wrong Artist</option>
                <option value="wrong_album">Wrong Album</option>
                <option value="audio_quality">Audio Quality</option>
              </optgroup>
              <optgroup label="Album Issues">
                <option value="wrong_cover">Wrong Cover Art</option>
                <option value="duplicate_tracks">Duplicate Tracks</option>
                <option value="missing_tracks">Missing Tracks</option>
                <option value="incomplete_album">Incomplete Album</option>
              </optgroup>
              <optgroup label="Both">
                <option value="wrong_metadata">Wrong Metadata</option>
                <option value="other">Other</option>
              </optgroup>
            </Select>
          </div>
        </div>
      </div>

      <div className={styles.issuesStats} id="issues-stats" data-testid="issue-counts">
        <div className={`${styles.issuesStatCard} ${styles.issuesStatOpen}`}>
          <div className={styles.issuesStatNumber}>{safeCounts.open}</div>
          <div className={styles.issuesStatLabel}>Open</div>
        </div>
        <div className={`${styles.issuesStatCard} ${styles.issuesStatProgress}`}>
          <div className={styles.issuesStatNumber}>{safeCounts.in_progress}</div>
          <div className={styles.issuesStatLabel}>In Progress</div>
        </div>
        <div className={`${styles.issuesStatCard} ${styles.issuesStatResolved}`}>
          <div className={styles.issuesStatNumber}>{safeCounts.resolved}</div>
          <div className={styles.issuesStatLabel}>Resolved</div>
        </div>
        <div className={`${styles.issuesStatCard} ${styles.issuesStatDismissed}`}>
          <div className={styles.issuesStatNumber}>{safeCounts.dismissed}</div>
          <div className={styles.issuesStatLabel}>Dismissed</div>
        </div>
        <div className={`${styles.issuesStatCard} ${styles.issuesStatTotal}`}>
          <div className={styles.issuesStatNumber}>{safeCounts.total}</div>
          <div className={styles.issuesStatLabel}>Total</div>
        </div>
      </div>

      <div className={styles.issuesList} id="issues-list" data-testid="issue-list">
        {issuesLoading ? (
          <div className={styles.issuesLoading}>
            <div className={styles.issuesSpinner} />
            Loading issues...
          </div>
        ) : issuesError ? (
          <div className={styles.issuesEmpty}>
            <div className={styles.issuesEmptyTitle}>Failed to load issues</div>
            <div className={styles.issuesEmptyText}>
              {issuesError instanceof Error ? issuesError.message : 'Unknown error'}
            </div>
          </div>
        ) : issues.length === 0 ? (
          <div className={styles.issuesEmpty}>
            <div className={styles.issuesEmptyIcon} aria-hidden="true">
              🔍
            </div>
            <div className={styles.issuesEmptyTitle}>No issues found</div>
            <div className={styles.issuesEmptyText}>
              {statusFilter !== 'open' || categoryFilter !== 'all'
                ? 'Try adjusting your filters'
                : 'No issues have been reported yet'}
            </div>
          </div>
        ) : (
          issues.map((issue) => {
            const snapshot = parseSnapshot(issue.snapshot_data);
            const artwork = getIssueArtwork(snapshot);
            const entityName = getEntityName(issue, snapshot);
            const details = getEntityDetails(issue, snapshot);
            const statusMeta = ISSUE_STATUS_META[issue.status] || ISSUE_STATUS_META.open;
            const catMeta = ISSUE_CATEGORY_META[issue.category] || ISSUE_CATEGORY_META.other;
            const priorityVariant = getPriorityClassName(issue.priority);
            const statusClassName =
              issue.status === 'in_progress'
                ? styles.issueStatusProgress
                : issue.status === 'resolved'
                  ? styles.issueStatusResolved
                  : issue.status === 'dismissed'
                    ? styles.issueStatusDismissed
                    : styles.issueStatusOpen;
            const priorityClass =
              priorityVariant === 'high'
                ? styles.issuePriorityHigh
                : priorityVariant === 'low'
                  ? styles.issuePriorityLow
                  : styles.issuePriorityNormal;
            const createdDate = formatIssueDate(issue.created_at);

            return (
              <button
                key={issue.id}
                className={styles.issueCard}
                type="button"
                data-testid={`issue-card-${issue.id}`}
                onClick={() => onIssueSelect(issue.id)}
              >
                <div className={styles.issueCardLeft}>
                  {artwork ? (
                    <img className={styles.issueCardThumb} src={artwork} alt="" />
                  ) : (
                    <div className={styles.issueCardThumbPlaceholder}>{catMeta.icon}</div>
                  )}
                </div>
                <div className={styles.issueCardCenter}>
                  <div className={styles.issueCardTitleRow}>
                    <span className={styles.issueCardCategoryIcon} title={catMeta.label}>
                      {catMeta.icon}
                    </span>
                    <span className={styles.issueCardTitle}>{issue.title}</span>
                    {issue.admin_response ? (
                      <span className={styles.issueCardResponded} title="Admin has responded">
                        💬
                      </span>
                    ) : null}
                  </div>
                  <div className={styles.issueCardEntity}>
                    <span className={styles.issueCardEntityType}>
                      {getEntityLabel(issue.entity_type)}
                    </span>
                    <span className={styles.issueCardEntityName}>{entityName}</span>
                    {details.length > 0 ? (
                      <span className={styles.issueCardMetaLine}>{details.join(' - ')}</span>
                    ) : null}
                  </div>
                  {issue.description ? (
                    <div className={styles.issueCardDescription}>{issue.description}</div>
                  ) : null}
                  <div className={styles.issueCardFooter}>
                    <span className={styles.issueCardDate}>{createdDate}</span>
                    {isAdmin && issue.reporter_name ? (
                      <span className={styles.issueCardProfile}>by {issue.reporter_name}</span>
                    ) : null}
                  </div>
                </div>
                <div className={styles.issueCardRight}>
                  <span className={`${styles.issueStatusBadge} ${statusClassName}`}>
                    {statusMeta.label}
                  </span>
                  <span
                    className={`${styles.issuePriorityDot} ${priorityClass}`}
                    title={`${issue.priority} priority`}
                  />
                </div>
              </button>
            );
          })
        )}
      </div>
    </div>
  );
}
