/**
 * The Similar Artists section at the foot of the page.
 *
 * This markup lived inside the vanilla #artist-detail-page, which the React
 * host replaces — so the section disappeared entirely and loadSimilarArtists
 * had nothing to render into (it resolves four elements by id and bails when
 * the section is absent).
 *
 * React renders the shell and nothing else: the vanilla loader owns the
 * loading/error toggles and fills the bubbles container itself. The container
 * is deliberately left EMPTY here, so React never reconciles away nodes it did
 * not create.
 */
export function SimilarArtistsSection() {
  return (
    <div className="similar-artists-section" id="ad-similar-artists-section">
      <div className="similar-artists-header">
        <h3 className="similar-artists-title">Similar Artists</h3>
        <p className="similar-artists-subtitle">Discover artists with a similar sound</p>
      </div>
      <div className="similar-artists-loading hidden" id="ad-similar-artists-loading">
        <div className="loading-spinner-small" />
        <span>Finding similar artists...</span>
      </div>
      <div className="similar-artists-error hidden" id="ad-similar-artists-error">
        <span className="error-icon">⚠️</span>
        <span className="error-text">Unable to load similar artists</span>
      </div>
      <div
        className="similar-artists-bubbles-container"
        id="ad-similar-artists-bubbles-container"
      />
    </div>
  );
}
