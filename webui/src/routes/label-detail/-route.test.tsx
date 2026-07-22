import { createMemoryHistory } from '@tanstack/react-router';
import { render, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { AppRouterProvider, createAppRouter } from '@/app/router';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

function renderLabelDetailRoute(initialEntries = ['/label-detail/mbid-1']) {
  const queryClient = createTestQueryClient();
  const history = createMemoryHistory({ initialEntries });
  const router = createAppRouter({ history, queryClient });

  return {
    history,
    router,
    ...render(<AppRouterProvider router={router} queryClient={queryClient} />),
  };
}

describe('label-detail route', () => {
  beforeEach(() => {
    window.SoulSyncWebShellBridge = createShellBridge();
  });

  afterEach(() => {
    window.SoulSyncWebShellBridge = undefined;
  });

  it('hands off canonical label-detail URLs to the legacy shell', async () => {
    renderLabelDetailRoute(['/label-detail/770a1e6b-2d17-4bbe-a0c2-a3c4f77e9bce']);

    await waitFor(() => {
      expect(window.SoulSyncWebShellBridge?.navigateToLabelDetail).toHaveBeenCalledWith(
        '770a1e6b-2d17-4bbe-a0c2-a3c4f77e9bce',
        '',
        { skipRouteChange: true },
      );
    });
  });

  it('passes the ?name= search param through (survives a page refresh)', async () => {
    renderLabelDetailRoute(['/label-detail/mbid-subpop?name=Sub%20Pop']);

    await waitFor(() => {
      expect(window.SoulSyncWebShellBridge?.navigateToLabelDetail).toHaveBeenCalledWith(
        'mbid-subpop',
        'Sub Pop',
        { skipRouteChange: true },
      );
    });
  });

  it('survives an all-digits label name in ?name=', async () => {
    // TanStack JSON-parses search params, so name=1200 arrives as a NUMBER; the
    // schema must coerce it back to a string or the route dies in its boundary.
    renderLabelDetailRoute(['/label-detail/mbid-x?name=1200']);

    await waitFor(() => {
      expect(window.SoulSyncWebShellBridge?.navigateToLabelDetail).toHaveBeenCalledWith(
        'mbid-x',
        '1200',
        { skipRouteChange: true },
      );
    });
  });
});
