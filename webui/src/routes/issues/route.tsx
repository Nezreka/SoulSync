import { createFileRoute } from '@tanstack/react-router';

import { guardPageAccess } from '@/platform/shell/route-guard';

import {
  issueCountsQueryOptions,
  issueDetailQueryOptions,
  issueListQueryOptions,
} from './-issues.api';
import { issueSearchSchema } from './-issues.types';
import { IssuesPage } from './-ui/issues-page';

export const Route = createFileRoute('/issues')({
  validateSearch: issueSearchSchema,
  beforeLoad: ({ context }) => {
    guardPageAccess(context.shell.bridge, 'issues');
  },
  loaderDeps: ({ search }) => ({
    status: search.status,
    category: search.category,
    issueId: search.issueId ?? null,
  }),
  loader: async ({ context, deps }) => {
    const { profile } = context.shell;

    // allSettled, not all: this loader WARMS the cache for the first paint, it
    // does not gate the route. A rejection here would hand the page to
    // defaultErrorComponent ("Something went wrong") on any backend hiccup,
    // where the vanilla page stayed usable. The components read the same
    // failures through useQuery and render their own error states.
    await Promise.allSettled([
      context.queryClient.ensureQueryData(issueCountsQueryOptions(profile.profileId)),
      context.queryClient.ensureQueryData(issueListQueryOptions(profile.profileId, deps)),
      deps.issueId
        ? context.queryClient.ensureQueryData(
            issueDetailQueryOptions(profile.profileId, deps.issueId),
          )
        : Promise.resolve(),
    ]);
  },
  component: IssuesPage,
});
