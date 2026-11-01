import { useEffect, useLayoutEffect, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

import styles from './status-notice.module.css';

/** Progress belongs outside the document flow. All notices share one stack,
 * so simultaneous wishlist/discography updates cannot cover each other. */
export function StatusNotice({
  tone,
  text,
  children,
  onDismiss,
}: {
  tone: 'busy' | 'ok' | 'err';
  text: string;
  children?: ReactNode;
  onDismiss?: () => void;
}) {
  const [host, setHost] = useState<HTMLElement | null>(null);
  const [dismissed, setDismissed] = useState(false);
  const identity = `${tone}:${text}`;
  // an event (started, done, failed) goes through SoulSync's own toasts and
  // the notification bell, like every other page. a notice with its own
  // controls is a live status (the wishlist sync's Retry) and stays a card.
  const toast = !children && typeof window !== 'undefined' ? window.showToast : undefined;

  useEffect(() => {
    if (!toast) return;
    toast(text, tone === 'ok' ? 'success' : tone === 'err' ? 'error' : 'info');
  }, [identity, toast]); // eslint-disable-line react-hooks/exhaustive-deps

  useLayoutEffect(() => {
    let region = document.getElementById('library-status-notices');
    if (!region) {
      region = document.createElement('div');
      region.id = 'library-status-notices';
      region.className = styles.region;
      region.setAttribute('role', 'region');
      region.setAttribute('aria-label', 'Background activity');
      document.body.appendChild(region);
    }
    setHost(region);
    // Keep the empty host for other notices; React removes this portal's
    // content when its operation or page unmounts.
  }, []);

  useEffect(() => {
    setDismissed(false);
    if (tone !== 'ok') return;
    const timer = window.setTimeout(() => setDismissed(true), 5000);
    return () => window.clearTimeout(timer);
  }, [identity, tone]);

  if (toast || !host || dismissed) return null;
  return createPortal(
    <div
      className={`${styles.notice} ${styles[tone]}`}
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >
      <span className={styles.indicator} aria-hidden="true">
        {tone === 'busy' ? '◌' : tone === 'ok' ? '✓' : '!'}
      </span>
      <span className={styles.message}>{text}</span>
      {children}
      <button
        type="button"
        className={styles.close}
        aria-label="Dismiss notification"
        onClick={() => {
          setDismissed(true);
          onDismiss?.();
        }}
      >
        ×
      </button>
    </div>,
    host,
  );
}
