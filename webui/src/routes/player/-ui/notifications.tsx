/**
 * the bell in the player's header: what's new for this profile (new
 * episodes, films, requests, invites). a click opens the list; a row opens
 * what it is about and counts as read. while the tab is in the background,
 * new ones also show as browser notifications when the browser allows
 * (asked from settings, never on load).
 */

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useRouter } from '@tanstack/react-router';
import { useEffect, useRef, useState } from 'react';

import { appURL } from '@/platform/url-base';

import {
  deleteNotification,
  markNotificationsRead,
  notificationsQuery,
  type NotificationList,
  type PlayerNotification,
} from '../-player.api';
import { BellIcon, XIcon } from './icons';
import n from './notifications.module.css';

const SEEN_KEY = 'ss-player-notify-seen';
const PROFILE_NOTIFY_EVENT = 'soulsync:profile-notify';

/** "now", "5m", "3h", "2d", then a date */
export function when(epoch: number, now = Date.now()): string {
  const secs = Math.max(0, Math.floor(now / 1000 - epoch));
  if (secs < 60) return 'now';
  if (secs < 3600) return `${Math.floor(secs / 60)}m`;
  if (secs < 86_400) return `${Math.floor(secs / 3600)}h`;
  if (secs < 7 * 86_400) return `${Math.floor(secs / 86_400)}d`;
  return new Date(epoch * 1000).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

function readSeen(): number {
  try {
    return Number(localStorage.getItem(SEEN_KEY)) || 0;
  } catch {
    return 0;
  }
}

function writeSeen(id: number) {
  try {
    localStorage.setItem(SEEN_KEY, String(id));
  } catch {
    /* private mode: the bell still counts */
  }
}

/** browser notifications for what arrived while the tab was away */
function useBrowserNotices(
  data: NotificationList | undefined,
  open: (x: PlayerNotification) => void,
) {
  const first = useRef(true);
  const opener = useRef(open);
  opener.current = open;
  useEffect(() => {
    if (!data) return;
    const seen = readSeen();
    const fresh = data.notifications.filter((x) => x.id > seen && !x.read);
    if (data.latest_id > seen) writeSeen(data.latest_id);
    // the first answer after a load is what was already there
    if (first.current) {
      first.current = false;
      if (seen === 0) return;
    }
    if (!fresh.length || typeof Notification === 'undefined') return;
    if (Notification.permission !== 'granted' || !document.hidden) return;
    for (const x of fresh.slice(0, 3)) {
      try {
        const shown = new Notification(x.title, {
          body: x.body || undefined,
          tag: x.group_key || `ss-${x.id}`,
          icon: x.image ? appURL(x.image) : undefined,
        });
        shown.onclick = () => {
          window.focus();
          opener.current(x);
          shown.close();
        };
      } catch {
        /* the bell is enough */
      }
    }
  }, [data]);
}

function Row({
  item,
  onOpen,
  onDismiss,
}: {
  item: PlayerNotification;
  onOpen: (x: PlayerNotification) => void;
  onDismiss: (x: PlayerNotification) => void;
}) {
  const [art, setArt] = useState(!!item.image);
  return (
    <li className={n.row} data-unread={item.read ? undefined : 'true'}>
      <button type="button" className={n.open} onClick={() => onOpen(item)} data-nav>
        <span className={n.art} data-kind={item.kind} aria-hidden="true">
          {art && item.image ? (
            <img src={appURL(item.image)} alt="" loading="lazy" onError={() => setArt(false)} />
          ) : (
            <BellIcon />
          )}
        </span>
        <span className={n.text}>
          <span className={n.title}>{item.title}</span>
          {item.body ? <span className={n.body}>{item.body}</span> : null}
          <span className={n.when}>{when(item.created_at)}</span>
        </span>
        {item.read ? null : <span className={n.dot} aria-label="unread" />}
      </button>
      <button
        type="button"
        className={n.dismiss}
        aria-label={`Dismiss ${item.title}`}
        title="Dismiss"
        onClick={() => onDismiss(item)}
        data-nav
      >
        <XIcon />
      </button>
    </li>
  );
}

export function NotificationBell() {
  const queryClient = useQueryClient();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const list = useQuery({
    ...notificationsQuery(),
    refetchInterval: 60_000,
    refetchOnWindowFocus: true,
  });
  const data = list.data;
  const unread = data?.unread ?? 0;
  const key = notificationsQuery().queryKey;

  // a look already on its way would answer with the list from before
  const patch = (fn: (old: NotificationList) => NotificationList) => {
    void queryClient.cancelQueries({ queryKey: key });
    queryClient.setQueryData<NotificationList>(key, (old) => (old ? fn(old) : old));
  };

  const markRead = (ids: number[] | 'all') => {
    patch((old) => ({
      ...old,
      unread:
        ids === 'all'
          ? 0
          : Math.max(
              0,
              old.unread - old.notifications.filter((x) => ids.includes(x.id) && !x.read).length,
            ),
      notifications: old.notifications.map((x) =>
        ids === 'all' || ids.includes(x.id) ? { ...x, read: true } : x,
      ),
    }));
    void markNotificationsRead(ids).catch(() => queryClient.invalidateQueries({ queryKey: key }));
  };

  const openItem = (x: PlayerNotification) => {
    if (!x.read) markRead([x.id]);
    setOpen(false);
    if (x.link && x.link.startsWith('/player')) router.history.push(x.link);
  };

  const dismiss = (x: PlayerNotification) => {
    patch((old) => ({
      ...old,
      unread: x.read ? old.unread : Math.max(0, old.unread - 1),
      notifications: old.notifications.filter((y) => y.id !== x.id),
    }));
    void deleteNotification(x.id).catch(() => queryClient.invalidateQueries({ queryKey: key }));
  };

  useBrowserNotices(data, openItem);

  // a request answered elsewhere comes over the manager's socket: look now
  useEffect(() => {
    const look = () => void queryClient.invalidateQueries({ queryKey: key });
    window.addEventListener(PROFILE_NOTIFY_EVENT, look);
    return () => window.removeEventListener(PROFILE_NOTIFY_EVENT, look);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queryClient]);

  useEffect(() => {
    if (!open) return;
    void queryClient.invalidateQueries({ queryKey: key });
    const close = (event: MouseEvent) => {
      if (!ref.current?.contains(event.target as Node)) setOpen(false);
    };
    const esc = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', close);
    document.addEventListener('keydown', esc);
    return () => {
      document.removeEventListener('mousedown', close);
      document.removeEventListener('keydown', esc);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const items = data?.notifications ?? [];
  const label = unread ? `Notifications, ${unread} unread` : 'Notifications';
  return (
    <div className={n.bell} ref={ref}>
      <button
        type="button"
        className={n.bellButton}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={label}
        title="Notifications"
        onClick={() => setOpen((v) => !v)}
        data-nav
      >
        <BellIcon />
        {unread ? (
          <span className={n.badge} aria-hidden="true">
            {unread > 9 ? '9+' : unread}
          </span>
        ) : null}
      </button>
      {open ? (
        <div className={n.panel} role="dialog" aria-label="Notifications">
          <div className={n.head}>
            <span className={n.heading}>Notifications</span>
            {unread ? (
              <button type="button" className={n.link} onClick={() => markRead('all')} data-nav>
                Mark all read
              </button>
            ) : null}
          </div>
          {list.isError && !data ? (
            <p className={n.empty}>Couldn't load your notifications. Try again in a moment.</p>
          ) : items.length ? (
            <ul className={n.list}>
              {items.map((x) => (
                <Row key={x.id} item={x} onOpen={openItem} onDismiss={dismiss} />
              ))}
            </ul>
          ) : (
            <p className={n.empty}>
              {data ? 'Nothing new. New episodes, films and invites show up here.' : 'Loading…'}
            </p>
          )}
          <Link
            to="/player/settings"
            search={{ section: 'notifications' }}
            className={n.foot}
            onClick={() => setOpen(false)}
            data-nav
          >
            Choose what you hear about
          </Link>
        </div>
      ) : null}
    </div>
  );
}
