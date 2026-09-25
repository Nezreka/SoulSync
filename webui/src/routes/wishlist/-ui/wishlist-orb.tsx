import { useRef, useState } from 'react';

import type { ParsedWishlistTrack, WishlistArtistGroup } from '../-wishlist.types';

import {
  artistHue,
  failingTitle,
  orbAnimationDelay,
  orbImage,
  orbImageFallback,
  orbRingCovers,
  orbSizeClass,
  trackCountLabel,
} from '../-wishlist.helpers';
import { WishlistCover } from './wishlist-cover';

interface Props {
  group: WishlistArtistGroup;
  index: number;
  artistImages: Map<string, string>;
  /** CDN photos to paint while a cold local artwork build runs. */
  artistImageFallbacks?: Map<string, string>;
  currentCycle: string;
  /** A wishlist run is in flight; the orb shows its working state. */
  processing: boolean;
  expanded: boolean;
  onToggleExpand: () => void;
  onRemoveAlbum: (albumName: string) => void;
  onRemoveTrack: (trackId: string) => void;
}

/** One artist orb plus its expanded album fan / singles orbit. */
export function WishlistOrb({
  group,
  index,
  artistImages,
  artistImageFallbacks,
  currentCycle,
  processing,
  expanded,
  onToggleExpand,
  onRemoveAlbum,
  onRemoveTrack,
}: Props) {
  // Which album tile is open. Local to this orb: the vanilla handler collapsed
  // tiles within `.wl-album-fan`, i.e. per artist, not globally.
  const [openAlbum, setOpenAlbum] = useState<string | null>(null);

  // Mount the heavy expanded subtree only once this orb has actually been
  // opened. `expanded` was only a CSS class, so EVERY orb mounted its whole
  // album fan and every track row up front — a 400-track wishlist rendered
  // the entire tracklist on first paint and re-reconciled it on each poll
  // settle (perf sweep, Aug 2026). Latched (a ref survives re-renders), so
  // closing keeps the DOM: the collapse animation and re-opens stay instant.
  const everExpandedRef = useRef(expanded);
  if (expanded) everExpandedRef.current = true;

  const image = orbImage(group, artistImages);
  const imageFallback = orbImageFallback(group, artistImages, artistImageFallbacks ?? new Map());
  const ringCovers = orbRingCovers(group);
  const hasAlbums = group.albums.length > 0;
  const pulse = hasAlbums && currentCycle === 'albums';

  return (
    <div
      className={`wl-orb-group${expanded ? ' expanded' : ''}${processing ? ' orb-processing' : ''}`}
      data-artist={group.name}
      data-failing={group.failingCount}
      style={{ animationDelay: `${orbAnimationDelay(index)}ms` }}
    >
      <div className="wl-orb-tooltip">
        {group.name}
        <br />
        <span>{trackCountLabel(group.total)}</span>
      </div>

      <div
        className={`wl-orb ${orbSizeClass(group.total)}${pulse ? ' orb-pulse' : ''}`}
        style={{ ['--orb-hue' as string]: artistHue(group.name) }}
        onClick={onToggleExpand}
      >
        <div className="wl-orb-glow" />
        <WishlistCover
          className="wl-orb-img"
          src={image}
          fallback={imageFallback}
          placeholder={
            <div className="wl-orb-initials">{group.name.substring(0, 2).toUpperCase()}</div>
          }
        />
        <div className="wl-orb-ring" />

        {ringCovers.length > 0 ? (
          <div className="wl-orb-art-ring">
            {ringCovers.map((url, i) => (
              <img
                key={`${url}-${i}`}
                className="wl-art-ring-item"
                src={url}
                alt=""
                style={{ ['--ring-angle' as string]: `${(360 / ringCovers.length) * i}deg` }}
              />
            ))}
          </div>
        ) : null}
      </div>

      {/* The label is a SIBLING of .wl-orb, not a child, so its click cannot
          reach the expand handler — stopPropagation here is belt-and-braces,
          carried over from the vanilla markup where it was equally redundant.
          Kept so a future nesting change cannot silently start toggling. */}
      <div
        className="wl-orb-label"
        title="View artist"
        onClick={(event) => {
          event.stopPropagation();
          window._navigateToArtistFromWishlist?.(group.name);
        }}
      >
        {group.name}
      </div>
      <div className="wl-orb-meta">
        {trackCountLabel(group.total)}
        {group.failingCount > 0 ? (
          <>
            {' · '}
            <span
              className="wl-orb-meta-failing"
              title={`${group.failingCount} track${
                group.failingCount !== 1 ? 's' : ''
              } repeatedly failing to download`}
            >
              ⚠ {group.failingCount} failing
            </span>
          </>
        ) : null}
      </div>

      <div className="wl-orb-expanded">
        {everExpandedRef.current && hasAlbums ? (
          <div className="wl-album-fan">
            {group.albums.map((album) => (
              <div
                key={album.name}
                className={`wl-album-tile${openAlbum === album.name ? ' tile-expanded' : ''}`}
                data-album={album.name}
                onClick={(event) => {
                  event.stopPropagation();
                  setOpenAlbum((current) => (current === album.name ? null : album.name));
                }}
              >
                <div className="wl-album-tile-art">
                  <WishlistCover
                    src={album.image}
                    fallback={album.imageFallback}
                    placeholder={<div className="wl-album-tile-fallback">💿</div>}
                  />
                </div>
                <div className="wl-album-tile-info">
                  <div className="wl-album-tile-name">{album.name}</div>
                  <div className="wl-album-tile-count">{trackCountLabel(album.tracks.length)}</div>
                </div>
                <span className="wl-album-tile-badge">{album.tracks.length}</span>
                <button
                  type="button"
                  className="wl-album-tile-remove"
                  title="Remove album"
                  aria-label={`Remove album ${album.name}`}
                  onClick={(event) => {
                    event.stopPropagation();
                    onRemoveAlbum(album.name);
                  }}
                >
                  ✕
                </button>

                <div className="wl-tile-tracks">
                  {album.tracks.map((track) => (
                    <TileTrack
                      key={track.id || track.track}
                      track={track}
                      onRemove={() => onRemoveTrack(track.id)}
                    />
                  ))}
                </div>
              </div>
            ))}
          </div>
        ) : null}

        {everExpandedRef.current && group.singles.length > 0 ? (
          <div className="wl-singles-orbit">
            {group.singles.map((single) => (
              <div
                key={single.id || single.track}
                className={`wl-single-moon${single.failing ? ' wl-moon-failing' : ''}`}
                data-track-id={single.id}
                title={single.failing ? failingTitle(single) : undefined}
              >
                <WishlistCover
                  src={single.image}
                  fallback={single.imageFallback}
                  placeholder={<span className="wl-moon-fallback">⭐</span>}
                />
                {single.failing ? <span className="wl-moon-failing-badge">⚠</span> : null}
                <div className="wl-moon-label">{single.track}</div>
                <button
                  type="button"
                  className="wl-moon-search-btn"
                  title="Search manually — pick a source yourself"
                  aria-label={`Search manually for ${single.track}`}
                  onClick={(event) => {
                    event.stopPropagation();
                    window._searchWishlistTrackManually?.(single.artist, single.track);
                  }}
                >
                  🔍
                </button>
                <button
                  type="button"
                  className="wl-moon-remove-btn"
                  title="Remove"
                  aria-label={`Remove ${single.track}`}
                  onClick={(event) => {
                    event.stopPropagation();
                    onRemoveTrack(single.id);
                  }}
                >
                  ✕
                </button>
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function TileTrack({ track, onRemove }: { track: ParsedWishlistTrack; onRemove: () => void }) {
  return (
    <div className={`wl-tile-track${track.failing ? ' wl-track-failing' : ''}`}>
      <span className="wl-tile-track-name">{track.track}</span>
      {track.failing ? (
        <span className="wl-failing-badge" title={failingTitle(track)}>
          ⚠ {track.retry}
        </span>
      ) : null}
      <button
        type="button"
        className="wl-tile-track-search"
        title="Search manually — pick a source yourself"
        aria-label={`Search manually for ${track.track}`}
        onClick={(event) => {
          event.stopPropagation();
          window._searchWishlistTrackManually?.(track.artist, track.track);
        }}
      >
        🔍
      </button>
      <button
        type="button"
        className="wl-tile-track-remove"
        title="Remove track"
        aria-label={`Remove ${track.track}`}
        onClick={(event) => {
          event.stopPropagation();
          onRemove();
        }}
      >
        ✕
      </button>
    </div>
  );
}
