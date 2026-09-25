import { Menu } from '@base-ui/react/menu';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import clsx from 'clsx';
import { useEffect, useMemo, useState } from 'react';

import { DialogBody, DialogFooter, DialogFrame, DialogHeader } from '@/components/dialog';
import { Button, FormField, TextArea } from '@/components/form';
import { useProfile, useReactPageShell } from '@/platform/shell/route-controllers';

import type { RequestItem, RequestTab } from '../-requests.types';

import {
  approveMusicRequest,
  declineMusicRequest,
  deleteMusicRequest,
  invalidateMusicRequests,
  markMusicRequestsSeen,
  musicRequestsQueryOptions,
  withdrawMusicRequest,
} from '../-requests.api';
import {
  REQUEST_TABS,
  buildRequestItems,
  countForTab,
  emptyText,
  filterRequestItems,
  itemImage,
  itemKey,
  itemKind,
  itemTitle,
  itemTracks,
  statusText,
  subLine,
  tabLabel,
  whoAsked,
} from '../-requests.helpers';
import { Route } from '../route';
import styles from './requests-page.module.css';

export const PROFILE_NOTIFY_EVENT = 'soulsync:profile-notify';

function toast(message: string, type: 'success' | 'error' | 'info' | 'warning' = 'success') {
  window.showToast?.(message, type);
}

function refreshBadge() {
  window.refreshMusicRequestsBadge?.();
}

export function RequestsPage() {
  useReactPageShell('requests');
  const { isAdmin, profileId } = useProfile();
  const queryClient = useQueryClient();
  const navigate = useNavigate({ from: Route.fullPath });
  const { tab } = Route.useSearch();
  const [declining, setDeclining] = useState<RequestItem | null>(null);

  const listQuery = useQuery(musicRequestsQueryOptions(profileId));

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

  const approve = useMutation({
    mutationFn: (item: RequestItem) => {
      if (item.source !== 'pending') throw new Error('That request isn’t waiting any more');
      return approveMusicRequest(profileId, {
        profile_id: item.group.profile_id,
        key: item.group.key,
      });
    },
    onSuccess: (_data, item) => {
      const name = item.source === 'pending' ? item.group.requester_name : '';
      toast(name ? `Approved. ${name} will hear when it lands` : 'Approved');
      afterChange();
    },
    onError: (error) => {
      toast(error instanceof Error ? error.message : 'Could not approve that request', 'error');
      afterChange();
    },
  });

  const decline = useMutation({
    mutationFn: ({ item, response }: { item: RequestItem; response: string }) => {
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
      toast(error instanceof Error ? error.message : 'Could not decline that request', 'error');
    },
  });

  const withdraw = useMutation({
    mutationFn: (item: RequestItem) => {
      if (item.source !== 'pending') throw new Error('That request isn’t waiting any more');
      return withdrawMusicRequest(profileId, item.group.key);
    },
    onSuccess: () => {
      toast('Request withdrawn', 'info');
      afterChange();
    },
    onError: (error) => {
      toast(error instanceof Error ? error.message : 'Could not withdraw that request', 'error');
    },
  });

  const remove = useMutation({
    mutationFn: (item: RequestItem) => {
      if (item.source !== 'history') throw new Error('Only finished requests can be removed');
      return deleteMusicRequest(profileId, item.row.id);
    },
    onSuccess: afterChange,
    onError: (error) => {
      toast(error instanceof Error ? error.message : 'Could not remove that request', 'error');
    },
  });

  const confirmWithdraw = async (item: RequestItem) => {
    const ok = window.showConfirmDialog
      ? await window.showConfirmDialog({
          title: 'Withdraw request',
          message: `Take back your request for ${itemTitle(item)}? It comes off your wishlist too.`,
          confirmText: 'Withdraw',
          destructive: true,
        })
      : true;
    if (ok) withdraw.mutate(item);
  };

  const setTab = (next: RequestTab) => {
    void navigate({ to: Route.fullPath, search: { tab: next }, replace: true });
  };

  const busy = approve.isPending || withdraw.isPending || remove.isPending;

  return (
    <div className={styles.page}>
      <header className={styles.head}>
        <h1 className={styles.title}>Requests</h1>
        <p className={styles.subtitle}>
          {isAdmin
            ? 'What your household asked for. Approve once, everyone who asked hears about it.'
            : 'What you asked for, and where it’s at.'}
        </p>
      </header>

      <div className={styles.tabs} role="tablist" aria-label="Request status">
        {REQUEST_TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={tab === t.id}
            className={clsx(styles.tab, tab === t.id && styles.tabActive)}
            onClick={() => setTab(t.id)}
          >
            {tabLabel(t, countForTab(items, t.id))}
          </button>
        ))}
      </div>

      {listQuery.isError ? (
        <div className={styles.empty}>
          Couldn’t load requests.{' '}
          <button type="button" className={styles.linkButton} onClick={() => void listQuery.refetch()}>
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
              busy={busy}
              isAdmin={isAdmin}
              item={item}
              onApprove={() => approve.mutate(item)}
              onDecline={() => setDeclining(item)}
              onRemove={() => remove.mutate(item)}
              onWithdraw={() => void confirmWithdraw(item)}
            />
          ))}
        </ul>
      )}

      <DeclineDialog
        item={declining}
        pending={decline.isPending}
        onClose={() => setDeclining(null)}
        onConfirm={(response) => {
          if (declining) decline.mutate({ item: declining, response });
        }}
      />
    </div>
  );
}

function RequestRow({
  busy,
  isAdmin,
  item,
  onApprove,
  onDecline,
  onRemove,
  onWithdraw,
}: {
  busy: boolean;
  isAdmin: boolean;
  item: RequestItem;
  onApprove: () => void;
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
  const reason = item.source === 'history' ? item.row.admin_response : null;

  return (
    <li className={styles.row} data-status={item.status}>
      <div className={styles.thumb} aria-hidden="true">
        {image && !imageFailed ? (
          <img src={image} alt="" loading="lazy" onError={() => setImageFailed(true)} />
        ) : (
          <span className={styles.thumbGlyph}>{kind === 'album' ? '◎' : '♪'}</span>
        )}
      </div>

      <div className={styles.main}>
        <div className={styles.titleLine}>
          <span className={styles.rowTitle}>{itemTitle(item)}</span>
          <span className={styles.kind}>{kind === 'album' ? 'Album' : 'Track'}</span>
        </div>
        {subLine(item) ? <div className={styles.sub}>{subLine(item)}</div> : null}
        <div className={styles.who}>{whoAsked(item, isAdmin)}</div>
        {reason ? <div className={styles.reason}>“{reason}”</div> : null}
        {showTracks && tracks.length > 0 ? (
          <ol className={styles.tracks}>
            {tracks.map((t) => (
              <li key={t.id}>{t.title}</li>
            ))}
          </ol>
        ) : null}
      </div>

      <div className={styles.side}>
        <span className={styles.status} data-status={item.status}>
          {statusText(item)}
        </span>
        {isAdmin && pending ? (
          <Button variant="primary" size="sm" disabled={busy} onClick={onApprove}>
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
                {pending && isAdmin ? (
                  <Menu.Item className={clsx(styles.menuItem, styles.menuDanger)} onClick={onDecline}>
                    Decline…
                  </Menu.Item>
                ) : null}
                {pending && !isAdmin ? (
                  <Menu.Item className={clsx(styles.menuItem, styles.menuDanger)} onClick={onWithdraw}>
                    Withdraw
                  </Menu.Item>
                ) : null}
                {!pending ? (
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

  const who = item?.source === 'pending' ? item.group.requester_name : '';

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
