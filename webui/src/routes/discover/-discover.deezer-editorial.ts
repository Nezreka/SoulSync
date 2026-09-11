/**
 * Deezer's own editors publish playlists, and the public API serves them with
 * no key and no linked account. This is the browse half of a pipeline that
 * already exists: picking a card hands the playlist to the Sync page's Deezer
 * flow, which is the same path a pasted deezer.com/playlist/... link takes —
 * load, match, sync. Nothing new downstream.
 *
 * The genre chips are served rather than hardcoded here so the two ends cannot
 * drift: core/deezer_client.py owns the list.
 */

import { apiClient, readJson } from '@/app/api-client';

export interface DeezerEditorialPlaylist {
  id: string;
  title: string;
  creator: string;
  track_count: number;
  image_url: string;
  link: string;
  source: string;
}

export interface DeezerEditorialGenre {
  id: number;
  name: string;
}

interface EditorialResponse {
  success?: boolean;
  playlists?: DeezerEditorialPlaylist[];
  count?: number;
  error?: string;
}

interface GenresResponse {
  success?: boolean;
  genres?: DeezerEditorialGenre[];
}

/**
 * One genre's curated playlists.
 *
 * Never throws. A browse row that cannot load is an empty row — the server
 * already answers 200 with an empty list rather than an error status, and a
 * transport failure is treated the same way, because a dead shelf must not take
 * the page with it.
 */
export async function fetchDeezerEditorial(genreId: number): Promise<DeezerEditorialPlaylist[]> {
  try {
    const data = await readJson<EditorialResponse>(
      apiClient.get('discover/deezer/editorial', { searchParams: { genre: String(genreId) } }),
    );
    return data.playlists ?? [];
  } catch {
    return [];
  }
}

/** The chips. Empty on failure, which hides the chip row rather than the shelf. */
export async function fetchDeezerEditorialGenres(): Promise<DeezerEditorialGenre[]> {
  try {
    const data = await readJson<GenresResponse>(apiClient.get('discover/deezer/genres'));
    return data.genres ?? [];
  } catch {
    return [];
  }
}

/**
 * Hand a playlist to the Sync page, the way a user would by pasting its link.
 *
 * Deliberately NOT a second loader. `loadDeezerPlaylist` reads the url input and
 * runs the whole existing flow — fetch, mirror, render, state — so driving that
 * input is what keeps this one pipeline instead of two. The same handoff idiom
 * the global search widget uses for Soulseek (downloads.js, _gsNavigateToSearchPage).
 *
 * Returns false when the Sync page is not reachable, so the caller can say so
 * rather than looking like the click did nothing.
 */
export function openDeezerPlaylistInSync(playlist: DeezerEditorialPlaylist): boolean {
  const link = playlist.link || `https://www.deezer.com/playlist/${playlist.id}`;
  const navigate = window.navigateToPage;
  if (typeof navigate !== 'function') return false;

  navigate('sync');
  // the input only exists once the page has mounted
  window.setTimeout(() => {
    const input = document.getElementById('deezer-url-input') as HTMLInputElement | null;
    if (input) {
      input.value = link;
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
    void window.loadDeezerPlaylist?.();
  }, 300);
  return true;
}
