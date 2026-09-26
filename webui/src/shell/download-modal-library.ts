/**
 * What Library v2 adds to the shared download dialog.
 *
 * The dialog (Search, Discover, the artist page, playlists) used to show every
 * track as "Pending" until "Begin Analysis" had run, so it could not say what
 * the library already has. It now asks as soon as it opens: the same fast,
 * Library-v2 backed check the search results use, which reads owned files and
 * the wishlist in one pass. A track the user already wants shows as
 * monitored. "Begin Analysis" still does its own, deeper matching and
 * overwrites these cells as it goes.
 *
 * Its "Monitor" button (the old "Add to Wishlist", the same in the Add to
 * Wishlist dialog) hands the tracks it added to `monitorAddedTracks`, which
 * starts their download at once instead of waiting for the next wishlist run,
 * and monitors the album itself when every one of its tracks was picked.
 *
 * Both are reached through window from the classic scripts: one line in
 * `applyProgressiveTrackRendering` (every variant of the dialog calls it once
 * its rows exist) and one in each add handler in wishlist-tools.js.
 */

interface ModalTrack {
  name?: string;
  title?: string;
  track_name?: string;
  artists?: Array<string | { name?: string | null } | null> | string | null;
  artist?: string | null;
  artist_name?: string | null;
}

interface PresenceRow {
  in_library?: boolean;
  in_wishlist?: boolean;
}

export type MonitoredDownloadOutcome = 'started' | 'busy' | 'failed' | 'nothing';

/** One request answers this many rows; a long playlist is asked in chunks. */
const CHUNK = 200;

function trackTitle(track: ModalTrack): string {
  return String(track.name || track.title || track.track_name || '').trim();
}

/** The primary credit: the check keys on title plus first artist. */
function trackArtist(track: ModalTrack): string {
  const { artists } = track;
  if (Array.isArray(artists)) {
    for (const entry of artists) {
      const name = typeof entry === 'string' ? entry : entry?.name;
      if (name && name.trim()) return name.trim();
    }
  } else if (typeof artists === 'string' && artists.trim()) {
    return artists.trim();
  }
  return String(track.artist_name || track.artist || '').trim();
}

function statusFor(row: PresenceRow | undefined): { text: string; cls: string } {
  if (row?.in_library) return { text: '✅ In library', cls: 'match-found' };
  if (row?.in_wishlist) return { text: '🔖 Monitored', cls: 'match-monitored' };
  return { text: '❌ Missing', cls: 'match-missing' };
}

async function presence(rows: Array<{ name: string; artist: string }>): Promise<PresenceRow[]> {
  const response = await fetch('/api/enhanced-search/library-check', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ albums: [], tracks: rows }),
  });
  if (!response.ok) throw new Error(`library check answered ${response.status}`);
  const data = (await response.json()) as { tracks?: PresenceRow[] };
  return Array.isArray(data.tracks) ? data.tracks : [];
}

/** What the dialog registers in `activeDownloadProcesses` before its rows render. */
interface ModalProcess {
  tracks?: ModalTrack[] | null;
  /** The album artist, on the album dialogs only. */
  artist?: { name?: string | null } | null;
}

/**
 * Fill the dialog's "Library Status" column and its Found / Missing counters.
 *
 * Each track is asked under its own first artist and, on an album dialog,
 * under the album artist too: a guest's song on someone's compilation is
 * filed under the album artist in the library. Only cells still showing
 * "Pending" are touched, so an analysis that already answered wins. Never
 * throws: a failed check leaves the dialog as it was.
 */
export async function hydrateDownloadModalLibraryStatus(
  playlistId: string,
  process: ModalProcess | null | undefined,
): Promise<void> {
  const tracks = process?.tracks;
  if (!playlistId || !Array.isArray(tracks) || tracks.length === 0) return;
  const albumArtist = String(process?.artist?.name || '').trim();
  const queries: Array<{ index: number; name: string; artist: string }> = [];
  tracks.forEach((track, index) => {
    const name = trackTitle(track);
    if (!name) return;
    const artist = trackArtist(track);
    queries.push({ index, name, artist });
    if (albumArtist && albumArtist.toLowerCase() !== artist.toLowerCase()) {
      queries.push({ index, name, artist: albumArtist });
    }
  });
  const answers = new Map<number, PresenceRow>();
  try {
    for (let start = 0; start < queries.length; start += CHUNK) {
      const chunk = queries.slice(start, start + CHUNK);
      const rows = await presence(chunk.map(({ name, artist }) => ({ name, artist })));
      rows.forEach((row, offset) => {
        const { index } = chunk[offset]!;
        const seen = answers.get(index);
        answers.set(index, {
          in_library: Boolean(seen?.in_library || row?.in_library),
          in_wishlist: Boolean(seen?.in_wishlist || row?.in_wishlist),
        });
      });
    }
  } catch (error) {
    console.debug('download dialog library status skipped:', error);
    return;
  }
  let found = 0;
  answers.forEach((answer, index) => {
    if (answer.in_library) found += 1;
    const cell = document.getElementById(`match-${playlistId}-${index}`);
    if (!cell || !cell.classList.contains('match-checking')) return;
    const status = statusFor(answer);
    cell.textContent = status.text;
    cell.className = `track-match-status ${status.cls}`;
  });
  // The counters belong to the analysis once it has reported; before that
  // they read "-".
  const foundStat = document.getElementById(`stat-found-${playlistId}`);
  const missingStat = document.getElementById(`stat-missing-${playlistId}`);
  if (foundStat && foundStat.textContent?.trim() === '-') foundStat.textContent = String(found);
  if (missingStat && missingStat.textContent?.trim() === '-') {
    missingStat.textContent = String(answers.size - found);
  }
}

/**
 * Download what "Monitor" just added, now.
 *
 * The same manual wishlist batch the Wishlist page's download button starts,
 * narrowed to exactly these ids, so the Quality Profile chosen in the dialog
 * (stored on each wishlist row) and the usual retry path apply. A track that
 * is not found stays in the wishlist, monitored. `busy` means the automatic
 * wishlist run is going and takes them with it.
 */
export async function startMonitoredDownloads(
  trackIds: Array<string | number | null | undefined>,
): Promise<MonitoredDownloadOutcome> {
  const ids = [...new Set(trackIds.filter((id) => id != null && id !== '').map(String))];
  if (ids.length === 0) return 'nothing';
  try {
    const response = await fetch('/api/wishlist/download_missing', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ track_ids: ids }),
    });
    if (response.status === 409) return 'busy';
    const data = (await response.json().catch(() => ({}))) as { success?: boolean };
    return response.ok && data.success !== false ? 'started' : 'failed';
  } catch {
    return 'failed';
  }
}

/** One track "Monitor" added: its wishlist row and the release it landed on. */
export interface AddedRow {
  id?: string | number | null;
  /** The Library v2 album id; absent when nothing was materialized (non-admin). */
  album?: number | null;
}

/**
 * Monitor the release itself when every one of its tracks was picked.
 *
 * Only then: picking a few tracks wants those tracks, picking all of them wants
 * the album, which also covers tracks the provider adds to it later. Needs one
 * Library v2 release behind all the rows; a playlist spans many and never gets
 * here.
 */
async function monitorRelease(rows: AddedRow[]): Promise<boolean> {
  const albums = new Set(rows.map((row) => row.album).filter((id) => id != null));
  if (albums.size !== 1) return false;
  const [albumId] = albums;
  try {
    const response = await fetch(`/api/library/v2/albums/${albumId}/monitor`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ monitored: true }),
    });
    return response.ok;
  } catch {
    return false;
  }
}

/**
 * The end of a "Monitor" click: monitor the release when all of it was
 * picked, start the downloads, and word the toast.
 *
 * `added` counts the tracks the server accepted, `rows` the ones it actually
 * added (an owned track it skipped has nothing to download). `wholeRelease`
 * says the pick was every track of one album.
 */
export async function monitorAddedTracks(
  added: number,
  rows: AddedRow[],
  wholeRelease = false,
): Promise<string> {
  const release = wholeRelease && (await monitorRelease(rows));
  const outcome = await startMonitoredDownloads(rows.map((row) => row.id));
  const what = release
    ? `Monitoring the album (${added} track${added === 1 ? '' : 's'})`
    : `Monitoring ${added} track${added === 1 ? '' : 's'}`;
  if (outcome === 'started') return `${what} — download started`;
  if (outcome === 'busy') return `${what} — the running wishlist pass takes them`;
  if (outcome === 'failed') return `${what} — the download could not start, still wanted`;
  return what;
}

declare global {
  interface Window {
    hydrateDownloadModalLibraryStatus?: typeof hydrateDownloadModalLibraryStatus;
    startMonitoredDownloads?: typeof startMonitoredDownloads;
    monitorAddedTracks?: typeof monitorAddedTracks;
  }
}

Object.assign(window, {
  hydrateDownloadModalLibraryStatus,
  startMonitoredDownloads,
  monitorAddedTracks,
});
