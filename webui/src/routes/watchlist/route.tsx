import { createFileRoute, redirect } from '@tanstack/react-router';

import { getProfileHomePath } from '@/platform/shell/bridge';

import { WatchlistPage } from './-ui/watchlist-page';
import {
  watchlistArtistsQueryOptions,
  watchlistCountQueryOptions,
  watchlistGlobalConfigQueryOptions,
  watchlistLabelsQueryOptions,
  watchlistScanStatusQueryOptions,
} from './-watchlist.api';
import { watchlistSearchSchema } from './-watchlist.types';

export const Route = createFileRoute('/watchlist')({
  validateSearch: watchlistSearchSchema,
  beforeLoad: ({ context }) => {
    const { bridge } = context.shell;

    if (!bridge.isPageAllowed('watchlist')) {
      throw redirect({ href: getProfileHomePath(bridge), replace: true });
    }
  },
  loaderDeps: ({ search }) => ({ tab: search.tab }),
  loader: async ({ context, deps }) => {
    const { profile } = context.shell;
    const { queryClient } = context;

    // The artist side always loads: the header count, the Next Auto chip and
    // the scan controls sit above the tabs and are visible on both tabs.
    const pending: Promise<unknown>[] = [
      queryClient.ensureQueryData(watchlistCountQueryOptions(profile.profileId)),
      queryClient.ensureQueryData(watchlistArtistsQueryOptions(profile.profileId)),
      queryClient.ensureQueryData(watchlistScanStatusQueryOptions(profile.profileId)),
      queryClient.ensureQueryData(watchlistGlobalConfigQueryOptions(profile.profileId)),
    ];

    // Labels are a separate blueprint and a separate round trip; only pay for
    // it when the Labels tab is the one being opened.
    if (deps.tab === 'labels') {
      pending.push(queryClient.ensureQueryData(watchlistLabelsQueryOptions(profile.profileId)));
    }

    // allSettled, not all: this loader WARMS the cache for the first paint, it
    // does not gate the route. A rejection here would hand the page to
    // defaultErrorComponent ("Something went wrong") on any backend hiccup,
    // where the vanilla page stayed usable. The components read the same
    // failures through useQuery and render their own error states.
    await Promise.allSettled(pending);
  },
  component: WatchlistPage,
});
