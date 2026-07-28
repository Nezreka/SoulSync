import { createFileRoute, redirect } from '@tanstack/react-router';
import { z } from 'zod';

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
    .preprocess(
      (v) =>
        typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean' ? String(v) : '',
      z.string(),
    )
    .optional()
    .default(''),
});

// ldp-01: this used to hand off to the vanilla-JS artist page, which is how a
// search result for an artist you don't own yet still landed a user in the
// legacy library. Every caller (search, global search, media player, playlist
// sync, similar-artist bubbles) navigates through this one URL, so redirecting
// here routes all of them into Library V2 at once — including the discovery
// mode for artists that have no catalogue row yet. The URL shape is kept so
// existing links and browser history keep working.
export const Route = createFileRoute('/artist-detail/$source/$id')({
  validateSearch: artistDetailSearchSchema,
  beforeLoad: ({ params, search }) => {
    throw redirect({
      to: '/library-v2',
      search: {
        discover: `${params.source.toLowerCase()}:${params.id}`,
        discoverName: search.name || undefined,
        // Coming from a search result means landing on what the legacy artist
        // page showed: the full discography, as cards, with the rich header.
        releases: 'all' as const,
        releaseView: 'cards' as const,
        header: 'rich' as const,
      },
      replace: true,
    });
  },
});
