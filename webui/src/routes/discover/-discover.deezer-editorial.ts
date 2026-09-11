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
import { postMirrorPlaylist } from '@/routes/sync/-sync.api';
import { buildMirrorPayload } from '@/routes/sync/-sync.import';

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

/** What /api/deezer/playlist/<id> answers with. */
interface DeezerPlaylistResponse {
  id?: string | number;
  name?: string;
  owner?: string;
  image_url?: string;
  tracks?: unknown[];
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
 * Load a Deezer playlist and put it on the Sync page's mirrored tab.
 *
 * The first version of this drove `#deezer-url-input` and called the global
 * `loadDeezerPlaylist`. Both are the RETIRED vanilla sync page: that input does
 * not exist in index.html any more, and the React sheet's field is a controlled
 * input, so assigning .value from outside would not update its state even if it
 * were the right element. The click navigated and did nothing.
 *
 * What actually makes a playlist appear is POST /api/mirror-playlist, which is
 * the same call the Sync page makes after parsing a pasted link. So: fetch the
 * playlist through the loader that already exists, mirror it with the payload
 * builder that already exists, then show the user where it landed.
 *
 * Returns an error string on failure, or null when it worked.
 */
export async function openDeezerPlaylistInSync(
  playlist: DeezerEditorialPlaylist,
): Promise<string | null> {
  let loaded: DeezerPlaylistResponse;
  try {
    const response = await fetch(`/api/deezer/playlist/${encodeURIComponent(playlist.id)}`);
    if (!response.ok) {
      const body = (await response.json().catch(() => ({}))) as { error?: string };
      return body.error || `Could not load that playlist (${response.status})`;
    }
    loaded = (await response.json()) as DeezerPlaylistResponse;
  } catch {
    return 'Could not reach the server to load that playlist';
  }

  const tracks = loaded.tracks ?? [];
  if (tracks.length === 0) return 'That playlist came back empty';

  try {
    const payload = buildMirrorPayload(
      'deezer',
      loaded.id ?? playlist.id,
      loaded.name ?? playlist.title,
      tracks as never,
      {
        owner: loaded.owner ?? playlist.creator,
        image_url: loaded.image_url ?? playlist.image_url,
        description: playlist.link,
      },
    );
    const result = await postMirrorPlaylist(payload);
    if (result.success === false) return result.error || 'Could not mirror that playlist';
  } catch {
    return 'Could not mirror that playlist';
  }

  // only navigate once it is actually there, so the tab is never opened onto
  // a playlist that failed to arrive
  window.SoulSyncWebRouter?.navigateToPage('sync');
  window.setTimeout(() => {
    document
      .querySelector<HTMLElement>('.sync-tab-button[data-tab="mirrored"]')
      ?.click();
  }, 200);
  return null;
}
