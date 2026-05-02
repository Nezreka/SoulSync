import { Outlet, createRootRouteWithContext } from '@tanstack/react-router';

import type { AppRouterContext } from '@/app/router';

import { IssueDomainHost } from './issues/-ui/issue-domain-host';

export const Route = createRootRouteWithContext<AppRouterContext>()({
  component: () => (
    <>
      <Outlet />
      <IssueDomainHost />
    </>
  ),
});
