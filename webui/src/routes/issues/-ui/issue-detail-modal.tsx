import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { useEffect, useRef, useState, type ReactNode } from 'react';

import { DialogBody, DialogFooter, DialogFrame, DialogHeader } from '@/components/dialog';
import { Button } from '@/components/form';
import { Show } from '@/components/primitives';
import { useProfile } from '@/platform/shell/route-controllers';
import {
  launchAlbumDownloadWorkflow,
  launchAlbumWishlistWorkflow,
} from '@/platform/workflows/album-workflows';

import type {
  IssueComment,
  IssuePriority,
  IssueRecord,
  IssueSnapshot,
  IssueStatus,
  IssueTrackRow,
  IssueUpdatePayload,
} from '../-issues.types';
import type { EnhancedAlbum, EnhancedTrack } from '../../artist-detail/-artist-detail.enhanced';
import type { ArtPickerTarget } from '../../artist-detail/-artist-detail.manage-actions';

import {
  addIssueComment,
  deleteIssue,
  invalidateIssuesQueries,
  issueDetailQueryOptions,
  updateIssue,
} from '../-issues.api';
import {
  formatFollowers,
  formatIssueAgo,
  formatIssueDate,
  formatStatusLabel,
  getEntityLabel,
  getIssueArtistLink,
  getIssueArtwork,
  getIssueCategoryMeta,
  getPriorityClassName,
  ISSUE_CATEGORY_META,
  parseSnapshot,
} from '../-issues.helpers';
import { ISSUE_PRIORITY_VALUES } from '../-issues.types';
import { ArtPicker } from '../../artist-detail/-ui/art-picker';
import { BodyPortal } from '../../artist-detail/-ui/portal';
import { RedownloadModal } from '../../artist-detail/-ui/redownload-modal';
import { ReidentifyModal } from '../../artist-detail/-ui/reidentify-modal';
import styles from './issue-detail-modal.module.css';

/** a fix tool opened from the issue; the issue dialog steps aside while it's up */
type OpenTool =
  | { kind: 'reidentify' }
  | { kind: 'art'; target: ArtPickerTarget; subtitle: string }
  | { kind: 'redownload'; track: EnhancedTrack; album: EnhancedAlbum };

/** where the reported item stands in the library right now */
interface ItemPresence {
  checked: boolean;
  removed: boolean;
  album?: EnhancedAlbum;
  track?: EnhancedTrack;
}

const UNCHECKED: ItemPresence = { checked: false, removed: false };

export function IssueDetailModal({
  issueId,
  onClose,
  onMutationSuccess,
}: {
  issueId?: number;
  onClose: () => void;
  /** the issue is gone (deleted or withdrawn): close and refresh */
  onMutationSuccess: () => void;
}) {
  const { isAdmin, profileId } = useProfile();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const selectedIssueQuery = useQuery({
    ...issueDetailQueryOptions(profileId, issueId ?? 0),
    enabled: issueId != null,
  });
  const issue = selectedIssueQuery.data ?? null;
  const queryError = selectedIssueQuery.error;
  const queryLoading = selectedIssueQuery.isLoading;
  const [reply, setReply] = useState('');
  const [tool, setTool] = useState<OpenTool | null>(null);
  const [offerResolve, setOfferResolve] = useState(false);
  // a pending app confirm sits outside the dialog; don't let it read as a dismiss
  const confirmingRef = useRef(false);
  const isOpen = Boolean(issueId || queryLoading || queryError);

  useEffect(() => {
    setReply('');
    setTool(null);
    setOfferResolve(false);
  }, [issueId]);

  const snapshot = issue ? parseSnapshot(issue.snapshot_data) : {};
  const artistLink = issue ? getIssueArtistLink(issue, snapshot) : null;

  // admins act on the item, so check it still exists. the enhanced payload is
  // the artist's full library listing, which also hands the redownload tool
  // the real track and album rows it expects.
  const presenceQuery = useQuery({
    queryKey: ['issues', 'item', artistLink?.artistId ?? ''],
    enabled: Boolean(isAdmin && issue && artistLink),
    staleTime: 60_000,
    retry: false,
    queryFn: async () => {
      const response = await fetch(
        `/api/library/artist/${encodeURIComponent(artistLink!.artistId)}/enhanced`,
      );
      if (response.status === 404) return { success: false } as EnhancedPayload;
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return (await response.json()) as EnhancedPayload;
    },
  });
  const presence: ItemPresence =
    issue && presenceQuery.data ? findItem(issue, presenceQuery.data) : UNCHECKED;

  const refresh = () => {
    void invalidateIssuesQueries(queryClient);
  };

  const updateMutation = useMutation({
    mutationFn: (updates: IssueUpdatePayload) => updateIssue(issue!.id, updates),
    onSuccess: (_, updates) => {
      if (updates.status === 'resolved') notify('Marked resolved', 'success');
      setOfferResolve(false);
      refresh();
    },
    onError: notifyError,
  });

  const replyMutation = useMutation({
    mutationFn: (body: string) => addIssueComment(issue!.id, body),
    onSuccess: () => {
      setReply('');
      refresh();
    },
    onError: notifyError,
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteIssue(id),
    onSuccess: () => {
      notify('Issue removed', 'info');
      onMutationSuccess();
    },
    onError: notifyError,
  });

  const downloadWorkflowMutation = useMutation({
    mutationFn: launchAlbumDownloadWorkflow,
    onError: notifyError,
    onSuccess: onClose,
  });

  const wishlistWorkflowMutation = useMutation({
    mutationFn: launchAlbumWishlistWorkflow,
    onError: notifyError,
    onSuccess: () => {
      if (issue && isActive(issue.status)) setOfferResolve(true);
    },
  });

  if (!issue && !queryLoading && !queryError) {
    return null;
  }

  const isReporter = Boolean(issue && issue.profile_id === profileId);
  const busy = updateMutation.isPending || deleteMutation.isPending || replyMutation.isPending;
  const albumWorkflowInput = {
    spotifyAlbumId: String(snapshot.spotify_album_id || ''),
    artistName: String(snapshot.artist_name || ''),
    albumName: String(snapshot.album_title || snapshot.title || ''),
    source: 'issue',
  };

  const confirm = async (options: Parameters<NonNullable<Window['showConfirmDialog']>>[0]) => {
    if (!window.showConfirmDialog) return false;
    confirmingRef.current = true;
    try {
      return await window.showConfirmDialog(options);
    } finally {
      confirmingRef.current = false;
    }
  };

  const removeIssue = async () => {
    if (!issue) return;
    const withdrawing = !isAdmin;
    const ok = await confirm({
      title: withdrawing ? 'Withdraw this report?' : 'Delete this issue?',
      message: withdrawing
        ? 'Your report is removed for good.'
        : 'The report and its thread are removed for good.',
      confirmText: withdrawing ? 'Withdraw' : 'Delete',
      cancelText: 'Cancel',
      destructive: true,
    });
    if (ok) deleteMutation.mutate(issue.id);
  };

  const openArtistPage = (hint?: string) => {
    if (!artistLink) {
      notify("This report didn't keep a link to the item", 'warning');
      return;
    }
    if (hint) notify(hint, 'info');
    onClose();
    void navigate({
      to: '/artist-detail/$source/$id',
      params: { source: 'library', id: artistLink.artistId },
      search: { name: artistLink.artistName },
    });
  };

  const runFixAction = () => {
    if (!issue?.fix_action) return;
    const entity = issue.entity_type;
    switch (issue.fix_action.id) {
      case 'reidentify':
        if (entity === 'track') {
          setTool({ kind: 'reidentify' });
          return;
        }
        openArtistPage('Open the track there and pick Re-identify from its menu');
        return;
      case 'pick_art': {
        const target = artTarget(issue, snapshot);
        if (target) {
          setTool({ kind: 'art', target: target.target, subtitle: target.subtitle });
          return;
        }
        openArtistPage('Pick the new art from the album there');
        return;
      }
      case 'redownload':
        if (entity === 'track' && presence.track && presence.album) {
          setTool({ kind: 'redownload', track: presence.track, album: presence.album });
          return;
        }
        openArtistPage('Open the track there and pick Redownload from its menu');
        return;
      case 'wishlist_missing':
        if (entity !== 'artist' && albumWorkflowInput.albumName) {
          wishlistWorkflowMutation.mutate(albumWorkflowInput);
          return;
        }
        openArtistPage('Wishlist the missing tracks from the album there');
        return;
      case 'edit_metadata':
        openArtistPage('Switch to the enhanced view there to edit the details');
        return;
      case 'find_duplicates':
        notify('Run the Duplicate Detector in Library Maintenance, then resolve this', 'info');
        onClose();
        void navigate({ to: '/tools' });
        return;
      default:
        openArtistPage();
    }
  };

  const closeTool = () => {
    setTool(null);
    if (issue && isActive(issue.status)) setOfferResolve(true);
  };

  const sendReply = () => {
    const body = reply.trim();
    if (body && issue) replyMutation.mutate(body);
  };

  const fixAction =
    isAdmin && issue?.fix_action && isActive(issue.status) ? issue.fix_action : null;
  const menuItems = issue ? buildMenuItems() : [];

  return (
    <>
      <DialogFrame
        open={isOpen && !tool}
        onOpenChange={(nextOpen) => {
          if (!nextOpen && !confirmingRef.current) {
            onClose();
          }
        }}
        className={styles.issueDetailDialog}
      >
        <DialogHeader
          title={issue ? `Issue #${issue.id}` : 'Issue details'}
          closeLabel="Close issue detail"
        />
        <DialogBody>{renderIssueDetailContent()}</DialogBody>
        {issue && (
          <DialogFooter>
            <div className={styles.threadFooter}>
              <div className={styles.threadFooterStart}>
                <Show when={menuItems.length}>
                  <IssueMenu items={menuItems} disabled={busy} />
                </Show>
                <Show when={isAdmin && isActive(issue.status)}>
                  <button
                    type="button"
                    className={styles.quietButton}
                    disabled={busy}
                    onClick={() => updateMutation.mutate({ status: 'resolved' })}
                  >
                    Resolve
                  </button>
                </Show>
              </div>
              <div className={styles.threadFooterEnd}>
                <Button
                  className={fixAction ? styles.modalButtonSecondary : styles.modalButtonPrimary}
                  type="button"
                  disabled={!reply.trim() || busy}
                  onClick={sendReply}
                >
                  {replyMutation.isPending ? 'Sending…' : 'Send reply'}
                </Button>
                {fixAction && (
                  <Button
                    className={styles.modalButtonPrimary}
                    type="button"
                    disabled={busy || wishlistWorkflowMutation.isPending || presence.removed}
                    title={presence.removed ? 'The item is no longer in the library' : undefined}
                    onClick={runFixAction}
                  >
                    {wishlistWorkflowMutation.isPending ? 'Loading…' : fixAction.label}
                  </Button>
                )}
              </div>
            </div>
          </DialogFooter>
        )}
      </DialogFrame>
      {issue && tool ? <BodyPortal>{renderTool(issue, tool)}</BodyPortal> : null}
    </>
  );

  function renderTool(current: IssueRecord, open: OpenTool) {
    if (open.kind === 'reidentify') {
      return (
        <ReidentifyModal
          trackId={current.entity_id}
          trackTitle={String(snapshot.title || current.title)}
          artistName={String(snapshot.artist_name || '')}
          albumTitle={String(snapshot.album_title || '')}
          imageUrl={getIssueArtwork(snapshot)}
          onClose={closeTool}
        />
      );
    }
    if (open.kind === 'art') {
      return (
        <ArtPicker
          target={open.target}
          subtitle={open.subtitle}
          onApplied={() => setOfferResolve(true)}
          onClose={closeTool}
        />
      );
    }
    return (
      <RedownloadModal
        track={open.track}
        album={open.album}
        artistName={String(snapshot.artist_name || '')}
        onReload={refresh}
        onClose={closeTool}
      />
    );
  }

  function buildMenuItems(): MenuItem[] {
    if (!issue) return [];
    const items: MenuItem[] = [];
    if (isAdmin) {
      if (issue.status === 'open') {
        items.push({
          key: 'progress',
          label: 'Mark in progress',
          onSelect: () => setStatus('in_progress'),
        });
      }
      if (isActive(issue.status)) {
        items.push({ key: 'dismiss', label: 'Dismiss', onSelect: () => setStatus('dismissed') });
      } else {
        items.push({ key: 'reopen', label: 'Reopen', onSelect: () => setStatus('open') });
      }
      items.push({ key: 'sep-priority', label: 'Priority', heading: true });
      for (const priority of ISSUE_PRIORITY_VALUES) {
        items.push({
          key: `priority-${priority}`,
          label: priority[0].toUpperCase() + priority.slice(1),
          checked: getPriorityClassName(issue.priority) === priority,
          onSelect: () => setPriority(priority),
        });
      }
      if (issue.entity_type !== 'artist') {
        items.push({ key: 'sep-album', label: 'Album', heading: true });
        items.push({
          key: 'download',
          label: downloadWorkflowMutation.isPending ? 'Loading…' : 'Download album',
          onSelect: () => downloadWorkflowMutation.mutate(albumWorkflowInput),
        });
        items.push({
          key: 'wishlist',
          label: 'Add album to wishlist',
          onSelect: () => wishlistWorkflowMutation.mutate(albumWorkflowInput),
        });
      }
      items.push({
        key: 'delete',
        label: 'Delete issue',
        danger: true,
        onSelect: () => void removeIssue(),
      });
    } else if (isReporter && issue.status === 'open') {
      items.push({
        key: 'withdraw',
        label: 'Withdraw report',
        danger: true,
        onSelect: () => void removeIssue(),
      });
    }
    return items;
  }

  function setStatus(status: IssueStatus) {
    updateMutation.mutate({ status });
  }

  function setPriority(priority: IssuePriority) {
    if (issue && getPriorityClassName(issue.priority) !== priority) {
      updateMutation.mutate({ priority });
    }
  }

  function renderIssueDetailContent() {
    if (queryLoading) {
      return (
        <div className={styles.issuesLoading}>
          <div className={styles.issuesSpinner} />
          Loading issue details...
        </div>
      );
    }

    if (queryError) {
      return (
        <div className={styles.issuesEmpty}>
          <div className={styles.issuesEmptyTitle}>Failed to load issue</div>
          <div className={styles.issuesEmptyText}>
            {queryError instanceof Error ? queryError.message : 'Unknown error'}
          </div>
        </div>
      );
    }

    if (!issue) {
      return null;
    }

    const categoryMeta = getIssueCategoryMeta(issue.category) || ISSUE_CATEGORY_META.other;
    const artwork = getIssueArtwork(snapshot);
    const itemName = getItemName(issue, snapshot);
    const itemContext = getItemContext(issue, snapshot);
    const reporter = issue.reporter_name || (isReporter ? 'You' : '');
    const eyebrow = [categoryMeta.label, reporter, formatIssueAgo(issue.created_at)]
      .filter(Boolean)
      .join(' · ');
    const followers = formatFollowers(
      (issue.followers ?? []).map((f) => String(f.follower_name || '')),
    );
    const priority = getPriorityClassName(issue.priority);

    return (
      <>
        <div className={styles.threadHero}>
          <Show
            when={artwork}
            fallback={<div className={styles.threadHeroArtPlaceholder}>{categoryMeta.icon}</div>}
          >
            <img
              className={`${styles.threadHeroArt} ${issue.entity_type === 'artist' ? styles.threadHeroArtRound : ''}`}
              src={artwork}
              alt=""
            />
          </Show>
          <div className={styles.threadHeroInfo}>
            <div className={styles.threadEyebrow} title={formatIssueDate(issue.created_at)}>
              {eyebrow}
            </div>
            <div className={styles.threadTitle}>{issue.title}</div>
            <div className={styles.threadItem}>
              <span className={styles.threadItemType}>{getEntityLabel(issue.entity_type)}</span>
              <span className={styles.threadItemName}>{itemName}</span>
              <Show when={itemContext}>
                <span className={styles.threadItemContext}> · {itemContext}</span>
              </Show>
            </div>
            <div className={styles.threadItemLinks}>
              <Show when={artistLink && !presence.removed}>
                <button
                  type="button"
                  className={styles.threadLink}
                  onClick={() => openArtistPage()}
                >
                  Open {getEntityLabel(issue.entity_type).toLowerCase()} →
                </button>
              </Show>
              <Show when={presence.removed}>
                <span className={styles.threadRemoved}>Item removed from the library</span>
              </Show>
            </div>
          </div>
          <div className={styles.threadHeroSide}>
            <span className={`${styles.issueStatusBadge} ${getStatusClassName(issue.status)}`}>
              {formatStatusLabel(issue.status)}
            </span>
            <Show when={priority !== 'normal'}>
              <span className={styles.threadPriority}>
                <span
                  className={`${styles.issuePriorityDot} ${getPriorityDotClassName(priority)}`}
                />
                {priority} priority
              </span>
            </Show>
          </div>
        </div>

        <Show when={followers}>
          <div className={styles.threadFollowers}>{followers}</div>
        </Show>

        <Show when={offerResolve && isAdmin && isActive(issue.status)}>
          <div className={styles.threadOffer} role="status">
            <span>Did that fix it?</span>
            <div className={styles.threadOfferActions}>
              <button
                type="button"
                className={styles.quietButton}
                onClick={() => setOfferResolve(false)}
              >
                Not yet
              </button>
              <Button
                className={styles.modalButtonPrimary}
                type="button"
                disabled={busy}
                onClick={() => updateMutation.mutate({ status: 'resolved' })}
              >
                Mark resolved
              </Button>
            </div>
          </div>
        </Show>

        <ol className={styles.thread} aria-label="Thread">
          <ThreadComment
            author={reporter || 'Reporter'}
            when={issue.created_at}
            body={issue.description || ''}
            emptyText="No details given"
          />
          <Show when={issue.admin_response && !hasComment(issue.comments, issue.admin_response)}>
            <ThreadComment
              author="Admin"
              when={issue.updated_at}
              body={issue.admin_response || ''}
            />
          </Show>
          {(issue.comments ?? []).map((comment) =>
            comment.kind === 'event' ? (
              <li key={comment.id} className={styles.threadEvent}>
                <span className={styles.threadEventDot} aria-hidden="true" />
                <span>
                  <strong>{comment.author_name || 'Someone'}</strong> {comment.body}
                </span>
                <span className={styles.threadWhen}>{formatIssueAgo(comment.created_at)}</span>
              </li>
            ) : (
              <ThreadComment
                key={comment.id}
                author={comment.author_name || 'Someone'}
                when={comment.created_at}
                body={comment.body}
              />
            ),
          )}
        </ol>

        <textarea
          className={styles.threadReply}
          id="issue-detail-reply-input"
          aria-label="Reply"
          value={reply}
          onChange={(event) => setReply(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
              event.preventDefault();
              sendReply();
            }
          }}
          placeholder={isAdmin ? 'Reply to the reporter…' : 'Add a reply…'}
          maxLength={4000}
          rows={3}
        />

        <ItemDetails issue={issue} snapshot={snapshot} />
      </>
    );
  }
}

interface EnhancedPayload {
  success?: boolean;
  albums?: EnhancedAlbum[];
}

/** is the reported item still in the artist's library listing */
export function findItem(issue: IssueRecord, payload: EnhancedPayload): ItemPresence {
  if (!payload.success) return { checked: true, removed: true };
  if (issue.entity_type === 'artist') return { checked: true, removed: false };
  const albums = payload.albums ?? [];
  if (issue.entity_type === 'album') {
    const album = albums.find((a) => String(a.id) === String(issue.entity_id));
    return { checked: true, removed: !album, album };
  }
  for (const album of albums) {
    const track = (album.tracks ?? []).find((t) => String(t.id) === String(issue.entity_id));
    if (track) return { checked: true, removed: false, album, track };
  }
  // not under this artist any more: deleted, or re-filed somewhere else
  return { checked: true, removed: true };
}

function artTarget(
  issue: IssueRecord,
  snapshot: IssueSnapshot,
): { target: ArtPickerTarget; subtitle: string } | null {
  if (issue.entity_type === 'artist') {
    return {
      target: { kind: 'artist', id: issue.entity_id },
      subtitle: `${String(snapshot.name || '')} · applies to SoulSync, your server, and artist.jpg on disk`,
    };
  }
  const albumId = issue.entity_type === 'album' ? issue.entity_id : snapshot.album_id;
  if (albumId == null || albumId === '') return null;
  const albumTitle = String(
    (issue.entity_type === 'album' ? snapshot.title : snapshot.album_title) || '',
  );
  const artistName = String(snapshot.artist_name || '');
  return {
    target: { kind: 'album', id: albumId, artistName, albumTitle },
    subtitle: [artistName, albumTitle].filter(Boolean).join(' · '),
  };
}

function getItemName(issue: IssueRecord, snapshot: IssueSnapshot): string {
  return String(
    snapshot.title || snapshot.name || `${getEntityLabel(issue.entity_type)} #${issue.entity_id}`,
  );
}

function getItemContext(issue: IssueRecord, snapshot: IssueSnapshot): string {
  if (issue.entity_type === 'track') {
    return [snapshot.artist_name, snapshot.album_title].filter(Boolean).map(String).join(' · ');
  }
  if (issue.entity_type === 'album') return String(snapshot.artist_name || '');
  return '';
}

function hasComment(comments: IssueComment[] | undefined, body: string | null | undefined) {
  const text = String(body || '').trim();
  return (comments ?? []).some((c) => c.kind !== 'event' && c.body.trim() === text);
}

function isActive(status: string) {
  return status === 'open' || status === 'in_progress';
}

function ThreadComment({
  author,
  body,
  emptyText,
  when,
}: {
  author: string;
  body: string;
  emptyText?: string;
  when?: string | null;
}) {
  return (
    <li className={styles.threadComment}>
      <span className={styles.threadAvatar} aria-hidden="true">
        {(author.trim()[0] || '?').toUpperCase()}
      </span>
      <div className={styles.threadCommentMain}>
        <div className={styles.threadCommentHead}>
          <span className={styles.threadAuthor}>{author}</span>
          <span className={styles.threadWhen} title={formatIssueDate(when)}>
            {formatIssueAgo(when)}
          </span>
        </div>
        <div className={body ? styles.threadBody : styles.threadBodyEmpty}>{body || emptyText}</div>
      </div>
    </li>
  );
}

interface MenuItem {
  key: string;
  label: ReactNode;
  onSelect?: () => void;
  danger?: boolean;
  checked?: boolean;
  /** a small section label, not clickable */
  heading?: boolean;
}

/**
 * the ⋯ menu. it lives inside the dialog on purpose: a body-level popup
 * counts as an outside press for the modal and would close it.
 */
function IssueMenu({ items, disabled }: { items: MenuItem[]; disabled?: boolean }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation();
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', onDown, true);
    document.addEventListener('keydown', onKey, true);
    return () => {
      document.removeEventListener('mousedown', onDown, true);
      document.removeEventListener('keydown', onKey, true);
    };
  }, [open]);

  return (
    <div className={styles.menuRoot} ref={rootRef}>
      <button
        type="button"
        className={styles.menuTrigger}
        aria-label="More actions"
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={disabled}
        onClick={() => setOpen((value) => !value)}
      >
        ⋯
      </button>
      {open ? (
        <div className={styles.menuPopup} role="menu">
          {items.map((item) =>
            item.heading ? (
              <div key={item.key} className={styles.menuHeading}>
                {item.label}
              </div>
            ) : (
              <button
                key={item.key}
                type="button"
                role={item.checked === undefined ? 'menuitem' : 'menuitemradio'}
                aria-checked={item.checked}
                className={`${styles.menuItem} ${item.danger ? styles.menuItemDanger : ''}`}
                onClick={() => {
                  setOpen(false);
                  item.onSelect?.();
                }}
              >
                <span>{item.label}</span>
                {item.checked ? <span aria-hidden="true">✓</span> : null}
              </button>
            ),
          )}
        </div>
      ) : null}
    </div>
  );
}

/** the snapshot the report captured, folded away so the thread leads */
function ItemDetails({ issue, snapshot }: { issue: IssueRecord; snapshot: IssueSnapshot }) {
  const externalLinks = getExternalLinks(snapshot);
  const trackMetaItems = getTrackMetaItems(snapshot);
  const trackRows = Array.isArray(snapshot.tracks) ? snapshot.tracks : [];
  const albumMetaParts = getAlbumMetaParts(issue, snapshot);
  const genreTags = Array.isArray(snapshot.genres) ? snapshot.genres.slice(0, 5) : [];
  const hasAnything =
    externalLinks.length ||
    trackMetaItems.length ||
    trackRows.length ||
    albumMetaParts.length ||
    genreTags.length ||
    snapshot.file_path;
  if (!hasAnything) return null;

  return (
    <details className={styles.itemDetails}>
      <summary className={styles.itemDetailsSummary}>
        As it was when reported
        <span className={styles.itemDetailsDate}>{formatIssueDate(issue.created_at)}</span>
      </summary>
      <div className={styles.itemDetailsBody}>
        <Show when={albumMetaParts.length > 0}>
          <div className={styles.issueHeroMeta}>{albumMetaParts.join(' · ')}</div>
        </Show>
        <Show when={genreTags.length}>
          <div className={styles.issueHeroGenres}>
            {genreTags.map((genre) => (
              <span className={styles.issueHeroGenreTag} key={String(genre)}>
                {String(genre)}
              </span>
            ))}
          </div>
        </Show>
        <Show when={externalLinks.length}>
          <div className={styles.issueExternalLinks}>
            {externalLinks.map((link) => (
              <Show
                key={`${link.service}-${link.type}-${link.label}`}
                when={link.url}
                fallback={
                  <span
                    className={`${styles.issueExternalLink} ${styles[link.className]}`}
                    title={`${link.service} ${link.type}: ${link.id}`}
                  >
                    <span className={styles.issueExternalLinkService}>{link.service}</span>
                    <span className={styles.issueExternalLinkType}>{link.type}</span>
                  </span>
                }
              >
                <a
                  className={`${styles.issueExternalLink} ${styles[link.className]}`}
                  href={link.url}
                  target="_blank"
                  rel="noreferrer"
                  title={link.label}
                >
                  <span className={styles.issueExternalLinkService}>{link.service}</span>
                  <span className={styles.issueExternalLinkType}>{link.type}</span>
                </a>
              </Show>
            ))}
          </div>
        </Show>

        <Show when={issue.entity_type === 'track' && trackMetaItems.length > 0}>
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
        </Show>

        <Show when={snapshot.file_path}>
          {(filePath) => (
            <div className={styles.issueDetailSection}>
              <div className={styles.issueDetailSectionTitle}>File Path</div>
              <div className={styles.issueDetailFilepath}>{String(filePath)}</div>
            </div>
          )}
        </Show>

        <Show when={trackRows.length}>
          <div className={styles.issueDetailSection}>
            <div className={styles.issueDetailSectionTitle}>
              Track Listing{' '}
              <span className={styles.issueDetailSectionCount}>{trackRows.length} tracks</span>
            </div>
            <div className={styles.issueDetailTracklist}>{renderTrackListing(trackRows)}</div>
          </div>
        </Show>
      </div>
    </details>
  );
}

function notify(message: string, type: 'success' | 'error' | 'warning' | 'info' = 'info') {
  window.showToast?.(message, type);
}

function notifyError(error: unknown) {
  notify(error instanceof Error ? error.message : 'Something went wrong', 'error');
}

function renderTrackListing(trackRows: IssueTrackRow[]) {
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
          <Show when={format}>
            <span className={`${styles.issueTrackBadge} ${formatClassName}`}>{format}</span>
          </Show>
          <Show when={bitrate}>
            <span className={`${styles.issueTrackBadge} ${bitrateClassName}`}>{bitrate}</span>
          </Show>
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
