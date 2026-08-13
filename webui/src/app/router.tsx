import { QueryClientProvider, type QueryClient } from '@tanstack/react-query';
import { createRouter, type RouterHistory } from '@tanstack/react-router';
import { RouterProvider } from '@tanstack/react-router';

import { getShellBridge } from '@/platform/shell/bridge';
import { routeTree } from '@/routeTree.gen';

import { createAppQueryClient } from './query-client';

export interface AppRouterContext {
  queryClient: QueryClient;
  platform: {
    getShellBridge: typeof getShellBridge;
  };
}

export function createAppRouter(
  options: {
    history?: RouterHistory;
    queryClient?: QueryClient;
    context?: Partial<AppRouterContext>;
  } = {},
) {
  const queryClient = options.queryClient ?? createAppQueryClient();
  const context: AppRouterContext = {
    ...options.context,
    queryClient,
    platform: {
      getShellBridge,
      ...options.context?.platform,
    },
  };

  return createRouter({
    routeTree,
    history: options.history,
    context,
    defaultPreload: 'intent',
    defaultPreloadStaleTime: 0,
    /**
     * Restoring scroll is right for the long list pages — go back to the
     * library and you are where you left off. It reads wrong on artist detail:
     * the similar-artist bubbles are at the very bottom, so the position it
     * saves is the footer, and going back drops you there instead of at the
     * artist you just returned to.
     *
     * Returning false makes the router leave scroll ALONE for that route (it
     * bails before both the restore and the scroll-to-top), so the page can own
     * it with no race between the two.
     */
    scrollRestoration: ({ location }) => !location.pathname.startsWith('/artist-detail'),
    defaultErrorComponent: DefaultErrorComponent,
    defaultNotFoundComponent: DefaultNotFoundComponent,
  });
}

export function AppRouterProvider({
  router,
  queryClient,
}: {
  router: ReturnType<typeof createAppRouter>;
  queryClient: QueryClient;
}) {
  return (
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  );
}

declare module '@tanstack/react-router' {
  interface Register {
    router: ReturnType<typeof createAppRouter>;
  }
}

export function DefaultErrorComponent() {
  return (
    <div role="alert">
      <h2>Something went wrong</h2>
      <p>Please refresh the page and try again.</p>
    </div>
  );
}

export function DefaultNotFoundComponent() {
  return (
    <div role="status">
      <h2>Page not found</h2>
      <p>The requested page could not be found.</p>
    </div>
  );
}
