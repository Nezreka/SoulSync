/**
 * The Server Manager tab's pure core + wire calls (pages-extra.js, whole file).
 *
 * This tab has NO engine coupling: it never touches downloads.js,
 * spotifyPlaylists or the discovery machinery, so unlike the account tabs it
 * needs no window bridge. `_serverEditorState` is script-scoped but only this
 * one file reads it, so it becomes ordinary React state.
 */

export interface ServerPlaylist {
  id: string;
  name: string;
  track_count?: number;
  /** Set by the split below, not by the backend. */
  _synced?: boolean;
}

export interface ServerPlaylistsResponse {
  success?: boolean;
  error?: string;
  server_type?: string;
  playlists?: ServerPlaylist[];
}

/**
 * Split the server's playlists into SYNCED and Other (59-73).
 *
 * A playlist counts as synced when its name appears in the mirrored list OR in
 * the sync history, matched trimmed and lower-cased. The returned order is
 * synced-then-unsynced, which is also the order `_serverPlaylists` keeps (75).
 */
export function splitServerPlaylists(
  playlists: readonly ServerPlaylist[],
  mirroredNames: readonly string[],
  historyNames: readonly string[],
): { synced: ServerPlaylist[]; unsynced: ServerPlaylist[] } {
  const key = (s: string) => s.trim().toLowerCase();
  const mirrored = new Set(mirroredNames.map(key));
  const history = new Set(historyNames.map(key));
  const synced: ServerPlaylist[] = [];
  const unsynced: ServerPlaylist[] = [];
  for (const pl of playlists) {
    const k = key(pl.name);
    if (mirrored.has(k) || history.has(k)) synced.push({ ...pl, _synced: true });
    else unsynced.push({ ...pl, _synced: false });
  }
  return { synced, unsynced };
}

/** 'Server Playlists (Plex)' — the server type, first letter upper (76-78). */
export function serverTabTitle(serverType: string | null | undefined): string {
  const name = serverType ? serverType.charAt(0).toUpperCase() + serverType.slice(1) : '';
  return `Server Playlists (${name})`;
}

/**
 * The card's hue, cycled per index (94). Cards continue the sequence across the
 * two sections — the Other section starts at synced.length (145), so the two
 * grids never repeat a colour side by side.
 */
export function serverCardHue(index: number): number {
  return (index * 37 + 200) % 360;
}

/**
 * ms → m:ss, or '' for nothing (_formatDurationMs, 484).
 *
 * ROUNDS to the nearest second — not floor. 1500ms reads '0:02' here where the
 * page's other duration helpers would say '0:01'. Transcribed, not unified.
 */
export function formatDurationMs(ms: number | null | undefined): string {
  if (!ms) return '';
  const seconds = Math.round(ms / 1000);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
}

/* ── Wire calls ───────────────────────────────────────────────────────────── */

/**
 * The three PARALLEL loads (41-52). The mirrored and history responses are
 * each tolerated independently: the vanilla wraps both in try/catch and falls
 * back to [], so one failing endpoint still renders the list — it just cannot
 * mark anything synced.
 */
export async function fetchServerPlaylistData(): Promise<{
  data: ServerPlaylistsResponse;
  mirroredNames: string[];
  historyNames: string[];
}> {
  const [serverRes, mirroredRes, historyRes] = await Promise.all([
    fetch('/api/server/playlists'),
    fetch('/api/mirrored-playlists'),
    fetch('/api/sync/history/names'),
  ]);
  const data = (await serverRes.json()) as ServerPlaylistsResponse;

  let mirroredNames: string[] = [];
  try {
    const mirrored = await mirroredRes.json();
    if (Array.isArray(mirrored)) {
      mirroredNames = (mirrored as { name?: string }[]).map((p) => p.name ?? '');
    }
  } catch {
    // 48: a failed mirrored fetch must not sink the list.
  }

  let historyNames: string[] = [];
  try {
    const history = await historyRes.json();
    if (Array.isArray(history)) historyNames = history as string[];
  } catch {
    // 51: same tolerance for the history names.
  }

  return { data, mirroredNames, historyNames };
}

/** The mirrored rows a server playlist's NAME matches (openServerPlaylistEditor, 158). */
export interface MirroredMatch {
  id: number;
  name: string;
  source?: string;
  owner?: string;
  track_count?: number;
  updated_at?: string;
  mirrored_at?: string;
}

/**
 * A failed lookup is SWALLOWED (168-170): the vanilla logs and carries on with
 * an empty list, which lands the user in the server-only view rather than an
 * error. Reproduced — throwing here would change a working path into a dead end.
 *
 * Declared divergence: `p.name` is guarded. The vanilla calls .trim() on it
 * unguarded (164), so a nameless row would throw and take the whole lookup with
 * it into that same empty-list catch.
 */
export async function fetchMirroredMatches(playlistName: string): Promise<MirroredMatch[]> {
  try {
    const response = await fetch('/api/mirrored-playlists');
    const rows = await response.json();
    if (!Array.isArray(rows)) return [];
    const key = playlistName.trim().toLowerCase();
    return (rows as MirroredMatch[]).filter((p) => (p.name ?? '').trim().toLowerCase() === key);
  } catch {
    return [];
  }
}

/**
 * selectDisambigPlaylist re-fetches the FULL mirrored playlist by id before
 * opening the compare view (237-239) — the disambiguation list rows are only
 * list entries, and the compare view needs the whole object.
 */
export async function fetchMirroredPlaylistById(mirroredId: number): Promise<MirroredMatch> {
  const response = await fetch(`/api/mirrored-playlists/${mirroredId}`);
  return (await response.json()) as MirroredMatch;
}
