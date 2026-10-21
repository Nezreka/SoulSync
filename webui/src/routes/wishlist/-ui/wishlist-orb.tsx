import { useState } from 'react';

import type { WishlistArtistGroup } from '../-wishlist.types';

import {
  artistHue,
  orbAnimationDelay,
  orbImage,
  orbMeta,
  orbRingCovers,
  orbSizeClass,
  trackCountLabel,
} from '../-wishlist.helpers';

interface Props {
  group: WishlistArtistGroup;
  index: number;
  artistImages: Map<string, string>;
  currentCycle: string;
  /** A wishlist run is in flight; the orb shows its working state. */
  processing: boolean;
  /** this artist's sheet is open beside the field. */
  expanded: boolean;
  onToggleExpand: () => void;
}

/**
 * one artist orb. the detail lives in WishlistArtistSheet now, opened beside
 * the field, so opening an orb never shoves the others around.
 */
export function WishlistOrb({
  group,
  index,
  artistImages,
  currentCycle,
  processing,
  expanded,
  onToggleExpand,
}: Props) {
  // the entrance plays once. a poll refetch can reorder orbs, and moving a node
  // restarts its css animations, so without this every moved orb blinked out
  // and faded back in while a wishlist run was going.
  const [entered, setEntered] = useState(false);

  const image = orbImage(group, artistImages);
  const ringCovers = orbRingCovers(group);
  const pulse = group.albums.length > 0 && currentCycle === 'albums';

  return (
    <div
      className={`wl-orb-group${expanded ? ' expanded' : ''}${processing ? ' orb-processing' : ''}${entered ? ' wl-orb-entered' : ''}`}
      data-artist={group.name}
      data-failing={group.failingCount}
      style={entered ? undefined : { animationDelay: `${orbAnimationDelay(index)}ms` }}
      onAnimationEnd={(event) => {
        if (event.target === event.currentTarget && event.animationName === 'orbEntrance') {
          setEntered(true);
        }
      }}
    >
      <div className="wl-orb-tooltip">
        {group.name}
        <br />
        <span>{trackCountLabel(group.total)}</span>
      </div>

      <button
        type="button"
        className={`wl-orb ${orbSizeClass(group.total)}${pulse ? ' orb-pulse' : ''}`}
        style={{ ['--orb-hue' as string]: artistHue(group.name) }}
        aria-label={`${group.name}, ${orbMeta(group)}`}
        aria-expanded={expanded}
        onClick={onToggleExpand}
      >
        <span className="wl-orb-glow" />
        {image ? (
          <img className="wl-orb-img" src={image} alt="" />
        ) : (
          <span className="wl-orb-initials">{group.name.substring(0, 2).toUpperCase()}</span>
        )}
        <span className="wl-orb-ring" />

        {ringCovers.length > 0 ? (
          <span className="wl-orb-art-ring">
            {ringCovers.map((url, i) => (
              <img
                key={`${url}-${i}`}
                className="wl-art-ring-item"
                src={url}
                alt=""
                style={{ ['--ring-angle' as string]: `${(360 / ringCovers.length) * i}deg` }}
              />
            ))}
          </span>
        ) : null}
      </button>

      {/* a sibling of .wl-orb, not a child, so its click can't reach the
          toggle. stopPropagation stays as a guard against future nesting. */}
      <button
        type="button"
        className="wl-orb-label"
        title="View artist"
        onClick={(event) => {
          event.stopPropagation();
          window._navigateToArtistFromWishlist?.(group.name);
        }}
      >
        {group.name}
      </button>
      <div className={`wl-orb-meta${group.failingCount > 0 ? ' wl-orb-meta--stuck' : ''}`}>
        {orbMeta(group)}
      </div>
    </div>
  );
}
