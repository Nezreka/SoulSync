/**
 * The Discover nav badge: how many inbox items are new.
 *
 * Asking for the count also lets a stale inbox refresh in the background
 * (the server decides), so the badge can say "something new" without anyone
 * opening Discover first. Best-effort: a failed poll leaves the badge as it
 * was.
 */

const BADGE_ID = 'discover-nav-badge';
export const INBOX_BADGE_POLL_MS = 120_000;

export function refreshDiscoverInboxBadge(): void {
  const badge = document.getElementById(BADGE_ID);
  if (!badge) return;
  fetch('/api/discover/inbox/counts', { headers: { Accept: 'application/json' } })
    .then((r) => (r.ok ? (r.json() as Promise<{ success?: boolean; unread?: number }>) : null))
    .then((d) => {
      if (!d?.success) return;
      const n = d.unread ?? 0;
      badge.textContent = n > 99 ? '99+' : String(n);
      badge.classList.toggle('hidden', !n);
    })
    .catch(() => {
      /* best-effort */
    });
}

export function startDiscoverInboxBadge(): void {
  const start = () => {
    setTimeout(refreshDiscoverInboxBadge, 4000);
    setInterval(refreshDiscoverInboxBadge, INBOX_BADGE_POLL_MS);
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
}
