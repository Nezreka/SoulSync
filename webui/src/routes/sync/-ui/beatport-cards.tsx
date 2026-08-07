/**
 * The five Beatport card shapes, transcribed from beatport-ui.js.
 *
 * These look interchangeable and are not. Each has its own class family, its
 * own CSS custom property, its own defaults for missing fields, and its own
 * rule about whether the background style is emitted at all. Every difference
 * below is in the vanilla.
 *
 * ONE DECLARED NORMALISATION, applied consistently. Template interpolation
 * writes the literal string 'undefined' for a missing value, so the vanilla can
 * emit `data-url="undefined"` and `url('undefined')`. JSX instead DROPS an
 * attribute whose value is undefined, which would change whether the attribute
 * is present at all. Neither is desirable, so every missing value here becomes
 * an empty string: the attribute stays present, as in the vanilla, without the
 * bogus text.
 *
 * Checked before relying on it: the only two attribute selectors in the whole
 * Beatport stylesheet are `.beatport-tab-button[data-beatport-tab="browse"]`
 * and `.beatport-rebuild-slide[data-image]` (17056), so no card styling depends
 * on `data-url` at all — but the cards now agree with each other either way.
 */

import type { CSSProperties } from 'react';

import type { BeatportSlideAttributes } from './beatport-slider';

/* ── Hero (86-103) ────────────────────────────────────────────────────────── */

export interface HeroTrackLike {
  title?: string;
  artist?: string;
  url?: string;
  image_url?: string;
}

/**
 * The hero's slide ATTRIBUTES, which must land on the slide element itself —
 * `.beatport-rebuild-slide[data-image]::before` is an attribute selector
 * reading `var(--slide-bg-image)` (style.css 17056).
 *
 * Both are emitted unconditionally, exactly as the vanilla does, so a track
 * with no artwork still gets `data-image=""` and `url('')`. The selector then
 * matches and paints nothing, which is the existing behaviour.
 */
export function heroSlideAttributes(track: HeroTrackLike): BeatportSlideAttributes {
  return {
    'data-url': track.url ?? '',
    'data-image': track.image_url ?? '',
    style: { '--slide-bg-image': `url('${track.image_url ?? ''}')` } as CSSProperties,
  };
}

export function BeatportHeroSlide({ track }: { track: HeroTrackLike }) {
  return (
    <>
      <div className="beatport-rebuild-slide-background">
        <div className="beatport-rebuild-slide-gradient" />
      </div>
      <div className="beatport-rebuild-slide-content">
        <div className="beatport-rebuild-track-info">
          <h2 className="beatport-rebuild-track-title">{track.title}</h2>
          <p className="beatport-rebuild-artist-name">{track.artist}</p>
          {/* 99: a fixed caption, not a field. */}
          <p className="beatport-rebuild-album-name">New on Beatport</p>
        </div>
      </div>
    </>
  );
}

/* ── New releases (438-467) ───────────────────────────────────────────────── */

export interface ReleaseLike {
  title?: string;
  artist?: string;
  label?: string;
  url?: string;
  image_url?: string;
}

/**
 * 439: the custom property is set UNCONDITIONALLY here, so a release with no
 * artwork gets `url('')`. Hype picks omit the style entirely in that case —
 * a real difference between two card types that look identical.
 *
 * The `title` attributes on all three lines are the vanilla's tooltips for
 * text that truncates.
 */
export function BeatportReleaseCard({
  release,
  onClick,
}: {
  release: ReleaseLike;
  onClick?: () => void;
}) {
  return (
    <div
      className="beatport-release-card"
      data-url={release.url ?? ''}
      style={{ '--card-bg-image': `url('${release.image_url ?? ''}')` } as CSSProperties}
      onClick={onClick}
    >
      <div className="beatport-release-card-content">
        <div className="beatport-release-artwork">
          {release.image_url ? (
            <img src={release.image_url} alt={release.title} loading="lazy" />
          ) : null}
        </div>
        <div className="beatport-release-info">
          <div className="beatport-release-title" title={release.title}>
            {release.title}
          </div>
          <div className="beatport-release-artist" title={release.artist}>
            {release.artist}
          </div>
          <div className="beatport-release-label" title={release.label}>
            {release.label}
          </div>
        </div>
      </div>
    </div>
  );
}

/** 454-467: the filler carries real copy, unlike hype picks' bare icon. */
export function BeatportReleasePlaceholder() {
  return (
    <div className="beatport-release-card beatport-release-placeholder">
      <div className="beatport-release-card-content">
        <div className="beatport-release-artwork">
          <div className="placeholder-icon">📀</div>
        </div>
        <div className="beatport-release-info">
          <div className="beatport-release-title">More Releases</div>
          <div className="beatport-release-artist">Coming Soon</div>
          <div className="beatport-release-label">Beatport</div>
        </div>
      </div>
    </div>
  );
}

/* ── Hype picks (780-821) ─────────────────────────────────────────────────── */

/**
 * 803-820. Two differences from the release card that are easy to flatten:
 *  - the background style is OMITTED when there is no artwork, rather than set
 *    to `url('')`,
 *  - the three text lines have literal defaults, and the label's is
 *    'Hype Pick' rather than 'Unknown Label'.
 *
 * The vanilla's click handler re-reads this rendered text back out of the DOM
 * (961-972) rather than closing over the release. Traced through
 * handleBeatportReleaseCardClick to see what that actually costs: `title` is
 * used only in two toasts, and the DOWNLOAD is named from `data.album.name`
 * off the release-metadata endpoint (1897) — so 'Unknown Title' never reaches
 * the download engine. The re-read touches exactly two things: the toast copy,
 * and `img.src` (a resolved absolute URL) as the bubble-image fallback.
 *
 * The port holds the real object, which is the natural React shape. Recorded
 * because the difference is real, but it is small and cosmetic — not, as an
 * earlier pass in SYNC_PORT_AUDIT.md claimed, wrong metadata on disk.
 */
export function BeatportHypePickCard({
  release,
  onClick,
}: {
  release: ReleaseLike;
  onClick?: () => void;
}) {
  const artworkUrl = release.image_url || '';
  return (
    <div
      className="beatport-hype-pick-card"
      data-url={release.url || ''}
      style={
        artworkUrl ? ({ '--card-bg-image': `url('${artworkUrl}')` } as CSSProperties) : undefined
      }
      onClick={onClick}
    >
      <div className="beatport-hype-pick-card-content">
        <div className="beatport-hype-pick-artwork">
          {artworkUrl ? (
            <img src={artworkUrl} alt={release.title || 'Release'} loading="lazy" />
          ) : null}
        </div>
        <div className="beatport-hype-pick-info">
          <div className="beatport-hype-pick-title">{release.title || 'Unknown Title'}</div>
          <div className="beatport-hype-pick-artist">{release.artist || 'Unknown Artist'}</div>
          <div className="beatport-hype-pick-label">{release.label || 'Hype Pick'}</div>
        </div>
      </div>
    </div>
  );
}

/** 780-783: an icon and nothing else. */
export function BeatportHypePickPlaceholder() {
  return (
    <div className="beatport-hype-pick-card beatport-hype-pick-placeholder">
      <div className="placeholder-icon">🔥</div>
    </div>
  );
}

/* ── Charts and DJ charts (1122-1129, 1421-1428) ──────────────────────────── */

export interface ChartLike {
  name?: string;
  creator?: string;
  url?: string;
  image?: string;
}

/**
 * The two chart cards are the file's only true clones, differing in class
 * prefix and custom property (`--chart-bg-image` vs `--dj-bg-image`) — so they
 * share one component with a `variant`, and the variant decides both.
 *
 * Like hype picks and unlike releases, the style is omitted when there is no
 * image.
 */
export function BeatportChartCard({
  chart,
  variant,
  onClick,
}: {
  chart: ChartLike;
  variant: 'chart' | 'dj';
  onClick?: () => void;
}) {
  const property = variant === 'dj' ? '--dj-bg-image' : '--chart-bg-image';
  return (
    <div
      className={`beatport-${variant}-card`}
      data-url={chart.url || ''}
      style={chart.image ? ({ [property]: `url('${chart.image}')` } as CSSProperties) : undefined}
      onClick={onClick}
    >
      <div className={`beatport-${variant}-card-content`}>
        <div className={`beatport-${variant}-name`}>{chart.name || 'Unknown Chart'}</div>
        <div className={`beatport-${variant}-creator`}>{chart.creator || 'Unknown Creator'}</div>
      </div>
    </div>
  );
}
