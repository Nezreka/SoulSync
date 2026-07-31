/**
 * The Artist Map hub — the three cards that open the map.
 *
 * Transcribed from `webui/index.html` 4306-4362. The classes are unchanged
 * because `style.css` still owns the look; only the `onclick` attributes become
 * props.
 */

export interface ArtistMapHubProps {
  onOpenWatchlist(): void;
  onOpenGenre(): void;
  onOpenExplorer(): void;
}

const ARROW = (
  <svg
    className="artmap-hub-card-arrow"
    width="16"
    height="16"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
  >
    <path d="M5 12h14" />
    <path d="M12 5l7 7-7 7" />
  </svg>
);

export function ArtistMapHub({ onOpenWatchlist, onOpenGenre, onOpenExplorer }: ArtistMapHubProps) {
  return (
    <div className="artmap-hub">
      <div className="artmap-hub-bg" />
      <div className="artmap-hub-content">
        <div className="artmap-hub-header">
          <svg
            className="artmap-hub-icon"
            width="28"
            height="28"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
          >
            <circle cx="12" cy="12" r="3" />
            <circle cx="4" cy="6" r="2" />
            <circle cx="20" cy="6" r="2" />
            <circle cx="4" cy="18" r="2" />
            <circle cx="20" cy="18" r="2" />
            <line x1="6" y1="7" x2="10" y2="10" />
            <line x1="14" y1="10" x2="18" y2="7" />
            <line x1="6" y1="17" x2="10" y2="14" />
            <line x1="14" y1="14" x2="18" y2="17" />
          </svg>
          <div>
            <h2 className="artmap-hub-title">Artist Map</h2>
            <p className="artmap-hub-subtitle">Explore the connections between your artists</p>
          </div>
        </div>
        <div className="artmap-hub-cards">
          <button type="button" className="artmap-hub-card" onClick={onOpenWatchlist}>
            <div className="artmap-hub-card-icon">
              <svg
                width="32"
                height="32"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
              >
                <path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z" />
              </svg>
            </div>
            <div className="artmap-hub-card-text">
              <h3>Watchlist</h3>
              <p>Your watched artists and their similar connections</p>
            </div>
            {ARROW}
          </button>
          <button type="button" className="artmap-hub-card" onClick={onOpenGenre}>
            <div className="artmap-hub-card-icon">
              <svg
                width="32"
                height="32"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
              >
                <circle cx="12" cy="12" r="10" />
                <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
                <line x1="2" y1="12" x2="22" y2="12" />
              </svg>
            </div>
            <div className="artmap-hub-card-text">
              <h3>Genres</h3>
              <p>Artists clustered by genre across your library and cache</p>
            </div>
            {ARROW}
          </button>
          <button type="button" className="artmap-hub-card" onClick={onOpenExplorer}>
            <div className="artmap-hub-card-icon">
              <svg
                width="32"
                height="32"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
              >
                <circle cx="11" cy="11" r="8" />
                <line x1="21" y1="21" x2="16.65" y2="16.65" />
                <line x1="11" y1="8" x2="11" y2="14" />
                <line x1="8" y1="11" x2="14" y2="11" />
              </svg>
            </div>
            <div className="artmap-hub-card-text">
              <h3>Explorer</h3>
              <p>Pick any artist and explore outward through their connections</p>
            </div>
            {ARROW}
          </button>
        </div>
      </div>
    </div>
  );
}
