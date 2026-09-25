import { createFileRoute } from '@tanstack/react-router';

import { guardPageAccess } from '@/platform/shell/route-guard';

import { musicRequestsQueryOptions } from './-requests.api';
import { requestSearchSchema } from './-requests.types';
import { RequestsPage } from './-ui/requests-page';

export const Route = createFileRoute('/requests')({
  validateSearch: requestSearchSchema,
  beforeLoad: ({ context }) => {
    guardPageAccess(context.shell.bridge, 'requests');
  },
  loader: async ({ context }) => {
    // warms the cache only, the page renders its own error state
    await Promise.allSettled([
      context.queryClient.ensureQueryData(
        musicRequestsQueryOptions(context.shell.profile.profileId),
      ),
    ]);
  },
  component: RequestsPage,
});
