import { createFileRoute, redirect } from '@tanstack/react-router';

import { getProfileHomePath } from '@/platform/shell/bridge';
import { LegacyRouteController } from '@/platform/shell/route-controllers';
import { getShellRouteByPageId } from '@/platform/shell/route-manifest';

import { automationsListQueryOptions, automationsMasterQueryOptions } from './-automations.api';
import { automationsSearchSchema } from './-automations.types';
import { AutomationsPage } from './-ui/automations-page';

/**
 * Whether the shell has handed /automations over to React yet.
 *
 * The route file exists (and is tested) before the React page reaches parity.
 * Without this check TanStack would match /automations regardless of the
 * manifest and the vanilla page and the React host would both activate.
 * Delete this indirection once the vanilla automations page is gone.
 */
function isReactOwned(): boolean {
  return getShellRouteByPageId('automations')?.kind === 'react';
}

export const Route = createFileRoute('/automations')({
  validateSearch: automationsSearchSchema,
  beforeLoad: ({ context }) => {
    const { bridge } = context.shell;

    if (!bridge.isPageAllowed('automations')) {
      throw redirect({ href: getProfileHomePath(bridge), replace: true });
    }
  },
  loader: async ({ context }) => {
    if (!isReactOwned()) return;

    const { profile } = context.shell;
    const { queryClient } = context;

    // Both feed the first paint: the sections need the list, and the stats bar
    // renders the master toggle beside the counts. Progress is deliberately
    // NOT prefetched — it is a catch-up read for runs already in flight, and
    // blocking paint on it would delay the page for the common idle case.
    // allSettled, not all: this loader WARMS the cache for the first paint, it
    // does not gate the route. A rejection here would hand the page to
    // defaultErrorComponent ("Something went wrong") on any backend hiccup,
    // where the vanilla page stayed usable. The components read the same
    // failures through useQuery and render their own error states.
    await Promise.allSettled([
      queryClient.ensureQueryData(automationsListQueryOptions(profile.profileId)),
      queryClient.ensureQueryData(automationsMasterQueryOptions()),
    ]);
  },
  component: AutomationsRouteComponent,
});

function AutomationsRouteComponent() {
  if (!isReactOwned()) {
    return <LegacyRouteController pathname="/automations" />;
  }
  return <AutomationsPage />;
}
