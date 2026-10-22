import type { WishlistArtistGroup } from '../-wishlist.types';

import {
  artistHue,
  orbImage,
  orbMeta,
  orbRingCovers,
  orbSizeClass,
  trackCountLabel,
} from '../-wishlist.helpers';
import { NebulaOrb } from './nebula-parts';

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
 * one artist orb. the detail lives in WishlistArtistSheet, opened beside the
 * field, so opening an orb never shoves the others around.
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
  return (
    <NebulaOrb
      name={group.name}
      image={orbImage(group, artistImages)}
      initials={group.name.substring(0, 2).toUpperCase()}
      hue={artistHue(group.name)}
      sizeClass={orbSizeClass(group.total)}
      meta={orbMeta(group)}
      stuck={group.failingCount > 0}
      failingCount={group.failingCount}
      expanded={expanded}
      processing={processing}
      pulse={group.albums.length > 0 && currentCycle === 'albums'}
      ringCovers={orbRingCovers(group)}
      index={index}
      data={{ artist: group.name }}
      tooltipSub={trackCountLabel(group.total)}
      onToggle={onToggleExpand}
      onOpen={() => window._navigateToArtistFromWishlist?.(group.name)}
      openTitle="View artist"
    />
  );
}
