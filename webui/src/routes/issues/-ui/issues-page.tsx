import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate } from '@tanstack/react-router';
import { useEffect, useMemo, useRef, useState } from 'react';

import { Select, TextInput } from '@/components/form';
import { PageHeader } from '@/components/page-header';
import { Show } from '@/components/primitives';
import { useProfile, useReactPageShell } from '@/platform/shell/route-controllers';

import type {
  IssueCounts,
  IssueEntityType,
  IssuePriority,
  IssueRecord,
  IssuesSearch,
} from '../-issues.types';

import {
  bulkResultToast,
  bulkUpdateIssues,
  type IssueBulkChange,
  issueCountsQueryOptions,
  issueListQueryOptions,
  invalidateIssuesQueries,
} from '../-issues.api';
import {
  formatIssueAgo,
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
  issueMatchesText,
  parseSnapshot,
} from '../-issues.helpers';
import {
  ISSUE_CATEGORY_VALUES,
  ISSUE_ENTITY_TYPE_VALUES,
  ISSUE_PRIORITY_VALUES,
  ISSUE_SEARCH_STATUS_VALUES,
} from '../-issues.types';
import { Route } from '../route';
import { IssueDetailModal } from './issue-detail-modal';
import styles from './issues-page.module.css';

export function IssuesPage() {
  useReactPageShell('issues');
  const queryClient = useQueryClient();
  const navigate = useNavigate({ from: Route.fullPath });
  const params = Route.useSearch();

  const clearIssueSelection = () => {
    void navigate({
      to: Route.fullPath,
      search: (prev) => ({ ...prev, issueId: undefined }),
      replace: true,
    });
  };

  const handleMutationSuccess = () => {
    clearIssueSelection();
    void invalidateIssuesQueries(queryClient);
  };

  return (
    <>
      <IssueBoard />
      <IssueDetailModal
        issueId={params.issueId}
        onClose={clearIssueSelection}
        onMutationSuccess={handleMutationSuccess}
      />
    </>
  );
}

type IssueScope = 'everyone' | 'mine';

function IssueBoard() {
  const { isAdmin, profileId } = useProfile();
  const navigate = useNavigate({ from: Route.fullPath });
  const params = Route.useSearch();
  // text search and the admin's mine/everyone toggle filter the loaded rows;
  // the server has no param for either, and a page of 50 is cheap to scan
  const [text, setText] = useState('');
  const [scope, setScope] = useState<IssueScope>('everyone');

  const countsQuery = useQuery({
    ...issueCountsQueryOptions(profileId),
  });
  const issuesQuery = useInfiniteQuery({
    ...issueListQueryOptions(profileId, params),
  });
  const loaded = useMemo(
    () => issuesQuery.data?.pages.flatMap((page) => page.issues ?? []) ?? [],
    [issuesQuery.data],
  );
  const total = issuesQuery.data?.pages[0]?.total ?? loaded.length;
  const visible = useMemo(
    () =>
      loaded.filter(
        (issue) =>
          (scope === 'everyone' || issue.profile_id === profileId) && issueMatchesText(issue, text),
      ),
    [loaded, scope, profileId, text],
  );
  const narrowed = Boolean(text.trim()) || scope === 'mine';

  // admin bulk triage: ticked rows, always a subset of what's on screen
  const [picked, setPicked] = useState<ReadonlySet<number>>(() => new Set());
  // a new filter starts a new selection. same set back when nothing is
  // ticked, so this never costs a render (the list rebuilds on each one)
  useEffect(() => {
    setPicked((current) => (current.size ? new Set() : current));
  }, [params.status, params.category, params.entity, text, scope]);
  const selectedIds = useMemo(
    () => visible.filter((issue) => picked.has(issue.id)).map((issue) => issue.id),
    [visible, picked],
  );
  const togglePicked = (id: number) =>
    setPicked((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const onEntityChange = (entity: IssueEntityType | 'all') => {
    void navigate({
      to: Route.fullPath,
      search: (prev) => ({ ...prev, entity: entity === 'all' ? undefined : entity }),
      replace: true,
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
      <div className={styles.issuesRefine}>
        <TextInput
          id="issues-filter-text"
          aria-label="Search issues"
          type="search"
          placeholder="Search titles, items, people…"
          value={text}
          onChange={(event) => setText(event.target.value)}
        />
        <Select
          id="issues-filter-entity"
          aria-label="Item type"
          value={params.entity ?? 'all'}
          onChange={(event) => onEntityChange(event.target.value as IssueEntityType | 'all')}
        >
          <option value="all">All items</option>
          {ISSUE_ENTITY_TYPE_VALUES.map((entity) => (
            <option key={entity} value={entity}>
              {ENTITY_FILTER_LABELS[entity]}
            </option>
          ))}
        </Select>
        <Show when={isAdmin}>
          <div className={styles.issuesScope} role="group" aria-label="Whose issues">
            {(['everyone', 'mine'] as const).map((option) => (
              <button
                key={option}
                type="button"
                className={styles.issuesScopeButton}
                aria-pressed={scope === option}
                onClick={() => setScope(option)}
              >
                {option === 'everyone' ? 'Everyone' : 'Mine'}
              </button>
            ))}
          </div>
        </Show>
      </div>
      <IssueBoardStats counts={countsQuery.data ?? EMPTY_ISSUE_COUNTS} />
      <IssueBoardList
        filtered={
          params.status !== 'open' ||
          params.category !== 'all' ||
          Boolean(params.entity) ||
          narrowed
        }
        issues={visible}
        issuesError={issuesQuery.error}
        issuesLoading={issuesQuery.isLoading}
        profileId={profileId}
        showReporterName={isAdmin}
        selectable={isAdmin}
        picked={picked}
        onTogglePicked={togglePicked}
      />
      <Show when={isAdmin && selectedIds.length > 0}>
        <IssueBulkBar
          ids={selectedIds}
          total={visible.length}
          onSelectAll={() => setPicked(new Set(visible.map((issue) => issue.id)))}
          onClear={() => setPicked(new Set())}
        />
      </Show>
      <Show when={issuesQuery.hasNextPage}>
        <div className={styles.issuesMore}>
          <span className={styles.issuesMoreCount}>
            Showing {loaded.length} of {total}
          </span>
          <button
            type="button"
            className={styles.issuesMoreButton}
            disabled={issuesQuery.isFetchingNextPage}
            onClick={() => void issuesQuery.fetchNextPage()}
          >
            {issuesQuery.isFetchingNextPage ? 'Loading…' : 'Load more'}
          </button>
        </div>
      </Show>
    </div>
  );
}

/**
 * the slim bar that shows up once rows are ticked: resolve, close, priority,
 * delete. one call to /bulk, then counts and list refresh together.
 */
function IssueBulkBar({
  ids,
  total,
  onSelectAll,
  onClear,
}: {
  ids: number[];
  total: number;
  onSelectAll: () => void;
  onClear: () => void;
}) {
  const queryClient = useQueryClient();
  const [priorityOpen, setPriorityOpen] = useState(false);
  const priorityRef = useRef<HTMLDivElement | null>(null);

  const mutation = useMutation({
    mutationFn: (change: IssueBulkChange) => bulkUpdateIssues(ids, change),
    onSuccess: (result, change) => {
      const toast = bulkResultToast(change, result);
      window.showToast?.(toast.message, toast.type);
      onClear();
      void invalidateIssuesQueries(queryClient);
    },
    onError: (error) => {
      window.showToast?.(error instanceof Error ? error.message : 'Something went wrong', 'error');
    },
  });

  useEffect(() => {
    if (!priorityOpen) return;
    const onDown = (event: MouseEvent) => {
      if (!priorityRef.current?.contains(event.target as Node)) setPriorityOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setPriorityOpen(false);
    };
    document.addEventListener('mousedown', onDown, true);
    document.addEventListener('keydown', onKey, true);
    return () => {
      document.removeEventListener('mousedown', onDown, true);
      document.removeEventListener('keydown', onKey, true);
    };
  }, [priorityOpen]);

  const remove = async () => {
    const ok = await window.showConfirmDialog?.({
      title: ids.length === 1 ? 'Delete this issue?' : `Delete ${ids.length} issues?`,
      message: 'The reports and their threads are removed for good.',
      confirmText: 'Delete',
      cancelText: 'Cancel',
      destructive: true,
    });
    if (ok) mutation.mutate({ delete: true });
  };

  const busy = mutation.isPending;
  return (
    <div className={styles.bulkBar} role="toolbar" aria-label="Selected issues">
      <span className={styles.bulkCount} aria-live="polite">
        {ids.length} selected
      </span>
      <Show when={ids.length < total}>
        <button type="button" className={styles.bulkQuiet} onClick={onSelectAll}>
          Select all {total}
        </button>
      </Show>
      <span className={styles.bulkSpacer} />
      <button
        type="button"
        className={styles.bulkButton}
        disabled={busy}
        onClick={() => mutation.mutate({ status: 'resolved' })}
      >
        Resolve
      </button>
      <button
        type="button"
        className={styles.bulkButton}
        disabled={busy}
        title="Close without a change"
        onClick={() => mutation.mutate({ status: 'dismissed' })}
      >
        Close
      </button>
      <div className={styles.bulkMenuRoot} ref={priorityRef}>
        <button
          type="button"
          className={styles.bulkButton}
          disabled={busy}
          aria-haspopup="menu"
          aria-expanded={priorityOpen}
          onClick={() => setPriorityOpen((open) => !open)}
        >
          Priority ▾
        </button>
        {priorityOpen ? (
          <div className={styles.bulkMenu} role="menu" aria-label="Priority">
            {ISSUE_PRIORITY_VALUES.map((priority, index) => (
              <button
                key={priority}
                autoFocus={index === 0}
                type="button"
                role="menuitem"
                className={styles.bulkMenuItem}
                onClick={() => {
                  setPriorityOpen(false);
                  mutation.mutate({ priority });
                }}
              >
                {priority[0].toUpperCase() + priority.slice(1)}
              </button>
            ))}
          </div>
        ) : null}
      </div>
      <button
        type="button"
        className={`${styles.bulkButton} ${styles.bulkDanger}`}
        disabled={busy}
        onClick={() => void remove()}
      >
        Delete
      </button>
      <button
        type="button"
        className={styles.bulkClose}
        aria-label="Clear selection"
        onClick={onClear}
      >
        ×
      </button>
    </div>
  );
}

const ENTITY_FILTER_LABELS: Record<IssueEntityType, string> = {
  track: 'Tracks',
  album: 'Albums',
  artist: 'Artists',
};

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
    <PageHeader
      id="issues-header"
      icon={
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={1.6}
          strokeLinecap="round"
          strokeLinejoin="round"
          style={{ color: 'rgb(var(--accent-light-rgb))' }}
          aria-hidden="true"
        >
          <path d="M9 3h6a2 2 0 0 1 2 2h1a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h1a2 2 0 0 1 2-2Z" />
          <path d="M9.5 3.6a1.8 1.8 0 0 0 0 3.4h5a1.8 1.8 0 0 0 0-3.4" />
          <path d="M8.5 13.5l2 2 4-4" />
        </svg>
      }
      title="Issues"
      subtitle={
        // Named against Library Maintenance on purpose. These two look like the
        // same page and are not: nothing here is ever written by a scan, every
        // row is a person reporting something (api/issues.py is the only writer).
        // "Isn't this what Library Maintenance shows as well?" (#1210)
        isAdmin
          ? 'Problems people reported by hand — automated scans live in Library Maintenance'
          : 'Problems you reported — automated scans live in Library Maintenance'
      }
      actions={
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
      }
    />
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
  filtered,
  issues,
  issuesError,
  issuesLoading,
  profileId,
  showReporterName,
  selectable,
  picked,
  onTogglePicked,
}: {
  filtered: boolean;
  issues: IssueRecord[];
  issuesError: unknown;
  issuesLoading: boolean;
  profileId: number;
  showReporterName: boolean;
  selectable: boolean;
  picked: ReadonlySet<number>;
  onTogglePicked: (id: number) => void;
}) {
  const selecting = selectable && issues.some((issue) => picked.has(issue.id));
  return (
    <div
      className={`${styles.issuesList} ${selecting ? styles.issuesListSelecting : ''}`}
      id="issues-list"
      data-testid="issue-list"
    >
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
            {filtered ? 'Try adjusting your filters' : 'No issues have been reported yet'}
          </div>
        </div>
      );
    }

    return issues.map((issue) => (
      <div
        key={issue.id}
        className={`${styles.issueRow} ${picked.has(issue.id) ? styles.issueRowPicked : ''}`}
      >
        <Show when={selectable}>
          <input
            type="checkbox"
            className={styles.issueRowCheck}
            aria-label={`Select ${issue.title}`}
            checked={picked.has(issue.id)}
            onChange={() => onTogglePicked(issue.id)}
          />
        </Show>
        <IssueBoardCard
          issue={issue}
          showReporterName={showReporterName}
          unread={Boolean(issue.reporter_unread) && issue.profile_id === profileId}
        />
      </div>
    ));
  }
}

function IssueBoardCard({
  issue,
  showReporterName,
  unread,
}: {
  issue: IssueRecord;
  showReporterName: boolean;
  /** the reporter has news on this one they haven't opened */
  unread: boolean;
}) {
  const snapshot = parseSnapshot(issue.snapshot_data);
  const artwork = getIssueArtwork(snapshot);
  const entityName = getEntityName(issue, snapshot);
  const details = getEntityDetails(issue, snapshot);
  const statusMeta = getIssueStatusMeta(issue.status) || ISSUE_STATUS_META.open;
  const catMeta = getIssueCategoryMeta(issue.category) || ISSUE_CATEGORY_META.other;
  const priorityClass = getIssuePriorityClassName(getPriorityClassName(issue.priority));
  const statusClassName = getIssueStatusClassName(issue.status);
  const createdDate = formatIssueAgo(issue.created_at);

  return (
    <Link
      className={styles.issueCard}
      data-testid={`issue-card-${issue.id}`}
      to={Route.fullPath}
      search={(prev) => ({ ...prev, issueId: issue.id })}
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
          <Show when={unread}>
            <span className={styles.issueCardUnread} title="New reply or status change">
              New
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
          <span className={styles.issueCardDate} title={formatIssueDate(issue.created_at)}>
            {createdDate}
          </span>
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
    </Link>
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
    label: 'Several',
    matches: (applies: Array<'track' | 'album' | 'artist'>) => applies.length > 1,
  },
] as const;

function getIssueCategoryFilterOptions(group: (typeof ISSUE_CATEGORY_FILTER_GROUPS)[number]) {
  return ISSUE_CATEGORY_VALUES.filter((category) =>
    group.matches(ISSUE_CATEGORY_META[category].applies),
  );
}
