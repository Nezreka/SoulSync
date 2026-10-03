import { HttpResponse, http } from 'msw';
import { afterEach, describe, expect, it } from 'vitest';

import { server } from '@/test/msw';

import { INBOX_BADGE_POLL_MS, refreshDiscoverInboxBadge } from './discover-inbox-badge';

afterEach(() => {
  server.resetHandlers();
  document.body.innerHTML = '';
});

function badge() {
  document.body.innerHTML = '<span class="dl-nav-badge hidden" id="discover-nav-badge">0</span>';
  return document.getElementById('discover-nav-badge')!;
}

describe('the Discover inbox badge', () => {
  it('shows the unread count, and hides at zero', async () => {
    const el = badge();
    server.use(
      http.get('*/api/discover/inbox/counts', () =>
        HttpResponse.json({ success: true, unread: 3 }),
      ),
    );
    refreshDiscoverInboxBadge();
    await expect.poll(() => el.textContent).toBe('3');
    expect(el.classList.contains('hidden')).toBe(false);
    server.use(
      http.get('*/api/discover/inbox/counts', () =>
        HttpResponse.json({ success: true, unread: 0 }),
      ),
    );
    refreshDiscoverInboxBadge();
    await expect.poll(() => el.classList.contains('hidden')).toBe(true);
  });

  it('caps at 99+ and leaves the badge alone when the poll fails', async () => {
    const el = badge();
    server.use(
      http.get('*/api/discover/inbox/counts', () =>
        HttpResponse.json({ success: true, unread: 250 }),
      ),
    );
    refreshDiscoverInboxBadge();
    await expect.poll(() => el.textContent).toBe('99+');
    server.use(
      http.get('*/api/discover/inbox/counts', () => HttpResponse.json({}, { status: 500 })),
    );
    refreshDiscoverInboxBadge();
    await new Promise((r) => setTimeout(r, 20));
    expect(el.textContent).toBe('99+');
    expect(INBOX_BADGE_POLL_MS).toBe(120000);
  });
});
