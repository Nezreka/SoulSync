import type { ParsedWishlistTrack, WishlistArtistGroup } from '../-wishlist.types';

import {
  artistHue,
  failingTitle,
  orbAnimationDelay,
  orbImage,
  orbRingCovers,
  orbSizeClass,
  trackCountLabel,
} from '../-wishlist.helpers';

interface Props {
  group: WishlistArtistGroup;
  index: number;
  artistImages: Map<string, string>;
  currentCycle: string;
}

/**
 * One artist orb plus its expanded album fan / singles orbit.
 *
 * P1 renders the full structure; expansion, filtering and the remove/search
 * buttons are wired in P2, so the handlers are deliberately absent rather than
 * stubbed — a no-op button that looks live is worse than one that is obviously
 * not wired yet.
 */
export function WishlistOrb({ group, index, artistImages, currentCycle }: Props) {
  const image = orbImage(group, artistImages);
  const ringCovers = orbRingCovers(group);
  const hasAlbums = group.albums.length > 0;
  // Pulse when this artist has albums and albums are what runs next.
  const pulse = hasAlbums && currentCycle === 'albums';

  return (
    <div
      className="wl-orb-group"
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
      >
        <div className="wl-orb-glow" />
        {image ? (
          <img className="wl-orb-img" src={image} alt="" />
        ) : (
          <div className="wl-orb-initials">{group.name.substring(0, 2).toUpperCase()}</div>
        )}
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

      <div className="wl-orb-label" title="View artist">
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
        {hasAlbums ? (
          <div className="wl-album-fan">
            {group.albums.map((album) => (
              <div key={album.name} className="wl-album-tile" data-album={album.name}>
                <div className="wl-album-tile-art">
                  {album.image ? (
                    <img src={album.image} alt="" />
                  ) : (
                    <div className="wl-album-tile-fallback">💿</div>
                  )}
                </div>
                <div className="wl-album-tile-info">
                  <div className="wl-album-tile-name">{album.name}</div>
                  <div className="wl-album-tile-count">{trackCountLabel(album.tracks.length)}</div>
                </div>
                <span className="wl-album-tile-badge">{album.tracks.length}</span>
                <div className="wl-tile-tracks">
                  {album.tracks.map((track) => (
                    <TileTrack key={track.id || track.track} track={track} />
                  ))}
                </div>
              </div>
            ))}
          </div>
        ) : null}

        {group.singles.length > 0 ? (
          <div className="wl-singles-orbit">
            {group.singles.map((single) => (
              <div
                key={single.id || single.track}
                className={`wl-single-moon${single.failing ? ' wl-moon-failing' : ''}`}
                data-track-id={single.id}
                title={single.failing ? failingTitle(single) : undefined}
              >
                {single.image ? (
                  <img src={single.image} alt="" />
                ) : (
                  <span className="wl-moon-fallback">⭐</span>
                )}
                {single.failing ? <span className="wl-moon-failing-badge">⚠</span> : null}
                <div className="wl-moon-label">{single.track}</div>
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function TileTrack({ track }: { track: ParsedWishlistTrack }) {
  return (
    <div className={`wl-tile-track${track.failing ? ' wl-track-failing' : ''}`}>
      <span className="wl-tile-track-name">{track.track}</span>
      {track.failing ? (
        <span className="wl-failing-badge" title={failingTitle(track)}>
          ⚠ {track.retry}
        </span>
      ) : null}
    </div>
  );
}
