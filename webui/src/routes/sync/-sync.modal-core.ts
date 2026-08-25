/**
 * Shared discovery modal — pure helpers, ported from the modal builder
 * (openYouTubeDiscoveryModal, sync-services.js 9328-9576) and its text
 * generators (getModalDescription/getInitialProgressText, 9930-9959), with the
 * source dispatch driven by the config instead of ten boolean parameters.
 *
 * Knowing fix (live bug #5): the vanilla interpolates currentMusicSourceName,
 * which is declared 'Spotify' and never reassigned — the headers always said
 * Spotify. metadataSourceLabel() asks the live seam instead
 * (window.getMetadataSourceLabel(window.getActiveMetadataSource()), both
 * window-reachable function declarations), falling back to 'Spotify'.
 */

import type { SyncSourceId } from './-sync.sources';
import type { SourcePlaylistState } from './-sync.state';
import type { RawDiscoveryResult } from './-sync.transform';

declare global {
  interface Window {
    getActiveMetadataSource?: () => string;
    getMetadataSourceLabel?: (source: string) => string;
  }
}

/** The live metadata-source display name; 'Spotify' when the seam is absent. */
export function metadataSourceLabel(): string {
  try {
    const source = window.getActiveMetadataSource?.();
    if (source && window.getMetadataSourceLabel) return window.getMetadataSourceLabel(source);
  } catch {
    // fall through to the default
  }
  return 'Spotify';
}

/** The lastfm radio special case is a HASH PREFIX, not a state flag (9356). */
export function isLastfmRadioHash(fakeHash: string): boolean {
  return typeof fakeHash === 'string' && fakeHash.startsWith('lastfm_radio_');
}

/** Modal titles (9358-9368), keyed by source + the lastfm hash special. */
export function modalTitle(source: SyncSourceId, fakeHash: string): string {
  if (source === 'mirrored') return '🎵 Mirrored Playlist Discovery';
  if (isLastfmRadioHash(fakeHash)) return '📻 Last.fm Radio Discovery';
  const titles: Record<SyncSourceId, string> = {
    spotify_public: '🎵 Spotify Playlist Discovery',
    itunes_link: '🎵 iTunes Link Discovery',
    deezer: '🎵 Deezer Playlist Discovery',
    tidal: '🎵 Tidal Playlist Discovery',
    qobuz: '🎵 Qobuz Playlist Discovery',
    beatport: '🎵 Beatport Chart Discovery',
    listenbrainz: '🎵 ListenBrainz Playlist Discovery',
    youtube: '🎵 YouTube Playlist Discovery',
    mirrored: '🎵 Mirrored Playlist Discovery',
  };
  return titles[source];
}

/**
 * The short source label for the table headers (9369-9379). NOTE the vanilla's
 * abbreviations: LB and YT; mirrored capitalises its mirrored_source.
 */
export function modalSourceLabel(
  source: SyncSourceId,
  fakeHash: string,
  mirroredSource?: string,
): string {
  if (source === 'mirrored') {
    return mirroredSource
      ? mirroredSource.charAt(0).toUpperCase() + mirroredSource.slice(1)
      : 'Source';
  }
  if (isLastfmRadioHash(fakeHash)) return 'Last.fm';
  const labels: Record<SyncSourceId, string> = {
    spotify_public: 'Spotify',
    itunes_link: 'iTunes',
    deezer: 'Deezer',
    tidal: 'Tidal',
    qobuz: 'Qobuz',
    beatport: 'Beatport',
    listenbrainz: 'LB',
    youtube: 'YT',
    mirrored: 'Source',
  };
  return labels[source];
}

/** The description's source word (getModalDescription 9931 — 'mirrored' is lowercase). */
export function descriptionSourceWord(source: SyncSourceId, fakeHash: string): string {
  if (source === 'mirrored') return 'mirrored';
  if (isLastfmRadioHash(fakeHash)) return 'Last.fm Radio';
  const words: Record<SyncSourceId, string> = {
    spotify_public: 'Spotify',
    itunes_link: 'iTunes',
    deezer: 'Deezer',
    tidal: 'Tidal',
    qobuz: 'Qobuz',
    beatport: 'Beatport',
    listenbrainz: 'ListenBrainz',
    youtube: 'YouTube',
    mirrored: 'mirrored',
  };
  return words[source];
}

/** getModalDescription's phase switch (9932-9944), metadata label injected. */
export function modalDescription(phase: string, sourceWord: string, metadataLabel: string): string {
  switch (phase) {
    case 'fresh':
      return `Ready to discover clean ${metadataLabel} metadata for ${sourceWord} tracks...`;
    case 'discovered':
    case 'downloading':
    case 'download_complete':
      return 'Discovery complete! View the results below.';
    default:
      return `Discovering clean ${metadataLabel} metadata for ${sourceWord} tracks...`;
  }
}

/** getInitialProgressText (9946-9958). */
export function initialProgressText(phase: string): string {
  switch (phase) {
    case 'fresh':
      return 'Click Start Discovery to begin...';
    case 'discovered':
    case 'downloading':
    case 'download_complete':
      return 'Discovery completed!';
    default:
      return 'Starting discovery...';
  }
}

/** The live progress line — "M / T tracks matched (P%)" (updateYouTubeDiscoveryModal 10127). */
export function progressLineText(matches: number, total: number, percent: number): string {
  return `${matches} / ${total} tracks matched (${percent}%)`;
}

/**
 * Modal-open progress seeding (9512-9527): stored progress, else computed from
 * results/track counts; matches fall back to counting found rows.
 */
export function seededProgress(state: SourcePlaylistState): { progress: number; matches: number } {
  let progress = state.discoveryProgress || 0;
  const trackCount = Array.isArray(state.playlist?.tracks)
    ? (state.playlist.tracks as unknown[]).length
    : 0;
  if (progress === 0 && state.rows.length > 0 && trackCount > 0) {
    progress = Math.min(100, Math.round((state.rows.length / trackCount) * 100));
  }
  const matches =
    state.spotifyMatches || state.rows.filter((r) => r.status_class === 'found').length;
  return { progress, matches };
}

/**
 * The numbers the "M / T tracks matched (P%)" line may print. The percent is
 * derived from matched/total and clamped - it is NOT the discovery-scan
 * percent, which measures how far the WORKER got, not how many tracks
 * matched. Rendering the scan percent in a line labeled "matched" is how a
 * cached mirrored open said "134 / 230 tracks matched (100%)", and an
 * inflated client-side counter is how a fix during discovery printed
 * "105 / 100" (user report, aug 25). Both are capped here at the display.
 */
export function matchLineNumbers(
  matches: number,
  total: number,
): { matches: number; percent: number } {
  if (total <= 0) return { matches, percent: 0 };
  const shown = Math.max(0, Math.min(matches, total));
  return { matches: shown, percent: Math.min(100, Math.round((shown / total) * 100)) };
}

/**
 * The two-format download-track builder from startYouTubeDownloadMissing
 * (10727-10753): spotify_data verbatim when present, else reconstructed from
 * the flat fields with the album coerced to an object for wishlist compat.
 * Rows qualify with spotify_data OR (spotify_track + status_class 'found').
 */
export function buildDownloadTracks(results: RawDiscoveryResult[]): unknown[] {
  return results
    .filter(
      (result) => result.spotify_data || (result.spotify_track && result.status_class === 'found'),
    )
    .map((result) => {
      if (result.spotify_data) return result.spotify_data;
      const albumData = (result as { spotify_album?: unknown }).spotify_album || 'Unknown Album';
      const albumObject =
        typeof albumData === 'object' && albumData !== null
          ? albumData
          : {
              name: typeof albumData === 'string' ? albumData : 'Unknown Album',
              album_type: 'album',
              images: [],
            };
      return {
        id: (result as { spotify_id?: string }).spotify_id || 'unknown',
        name: result.spotify_track || 'Unknown Track',
        artists: result.spotify_artist ? [result.spotify_artist] : ['Unknown Artist'],
        album: albumObject,
        duration_ms: 0,
      };
    });
}
