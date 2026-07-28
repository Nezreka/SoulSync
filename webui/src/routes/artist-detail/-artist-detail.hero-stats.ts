import type { ArtistInfo, Discography, DiscographyBucket } from './-artist-detail.types';

/**
 * Hero derivations from updateArtistHeroSection / updateCategoryStats /
 * _isUsableArtistHeroImageUrl / _getArtistHeroReleaseImage (library.js 916-1060).
 */

/**
 * The string 'null' is rejected explicitly, not just null: the backend can
 * serialise a missing image as the literal text "null", which would otherwise
 * be requested as a relative url and 404.
 */
export function isUsableHeroImageUrl(url: unknown): url is string {
  return typeof url === 'string' && url.trim() !== '' && url !== 'null';
}

/** First usable release cover, scanned albums -> eps -> singles. */
export function heroReleaseImage(discography: Discography | undefined): string {
  for (const bucket of ['albums', 'eps', 'singles'] as const) {
    for (const release of discography?.[bucket] ?? []) {
      if (isUsableHeroImageUrl(release?.image_url)) return release.image_url as string;
    }
  }
  return '';
}

export interface HeroImage {
  /** What to show first; '' means go straight to the music-note fallback. */
  primary: string;
  /** The artist's own image, if usable — '' when we fell back to release art. */
  artistImage: string;
  /** Release cover used as a fallback, if any. */
  releaseImage: string;
}

/**
 * The artist's own photo wins; otherwise the first release cover stands in, and
 * the blurred hero background uses whichever won.
 */
export function heroImage(artist: ArtistInfo, discography: Discography | undefined): HeroImage {
  const artistImage = isUsableHeroImageUrl(artist.image_url) ? artist.image_url : '';
  const releaseImage = heroReleaseImage(discography);
  return { primary: artistImage || releaseImage, artistImage, releaseImage };
}

/**
 * The image error chain: Deezer, then release art, then the icon.
 *
 * The vanilla tracked this with two dataset flags so neither hop could repeat
 * (a failing Deezer url would otherwise loop forever). `triedReleaseFallback`
 * starts already-set when the artist had no image of their own, because in
 * that case the release cover IS the primary and re-trying it is pointless.
 */
export type HeroImageStage = 'primary' | 'deezer' | 'release' | 'fallback';

export function nextHeroImageStage(
  stage: HeroImageStage,
  artist: ArtistInfo,
  image: HeroImage,
): HeroImageStage {
  if (stage === 'primary' && artist.deezer_id) return 'deezer';
  const releaseUsed = !image.artistImage;
  if ((stage === 'primary' || stage === 'deezer') && image.releaseImage && !releaseUsed) {
    return 'release';
  }
  return 'fallback';
}

export function heroImageSrc(stage: HeroImageStage, artist: ArtistInfo, image: HeroImage): string {
  if (stage === 'deezer') return `https://api.deezer.com/artist/${artist.deezer_id}/image?size=big`;
  if (stage === 'release') return image.releaseImage;
  return image.primary;
}

export interface CategoryStats {
  /** "3/12", or "..." while any ownership check is pending. */
  text: string;
  /** Bar width as a percentage string. */
  width: string;
  /** The bar animates instead of showing a real value while checking. */
  checking: boolean;
}

/**
 * Per-category completion bar.
 *
 * Note the default: an EMPTY category is 100%, not 0% — you cannot be missing
 * anything from a bucket with nothing in it. That differs from
 * updateArtistSummaryStats, which uses 0 for the same situation.
 */
export function categoryStats(releases: { owned?: boolean | null }[] = []): CategoryStats {
  const checking = releases.some((r) => r.owned === null);
  const owned = releases.filter((r) => r.owned === true).length;
  const total = releases.length;
  const completion = total > 0 ? Math.round((owned / total) * 100) : 100;

  if (checking) return { text: '...', width: '100%', checking: true };
  return { text: `${owned}/${total}`, width: `${completion}%`, checking: false };
}

export interface SummaryStats {
  owned: string;
  missing: string;
  completion: string;
}

/**
 * The album-only summary. Unlike categoryStats, an empty album list is 0% here
 * — reproduced as-is rather than harmonised.
 */
export function summaryStats(discography: Discography): SummaryStats {
  const all = [
    ...(discography.albums ?? []),
    ...(discography.eps ?? []),
    ...(discography.singles ?? []),
  ];
  const checking = all.some((r) => r.owned === null);
  const albums = discography.albums ?? [];
  const owned = albums.filter((a) => a.owned === true).length;
  const missing = albums.filter((a) => a.owned === false).length;
  const pct = albums.length > 0 ? Math.round((owned / albums.length) * 100) : 0;

  return {
    owned: checking ? '...' : String(owned),
    missing: checking ? '...' : String(missing),
    completion: checking ? 'Checking...' : `${pct}%`,
  };
}

/** Last.fm counts: 1.2M / 3.4K / 999, with a trailing ".0" trimmed. */
export function formatHeroNumber(n: unknown): string {
  const value = Number(n);
  if (!value || value <= 0) return '0';
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1).replace(/\.0$/, '')}M`;
  if (value >= 1000) return `${(value / 1000).toFixed(1).replace(/\.0$/, '')}K`;
  return value.toLocaleString();
}

/**
 * Last.fm bios arrive as HTML and always carry a "Read more on Last.fm" link.
 * Anchors are removed WHOLE (link text included), then any other tags are
 * stripped. Returns '' when nothing readable is left, which hides the block.
 */
export function cleanArtistBio(bio: unknown): string {
  if (typeof bio !== 'string' || !bio.trim()) return '';
  return bio
    .replace(/<a\b[^>]*>.*?<\/a>/gi, '')
    .replace(/<[^>]+>/g, '')
    .trim();
}

/** The Download Discography button appears only if there is anything to download. */
export function totalReleaseCount(discography: Discography | undefined): number {
  return (['albums', 'eps', 'singles'] as DiscographyBucket[]).reduce(
    (sum, bucket) => sum + (discography?.[bucket]?.length ?? 0),
    0,
  );
}
