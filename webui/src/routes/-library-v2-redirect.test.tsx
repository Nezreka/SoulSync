import { createMemoryHistory } from '@tanstack/react-router';
import { render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { AppRouterProvider, createAppRouter } from '@/app/router';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

function renderAt(entry: string) {
  const queryClient = createTestQueryClient();
  const history = createMemoryHistory({ initialEntries: [entry] });
  const router = createAppRouter({ history, queryClient });

  return {
    history,
    router,
    ...render(<AppRouterProvider router={router} queryClient={queryClient} />),
  };
}

/** Library V2 became simply "Library" at /library. Everyone who used the page
 *  while it was opt-in has /library-v2 links in their bookmarks, their history
 *  and their open tabs, so that URL has to keep working — including the search
 *  params, which carry the album, filter or discovery view they saved. */
describe('/library-v2 legacy URL', () => {
  beforeEach(() => {
    window.SoulSyncWebShellBridge = createShellBridge();
  });

  afterEach(() => {
    window.SoulSyncWebShellBridge = undefined;
  });

  it('redirects a bare bookmark to the library', async () => {
    const { history } = renderAt('/library-v2');

    await waitFor(() => expect(history.location.pathname).toBe('/library'));
  });

  it('keeps the saved view when redirecting', async () => {
    const { history, router } = renderAt('/library-v2?artist=42&section=wanted');

    await waitFor(() => expect(history.location.pathname).toBe('/library'));
    expect(router.state.location.search).toMatchObject({ artist: 42, section: 'wanted' });
  });
});
