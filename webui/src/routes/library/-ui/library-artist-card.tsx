import { thumb } from '@/platform/artwork-thumb';
import { useState } from 'react';

import type { ArtistBadge, LibraryArtist } from '../-library.types';

import {
  buildArtistBadges,
  canWatchArtist,
  cardAnimationDelay,
  splitBadgeColumns,
  trackCountLabel,
} from '../-library.helpers';

interface Props {
  artist: LibraryArtist;
  index: number;
  /** Active music source name; decides which id makes an artist watchable. */
  musicSource?: string;
  href: string;
  /** Toggle this artist's watchlist membership. Undefined = not watchable. */
  onToggleWatch?: () => void;
  /** True while that toggle is in flight — the badge label shows "…". */
  watchPending?: boolean;
}

/**
 * A badge click must never fall through to the card's link.
 *
 * A plain factory, NOT a hook — it is called conditionally below.
 *
 * The vanilla grid delegated this: any `.source-card-icon` click called
 * preventDefault + stopPropagation, then either opened the provider url in a
 * new tab or toggled the watchlist. Without it every badge would just navigate
 * to artist detail.
 */
function badgeClickHandler(action?: () => void) {
  return (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    action?.();
  };
}

function BadgeIcon({ badge }: { badge: ArtistBadge }) {
  const onClick = badgeClickHandler(
    badge.url ? () => window.open(badge.url as string, '_blank') : undefined,
  );

  return (
    <div
      className="source-card-icon"
      title={badge.title}
      data-url={badge.url ?? undefined}
      onClick={onClick}
    >
      {badge.logo ? (
        <img
          src={badge.logo}
          style={{ width: 16, height: 'auto', display: 'block' }}
          alt=""
          // The vanilla markup swapped the whole icon for its text fallback on
          // error. React cannot replace its own parent, so the text sits
          // underneath and the image is hidden instead — same visual result,
          // without reaching outside this component's tree.
          onError={(e) => {
            e.currentTarget.style.display = 'none';
          }}
        />
      ) : null}
      <span
        style={{ fontSize: 9, fontWeight: 700 }}
        className={badge.logo ? 'source-card-icon-fallback' : undefined}
      >
        {badge.logo ? '' : badge.fallback}
      </span>
    </div>
  );
}

/**
 * The artist image, with the vanilla TWO-stage fallback.
 *
 * The original `onerror` tried Deezer's image API once (guarded by a
 * `triedDeezer` dataset flag so a failing Deezer url could not loop), and only
 * then replaced the node with the music-note placeholder. Dropping the Deezer
 * hop would silently lose artwork for every artist whose stored image_url has
 * rotted but who has a deezer_id.
 */
function ArtistImage({ artist, hasImage }: { artist: LibraryArtist; hasImage: boolean }) {
  type Stage = 'primary' | 'deezer' | 'placeholder';
  const [stage, setStage] = useState<Stage>(hasImage ? 'primary' : 'placeholder');

  const onError = () => {
    // One retry only, and only when there is a Deezer id to retry with.
    setStage((s) => (s === 'primary' && artist.deezer_id ? 'deezer' : 'placeholder'));
  };

  if (stage === 'placeholder') return <div className="library-artist-image-fallback">🎵</div>;

  const src =
    stage === 'deezer'
      ? `https://api.deezer.com/artist/${artist.deezer_id}/image?size=big`
      : (artist.image_url ?? '');

  // `key` remounts rather than swapping src on the element that just errored,
  // so each stage gets a clean load cycle. Not test-observable: jsdom never
  // fetches images, so the error events above are synthetic either way.
  return <img key={stage} src={thumb(src, 'grid')} alt={artist.name} loading="lazy" onError={onError} />;
}

/** One artist tile. Markup mirrors buildLibraryArtistCardHTML so the CSS applies unchanged. */
export function LibraryArtistCard({
  artist,
  index,
  musicSource,
  href,
  onToggleWatch,
  watchPending,
}: Props) {
  const badges = buildArtistBadges(artist);
  const { primary, overflow, needsOverflow } = splitBadgeColumns(badges);
  const watched = Boolean(artist.is_watched);
  const watchable = canWatchArtist(artist, musicSource);
  const tracks = trackCountLabel(artist.track_count);
  const hasImage = Boolean(artist.image_url && artist.image_url.trim() !== '');
  const onWatchClick = badgeClickHandler(watchPending ? undefined : onToggleWatch);

  // Only the UNWATCHED badge acts: the vanilla handler gated the toggle on
  // `badge.dataset.unwatched`, so a "Watching" badge swallowed its click and
  // did nothing. Removing is done from the Watchlist page, not from here.
  const watchBadge = watched ? (
    <div
      className="watch-card-icon watched source-card-icon"
      title="On your watchlist"
      onClick={badgeClickHandler()}
    >
      <span className="watch-icon-emoji">👁️</span>
      <span className="watch-icon-label">Watching</span>
    </div>
  ) : watchable ? (
    <div
      className="watch-card-icon source-card-icon"
      data-unwatched="1"
      title="Add to Watchlist"
      style={{ opacity: 0.4 }}
      onClick={onWatchClick}
    >
      <span className="watch-icon-emoji">👁️</span>
      <span className="watch-icon-label">{watchPending ? '...' : 'Watch'}</span>
    </div>
  ) : null;

  return (
    <a
      className="library-artist-card"
      href={href}
      data-artist-id={String(artist.id)}
      data-artist-name={artist.name}
      style={{
        position: 'relative',
        display: 'block',
        animation: `cardFadeIn 0.35s cubic-bezier(0.4,0,0.2,1) ${cardAnimationDelay(index)}ms both`,
        textDecoration: 'none',
        color: 'inherit',
      }}
    >
      {badges.length > 0 || watchBadge ? (
        needsOverflow ? (
          // Note the order: the overflow column renders FIRST, as in the
          // vanilla markup — the CSS positions them, so swapping them moves
          // the badges on screen.
          <div className="card-badge-container">
            <div className="badge-overflow-column">
              {watchBadge}
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
          // Without overflow the watch badge comes LAST, not first.
          <div className="card-badge-container">
            {primary.map((b) => (
              <BadgeIcon key={b.key} badge={b} />
            ))}
            {watchBadge}
          </div>
        )
      ) : null}

      <div className="library-artist-image">
        <ArtistImage artist={artist} hasImage={hasImage} />
      </div>

      <div className="library-artist-info">
        <h3 className="library-artist-name" title={artist.name}>
          {artist.name}
        </h3>
        <div className="library-artist-stats">
          {tracks ? <span className="library-artist-stat">{tracks}</span> : null}
        </div>
      </div>
    </a>
  );
}
