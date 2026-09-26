import { useRouter } from '@tanstack/react-router';

import { backButtonLabel, popBackOrigin } from '../-artist-detail.back-label';

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
 * Browser history first, as the vanilla did, with a Library fallback for a cold
 * load straight onto an artist url where going back would leave SoulSync.
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
          if (window.history.length > 1) {
            // The stack follows you back down the chain, so the next label
            // describes where THAT page came from.
            popBackOrigin();
            router.history.back();
            return;
          }
          const origin = popBackOrigin();
          void router.navigate({
            to: origin?.type === 'page' && origin.pageId ? `/${origin.pageId}` : '/library',
          });
        }}
      >
        <span>{label ?? backButtonLabel()}</span>
      </button>
    </div>
  );
}
