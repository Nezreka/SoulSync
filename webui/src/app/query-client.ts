import { QueryClient } from '@tanstack/react-query';

export function createAppQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: 1,
        refetchOnWindowFocus: false,
        staleTime: 10_000,
      },
    },
  });
}
