import { createFileRoute, redirect } from '@tanstack/react-router';

import { getProfileHomePath, getShellProfileContext } from '@/platform/shell/bridge';

import {
  issueCountsQueryOptions,
  issueListQueryOptions,
  normalizeIssuesSearch,
} from './-issues.helpers';
import { IssuesPage } from './-ui/issues-page';

export const Route = createFileRoute('/issues')({
  validateSearch: normalizeIssuesSearch,
  beforeLoad: ({ context }) => {
    const bridge = context.platform.getShellBridge();
    if (bridge && !bridge.isPageAllowed('issues')) {
      throw redirect({ href: getProfileHomePath(bridge), replace: true });
    }
  },
  loaderDeps: ({ search }) => search,
  loader: async ({ context, deps }) => {
    const profile = getShellProfileContext(context.platform.getShellBridge());
    if (!profile) return;

    await Promise.all([
      context.queryClient.ensureQueryData(issueCountsQueryOptions(profile.profileId)),
      context.queryClient.ensureQueryData(issueListQueryOptions(profile.profileId, deps)),
    ]);
  },
  component: IssuesPage,
});
