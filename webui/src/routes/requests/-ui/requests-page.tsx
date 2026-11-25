import { Menu } from '@base-ui/react/menu';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import clsx from 'clsx';
import { useEffect, useMemo, useState } from 'react';

import { DialogBody, DialogFooter, DialogFrame, DialogHeader } from '@/components/dialog';
import { Button, FormField, TextArea } from '@/components/form';
import { refusalMessage, useRequestRights } from '@/platform/shell/my-permissions';
import { useProfile, useReactPageShell } from '@/platform/shell/route-controllers';

import type {
  AcquisitionKind,
  AcquisitionMode,
  AcquisitionModes,
  AwaitingMedia,
  RequestItem,
  RequestTab,
} from '../-requests.types';

import {
  AWAITING_QUERY_KEY,
  approveAllMusicRequests,
  approveMediaRequest,
  approveMusicRequest,
  approveMusicVideoRequest,
  declineMediaRequest,
  declineMusicRequest,
  declineMusicVideoRequest,
  deleteMediaRequest,
  deleteMusicRequest,
  deleteMusicVideoRequest,
  fetchAwaitingQueue,
  invalidateMusicRequests,
  markMediaRequestPicked,
  markMusicRequestsSeen,
  musicRequestsQueryOptions,
  normalizeModes,
  releaseMediaRequestToAuto,
  releaseMusicRequestToAuto,
  releaseVideoRequestToAuto,
  saveAcquisitionModes,
  withdrawMediaRequest,
  withdrawMusicRequest,
  withdrawMusicVideoRequest,
} from '../-requests.api';
import {
  REQUEST_TABS,
  buildRequestItems,
  canAnswer,
  countForTab,
  emptyText,
  filterRequestItems,
  itemImage,
  itemKey,
  itemKind,
  itemOwner,
  itemTitle,
  itemTracks,
  kindLabel,
  quotaLine,
  showPickTab,
  statusText,
  subLine,
  tabLabel,
  whoAsked,
} from '../-requests.helpers';
import { Route } from '../route';
import { AccessRequests } from './access-requests';
import { ApprovalSettingsDialog } from './approval-settings';
import { PickQueue, pickItems, type PickItem } from './pick-queue';
import styles from './requests-page.module.css';

export const PROFILE_NOTIFY_EVENT = 'soulsync:profile-notify';

function toast(
  message: string,
  type: 'success' | 'error' | 'info' | 'warning' = 'success',
  opts?: { history?: boolean },
) {
  if (opts) window.showToast?.(message, type, null, opts);
  else window.showToast?.(message, type);
}

/** a failed action: the server's refusal in its own calm words (#2166, the
 *  shell shows the same words, so the toast dedupes), anything else as an
 *  error */
function failToast(error: unknown, fallback: string) {
  const refused = refusalMessage((error as { data?: unknown } | null)?.data);
  if (refused) {
    // a refusal is said once, not kept in the notification panel (#2191)
    toast(refused, 'info', { history: false });
    return;
  }
  toast(error instanceof Error ? error.message : fallback, 'error');
}

function refreshBadge() {
  window.refreshMusicRequestsBadge?.();
}

/** the kind whose setting decides a waiting ask's approval, or null when
 *  it has nothing to pick between (a music video, a podcast episode) */
export function pickKind(item: RequestItem): AcquisitionKind | null {
  if (item.source === 'pending') return 'music';
  if (item.source === 'media-pending' && item.media.kind === 'audiobook') return 'audiobook';
  return null;
}

/** the other way to approve one ask: "Approve & pick release" when the
 *  setting downloads automatically, "Approve & auto-download" when it holds */
export function alternateApproval(
  item: RequestItem,
  modes: AcquisitionModes,
): { mode: AcquisitionMode; label: string } | null {
  const kind = pickKind(item);
  if (!kind) return null;
  return modes[kind] === 'manual'
    ? { mode: 'auto', label: 'Approve & auto-download' }
    : { mode: 'manual', label: 'Approve & pick release' };
}

export function RequestsPage() {
  useReactPageShell('requests');
  const { isAdmin: shellAdmin, profileId } = useProfile();
  // who answers and who sees whose (#2166): admins everything; a moderator
  // answers other people's asks, never their own; see-everyone's shows the
  // household's asks with names
  const rights = useRequestRights();
  const isAdmin = shellAdmin || rights.isAdmin;
  const answerRights = { isAdmin, moderate: rights.moderate };
  const showNames = isAdmin || rights.seeAll;
  const queryClient = useQueryClient();
  const navigate = useNavigate({ from: Route.fullPath });
  const { tab } = Route.useSearch();
  const [declining, setDeclining] = useState<RequestItem | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  // who may pick a release for a held request: the same people who answer
  const decider = isAdmin || rights.moderate;

  const listQuery = useQuery(musicRequestsQueryOptions(profileId));
  const modes = listQuery.data?.acquisition ?? normalizeModes(undefined);
  const awaitingQuery = useQuery({
    queryKey: [...AWAITING_QUERY_KEY, profileId],
    queryFn: () => fetchAwaitingQueue(profileId),
    enabled: decider,
  });
  const picks = useMemo(
    () => (awaitingQuery.data ? pickItems(awaitingQuery.data) : []),
    [awaitingQuery.data],
  );
  const pickCount = awaitingQuery.data ? picks.length : (listQuery.data?.awaiting ?? 0);
  const anyManual = Object.values(modes).includes('manual');
  const pickTab = showPickTab(decider, pickCount, anyManual);
  const tabs = REQUEST_TABS.filter((t) => t.id !== 'pick' || pickTab);

  // a member opening the page has read their news
  useEffect(() => {
    if (isAdmin) return;
    markMusicRequestsSeen(profileId)
      .then(refreshBadge)
      .catch(() => undefined);
  }, [isAdmin, profileId]);

  // the server tells us when something moved (approved, arrived, new ask)
  useEffect(() => {
    const onNotify = (event: Event) => {
      const link = (event as CustomEvent<{ link?: string }>).detail?.link;
      if (link === 'requests') void invalidateMusicRequests(queryClient);
    };
    window.addEventListener(PROFILE_NOTIFY_EVENT, onNotify);
    return () => window.removeEventListener(PROFILE_NOTIFY_EVENT, onNotify);
  }, [queryClient]);

  const items = useMemo(
    () => (listQuery.data ? buildRequestItems(listQuery.data) : []),
    [listQuery.data],
  );
  const visible = filterRequestItems(items, tab);

  const afterChange = () => {
    void invalidateMusicRequests(queryClient);
    refreshBadge();
  };

  // one song of a playlist's request, answered on its own (#2302)
  const answerSong = useMutation({
    mutationFn: ({ item, trackId, yes }: { item: RequestItem; trackId: string; yes: boolean }) => {
      if (item.source !== 'pending') throw new Error('That request isn’t waiting any more');
      const body = {
        profile_id: item.group.profile_id,
        key: item.group.key,
        track_ids: [trackId],
      };
      return yes ? approveMusicRequest(profileId, body) : declineMusicRequest(profileId, body);
    },
    onSuccess: (_data, { yes }) => {
      toast(yes ? 'Approved that song' : 'Declined that song', yes ? 'success' : 'info');
      afterChange();
    },
    onError: (error) => {
      failToast(error, 'Could not answer that song');
      afterChange();
    },
  });

  const approve = useMutation({
    // acquisition: the approver's choice for this one (#2199); left out, the
    // kind's setting decides
    mutationFn: ({ item, acquisition }: { item: RequestItem; acquisition?: AcquisitionMode }) => {
      if (item.source === 'video-pending') {
        return approveMusicVideoRequest(profileId, item.video.id);
      }
      if (item.source === 'media-pending') {
        return approveMediaRequest(profileId, item.media.id, acquisition);
      }
      if (item.source !== 'pending') throw new Error('That request isn’t waiting any more');
      return approveMusicRequest(profileId, {
        profile_id: item.group.profile_id,
        key: item.group.key,
        ...(acquisition ? { acquisition } : {}),
      });
    },
    onSuccess: (_data, { item, acquisition }) => {
      const name =
        item.source === 'pending'
          ? item.group.requester_name
          : item.source === 'video-pending'
            ? item.video.requester_name
            : item.source === 'media-pending'
              ? item.media.requester_name
              : '';
      const kind = pickKind(item);
      const held = kind != null && (acquisition ?? modes[kind]) === 'manual';
      if (held) toast('Approved. Pick its release on the Pick a release tab');
      else toast(name ? `Approved. ${name} will hear when it lands` : 'Approved');
      afterChange();
    },
    onError: (error) => {
      failToast(error, 'Could not approve that request');
      afterChange();
    },
  });

  const decline = useMutation({
    mutationFn: ({ item, response }: { item: RequestItem; response: string }) => {
      if (item.source === 'video-pending') {
        return declineMusicVideoRequest(profileId, item.video.id, response || undefined);
      }
      if (item.source === 'media-pending') {
        return declineMediaRequest(profileId, item.media.id, response || undefined);
      }
      if (item.source !== 'pending') throw new Error('That request isn’t waiting any more');
      return declineMusicRequest(profileId, {
        profile_id: item.group.profile_id,
        key: item.group.key,
        response: response || undefined,
      });
    },
    onSuccess: () => {
      toast('Declined', 'info');
      setDeclining(null);
      afterChange();
    },
    onError: (error) => {
      failToast(error, 'Could not decline that request');
    },
  });

  const withdraw = useMutation({
    mutationFn: (item: RequestItem) => {
      if (item.source === 'video-pending') {
        return withdrawMusicVideoRequest(profileId, item.video.id);
      }
      if (item.source === 'media-pending') {
        return withdrawMediaRequest(profileId, item.media.id);
      }
      if (item.source !== 'pending') throw new Error('That request isn’t waiting any more');
      return withdrawMusicRequest(profileId, item.group.key);
    },
    onSuccess: () => {
      toast('Request withdrawn', 'info');
      afterChange();
    },
    onError: (error) => {
      failToast(error, 'Could not withdraw that request');
    },
  });

  const remove = useMutation({
    mutationFn: (item: RequestItem) => {
      if (item.source === 'video-history') {
        return deleteMusicVideoRequest(profileId, item.video.id);
      }
      if (item.source === 'media-history') {
        return deleteMediaRequest(profileId, item.media.id);
      }
      if (item.source !== 'history') throw new Error('Only finished requests can be removed');
      return deleteMusicRequest(profileId, item.row.id);
    },
    onSuccess: afterChange,
    onError: (error) => {
      failToast(error, 'Could not remove that request');
    },
  });

  const saveModes = useMutation({
    mutationFn: (changed: Partial<AcquisitionModes>) => saveAcquisitionModes(profileId, changed),
    onSuccess: () => {
      toast('Saved');
      setSettingsOpen(false);
      afterChange();
    },
    onError: (error) => failToast(error, 'Could not save the approval settings'),
  });

  const handBack = useMutation({
    mutationFn: (item: PickItem) => {
      if (item.source === 'music') {
        return releaseMusicRequestToAuto(profileId, {
          profile_id: item.group.profile_id,
          key: item.group.key,
        });
      }
      if (item.source === 'media') return releaseMediaRequestToAuto(profileId, item.media.id);
      return releaseVideoRequestToAuto(profileId, item.video.id);
    },
    onSuccess: () => {
      toast('It downloads automatically now');
      afterChange();
    },
    onError: (error) => failToast(error, 'Could not hand that request to the automatic download'),
  });

  const bookPicked = useMutation({
    mutationFn: (media: AwaitingMedia) => markMediaRequestPicked(profileId, media.id),
    onSuccess: (_d, media) => {
      toast(
        media.requester_name
          ? `On its way. ${media.requester_name} will hear when it lands`
          : 'On its way',
      );
      afterChange();
    },
    onError: (error) =>
      failToast(error, 'The release is on its way, but the request didn’t update'),
  });

  const approveAll = useMutation({
    mutationFn: () => approveAllMusicRequests(profileId),
    onSuccess: (approved) => {
      toast(
        approved === 0
          ? 'Nothing was waiting'
          : `Approved ${approved} request${approved === 1 ? '' : 's'}. Everyone who asked will hear`,
      );
      afterChange();
    },
    onError: (error) => {
      failToast(error, 'Could not approve those requests');
      afterChange();
    },
  });

  const waitingCount =
    (listQuery.data?.pending.length ?? 0) +
    (listQuery.data?.pendingVideos.length ?? 0) +
    (listQuery.data?.pendingMedia?.length ?? 0);

  const confirmApproveAll = async () => {
    const noun = waitingCount === 1 ? 'request' : 'requests';
    const ok = window.showConfirmDialog
      ? await window.showConfirmDialog({
          title: `Approve all ${waitingCount} ${noun}?`,
          message:
            'Everything waiting starts downloading, and everyone who asked hears it’s on the way.',
          confirmText: 'Approve all',
        })
      : true;
    if (ok) approveAll.mutate();
  };

  const confirmWithdraw = async (item: RequestItem) => {
    const stored = item.source === 'video-pending' || item.source === 'media-pending';
    const ok = window.showConfirmDialog
      ? await window.showConfirmDialog({
          title: 'Withdraw request',
          message: stored
            ? `Take back your request for ${itemTitle(item)}?`
            : `Take back your request for ${itemTitle(item)}? It comes off your wishlist too.`,
          confirmText: 'Withdraw',
          destructive: true,
        })
      : true;
    if (ok) withdraw.mutate(item);
  };

  const setTab = (next: RequestTab) => {
    void navigate({ to: Route.fullPath, search: { tab: next }, replace: true });
  };

  const busy =
    approve.isPending ||
    approveAll.isPending ||
    withdraw.isPending ||
    remove.isPending ||
    handBack.isPending;
  const quotaText = isAdmin ? '' : quotaLine(listQuery.data?.quota);

  return (
    <div className="page-shell pgf-shell">
      <div className="pgf-page pgf-page--plain">
        <header className={styles.head}>
          <div className={styles.headText}>
            <h1 className="pgf-title">Requests</h1>
            <p className="pgf-status">
              {isAdmin
                ? 'What your household asked for. Approve once, everyone who asked hears about it.'
                : rights.moderate
                  ? 'What your household asked for. You answer everyone’s asks but your own.'
                  : rights.seeAll
                    ? 'What your household asked for, and where it’s at.'
                    : 'What you asked for, and where it’s at.'}
            </p>
            {quotaText ? <p className={styles.quota}>{quotaText}</p> : null}
          </div>
          <div className={styles.headActions}>
            {isAdmin ? (
              <Button variant="ghost" size="sm" onClick={() => setSettingsOpen(true)}>
                Approval settings
              </Button>
            ) : null}
            {isAdmin && waitingCount > 1 ? (
              <Button
                variant="secondary"
                size="sm"
                disabled={busy}
                onClick={() => void confirmApproveAll()}
              >
                {approveAll.isPending ? 'Approving…' : 'Approve all'}
              </Button>
            ) : null}
          </div>
        </header>

        <div className={`pgf-pills ${styles.tabs}`} role="tablist" aria-label="Request status">
          {tabs.map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={tab === t.id}
              className="pgf-pill"
              onClick={() => setTab(t.id)}
            >
              {tabLabel(t, t.id === 'pick' ? pickCount : countForTab(items, t.id))}
            </button>
          ))}
        </div>

        {isAdmin ? <AccessRequests /> : null}
        {tab === 'pick' && decider ? (
          <PickQueue
            items={picks}
            loading={awaitingQuery.isPending}
            failed={awaitingQuery.isError}
            busy={busy}
            onRetry={() => void awaitingQuery.refetch()}
            onPicked={afterChange}
            onBookGrabbed={(media) => bookPicked.mutate(media)}
            onAuto={(item) => handBack.mutate(item)}
          />
        ) : listQuery.isError ? (
          <div className={styles.empty}>
            Couldn’t load requests.{' '}
            <button
              type="button"
              className={styles.linkButton}
              onClick={() => void listQuery.refetch()}
            >
              Try again
            </button>
          </div>
        ) : listQuery.isPending ? (
          <div className={styles.empty}>Loading…</div>
        ) : visible.length === 0 ? (
          <div className={styles.empty}>
            {emptyText(tab, isAdmin, listQuery.data?.asksFirst ?? false)}
          </div>
        ) : (
          <ul className={styles.list}>
            {visible.map((item) => (
              <RequestRow
                key={itemKey(item)}
                onSong={(trackId, yes) => answerSong.mutate({ item, trackId, yes })}
                busy={busy || answerSong.isPending}
                answer={canAnswer(item, answerRights, profileId)}
                showNames={showNames}
                viewerId={profileId}
                item={item}
                modes={modes}
                onGoPick={decider ? () => setTab('pick') : undefined}
                onApprove={(acquisition) => approve.mutate({ item, acquisition })}
                onDecline={() => setDeclining(item)}
                onRemove={() => remove.mutate(item)}
                onWithdraw={() => void confirmWithdraw(item)}
              />
            ))}
          </ul>
        )}

        {isAdmin ? (
          <ApprovalSettingsDialog
            open={settingsOpen}
            modes={modes}
            saving={saveModes.isPending}
            onClose={() => setSettingsOpen(false)}
            onSave={(changed) => saveModes.mutate(changed)}
          />
        ) : null}

        <DeclineDialog
          item={declining}
          pending={decline.isPending}
          onClose={() => setDeclining(null)}
          onConfirm={(response) => {
            if (declining) decline.mutate({ item: declining, response });
          }}
        />
      </div>
    </div>
  );
}

function RequestRow({
  busy,
  answer,
  showNames,
  viewerId,
  item,
  modes,
  onGoPick,
  onApprove,
  onDecline,
  onRemove,
  onWithdraw,
  onSong,
}: {
  /** approve or decline one song of a playlist's request */
  onSong: (trackId: string, yes: boolean) => void;
  busy: boolean;
  /** may approve or decline this one (an admin, or a moderator and not theirs) */
  answer: boolean;
  showNames: boolean;
  viewerId: number;
  item: RequestItem;
  modes: AcquisitionModes;
  /** a decider: an approved ask waiting for a pick links to the pick tab */
  onGoPick?: () => void;
  /** acquisition: the other way than the kind's setting, for this one */
  onApprove: (acquisition?: AcquisitionMode) => void;
  onDecline: () => void;
  onRemove: () => void;
  onWithdraw: () => void;
}) {
  const [showTracks, setShowTracks] = useState(false);
  const [imageFailed, setImageFailed] = useState(false);
  const kind = itemKind(item);
  const image = itemImage(item);
  const tracks = itemTracks(item);
  const pending = item.status === 'pending';
  const alternate = answer ? alternateApproval(item, modes) : null;
  const kindForPick = pickKind(item);
  const holdsByDefault = kindForPick != null && modes[kindForPick] === 'manual';
  // withdraw is yours alone, whoever else can see it
  const mine = itemOwner(item) === viewerId;
  const reason =
    item.source === 'history'
      ? item.row.admin_response
      : item.source === 'video-history'
        ? item.video.admin_response
        : item.source === 'media-history'
          ? item.media.admin_response
          : null;

  return (
    <li className={styles.row} data-status={item.status}>
      <div className={styles.thumb} aria-hidden="true">
        {image && !imageFailed ? (
          <img src={image} alt="" loading="lazy" onError={() => setImageFailed(true)} />
        ) : (
          <span className={styles.thumbGlyph}>
            {kind === 'album'
              ? '◎'
              : kind === 'playlist'
                ? '≡'
                : kind === 'track'
                  ? '♪'
                  : kind === 'audiobook'
                    ? '▤'
                    : kind === 'podcast'
                      ? '◉'
                      : '▶'}
          </span>
        )}
      </div>

      <div className={styles.main}>
        <div className={styles.titleLine}>
          <span className={styles.rowTitle}>{itemTitle(item)}</span>
          <span className={styles.kind}>{kindLabel(kind)}</span>
        </div>
        {subLine(item) ? <div className={styles.sub}>{subLine(item)}</div> : null}
        <div className={styles.who}>{whoAsked(item, showNames, Date.now(), viewerId)}</div>
        {reason ? <div className={styles.reason}>“{reason}”</div> : null}
        {showTracks && tracks.length > 0 ? (
          <ol className={styles.tracks}>
            {tracks.map((t) => (
              <li key={t.id}>
                {t.title}
                {t.artist && kind === 'playlist' ? ` · ${t.artist}` : ''}
                {/* a playlist's songs can each be answered on their own */}
                {kind === 'playlist' && pending && answer && tracks.length > 1 ? (
                  <>
                    {' '}
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={busy}
                      aria-label={`Approve ${t.title}`}
                      onClick={() => onSong(t.id, true)}
                    >
                      Approve
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={busy}
                      aria-label={`Decline ${t.title}`}
                      onClick={() => onSong(t.id, false)}
                    >
                      Decline
                    </Button>
                  </>
                ) : null}
              </li>
            ))}
          </ol>
        ) : null}
      </div>

      <div className={styles.side}>
        <span className={styles.status} data-status={item.status}>
          {statusText(item)}
        </span>
        {answer ? (
          <Button
            variant="primary"
            size="sm"
            disabled={busy}
            title={holdsByDefault ? 'Approve, then pick its release' : undefined}
            onClick={() => onApprove()}
          >
            Approve
          </Button>
        ) : null}
        <Menu.Root>
          <Menu.Trigger className={styles.more} aria-label={`More for ${itemTitle(item)}`}>
            ⋯
          </Menu.Trigger>
          <Menu.Portal>
            <Menu.Positioner className={styles.menuPositioner} align="end" sideOffset={6}>
              <Menu.Popup className={styles.menu}>
                {tracks.length > 1 ? (
                  <Menu.Item className={styles.menuItem} onClick={() => setShowTracks((v) => !v)}>
                    {showTracks ? 'Hide tracks' : `Show ${tracks.length} tracks`}
                  </Menu.Item>
                ) : null}
                {alternate ? (
                  <Menu.Item className={styles.menuItem} onClick={() => onApprove(alternate.mode)}>
                    {alternate.label}
                  </Menu.Item>
                ) : null}
                {item.status === 'awaiting_selection' && onGoPick ? (
                  <Menu.Item className={styles.menuItem} onClick={onGoPick}>
                    Pick a release
                  </Menu.Item>
                ) : null}
                {answer ? (
                  <Menu.Item
                    className={clsx(styles.menuItem, styles.menuDanger)}
                    onClick={onDecline}
                  >
                    Decline…
                  </Menu.Item>
                ) : null}
                {pending && mine ? (
                  <Menu.Item
                    className={clsx(styles.menuItem, styles.menuDanger)}
                    onClick={onWithdraw}
                  >
                    Withdraw
                  </Menu.Item>
                ) : null}
                {!pending &&
                (item.source === 'history' ||
                  item.source === 'video-history' ||
                  item.source === 'media-history') ? (
                  <Menu.Item className={styles.menuItem} onClick={onRemove}>
                    Remove from history
                  </Menu.Item>
                ) : null}
              </Menu.Popup>
            </Menu.Positioner>
          </Menu.Portal>
        </Menu.Root>
      </div>
    </li>
  );
}

function DeclineDialog({
  item,
  onClose,
  onConfirm,
  pending,
}: {
  item: RequestItem | null;
  onClose: () => void;
  onConfirm: (response: string) => void;
  pending: boolean;
}) {
  const [reason, setReason] = useState('');

  useEffect(() => {
    if (item) setReason('');
  }, [item]);

  const who =
    item?.source === 'pending'
      ? item.group.requester_name
      : item?.source === 'video-pending'
        ? item.video.requester_name
        : item?.source === 'media-pending'
          ? item.media.requester_name
          : '';

  return (
    <DialogFrame open={item != null} onOpenChange={(open) => (!open ? onClose() : undefined)}>
      <DialogHeader title="Decline request">
        {item ? <span>{itemTitle(item)}</span> : null}
      </DialogHeader>
      <DialogBody>
        <FormField label="Reason (optional)" htmlFor="request-decline-reason">
          <TextArea
            id="request-decline-reason"
            rows={3}
            maxLength={500}
            value={reason}
            placeholder={who ? `${who} sees this` : 'They see this'}
            onChange={(event) => setReason(event.target.value)}
          />
        </FormField>
      </DialogBody>
      <DialogFooter>
        <Button variant="ghost" onClick={onClose} disabled={pending}>
          Cancel
        </Button>
        <Button variant="primary" onClick={() => onConfirm(reason.trim())} disabled={pending}>
          {pending ? 'Declining…' : 'Decline'}
        </Button>
      </DialogFooter>
    </DialogFrame>
  );
}
