import { createFileRoute, redirect } from '@tanstack/react-router';

import { getProfileHomePath, getShellProfileContext } from '@/platform/shell/bridge';

import {
  issueCountsQueryOptions,
  issueDetailQueryOptions,
  issueListQueryOptions,
} from './-issues.api';
import { normalizeIssuesSearch } from './-issues.helpers';
import { IssuesPage } from './-ui/issues-page';

export const Route = createFileRoute('/issues')({
  validateSearch: normalizeIssuesSearch,
  beforeLoad: ({ context }) => {
    const bridge = context.platform.getShellBridge();
    if (bridge && !bridge.isPageAllowed('issues')) {
      throw redirect({ href: getProfileHomePath(bridge), replace: true });
    }
  },
  loaderDeps: ({ search }) => ({
    status: search.status,
    category: search.category,
    issueId: search.issueId ?? null,
  }),
  loader: async ({ context, deps }) => {
    const profile = getShellProfileContext(context.platform.getShellBridge());
    if (!profile) return;

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
