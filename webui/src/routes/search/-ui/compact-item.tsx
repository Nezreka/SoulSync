import { useEffect, useRef, useState } from 'react';

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
  /**
   * Extra badges the library check adds — in-library / on-wishlist.
   *
   * `delay` staggers the arrival animation, which is how the vanilla's
   * setTimeout cascade reads without any timers.
   */
  extraBadges?: { text: string; className: string; delay?: string }[];
  artistId?: string | number;
  artistName?: string;
  /**
   * Receives the event: a link card has to be able to preventDefault so the
   * click routes in-app instead of reloading the page.
   */
  onClick?: (event: React.MouseEvent | React.KeyboardEvent) => void;
  onPlay?: () => void;
  /** The play button's tooltip — it streams, or plays a local file. */
  playTitle?: string;
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
  playTitle = 'Stream this track',
}: CompactItemProps) {
  /**
   * A deterministic image URL that 404s is common — MusicBrainz Cover Art
   * Archive urls are constructed without probing first. Falling back to the
   * placeholder on error is what stops the browser's broken-image glyph.
   */
  const [imageFailed, setImageFailed] = useState(false);
  const showImage = Boolean(image) && !imageFailed;

  /**
   * Every card with artwork gets a glow sampled from that artwork.
   *
   * Not an artist-only flourish: renderCompactSection ran this for ANY card with
   * an image (shared-helpers.js:748-753), and the stylesheet turns the sampled
   * palette into the hover border and box-shadow of album, track and artist
   * cards alike (style.css:40314/40538). Skipping it leaves every result card
   * with the plain grey hover it has when art fails to load.
   *
   * Keyed on the url, so a lazily-resolved artist photo arriving later glows
   * too — the same path, one effect.
   */
  const cardRef = useRef<HTMLElement | null>(null);
  useEffect(() => {
    const card = cardRef.current;
    if (!card || !image || imageFailed) return;
    try {
      window.extractImageColors?.(image, (colors) => {
        window.applyDynamicGlow?.(card, colors);
      });
    } catch {
      // Reading pixels can throw on a CORS-opaque image. A card without a glow
      // is fine; a card that failed to render is not.
    }
  }, [image, imageFailed]);

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
            title={playTitle}
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
        <div
          key={extra.className}
          className={extra.className}
          style={extra.delay ? { animationDelay: extra.delay } : undefined}
        >
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
        ref={cardRef as React.Ref<HTMLAnchorElement>}
        className={CARD_CLASS[kind]}
        href={href}
        aria-label={name || 'Artist'}
        style={{ color: 'inherit', textDecoration: 'none' }}
        // The handler receives the event so the page can preventDefault and
        // route in-app; the href stays real for middle-click and copy-link.
        onClick={onClick}
        {...dataAttrs}
      >
        {content}
      </a>
    );
  }

  return (
    <div
      ref={cardRef as React.Ref<HTMLDivElement>}
      className={CARD_CLASS[kind]}
      role={onClick ? 'button' : undefined}
      tabIndex={onClick ? 0 : undefined}
      onClick={onClick}
      onKeyDown={(event) => {
        if (!onClick) return;
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          // The media player's document-level keydown treats Space on
          // anything that isn't an input as play/pause (media-player.js:2591)
          // — without this, activating a focused card also toggles playback.
          // Vanilla cards weren't focusable, so only this path can collide.
          event.stopPropagation();
          onClick(event);
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
