import { createFileRoute, redirect } from '@tanstack/react-router';

import { getProfileHomePath } from '@/platform/shell/bridge';
import { LegacyRouteController } from '@/platform/shell/route-controllers';
import { getShellRouteByPageId } from '@/platform/shell/route-manifest';

import { WishlistPage } from './-ui/wishlist-page';
import {
  wishlistArtistPhotosQueryOptions,
  wishlistCycleQueryOptions,
  wishlistStatsQueryOptions,
  wishlistTracksQueryOptions,
} from './-wishlist.api';
import { wishlistSearchSchema } from './-wishlist.types';

/**
 * Whether the shell has handed /wishlist over to React yet.
 *
 * The route file exists (and is tested) before the React page reaches parity.
 * Without this check TanStack would match /wishlist regardless of the manifest
 * and the vanilla page and the React host would both activate. Delete this
 * indirection once the vanilla wishlist page is gone.
 */
function isReactOwned(): boolean {
  return getShellRouteByPageId('wishlist')?.kind === 'react';
}

export const Route = createFileRoute('/wishlist')({
  validateSearch: wishlistSearchSchema,
  beforeLoad: ({ context }) => {
    const { bridge } = context.shell;

    if (!bridge.isPageAllowed('wishlist')) {
      throw redirect({ href: getProfileHomePath(bridge), replace: true });
    }
  },
  loader: async ({ context }) => {
    if (!isReactOwned()) return;

    const { profile } = context.shell;
    const { queryClient } = context;

    // All five feed the first paint: the nebula needs both track categories,
    // the header needs stats + cycle, and the orbs want the artist photos.
    await Promise.all([
      queryClient.ensureQueryData(wishlistStatsQueryOptions(profile.profileId)),
      queryClient.ensureQueryData(wishlistCycleQueryOptions(profile.profileId)),
      queryClient.ensureQueryData(wishlistTracksQueryOptions(profile.profileId, 'albums')),
      queryClient.ensureQueryData(wishlistTracksQueryOptions(profile.profileId, 'singles')),
      queryClient.ensureQueryData(wishlistArtistPhotosQueryOptions(profile.profileId)),
    ]);
  },
  component: WishlistRouteComponent,
});

function WishlistRouteComponent() {
  if (!isReactOwned()) {
    return <LegacyRouteController pathname="/wishlist" />;
  }
  return <WishlistPage />;
}
