import { useMutation } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';

import { Button } from '@/components/form';
import {
  launchAlbumDownloadWorkflow,
  launchAlbumWishlistWorkflow,
} from '@/platform/workflows/album-workflows';

import type { IssueRecord } from '../-issues.types';

import { deleteIssue, updateIssue } from '../-issues.api';
import {
  formatIssueDate,
  formatStatusLabel,
  getIssueArtwork,
  getPriorityClassName,
  ISSUE_CATEGORY_META,
  parseSnapshot,
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
  const modalRef = useRef<HTMLDivElement | null>(null);
  const previouslyFocusedElementRef = useRef<HTMLElement | null>(null);
  const isOpen = Boolean(issue || isLoading || error);

  useEffect(() => {
    setAdminResponse(issue?.admin_response || '');
  }, [issue?.admin_response, issue?.id]);

  useEffect(() => {
    if (!isOpen) {
      return;
    }

    previouslyFocusedElementRef.current =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;

    const focusModal = () => {
      const modal = modalRef.current;
      if (!modal) return;

      const focusable = getFocusableElements(modal);
      (focusable[0] || modal).focus();
    };

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        onClose();
        return;
      }

      if (event.key !== 'Tab') return;

      const modal = modalRef.current;
      if (!modal) return;

      const focusable = getFocusableElements(modal);
      if (focusable.length === 0) {
        event.preventDefault();
        modal.focus();
        return;
      }

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const activeElement = document.activeElement;

      if (event.shiftKey) {
        if (activeElement === first || !modal.contains(activeElement)) {
          event.preventDefault();
          last.focus();
        }
      } else if (activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    const onFocusIn = (event: FocusEvent) => {
      const modal = modalRef.current;
      if (!modal) return;
      if (event.target instanceof Node && !modal.contains(event.target)) {
        const focusable = getFocusableElements(modal);
        (focusable[0] || modal).focus();
      }
    };

    const raf = requestAnimationFrame(focusModal);
    document.addEventListener('keydown', handleKeyDown);
    document.addEventListener('focusin', onFocusIn);

    return () => {
      cancelAnimationFrame(raf);
      document.removeEventListener('keydown', handleKeyDown);
      document.removeEventListener('focusin', onFocusIn);
      previouslyFocusedElementRef.current?.focus?.();
      previouslyFocusedElementRef.current = null;
    };
  }, [isOpen, onClose]);

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
            updateMutation.mutate({
              issueId: issue.id,
              status: 'open',
              adminResponse,
            })
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
  const issueArtwork = getIssueArtwork(snapshot);
  const issueCategoryLabel = issue
    ? `${ISSUE_CATEGORY_META[issue.category]?.icon || ''} ${
        ISSUE_CATEGORY_META[issue.category]?.label || issue.category
      }`.trim()
    : '';
  const externalLinks = getExternalLinks(snapshot);
  const trackMetaItems = getTrackMetaItems(snapshot);
  const trackRows = Array.isArray(snapshot.tracks) ? snapshot.tracks : [];
  const priorityClassName = issue ? getPriorityClassName(issue.priority) : 'normal';
  const albumMetaParts = issue ? getAlbumMetaParts(issue, snapshot) : [];
  const genreTags = Array.isArray(snapshot.genres) ? snapshot.genres.slice(0, 5) : [];
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
        ref={modalRef}
        tabIndex={-1}
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
                    {String(
                      issue.entity_type === 'artist'
                        ? snapshot.name || issue.title
                        : snapshot.album_title || snapshot.title || issue.title,
                    )}
                  </div>
                  {issue.entity_type === 'track' ? (
                    <div className={styles.issueHeroTrackName}>♪ {issue.title}</div>
                  ) : null}
                  {issue.entity_type !== 'artist' && albumMetaParts.length > 0 ? (
                    <div className={styles.issueHeroMeta}>{albumMetaParts.join(' - ')}</div>
                  ) : null}
                  {genreTags.length > 0 ? (
                    <div className={styles.issueHeroGenres}>
                      {genreTags.map((genre) => (
                        <span className={styles.issueHeroGenreTag} key={String(genre)}>
                          {String(genre)}
                        </span>
                      ))}
                    </div>
                  ) : null}
                  {externalLinks.length > 0 ? (
                    <div className={styles.issueExternalLinks}>
                      {externalLinks.map((link) =>
                        link.url ? (
                          <a
                            key={`${link.service}-${link.type}-${link.label}`}
                            className={`${styles.issueExternalLink} ${styles[link.className]}`}
                            href={link.url}
                            target="_blank"
                            rel="noreferrer"
                            title={`${link.service} ${link.type}`}
                          >
                            <span className={styles.issueExternalLinkService}>{link.service}</span>
                            <span className={styles.issueExternalLinkType}>{link.type}</span>
                          </a>
                        ) : (
                          <span
                            key={`${link.service}-${link.type}-${link.label}`}
                            className={`${styles.issueExternalLink} ${styles[link.className]}`}
                            title={`${link.service} ${link.type}: ${link.id}`}
                          >
                            <span className={styles.issueExternalLinkService}>{link.service}</span>
                            <span className={styles.issueExternalLinkType}>{link.type}</span>
                          </span>
                        ),
                      )}
                    </div>
                  ) : null}
                </div>
              </div>

              <div className={styles.issueDetailInfoBar}>
                <div className={styles.issueDetailInfoLeft}>
                  <span
                    className={`${styles.issuePriorityDot} ${getPriorityDotClassName(priorityClassName)}`}
                  />
                  <span
                    className={`${styles.issueStatusBadge} ${getStatusClassName(issue.status)}`}
                  >
                    {formatStatusLabel(issue.status)}
                  </span>
                  <span className={styles.issueDetailCategory}>{issueCategoryLabel}</span>
                </div>
                <div className={styles.issueDetailInfoRight}>
                  <span className={styles.issueDetailDate}>
                    Reported {formatIssueDate(issue.created_at)}
                  </span>
                  {issue.resolved_at ? (
                    <span className={styles.issueDetailDate}>
                      Resolved {formatIssueDate(issue.resolved_at)}
                    </span>
                  ) : null}
                  {issue.reporter_name && isAdmin ? (
                    <span className={styles.issueDetailProfile}>by {issue.reporter_name}</span>
                  ) : null}
                </div>
              </div>

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

              <div className={styles.issueDetailSection}>
                <div className={styles.issueDetailSectionTitle}>Issue</div>
                <div className={styles.issueDetailTitleText}>{issue.title}</div>
                <div
                  className={
                    issue.description ? styles.issueDetailDescription : styles.issueDetailNoDesc
                  }
                >
                  {issue.description || 'No additional details provided'}
                </div>
              </div>

              {issue.entity_type === 'track' && trackMetaItems.length > 0 ? (
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
                    Track Listing{' '}
                    <span className={styles.issueDetailSectionCount}>
                      {trackRows.length} tracks
                    </span>
                  </div>
                  <div className={styles.issueDetailTracklist}>{renderTrackListing(trackRows)}</div>
                </div>
              ) : null}

              {isAdmin && (
                <div className={styles.issueDetailSection}>
                  <div className={styles.issueDetailSectionTitle}>Admin Response</div>
                  <textarea
                    className={styles.issueDetailResponseTextarea}
                    id="issue-detail-response-input"
                    value={adminResponse}
                    onChange={(event) => setAdminResponse(event.target.value)}
                    placeholder="Write a response to the reporter..."
                    rows={3}
                  />
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

function getFocusableElements(container: HTMLElement) {
  return Array.from(
    container.querySelectorAll<HTMLElement>(
      [
        'button:not([disabled])',
        '[href]',
        'input:not([disabled])',
        'select:not([disabled])',
        'textarea:not([disabled])',
        '[tabindex]:not([tabindex="-1"])',
      ].join(','),
    ),
  ).filter((element) => element.tabIndex >= 0);
}

function renderTrackListing(trackRows: Array<Record<string, unknown>>) {
  const nodes: ReactNode[] = [];
  let lastDisc: number | null = null;
  const hasMultiDisc = trackRows.some((track) => Number(track.disc_number || 1) > 1);

  trackRows.forEach((track, index) => {
    const disc = Number(track.disc_number || 1);
    if (hasMultiDisc && disc !== lastDisc) {
      nodes.push(
        <div className={styles.issueDetailTracklistDisc} key={`disc-${disc}-${index}`}>
          Disc {disc}
        </div>,
      );
      lastDisc = disc;
    }

    const format = String(track.format || '').toUpperCase();
    const bitrateValue = typeof track.bitrate === 'number' ? track.bitrate : Number(track.bitrate);
    const bitrate = Number.isFinite(bitrateValue) && bitrateValue > 0 ? `${bitrateValue}k` : '';
    const duration = formatDuration(track.duration);
    const formatClassName = getTrackFormatClassName(format);
    const bitrateClassName = getTrackBitrateClassName(bitrateValue, format);
    nodes.push(
      <div
        className={styles.issueDetailTracklistRow}
        key={String(track.id || `${track.title}-${index}`)}
      >
        <span className={styles.issueDetailTracklistNum}>{String(track.track_number || '-')}</span>
        <span className={styles.issueDetailTracklistTitle}>{String(track.title || 'Unknown')}</span>
        <span className={styles.issueDetailTracklistDur}>{duration}</span>
        <span className={styles.issueDetailTracklistMeta}>
          {format ? (
            <span className={`${styles.issueTrackBadge} ${formatClassName}`}>{format}</span>
          ) : null}
          {bitrate ? (
            <span className={`${styles.issueTrackBadge} ${bitrateClassName}`}>{bitrate}</span>
          ) : null}
        </span>
      </div>,
    );
  });

  return nodes;
}

function getPriorityDotClassName(priority: string) {
  if (priority === 'high') return styles.issuePriorityHigh;
  if (priority === 'low') return styles.issuePriorityLow;
  return styles.issuePriorityNormal;
}

function getTrackFormatClassName(format: string) {
  const lower = format.toLowerCase();
  if (lower === 'flac') return styles.issueTrackBadgeFlac;
  if (lower === 'mp3') return styles.issueTrackBadgeMp3;
  return styles.issueTrackBadgeOther;
}

function getTrackBitrateClassName(bitrate: number, format: string) {
  const lower = format.toLowerCase();
  if (!Number.isFinite(bitrate) || bitrate <= 0) return styles.issueTrackBadgeOther;
  if (bitrate >= 320 || lower === 'flac') return styles.issueTrackBadgeHigh;
  if (bitrate >= 192) return styles.issueTrackBadgeMedium;
  return styles.issueTrackBadgeLow;
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
  const links: Array<{
    className:
      | 'issueExternalLinkSpotify'
      | 'issueExternalLinkMusicBrainz'
      | 'issueExternalLinkDeezer'
      | 'issueExternalLinkTidal'
      | 'issueExternalLinkQobuz';
    id?: string | number;
    label: string;
    service: string;
    type: string;
    url?: string;
  }> = [];
  if (snapshot.spotify_artist_id) {
    links.push({
      className: 'issueExternalLinkSpotify',
      label: 'Spotify Artist',
      service: 'Spotify',
      type: 'Artist',
      url: `https://open.spotify.com/artist/${snapshot.spotify_artist_id}`,
    });
  }
  if (snapshot.spotify_album_id) {
    links.push({
      className: 'issueExternalLinkSpotify',
      label: 'Spotify Album',
      service: 'Spotify',
      type: 'Album',
      url: `https://open.spotify.com/album/${snapshot.spotify_album_id}`,
    });
  }
  if (snapshot.spotify_track_id) {
    links.push({
      className: 'issueExternalLinkSpotify',
      label: 'Spotify Track',
      service: 'Spotify',
      type: 'Track',
      url: `https://open.spotify.com/track/${snapshot.spotify_track_id}`,
    });
  }
  if (snapshot.artist_musicbrainz_id) {
    links.push({
      className: 'issueExternalLinkMusicBrainz',
      label: 'MusicBrainz Artist',
      service: 'MusicBrainz',
      type: 'Artist',
      url: `https://musicbrainz.org/artist/${snapshot.artist_musicbrainz_id}`,
    });
  }
  if (snapshot.musicbrainz_release_id) {
    links.push({
      className: 'issueExternalLinkMusicBrainz',
      label: 'MusicBrainz Release',
      service: 'MusicBrainz',
      type: 'Release',
      url: `https://musicbrainz.org/release/${snapshot.musicbrainz_release_id}`,
    });
  }
  if (snapshot.musicbrainz_recording_id) {
    links.push({
      className: 'issueExternalLinkMusicBrainz',
      label: 'MusicBrainz Recording',
      service: 'MusicBrainz',
      type: 'Recording',
      url: `https://musicbrainz.org/recording/${snapshot.musicbrainz_recording_id}`,
    });
  }
  if (snapshot.artist_deezer_id) {
    links.push({
      className: 'issueExternalLinkDeezer',
      label: 'Deezer Artist',
      service: 'Deezer',
      type: 'Artist',
      url: `https://www.deezer.com/artist/${snapshot.artist_deezer_id}`,
    });
  }
  if (snapshot.album_deezer_id) {
    links.push({
      className: 'issueExternalLinkDeezer',
      label: 'Deezer Album',
      service: 'Deezer',
      type: 'Album',
      url: `https://www.deezer.com/album/${snapshot.album_deezer_id}`,
    });
  }
  if (snapshot.track_deezer_id) {
    links.push({
      className: 'issueExternalLinkDeezer',
      label: 'Deezer Track',
      service: 'Deezer',
      type: 'Track',
      url: `https://www.deezer.com/track/${snapshot.track_deezer_id}`,
    });
  }
  if (snapshot.artist_tidal_id) {
    links.push({
      className: 'issueExternalLinkTidal',
      label: 'Tidal Artist',
      service: 'Tidal',
      type: 'Artist',
      url: `https://listen.tidal.com/artist/${snapshot.artist_tidal_id}`,
    });
  }
  if (snapshot.album_tidal_id) {
    links.push({
      className: 'issueExternalLinkTidal',
      label: 'Tidal Album',
      service: 'Tidal',
      type: 'Album',
      url: `https://listen.tidal.com/album/${snapshot.album_tidal_id}`,
    });
  }
  if (snapshot.artist_qobuz_id) {
    links.push({
      className: 'issueExternalLinkQobuz',
      id: snapshot.artist_qobuz_id,
      label: 'Qobuz Artist',
      service: 'Qobuz',
      type: 'Artist',
    });
  }
  if (snapshot.album_qobuz_id) {
    links.push({
      className: 'issueExternalLinkQobuz',
      id: snapshot.album_qobuz_id,
      label: 'Qobuz Album',
      service: 'Qobuz',
      type: 'Album',
    });
  }
  return links;
}

function getAlbumMetaParts(
  issue: IssueRecord,
  snapshot: ReturnType<typeof parseSnapshot>,
): string[] {
  if (issue.entity_type === 'artist') return [];

  const parts: string[] = [];
  if (snapshot.year) parts.push(String(snapshot.year));
  if (snapshot.record_type) {
    const recordType = String(snapshot.record_type);
    parts.push(recordType.charAt(0).toUpperCase() + recordType.slice(1));
  }

  const trackCount =
    issue.entity_type === 'album' ? snapshot.track_count : snapshot.album_track_count;
  if (trackCount) parts.push(`${trackCount} tracks`);
  if (snapshot.label) parts.push(String(snapshot.label));

  return parts;
}

function getTrackMetaItems(snapshot: ReturnType<typeof parseSnapshot>) {
  const items: Array<{ icon: string; label: string; value: string }> = [];
  if (snapshot.track_number) {
    items.push({
      icon: '#',
      label: 'Track',
      value: String(snapshot.track_number),
    });
  }
  const duration = formatDuration(snapshot.duration);
  if (duration) items.push({ icon: 'T', label: 'Duration', value: duration });
  if (snapshot.format) items.push({ icon: 'F', label: 'Format', value: String(snapshot.format) });
  if (snapshot.bitrate)
    items.push({
      icon: 'B',
      label: 'Bitrate',
      value: `${snapshot.bitrate} kbps`,
    });
  if (snapshot.bpm) items.push({ icon: 'M', label: 'BPM', value: String(snapshot.bpm) });
  if (snapshot.quality)
    items.push({
      icon: 'Q',
      label: 'Quality',
      value: String(snapshot.quality),
    });
  return items;
}
