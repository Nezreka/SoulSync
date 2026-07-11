import type { QueryClient } from '@tanstack/react-query';

import { createAppQueryClient } from '@/app/query-client';

// The app client retries failed queries once with a ~1s backoff, so in tests a
// query's error state lands right at Testing Library's 1s findBy* timeout —
// error-path assertions become a coin flip on slow runners. Tests exercise
// failures deterministically through MSW, so retries only add latency here.
export function createTestQueryClient(): QueryClient {
  const queryClient = createAppQueryClient();
  const defaults = queryClient.getDefaultOptions();

  queryClient.setDefaultOptions({
    ...defaults,
    queries: { ...defaults.queries, retry: false },
    mutations: { ...defaults.mutations, retry: false },
  });

  return queryClient;
}
