import type { Discography } from './-artist-detail.types';

import { classifyReleaseContent } from './-artist-detail.filters';
import { type ReleaseSectionType } from './-artist-detail.open-release';

/**
 * Download Discography (library.js: openDiscographyModal 580, filters 798,
 * startDiscographyDownload 843, _discogItemStatus 997). The data loading,
 * pure classification and the NDJSON download stream; the modal UI lives in
 * -ui/discography-modal.tsx.
 *
 * The vanilla preferred artistsPageState (the OLD vanilla search page's
 * globals) and fell back to the library path; the search page is React now
 * and never populates those globals, so the library path is the only one the
 * port keeps.
 */

export interface DiscogRelease {
  id?: unknown;
  name?: string;
  title?: string;
  release_date?: string;
  total_tracks?: number;
  track_count?: number;
  image_url?: string;
  explicit?: boolean;
  album_type?: string;
  _type: string;
  /** #1450: stamped by the backend (GET /api/artist-detail). The modal
   *  pre-checks only preferred editions; a missing flag reads as preferred
   *  (gap-fill cards and stale responses predate it). */
  edition_preferred?: boolean;
  /** Gap-fill releases resolve from THEIR source (#1067). */
  _gap_source?: string | null;
}

export interface DiscogModalData {
  artist: { id: unknown; name: string; source: string | null };
  releases: DiscogRelease[];
}

/**
 * The releases the artist page is showing, flattened for the modal.
 *
 * the modal used to refetch the discography on its own, with no source and its
 * own gap-fill call, so it could list a different source's releases than the
 * page (discord, SeadogsBooty: deezer page showed 2 EPs, the download pulled
 * musicbrainz's Underground fan club EPs). now it lists exactly what the page
 * rendered, gap cards included with their own source.
 *
 * #1450: `editionSuperseded` holds base-release ids whose edition_preferred
 * stamp the combined base+gap edition group overturned. Those cards get
 * edition_preferred=false here, so discogCardView's
 * `edition_preferred !== false && !isOwned` pre-check flips them off while
 * genuinely flag-less cards (stale responses) keep reading as preferred.
 */
export function releasesFromPageDiscography(
  discography: Discography,
  editionSuperseded?: Set<unknown>,
): DiscogRelease[] {
  const releases: DiscogRelease[] = [];
  for (const [bucket, type] of [
    ['albums', 'album'],
    ['eps', 'ep'],
    ['singles', 'single'],
  ] as const) {
    for (const release of discography[bucket] ?? []) {
      const entry: DiscogRelease = {
        ...release,
        name: release.name || release.title || 'Unknown Release',
        image_url: release.image_url || undefined,
        total_tracks: Number(release.track_count) || Number(release._gap_track_count) || undefined,
        _type: type,
        _gap_source: (release._gap_source as string | undefined) || undefined,
      };
      if (editionSuperseded?.has(release.id)) entry.edition_preferred = false;
      releases.push(entry);
    }
  }
  return releases;
}

/**
 * Resolve the artist's metadata id from the enhanced record (the download
 * URL carries it), then take the releases straight from the page.
 */
export async function loadDiscographyForModal(
  libraryArtistId: unknown,
  artistName: string,
  pageDiscography: Discography,
  editionSuperseded?: Set<unknown>,
): Promise<DiscogModalData | null> {
  const releases = releasesFromPageDiscography(pageDiscography, editionSuperseded);
  if (releases.length === 0) return null;

  let metadataArtistId: string | null = null;
  try {
    const idResponse = await fetch(`/api/library/artist/${libraryArtistId}/enhanced`);
    const idData = await idResponse.json();
    if (idData.success && idData.artist) {
      const a = idData.artist;
      metadataArtistId = a.spotify_artist_id || a.itunes_artist_id || a.deezer_id || null;
    }
  } catch {
    console.debug('[Discography] Could not fetch artist IDs, using DB id');
  }

  const source = pageDiscography.source || null;
  const artist = { id: metadataArtistId || libraryArtistId, name: artistName, source };
  return { artist, releases };
}

export interface DiscogCardView {
  albumName: string;
  year: string;
  tracks: number;
  statusClass: '' | 'owned' | 'partial';
  statusIcon: '' | '✓' | '◐';
  /** #1450: unowned releases come pre-checked (767) ONLY when the backend
   *  marks them the preferred edition (edition_preferred !== false);
   *  the other editions of the same album stay visible and hand-checkable. */
  checkedByDefault: boolean;
  isLive: boolean;
  isCompilation: boolean;
  isFeatured: boolean;
}

/** Per-card derivation (758-785): completion status + #877 content flags. */
export function discogCardView(
  release: DiscogRelease,
  completionData: {
    albums?: { id?: unknown; status?: string }[];
    singles?: { id?: unknown; status?: string }[];
  },
): DiscogCardView {
  const comp =
    completionData?.albums?.find((c) => c.id === release.id) ||
    completionData?.singles?.find((c) => c.id === release.id);
  const status = comp?.status || 'unknown';
  const isOwned = status === 'completed';
  const isPartial = status === 'partial' || status === 'nearly_complete';
  const flags = classifyReleaseContent(release as never);
  // #1450: the backend stamps edition_preferred on every release it serves
  // ("all" stamps true everywhere, preserving today's behaviour). A missing
  // flag (gap-fill cards, stale responses) reads as preferred so those
  // releases keep today's pre-check.
  const editionPreferred = release.edition_preferred !== false;
  return {
    albumName: release.name || release.title || '',
    year: release.release_date ? release.release_date.substring(0, 4) : '',
    tracks: release.total_tracks || release.track_count || 0,
    statusClass: isOwned ? 'owned' : isPartial ? 'partial' : '',
    statusIcon: isOwned ? '✓' : isPartial ? '◐' : '',
    checkedByDefault: editionPreferred && !isOwned,
    isLive: flags.isLive,
    isCompilation: flags.isCompilation,
    isFeatured: flags.isFeatured,
  };
}

export interface DiscogFilters {
  album: boolean;
  ep: boolean;
  single: boolean;
  live: boolean;
  compilations: boolean;
  featured: boolean;
}

export const DISCOG_DEFAULT_FILTERS: DiscogFilters = {
  album: true,
  ep: true,
  single: true,
  live: true,
  compilations: true,
  featured: true,
};

/**
 * #877: hidden if the category is off OR any active content exclusion applies.
 * The download payload is built from VISIBLE checked cards, so every toggle
 * changes what gets downloaded.
 */
export function discogCardVisible(
  view: DiscogCardView,
  type: string,
  filters: DiscogFilters,
): boolean {
  if ((filters as unknown as Record<string, boolean>)[type] === false) return false;
  if (!filters.live && view.isLive) return false;
  if (!filters.compilations && view.isCompilation) return false;
  if (!filters.featured && view.isFeatured) return false;
  return true;
}

/** The footer line + submit labels (826-840). asksFirst: a profile without
 * download rights, whose wishlist adds are requests. */
export function discogFooter(
  selection: { tracks: number }[],
  asksFirst = false,
): {
  info: string;
  submitText: string;
  bothText: string;
  disabled: boolean;
} {
  const releases = selection.length;
  const tracks = selection.reduce((sum, s) => sum + (s.tracks || 0), 0);
  const submitText =
    releases === 0
      ? 'Select releases'
      : asksFirst
        ? `Request ${releases}`
        : `Add ${releases} to Wishlist`;
  return {
    info: `${releases} release${releases !== 1 ? 's' : ''} · ${tracks} tracks`,
    submitText,
    bothText:
      releases === 0
        ? 'Select releases'
        : asksFirst
          ? `Request ${releases} + Watch`
          : `Wishlist + Watchlist`,
    disabled: releases === 0,
  };
}

export interface DiscogEntry {
  id: unknown;
  name: string;
  tracks: number;
  gapSource: string | null;
  /** the section the release sat in on the artist page */
  albumType?: ReleaseSectionType;
}

/**
 * The body POST /api/artist/<id>/download-discography accepts.
 *
 * `source` is optional because the playlist explorer, which streams the SAME
 * endpoint, sends per-album sources only — it has no batch-level source to
 * name (pages-extra.js:820-828).
 */
export interface DiscographyDownloadPayload {
  albums: {
    id: unknown;
    name: string;
    artist_name: string;
    source: string | null;
    /** the artist page section: the server files the release under it */
    album_type?: ReleaseSectionType;
  }[];
  artist_name: string;
  source?: string | null;
}

/**
 * The batch payload (855-933): entries sorted by track count DESC so Deluxe /
 * expanded editions process first and standard editions dedupe against them;
 * each gap-fill entry carries ITS source (#1067).
 */
export function buildDiscographyPayload(
  entries: DiscogEntry[],
  artist: { id: unknown; name: string; source: string | null },
): DiscographyDownloadPayload & { source: string | null } {
  const sourceForBatch = (artist.source || '').toString().toLowerCase() || null;
  const sorted = [...entries].sort((a, b) => b.tracks - a.tracks);
  return {
    albums: sorted.map((e) => ({
      id: e.id,
      name: e.name,
      artist_name: artist.name,
      source: e.gapSource || sourceForBatch,
      ...(e.albumType ? { album_type: e.albumType } : {}),
    })),
    artist_name: artist.name,
    source: sourceForBatch,
  };
}

/**
 * #830: surface WHY tracks weren't added — other-artist credit, already
 * owned/queued, or content-filtered — instead of a misleading "No new tracks".
 */
export function discogItemStatus(data: Record<string, unknown>): string {
  const parts: string[] = [];
  const n = (key: string) => (data[key] as number) || 0;
  if (n('tracks_added') > 0) parts.push(`${data.tracks_added} added`);
  if (n('tracks_skipped_owned') > 0) parts.push(`${data.tracks_skipped_owned} already owned`);
  if (n('tracks_skipped') > 0) parts.push(`${data.tracks_skipped} already queued`);
  if (n('tracks_skipped_artist') > 0) parts.push(`${data.tracks_skipped_artist} by other artists`);
  if (n('tracks_skipped_filter') > 0) parts.push(`${data.tracks_skipped_filter} filtered out`);
  return parts.join(', ') || 'No tracks';
}

export interface DiscogAlbumUpdate {
  album_id?: unknown;
  status?: string;
  message?: string;
  tracks_added?: number;
  tracks_total?: number;
  [key: string]: unknown;
}

export interface FailedRelease {
  album_id: unknown;
  name: string;
  source: string | null;
  album_type?: string;
  error: string;
}

export interface DiscogTotals {
  total_added: number;
  total_skipped: number;
  failed_releases: FailedRelease[];
}

/**
 * The "Future releases" section of the Download Discography modal: the
 * per-artist watchlist settings the combined "Wishlist + Watchlist" button
 * applies after the download stream finishes. Plain booleans, exactly like
 * the watchlist page's config modal — the include_* columns have no
 * "follow global" state (that tri-state exists only on auto_download_pref).
 */
export interface FutureReleases {
  include_albums: boolean;
  include_eps: boolean;
  include_singles: boolean;
  include_live: boolean;
  include_remixes: boolean;
  include_acoustic: boolean;
  include_compilations: boolean;
  include_instrumentals: boolean;
  /** null = follow the global auto-download setting */
  auto_download_pref: 'on' | 'off' | null;
}

/**
 * One-time defaults for the Future releases section, taken from the
 * download filter state the modal opened with: "I'm excluding live
 * versions now" becomes "keep excluding them later". Independent after
 * that — changing a download filter does not rewrite these.
 */
export function defaultFutureReleases(filters: DiscogFilters): FutureReleases {
  return {
    include_albums: filters.album,
    include_eps: filters.ep,
    include_singles: filters.single,
    include_live: filters.live,
    include_remixes: false,
    include_acoustic: false,
    include_compilations: filters.compilations,
    include_instrumentals: false,
    auto_download_pref: null,
  };
}

/**
 * Watch-then-configure, for the combined "Wishlist + Watchlist" button.
 * Runs AFTER the download stream completes so a watchlist failure can never
 * roll back queued downloads; each half reports its own outcome.
 */
export async function watchArtistWithSettings(
  artistId: unknown,
  artistName: string,
  settings: FutureReleases,
): Promise<{ watching: boolean; message: string }> {
  const checkResponse = await fetch('/api/watchlist/check', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ artist_id: artistId }),
  });
  const checkData = await checkResponse.json();
  if (!checkData.success) {
    throw new Error(checkData.error || 'Failed to check watchlist status');
  }
  let watching = Boolean(checkData.is_watching);
  let message = '';

  if (!watching) {
    const addResponse = await fetch('/api/watchlist/add', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ artist_id: artistId, artist_name: artistName }),
    });
    const addData = await addResponse.json();
    if (!addData.success) throw new Error(addData.error || 'Failed to add to watchlist');
    watching = true;
    message = String(addData.message ?? '');
    if (typeof window.updateWatchlistCount === 'function') window.updateWatchlistCount();
  }

  const configResponse = await fetch(
    `/api/watchlist/artist/${encodeURIComponent(String(artistId))}/config`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        include_albums: settings.include_albums,
        include_eps: settings.include_eps,
        include_singles: settings.include_singles,
        include_live: settings.include_live,
        include_remixes: settings.include_remixes,
        include_acoustic: settings.include_acoustic,
        include_compilations: settings.include_compilations,
        include_instrumentals: settings.include_instrumentals,
        auto_download_pref: settings.auto_download_pref,
      }),
    },
  );
  const configData = await configResponse.json();
  if (!configData.success) {
    throw new Error(configData.error || 'Failed to save watchlist settings');
  }
  return { watching, message };
}

/** POST + NDJSON stream (936-986): per-album updates, then the completion line. */
export async function streamDiscographyDownload(
  artistId: unknown,
  payload: DiscographyDownloadPayload,
  onAlbum: (update: DiscogAlbumUpdate) => void,
  onComplete: (totals: DiscogTotals) => void,
): Promise<void> {
  const response = await fetch(`/api/artist/${artistId}/download-discography`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!response.body) return;

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() ?? '';
    for (const line of lines) {
      if (!line.trim()) continue;
      try {
        const data = JSON.parse(line);
        if (data.status === 'complete') {
          onComplete({
            total_added: data.total_added || 0,
            total_skipped: data.total_skipped || 0,
            failed_releases: Array.isArray(data.failed_releases) ? data.failed_releases : [],
          });
        } else {
          onAlbum(data);
        }
      } catch {
        /* skip malformed line */
      }
    }
  }
}
