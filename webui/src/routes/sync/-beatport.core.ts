/**
 * The Beatport tab's pure core, transcribed from beatport-ui.js.
 *
 * Everything here is a straight transcription with no improvement, because the
 * read established that several of these behaviours look like bugs and are not
 * safe to "fix" mid-port — they change what lands on disk. Where a rule is
 * blunt or lossy, that is recorded on the function rather than smoothed over.
 */

/* ── Text ─────────────────────────────────────────────────────────────────── */

/**
 * cleanTrackText (1638-1649) — un-concatenates scraped Beatport strings.
 *
 * Four substitutions, IN ORDER. Transcribed exactly, including two behaviours
 * worth knowing before anyone is tempted to tidy it:
 *
 * 1. Rule one splits on ANY internal capital, so 'McCartney' becomes
 *    'Mc Cartney' and 'MoBlack' becomes 'Mo Black'. That is the accepted cost
 *    of fixing the far more common concatenation, and the vanilla has always
 *    paid it. Changing it here would change folder names on disk.
 * 2. A falsy argument is returned UNCHANGED, not coerced — `cleanTrackText('')`
 *    is `''` and `cleanTrackText(undefined)` is `undefined`.
 */
export function cleanTrackText<T extends string | null | undefined>(text: T): T {
  if (!text) return text;
  let out: string = text;
  out = out.replace(/([a-z$!@#%&*])([A-Z])/g, '$1 $2');
  out = out.replace(/([a-zA-Z]),([a-zA-Z])/g, '$1, $2');
  out = out.replace(/([a-zA-Z])(Mix|Remix|Extended|Version)\b/g, '$1 $2');
  out = out.replace(/\s+/g, ' ');
  out = out.trim();
  return out as T;
}

/**
 * parseBeatportDuration (1990-1997) — to milliseconds.
 *
 * Accepts 'm:ss' or a bare seconds count. Anything unparseable lands on 0
 * because `NaN || 0` is 0 — which is why the vanilla's `|| 0` sits where it
 * does rather than around the whole expression.
 */
export function parseBeatportDuration(raw: string | number | null | undefined): number {
  if (!raw) return 0;
  if (typeof raw === 'string' && raw.includes(':')) {
    const parts = raw.split(':');
    return (parseInt(parts[0], 10) * 60 + parseInt(parts[1], 10)) * 1000 || 0;
  }
  return (parseInt(String(raw), 10) || 0) * 1000;
}

/**
 * The top-10 release card background (1824). A plain string replace, so a URL
 * without that exact segment passes through untouched. Only the BACKGROUND is
 * upscaled — the thumbnail keeps the 95px original.
 */
export function upscaleBeatportArtwork(url: string): string {
  return url.replace('/image_size/95x95/', '/image_size/500x500/');
}

/** 1825: the gradient is baked into the inline style, not a CSS class. */
export function beatportCardBackground(url: string): string {
  return `linear-gradient(rgba(0,0,0,0.7), rgba(0,0,0,0.8)), url('${upscaleBeatportArtwork(url)}')`;
}

/* ── Slides ───────────────────────────────────────────────────────────────── */

export interface BeatportSliderConfig {
  /**
   * The CSS infix. Every class this slider owns is `beatport-<slug>-<part>`,
   * verified against style.css by a test rather than trusted — a wrong slug
   * loses the styling silently, since a missing class is not an error.
   *
   * Note the hero slider's slug is 'rebuild', not 'hero': the markup predates
   * the name, and renaming it would orphan the stylesheet.
   */
  slug: string;
  /** ms between auto-advances. Five sliders, five values — none shared. */
  autoPlayDelay: number;
  /** Items per slide. 1 = one item fills the slide. */
  cardsPerSlide: number;
  /**
   * Whether the slide wraps its cards in a `beatport-<slug>-grid`. The hero is
   * the only one that does not — its single item fills the slide itself.
   */
  hasGrid: boolean;
  /** Whether the last slide is padded out with placeholder cards. */
  padsLastSlide: boolean;
  /** What a failed load renders. */
  onFailure: 'keep-static-markup' | 'error-block' | 'nothing';
  /**
   * WHEN the vanilla marks the slider loaded, which decides whether a return to
   * the tab re-fetches.
   *
   * The hero marks itself initialised BEFORE the fetch (31-34), so a failed
   * load is never retried for the rest of the session — the section simply
   * stays on its static placeholders. The other four mark themselves only
   * INSIDE `.then(success => …)` (369, 1056, 1355) or on their state flag
   * (700), so a failure retries next time and a success does not.
   *
   * This is load-bearing for the port: Beatport scraping is slow and
   * rate-limited, and a React section that re-fetched on every tab visit would
   * hammer it in a way the vanilla never does.
   */
  marksLoadedBeforeFetch: boolean;
  /**
   * The section's placeholder copy, from index.html (2823, 2947, 2985, 3022,
   * 3058) — heading first, subtitle second.
   *
   * WHY THE PORT OWNS THIS. In the vanilla the block is page markup: present
   * before any script runs, replaced when the data lands, so no loader ever
   * draws one. The flip DELETES that markup — so a section that drew nothing
   * while loading would be a blank strip, and for the three whose failure arm
   * replaces nothing (hero, charts, DJ) it would stay blank forever, where
   * today it keeps showing this copy.
   *
   * Found by a coverage sweep rather than by a test. The same reasoning was
   * already applied to the three top-10 lists; it had simply not been carried
   * back to the sliders.
   */
  loadingTitle: string;
  loadingSubtitle: string;
  /**
   * The heading level the placeholder/error block uses. NOT cosmetic and NOT
   * uniform: the hero's is `h2` (index.html 2824) and the four grid sections
   * use `h3` (2949, 2987, 3024, 3060), and `.beatport-*-loading-content h2`
   * and `… h3` are separately sized in the stylesheet. The port rendered `h3`
   * for all five, so the hero's placeholder was styled as a subheading.
   */
  loadingHeadingLevel: 'h2' | 'h3';
  /**
   * The section's own heading, rendered above the slider inside
   * `.beatport-{slug}-section > .beatport-{slug}-header`.
   *
   * `null` for the hero ALONE, and that is the vanilla's shape rather than an
   * omission: `#beatport-rebuild-content` goes straight into the slider
   * container with no section wrapper and no title (2817-2819), and no
   * `.beatport-rebuild-header/-title/-subtitle` rule exists in style.css. The
   * other four each carry one (2937, 2974, 3012, 3049).
   */
  sectionHeading: { title: string; subtitle: string } | null;
}

/** Every class name a slider owns, derived from its slug in one place. */
export function beatportSliderClasses(slug: string) {
  return {
    /**
     * The OUTERMOST box — the section, and its heading block. index.html wraps
     * each grid slider as
     * `.beatport-{slug}-section > .beatport-{slug}-header > h2.title + p.subtitle`
     * and then the slider container (2936, 2973, 3011, 3048).
     *
     * All four were missing from the port, which is why every section title
     * was gone on screen: the section carries the vertical rhythm and the
     * header carries the title and subtitle typography. Same failure as the
     * missing slider box — a box CSS cannot find is a box that does nothing,
     * and no behavioural test can see it.
     *
     * The hero uses none of these; see BeatportSliderConfig.sectionHeading.
     */
    section: `beatport-${slug}-section`,
    header: `beatport-${slug}-header`,
    title: `beatport-${slug}-title`,
    subtitle: `beatport-${slug}-subtitle`,
    container: `beatport-${slug}-slider-container`,
    /**
     * The MIDDLE wrapper, between the container and the track — and the one
     * that makes the slider visible at all. index.html nests
     * `.beatport-{slug}-slider-container > .beatport-{slug}-slider > track/nav/
     * indicators` for all five sliders, and this element is what carries
     * `height: 500px`, `position: relative` and `overflow: hidden`
     * (style.css 17059). Without it the track has no height and the
     * absolutely-positioned nav and indicators have nothing to anchor to, so
     * the whole slider collapses to nothing — which is exactly how the hero
     * "disappeared" the first time this rendered live.
     *
     * The vanilla also gives it `id="beatport-{slug}-slider"`. Not reproduced:
     * the only readers are five getElementById calls in beatport-ui.js, the
     * file the flip deletes, and no CSS rule uses the id.
     */
    slider: `beatport-${slug}-slider`,
    track: `beatport-${slug}-slider-track`,
    slide: `beatport-${slug}-slide`,
    indicator: `beatport-${slug}-indicator`,
    indicators: `beatport-${slug}-slider-indicators`,
    grid: `beatport-${slug}-grid`,
    /** index.html wraps the two buttons in this; it is what positions them. */
    nav: `beatport-${slug}-slider-nav`,
    navButton: `beatport-${slug}-nav-btn`,
    /** The error block (638-650, 983-994). Present for all five in the CSS,
     *  even the three that never render one. */
    loading: `beatport-${slug}-loading`,
    loadingContent: `beatport-${slug}-loading-content`,
  };
}

/**
 * The five sliders' settings, from the read (see the comparison table in
 * SYNC_PORT_AUDIT.md). Every function in the vanilla is commented "copied from"
 * its predecessor and every one has drifted, so a shared component MUST be
 * driven by this table rather than by one slider's behaviour.
 *
 * 'nothing' is not an oversight: charts and DJ render no error AND never mark
 * themselves initialised, which makes them the only two that retry on re-entry.
 */
export const BEATPORT_SLIDERS: Readonly<Record<string, BeatportSliderConfig>> = {
  hero: {
    slug: 'rebuild',
    autoPlayDelay: 5000,
    cardsPerSlide: 1,
    hasGrid: false,
    padsLastSlide: false,
    onFailure: 'keep-static-markup',
    marksLoadedBeforeFetch: true,
    loadingTitle: '🎯 Loading Fresh Beatport Tracks...',
    loadingSubtitle: 'Fetching the latest music from Beatport',
    loadingHeadingLevel: 'h2',
    sectionHeading: null,
  },
  releases: {
    slug: 'releases',
    autoPlayDelay: 8000,
    cardsPerSlide: 10,
    hasGrid: true,
    padsLastSlide: true,
    onFailure: 'error-block',
    marksLoadedBeforeFetch: false,
    loadingTitle: '📀 Loading New Releases...',
    loadingSubtitle: 'Fetching the latest albums and EPs',
    loadingHeadingLevel: 'h3',
    sectionHeading: {
      title: '🆕 New Releases',
      subtitle: 'Latest albums and EPs from Beatport',
    },
  },
  hypePicks: {
    slug: 'hype-picks',
    autoPlayDelay: 4000,
    cardsPerSlide: 10,
    hasGrid: true,
    padsLastSlide: true,
    onFailure: 'error-block',
    marksLoadedBeforeFetch: false,
    loadingTitle: '🔥 Loading Hype Picks...',
    loadingSubtitle: 'Fetching the hottest trending tracks',
    loadingHeadingLevel: 'h3',
    sectionHeading: {
      title: '🔥 Hype Picks',
      subtitle: 'Editor selected trending tracks from Beatport',
    },
  },
  charts: {
    slug: 'charts',
    autoPlayDelay: 10000,
    cardsPerSlide: 10,
    hasGrid: true,
    padsLastSlide: false,
    onFailure: 'nothing',
    marksLoadedBeforeFetch: false,
    loadingTitle: '📊 Loading Featured Charts...',
    loadingSubtitle: 'Fetching top chart collections',
    loadingHeadingLevel: 'h3',
    sectionHeading: {
      title: '🔥 Featured Charts',
      subtitle: 'Top chart collections from Beatport creators',
    },
  },
  dj: {
    slug: 'dj',
    autoPlayDelay: 12000,
    cardsPerSlide: 3,
    hasGrid: true,
    padsLastSlide: false,
    onFailure: 'nothing',
    marksLoadedBeforeFetch: false,
    loadingTitle: '🎧 Loading DJ Charts...',
    loadingSubtitle: 'Fetching curated DJ selections',
    loadingHeadingLevel: 'h3',
    sectionHeading: {
      title: '🎧 DJ Charts',
      subtitle: 'Curated charts from top DJs and artists',
    },
  },
};

/** ceil(items / perSlide) — the hero slider is 1:1, so this covers it too. */
export function slideCount(itemCount: number, cardsPerSlide: number): number {
  if (cardsPerSlide <= 0) return 0;
  return Math.ceil(itemCount / cardsPerSlide);
}

/**
 * Wrap-around in both directions (239-243 and its four twins). Past the end
 * lands on 0; before the start lands on the last.
 */
export function wrapSlideIndex(index: number, totalSlides: number): number {
  if (totalSlides <= 0) return 0;
  if (index < 0) return totalSlides - 1;
  if (index >= totalSlides) return 0;
  return index;
}

/**
 * Each slide gets exactly one of these (252-262). The CSS transition needs the
 * DIRECTION, so this is not an is-active boolean.
 */
export function slidePosition(index: number, currentSlide: number): 'active' | 'prev' | 'next' {
  if (index === currentSlide) return 'active';
  return index < currentSlide ? 'prev' : 'next';
}

/* ── Genres ───────────────────────────────────────────────────────────────── */

/**
 * 2869-2871. The genre hero endpoint returns RELATIVE release urls, alone among
 * the Beatport endpoints — every other one hands back an absolute link.
 *
 * That matters beyond the href: the url is POSTed to /api/beatport/release-
 * metadata, so a relative one would reach the scraper as a path with no host.
 * The test is `startsWith('http')`, which also lets an already-absolute url
 * through untouched.
 */
export function absoluteBeatportUrl(url: string | undefined | null): string {
  if (!url) return '';
  return url.startsWith('http') ? url : `https://www.beatport.com${url}`;
}

/**
 * 2382-2392: nine names Beatport returns that are section headings rather than
 * genres. Matched lower-cased and trimmed, exact equality — not a prefix test,
 * so a real genre containing one of these words survives.
 */
export const EXCLUDED_GENRE_NAMES: readonly string[] = [
  'open format',
  'electronic',
  'genres',
  'browse',
  'charts',
  'new releases',
  'trending',
  'featured',
  'popular',
];

export function isExcludedGenre(name: string): boolean {
  return EXCLUDED_GENRE_NAMES.includes(name.toLowerCase().trim());
}

export function filterBeatportGenres<T extends { name: string }>(genres: readonly T[]): T[] {
  return genres.filter((genre) => !isExcludedGenre(genre.name));
}

/* ── The chart → download-modal bridge (1999-2064) ────────────────────────── */

export interface BeatportScrapedTrack {
  title?: string;
  artist?: string;
  mix_name?: string;
  duration?: string | number;
  release_name?: string;
  release_id?: string | number;
  release_image?: string;
  release_date?: string;
}

export interface DownloadModalAlbum {
  id: string;
  name: string;
  album_type: string;
  images: { url: string }[];
  total_tracks: number;
  release_date?: string;
}

export interface DownloadModalTrack {
  id: string;
  name: string;
  artists: string[];
  duration_ms: number;
  track_number: number;
  disc_number: number;
  album: DownloadModalAlbum;
}

/** 2004-2010. A chart is a COMPILATION; only a release is a real album. */
export function buildChartAlbum(
  albumId: string,
  chartName: string,
  chartImage: string | null | undefined,
  trackCount: number,
): DownloadModalAlbum {
  return {
    id: albumId,
    name: chartName,
    album_type: 'compilation',
    images: chartImage ? [{ url: chartImage }] : [],
    total_tracks: trackCount,
  };
}

/**
 * 2025-2028. The mix name is appended UNLESS it is 'original mix' — compared
 * case-insensitively, because Beatport is inconsistent about the casing.
 */
export function buildChartTrackName(track: BeatportScrapedTrack): string {
  let name = cleanTrackText(track.title || 'Unknown Title');
  if (track.mix_name && track.mix_name.toLowerCase() !== 'original mix') {
    name = `${name} (${cleanTrackText(track.mix_name)})`;
  }
  return name;
}

/**
 * 2031-2034. Split on commas so the download engine gets real artist names and
 * builds the right folders. Note the split happens AFTER cleanTrackText, which
 * has already normalised 'A,B' to 'A, B'.
 */
export function splitBeatportArtists(rawArtist: string | null | undefined): string[] {
  const cleaned = cleanTrackText(rawArtist || 'Unknown Artist');
  if (!cleaned.includes(',')) return [cleaned];
  return cleaned
    .split(',')
    .map((a) => a.trim())
    .filter((a) => a);
}

/**
 * 2012-2045. Per-track release metadata is preferred when the scrape found it;
 * otherwise every track shares the one compilation album object.
 */
export function buildChartTracks(
  tracks: readonly BeatportScrapedTrack[],
  chartAlbum: DownloadModalAlbum,
): DownloadModalTrack[] {
  return tracks.map((track, index) => {
    const hasRelease = Boolean(track.release_name && track.release_name.length > 0);
    const trackAlbum: DownloadModalAlbum = hasRelease
      ? {
          id: `beatport_release_${track.release_id || index}`,
          name: cleanTrackText(track.release_name) as string,
          album_type: 'single',
          images: track.release_image ? [{ url: track.release_image }] : [],
          release_date: track.release_date || '',
          total_tracks: 1,
        }
      : chartAlbum;

    return {
      id: `beatport_chart_${index}`,
      name: buildChartTrackName(track),
      artists: splitBeatportArtists(track.artist),
      duration_ms: parseBeatportDuration(track.duration),
      track_number: index + 1,
      disc_number: 1,
      album: trackAlbum,
    };
  });
}

/** 2050: every chart is credited to the same synthetic artist. */
export const BEATPORT_COMPILATION_ARTIST = { id: 'beatport_various', name: 'Various Artists' };

/**
 * The seventh argument to openDownloadMissingModalForArtistAlbum
 * (shared-helpers.js 1763: `contextType = 'artist_album'`).
 *
 * Charts pass 'playlist' EXPLICITLY (2059). Releases pass only six arguments
 * (1900-1907), so they take the default. Easy to miss precisely because the
 * release call looks complete — the difference is an argument that is not
 * there. Both pass `false` for showLoadingOverlay, because Beatport is already
 * showing its own.
 */
export function beatportDownloadContext(kind: 'chart' | 'release'): 'playlist' | 'artist_album' {
  return kind === 'chart' ? 'playlist' : 'artist_album';
}

/**
 * Which image the release's download bubble gets (1910).
 *
 * The album art the metadata endpoint returned wins; the card's own thumbnail
 * is the fallback; then the empty string. Charts do not go through this — they
 * hand registerBeatportDownload the chart image directly.
 */
export function releaseBubbleImage(
  album: { images?: { url: string }[] } | null | undefined,
  releaseImageUrl: string | null | undefined,
): string {
  const fromAlbum = album?.images && album.images.length > 0 ? album.images[0].url : '';
  return fromAlbum || releaseImageUrl || '';
}

/* ── The synthetic ids (1896, 1939, 2005, 2047) ───────────────────────────── */

/**
 * `Math.random().toString(36).substr(2, n)` — the vanilla's id suffix.
 *
 * `substr` is deprecated but not wrong; `slice(2, 2 + n)` is the same string for
 * every input this can receive, since the argument is always a positive
 * fractional number and both forms count from the same offset. Injected rather
 * than called directly so a test can pin the id.
 */
export function beatportRandomToken(random: () => number, length: number): string {
  return random()
    .toString(36)
    .slice(2, 2 + length);
}

/**
 * The four id shapes, kept together because they are NOT interchangeable:
 *
 *  - a release's virtual playlist (1896) and a chart's (2047) both carry a
 *    9-char suffix, but different prefixes,
 *  - the chart's ALBUM id (2005) has no random suffix at all — just the
 *    timestamp — so two charts opened in the same millisecond share it,
 *  - the enrichment id (1939) uses a SHORTER 6-char suffix.
 *
 * Transcribed as-is. The album-id collision is not reachable in practice (a
 * chart open costs a scrape), and widening it would change the id the download
 * engine keys on.
 */
export function beatportReleasePlaylistId(now: () => number, random: () => number): string {
  return `beatport_release_${now()}_${beatportRandomToken(random, 9)}`;
}

export function beatportChartPlaylistId(now: () => number, random: () => number): string {
  return `beatport_chart_${now()}_${beatportRandomToken(random, 9)}`;
}

export function beatportChartAlbumId(now: () => number): string {
  return `beatport_chart_${now()}`;
}

export function beatportEnrichmentId(now: () => number, random: () => number): string {
  return `enrich_${now()}_${beatportRandomToken(random, 6)}`;
}

/**
 * 1891-1894. A release's tracks arrive with artists as objects OR strings
 * depending on the scrape; the download modal wants strings either way.
 */
export function normaliseReleaseTrackArtists<T extends { artists: (string | { name: string })[] }>(
  tracks: readonly T[],
): (Omit<T, 'artists'> & { artists: string[] })[] {
  return tracks.map((track) => ({
    ...track,
    artists: track.artists.map((a) => (typeof a === 'object' ? a.name : a)),
  }));
}
