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
 * Check-then-toggle, exactly the vanilla's two-step (6963-6988): the CURRENT
 * server state decides between add and remove, not the button's belief.
 */
export async function toggleWatchlistRequest(
  artistId: unknown,
  artistName: string,
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

  const endpoint = isWatching ? '/api/watchlist/remove' : '/api/watchlist/add';
  const payload = isWatching
    ? { artist_id: artistId }
    : { artist_id: artistId, artist_name: artistName };
  const response = await fetch(endpoint, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!data.success) throw new Error(data.error || 'Failed to update watchlist');

  // Dashboard badge, when that page's script is loaded (7012).
  if (typeof window.updateWatchlistCount === 'function') window.updateWatchlistCount();
  return { watching: !isWatching, message: String(data.message ?? '') };
}
