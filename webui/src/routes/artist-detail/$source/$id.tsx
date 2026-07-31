import { createFileRoute } from '@tanstack/react-router';
import { useLayoutEffect } from 'react';

import { useShellBridge } from '@/platform/shell/route-controllers';
import { getShellRouteByPageId } from '@/platform/shell/route-manifest';

import { artistDetailSearchSchema } from '../-artist-detail.types';
import { ArtistDetailPage } from '../-ui/artist-detail-page';

/**
 * Whether the shell has handed artist detail over to React yet.
 *
 * TanStack matches from the generated route tree, not the manifest, so this
 * route matches the moment the file exists. Without this guard the React page
 * and the vanilla page would both activate on the same URL. Delete the
 * indirection once the vanilla artist-detail page is gone.
 *
 * The search schema moved to -artist-detail.types.ts, where its preprocess is
 * unit-tested: TanStack JSON-parses search values, so an all-digits artist
 * name ("311", "702") arrives as a NUMBER and a bare z.string() throws
 * SearchParamError, killing the route. That has regressed once already.
 */
function isReactOwned(): boolean {
  return getShellRouteByPageId('artist-detail')?.kind === 'react';
}

export const Route = createFileRoute('/artist-detail/$source/$id')({
  validateSearch: artistDetailSearchSchema,
  component: ArtistDetailRouteComponent,
});

function ArtistDetailRouteComponent() {
  if (!isReactOwned()) {
    return <LegacyArtistDetailHandoff />;
  }
  return <ArtistDetailPage />;
}

/**
 * Thin legacy handoff: TanStack owns the URL shape, but the vanilla JS page
 * still renders the experience. The route owns cancellation so similar-artist
 * loading stops when this page changes.
 */
function LegacyArtistDetailHandoff() {
  const bridge = useShellBridge();
  const { source, id } = Route.useParams();
  const { name } = Route.useSearch();

  useLayoutEffect(() => {
    if (!bridge) return;

    const normalizedSource = source.toLowerCase() === 'library' ? null : source.toLowerCase();
    bridge.navigateToArtistDetail(id, name, normalizedSource, {
      skipRouteChange: true,
    });

    return () => {
      bridge.cancelSimilarArtistsLoad();
    };
  }, [bridge, id, source, name]);

  return null;
}
