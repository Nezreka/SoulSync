import { createFileRoute } from '@tanstack/react-router';

import { LegacyRouteController } from '@/platform/shell/route-controllers';

export const Route = createFileRoute('/$')({
  component: LegacyFallbackRouteComponent,
});

function LegacyFallbackRouteComponent() {
  const { _splat } = Route.useParams();
  return <LegacyRouteController pathname={`/${_splat}`} />;
}
