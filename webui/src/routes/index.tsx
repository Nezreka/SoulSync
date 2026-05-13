import { createFileRoute, redirect } from '@tanstack/react-router';

import { getProfileHomePath } from '@/platform/shell/bridge';
import { LegacyRouteController } from '@/platform/shell/route-controllers';

export const Route = createFileRoute('/')({
  beforeLoad: ({ context, location }) => {
    if (location.pathname !== '/') return;

    const { bridge } = context.shell;

    throw redirect({ href: getProfileHomePath(bridge), replace: true });
  },
  component: IndexRouteComponent,
});

function IndexRouteComponent() {
  return <LegacyRouteController pathname="/" />;
}
