import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { createPortal } from 'react-dom';

import { getShellProfileContext } from '@/platform/shell/bridge';
import { useShellBridge } from '@/platform/shell/route-controllers';

import type { IssuePriority, IssueReportPayload } from '../-issues.types';

import {
  REFRESH_EVENT,
  createDefaultIssueTitle,
  createIssue,
  getIssueCategoriesForEntity,
  issueCountsQueryOptions,
} from '../-issues.helpers';
import styles from './issue-detail-modal.module.css';

const ISSUE_DOMAIN_QUERY_KEY = ['issues'] as const;

export function IssueDomainHost() {
  const bridge = useShellBridge();
  const queryClient = useQueryClient();
  const profile = getShellProfileContext(bridge);
  const [reportPayload, setReportPayload] = useState<IssueReportPayload | null>(null);
  const profileId = profile?.profileId ?? 0;

  const countsQuery = useQuery({
    ...issueCountsQueryOptions(profileId),
    enabled: profileId > 0,
  });

  useEffect(() => {
    if (countsQuery.data) {
      updateBadge(countsQuery.data.open || 0);
    }
  }, [countsQuery.data]);

  useEffect(() => {
    const handleRefresh = () => {
      void queryClient.invalidateQueries({ queryKey: ISSUE_DOMAIN_QUERY_KEY });
    };

    window.addEventListener(REFRESH_EVENT, handleRefresh);
    return () => {
      window.removeEventListener(REFRESH_EVENT, handleRefresh);
    };
  }, [queryClient]);

  useEffect(() => {
    window.SoulSyncIssueDomain = {
      openReportIssue(payload) {
        setReportPayload(payload);
      },
      closeReportIssue() {
        setReportPayload(null);
      },
      refresh() {
        void queryClient.invalidateQueries({ queryKey: ISSUE_DOMAIN_QUERY_KEY });
      },
    };

    return () => {
      if (window.SoulSyncIssueDomain?.openReportIssue) {
        window.SoulSyncIssueDomain = undefined;
      }
    };
  }, [queryClient]);

  if (!reportPayload) return null;

  return createPortal(
    <ReportIssueModal
      payload={reportPayload}
      profileId={profileId}
      onClose={() => setReportPayload(null)}
      onSubmitted={() => {
        setReportPayload(null);
        void queryClient.invalidateQueries({ queryKey: ISSUE_DOMAIN_QUERY_KEY });
      }}
    />,
    document.body,
  );
}

function ReportIssueModal({
  onClose,
  onSubmitted,
  payload,
  profileId,
}: {
  onClose: () => void;
  onSubmitted: () => void;
  payload: IssueReportPayload;
  profileId: number;
}) {
  const [selectedCategory, setSelectedCategory] = useState('');
  const [selectedPriority, setSelectedPriority] = useState<IssuePriority>('normal');
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [titleEdited, setTitleEdited] = useState(false);
  const [error, setError] = useState('');

  const categories = useMemo(
    () => getIssueCategoriesForEntity(payload.entityType),
    [payload.entityType],
  );
  const entityLabel =
    payload.entityType === 'track' ? 'Track' : payload.entityType === 'album' ? 'Album' : 'Artist';

  const createMutation = useMutation({
    mutationFn: async () => {
      if (!profileId) throw new Error('Profile is still loading');
      if (!selectedCategory) throw new Error('Please select an issue category');
      const trimmedTitle = title.trim();
      if (!trimmedTitle) throw new Error('Please provide a title for the issue');

      await createIssue(profileId, {
        entity_type: payload.entityType,
        entity_id: String(payload.entityId),
        category: selectedCategory,
        title: trimmedTitle,
        description: description.trim(),
        priority: selectedPriority,
      });
    },
    onError: (mutationError) => {
      const message =
        mutationError instanceof Error ? mutationError.message : 'Failed to submit issue';
      setError(message);
      notify(message, 'error');
    },
    onSuccess: () => {
      notify('Issue reported successfully', 'success');
      onSubmitted();
    },
  });

  function selectCategory(category: string) {
    setSelectedCategory(category);
    setError('');
    if (!titleEdited) {
      setTitle(createDefaultIssueTitle(category, payload.entityName));
    }
  }

  return (
    <div
      className={styles.modalOverlay}
      role="dialog"
      aria-modal="true"
      aria-labelledby="report-issue-title"
      onClick={onClose}
    >
      <div
        className={`${styles.modal} ${styles.reportIssueModal}`}
        onClick={(event) => event.stopPropagation()}
      >
        <div className={styles.modalHeader}>
          <h3 className={styles.modalHeaderTitle} id="report-issue-title">
            Report Issue - {entityLabel}
          </h3>
          <button
            className={styles.modalClose}
            type="button"
            onClick={onClose}
            aria-label="Close report issue modal"
          >
            &times;
          </button>
        </div>

        <div className={styles.modalBody}>
          <div className={styles.reportIssueEntityInfo}>
            <div className={styles.reportIssueEntityName}>{payload.entityName}</div>
            {payload.artistName ? (
              <div className={styles.reportIssueEntityArtist}>
                {payload.artistName}
                {payload.albumTitle ? ` - ${payload.albumTitle}` : ''}
              </div>
            ) : null}
          </div>

          <div className={styles.issueDetailSection}>
            <label className={styles.issueDetailSectionTitle}>What's the problem?</label>
            <div className={styles.reportIssueCategoryGrid}>
              {categories.map(([category, meta]) => (
                <button
                  key={category}
                  className={`${styles.reportIssueCategoryCard} ${
                    selectedCategory === category ? styles.reportIssueCategoryCardSelected : ''
                  }`}
                  type="button"
                  onClick={() => selectCategory(category)}
                >
                  <div className={styles.reportIssueCategoryIcon}>{meta.icon}</div>
                  <div className={styles.reportIssueCategoryLabel}>{meta.label}</div>
                  <div className={styles.reportIssueCategoryDesc}>{meta.description}</div>
                </button>
              ))}
            </div>
          </div>

          {selectedCategory ? (
            <div className={styles.issueDetailSection}>
              <label className={styles.issueDetailSectionTitle} htmlFor="report-issue-title-input">
                Title
              </label>
              <input
                className={styles.reportIssueInput}
                id="report-issue-title-input"
                maxLength={200}
                onChange={(event) => {
                  setTitle(event.target.value);
                  setTitleEdited(true);
                }}
                placeholder="Brief summary of the issue..."
                value={title}
              />
              <label className={styles.issueDetailSectionTitle} htmlFor="report-issue-desc-input">
                Details
              </label>
              <textarea
                className={styles.issueDetailResponseTextarea}
                id="report-issue-desc-input"
                maxLength={2000}
                onChange={(event) => setDescription(event.target.value)}
                placeholder="Provide more details about what's wrong..."
                rows={4}
                value={description}
              />
              <div className={styles.reportIssuePriorityRow} aria-label="Priority">
                {(['low', 'normal', 'high'] as const).map((priority) => (
                  <button
                    key={priority}
                    className={`${styles.reportIssuePriorityButton} ${
                      selectedPriority === priority ? styles.reportIssuePriorityButtonSelected : ''
                    }`}
                    type="button"
                    onClick={() => setSelectedPriority(priority)}
                  >
                    {priority[0].toUpperCase()}
                    {priority.slice(1)}
                  </button>
                ))}
              </div>
            </div>
          ) : null}

          {error ? <div className={styles.reportIssueError}>{error}</div> : null}
        </div>

        <div className={styles.modalFooter}>
          <button
            className={`${styles.modalButton} ${styles.modalButtonSecondary}`}
            type="button"
            onClick={onClose}
          >
            Cancel
          </button>
          <button
            className={`${styles.modalButton} ${styles.modalButtonPrimary}`}
            type="button"
            disabled={!selectedCategory || createMutation.isPending}
            onClick={() => createMutation.mutate()}
          >
            {createMutation.isPending ? 'Submitting...' : 'Submit Issue'}
          </button>
        </div>
      </div>
    </div>
  );
}

function notify(message: string, type: 'success' | 'error' | 'warning' | 'info' = 'info') {
  window.showToast?.(message, type);
}

function updateBadge(openCount: number) {
  const badge = document.getElementById('issues-nav-badge');
  if (!badge) return;
  badge.textContent = String(openCount || 0);
  badge.classList.toggle('hidden', !openCount);
}
