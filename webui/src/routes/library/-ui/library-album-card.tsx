import { useEffect, useRef, useState } from 'react';

import { thumb } from '@/platform/artwork-thumb';

import type { LibraryAlbum } from '../-library.types';

import {
  buildAlbumBadges,
  cardAnimationDelay,
  splitBadgeColumns,
  trackCountLabel,
} from '../-library.helpers';
import { BadgeIcon, badgeClickHandler } from './library-artist-card';

interface Props {
  album: LibraryAlbum;
  index: number;
  href: string;
  /** Replace the player queue with this album's owned tracks. */
  onPlay?: () => void;
  /** True while that tracklist is being resolved — the button shows "…". */
  playPending?: boolean;
}

/**
 * The album cover, with the artist card's fade-in but none of its fallback
 * chain: an album has no second source to try, so a missing cover goes
 * straight to the placeholder.
 */
function AlbumImage({ album }: { album: LibraryAlbum }) {
  const art = album.thumb_url?.trim();
  const [broken, setBroken] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const imgRef = useRef<HTMLImageElement | null>(null);

  useEffect(() => {
    setLoaded(false);
    // A cached image can be complete before onLoad is wired; read it off the
    // element so a paged-back card does not sit invisible.
    const el = imgRef.current;
    if (el && el.complete && el.naturalWidth > 0) setLoaded(true);
  }, [art]);

  if (!art || broken) return <div className="library-artist-image-fallback">💿</div>;

  return (
    <img
      ref={imgRef}
      src={thumb(art, 'grid')}
      alt={album.title}
      loading="lazy"
      className={loaded ? 'is-loaded' : 'is-loading'}
      onLoad={() => setLoaded(true)}
      onError={() => setBroken(true)}
    />
  );
}

/**
 * One album tile.
 *
 * It wears the ARTIST card's classes rather than carrying its own: the two
 * grids are meant to be indistinguishable apart from what is on the tile, and
 * a second near-identical set of rules would be two places to keep in step.
 * `library-album-card` is only a hook for the few album-specific bits.
 */
export function LibraryAlbumCard({ album, index, href, onPlay, playPending }: Props) {
  const badges = buildAlbumBadges(album);
  const { primary, overflow, needsOverflow } = splitBadgeColumns(badges);
  const tracks = trackCountLabel(album.track_count);
  const onPlayClick = badgeClickHandler(playPending ? undefined : onPlay);

  return (
    <a
      className="library-artist-card library-album-card"
      href={href}
      data-album-id={String(album.id)}
      data-album-name={album.title}
      style={{
        position: 'relative',
        display: 'block',
        animation: `cardFadeIn 0.35s cubic-bezier(0.4,0,0.2,1) ${cardAnimationDelay(index)}ms both`,
        textDecoration: 'none',
        color: 'inherit',
      }}
    >
      {badges.length > 0 ? (
        needsOverflow ? (
          // The overflow column renders FIRST, as on the artist card — the CSS
          // positions them, so swapping them moves the badges on screen.
          <div className="card-badge-container">
            <div className="badge-overflow-column">
              {overflow.map((b) => (
                <BadgeIcon key={b.key} badge={b} />
              ))}
            </div>
            <div className="badge-primary-column">
              {primary.map((b) => (
                <BadgeIcon key={b.key} badge={b} />
              ))}
            </div>
          </div>
        ) : (
          <div className="card-badge-container">
            {primary.map((b) => (
              <BadgeIcon key={b.key} badge={b} />
            ))}
          </div>
        )
      ) : null}

      <div className="library-artist-image">
        <AlbumImage album={album} />
      </div>

      <span
        className="library-artist-play-btn"
        role="button"
        tabIndex={0}
        aria-label={`Play ${album.title}`}
        aria-disabled={playPending || undefined}
        title={`Play ${album.title}`}
        onClick={onPlayClick}
        onKeyDown={(e) => {
          if (e.key !== 'Enter' && e.key !== ' ') return;
          e.preventDefault();
          e.stopPropagation();
          if (!playPending) onPlay?.();
        }}
      >
        {playPending ? '…' : '▶'}
      </span>

      <div className="library-artist-info">
        <h3 className="library-artist-name" title={album.title}>
          {album.title}
        </h3>
        <div className="library-artist-stats">
          <span className="library-artist-stat">{album.artist_name}</span>
          {album.year ? <span className="library-artist-stat">{album.year}</span> : null}
          {tracks ? <span className="library-artist-stat">{tracks}</span> : null}
        </div>
      </div>
    </a>
  );
}
