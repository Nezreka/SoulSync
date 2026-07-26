import { createFileRoute, redirect } from '@tanstack/react-router';

import { getProfileHomePath } from '@/platform/shell/bridge';
import { LegacyRouteController } from '@/platform/shell/route-controllers';
import { getShellRouteByPageId } from '@/platform/shell/route-manifest';

import { WatchlistPage } from './-ui/watchlist-page';
import {
  watchlistArtistsQueryOptions,
  watchlistCountQueryOptions,
  watchlistGlobalConfigQueryOptions,
  watchlistLabelsQueryOptions,
  watchlistScanStatusQueryOptions,
} from './-watchlist.api';
import { watchlistSearchSchema } from './-watchlist.types';

/**
 * Whether the shell has handed /watchlist over to React yet.
 *
 * The route file exists (and is tested) before the React page reaches parity
 * with the vanilla one. Without this check TanStack would match /watchlist to
 * the React route regardless of the manifest, and the vanilla page and the
 * React host would both activate — so until the manifest says `react`, this
 * route renders the legacy page exactly as the `/$` splat route would.
 *
 * Delete this indirection once the vanilla watchlist page is gone.
 */
function isReactOwned(): boolean {
  return getShellRouteByPageId('watchlist')?.kind === 'react';
}

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
    if (!isReactOwned()) return;

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

    await Promise.all(pending);
  },
  component: WatchlistRouteComponent,
});

function WatchlistRouteComponent() {
  if (!isReactOwned()) {
    return <LegacyRouteController pathname="/watchlist" />;
  }
  return <WatchlistPage />;
}
