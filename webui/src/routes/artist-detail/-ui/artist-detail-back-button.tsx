import { useRouter } from '@tanstack/react-router';

import { backButtonLabel, backOriginHref, popBackOrigin } from '../-artist-detail.back-label';

/**
 * The page-header Back button (index.html #artist-detail-back-btn, wired in
 * initializeArtistDetailPage).
 *
 * That markup lives inside the vanilla #artist-detail-page, which the React
 * host replaces — so without this the button simply disappeared.
 *
 * The label is the smart one, not a bare "Back": arrivals from a still-vanilla
 * page (search, label detail, enrichment) push their origin onto
 * artistDetailLabelStack inside navigateToArtistDetail, and artist → artist
 * hops push the previous artist's name. It reads "Back to Search",
 * "Back to Aphex Twin", or plain "Back" on a cold load.
 *
 * Navigation uses the URL stored with the label entry. Browser history cannot
 * express an Artist → Album → Artist chain reliably and caused loops when a
 * redirect replaced one of those entries.
 */
export function ArtistDetailBackButton({ label }: { label?: string }) {
  const router = useRouter();

  return (
    <div className="page-header">
      <button
        type="button"
        className="back-btn"
        id="artist-detail-back-btn"
        onClick={() => {
          void router.navigate({ href: backOriginHref(popBackOrigin()) });
        }}
      >
        <span>{label ?? backButtonLabel()}</span>
      </button>
    </div>
  );
}
