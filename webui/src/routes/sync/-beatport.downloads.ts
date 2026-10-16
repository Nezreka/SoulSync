/**
 * The Beatport release download — beatport-ui.js 1855-1923.
 *
 * Four card types share ONE release handler. The hero slide, the releases
 * slider, the hype picks and the top-10 release cards all call it; what
 * differs between them is only how they answer "which release was clicked",
 * and that is settled in the components, not here.
 *
 * a release is a real album with a real artist, so it opens the album
 * download. charts and lists are not handled here any more: they open as
 * playlists to identify (-beatport.lists.ts), because sending them to the
 * download modal as a chart-named compilation is what put "Hype Top 10" into
 * album tags.
 */

import type { BeatportListRequest } from './-beatport.lists';

import { fetchBeatportReleaseMetadata } from './-beatport.api';
import {
  beatportDownloadContext,
  beatportReleasePlaylistId,
  normaliseReleaseTrackArtists,
  releaseBubbleImage,
} from './-beatport.core';

/* ── The double-click latch ───────────────────────────────────────────────── */

/**
 * `_beatportModalOpening`: set on entry, cleared on EVERY exit, so a release
 * click is held for exactly as long as the work takes.
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
  /**
   * opens a chart-type list as a beatport discovery (-beatport.lists.ts).
   * the tab supplies it, because only the tab holds the beatport vertical.
   */
  openList?: (request: BeatportListRequest) => void;
}

export function defaultBeatportDownloadEnv(): BeatportDownloadEnv {
  return {
    showToast: (message, type) => window.showToast?.(message, type),
    showLoadingOverlay: (message) => window.showLoadingOverlay?.(message),
    hideLoadingOverlay: () => window.hideLoadingOverlay?.(),
    openDownloadModal: (...args) => window.openDownloadMissingModalForArtistAlbum?.(...args),
    registerDownload: (name, image, virtualPlaylistId) =>
      window.registerBeatportDownload?.(name, image, virtualPlaylistId),
    now: () => Date.now(),
    random: () => Math.random(),
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
