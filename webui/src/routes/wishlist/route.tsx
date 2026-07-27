import { createFileRoute, redirect } from '@tanstack/react-router';

import { getProfileHomePath } from '@/platform/shell/bridge';

import { WishlistPage } from './-ui/wishlist-page';
import {
  wishlistArtistPhotosQueryOptions,
  wishlistCycleQueryOptions,
  wishlistStatsQueryOptions,
  wishlistTracksQueryOptions,
} from './-wishlist.api';
import { wishlistSearchSchema } from './-wishlist.types';

export const Route = createFileRoute('/wishlist')({
  validateSearch: wishlistSearchSchema,
  beforeLoad: ({ context }) => {
    const { bridge } = context.shell;

    if (!bridge.isPageAllowed('wishlist')) {
      throw redirect({ href: getProfileHomePath(bridge), replace: true });
    }
  },
  loader: async ({ context }) => {
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
  component: WishlistPage,
});
