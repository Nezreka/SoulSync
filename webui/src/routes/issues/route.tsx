import { createFileRoute, redirect } from '@tanstack/react-router';

import { getProfileHomePath } from '@/platform/shell/bridge';

import {
  issueCountsQueryOptions,
  issueDetailQueryOptions,
  issueListQueryOptions,
} from './-issues.api';
import { ISSUE_SEARCH_SCHEMA } from './-issues.types';
import { IssuesPage } from './-ui/issues-page';

export const Route = createFileRoute('/issues')({
  validateSearch: ISSUE_SEARCH_SCHEMA,
  beforeLoad: ({ context }) => {
    const { bridge } = context.shell;

    if (!bridge.isPageAllowed('issues')) {
      throw redirect({ href: getProfileHomePath(bridge), replace: true });
    }
  },
  loaderDeps: ({ search }) => ({
    status: search.status,
    category: search.category,
    issueId: search.issueId ?? null,
  }),
  loader: async ({ context, deps }) => {
    const { profile } = context.shell;

    await Promise.all([
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
