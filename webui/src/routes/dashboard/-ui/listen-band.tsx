/**
 * The Listen band (dash-card data-card="listen") — the dashboard's payoff
 * section. Everything above it is about OWNING music; this row is about
 * playing it. Boxless like the rails (the content is the chrome).
 *
 * - Library Radio hero: the endless own-collection shuffle that lived buried
 *   in the media player finally gets a front door (startLibraryRadio seam,
 *   media-player.js:3129).
 * - Mixes tile: the doorway to Discover's daily mixes.
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
          <span className="listen-hero-icon">
            <svg
              width="26"
              height="26"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <circle cx="12" cy="12" r="2" />
              <path d="M16.24 7.76a6 6 0 0 1 0 8.49" />
              <path d="M7.76 16.24a6 6 0 0 1 0-8.49" />
              <path d="M19.07 4.93a10 10 0 0 1 0 14.14" />
              <path d="M4.93 19.07a10 10 0 0 1 0-14.14" />
            </svg>
          </span>
          <span className="listen-hero-text">
            <strong>Library Radio</strong>
            <span>Endless shuffle through your own collection — one click, infinite queue.</span>
          </span>
          <span className="listen-hero-play">▶</span>
        </button>
        <button
          type="button"
          className="listen-tile"
          onClick={() => void window.SoulSyncWebRouter?.navigateToPage('discover')}
        >
          <span className="listen-tile-title">Your Mixes</span>
          <span className="listen-tile-sub">
            Daily playlists built from your library — archives, discoveries, decades.
          </span>
          <span className="listen-tile-arrow">→</span>
        </button>
      </div>
    </article>
  );
}
