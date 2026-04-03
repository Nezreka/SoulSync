import { useMutation } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';

import type { IssueRecord } from '../-issues.types';

import {
  deleteIssue,
  formatIssueDate,
  formatStatusLabel,
  getEntityDetails,
  getEntityLabel,
  getIssueArtwork,
  ISSUE_CATEGORY_META,
  parseSnapshot,
  updateIssue,
} from '../-issues.helpers';
import styles from './issue-detail-modal.module.css';

function getStatusClassName(status: string) {
  if (status === 'in_progress') return styles.issueStatusProgress;
  if (status === 'resolved') return styles.issueStatusResolved;
  if (status === 'dismissed') return styles.issueStatusDismissed;
  return styles.issueStatusOpen;
}

export function IssueDetailModal({
  error,
  isAdmin,
  isLoading,
  issue,
  onClose,
  onMutationSuccess,
  profileId,
}: {
  error: unknown;
  isAdmin: boolean;
  isLoading: boolean;
  issue: IssueRecord | null;
  onClose: () => void;
  onMutationSuccess: () => void;
  profileId: number;
}) {
  const [adminResponse, setAdminResponse] = useState('');

  useEffect(() => {
    setAdminResponse(issue?.admin_response || '');
  }, [issue?.admin_response, issue?.id]);

  const updateMutation = useMutation({
    mutationFn: async (payload: { issueId: number; status: string; adminResponse: string }) => {
      await updateIssue(profileId, payload.issueId, {
        status: payload.status,
        admin_response: payload.adminResponse,
      });
    },
    onSuccess: async () => {
      onMutationSuccess();
    },
  });

  const deleteMutation = useMutation({
    mutationFn: async (issueId: number) => {
      await deleteIssue(profileId, issueId);
    },
    onSuccess: async () => {
      onMutationSuccess();
    },
  });

  const statusButtons = useMemo(() => {
    if (!issue) return null;

    if (isAdmin) {
      if (issue.status === 'open' || issue.status === 'in_progress') {
        return (
          <>
            {issue.status === 'open' && (
              <button
                className={`${styles.modalButton} ${styles.modalButtonProgress}`}
                type="button"
                onClick={() =>
                  updateMutation.mutate({
                    issueId: issue.id,
                    status: 'in_progress',
                    adminResponse,
                  })
                }
                disabled={updateMutation.isPending}
              >
                Mark In Progress
              </button>
            )}
            <button
              className={`${styles.modalButton} ${styles.modalButtonResolve}`}
              type="button"
              onClick={() =>
                updateMutation.mutate({
                  issueId: issue.id,
                  status: 'resolved',
                  adminResponse,
                })
              }
              disabled={updateMutation.isPending}
            >
              Resolve
            </button>
            <button
              className={`${styles.modalButton} ${styles.modalButtonDismiss}`}
              type="button"
              onClick={() =>
                updateMutation.mutate({
                  issueId: issue.id,
                  status: 'dismissed',
                  adminResponse,
                })
              }
              disabled={updateMutation.isPending}
            >
              Dismiss
            </button>
          </>
        );
      }

      return (
        <button
          className={`${styles.modalButton} ${styles.modalButtonReopen}`}
          type="button"
          onClick={() =>
            updateMutation.mutate({ issueId: issue.id, status: 'open', adminResponse })
          }
          disabled={updateMutation.isPending}
        >
          Reopen
        </button>
      );
    }

    if (issue.status === 'open') {
      return (
        <button
          className={`${styles.modalButton} ${styles.modalButtonDelete}`}
          type="button"
          onClick={() => {
            if (window.confirm('Withdraw this issue?')) {
              deleteMutation.mutate(issue.id);
            }
          }}
          disabled={deleteMutation.isPending}
        >
          Withdraw
        </button>
      );
    }

    return null;
  }, [adminResponse, deleteMutation, isAdmin, issue, updateMutation]);

  if (!issue && !isLoading && !error) {
    return null;
  }

  const snapshot = issue ? parseSnapshot(issue.snapshot_data) : {};
  const issueDetails = issue ? getEntityDetails(issue, snapshot) : [];
  const issueArtwork = getIssueArtwork(snapshot);
  const issueCategoryLabel = issue
    ? ISSUE_CATEGORY_META[issue.category]?.label || issue.category
    : '';

  const downloadAlbum = window.SoulSyncIssueActions?.downloadAlbum;
  const addToWishlist = window.SoulSyncIssueActions?.addToWishlist;

  return (
    <div
      className={styles.modalOverlay}
      role="dialog"
      aria-modal="true"
      aria-labelledby="issue-detail-title"
      onClick={onClose}
    >
      <div
        className={`${styles.modal} ${styles.issueDetailModal}`}
        onClick={(event) => event.stopPropagation()}
      >
        <div className={styles.modalHeader}>
          <h3 className={styles.modalHeaderTitle} id="issue-detail-title">
            {issue ? `Issue #${issue.id}` : 'Issue details'}
          </h3>
          <button
            className={styles.modalClose}
            type="button"
            onClick={onClose}
            aria-label="Close issue detail"
          >
            &times;
          </button>
        </div>

        <div className={styles.modalBody}>
          {isLoading ? (
            <div className={styles.issuesLoading}>
              <div className={styles.issuesSpinner} />
              Loading issue details...
            </div>
          ) : error ? (
            <div className={styles.issuesEmpty}>
              <div className={styles.issuesEmptyTitle}>Failed to load issue</div>
              <div className={styles.issuesEmptyText}>
                {error instanceof Error ? error.message : 'Unknown error'}
              </div>
            </div>
          ) : issue ? (
            <>
              <div className={styles.issueHero}>
                <div className={styles.issueHeroArtGroup}>
                  {issue.entity_type === 'artist' && issueArtwork ? (
                    <img className={styles.issueHeroArtistThumb} src={issueArtwork} alt="" />
                  ) : null}
                  {issueArtwork ? (
                    <img className={styles.issueHeroAlbumArt} src={issueArtwork} alt="" />
                  ) : (
                    <div className={styles.issueHeroAlbumPlaceholder}>
                      {ISSUE_CATEGORY_META[issue.category]?.icon || 'OT'}
                    </div>
                  )}
                </div>
                <div className={styles.issueHeroInfo}>
                  {issue.entity_type !== 'artist' && snapshot.artist_name ? (
                    <div className={styles.issueHeroArtist}>{String(snapshot.artist_name)}</div>
                  ) : null}
                  <div className={styles.issueHeroAlbum}>
                    {String(snapshot.album_title || snapshot.title || issue.title)}
                  </div>
                  {issue.entity_type === 'track' ? (
                    <div className={styles.issueHeroTrackName}>♪ {issue.title}</div>
                  ) : null}
                  {issue.entity_type === 'artist' && snapshot.name ? (
                    <div className={styles.issueHeroTrackName}>{String(snapshot.name)}</div>
                  ) : null}
                  {issueDetails.length > 0 ? (
                    <div className={styles.issueHeroMeta}>{issueDetails.join(' · ')}</div>
                  ) : null}
                </div>
              </div>

              <div className={styles.issueDetailInfoBar}>
                <div className={styles.issueDetailInfoLeft}>
                  <span
                    className={`${styles.issueStatusBadge} ${getStatusClassName(issue.status)}`}
                  >
                    {formatStatusLabel(issue.status)}
                  </span>
                  <span className={styles.issueDetailCategory}>{issueCategoryLabel}</span>
                </div>
                <div className={styles.issueDetailInfoRight}>
                  <span className={styles.issueDetailDate}>
                    Created {formatIssueDate(issue.created_at)}
                  </span>
                  {issue.reporter_name ? (
                    <span className={styles.issueDetailProfile}>by {issue.reporter_name}</span>
                  ) : null}
                </div>
              </div>

              <div className={styles.issueDetailSection}>
                <div className={styles.issueDetailSectionTitle}>Issue Details</div>
                <div className={styles.issueDetailTitleText}>{issue.title}</div>
                <div
                  className={
                    issue.description ? styles.issueDetailDescription : styles.issueDetailNoDesc
                  }
                >
                  {issue.description || 'No additional details provided'}
                </div>
              </div>

              <div className={styles.issueDetailSection}>
                <div className={styles.issueDetailSectionTitle}>Context</div>
                <div className={styles.issueDetailMetaGrid}>
                  <div className={styles.issueMetaItem}>
                    <span className={styles.issueMetaIcon}>•</span>
                    <span className={styles.issueMetaLabel}>Entity</span>
                    <span className={styles.issueMetaValue}>
                      {getEntityLabel(issue.entity_type)}
                    </span>
                  </div>
                  <div className={styles.issueMetaItem}>
                    <span className={styles.issueMetaIcon}>#</span>
                    <span className={styles.issueMetaLabel}>Entity ID</span>
                    <span className={styles.issueMetaValue}>{issue.entity_id}</span>
                  </div>
                  <div className={styles.issueMetaItem}>
                    <span className={styles.issueMetaIcon}>⊘</span>
                    <span className={styles.issueMetaLabel}>Category</span>
                    <span className={styles.issueMetaValue}>
                      {ISSUE_CATEGORY_META[issue.category]?.label || issue.category}
                    </span>
                  </div>
                  <div className={styles.issueMetaItem}>
                    <span className={styles.issueMetaIcon}>!</span>
                    <span className={styles.issueMetaLabel}>Priority</span>
                    <span className={styles.issueMetaValue}>{issue.priority}</span>
                  </div>
                  <div className={styles.issueMetaItem}>
                    <span className={styles.issueMetaIcon}>✓</span>
                    <span className={styles.issueMetaLabel}>Status</span>
                    <span className={styles.issueMetaValue}>{formatStatusLabel(issue.status)}</span>
                  </div>
                  <div className={styles.issueMetaItem}>
                    <span className={styles.issueMetaIcon}>⏱</span>
                    <span className={styles.issueMetaLabel}>Created</span>
                    <span className={styles.issueMetaValue}>
                      {formatIssueDate(issue.created_at)}
                    </span>
                  </div>
                </div>
              </div>

              {issue.entity_type !== 'artist' && isAdmin && (
                <div className={styles.issueDetailSection}>
                  <div className={styles.issueDetailSectionTitle}>Admin Actions</div>
                  <div className={styles.issueActionButtons}>
                    {downloadAlbum && (
                      <button
                        className={`${styles.issueActionButton} ${styles.issueActionDownload}`}
                        type="button"
                        onClick={() =>
                          downloadAlbum(
                            String(snapshot.spotify_album_id || ''),
                            String(snapshot.artist_name || ''),
                            String(snapshot.album_title || snapshot.title || ''),
                          )
                        }
                      >
                        Download Album
                      </button>
                    )}
                    {addToWishlist && (
                      <button
                        className={`${styles.issueActionButton} ${styles.issueActionWishlist}`}
                        type="button"
                        onClick={() =>
                          addToWishlist(
                            String(snapshot.spotify_album_id || ''),
                            String(snapshot.artist_name || ''),
                            String(snapshot.album_title || snapshot.title || ''),
                          )
                        }
                      >
                        Add to Wishlist
                      </button>
                    )}
                  </div>
                </div>
              )}

              {isAdmin && (
                <div className={styles.issueDetailSection}>
                  <div className={styles.issueDetailSectionTitle}>Admin Response</div>
                  <textarea
                    className={styles.issueDetailResponseTextarea}
                    value={adminResponse}
                    onChange={(event) => setAdminResponse(event.target.value)}
                    placeholder="Write a response to the reporter..."
                  />
                </div>
              )}
            </>
          ) : null}
        </div>

        <div className={styles.modalFooter}>
          <button
            className={`${styles.modalButton} ${styles.modalButtonSecondary}`}
            type="button"
            onClick={onClose}
          >
            Close
          </button>
          {!isLoading && !error && issue && (
            <>
              {statusButtons}
              {isAdmin && (
                <button
                  className={`${styles.modalButton} ${styles.modalButtonDelete}`}
                  type="button"
                  onClick={() => {
                    if (window.confirm('Delete this issue?')) {
                      deleteMutation.mutate(issue.id);
                    }
                  }}
                  disabled={deleteMutation.isPending}
                >
                  Delete
                </button>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
