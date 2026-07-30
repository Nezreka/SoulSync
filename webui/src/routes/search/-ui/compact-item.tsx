import { useState } from 'react';

/**
 * One result card, ported from renderCompactSection's per-item DOM.
 *
 * The class names are the vanilla's and are load-bearing: the stylesheet keys on
 * the COMPOUND selectors (`.enh-compact-item.album-card`), which is the only
 * thing keeping these cards clear of the global `.album-card` used by the
 * artist-detail and library discographies. Rendering `.album-card` alone here
 * would inherit the 300px stacked treatment from a different page.
 *
 * The vanilla inferred the card type from the section id string
 * (`sectionId.includes('albums')`). That is passed explicitly instead — a
 * renamed section silently changing card type is a trap, not a feature.
 */
export type CompactItemKind = 'artist' | 'label' | 'album' | 'track';

const IMAGE_CLASS: Record<CompactItemKind, string> = {
  artist: 'artist-image',
  label: 'artist-image',
  album: 'album-cover',
  track: 'track-cover',
};

const PLACEHOLDER_CLASS: Record<CompactItemKind, string> = {
  artist: 'artist-placeholder',
  label: 'artist-placeholder',
  album: 'album-placeholder',
  track: 'track-placeholder',
};

/** Labels piggyback the artist card's styling, hence both classes. */
const CARD_CLASS: Record<CompactItemKind, string> = {
  artist: 'enh-compact-item artist-card',
  label: 'enh-compact-item label-card artist-card',
  album: 'enh-compact-item album-card',
  track: 'enh-compact-item track-item',
};

export interface CompactItemProps {
  kind: CompactItemKind;
  name: string;
  meta: string;
  placeholder: string;
  image?: string;
  /** Artists and labels render as links so middle-click and copy-link work. */
  href?: string;
  duration?: string;
  badge?: { text: string; className: string };
  /** Extra badges the library check adds — in-library / on-wishlist. */
  extraBadges?: { text: string; className: string }[];
  artistId?: string | number;
  artistName?: string;
  onClick?: () => void;
  onPlay?: () => void;
}

export function CompactItem({
  kind,
  name,
  meta,
  placeholder,
  image,
  href,
  duration,
  badge,
  extraBadges,
  artistId,
  artistName,
  onClick,
  onPlay,
}: CompactItemProps) {
  /**
   * A deterministic image URL that 404s is common — MusicBrainz Cover Art
   * Archive urls are constructed without probing first. Falling back to the
   * placeholder on error is what stops the browser's broken-image glyph.
   */
  const [imageFailed, setImageFailed] = useState(false);
  const showImage = Boolean(image) && !imageFailed;

  const content = (
    <>
      {showImage ? (
        <img
          src={image}
          className={`enh-item-image ${IMAGE_CLASS[kind]}`}
          alt={name}
          onError={() => setImageFailed(true)}
        />
      ) : (
        <div
          className={`enh-item-image-placeholder ${PLACEHOLDER_CLASS[kind]}`}
          data-lazy-image="true"
        >
          {placeholder}
        </div>
      )}
      <div className="enh-item-info">
        <div className="enh-item-name">{name}</div>
        <div className="enh-item-meta">{meta}</div>
      </div>
      {duration && kind === 'track' ? (
        <div className="enh-item-duration">
          {duration}
          <button
            className="enh-item-play-btn"
            title="Stream this track"
            type="button"
            onClick={(event) => {
              // Without this the card's own click fires too and the download
              // modal opens behind the player.
              event.stopPropagation();
              onPlay?.();
            }}
          >
            ▶
          </button>
        </div>
      ) : null}
      {badge ? <div className={`enh-item-badge ${badge.className}`}>{badge.text}</div> : null}
      {extraBadges?.map((extra) => (
        <div key={extra.className} className={extra.className}>
          {extra.text}
        </div>
      ))}
    </>
  );

  // `data-needs-image` is what the lazy image loader looks for; it must be
  // 'true' only when there is genuinely nothing to show yet.
  const dataAttrs =
    artistId != null
      ? {
          'data-artist-id': String(artistId),
          'data-needs-image': image ? 'false' : 'true',
          ...(artistName ? { 'data-artist-name': artistName } : {}),
        }
      : {};

  if (href && (kind === 'artist' || kind === 'label')) {
    return (
      <a
        className={CARD_CLASS[kind]}
        href={href}
        aria-label={name || 'Artist'}
        style={{ color: 'inherit', textDecoration: 'none' }}
        onClick={onClick}
        {...dataAttrs}
      >
        {content}
      </a>
    );
  }

  return (
    <div
      className={CARD_CLASS[kind]}
      role={onClick ? 'button' : undefined}
      tabIndex={onClick ? 0 : undefined}
      onClick={onClick}
      onKeyDown={(event) => {
        if (!onClick) return;
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          onClick();
        }
      }}
      {...dataAttrs}
    >
      {content}
    </div>
  );
}

/** The grid class each kind's list wears. */
export const LIST_CLASS: Record<CompactItemKind, string> = {
  artist: 'enh-artists-grid',
  label: 'enh-artists-grid',
  album: 'enh-albums-grid',
  track: 'enh-tracks-list',
};

/**
 * One titled section. Renders nothing at all when empty — the vanilla added
 * `.hidden`, and an empty section with a "0" count is noise.
 */
export function ResultSection({
  id,
  listId,
  countId,
  icon,
  title,
  kind,
  count,
  sectionClass,
  children,
}: {
  id: string;
  listId: string;
  countId: string;
  icon: string;
  title: string;
  kind: CompactItemKind;
  count: number;
  /** Extra section class — `enh-artist-section` strips the card chrome. */
  sectionClass?: string;
  children: React.ReactNode;
}) {
  if (!count) return null;
  return (
    <div className={`enh-dropdown-section${sectionClass ? ` ${sectionClass}` : ''}`} id={id}>
      <div className="enh-section-header">
        {/* The icon is display:none in the stylesheet; kept so the markup still
            matches the vanilla's, and re-showing it stays a CSS-only change. */}
        <span className="enh-section-icon">{icon}</span>
        {/* A heading, as the vanilla's was — the styling is class-only, so this
            is purely the document outline a screen reader reads. */}
        <h4 className="enh-section-title">{title}</h4>
        <span className="enh-section-count" id={countId}>
          {count}
        </span>
      </div>
      <div className={`enh-compact-list ${LIST_CLASS[kind]}`} id={listId}>
        {children}
      </div>
    </div>
  );
}
