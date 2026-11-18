import { browserSafeImageUrl } from '@/platform/artwork-thumb';

import type {
  ArtistAboutSource,
  ArtistInfo,
  Discography,
  DiscographyBucket,
} from './-artist-detail.types';

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
      if (isUsableHeroImageUrl(release?.image_url)) return browserSafeImageUrl(release.image_url);
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
  const artistImage = isUsableHeroImageUrl(artist.image_url)
    ? browserSafeImageUrl(artist.image_url)
    : '';
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
  if (stage === 'deezer') {
    return `https://api.deezer.com/artist/${String(artist.deezer_id)}/image?size=big`;
  }
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

/**
 * Genre chips: the artist's own genres, then up to 5 Last.fm tags that are not
 * already present.
 *
 * Three things the vanilla does here that are easy to lose:
 *   - `lastfm_tags` may arrive as a JSON STRING rather than an array, and a
 *     parse failure is swallowed — bad tags must not take the hero down
 *   - the dedup is case-INSENSITIVE against existing genres
 *   - the extra tags are capped at 5 and rendered dimmed (opacity 0.6) so they
 *     read as secondary to real genres
 */
export interface GenreChip {
  label: string;
  /** where a tag chip came from; tag chips render dimmed, the artist's own
   *  genres ('genre') do not. musicbrainz only when last.fm has no tags. */
  source: 'genre' | 'lastfm' | 'musicbrainz';
}

function parseTags(raw: unknown): unknown[] {
  let tags = raw;
  if (typeof tags === 'string') {
    try {
      tags = JSON.parse(tags);
    } catch {
      // Malformed tags are ignored rather than thrown — the vanilla swallowed
      // this too, and the hero must still render.
      tags = null;
    }
  }
  return Array.isArray(tags) ? tags : [];
}

/** the tags to show and where they came from: last.fm's, else the free
 *  source's (musicbrainz) from `about`. */
export function artistTags(artist: ArtistInfo): {
  tags: unknown[];
  source: 'lastfm' | 'musicbrainz' | null;
} {
  const lastfm = parseTags(artist.lastfm_tags);
  if (lastfm.length) return { tags: lastfm, source: 'lastfm' };
  const about = artist.about;
  if (about?.tags?.length && about.tags_source) {
    return {
      tags: about.tags,
      source: about.tags_source === 'lastfm' ? 'lastfm' : 'musicbrainz',
    };
  }
  return { tags: [], source: null };
}

export function buildGenreChips(artist: ArtistInfo): GenreChip[] {
  const genres = Array.isArray(artist.genres) ? artist.genres : [];
  const chips: GenreChip[] = genres.map((label) => ({ label: String(label), source: 'genre' }));

  const { tags, source } = artistTags(artist);
  if (!source || tags.length === 0) return chips;

  const existing = new Set(genres.map((g) => String(g).toLowerCase()));
  for (const tag of tags) {
    if (chips.filter((c) => c.source !== 'genre').length >= 5) break;
    const label = String(tag);
    if (existing.has(label.toLowerCase())) continue;
    chips.push({ label, source });
  }
  return chips;
}

/** the names the source notes use */
export const ABOUT_SOURCE_NAMES: Record<ArtistAboutSource, string> = {
  lastfm: 'Last.fm',
  audiodb: 'TheAudioDB',
  wikipedia: 'Wikipedia',
  musicbrainz: 'MusicBrainz',
  listenbrainz: 'ListenBrainz',
  deezer: 'Deezer',
};

export interface ArtistBio {
  text: string;
  /** null for last.fm and the media server's own summary: those carry no note */
  source: ArtistAboutSource | null;
  url: string | null;
}

/**
 * the bio and where it came from: last.fm, then the media server's summary,
 * then the free sources (theaudiodb, wikipedia). only a free source gets a
 * note, so a last.fm user's hero looks exactly as before.
 */
export function pickArtistBio(artist: ArtistInfo): ArtistBio | null {
  const lastfm = cleanArtistBio(artist.lastfm_bio);
  if (lastfm) return { text: lastfm, source: null, url: null };
  const summary = cleanArtistBio(artist.bio);
  if (summary) return { text: summary, source: null, url: null };
  const about = artist.about;
  const text = cleanArtistBio(about?.bio);
  if (!text) return null;
  const source = about?.bio_source && about.bio_source !== 'lastfm' ? about.bio_source : null;
  return { text, source, url: source ? (about?.bio_url ?? null) : null };
}

export interface HeroNumber {
  id: string;
  value: number;
  label: string;
}

/**
 * the hero's numbers, each labelled as what it is. last.fm listeners/plays
 * keep their old labels; listenbrainz numbers say listenbrainz, deezer fans
 * say fans. never one source's number under another's name.
 */
export function heroNumbers(artist: ArtistInfo): HeroNumber[] {
  const out: HeroNumber[] = [];
  const listeners = Number(artist.lastfm_listeners) || 0;
  const plays = Number(artist.lastfm_playcount) || 0;
  const about = artist.about;
  if (listeners || plays) {
    if (listeners) out.push({ id: 'listeners', value: listeners, label: 'listeners' });
    if (plays) out.push({ id: 'playcount', value: plays, label: 'plays' });
  } else if (about?.popularity_source === 'lastfm') {
    if (about.listeners) out.push({ id: 'listeners', value: about.listeners, label: 'listeners' });
    if (about.plays) out.push({ id: 'playcount', value: about.plays, label: 'plays' });
  } else if (about?.popularity_source === 'listenbrainz') {
    if (about.listeners)
      out.push({ id: 'listeners', value: about.listeners, label: 'ListenBrainz listeners' });
    if (about.plays)
      out.push({ id: 'playcount', value: about.plays, label: 'ListenBrainz listens' });
  }
  if (about?.fans && about.fans_source === 'deezer') {
    out.push({ id: 'fans', value: about.fans, label: 'Deezer fans' });
  }
  return out;
}

/**
 * The completion bar WHILE the stream is running, from
 * updateCategoryStatsFromStream (library.js:2090).
 *
 * Deliberately different from categoryStats: the denominator is only what has
 * RESOLVED so far (owned + missing), not the whole bucket. So a 12-album
 * discography reads "2/3" after three results, not "2/12" — the bar tracks
 * progress through the check rather than the final answer, and the "checking"
 * class is dropped as soon as the first result lands.
 *
 * An empty resolved set is 100%, matching categoryStats rather than
 * summaryStats.
 */
export function streamCategoryStats(ownedCount: number, missingCount: number): CategoryStats {
  const total = ownedCount + missingCount;
  const completion = total > 0 ? Math.round((ownedCount / total) * 100) : 100;
  return { text: `${ownedCount}/${total}`, width: `${completion}%`, checking: false };
}

/**
 * The bars once the stream reports `complete`, from recalculateSummaryStats
 * (library.js:2106).
 *
 * The vanilla recounted the CARDS rather than trusting its running tallies, and
 * that recount excludes anything still unresolved — so a release the backend
 * never reported on drops out of the denominator instead of pinning the bar
 * back to "...". That is the only reason this is not just categoryStats: after
 * a clean stream the two agree.
 */
export function resolvedCategoryStats(releases: { owned?: boolean | null }[] = []): CategoryStats {
  let owned = 0;
  let missing = 0;
  for (const release of releases) {
    if (release.owned === true) owned += 1;
    else if (release.owned === false) missing += 1;
  }
  return streamCategoryStats(owned, missing);
}

/**
 * Artist-level format tags, shown under the genre chips once the stream ends.
 *
 * Accumulated from OWNED releases only (tallyEvent), sorted, and rendered only
 * when non-empty — the vanilla removed the block entirely rather than leaving
 * an empty row.
 */
export function artistFormatTags(formats: Set<string> | undefined): string[] {
  if (!formats || formats.size === 0) return [];
  return [...formats].sort();
}
