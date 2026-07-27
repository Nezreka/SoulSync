import { createFileRoute, redirect } from '@tanstack/react-router';

import { getProfileHomePath } from '@/platform/shell/bridge';
import { LegacyRouteController } from '@/platform/shell/route-controllers';
import { getShellRouteByPageId } from '@/platform/shell/route-manifest';

import { libraryArtistsQueryOptions } from './-library.api';
import { librarySearchSchema } from './-library.types';

/**
 * Whether the shell has handed /library over to React yet.
 *
 * The route file exists (and is tested) before the React page reaches parity.
 * Without this check TanStack would match /library regardless of the manifest
 * and the vanilla page and the React host would both activate. Delete this
 * indirection once the vanilla library page is gone.
 */
function isReactOwned(): boolean {
  return getShellRouteByPageId('library')?.kind === 'react';
}

export const Route = createFileRoute('/library')({
  validateSearch: librarySearchSchema,
  beforeLoad: ({ context }) => {
    const { bridge } = context.shell;

    if (!bridge.isPageAllowed('library')) {
      throw redirect({ href: getProfileHomePath(bridge), replace: true });
    }
  },
  loaderDeps: ({ search }) => search,
  loader: async ({ context, deps }) => {
    if (!isReactOwned()) return;

    const { profile } = context.shell;
    await context.queryClient.ensureQueryData(libraryArtistsQueryOptions(profile.profileId, deps));
  },
  component: LibraryRouteComponent,
});

function LibraryRouteComponent() {
  if (!isReactOwned()) {
    return <LegacyRouteController pathname="/library" />;
  }
  // The page component lands in P2. Until the manifest flips this branch is
  // unreachable, so deferring to the legacy controller keeps it honest.
  return <LegacyRouteController pathname="/library" />;
}
