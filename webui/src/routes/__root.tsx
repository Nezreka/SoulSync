import { Outlet, createRootRouteWithContext } from '@tanstack/react-router';

import type { AppRouterContext } from '@/app/router';

import { waitForShellContext } from '@/platform/shell/bridge';
import { shellStatusQueryOptions } from '@/platform/shell/status';

import { IssueDomainHost } from './issues/-ui/issue-domain-host';
import { AutoSyncHost } from './sync/-ui/autosync-host';

export const Route = createRootRouteWithContext<AppRouterContext>()({
  beforeLoad: async ({ context }) => {
    const [shell, status] = await Promise.all([
      waitForShellContext(),
      context.queryClient.fetchQuery(shellStatusQueryOptions()).catch(() => undefined),
    ]);

    return { shell: { ...shell, status } };
  },
  component: () => (
    <>
      <Outlet />
      <IssueDomainHost />
      {/* the dashboard opens auto-sync through a window global, this makes
          it the same react manager the playlists page uses */}
      <AutoSyncHost />
    </>
  ),
});
