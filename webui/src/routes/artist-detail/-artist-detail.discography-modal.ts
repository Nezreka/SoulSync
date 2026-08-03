import { classifyReleaseContent } from './-artist-detail.filters';
import { gapFillEnabled, gapSameRelease } from './-artist-detail.gap-fill';

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
  /** Gap-fill releases resolve from THEIR source (#1067). */
  _gap_source?: string | null;
}

export interface DiscogModalData {
  artist: { id: unknown; name: string; source: string | null };
  releases: DiscogRelease[];
}

/**
 * The library-path load (592-677): resolve the artist's metadata id from the
 * enhanced record (the modal's download API needs it), fetch the discography,
 * then merge gap-fill releases when '+ Other sources' is on — deduped against
 * the base list by title + year.
 */
export async function loadDiscographyForModal(
  libraryArtistId: unknown,
  artistName: string,
): Promise<DiscogModalData | null> {
  let metadataArtistId: string | null = null;
  let lookupId = libraryArtistId;
  try {
    const idResponse = await fetch(`/api/library/artist/${libraryArtistId}/enhanced`);
    const idData = await idResponse.json();
    if (idData.success && idData.artist) {
      const a = idData.artist;
      metadataArtistId = a.spotify_artist_id || a.itunes_artist_id || a.deezer_id || null;
      lookupId = metadataArtistId || libraryArtistId;
    }
  } catch {
    console.debug('[Discography] Could not fetch artist IDs, using DB id');
  }

  let releases: DiscogRelease[] = [];
  let source: string | null = null;
  try {
    const response = await fetch(
      `/api/artist/${encodeURIComponent(String(lookupId))}/discography?artist_name=${encodeURIComponent(artistName)}`,
    );
    const data = await response.json();
    if (!data.error) {
      releases = [
        ...(data.albums || []).map((a: object) => ({ ...a, _type: 'album' })),
        ...(data.eps || []).map((a: object) => ({ ...a, _type: 'ep' })),
        ...(data.singles || []).map((a: object) => ({ ...a, _type: 'single' })),
      ];
      source = data.source || null;
    }
  } catch (error) {
    console.error('Failed to load discography:', error);
  }
  if (releases.length === 0) return null;

  const artist = { id: metadataArtistId || libraryArtistId, name: artistName, source };

  if (gapFillEnabled()) {
    try {
      const params = new URLSearchParams();
      if (artistName) params.set('artist_name', artistName);
      if (source) params.set('base_source', source);
      const response = await fetch(
        `/api/artist/${encodeURIComponent(String(artist.id))}/discography/gap-fill?${params}`,
      );
      const data = await response.json().catch(() => ({}));
      if (response.ok && data.success) {
        const gaps = data.gaps || {};
        const gapReleases: DiscogRelease[] = [
          ...(gaps.albums || []).map((g: object) => ({ ...g, _type: 'album' })),
          ...(gaps.eps || []).map((g: object) => ({ ...g, _type: 'ep' })),
          ...(gaps.singles || []).map((g: object) => ({ ...g, _type: 'single' })),
        ];
        for (const g of gapReleases) {
          if (releases.some((r) => gapSameRelease(g, r))) continue;
          releases.push({
            ...g,
            name: g.title || g.name || 'Unknown Release',
            _gap_source: (g as { gap_source?: string }).gap_source,
          });
        }
      }
    } catch (error) {
      console.debug('discog modal gap-fill skipped:', error);
    }
  }

  return { artist, releases };
}

export interface DiscogCardView {
  albumName: string;
  year: string;
  tracks: number;
  statusClass: '' | 'owned' | 'partial';
  statusIcon: '' | '✓' | '◐';
  /** Unowned releases come pre-checked (767). */
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
  return {
    albumName: release.name || release.title || '',
    year: release.release_date ? release.release_date.substring(0, 4) : '',
    tracks: release.total_tracks || release.track_count || 0,
    statusClass: isOwned ? 'owned' : isPartial ? 'partial' : '',
    statusIcon: isOwned ? '✓' : isPartial ? '◐' : '',
    checkedByDefault: !isOwned,
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

/** The footer line + submit label (826-840). */
export function discogFooter(selection: { tracks: number }[]): {
  info: string;
  submitText: string;
  disabled: boolean;
} {
  const releases = selection.length;
  const tracks = selection.reduce((sum, s) => sum + (s.tracks || 0), 0);
  return {
    info: `${releases} release${releases !== 1 ? 's' : ''} · ${tracks} tracks`,
    submitText: releases > 0 ? `Add ${releases} to Wishlist` : 'Select releases',
    disabled: releases === 0,
  };
}

export interface DiscogEntry {
  id: unknown;
  name: string;
  tracks: number;
  gapSource: string | null;
}

/**
 * The body POST /api/artist/<id>/download-discography accepts.
 *
 * `source` is optional because the playlist explorer, which streams the SAME
 * endpoint, sends per-album sources only — it has no batch-level source to
 * name (pages-extra.js:820-828).
 */
export interface DiscographyDownloadPayload {
  albums: { id: unknown; name: string; artist_name: string; source: string | null }[];
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

/** POST + NDJSON stream (936-986): per-album updates, then the completion line. */
export async function streamDiscographyDownload(
  artistId: unknown,
  payload: DiscographyDownloadPayload,
  onAlbum: (update: DiscogAlbumUpdate) => void,
  onComplete: (totals: { total_added: number; total_skipped: number }) => void,
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
