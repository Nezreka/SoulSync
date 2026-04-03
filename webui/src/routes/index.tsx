import { createFileRoute } from '@tanstack/react-router';

import { LegacyRouteController } from '@/platform/shell/route-controllers';

export const Route = createFileRoute('/')({
  component: IndexRouteComponent,
});

function IndexRouteComponent() {
  return <LegacyRouteController pathname="/" />;
}
