import { createFileRoute, redirect } from '@tanstack/react-router';

import { getProfileHomePath } from '@/platform/shell/bridge';
import { LegacyRouteController } from '@/platform/shell/route-controllers';
import { appURL } from '@/platform/url-base';

export const Route = createFileRoute('/')({
  beforeLoad: ({ context, location }) => {
    if (
      location.pathname !== '/' &&
      location.pathname !== appURL('/') &&
      location.pathname !== appURL('/').replace(/\/$/, '')
    )
      return;

    const { bridge } = context.shell;

    throw redirect({ href: appURL(getProfileHomePath(bridge)), replace: true });
  },
  component: IndexRouteComponent,
});

function IndexRouteComponent() {
  return <LegacyRouteController pathname="/" />;
}
