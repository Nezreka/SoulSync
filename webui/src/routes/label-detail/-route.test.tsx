import { createMemoryHistory } from '@tanstack/react-router';
import { render, screen } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { AppRouterProvider, createAppRouter } from '@/app/router';
import { server } from '@/test/msw';
import { createTestQueryClient } from '@/test/query-client';
import { createShellBridge } from '@/test/shell-bridge';

/**
 * What the ROUTE is responsible for: reading the id from the path and the
 * display name from ?name=, and handing both to the page.
 *
 * This used to assert the legacy handoff — the route existed while the vanilla
 * page still rendered the experience. The page is React now, so the same three
 * cases are asserted against what actually reaches it.
 */
function renderRoute(initialEntries: string[]) {
  const queryClient = createTestQueryClient();
  const history = createMemoryHistory({ initialEntries });
  const router = createAppRouter({ history, queryClient });
  return {
    history,
    router,
    ...render(<AppRouterProvider router={router} queryClient={queryClient} />),
  };
}

/** Echo the id back so the assertions can see which one the route passed. */
function stubCatalogEchoingId() {
  server.use(
    http.get('/api/labels/:id/catalog', ({ params, request }) =>
      HttpResponse.json({
        label: { name: new URL(request.url).searchParams.get('name') || String(params.id) },
        total: 0,
        artist_count: 0,
        releases: [],
      }),
    ),
    http.post('/api/enhanced-search/library-check', () => HttpResponse.json({ albums: [] })),
  );
}

describe('label-detail route', () => {
  beforeEach(() => {
    window.SoulSyncWebShellBridge = createShellBridge();
    stubCatalogEchoingId();
  });

  afterEach(() => {
    window.SoulSyncWebShellBridge = undefined;
  });

  it('renders the label page for a canonical URL', async () => {
    renderRoute(['/label-detail/770a1e6b-2d17-4bbe-a0c2-a3c4f77e9bce']);
    // No ?name=, so the page falls back to what the catalog resolves.
    await screen.findByText('770a1e6b-2d17-4bbe-a0c2-a3c4f77e9bce');
    expect(screen.getByText('Record Label')).toBeInTheDocument();
  });

  it('passes the ?name= search param through (survives a page refresh)', async () => {
    renderRoute(['/label-detail/mbid-subpop?name=Sub%20Pop']);
    // Shown before the catalog resolves, which is the whole point of carrying
    // the name in the URL.
    await screen.findByText('Sub Pop');
  });

  it('survives an all-digits label name in ?name=', async () => {
    // TanStack JSON-parses search params, so name=1200 arrives as a NUMBER; the
    // schema must coerce it back to a string or the route dies in its boundary.
    renderRoute(['/label-detail/mbid-x?name=1200']);
    await screen.findByText('1200');
  });

  it('does not display a structured search param as [object Object]', async () => {
    // TanStack JSON-parses search params, so an object literal in ?name=
    // arrives as an OBJECT. String()-ing it would paint "[object Object]" as
    // the label name; the schema drops it instead and the catalog resolves the
    // name, exactly as it does when ?name= is absent.
    renderRoute(['/label-detail/mbid-x?name=%7B%22unexpected%22%3Atrue%7D']);

    await screen.findByText('mbid-x');
    expect(screen.queryByText('[object Object]')).not.toBeInTheDocument();
  });
});
