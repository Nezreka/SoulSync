import { createFileRoute } from '@tanstack/react-router';
import { useLayoutEffect } from 'react';
import { z } from 'zod';

import { useShellBridge } from '@/platform/shell/route-controllers';

// Some metadata sources (Bandcamp) have no numeric-ID lookup API at all —
// they're addressed entirely by URL/name — so the artist's display name has
// to travel as a search param. Sources that can resolve by ID alone just
// don't need it; this is a no-op for them.
//
// TanStack's search parser JSON-parses param values, so an all-digits artist
// name ("311", "702") arrives as a NUMBER — a bare z.string() then throws
// SearchParamError and the whole route dies in the error boundary (clicking
// the artist did nothing). Coerce whatever arrives back to a string.
const artistDetailSearchSchema = z.object({
  name: z
    .preprocess((v) => (v == null ? '' : String(v)), z.string())
    .optional()
    .default(''),
});

export const Route = createFileRoute('/artist-detail/$source/$id')({
  validateSearch: artistDetailSearchSchema,
  component: ArtistDetailPage,
});

// Thin legacy handoff: TanStack owns the URL shape here, but the vanilla JS
// artist-detail page still renders the actual experience for now. The route
// owns cancellation so similar-artist loading stops when this page changes.
function ArtistDetailPage() {
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
