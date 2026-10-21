import { createFileRoute } from '@tanstack/react-router';

import { guardPageAccess } from '@/platform/shell/route-guard';

import { WishlistPage } from './-ui/wishlist-page';
import { isVideoMedia } from './-video.types';
import {
  wishlistArtistPhotosQueryOptions,
  wishlistCycleQueryOptions,
  wishlistStatsQueryOptions,
  wishlistTracksQueryOptions,
} from './-wishlist.api';
import { wishlistSearchSchema } from './-wishlist.types';

export const Route = createFileRoute('/wishlist')({
  validateSearch: wishlistSearchSchema,
  beforeLoad: ({ context, search }) => {
    // the video tabs are the video side's wishlist: the shell asks for the
    // video-wishlist permission (and the video side) instead of the music one.
    guardPageAccess(context.shell.bridge, 'wishlist', `?media=${search.media}`);
  },
  loaderDeps: ({ search }) => ({ media: search.media }),
  loader: async ({ context, deps }) => {
    // the video tabs fetch their own; warming the music lists would be waste.
    if (isVideoMedia(deps.media)) return;
    const { profile } = context.shell;
    const { queryClient } = context;

    // All five feed the first paint: the nebula needs both track categories,
    // the header needs stats + cycle, and the orbs want the artist photos.
    // allSettled, not all: this loader WARMS the cache for the first paint, it
    // does not gate the route. A rejection here would hand the page to
    // defaultErrorComponent ("Something went wrong") on any backend hiccup,
    // where the vanilla page stayed usable. The components read the same
    // failures through useQuery and render their own error states.
    await Promise.allSettled([
      queryClient.ensureQueryData(wishlistStatsQueryOptions(profile.profileId)),
      queryClient.ensureQueryData(wishlistCycleQueryOptions(profile.profileId)),
      queryClient.ensureQueryData(wishlistTracksQueryOptions(profile.profileId, 'albums')),
      queryClient.ensureQueryData(wishlistTracksQueryOptions(profile.profileId, 'singles')),
      queryClient.ensureQueryData(wishlistArtistPhotosQueryOptions(profile.profileId)),
    ]);
  },
  component: WishlistPage,
});
