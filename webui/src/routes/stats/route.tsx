import { createFileRoute, redirect } from '@tanstack/react-router';

import { getProfileHomePath } from '@/platform/shell/bridge';

import { listeningStatsStatusQueryOptions, statsCachedQueryOptions } from './-stats.api';
import { statsSearchSchema } from './-stats.types';
import { StatsPage } from './-ui/stats-page';

export const Route = createFileRoute('/stats')({
  validateSearch: statsSearchSchema,
  beforeLoad: ({ context }) => {
    const { bridge } = context.shell;

    if (!bridge.isPageAllowed('stats')) {
      throw redirect({ href: getProfileHomePath(bridge), replace: true });
    }
  },
  loaderDeps: ({ search }) => ({
    range: search.range,
  }),
  loader: async ({ context, deps }) => {
    await Promise.all([
      context.queryClient.ensureQueryData(statsCachedQueryOptions(deps.range)),
      context.queryClient.ensureQueryData(listeningStatsStatusQueryOptions()),
    ]);
  },
  component: StatsPage,
});
