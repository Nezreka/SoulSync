/**
 * The Listen band (dash-card data-card="listen") — the dashboard's payoff
 * section. Everything above it is about OWNING music; this row is about
 * playing it. Boxless like the rails (the content is the chrome).
 *
 * - Library Radio hero: the endless own-collection shuffle that lived buried
 *   in the media player finally gets a front door (startLibraryRadio seam,
 *   media-player.js:3129). Designed as a living station deck: spinning
 *   vinyl, dancing EQ, glowing transport button.
 * - Mixes tile: the doorway to Discover's daily mixes, with the three real
 *   mix categories (Archives, Discoveries, Decades) as rows.
 *
 * Deliberately static — no fetches, no state beyond the seams. The rails
 * above already play albums on click; this band covers the "just play me
 * something" and "take me to my mixes" intents.
 */

export function ListenBand() {
  return (
    <article className="dash-card dash-card--rail" data-card="listen">
      <div className="listen-band">
        <button
          type="button"
          className="listen-hero"
          onClick={() => void window.startLibraryRadio?.()}
        >
          <span className="listen-vinyl" aria-hidden="true" />
          <span className="listen-hero-text">
            <span className="listen-kicker">
              <span className="listen-eq" aria-hidden="true">
                <i />
                <i />
                <i />
                <i />
                <i />
              </span>
              On air — your collection
            </span>
            <strong>Library Radio</strong>
            <span>Endless shuffle through your own collection — one click, infinite queue.</span>
          </span>
          <span className="listen-hero-play" aria-hidden="true">
            ▶
          </span>
        </button>
        <button
          type="button"
          className="listen-tile"
          onClick={() => void window.navigateToPage?.('discover')}
        >
          <span className="listen-kicker listen-kicker--muted">Rebuilt daily</span>
          <span className="listen-tile-title">Your Mixes</span>
          <span className="listen-mixrows" aria-hidden="true">
            <span className="listen-mixrow">
              <i />
              Archives
            </span>
            <span className="listen-mixrow">
              <i />
              Discoveries
            </span>
            <span className="listen-mixrow">
              <i />
              Decades
            </span>
          </span>
          <span className="listen-tile-arrow">→</span>
        </button>
      </div>
    </article>
  );
}
