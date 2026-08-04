import { createFileRoute, redirect } from '@tanstack/react-router';

import { artistDetailSearchSchema } from '../-artist-detail.types';

/**
 * ldp-01: every artist URL lands in Library V2.
 *
 * Six callers build this URL through `buildArtistDetailPath` (`init.js:2988`) —
 * search, global search, media player, playlist sync, similar-artist bubbles,
 * stats. Redirecting in the ROUTE covers all of them at once and keeps existing
 * links and browser history working, which is why the redirect lives here and
 * not in each caller.
 *
 * `library:<numeric id>` opens the owned catalogue row through `artist=<id>`.
 * `discover=<source>:<id>` is the provider discovery mode: V2 renders an artist
 * that has no catalogue row yet, read-only, and materialises one only when the
 * user bookmarks the artist or opens a release (ldp-02/ldp-06).
 *
 * Withdrawn during the 2026-07-31 upstream sync because upstream had ported its
 * own artist page onto this route; restored deliberately afterwards, because
 * losing it regressed the search → Library V2 flow this fork exists for.
 * Upstream's page is unreachable by design — docs/library-v2-status.md §46.
 */
export const Route = createFileRoute('/artist-detail/$source/$id')({
  validateSearch: artistDetailSearchSchema,
  beforeLoad: ({ params, search }) => {
    const source = params.source.toLowerCase();
    const ownedArtistId =
      source === 'library' && /^\d+$/.test(params.id) ? Number(params.id) : null;
    throw redirect({
      to: '/library',
      search:
        ownedArtistId == null
          ? {
              discover: `${source}:${params.id}`,
              discoverName: search.name || undefined,
              // Coming from a search result means landing on what the legacy artist
              // page showed: the full discography, as cards, with the rich header
              // (ldp-05).
              releases: 'all' as const,
              releaseView: 'cards' as const,
              header: 'rich' as const,
            }
          : { artist: ownedArtistId },
      replace: true,
    });
  },
});
