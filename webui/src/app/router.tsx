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

/**
 * What a page shows when it throws.
 *
 * It used to say "Something went wrong. Please refresh." and nothing else.
 * The console is muted by default (see the ?debug=1 notice in index.html), so
 * a user reporting this had literally nothing to send and we had nothing to
 * act on. The router hands us the error; show it, and make it one click to
 * copy.
 */
export function DefaultErrorComponent({ error }: { error?: unknown }) {
  const detail = errorReport(error);
  return (
    <div role="alert" className="app-error-boundary">
      <h2>Something went wrong</h2>
      <p>This page failed to render. The rest of SoulSync still works.</p>
      {detail ? (
        <>
          <pre className="app-error-boundary__detail">{detail}</pre>
          <div className="app-error-boundary__actions">
            <button
              type="button"
              className="btn btn--sm btn--secondary"
              onClick={() => void navigator.clipboard?.writeText(detail)}
            >
              Copy error
            </button>
            <button
              type="button"
              className="btn btn--sm btn--primary"
              onClick={() => window.location.reload()}
            >
              Reload
            </button>
          </div>
          <p className="app-error-boundary__hint">Please include this text in a bug report.</p>
        </>
      ) : (
        <p>Please refresh the page and try again.</p>
      )}
    </div>
  );
}

/** The message plus a trimmed stack, or null when there is nothing to show. */
export function errorReport(error: unknown, maxStackLines = 12): string | null {
  if (error == null) return null;
  if (error instanceof Error) {
    const stack = (error.stack ?? '').split('\n').slice(0, maxStackLines).join('\n');
    // A stack usually repeats the message on its first line; don't print twice.
    return stack.includes(error.message) ? stack : `${error.message}\n${stack}`.trim();
  }
  if (typeof error === 'string') return error;
  try {
    return JSON.stringify(error, null, 2);
  } catch {
    return String(error);
  }
}

export function DefaultNotFoundComponent() {
  return (
    <div role="status">
      <h2>Page not found</h2>
      <p>The requested page could not be found.</p>
    </div>
  );
}
