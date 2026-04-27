import { useMutation } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';

import { Button, FormField, TextArea } from '@/components/form';
import {
  launchAlbumDownloadWorkflow,
  launchAlbumWishlistWorkflow,
} from '@/platform/workflows/album-workflows';

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

  const downloadWorkflowMutation = useMutation({
    mutationFn: launchAlbumDownloadWorkflow,
    onError: notifyWorkflowError,
    onSuccess: onClose,
  });

  const wishlistWorkflowMutation = useMutation({
    mutationFn: launchAlbumWishlistWorkflow,
    onError: notifyWorkflowError,
    onSuccess: onClose,
  });

  const statusButtons = useMemo(() => {
    if (!issue) return null;

    if (isAdmin) {
      if (issue.status === 'open' || issue.status === 'in_progress') {
        return (
          <>
            {issue.status === 'open' && (
              <Button
                className={styles.modalButtonProgress}
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
              </Button>
            )}
            <Button
              className={styles.modalButtonResolve}
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
            </Button>
            <Button
              className={styles.modalButtonDismiss}
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
            </Button>
          </>
        );
      }

      return (
        <Button
          className={styles.modalButtonReopen}
          type="button"
          onClick={() =>
            updateMutation.mutate({ issueId: issue.id, status: 'open', adminResponse })
          }
          disabled={updateMutation.isPending}
        >
          Reopen
        </Button>
      );
    }

    if (issue.status === 'open') {
      return (
        <Button
          className={styles.modalButtonDelete}
          type="button"
          onClick={() => {
            if (window.confirm('Withdraw this issue?')) {
              deleteMutation.mutate(issue.id);
            }
          }}
          disabled={deleteMutation.isPending}
        >
          Withdraw
        </Button>
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
  const externalLinks = getExternalLinks(snapshot);
  const trackMetaItems = getTrackMetaItems(snapshot);
  const trackRows = Array.isArray(snapshot.tracks) ? snapshot.tracks : [];
  const albumWorkflowInput = {
    spotifyAlbumId: String(snapshot.spotify_album_id || ''),
    artistName: String(snapshot.artist_name || ''),
    albumName: String(snapshot.album_title || snapshot.title || ''),
    source: 'issue',
  };

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
                  {issue.updated_at ? (
                    <div className={styles.issueMetaItem}>
                      <span className={styles.issueMetaIcon}>U</span>
                      <span className={styles.issueMetaLabel}>Updated</span>
                      <span className={styles.issueMetaValue}>
                        {formatIssueDate(issue.updated_at)}
                      </span>
                    </div>
                  ) : null}
                  {issue.resolved_at ? (
                    <div className={styles.issueMetaItem}>
                      <span className={styles.issueMetaIcon}>R</span>
                      <span className={styles.issueMetaLabel}>Resolved</span>
                      <span className={styles.issueMetaValue}>
                        {formatIssueDate(issue.resolved_at)}
                      </span>
                    </div>
                  ) : null}
                  {issue.resolved_by ? (
                    <div className={styles.issueMetaItem}>
                      <span className={styles.issueMetaIcon}>A</span>
                      <span className={styles.issueMetaLabel}>Resolver</span>
                      <span className={styles.issueMetaValue}>{issue.resolved_by}</span>
                    </div>
                  ) : null}
                  {issue.reporter_name ? (
                    <div className={styles.issueMetaItem}>
                      <span className={styles.issueMetaIcon}>P</span>
                      <span className={styles.issueMetaLabel}>Reporter</span>
                      <span className={styles.issueMetaValue}>{issue.reporter_name}</span>
                    </div>
                  ) : null}
                </div>
              </div>

              {externalLinks.length > 0 ? (
                <div className={styles.issueDetailSection}>
                  <div className={styles.issueDetailSectionTitle}>External Links</div>
                  <div className={styles.issueExternalLinks}>
                    {externalLinks.map((link) => (
                      <a
                        key={link.url}
                        className={styles.issueExternalLink}
                        href={link.url}
                        target="_blank"
                        rel="noreferrer"
                      >
                        {link.label}
                      </a>
                    ))}
                  </div>
                </div>
              ) : null}

              {trackMetaItems.length > 0 ? (
                <div className={styles.issueDetailSection}>
                  <div className={styles.issueDetailSectionTitle}>Track Details</div>
                  <div className={styles.issueDetailMetaGrid}>
                    {trackMetaItems.map((item) => (
                      <div className={styles.issueMetaItem} key={item.label}>
                        <span className={styles.issueMetaIcon}>{item.icon}</span>
                        <span className={styles.issueMetaLabel}>{item.label}</span>
                        <span className={styles.issueMetaValue}>{item.value}</span>
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}

              {snapshot.file_path ? (
                <div className={styles.issueDetailSection}>
                  <div className={styles.issueDetailSectionTitle}>File Path</div>
                  <div className={styles.issueDetailFilepath}>{String(snapshot.file_path)}</div>
                </div>
              ) : null}

              {trackRows.length > 0 ? (
                <div className={styles.issueDetailSection}>
                  <div className={styles.issueDetailSectionTitle}>
                    Track Listing ({trackRows.length} tracks)
                  </div>
                  <div className={styles.issueDetailTracklist}>
                    {trackRows.map((track, index) => {
                      const format = String(track.format || '').toUpperCase();
                      const bitrate = track.bitrate ? `${track.bitrate}k` : '';
                      const duration = formatDuration(track.duration);
                      return (
                        <div
                          className={styles.issueDetailTracklistRow}
                          key={String(track.id || `${track.title}-${index}`)}
                        >
                          <span className={styles.issueDetailTracklistNum}>
                            {String(track.track_number || index + 1)}
                          </span>
                          <span className={styles.issueDetailTracklistTitle}>
                            {String(track.title || 'Unknown')}
                          </span>
                          <span className={styles.issueDetailTracklistDur}>{duration}</span>
                          <span className={styles.issueDetailTracklistMeta}>
                            {format ? (
                              <span className={styles.issueTrackBadge}>{format}</span>
                            ) : null}
                            {bitrate ? (
                              <span className={styles.issueTrackBadge}>{bitrate}</span>
                            ) : null}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              ) : null}

              {issue.entity_type !== 'artist' && isAdmin && (
                <div className={styles.issueDetailSection}>
                  <div className={styles.issueDetailSectionTitle}>Admin Actions</div>
                  <div className={styles.issueActionButtons}>
                    <Button
                      className={styles.issueActionDownload}
                      type="button"
                      disabled={downloadWorkflowMutation.isPending}
                      onClick={() => downloadWorkflowMutation.mutate(albumWorkflowInput)}
                    >
                      {downloadWorkflowMutation.isPending ? 'Loading...' : 'Download Album'}
                    </Button>
                    <Button
                      className={styles.issueActionWishlist}
                      type="button"
                      disabled={wishlistWorkflowMutation.isPending}
                      onClick={() => wishlistWorkflowMutation.mutate(albumWorkflowInput)}
                    >
                      {wishlistWorkflowMutation.isPending ? 'Loading...' : 'Add to Wishlist'}
                    </Button>
                  </div>
                </div>
              )}

              {isAdmin && (
                <div className={styles.issueDetailSection}>
                  <FormField
                    label="Admin Response"
                    htmlFor="issue-detail-response-input"
                    helperText="Write a response to the reporter."
                  >
                    <TextArea
                      className={styles.issueDetailResponseTextarea}
                      id="issue-detail-response-input"
                      value={adminResponse}
                      onChange={(event) => setAdminResponse(event.target.value)}
                      placeholder="Write a response to the reporter..."
                    />
                  </FormField>
                </div>
              )}

              {!isAdmin && issue.admin_response ? (
                <div className={styles.issueDetailSection}>
                  <div className={styles.issueDetailSectionTitle}>Admin Response</div>
                  <div className={styles.issueDetailAdminResponse}>{issue.admin_response}</div>
                </div>
              ) : null}
            </>
          ) : null}
        </div>

        <div className={styles.modalFooter}>
          <Button className={styles.modalButtonSecondary} type="button" onClick={onClose}>
            Close
          </Button>
          {!isLoading && !error && issue && (
            <>
              {statusButtons}
              {isAdmin && (
                <Button
                  className={styles.modalButtonDelete}
                  type="button"
                  onClick={() => {
                    if (window.confirm('Delete this issue?')) {
                      deleteMutation.mutate(issue.id);
                    }
                  }}
                  disabled={deleteMutation.isPending}
                >
                  Delete
                </Button>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function getStatusClassName(status: string) {
  if (status === 'in_progress') return styles.issueStatusProgress;
  if (status === 'resolved') return styles.issueStatusResolved;
  if (status === 'dismissed') return styles.issueStatusDismissed;
  return styles.issueStatusOpen;
}

function notifyWorkflowError(error: unknown) {
  const message = error instanceof Error ? error.message : 'Workflow failed';
  window.showToast?.(message, 'error');
}

function formatDuration(value: unknown): string {
  const duration = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(duration) || duration <= 0) return '';
  const seconds = duration > 10000 ? Math.floor(duration / 1000) : Math.floor(duration);
  const minutes = Math.floor(seconds / 60);
  const remaining = seconds % 60;
  return `${minutes}:${String(remaining).padStart(2, '0')}`;
}

function getExternalLinks(snapshot: ReturnType<typeof parseSnapshot>) {
  const links: Array<{ label: string; url: string }> = [];
  if (snapshot.spotify_artist_id) {
    links.push({
      label: 'Spotify Artist',
      url: `https://open.spotify.com/artist/${snapshot.spotify_artist_id}`,
    });
  }
  if (snapshot.spotify_album_id) {
    links.push({
      label: 'Spotify Album',
      url: `https://open.spotify.com/album/${snapshot.spotify_album_id}`,
    });
  }
  if (snapshot.spotify_track_id) {
    links.push({
      label: 'Spotify Track',
      url: `https://open.spotify.com/track/${snapshot.spotify_track_id}`,
    });
  }
  if (snapshot.artist_musicbrainz_id) {
    links.push({
      label: 'MusicBrainz Artist',
      url: `https://musicbrainz.org/artist/${snapshot.artist_musicbrainz_id}`,
    });
  }
  if (snapshot.musicbrainz_release_id) {
    links.push({
      label: 'MusicBrainz Release',
      url: `https://musicbrainz.org/release/${snapshot.musicbrainz_release_id}`,
    });
  }
  if (snapshot.musicbrainz_recording_id) {
    links.push({
      label: 'MusicBrainz Recording',
      url: `https://musicbrainz.org/recording/${snapshot.musicbrainz_recording_id}`,
    });
  }
  if (snapshot.artist_deezer_id) {
    links.push({
      label: 'Deezer Artist',
      url: `https://www.deezer.com/artist/${snapshot.artist_deezer_id}`,
    });
  }
  if (snapshot.album_deezer_id) {
    links.push({
      label: 'Deezer Album',
      url: `https://www.deezer.com/album/${snapshot.album_deezer_id}`,
    });
  }
  if (snapshot.track_deezer_id) {
    links.push({
      label: 'Deezer Track',
      url: `https://www.deezer.com/track/${snapshot.track_deezer_id}`,
    });
  }
  if (snapshot.artist_tidal_id) {
    links.push({
      label: 'Tidal Artist',
      url: `https://listen.tidal.com/artist/${snapshot.artist_tidal_id}`,
    });
  }
  if (snapshot.album_tidal_id) {
    links.push({
      label: 'Tidal Album',
      url: `https://listen.tidal.com/album/${snapshot.album_tidal_id}`,
    });
  }
  return links;
}

function getTrackMetaItems(snapshot: ReturnType<typeof parseSnapshot>) {
  const items: Array<{ icon: string; label: string; value: string }> = [];
  if (snapshot.track_number) {
    items.push({ icon: '#', label: 'Track', value: String(snapshot.track_number) });
  }
  const duration = formatDuration(snapshot.duration);
  if (duration) items.push({ icon: 'T', label: 'Duration', value: duration });
  if (snapshot.format) items.push({ icon: 'F', label: 'Format', value: String(snapshot.format) });
  if (snapshot.bitrate)
    items.push({ icon: 'B', label: 'Bitrate', value: `${snapshot.bitrate} kbps` });
  if (snapshot.bpm) items.push({ icon: 'M', label: 'BPM', value: String(snapshot.bpm) });
  if (snapshot.quality)
    items.push({ icon: 'Q', label: 'Quality', value: String(snapshot.quality) });
  return items;
}
