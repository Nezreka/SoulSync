import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { useEffect } from 'react';

import { Select } from '@/components/form';
import { Show } from '@/components/primitives';
import { useProfile, useReactPageShell } from '@/platform/shell/route-controllers';

import type { IssueCounts, IssuePriority, IssueRecord, IssuesSearch } from '../-issues.types';

import { issueCountsQueryOptions, issueListQueryOptions } from '../-issues.api';
import {
  REFRESH_EVENT,
  dispatchIssuesRefreshEvent,
  formatIssueDate,
  getEntityDetails,
  getEntityLabel,
  getEntityName,
  getIssueArtwork,
  getPriorityClassName,
  ISSUE_CATEGORY_META,
  ISSUE_STATUS_META,
  getIssueCategoryMeta,
  getIssueStatusMeta,
  parseSnapshot,
} from '../-issues.helpers';
import { ISSUE_CATEGORY_VALUES, ISSUE_SEARCH_STATUS_VALUES } from '../-issues.types';
import { Route } from '../route';
import { IssueDetailModal } from './issue-detail-modal';
import styles from './issues-page.module.css';

export function IssuesPage() {
  useReactPageShell('issues');
  const navigate = useNavigate({ from: Route.fullPath });
  const params = Route.useSearch();

  const clearIssueSelection = () => {
    void navigate({
      to: Route.fullPath,
      search: (prev) => ({ ...prev, issueId: undefined }),
      replace: true,
    });
  };

  return (
    <>
      <IssueBoard />
      <IssueDetailModal
        issueId={params.issueId}
        onClose={clearIssueSelection}
        onMutationSuccess={() => {
          clearIssueSelection();
          dispatchIssuesRefreshEvent();
        }}
      />
    </>
  );
}

function IssueBoard() {
  const { isAdmin, profileId } = useProfile();
  const queryClient = useQueryClient();
  const navigate = useNavigate({ from: Route.fullPath });
  const params = Route.useSearch();

  useEffect(() => {
    const handleRefresh = () => {
      void queryClient.invalidateQueries({ queryKey: ['issues'] });
    };

    window.addEventListener(REFRESH_EVENT, handleRefresh);
    return () => {
      window.removeEventListener(REFRESH_EVENT, handleRefresh);
    };
  }, [queryClient]);

  const countsQuery = useQuery({
    ...issueCountsQueryOptions(profileId),
  });
  const issuesQuery = useQuery({
    ...issueListQueryOptions(profileId, params),
  });

  const openIssue = (issueId: number) => {
    void navigate({
      to: Route.fullPath,
      search: (prev) => ({ ...prev, issueId }),
    });
  };

  const onCategoryChange = (category: IssuesSearch['category']) => {
    void navigate({
      to: Route.fullPath,
      search: (prev) => ({ ...prev, category }),
      replace: true,
    });
  };

  const onStatusChange = (status: IssuesSearch['status']) => {
    void navigate({
      to: Route.fullPath,
      search: (prev) => ({ ...prev, status }),
      replace: true,
    });
  };

  return (
    <div className={styles.issuesContainer} data-testid="issues-board">
      <IssueBoardHeader
        isAdmin={isAdmin}
        category={params.category}
        status={params.status}
        onCategoryChange={onCategoryChange}
        onStatusChange={onStatusChange}
      />
      <IssueBoardStats counts={countsQuery.data ?? EMPTY_ISSUE_COUNTS} />
      <IssueBoardList
        categoryFilter={params.category}
        issues={issuesQuery.data?.issues ?? []}
        issuesError={issuesQuery.error}
        issuesLoading={issuesQuery.isLoading}
        showReporterName={isAdmin}
        onIssueSelect={openIssue}
        statusFilter={params.status}
      />
    </div>
  );
}

function IssueBoardHeader({
  category,
  isAdmin,
  status,
  onCategoryChange,
  onStatusChange,
}: {
  category: IssuesSearch['category'];
  isAdmin: boolean;
  status: IssuesSearch['status'];
  onCategoryChange: (category: IssuesSearch['category']) => void;
  onStatusChange: (status: IssuesSearch['status']) => void;
}) {
  return (
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
            value={status}
            onChange={(event) => onStatusChange(event.target.value as IssuesSearch['status'])}
          >
            {ISSUE_SEARCH_STATUS_VALUES.map((option) => (
              <option key={option} value={option}>
                {getIssueStatusFilterLabel(option)}
              </option>
            ))}
          </Select>
          <Select
            id="issues-filter-category"
            aria-label="Category"
            value={category}
            onChange={(event) => onCategoryChange(event.target.value as IssuesSearch['category'])}
          >
            <option value="all">All Categories</option>
            {ISSUE_CATEGORY_FILTER_GROUPS.map((group) => (
              <optgroup key={group.label} label={group.label}>
                {getIssueCategoryFilterOptions(group).map((option) => (
                  <option key={option} value={option}>
                    {ISSUE_CATEGORY_META[option].label}
                  </option>
                ))}
              </optgroup>
            ))}
          </Select>
        </div>
      </div>
    </div>
  );
}

function IssueBoardStats({ counts }: { counts: IssueCounts }) {
  return (
    <div className={styles.issuesStats} id="issues-stats" data-testid="issue-counts">
      <IssueStatCard className={styles.issuesStatOpen} label="Open" value={counts.open} />
      <IssueStatCard
        className={styles.issuesStatProgress}
        label="In Progress"
        value={counts.in_progress}
      />
      <IssueStatCard
        className={styles.issuesStatResolved}
        label="Resolved"
        value={counts.resolved}
      />
      <IssueStatCard
        className={styles.issuesStatDismissed}
        label="Dismissed"
        value={counts.dismissed}
      />
      <IssueStatCard className={styles.issuesStatTotal} label="Total" value={counts.total} />
    </div>
  );

  function IssueStatCard({
    className,
    label,
    value,
  }: {
    className: string;
    label: string;
    value: number;
  }) {
    return (
      <div className={`${styles.issuesStatCard} ${className}`}>
        <div className={styles.issuesStatNumber}>{value}</div>
        <div className={styles.issuesStatLabel}>{label}</div>
      </div>
    );
  }
}

function IssueBoardList({
  categoryFilter,
  issues,
  issuesError,
  issuesLoading,
  onIssueSelect,
  showReporterName,
  statusFilter,
}: {
  categoryFilter: string;
  issues: IssueRecord[];
  issuesError: unknown;
  issuesLoading: boolean;
  onIssueSelect: (issueId: number) => void;
  showReporterName: boolean;
  statusFilter: IssuesSearch['status'];
}) {
  return (
    <div className={styles.issuesList} id="issues-list" data-testid="issue-list">
      <IssueBoardListContent />
    </div>
  );

  function IssueBoardListContent() {
    if (issuesLoading) {
      return (
        <div className={styles.issuesLoading}>
          <div className={styles.issuesSpinner} />
          Loading issues...
        </div>
      );
    }

    if (issuesError) {
      return (
        <div className={styles.issuesEmpty}>
          <div className={styles.issuesEmptyTitle}>Failed to load issues</div>
          <div className={styles.issuesEmptyText}>
            {issuesError instanceof Error ? issuesError.message : 'Unknown error'}
          </div>
        </div>
      );
    }

    if (issues.length === 0) {
      return (
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
      );
    }

    return issues.map((issue) => (
      <IssueBoardCard
        key={issue.id}
        issue={issue}
        showReporterName={showReporterName}
        onIssueSelect={onIssueSelect}
      />
    ));
  }
}

function IssueBoardCard({
  issue,
  showReporterName,
  onIssueSelect,
}: {
  issue: IssueRecord;
  showReporterName: boolean;
  onIssueSelect: (issueId: number) => void;
}) {
  const snapshot = parseSnapshot(issue.snapshot_data);
  const artwork = getIssueArtwork(snapshot);
  const entityName = getEntityName(issue, snapshot);
  const details = getEntityDetails(issue, snapshot);
  const statusMeta = getIssueStatusMeta(issue.status) || ISSUE_STATUS_META.open;
  const catMeta = getIssueCategoryMeta(issue.category) || ISSUE_CATEGORY_META.other;
  const priorityClass = getIssuePriorityClassName(getPriorityClassName(issue.priority));
  const statusClassName = getIssueStatusClassName(issue.status);
  const createdDate = formatIssueDate(issue.created_at);

  return (
    <button
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
          <Show when={issue.admin_response}>
            <span className={styles.issueCardResponded} title="Admin has responded">
              💬
            </span>
          </Show>
        </div>
        <div className={styles.issueCardEntity}>
          <span className={styles.issueCardEntityType}>{getEntityLabel(issue.entity_type)}</span>
          <span className={styles.issueCardEntityName}>{entityName}</span>
          <Show when={details.length}>
            <span className={styles.issueCardMetaLine}>{details.join(' - ')}</span>
          </Show>
        </div>
        <Show when={issue.description}>
          <div className={styles.issueCardDescription}>{issue.description}</div>
        </Show>
        <div className={styles.issueCardFooter}>
          <span className={styles.issueCardDate}>{createdDate}</span>
          <Show when={showReporterName && issue.reporter_name}>
            <span className={styles.issueCardProfile}>by {issue.reporter_name}</span>
          </Show>
        </div>
      </div>
      <div className={styles.issueCardRight}>
        <span className={`${styles.issueStatusBadge} ${statusClassName}`}>{statusMeta.label}</span>
        <span
          className={`${styles.issuePriorityDot} ${priorityClass}`}
          title={`${issue.priority} priority`}
        />
      </div>
    </button>
  );
}

const EMPTY_ISSUE_COUNTS: IssueCounts = {
  open: 0,
  in_progress: 0,
  resolved: 0,
  dismissed: 0,
  total: 0,
};

const ISSUE_STATUS_CLASS_NAMES: Record<IssueRecord['status'], string> = {
  open: styles.issueStatusOpen,
  in_progress: styles.issueStatusProgress,
  resolved: styles.issueStatusResolved,
  dismissed: styles.issueStatusDismissed,
};

const ISSUE_PRIORITY_CLASS_NAMES: Record<IssuePriority, string> = {
  high: styles.issuePriorityHigh,
  low: styles.issuePriorityLow,
  normal: styles.issuePriorityNormal,
};

function getIssueStatusFilterLabel(status: IssuesSearch['status']): string {
  if (status === 'all') return 'All Statuses';
  return getIssueStatusMeta(status)?.label || status.replace(/_/g, ' ');
}

function getIssueStatusClassName(status: IssueRecord['status']): string {
  return ISSUE_STATUS_CLASS_NAMES[status] || styles.issueStatusOpen;
}

function getIssuePriorityClassName(priority: IssuePriority): string {
  return ISSUE_PRIORITY_CLASS_NAMES[priority] || styles.issuePriorityNormal;
}

const ISSUE_CATEGORY_FILTER_GROUPS = [
  {
    label: 'Track Issues',
    matches: (applies: Array<'track' | 'album' | 'artist'>) =>
      applies.length === 1 && applies.includes('track'),
  },
  {
    label: 'Album Issues',
    matches: (applies: Array<'track' | 'album' | 'artist'>) =>
      applies.length === 1 && applies.includes('album'),
  },
  {
    label: 'Both',
    matches: (applies: Array<'track' | 'album' | 'artist'>) => applies.length > 1,
  },
] as const;

function getIssueCategoryFilterOptions(group: (typeof ISSUE_CATEGORY_FILTER_GROUPS)[number]) {
  return ISSUE_CATEGORY_VALUES.filter((category) =>
    group.matches(ISSUE_CATEGORY_META[category].applies),
  );
}
