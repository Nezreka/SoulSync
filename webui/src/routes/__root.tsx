import { Outlet, createRootRouteWithContext } from '@tanstack/react-router';

import type { AppRouterContext } from '@/app/router';

import { waitForShellContext } from '@/platform/shell/bridge';

import { IssueDomainHost } from './issues/-ui/issue-domain-host';

export const Route = createRootRouteWithContext<AppRouterContext>()({
  beforeLoad: async () => {
    const shell = await waitForShellContext();
    return { shell };
  },
  component: () => (
    <>
      <Outlet />
      <IssueDomainHost />
    </>
  ),
});
