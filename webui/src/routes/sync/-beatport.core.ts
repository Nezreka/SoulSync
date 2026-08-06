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
  /** ms between auto-advances. Five sliders, five values — none shared. */
  autoPlayDelay: number;
  /** Items per slide. 1 = one item fills the slide. */
  cardsPerSlide: number;
  /** Whether the last slide is padded out with placeholder cards. */
  padsLastSlide: boolean;
  /** What a failed load renders. */
  onFailure: 'keep-static-markup' | 'error-block' | 'nothing';
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
    autoPlayDelay: 5000,
    cardsPerSlide: 1,
    padsLastSlide: false,
    onFailure: 'keep-static-markup',
  },
  releases: {
    autoPlayDelay: 8000,
    cardsPerSlide: 10,
    padsLastSlide: true,
    onFailure: 'error-block',
  },
  hypePicks: {
    autoPlayDelay: 4000,
    cardsPerSlide: 10,
    padsLastSlide: true,
    onFailure: 'error-block',
  },
  charts: { autoPlayDelay: 10000, cardsPerSlide: 10, padsLastSlide: false, onFailure: 'nothing' },
  dj: { autoPlayDelay: 12000, cardsPerSlide: 3, padsLastSlide: false, onFailure: 'nothing' },
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
