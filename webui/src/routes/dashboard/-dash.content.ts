/**
 * The dashboard content band — data layer.
 *
 * Two rails, both video-dashboard parity (Recently Added / Upcoming over
 * there):
 *
 * - Recently Added: /api/library/recently-added — the backend folds the
 *   per-track library_history rows into one card per album and backfills
 *   missing covers from the library's own art (most history rows carry none).
 *   Each card also carries the newest landed track's title + file_path so a
 *   click can hand it straight to the media player.
 *
 * - Fresh from your artists: /api/watchlist/recent-releases (flat rows off
 *   the `recent_releases` table the watchlist scan fills). A user with no
 *   watchlist falls back to the discover page's cached recent-releases feed —
 *   same card shape, broader source. A click checks ownership and either
 *   plays (owned) or opens the standard download-missing modal (not owned),
 *   the same sequence the discover page's release cards run.
 */

import { getShellBridge } from '@/platform/shell/bridge';

export interface RecentlyAddedAlbum {
  artistName: string;
  albumName: string;
  cover: string;
  /** The artist's own art — the card's fallback when `cover` 404s. */
  artistCover: string;
  /** SQLite CURRENT_TIMESTAMP of the newest track that landed. */
  addedAt: string;
  trackCount: number;
  /** "FLAC" / "MP3 320" — the newest row's quality, already uppercased. */
  quality: string;
  /** soulseek / tidal / qobuz / youtube — where the newest track came from. */
  source: string;
  /** The newest landed track, ready for playLibraryTrack. */
  playTitle: string;
  playFilePath: string;
}

interface RecentlyAddedRow {
  artist_name?: string;
  album_name?: string;
  thumb_url?: string;
  artist_thumb_url?: string;
  added_at?: string;
  track_count?: number;
  quality?: string;
  download_source?: string;
  play_title?: string;
  play_file_path?: string;
}

export async function fetchRecentlyAdded(limit = 20): Promise<RecentlyAddedAlbum[]> {
  try {
    const response = await fetch(`/api/library/recently-added?limit=${limit}`);
    if (!response.ok) return [];
    const payload = (await response.json()) as { albums?: RecentlyAddedRow[] };
    return (payload.albums ?? []).map((row) => ({
      artistName: row.artist_name ?? '',
      albumName: row.album_name ?? '',
      cover: row.thumb_url ?? '',
      artistCover: row.artist_thumb_url ?? '',
      addedAt: row.added_at ?? '',
      trackCount: row.track_count ?? 1,
      quality: row.quality ?? '',
      source: row.download_source ?? '',
      playTitle: row.play_title ?? '',
      playFilePath: row.play_file_path ?? '',
    }));
  } catch {
    return [];
  }
}

/** "2m ago" / "3h ago" / "5d ago" — the video dashboard's tile caption style. */
export function relativeAge(iso: string, now: number): string {
  const t = Date.parse(iso.includes('T') || iso.includes('Z') ? iso : `${iso.replace(' ', 'T')}Z`);
  if (Number.isNaN(t)) return '';
  const seconds = Math.max(0, Math.floor((now - t) / 1000));
  if (seconds < 60) return 'just now';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 31) return `${days}d ago`;
  const months = Math.floor(days / 30);
  return `${months}mo ago`;
}

/** The card's file line: "FLAC · soulseek", degrading to whichever half exists. */
export function fileBadge(quality: string, source: string): string {
  return [quality, source].filter(Boolean).join(' · ');
}

// ── Fresh from your artists ──────────────────────────────────────────────────

export interface FreshRelease {
  albumName: string;
  artistName: string;
  cover: string;
  /** YYYY-MM-DD from the provider; may be partial ("2026"). */
  releaseDate: string;
  trackCount: number;
  spotifyArtistId: string;
  /** Album ids + provider, for the album fetch behind the click. */
  albumSpotifyId: string;
  albumItunesId: string;
  albumDeezerId: string;
  sourceProvider: string;
  /** True when this card came from the discover fallback, not the watchlist. */
  fromDiscover: boolean;
}

interface WatchlistReleaseRow {
  album_name?: string;
  release_date?: string;
  album_cover_url?: string;
  track_count?: number;
  source?: string;
  album_spotify_id?: string;
  album_itunes_id?: string;
  album_deezer_id?: string;
  artist_name?: string;
  spotify_artist_id?: string;
}

interface DiscoverAlbumRow {
  album_name?: string;
  artist_name?: string;
  album_cover_url?: string;
  release_date?: string;
  source?: string;
  album_spotify_id?: string;
  album_itunes_id?: string;
  album_deezer_id?: string;
  artist_spotify_id?: string;
}

function fromRow(row: WatchlistReleaseRow & DiscoverAlbumRow, fromDiscover: boolean): FreshRelease {
  return {
    albumName: row.album_name ?? '',
    artistName: row.artist_name ?? '',
    cover: row.album_cover_url ?? '',
    releaseDate: row.release_date ?? '',
    trackCount: row.track_count ?? 0,
    spotifyArtistId: row.spotify_artist_id ?? row.artist_spotify_id ?? '',
    albumSpotifyId: row.album_spotify_id ?? '',
    albumItunesId: row.album_itunes_id ?? '',
    albumDeezerId: row.album_deezer_id ?? '',
    sourceProvider:
      row.source ??
      (row.album_spotify_id ? 'spotify' : row.album_deezer_id ? 'deezer' : 'itunes'),
    fromDiscover,
  };
}

/**
 * Watchlist first — those are releases from artists the user explicitly
 * follows, which is the whole point of the rail. Only a completely empty
 * watchlist result falls back to the discover feed; a short watchlist list is
 * NOT topped up from discover, because mixing "your artists" with "artists
 * like yours" under one heading would make the heading a lie.
 */
export async function fetchFreshReleases(limit = 20): Promise<FreshRelease[]> {
  try {
    const response = await fetch(`/api/watchlist/recent-releases?limit=${limit}`);
    if (response.ok) {
      const payload = (await response.json()) as { releases?: WatchlistReleaseRow[] };
      const rows = payload.releases ?? [];
      if (rows.length > 0) return rows.map((r) => fromRow(r, false));
    }
  } catch {
    // fall through to discover
  }
  try {
    const response = await fetch('/api/discover/recent-releases');
    if (!response.ok) return [];
    const payload = (await response.json()) as { albums?: DiscoverAlbumRow[] };
    return (payload.albums ?? []).slice(0, limit).map((r) => fromRow(r, true));
  } catch {
    return [];
  }
}

// ── Fresh-release click: play when owned, download modal when not ────────────

interface AlbumDetailTrack {
  id?: string;
  name?: string;
  duration_ms?: number;
  track_number?: number;
}

interface AlbumDetail {
  id?: string;
  name?: string;
  image_url?: string;
  tracks?: AlbumDetailTrack[];
}

interface OwnedEntry {
  owned?: boolean;
  track_id?: number | string;
  title?: string;
  file_path?: string;
  bitrate?: number | string;
}

/**
 * Every track owned → owned. "Most" is not enough: playing an album the user
 * is missing half of, instead of offering to complete it, buries the gap.
 */
export function albumIsOwned(ownedTracks: Record<string, OwnedEntry>, trackNames: string[]): OwnedEntry | null {
  if (trackNames.length === 0) return null;
  let first: OwnedEntry | null = null;
  for (const name of trackNames) {
    const entry = ownedTracks[name];
    if (!entry?.owned || !entry.file_path) return null;
    first ??= entry;
  }
  return first;
}

/**
 * The standard release-card click, same sequence as the discover page's
 * recent-releases cards (-discover.use-album-open.ts openRecentAlbum — kept as
 * a local twin rather than a cross-route import; the dashboard deliberately
 * imports nothing from page modules): fetch the album's tracks, then either
 * hand the first track to the media player (fully owned) or open the shared
 * download-missing modal with the full track list (anything missing).
 */
export async function openFreshRelease(release: FreshRelease): Promise<void> {
  window.showLoadingOverlay?.(`Loading tracks for ${release.albumName}...`);
  try {
    const source = release.sourceProvider || (release.albumSpotifyId ? 'spotify' : 'itunes');
    const albumId =
      source === 'spotify'
        ? release.albumSpotifyId
        : source === 'deezer'
          ? release.albumDeezerId
          : release.albumItunesId;
    if (!albumId) throw new Error(`No ${source} album ID available`);
    const params = new URLSearchParams({ name: release.albumName, artist: release.artistName });
    const response = await fetch(`/api/discover/album/${source}/${albumId}?${params}`);
    if (!response.ok) throw new Error('Failed to fetch album tracks');
    const albumData = (await response.json()) as AlbumDetail;
    if (!albumData.tracks?.length) throw new Error('No tracks found in album');

    const trackNames = albumData.tracks.map((t) => t.name ?? '').filter(Boolean);
    let ownedFirst: OwnedEntry | null = null;
    try {
      const check = await fetch('/api/library/check-tracks', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          artist_name: release.artistName,
          album_name: release.albumName,
          tracks: trackNames.map((name) => ({ name })),
        }),
      });
      if (check.ok) {
        const payload = (await check.json()) as { owned_tracks?: Record<string, OwnedEntry> };
        ownedFirst = albumIsOwned(payload.owned_tracks ?? {}, trackNames);
      }
    } catch {
      ownedFirst = null; // an unreachable check falls through to the modal
    }

    if (ownedFirst) {
      window.hideLoadingOverlay?.();
      getShellBridge()?.playLibraryTrack(
        {
          id: ownedFirst.track_id ?? -1,
          title: ownedFirst.title ?? trackNames[0],
          file_path: ownedFirst.file_path ?? '',
          bitrate: ownedFirst.bitrate ?? null,
        },
        release.albumName,
        release.artistName,
      );
      return;
    }

    const spotifyTracks = albumData.tracks.map((track) => ({
      id: track.id,
      name: track.name,
      artists: [{ name: release.artistName }],
      album: { id: albumData.id, name: albumData.name, image_url: albumData.image_url },
      duration_ms: track.duration_ms || 0,
      track_number: track.track_number || 0,
    }));
    await window.openDownloadMissingModalForYouTube?.(
      `recent_album_${albumId}`,
      albumData.name ?? release.albumName,
      spotifyTracks,
      { id: release.spotifyArtistId || null, name: release.artistName },
      { id: albumData.id, name: albumData.name, image_url: albumData.image_url },
    );
    window.hideLoadingOverlay?.();
  } catch (error) {
    window.hideLoadingOverlay?.();
    const message = error instanceof Error ? error.message : String(error);
    window.showToast?.(`Failed to load album: ${message}`, 'error');
  }
}
