import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { server } from '@/test/msw';

import { ShareListeningAsk } from './share-listening-ask';

/**
 * The one-time ask above Made For You. It shows only while the server says
 * to ask, either answer makes it go away for good (the server remembers),
 * and "Not now" never turns sharing on.
 */

let posted: unknown[] = [];

function mount(
  privacy: Record<string, unknown>,
  reply: Record<string, unknown> = { success: true },
) {
  server.use(
    http.get('/api/profiles/me/listening-privacy', () =>
      HttpResponse.json({ success: true, ...privacy }),
    ),
    http.post('/api/profiles/me/listening-privacy', async ({ request }) => {
      posted.push(await request.json());
      return HttpResponse.json(reply, { status: reply.success === false ? 500 : 200 });
    }),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ShareListeningAsk />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  posted = [];
  window.showToast = vi.fn();
});
afterEach(cleanup);

describe('the share listening ask', () => {
  it('asks in plain words while the server says to', async () => {
    mount({ ask: true, share_listening: false });
    expect(await screen.findByText(/Make blends with people on this server\?/)).toBeTruthy();
    expect(screen.getByText(/only made when you both say yes/)).toBeTruthy();
  });

  it('stays away once asked', async () => {
    const { container } = mount({ ask: false, share_listening: false });
    await waitFor(() => expect(posted).toEqual([]));
    await new Promise((r) => setTimeout(r, 20));
    expect(container.querySelector('.discover-share-ask')).toBeNull();
  });

  it('yes turns sharing on and goes away', async () => {
    const { container } = mount(
      { ask: true },
      { success: true, share_listening: true, ask: false },
    );
    fireEvent.click(await screen.findByText('Share my listening'));
    await waitFor(() => expect(posted).toEqual([{ share_listening: true }]));
    await waitFor(() => expect(container.querySelector('.discover-share-ask')).toBeNull());
    expect(window.showToast).toHaveBeenCalledWith(
      expect.stringMatching(/^Sharing your listening/),
      'success',
    );
  });

  it('not now only stops the asking', async () => {
    const { container } = mount(
      { ask: true },
      { success: true, share_listening: false, ask: false },
    );
    fireEvent.click(await screen.findByText('Not now'));
    await waitFor(() => expect(posted).toEqual([{ asked: true }]));
    await waitFor(() => expect(container.querySelector('.discover-share-ask')).toBeNull());
  });

  it('a failed save keeps the ask and says so', async () => {
    const { container } = mount({ ask: true }, { success: false });
    fireEvent.click(await screen.findByText('Share my listening'));
    await waitFor(() =>
      expect(window.showToast).toHaveBeenCalledWith("Couldn't save that. Try again.", 'error'),
    );
    expect(container.querySelector('.discover-share-ask')).not.toBeNull();
  });
});
