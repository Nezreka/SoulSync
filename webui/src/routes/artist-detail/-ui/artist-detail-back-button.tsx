import { useRouter } from '@tanstack/react-router';

/**
 * The page-header Back button (index.html #artist-detail-back-btn, wired in
 * initializeArtistDetailPage).
 *
 * That markup lives inside the vanilla #artist-detail-page, which the React
 * host replaces — so without this the button simply disappeared.
 *
 * Browser history first, exactly as the vanilla did, with a Library fallback
 * for a cold load straight onto an artist URL where going "back" would leave
 * SoulSync entirely.
 *
 * The vanilla also relabelled this "← Back to <artist>" from its own
 * _artistDetailLabelStack. Nothing pushes to that stack any more — React owns
 * the navigation now — so it would read "← Back" in every case regardless,
 * which is the vanilla's own empty-stack label.
 */
export function ArtistDetailBackButton() {
  const router = useRouter();

  return (
    <div className="page-header">
      <button
        type="button"
        className="back-btn"
        id="artist-detail-back-btn"
        onClick={() => {
          if (window.history.length > 1) {
            router.history.back();
            return;
          }
          void router.navigate({ to: '/library' });
        }}
      >
        <span>← Back</span>
      </button>
    </div>
  );
}
