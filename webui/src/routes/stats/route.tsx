import { createFileRoute } from '@tanstack/react-router';

import { guardPageAccess } from '@/platform/shell/route-guard';

import {
  lastfmListeningImportStatusQueryOptions,
  listeningStatsStatusQueryOptions,
  statsCachedQueryOptions,
} from './-stats.api';
import { statsSearchSchema } from './-stats.types';
import { StatsPage } from './-ui/stats-page';

export const Route = createFileRoute('/stats')({
  validateSearch: statsSearchSchema,
  beforeLoad: ({ context }) => {
    guardPageAccess(context.shell.bridge, 'stats');
  },
  loaderDeps: ({ search }) => ({
    range: search.range,
  }),
  loader: async ({ context, deps }) => {
    // allSettled, not all: this loader WARMS the cache for the first paint, it
    // does not gate the route. A rejection here would hand the page to
    // defaultErrorComponent ("Something went wrong") on any backend hiccup,
    // where the vanilla page stayed usable. The components read the same
    // failures through useQuery and render their own error states.
    await Promise.allSettled([
      context.queryClient.ensureQueryData(statsCachedQueryOptions(deps.range)),
      context.queryClient
        .fetchQuery({
          ...listeningStatsStatusQueryOptions(),
          retry: false,
        })
        .catch(() => undefined),
      context.queryClient
        .fetchQuery({
          ...lastfmListeningImportStatusQueryOptions(),
          retry: false,
        })
        .catch(() => undefined),
    ]);
  },
  component: StatsPage,
});
