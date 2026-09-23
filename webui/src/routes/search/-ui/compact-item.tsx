import { useState } from 'react';

import { DownloadIcon, PlayIcon } from './search-icons';
import styles from './search.module.css';

/**
 * The search page's result pieces: an artist face, a cover card (albums,
 * singles, playlists, labels) and a track row.
 *
 * one primary action each. a card opens its thing, the round button on a
 * cover and the arrow on a track row download it, the play button on a row
 * plays it. nothing decorative pretends to be a button.
 */

/** the first letters of a name, for a card with no art */
function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  return (
    words.length > 1 ? words[0][0] + words[1][0] : (words[0] ?? '?').slice(0, 2)
  ).toUpperCase();
}

/** an image that falls back to its placeholder when the url 404s */
function useImage(url: string | undefined) {
  const [failed, setFailed] = useState(false);
  return { show: Boolean(url) && !failed, onError: () => setFailed(true) };
}

export function ArtistFace({
  name,
  image,
  href,
  inLibrary,
  artistId,
}: {
  name: string;
  image?: string;
  href: string;
  inLibrary: boolean;
  artistId?: string | number;
}) {
  const img = useImage(image);
  return (
    <a
      className={styles.face}
      href={href}
      data-artist-id={artistId != null ? String(artistId) : undefined}
    >
      {img.show ? (
        <img className={styles.faceImg} src={image} alt="" loading="lazy" onError={img.onError} />
      ) : (
        <span
          className={`${styles.faceImg} ${styles.facePh}`}
          aria-hidden="true"
          // the lazy artist-image loader looks for these
          data-needs-image={artistId != null ? 'true' : undefined}
          data-artist-id={artistId != null ? String(artistId) : undefined}
        >
          {initials(name)}
        </span>
      )}
      <span className={styles.faceName}>{name}</span>
      <span className={styles.faceSub}>
        {inLibrary ? (
          <>
            <span className={styles.libDot} aria-hidden="true" />
            In your library
          </>
        ) : (
          'Artist'
        )}
      </span>
    </a>
  );
}

export function CoverCard({
  name,
  sub,
  image,
  round = false,
  badge,
  href,
  onOpen,
  actionLabel,
  onAction,
}: {
  name: string;
  sub: string;
  image?: string;
  round?: boolean;
  badge?: string;
  /** a label or anything else that is a page, opens as a link */
  href?: string;
  onOpen?: () => void;
  /** the round button on the cover, e.g. "Download OK Computer" */
  actionLabel?: string;
  onAction?: () => void;
}) {
  const img = useImage(image);
  const body = (
    <>
      <div className={`${styles.cover}${round ? ` ${styles.coverRound}` : ''}`}>
        {img.show ? (
          <img src={image} alt="" loading="lazy" onError={img.onError} />
        ) : (
          <span className={styles.coverPh} aria-hidden="true">
            {initials(name)}
          </span>
        )}
        {badge ? <span className={`${styles.badge} ${styles.coverBadge}`}>{badge}</span> : null}
        {onAction ? (
          <button
            type="button"
            className={styles.coverAction}
            aria-label={actionLabel}
            title={actionLabel}
            onClick={(event) => {
              event.stopPropagation();
              event.preventDefault();
              onAction();
            }}
          >
            <DownloadIcon />
          </button>
        ) : null}
      </div>
      <span className={styles.coverName}>{name}</span>
      <span className={styles.coverSub}>{sub}</span>
    </>
  );
  if (href) {
    return (
      <a className={styles.coverCard} href={href}>
        {body}
      </a>
    );
  }
  return (
    <div
      className={styles.coverCard}
      role="button"
      tabIndex={0}
      aria-label={name}
      onClick={onOpen}
      onKeyDown={(event) => {
        if (event.target !== event.currentTarget) return;
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          onOpen?.();
        }
      }}
    >
      {body}
    </div>
  );
}

export function TrackRow({
  index,
  name,
  sub,
  image,
  duration,
  badge,
  playTitle,
  onOpen,
  onPlay,
}: {
  index: number;
  name: string;
  sub: string;
  image?: string;
  duration: string;
  badge?: 'library' | 'wishlist';
  /** plays from the library, or streams */
  playTitle: string;
  /** opens the download for this track */
  onOpen: () => void;
  onPlay: () => void;
}) {
  const img = useImage(image);
  return (
    <div
      className={styles.trackRow}
      role="button"
      tabIndex={0}
      aria-label={`${name}, ${sub}`}
      onClick={onOpen}
      onKeyDown={(event) => {
        if (event.target !== event.currentTarget) return;
        if (event.key === 'Enter') {
          event.preventDefault();
          onOpen();
        }
      }}
    >
      <span className={styles.trackNum}>{index + 1}</span>
      <button
        type="button"
        className={styles.trackPlay}
        aria-label={`${playTitle}: ${name}`}
        title={playTitle}
        onClick={(event) => {
          event.stopPropagation();
          onPlay();
        }}
      >
        <PlayIcon />
      </button>
      {img.show ? (
        <img className={styles.thumb} src={image} alt="" loading="lazy" onError={img.onError} />
      ) : (
        <span className={styles.thumb} aria-hidden="true" />
      )}
      <span style={{ minWidth: 0 }}>
        <span className={styles.trackTitle} style={{ display: 'block' }}>
          {name}
        </span>
        <span className={styles.trackSub} style={{ display: 'block' }}>
          {sub}
        </span>
      </span>
      <span className={styles.badgeSlot}>
        {badge === 'library' ? (
          <span className={styles.badge}>In library</span>
        ) : badge === 'wishlist' ? (
          <span className={`${styles.badge} ${styles.badgeWish}`}>In wishlist</span>
        ) : null}
      </span>
      <span className={styles.duration}>{duration}</span>
      <button
        type="button"
        className={styles.rowDownload}
        aria-label={`Download ${name}`}
        title="Download"
        onClick={(event) => {
          event.stopPropagation();
          onOpen();
        }}
      >
        <DownloadIcon />
      </button>
    </div>
  );
}
