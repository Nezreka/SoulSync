import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { DialogBody, DialogFrame, DialogHeader } from '@/components/dialog';
import { appURL } from '@/platform/url-base';

import type { CalendarScope } from '../-calendar.types';

import { CALENDAR_QUERY_KEY, feedUrl } from '../-calendar.api';
import styles from './calendar.module.css';

const toast = (message: string, type: string) => window.showToast?.(message, type);

/**
 * The iCal subscription. The url carries this profile's token, so a calendar
 * app (which has no SoulSync session) can read it; reset makes a new one and
 * the old url stops working.
 */
export function FeedDialog({
  open,
  scope,
  onClose,
}: {
  open: boolean;
  scope: CalendarScope;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const key = [...CALENDAR_QUERY_KEY, 'feed-url'] as const;
  const q = useQuery({
    queryKey: key,
    queryFn: () => feedUrl(false),
    enabled: open,
    staleTime: Infinity,
  });
  const [rotating, setRotating] = useState(false);
  const path = q.data?.path;
  const url = path
    ? new URL(appURL(path), window.location.origin).toString() +
      (scope === 'all' ? '?scope=all' : '')
    : '';

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(url);
      toast('Feed link copied', 'success');
    } catch {
      toast('Select the link and copy it', 'info');
    }
  };

  const rotate = async () => {
    setRotating(true);
    try {
      const r = await feedUrl(true);
      if (!r.success) throw new Error();
      qc.setQueryData(key, r);
      toast('New link made. Update it in your calendar app.', 'success');
    } catch {
      toast('Could not make a new link', 'error');
    } finally {
      setRotating(false);
    }
  };

  return (
    <DialogFrame
      open={open}
      onOpenChange={(o) => !o && onClose()}
      aria-label="Subscribe to your calendar"
    >
      <DialogHeader title="Subscribe in a calendar app" />
      <DialogBody>
        <p className={styles.feedLead}>
          Add this link in Google Calendar, Apple Calendar or Outlook (“add calendar from URL”).
          Upcoming episodes and movie releases show up there and stay in sync.
        </p>
        <div className={styles.feedRow}>
          <input
            className={styles.feedUrl}
            readOnly
            value={q.isLoading ? 'Making your link…' : url}
            aria-label="Feed link"
            onFocus={(e) => e.currentTarget.select()}
          />
          <button type="button" className={styles.btnPrimary} disabled={!url} onClick={copy}>
            Copy
          </button>
        </div>
        <p className={styles.feedNote}>
          The link works without signing in, so treat it like a password: anyone with it sees your
          calendar. It covers the next 14 days (add <code>days=60</code> for more), with air times
          where known.{' '}
          <button type="button" className={styles.linkBtn} disabled={rotating} onClick={rotate}>
            {rotating ? 'Resetting…' : 'Reset link'}
          </button>
        </p>
      </DialogBody>
    </DialogFrame>
  );
}
