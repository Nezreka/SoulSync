/**
 * The hero watchlist button's requests (initializeLibraryWatchlistButton /
 * toggleLibraryWatchlist / updateLibraryWatchlistButtonStatus, library.js:
 * 6931-7063). The button itself lives in -ui/artist-hero.tsx; #library-artist-
 * watchlist-btn keeps its id because the guided tour anchors to it
 * (helper.js:1518).
 */

/** Current status; null when the check fails (the vanilla just warned). */
export async function checkWatchlistRequest(artistId: unknown): Promise<boolean | null> {
  try {
    const response = await fetch('/api/watchlist/check', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ artist_id: artistId }),
    });
    const data = await response.json();
    if (!data.success) return null;
    return Boolean(data.is_watching);
  } catch (error) {
    console.warn('Failed to check library watchlist status:', error);
    return null;
  }
}

/**
 * The one /api/watchlist/add request. `source` names the provider that owns
 * `artistId`; leave it out for a Spotify id or a library row id, which the
 * server resolves itself. Without it a numeric id is filed under the primary
 * metadata source, so an iTunes id can land in the Deezer column.
 */
export async function addToWatchlistRequest(
  artistId: unknown,
  artistName: string,
  source?: string,
): Promise<string> {
  const response = await fetch('/api/watchlist/add', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      artist_id: artistId,
      artist_name: artistName,
      ...(source ? { source } : {}),
    }),
  });
  const data = await response.json();
  if (!data.success) throw new Error(data.error || 'Failed to add to watchlist');
  return String(data.message ?? '');
}

/**
 * Check-then-toggle, exactly the vanilla's two-step (6963-6988): the CURRENT
 * server state decides between add and remove, not the button's belief.
 */
export async function toggleWatchlistRequest(
  artistId: unknown,
  artistName: string,
  source?: string,
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
  const isWatching = Boolean(checkData.is_watching);

  let message: string;
  if (isWatching) {
    const response = await fetch('/api/watchlist/remove', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ artist_id: artistId }),
    });
    const data = await response.json();
    if (!data.success) throw new Error(data.error || 'Failed to update watchlist');
    message = String(data.message ?? '');
  } else {
    message = await addToWatchlistRequest(artistId, artistName, source);
  }

  // Dashboard badge, when that page's script is loaded (7012).
  if (typeof window.updateWatchlistCount === 'function') window.updateWatchlistCount();
  return { watching: !isWatching, message };
}
