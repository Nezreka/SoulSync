import { useEffect, useRef, useState } from 'react';

import type { StreamCounts } from '../-artist-detail.completion';
import type { ArtistInfo, Discography, DiscographyBucket } from '../-artist-detail.types';

import { buildHeroBadges } from '../-artist-detail.hero';
import {
  artistFormatTags,
  buildGenreChips,
  categoryStats,
  type CategoryStats,
  cleanArtistBio,
  formatHeroNumber,
  heroImage,
  type HeroImageStage,
  heroImageSrc,
  nextHeroImageStage,
  resolvedCategoryStats,
  streamCategoryStats,
  totalReleaseCount,
} from '../-artist-detail.hero-stats';
import { bucketCounts } from '../-artist-detail.use-completion';
import { checkWatchlistRequest, toggleWatchlistRequest } from '../-artist-detail.watchlist-button';
import { ArtPicker } from './art-picker';
import { ArtistDbRecord } from './artist-db-record';
import { DiscographyModal } from './discography-modal';
import { EnrichmentCoverage } from './enrichment-coverage';
import { BodyPortal } from './portal';
import { TopTracksSidebar } from './top-tracks-sidebar';

interface Props {
  artist: ArtistInfo;
  discography: Discography;
  isSourceArtist: boolean;
  /** Running per-bucket tallies while the ownership stream runs. */
  streamCounts?: StreamCounts | null;
  /** True once the stream reported `complete`. */
  streamCompleted?: boolean;
  /** Per-service coverage payload; the panel hides itself when absent. */
  enrichment?: Record<string, unknown>;
  /** Canonical identity for the watchlist button; null hides its behavior. */
  watchlist?: { id: unknown; name: string } | null;
}

/**
 * The completion bar for one bucket, in the three states the vanilla had:
 *
 *   - no stream yet    -> categoryStats over the whole bucket ("..." if pending)
 *   - stream running   -> the running tallies, denominator = resolved so far
 *   - stream complete  -> recounted from the merged discography
 *
 * Reproducing all three matters because they disagree: mid-stream the bar reads
 * "2/3" of a twelve-album discography, and after `complete` anything the
 * backend never reported drops out of the denominator instead of pinning the
 * bar back to "...".
 */
function bucketStats(
  bucket: DiscographyBucket,
  discography: Discography,
  streamCounts: StreamCounts | null | undefined,
  streamCompleted: boolean,
): CategoryStats {
  const releases = discography[bucket] ?? [];
  if (streamCompleted) return resolvedCategoryStats(releases);
  const counts = bucketCounts(streamCounts ?? null, bucket);
  if (counts) return streamCategoryStats(counts.owned, counts.missing);
  return categoryStats(releases);
}

const CATEGORY_LABELS: { bucket: DiscographyBucket; label: string }[] = [
  { bucket: 'albums', label: 'Albums' },
  { bucket: 'eps', label: 'EPs' },
  { bucket: 'singles', label: 'Singles' },
];

/**
 * Artist photo with the vanilla's three-step fallback.
 *
 * `key={stage}` remounts rather than swapping src on the element that just
 * errored, so each stage gets a clean load cycle. Not test-observable — jsdom
 * never fetches images, so dropping the key survives mutation. Kept because a
 * real browser can hold the broken-image state on a reused element.
 *
 * The stage reset on artist change IS observable and tested: without it,
 * navigating from an artist whose image failed to one with a good image would
 * start at 'fallback' and show the music-note icon over a valid photo.
 */
function ArtistPhoto({ artist, discography }: { artist: ArtistInfo; discography: Discography }) {
  const image = heroImage(artist, discography);
  const [stage, setStage] = useState<HeroImageStage>(image.primary ? 'primary' : 'fallback');

  useEffect(() => {
    setStage(image.primary ? 'primary' : 'fallback');
  }, [image.primary]);

  const showFallback = stage === 'fallback';

  return (
    <>
      <img
        key={stage}
        className="artist-image"
        id="artist-detail-image"
        src={showFallback ? '' : heroImageSrc(stage, artist, image)}
        alt={artist.name ?? 'Artist Image'}
        style={{ display: showFallback ? 'none' : 'block' }}
        onError={() => setStage((s) => nextHeroImageStage(s, artist, image))}
      />
      <div
        className="artist-image-fallback"
        id="artist-detail-image-fallback"
        style={{ display: showFallback ? 'flex' : 'none' }}
      >
        🎵
      </div>
    </>
  );
}

/**
 * The artist hero. Mirrors #artist-hero-section in index.html so the CSS and
 * the guided-tour anchors apply unchanged.
 *
 * Radio and the art picker are vanilla globals invoked through window — they
 * live in stats-automations.js and library.js respectively and are not
 * reimplemented here.
 */
export function ArtistHero({
  artist,
  discography,
  isSourceArtist,
  streamCounts,
  streamCompleted = false,
  enrichment,
  watchlist = null,
}: Props) {
  const image = heroImage(artist, discography);
  const badges = buildHeroBadges(artist);
  const chips = buildGenreChips(artist);
  const bio = cleanArtistBio(artist.lastfm_bio);
  const [bioExpanded, setBioExpanded] = useState(false);
  const bioRef = useRef<HTMLDivElement | null>(null);
  /**
   * Has the bio been MEASURED to fit inside the collapsed box?
   *
   * The toggle used to render unconditionally inside a box with
   * `max-height: 8em; overflow: hidden`, so on a long bio — every bio worth
   * reading — it sat below the clip and was invisible, leaving no way to read
   * on, scroll, or pop it out (#1200, wishx). It is pinned to the box now, so
   * it also has to know when there is nothing to reveal.
   *
   * The polarity is deliberate and FAIL-SAFE: show the toggle unless we have
   * positively measured that the text fits. Asking "does it overflow?" and
   * defaulting to false hides the toggle wherever measurement is unavailable
   * (jsdom, a pre-font-load paint, no ResizeObserver) — which recreates the
   * exact bug being fixed. This way a short bio may briefly carry a redundant
   * "Read more"; the other way a long one loses its only way out.
   */
  const [bioFits, setBioFits] = useState(false);
  const [pickingPhoto, setPickingPhoto] = useState(false);
  /** null = unknown (check pending/failed); the vanilla left the default label. */
  const [watching, setWatching] = useState<boolean | null>(null);
  const [watchlistBusy, setWatchlistBusy] = useState(false);
  const [downloadingDiscog, setDownloadingDiscog] = useState(false);
  /** A freshly applied photo shows immediately, as the vanilla swapped the
      hero img src in place (openArtistArtPicker apply, library.js:1966-1971). */
  const [appliedPhoto, setAppliedPhoto] = useState<string | null>(null);
  const hasReleases = totalReleaseCount(discography) > 0;

  /**
   * Measure the collapsed bio so the toggle only offers what it can deliver.
   *
   * Re-measured on bio change AND on resize: the box is a fixed 8em tall but
   * its WIDTH is fluid, so the same text wraps to more lines in a narrow
   * column and a bio that fit at 1920 overflows on a laptop.
   */
  useEffect(() => {
    const el = bioRef.current;
    if (!el || !bio) {
      setBioFits(false);
      return;
    }
    const measure = () => {
      const node = bioRef.current;
      if (!node) return;
      // measure the COLLAPSED height — while expanded the box grew to fit,
      // so scrollHeight equals clientHeight and would report "it fits", then
      // hide the Show less button the reader needs to get back.
      const wasExpanded = node.classList.contains('expanded');
      if (wasExpanded) node.classList.remove('expanded');
      const { scrollHeight, clientHeight } = node;
      if (wasExpanded) node.classList.add('expanded');
      // an environment that cannot measure reports 0/0 — that is "unknown",
      // never "it fits".
      setBioFits(clientHeight > 0 && scrollHeight - clientHeight <= 4);
    };
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [bio]);

  /** The watchlist is keyed on the CANONICAL Spotify identity where one exists.
   *
   *  Depends on the ID, not the `watchlist` OBJECT. `watchlistIdentity()` builds
   *  a fresh `{id, name}` on every call and the page calls it inline in JSX, so
   *  the object's identity changes on every parent render — and the parent
   *  re-renders on every enrichment-stream tick. Keying the effect on the object
   *  re-ran this on each of those: `setWatching(null)` reset the button to its
   *  default label, the re-check flipped it back, and the pair repeated for as
   *  long as the stream ran. That's the "watched/unwatched over and over"
   *  flapping, and it also swallowed toggles — an in-flight check that resolved
   *  after a toggle overwrote the new state with the old one. */
  const watchlistId = watchlist?.id ?? null;
  useEffect(() => {
    setWatching(null);
    if (!watchlistId) return;
    let cancelled = false;
    void checkWatchlistRequest(watchlistId).then((state) => {
      if (!cancelled) setWatching(state);
    });
    return () => {
      cancelled = true;
    };
  }, [watchlistId]);

  const toggleWatchlist = async () => {
    if (!watchlist || watchlistBusy) return;
    setWatchlistBusy(true);
    try {
      const { watching: next, message } = await toggleWatchlistRequest(
        watchlist.id,
        watchlist.name,
      );
      setWatching(next);
      window.showToast?.(message, 'success');
    } catch (error) {
      window.showToast?.(`Error: ${(error as Error).message}`, 'error');
    }
    setWatchlistBusy(false);
  };
  // Only after `complete`, matching the vanilla: the set accumulates through
  // the whole stream but the block was not rendered until it ended.
  const formats = streamCompleted ? artistFormatTags(streamCounts?.formats) : [];

  return (
    <div className="artist-hero-section" id="artist-hero-section">
      {downloadingDiscog ? (
        <DiscographyModal
          libraryArtistId={artist.id}
          artistName={String(artist.name || '')}
          artistImage={appliedPhoto || image.primary || ''}
          onClose={() => setDownloadingDiscog(false)}
        />
      ) : null}
      {pickingPhoto ? (
        // Portaled for the same reason as the discography modal: the hero's
        // backdrop-filter would clamp a fixed overlay to the hero box.
        <BodyPortal>
          <ArtPicker
            target={{ kind: 'artist', id: artist.id }}
            currentUrl={appliedPhoto || image.primary || null}
            subtitle={
              String(artist.name || '') +
              ' · applies to SoulSync, your server, and artist.jpg on disk'
            }
            onApplied={(url) => setAppliedPhoto(url)}
            onClose={() => setPickingPhoto(false)}
          />
        </BodyPortal>
      ) : null}
      <div
        className="artist-detail-hero-bg"
        id="artist-detail-hero-bg"
        style={{ backgroundImage: image.primary ? `url('${image.primary}')` : undefined }}
      />
      <div className="artist-detail-hero-overlay" />

      <div className="artist-hero-content">
        <div
          className="artist-image-container"
          title="Change artist photo"
          onClick={() => setPickingPhoto(true)}
        >
          <ArtistPhoto
            artist={
              appliedPhoto
                ? { ...artist, image_url: appliedPhoto, thumb_url: appliedPhoto }
                : artist
            }
            discography={discography}
          />
          <div className="artist-image-edit-overlay">
            <svg
              viewBox="0 0 24 24"
              width="26"
              height="26"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.7"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <rect x="3" y="3" width="18" height="18" rx="2" />
              <circle cx="8.5" cy="8.5" r="1.5" />
              <path d="m21 15-5-5L5 21" />
            </svg>
            <span>Change photo</span>
          </div>
        </div>

        <div className="artist-info">
          <div className="artist-hero-identity">
            <h1 className="artist-name" id="artist-detail-name">
              {artist.name}
            </h1>
            <div className="artist-hero-badges" id="artist-hero-badges">
              {badges.map((badge) =>
                badge.url ? (
                  <a
                    key={badge.key}
                    className="artist-hero-badge"
                    title={badge.title}
                    href={badge.url}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    <BadgeInner logo={badge.logo} fallback={badge.fallback} />
                  </a>
                ) : (
                  // No public artist page — rendered, but not a link.
                  <div key={badge.key} className="artist-hero-badge" title={badge.title}>
                    <BadgeInner logo={badge.logo} fallback={badge.fallback} />
                  </div>
                ),
              )}
            </div>
          </div>

          <div className="artist-hero-actions">
            <button
              type="button"
              className="library-artist-radio-btn"
              id="library-artist-radio-btn"
              onClick={() => window.playArtistRadio?.()}
            >
              <span className="radio-icon">📻</span>
              <span className="radio-text">Artist Radio</span>
            </button>
            {/*
              The watchlist button is local now (initializeLibraryWatchlistButton's
              port); its ID stays because the guided tour anchors to it
              (helper.js:1518). The enhance button is still driven from outside
              React: checkArtistEnhanceEligibility unhides it and rewrites
              `.enhance-text` with the count, so it has no React state.
            */}
            <button
              type="button"
              className={`library-artist-watchlist-btn${watching ? ' watching' : ''}`}
              id="library-artist-watchlist-btn"
              disabled={watchlistBusy}
              onClick={() => void toggleWatchlist()}
            >
              <span className="watchlist-icon">👁️</span>
              <span className="watchlist-text">
                {watchlistBusy ? 'Loading...' : watching ? 'Watching...' : 'Add to Watchlist'}
              </span>
            </button>
            {/* Only shown when there is something to download. */}
            <div
              className="discog-download-wrap"
              id="discog-download-wrap"
              style={{ display: hasReleases ? '' : 'none' }}
            >
              <button
                type="button"
                className="discog-download-btn discog-btn-compact"
                id="discog-download-btn"
                onClick={() => setDownloadingDiscog(true)}
              >
                <span className="discog-btn-icon">⬇</span>
                <span className="discog-btn-text">Download Discography</span>
                <span className="discog-btn-shimmer" />
              </button>
            </div>
            <button
              type="button"
              className="library-artist-enhance-btn hidden"
              id="library-artist-enhance-btn"
              onClick={() => window.openEnhanceQualityModal?.()}
            >
              <span className="enhance-icon">⚡</span>
              <span className="enhance-text">Enhance Quality</span>
            </button>
          </div>

          <div className="artist-genres-container" id="artist-genres">
            {chips.map((chip) => (
              <span
                key={`${chip.fromLastfm ? 'lfm' : 'genre'}-${chip.label}`}
                className="genre-tag"
                // Last.fm tags are dimmed so they read as secondary.
                style={chip.fromLastfm ? { opacity: 0.6 } : undefined}
              >
                {chip.label}
              </span>
            ))}
          </div>

          {formats.length > 0 ? (
            <div className="artist-formats">
              {formats.map((format) => (
                <span className="artist-format-tag" key={format}>
                  {format}
                </span>
              ))}
            </div>
          ) : null}

          {bio ? (
            <div
              ref={bioRef}
              className={
                `artist-hero-bio${bioExpanded ? ' expanded' : ''}` +
                (bioFits ? '' : ' has-more')
              }
              id="artist-hero-bio"
            >
              <span className="bio-text">{bio}</span>
              {!bioFits || bioExpanded ? (
                <button
                  type="button"
                  className="artist-hero-bio-toggle"
                  aria-expanded={bioExpanded}
                  onClick={() => setBioExpanded((v) => !v)}
                >
                  {bioExpanded ? 'Show less' : 'Read more'}
                </button>
              ) : null}
            </div>
          ) : null}

          <div className="artist-hero-numbers">
            {artist.lastfm_listeners ? (
              <div className="artist-hero-stat" id="artist-hero-listeners">
                <span className="hero-stat-value">{formatHeroNumber(artist.lastfm_listeners)}</span>
                <span className="hero-stat-label">listeners</span>
              </div>
            ) : null}
            {artist.lastfm_playcount ? (
              <div className="artist-hero-stat" id="artist-hero-playcount">
                <span className="hero-stat-value">{formatHeroNumber(artist.lastfm_playcount)}</span>
                <span className="hero-stat-label">plays</span>
              </div>
            ) : null}
          </div>

          {/* Completion bars are library-only: a source artist owns nothing, so
              every bar would read 0/0 and mean nothing. */}
          {isSourceArtist ? null : (
            <div className="collection-overview">
              {CATEGORY_LABELS.map(({ bucket, label }) => {
                const stats = bucketStats(bucket, discography, streamCounts, streamCompleted);
                return (
                  <div className="collection-category" key={bucket}>
                    <span className="category-label">{label}</span>
                    <div className="completion-bar">
                      <div
                        className={`completion-fill${stats.checking ? ' checking' : ''}`}
                        id={`${bucket}-completion-fill`}
                        style={{ width: stats.width }}
                      />
                    </div>
                    <span className="category-stats" id={`${bucket}-stats`}>
                      {stats.text}
                    </span>
                  </div>
                );
              })}
            </div>
          )}

          <EnrichmentCoverage enrichment={enrichment} />
        </div>

        <TopTracksSidebar artistId={artist.id} artistName={artist.name ?? ''} />
      </div>

      {/* Appended to the hero SECTION, not the content row — the vanilla did
          the same and the CSS positions it against the section. */}
      <ArtistDbRecord artist={artist} isSourceArtist={isSourceArtist} />
    </div>
  );
}

/**
 * The vanilla swapped the whole badge for its text on image error. React
 * cannot replace its own parent, so the image hides and the text underneath
 * shows — same visual result, without reaching outside this component.
 */
function BadgeInner({ logo, fallback }: { logo: string; fallback: string }) {
  const [broken, setBroken] = useState(false);
  if (!logo || broken) {
    return <span style={{ fontSize: 9, fontWeight: 700 }}>{fallback}</span>;
  }
  return <img src={logo} alt={fallback} onError={() => setBroken(true)} />;
}
