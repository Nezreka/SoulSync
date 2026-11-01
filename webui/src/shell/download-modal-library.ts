/**
 * What Library v2 adds to the shared download dialog.
 *
 * The dialog's library status is the ownership preview (previewModalOwnership
 * in downloads.js): one read of Library v2 when it opens, the same answer
 * Begin Analysis gives. A missing track the user already wants carries
 * `in_wishlist`, and `markWishlistedTrack` draws it the way the library does.
 *
 * Its "Add to Wishlist" button (the same in the Add to Wishlist dialog) hands the tracks it added to `monitorAddedTracks`, which
 * starts their download at once instead of waiting for the next wishlist run,
 * and monitors the album itself when every one of its tracks was picked.
 *
 * Both are reached through window from the classic scripts: one line in
 * `updateTrackAnalysisResults` and one in each add handler in wishlist-tools.js.
 */

export type MonitoredDownloadOutcome = 'started' | 'busy' | 'failed' | 'nothing';

/** the wishlist heart and its sparkle, as the library draws them (list-icons.tsx) */
const WISHLIST_PATHS = [
  'M10.5 19.5S3 15 3 9.8A4.3 4.3 0 0 1 10.5 7a4.3 4.3 0 0 1 7.5 2.8c0 5.2-7.5 9.7-7.5 9.7z',
  'M20.2 3.4l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z',
];
const SVG_NS = 'http://www.w3.org/2000/svg';

function bookmarkIcon(): SVGSVGElement {
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('class', 'match-bookmark');
  for (const d of WISHLIST_PATHS) {
    const path = document.createElementNS(SVG_NS, 'path');
    path.setAttribute('d', d);
    svg.appendChild(path);
  }
  return svg;
}

/** A missing row the user already wants: "On Wishlist" with the library's heart. */
export function markWishlistedTrack(cell: HTMLElement): void {
  cell.textContent = 'On Wishlist';
  cell.prepend(bookmarkIcon());
  cell.className = 'track-match-status match-monitored';
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

/** One track "Add to Wishlist" added: its wishlist row and the release it landed on. */
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
 * The end of an "Add to Wishlist" click: monitor the release when all of it was
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
    ? `Added the album to your Wishlist (${added} track${added === 1 ? '' : 's'})`
    : `Added ${added} track${added === 1 ? '' : 's'} to your Wishlist`;
  if (outcome === 'started') return `${what} — download started`;
  if (outcome === 'busy') return `${what} — the running wishlist pass takes them`;
  if (outcome === 'failed') return `${what} — the download could not start, still wanted`;
  return what;
}

declare global {
  interface Window {
    markWishlistedTrack?: typeof markWishlistedTrack;
    startMonitoredDownloads?: typeof startMonitoredDownloads;
    monitorAddedTracks?: typeof monitorAddedTracks;
  }
}

Object.assign(window, {
  markWishlistedTrack,
  startMonitoredDownloads,
  monitorAddedTracks,
});
