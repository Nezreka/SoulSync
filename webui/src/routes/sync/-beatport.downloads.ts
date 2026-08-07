/**
 * The Beatport download bridge — beatport-ui.js 1855-2228.
 *
 * This is the region where a click stops being a UI event and becomes files on
 * disk, so it is transcribed rather than tidied. Three things about it are worth
 * knowing before reading the code:
 *
 * 1. **Four card types share ONE release handler.** The hero slide (149), the
 *    releases slider (502), the hype picks (974) and the top-10 release cards
 *    (1834) all call handleBeatportReleaseCardClick. What differs between them
 *    is only how they answer "which release was clicked" — closure, URL lookup,
 *    re-reading rendered text, index alignment — and that question is settled in
 *    the components, not here.
 *
 * 2. **A release is an ALBUM; a chart is a COMPILATION.** They take different
 *    endpoints, different context types and different bubble images. The two
 *    calls look alike and are not.
 *
 * 3. **The double-click latch is applied five different ways** in the vanilla
 *    and every one is reproduced below, because they are not equivalent — see
 *    the note on `latch`.
 */

import {
  type BeatportChart,
  enrichBeatportTracks,
  extractBeatportChart,
  fetchBeatportGenreTracks,
  fetchBeatportReleaseMetadata,
  fetchBeatportTop100,
} from './-beatport.api';
import {
  type BeatportScrapedTrack,
  beatportChartAlbumId,
  beatportChartPlaylistId,
  beatportDownloadContext,
  beatportEnrichmentId,
  beatportReleasePlaylistId,
  buildChartAlbum,
  buildChartTracks,
  BEATPORT_COMPILATION_ARTIST,
  normaliseReleaseTrackArtists,
  releaseBubbleImage,
} from './-beatport.core';

/* ── The double-click latch (1859, 1931, 2002, 2162, 2198) ────────────────── */

/**
 * `_beatportModalOpening`, module-scoped exactly as in the vanilla.
 *
 * FIVE different applications, all real and none interchangeable:
 *  - release clicks: set on entry, cleared on EVERY exit — so it is held for
 *    exactly as long as the work takes;
 *  - the two Top 100 buttons (and, in the genre browser, its Top 100 and chart
 *    buttons): set on entry and cleared by a blind 2s timer, so a scrape slower
 *    than 2s reopens the gate mid-flight;
 *  - openBeatportChartAsDownloadModal: clears it UNCONDITIONALLY, whether or not
 *    it set it — the vanilla's comment says this is so a cached (fast)
 *    enrichment can still open the modal;
 *  - the featured-chart and DJ-chart card clicks: do not touch it at all, so
 *    those two are not double-click guarded.
 *
 * Reproduced rather than unified. Making it consistent would change which
 * clicks are swallowed, and this is the path that queues downloads.
 */
let modalOpening = false;

/** Test seam, and the only way to clear the latch other than the flows. */
export function resetBeatportModalLatch(): void {
  modalOpening = false;
}

export function isBeatportModalOpening(): boolean {
  return modalOpening;
}

/* ── The environment this bridge needs ────────────────────────────────────── */

export interface BeatportDownloadEnv {
  showToast: (message: string, type?: string) => void;
  showLoadingOverlay: (message: string) => void;
  hideLoadingOverlay: () => void;
  /**
   * 1964-1966: the vanilla writes the enrichment progress STRAIGHT into the
   * overlay's message node rather than re-calling showLoadingOverlay — and
   * null-guards it. Kept that way: downloads.js's showLoadingOverlay does not
   * null-guard `#loading-overlay`, so routing through it would turn a missing
   * overlay from a silent no-op into a thrown error inside the download path.
   */
  setOverlayMessage: (message: string) => void;
  openDownloadModal: (
    virtualPlaylistId: string,
    playlistName: string,
    tracks: unknown[],
    album: unknown,
    artist: unknown,
    showLoadingOverlay?: boolean,
    contextType?: string,
  ) => void | Promise<void>;
  /** shared-helpers.js 3390 — the Beatport download bubble registry. */
  registerDownload: (name: string, image: string, virtualPlaylistId: string) => void;
  now: () => number;
  random: () => number;
  /** 2164: the blind 2s latch release. */
  schedule: (callback: () => void, ms: number) => void;
  /**
   * The gap between enrichment polls (1957). Injectable for the same reason the
   * api layer's is: a test that waits three real 800ms sleeps costs more than
   * everything else in this file put together.
   */
  sleep?: (ms: number) => Promise<void>;
}

export function defaultBeatportDownloadEnv(): BeatportDownloadEnv {
  return {
    showToast: (message, type) => window.showToast?.(message, type),
    showLoadingOverlay: (message) => window.showLoadingOverlay?.(message),
    hideLoadingOverlay: () => window.hideLoadingOverlay?.(),
    setOverlayMessage: (message) => {
      const node = document.querySelector('#loading-overlay .loading-message');
      if (node) node.textContent = message;
    },
    openDownloadModal: (...args) => window.openDownloadMissingModalForArtistAlbum?.(...args),
    registerDownload: (name, image, virtualPlaylistId) =>
      window.registerBeatportDownload?.(name, image, virtualPlaylistId),
    now: () => Date.now(),
    random: () => Math.random(),
    schedule: (callback, ms) => {
      setTimeout(callback, ms);
    },
  };
}

/* ── Releases (1858-1923) ─────────────────────────────────────────────────── */

export interface BeatportClickedRelease {
  title?: string;
  artist?: string;
  label?: string;
  url?: string;
  image_url?: string;
}

/**
 * handleBeatportReleaseCardClick. Opens the release as a real album, with its
 * real artist — the one Beatport flow that is not a compilation.
 *
 * The 'No release URL available' toast is only reachable from the top-10
 * release cards: the other three call sites refuse to attach a handler at all
 * when the url is missing or '#'.
 */
export async function openBeatportRelease(
  release: BeatportClickedRelease,
  env: BeatportDownloadEnv,
): Promise<void> {
  if (modalOpening) return;
  modalOpening = true;

  if (!release.url || release.url === '#') {
    modalOpening = false;
    env.showToast('No release URL available', 'error');
    return;
  }

  try {
    env.showToast(`Loading ${release.title}...`, 'info');
    env.showLoadingOverlay(`Getting tracks from ${release.title}...`);

    const data = await fetchBeatportReleaseMetadata(release.url);

    // 1884, plus `!data.album`. The vanilla has no album check and instead
    // throws a TypeError one line later when it logs `data.album.name`; both
    // land in the same catch and show a toast. Only the wording differs, and a
    // response with tracks but no album is malformed either way.
    if (!data.success || !data.tracks || data.tracks.length === 0 || !data.album) {
      throw new Error(data.error || 'No tracks found in this release');
    }

    const formattedTracks = normaliseReleaseTrackArtists(data.tracks);
    const virtualPlaylistId = beatportReleasePlaylistId(env.now, env.random);
    const playlistName = data.album.name;

    // Six arguments, so contextType takes its 'artist_album' default — unlike
    // the chart flow, which passes 'playlist' explicitly.
    await env.openDownloadModal(
      virtualPlaylistId,
      playlistName,
      formattedTracks,
      data.album,
      data.artist,
      false,
      beatportDownloadContext('release'),
    );

    env.registerDownload(
      playlistName,
      releaseBubbleImage(data.album, release.image_url),
      virtualPlaylistId,
    );

    env.hideLoadingOverlay();
    modalOpening = false;
  } catch (error) {
    env.hideLoadingOverlay();
    modalOpening = false;
    env.showToast(
      `Error loading ${release.title}: ${error instanceof Error ? error.message : String(error)}`,
      'error',
    );
  }
}

/* ── Charts (1999-2064) ───────────────────────────────────────────────────── */

/**
 * openBeatportChartAsDownloadModal. Every chart, Top 100 and genre chart ends
 * up here.
 *
 * Note the order: the modal opens FIRST and the bubble is registered after, and
 * the latch is cleared before either.
 */
export function openBeatportChartAsDownloadModal(
  tracks: readonly BeatportScrapedTrack[],
  chartName: string,
  chartImage: string | null | undefined,
  env: BeatportDownloadEnv,
): void {
  // 2002: cleared whether or not this flow set it.
  modalOpening = false;

  const chartAlbum = buildChartAlbum(
    beatportChartAlbumId(env.now),
    chartName,
    chartImage,
    tracks.length,
  );
  const virtualPlaylistId = beatportChartPlaylistId(env.now, env.random);

  void env.openDownloadModal(
    virtualPlaylistId,
    chartName,
    buildChartTracks(tracks, chartAlbum),
    chartAlbum,
    BEATPORT_COMPILATION_ARTIST,
    false,
    beatportDownloadContext('chart'),
  );

  // 2063 passes chartImage RAW, so a chart with no image hands it null. The
  // `|| ''` here is not a change: registerBeatportDownload stores
  // `chartImage || ''` itself (shared-helpers.js 3398), so both forms produce
  // the same stored bubble — this one just keeps the type honest.
  env.registerDownload(chartName, chartImage || '', virtualPlaylistId);
}

/**
 * The enrichment step, with its progress written into the loading overlay.
 * Never throws — a failed enrichment still downloads what was scraped.
 */
async function enrichWithOverlayProgress(
  tracks: readonly BeatportScrapedTrack[],
  env: BeatportDownloadEnv,
): Promise<BeatportScrapedTrack[]> {
  return enrichBeatportTracks(tracks, beatportEnrichmentId(env.now, env.random), env.now, {
    ...(env.sleep ? { sleep: env.sleep } : {}),
    onProgress: (progress) => {
      env.setOverlayMessage(
        `Fetching track metadata... (${progress.completed}/${progress.total}) ${progress.current_track || ''}`,
      );
    },
  });
}

/**
 * The featured-chart (2069) and DJ-chart (2115) card clicks. Two functions in
 * the vanilla, identical but for these four strings — and neither touches the
 * double-click latch, so both are genuinely unguarded.
 */
const CHART_CLICK_COPY = {
  chart: {
    noUrl: 'No chart URL available',
    namePrefix: 'Featured Chart: ',
    noTracks: 'No tracks found in this chart',
  },
  dj: {
    noUrl: 'No DJ chart URL available',
    namePrefix: 'DJ Chart: ',
    noTracks: 'No tracks found in this DJ chart',
  },
} as const;

export async function openBeatportChartCard(
  chart: BeatportChart,
  variant: 'chart' | 'dj',
  env: BeatportDownloadEnv,
): Promise<void> {
  const copy = CHART_CLICK_COPY[variant];

  // 2072 tests `!chart.url || chart.url === ''`; the second clause cannot add
  // anything to the first. Unlike the release flow, '#' is NOT rejected here.
  if (!chart.url) {
    env.showToast(copy.noUrl, 'error');
    return;
  }

  // The chart's DISPLAY name — creator included — which is also the download's
  // playlist name and its bubble key. The name sent to the scraper is the
  // prefixed one below, and they are deliberately different.
  const chartName = `${chart.name} - ${chart.creator}`;

  try {
    env.showToast(`Loading ${chart.name}...`, 'info');
    env.showLoadingOverlay(`Scraping ${chart.name}...`);

    const data = await extractBeatportChart(chart.url, `${copy.namePrefix}${chart.name}`);
    if (!data.success || !data.tracks || data.tracks.length === 0) {
      throw new Error(copy.noTracks);
    }

    const enriched = await enrichWithOverlayProgress(data.tracks, env);

    env.hideLoadingOverlay();
    openBeatportChartAsDownloadModal(enriched, chartName, chart.image, env);
  } catch (error) {
    env.hideLoadingOverlay();
    env.showToast(
      `Error loading ${chart.name}: ${error instanceof Error ? error.message : String(error)}`,
      'error',
    );
  }
}

/* ── The two top-10 track lists — sync-services.js 4893-4936 ──────────────── */

/**
 * handleRebuildChartClick, the SIXTH download flow and the only one that does
 * not live in beatport-ui.js.
 *
 * The two top-10 track lists have no per-card handler at all. Instead
 * sync-services.js 3948-3963 binds a click to the whole CONTAINER, so clicking
 * anywhere in the list — including its header — queues all ten tracks. That is
 * easy to miss when reading beatport-ui.js alone, where those cards look inert.
 *
 * The vanilla then SCRAPES the rendered cards for the track data
 * (getRebuildPageTrackData, 4937-4992): title, artist, label, url and rank, out
 * of the DOM it just wrote. The port passes the loaded objects instead.
 *
 * That substitution is safe, and it was checked rather than assumed: the scrape
 * reads text that cleanTrackText has ALREADY been applied to at render time
 * (1669-1671), and buildChartTracks applies cleanTrackText again downstream —
 * which is idempotent for these inputs. The scrape's per-field
 * 'Unknown Title' / 'Unknown Artist' / 'Unknown Label' defaults are the same
 * ones the renderer used, and buildChartTrackName defaults identically. The
 * fields the scrape drops (artwork_url) are ones buildChartTracks never reads.
 *
 * Latch: set, then released by the same blind 2s timer as the Top 100 buttons.
 */
export async function openBeatportTop10List(
  tracks: readonly BeatportScrapedTrack[],
  chartName: string,
  env: BeatportDownloadEnv,
): Promise<void> {
  if (modalOpening) return;
  modalOpening = true;
  env.schedule(() => {
    modalOpening = false;
  }, 2000);

  try {
    if (tracks.length === 0) {
      // 4917. The vanilla can also fail one step earlier, inside the scrape,
      // with 'No track cards found in #beatport-top10-list' — a selector string
      // shown to the user. There is no container to name here, so the port uses
      // the other message the same flow already produces.
      throw new Error(`No track data found for ${chartName}`);
    }

    // 4925: the overlay opens with the count already in it, unlike every other
    // flow, whose first overlay message is a 'Scraping …' line.
    env.showLoadingOverlay(`Fetching track metadata... (0/${tracks.length})`);

    const enriched = await enrichWithOverlayProgress(tracks, env);

    env.hideLoadingOverlay();
    openBeatportChartAsDownloadModal(enriched, chartName, null, env);
  } catch (error) {
    env.hideLoadingOverlay();
    env.showToast(
      `Error loading ${chartName}: ${error instanceof Error ? error.message : String(error)}`,
      'error',
    );
  }
}

/* ── The genre Top 100 button (3406-3439) ─────────────────────────────────── */

/**
 * The same shape as the two homepage Top 100 buttons — latch, blind 2s release,
 * scrape unenriched, enrich with progress, open as a compilation with no image
 * — over the genre tracks endpoint, and filed as '<Genre> Top 100'.
 *
 * Like them, and unlike every other flow, it shows no 'Loading …' toast.
 */
export async function openBeatportGenreTop100(
  genreSlug: string,
  genreId: string | number,
  genreName: string,
  env: BeatportDownloadEnv,
): Promise<void> {
  if (modalOpening) return;
  modalOpening = true;
  env.schedule(() => {
    modalOpening = false;
  }, 2000);

  const chartName = `${genreName} Top 100`;

  try {
    env.showLoadingOverlay(`Scraping ${chartName}...`);

    const data = await fetchBeatportGenreTracks(genreSlug, genreId);
    if (!data.success || !data.tracks || data.tracks.length === 0) {
      throw new Error(`No tracks found in ${chartName}`);
    }

    const enriched = await enrichWithOverlayProgress(data.tracks, env);

    env.hideLoadingOverlay();
    openBeatportChartAsDownloadModal(enriched, chartName, null, env);
  } catch (error) {
    env.hideLoadingOverlay();
    env.showToast(
      `Error loading ${chartName}: ${error instanceof Error ? error.message : String(error)}`,
      'error',
    );
  }
}

/* ── The two Top 100 buttons (2161-2228) ──────────────────────────────────── */

const TOP_100_COPY = {
  beatport: {
    chartName: 'Beatport Top 100',
    overlay: 'Scraping Beatport Top 100...',
    noTracks: 'No tracks found in Beatport Top 100',
  },
  hype: {
    chartName: 'Hype Top 100',
    overlay: 'Scraping Hype Top 100...',
    noTracks: 'No tracks found in Hype Top 100',
  },
} as const;

/**
 * Both Top 100 buttons. Unlike the release flow, the latch is released by a
 * blind 2s timer rather than when the work finishes — so a scrape slower than
 * 2s can be started twice. Transcribed, not fixed: the fix would be a
 * behaviour change on the path that queues 100 downloads.
 *
 * Note also that neither shows a "Loading…" toast, where every other flow does.
 */
export async function openBeatportTop100(
  variant: 'beatport' | 'hype',
  env: BeatportDownloadEnv,
): Promise<void> {
  if (modalOpening) return;
  modalOpening = true;
  env.schedule(() => {
    modalOpening = false;
  }, 2000);

  const copy = TOP_100_COPY[variant];

  try {
    env.showLoadingOverlay(copy.overlay);

    const data = await fetchBeatportTop100(variant);
    if (!data.success || !data.tracks || data.tracks.length === 0) {
      throw new Error(copy.noTracks);
    }

    const enriched = await enrichWithOverlayProgress(data.tracks, env);

    env.hideLoadingOverlay();
    // The bubble gets NO image for either Top 100 (2185, 2221).
    openBeatportChartAsDownloadModal(enriched, copy.chartName, null, env);
  } catch (error) {
    env.hideLoadingOverlay();
    env.showToast(
      `Error loading ${copy.chartName}: ${error instanceof Error ? error.message : String(error)}`,
      'error',
    );
  }
}
