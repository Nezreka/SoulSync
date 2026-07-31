import { useState } from 'react';

import type { YourArtist } from '../-discover.your-artists';

import {
  artistSourceBadges,
  pickArtistDetailSource,
  sourceColor,
  watchlistButtonTitle,
} from '../-discover.your-artists';
import { DiscoverSection } from './discover-section';

/**
 * The Your Artists shelf.
 *
 * Transcribed from index.html 4628-4650 for the section and discover.js
 * 5295-5354 for the card.
 *
 * Two things on this card look interchangeable and are not. The BADGES say
 * "there is an id for this provider" and appear in a fixed order regardless of
 * which source is active; the ORIGIN DOTS say "this artist came from this
 * service" and follow the payload. Merging them loses both meanings.
 */

/** Logo urls the vanilla holds as globals (5301-5304). */
export interface SourceLogos {
  spotify?: string;
  itunes?: string;
  deezer?: string;
  discogs?: string;
}

export interface YourArtistsShelfProps {
  artists: YourArtist[];
  loaded: boolean;
  subtitle: string;
  logos: SourceLogos;
  refreshing?: boolean;
  buildDetailPath: (id: string, source: string) => string;
  onRefresh: () => void;
  onConfigureSources: () => void;
  onViewAll: () => void;
  onOpenInfo: (artist: YourArtist) => void;
  onToggleWatchlist: (artist: YourArtist) => void;
}

export function YourArtistsShelf({
  artists,
  loaded,
  subtitle,
  logos,
  refreshing,
  buildDetailPath,
  onRefresh,
  onConfigureSources,
  onViewAll,
  onOpenInfo,
  onToggleWatchlist,
}: YourArtistsShelfProps) {
  return (
    <DiscoverSection
      id="your-artists-section"
      title="Your Artists"
      subtitle={subtitle}
      count={artists.length}
      loaded={loaded}
      actions={
        <>
          <button
            type="button"
            className="btn btn--sm btn--secondary ya-header-btn ya-refresh-btn"
            id="your-artists-refresh-btn"
            title="Refresh from services"
            aria-label="Refresh from services"
            disabled={refreshing}
            onClick={onRefresh}
          >
            <RefreshIcon />
          </button>
          <button
            type="button"
            className="btn btn--sm btn--secondary ya-header-btn ya-settings-btn"
            title="Configure sources"
            aria-label="Configure sources"
            onClick={onConfigureSources}
          >
            <GearIcon />
          </button>
          <button
            type="button"
            className="btn btn--sm btn--secondary ya-header-btn ya-viewall-btn"
            onClick={onViewAll}
          >
            <span>View All</span>
            <ArrowIcon />
          </button>
        </>
      }
    >
      <div className="discover-carousel" id="your-artists-carousel">
        {artists.map((artist) => (
          <YourArtistCard
            key={String(artist.id ?? artist.artist_name)}
            artist={artist}
            logos={logos}
            buildDetailPath={buildDetailPath}
            onOpenInfo={onOpenInfo}
            onToggleWatchlist={onToggleWatchlist}
          />
        ))}
      </div>
    </DiscoverSection>
  );
}

interface YourArtistCardProps {
  artist: YourArtist;
  logos: SourceLogos;
  buildDetailPath: (id: string, source: string) => string;
  onOpenInfo: (artist: YourArtist) => void;
  onToggleWatchlist: (artist: YourArtist) => void;
}

function YourArtistCard({
  artist,
  logos,
  buildDetailPath,
  onOpenInfo,
  onToggleWatchlist,
}: YourArtistCardProps) {
  const [imageFailed, setImageFailed] = useState(false);
  const [triedDeezer, setTriedDeezer] = useState(false);
  const detail = pickArtistDetailSource(artist);
  const hasId = detail.id !== '';
  const badges = artistSourceBadges(artist);
  const sources = artist.source_services ?? [];
  const watching = Boolean(artist.on_watchlist);

  // The vanilla retries a failed image against Deezer's own artist endpoint
  // BEFORE giving up — a stale Spotify url is the common case, and Deezer will
  // usually still serve a picture for the same artist.
  const deezerId = artist.deezer_artist_id;
  const src =
    triedDeezer && deezerId
      ? `https://api.deezer.com/artist/${String(deezerId)}/image?size=big`
      : (artist.image_url ?? '');

  const onImageError = () => {
    if (deezerId && !triedDeezer) setTriedDeezer(true);
    else setImageFailed(true);
  };

  return (
    <div
      className="ya-card"
      // Only clickable when something can actually be opened; a dead card that
      // swallows clicks is worse than an inert one.
      onClick={hasId ? () => onOpenInfo(artist) : undefined}
    >
      <div className="ya-card-img">
        {src && !imageFailed && <img src={src} alt="" loading="lazy" onError={onImageError} />}
        <div
          className="ya-card-placeholder"
          style={src && !imageFailed ? { display: 'none' } : undefined}
        >
          ♫
        </div>
      </div>
      <div className="ya-card-gradient" />
      <div className="ya-card-badges">
        {badges.map((badge) => (
          <SourceBadgeChip key={badge.key} badge={badge} logos={logos} />
        ))}
      </div>
      <button
        type="button"
        className={watching ? 'ya-watchlist-btn active' : 'ya-watchlist-btn'}
        title={watchlistButtonTitle(artist.on_watchlist)}
        aria-label={watchlistButtonTitle(artist.on_watchlist)}
        onClick={(e) => {
          // Without this the card's own click fires too and opens the info modal
          // on top of the toggle.
          e.stopPropagation();
          onToggleWatchlist(artist);
        }}
      >
        <svg
          width="14"
          height="14"
          viewBox="0 0 24 24"
          fill={watching ? 'currentColor' : 'none'}
          stroke="currentColor"
          strokeWidth="2"
        >
          <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
          <circle cx="12" cy="12" r="3" />
        </svg>
      </button>
      <div className="ya-card-info">
        <div className="ya-card-info-row">
          <div className="ya-origin-dots">
            {sources.map((s) => (
              <span
                key={s}
                className="ya-origin-dot"
                style={{ background: sourceColor(s) }}
                title={`From ${s}`}
              />
            ))}
          </div>
        </div>
        {hasId ? (
          <a
            className="ya-card-name"
            href={buildDetailPath(detail.id, detail.source)}
            style={{ display: 'block', textDecoration: 'none', color: 'inherit' }}
            onClick={(e) => e.stopPropagation()}
          >
            {artist.artist_name}
          </a>
        ) : (
          <div className="ya-card-name">{artist.artist_name}</div>
        )}
      </div>
    </div>
  );
}

function SourceBadgeChip({
  badge,
  logos,
}: {
  badge: { key: string; fallback: string; title: string };
  logos: SourceLogos;
}) {
  const [failed, setFailed] = useState(false);
  const logo = logos[badge.key as keyof SourceLogos];
  return (
    <div className="ya-badge" title={badge.title}>
      {logo && !failed ? (
        <img src={logo} alt={badge.title} onError={() => setFailed(true)} />
      ) : (
        <span>{badge.fallback}</span>
      )}
    </div>
  );
}

// ── Shared header icons ──────────────────────────────────────────────────────

export function RefreshIcon() {
  return (
    <svg
      width="13"
      height="13"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.5"
    >
      <path d="M23 4v6h-6" />
      <path d="M1 20v-6h6" />
      <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
    </svg>
  );
}

export function GearIcon() {
  return (
    <svg
      width="13"
      height="13"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.5"
    >
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </svg>
  );
}

export function ArrowIcon() {
  return (
    <svg
      width="12"
      height="12"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.5"
    >
      <path d="M5 12h14" />
      <path d="M12 5l7 7-7 7" />
    </svg>
  );
}
