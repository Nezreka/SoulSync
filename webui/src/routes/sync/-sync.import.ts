/**
 * Import-file tab parser suite + the mirror contract normalizer — ported 1:1
 * from stats-automations.js 13-495 (the file is misnamed; its first third is
 * sync-page content). All pure: the vanilla's DOM reads (format selects,
 * column-map selects) become parameters here.
 */

import type { SourceSkipped } from './-sync.state';

/** The one track shape the whole import/mirror pipeline speaks. */
export interface ImportTrack {
  track_name: string;
  artist_name: string;
  album_name: string;
  duration_ms: number;
}

export const IMPORT_FILE_EXTENSIONS: readonly string[] = ['csv', 'tsv', 'txt', 'm3u', 'm3u8'];

/** File-type bucket from an extension (stats-automations.js 53-54); null = unsupported. */
export function importFileTypeForExtension(ext: string): 'm3u' | 'text' | 'csv' | null {
  const e = ext.toLowerCase();
  if (!IMPORT_FILE_EXTENSIONS.includes(e)) return null;
  return e === 'm3u' || e === 'm3u8' ? 'm3u' : e === 'txt' ? 'text' : 'csv';
}

/**
 * Delimiter sniffing on the first line (stats-automations.js 60-67): tab wins
 * ties, then semicolon, comma is the fallback.
 */
export function importFileDetectDelimiter(firstLine: string): string {
  const tab = (firstLine.match(/\t/g) || []).length;
  const semi = (firstLine.match(/;/g) || []).length;
  const comma = (firstLine.match(/,/g) || []).length;
  if (tab >= comma && tab >= semi && tab > 0) return '\t';
  if (semi >= comma && semi > 0) return ';';
  return ',';
}

/**
 * CSV/TSV parser with doubled-quote handling (stats-automations.js 69-105).
 * Fewer than two non-blank lines ⇒ empty result (a header alone is no data).
 */
export function importFileParseCsv(
  text: string,
  delimiter: string,
): { headers: string[]; rows: string[][] } {
  const lines = text.split(/\r?\n/).filter((l) => l.trim());
  if (lines.length < 2) return { headers: [], rows: [] };

  function parseLine(line: string): string[] {
    const result: string[] = [];
    let current = '';
    let inQuotes = false;
    for (let i = 0; i < line.length; i++) {
      const ch = line[i];
      if (ch === '"') {
        if (inQuotes && i + 1 < line.length && line[i + 1] === '"') {
          current += '"';
          i++;
        } else {
          inQuotes = !inQuotes;
        }
      } else if (ch === delimiter && !inQuotes) {
        result.push(current.trim());
        current = '';
      } else {
        current += ch;
      }
    }
    result.push(current.trim());
    return result;
  }

  const headers = parseLine(lines[0]);
  const rows: string[][] = [];
  for (let i = 1; i < lines.length; i++) {
    const row = parseLine(lines[i]);
    if (row.some((cell) => cell)) rows.push(row);
  }
  return { headers, rows };
}

export type M3uOrder = 'artist-title' | 'title-artist';

/** Lower case, letters and digits only: how two spellings of a name compare. */
function m3uFold(value: string): string {
  return value.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, '');
}

/** Does `folded` name the thing `label` names? A file name is often cut
 *  short, so a long shared start counts. */
function m3uNames(folded: string, label: string): boolean {
  const want = m3uFold(label);
  if (want.length < 3 || folded.length < 3) return false;
  return (
    folded === want || (folded.length >= 12 && want.startsWith(folded)) || folded.includes(want)
  );
}

/**
 * Which side of an "A - B" line is the title, read off the path under it.
 *
 * Players disagree: most write "Artist - Title", iTunes writes
 * "Title - Artist". Nothing in the line says which, but the path usually
 * does (".../Artist/Album/03 Title.mp3"): the half the file is named after is
 * the title, and the half its artist folder is named after is the artist.
 * Each line that can tell casts a vote, and the file is read one way
 * throughout. With no vote either way it is "Artist - Title", the common
 * form and the one SoulSync's own export writes.
 */
export function importM3uDetectOrder(entries: readonly { meta: string; path: string }[]): M3uOrder {
  let titleFirst = 0;
  let artistFirst = 0;
  for (const { meta, path } of entries) {
    const first = meta.indexOf(' - ');
    if (first === -1 || !path) continue;
    const head = meta.slice(0, first).trim();
    const tail = meta.slice(meta.lastIndexOf(' - ') + 3).trim();
    if (!head || !tail || m3uFold(head) === m3uFold(tail)) continue;
    const parts = path.split(/[\\/]/).filter(Boolean);
    const stem = m3uFold(
      (parts.pop() || '').replace(/\.[^.]+$/, '').replace(/^\d+[-.]?\d*[\s._-]+/, ''),
    );
    const artistFolder = m3uFold(parts.length >= 2 ? parts[parts.length - 2] : '');
    const stemHead = m3uNames(stem, head);
    const stemTail = m3uNames(stem, tail);
    const folderHead = m3uNames(artistFolder, head);
    const folderTail = m3uNames(artistFolder, tail);
    // a file called "Artist - Title" names both halves and settles nothing
    const saysTitleFirst = (stemHead && !stemTail ? 1 : 0) + (folderTail && !folderHead ? 1 : 0);
    const saysArtistFirst = (stemTail && !stemHead ? 1 : 0) + (folderHead && !folderTail ? 1 : 0);
    if (saysTitleFirst > saysArtistFirst) titleFirst++;
    else if (saysArtistFirst > saysTitleFirst) artistFirst++;
  }
  return titleFirst > artistFirst ? 'title-artist' : 'artist-title';
}

/**
 * M3U/M3U8 parser (stats-automations.js 113-165). Handles simple and extended
 * forms; a pending #EXTINF is flushed even with no path line after it, because
 * SoulSync's own export replaces un-located paths with "# MISSING:" comments —
 * exactly the tracks a user imports to go match/download.
 *
 * `order` is how an #EXTINF "A - B" reads. Left out, it is worked out from the
 * paths (`importM3uDetectOrder`); the one used is returned so the page can
 * show it and let it be turned round.
 */
export function importFileParseM3u(
  text: string | null | undefined,
  order?: M3uOrder,
): {
  tracks: ImportTrack[];
  playlistName: string;
  order: M3uOrder;
} {
  const lines = (text || '').split(/\r?\n/);
  let playlistName = '';
  interface Entry {
    duration_ms: number;
    /** the #EXTINF text; '' when the entry is a bare path */
    meta: string;
    path: string;
  }
  const entries: Entry[] = [];
  let pending: Entry | null = null;

  for (const raw of lines) {
    const line = raw.trim();
    if (!line) continue;
    if (line.charAt(0) === '#') {
      if (/^#EXTINF:/i.test(line)) {
        if (pending) entries.push(pending);
        const rest = line.slice(8);
        const comma = rest.indexOf(',');
        const secs = parseFloat(comma === -1 ? rest : rest.slice(0, comma));
        pending = {
          duration_ms: !isNaN(secs) && secs > 0 ? Math.round(secs * 1000) : 0,
          meta: comma === -1 ? '' : rest.slice(comma + 1).trim(),
          path: '',
        };
      } else if (/^#PLAYLIST:/i.test(line)) {
        playlistName = line.slice(line.indexOf(':') + 1).trim();
      }
      continue;
    }
    if (pending && pending.meta) entries.push({ ...pending, path: line });
    else entries.push({ duration_ms: 0, meta: '', path: line });
    pending = null;
  }
  if (pending) entries.push(pending);

  const used = order ?? importM3uDetectOrder(entries);
  const tracks: ImportTrack[] = [];
  for (const entry of entries) {
    let artist = '';
    let title = '';
    if (entry.meta) {
      const first = entry.meta.indexOf(' - ');
      if (first === -1) title = entry.meta;
      else if (used === 'title-artist') {
        // the artist is the last part: a title may carry a dash of its own
        const last = entry.meta.lastIndexOf(' - ');
        title = entry.meta.slice(0, last).trim();
        artist = entry.meta.slice(last + 3).trim();
      } else {
        artist = entry.meta.slice(0, first).trim();
        title = entry.meta.slice(first + 3).trim();
      }
    } else if (entry.path) {
      // a bare path: its file name, which is "Artist - Title" when it has both
      const base = (entry.path.split(/[\\/]/).pop() || entry.path).replace(/\.[^.]+$/, '');
      const i = base.indexOf(' - ');
      if (i === -1) title = base.trim();
      else {
        artist = base.slice(0, i).trim();
        title = base.slice(i + 3).trim();
      }
    }
    if (!title && !artist) continue;
    tracks.push({
      track_name: title,
      artist_name: artist,
      album_name: '',
      duration_ms: entry.duration_ms,
    });
  }
  return { tracks, playlistName, order: used };
}

export type ImportColumnRole = 'track_name' | 'artist_name' | 'album_name' | 'duration';

/**
 * Header auto-mapping by pattern lists (stats-automations.js 167-197). First
 * pattern hit wins per role; a column already claimed is skipped.
 */
export function importFileAutoMapColumns(headers: string[]): Record<number, ImportColumnRole> {
  const map: Record<number, ImportColumnRole> = {};
  const lowerHeaders = headers.map((h) => h.toLowerCase().trim());

  const trackPatterns = [
    'track_name',
    'track name',
    'track',
    'title',
    'song',
    'song_name',
    'song name',
    'name',
  ];
  const artistPatterns = ['artist_name', 'artist name', 'artist', 'artists', 'performer'];
  const albumPatterns = ['album_name', 'album name', 'album'];
  const durationPatterns = ['duration', 'duration_ms', 'length', 'time'];

  function findMatch(patterns: string[]): number {
    for (const p of patterns) {
      const idx = lowerHeaders.indexOf(p);
      if (idx !== -1 && !(idx in map)) return idx;
    }
    return -1;
  }

  const trackIdx = findMatch(trackPatterns);
  if (trackIdx !== -1) map[trackIdx] = 'track_name';
  const artistIdx = findMatch(artistPatterns);
  if (artistIdx !== -1) map[artistIdx] = 'artist_name';
  const albumIdx = findMatch(albumPatterns);
  if (albumIdx !== -1) map[albumIdx] = 'album_name';
  const durIdx = findMatch(durationPatterns);
  if (durIdx !== -1) map[durIdx] = 'duration';

  return map;
}

/**
 * Track objects from plain-text lines (the text half of _importFileBuildTracks,
 * stats-automations.js 242-268); the vanilla reads order/separator from the
 * format selects — parameters here.
 */
export function importTracksFromText(
  lines: string[],
  order: 'artist-title' | 'title-artist',
  sep: string,
): ImportTrack[] {
  const out: ImportTrack[] = [];
  for (const line of lines) {
    const parts = line.split(sep);
    if (parts.length >= 2) {
      const a = parts[0].trim();
      const b = parts.slice(1).join(sep).trim();
      out.push({
        track_name: order === 'artist-title' ? b : a,
        artist_name: order === 'artist-title' ? a : b,
        album_name: '',
        duration_ms: 0,
      });
    } else {
      out.push({ track_name: line.trim(), artist_name: '', album_name: '', duration_ms: 0 });
    }
  }
  return out;
}

/**
 * Track objects from mapped CSV rows (the CSV half of _importFileBuildTracks,
 * stats-automations.js 269-304), duration heuristic included: mm:ss, else
 * a number read as ms when > 10000 and as seconds otherwise.
 */
export function importTracksFromCsv(
  rows: string[][],
  columnMap: Record<number, ImportColumnRole>,
): ImportTrack[] {
  const keys = Object.keys(columnMap) as unknown as (keyof typeof columnMap)[];
  const trackCol = keys.find((k) => columnMap[k] === 'track_name');
  const artistCol = keys.find((k) => columnMap[k] === 'artist_name');
  const albumCol = keys.find((k) => columnMap[k] === 'album_name');
  const durCol = keys.find((k) => columnMap[k] === 'duration');

  const out: ImportTrack[] = [];
  for (const row of rows) {
    const track = trackCol !== undefined ? row[trackCol] || '' : '';
    const artist = artistCol !== undefined ? row[artistCol] || '' : '';
    const album = albumCol !== undefined ? row[albumCol] || '' : '';
    let dur = durCol !== undefined ? row[durCol] || '' : '';

    let durationMs = 0;
    if (dur) {
      dur = dur.trim();
      if (dur.includes(':')) {
        const parts = dur.split(':');
        durationMs = (parseInt(parts[0]) * 60 + parseInt(parts[1] || '0')) * 1000;
      } else {
        const num = parseFloat(dur);
        durationMs = num > 10000 ? num : num * 1000;
      }
      if (isNaN(durationMs)) durationMs = 0;
    }

    out.push({
      track_name: track,
      artist_name: artist,
      album_name: album,
      duration_ms: durationMs,
    });
  }
  return out;
}

/* ── The mirror contract normalizer (stats-automations.js 469-495) ────────── */

/** A source track in any of the shapes the verticals hand to mirrorPlaylist. */
export interface MirrorSourceTrack {
  track_name?: string;
  name?: string;
  artist_name?: string;
  artists?: (string | { name?: string })[] | string;
  album_name?: string;
  album?: string | { name?: string; images?: { url?: string }[] };
  duration_ms?: number;
  image_url?: string;
  source_track_id?: string | number;
  id?: string | number;
  spotify_track_id?: string | number;
  extra_data?: unknown;
}

export interface MirrorPayload {
  source: string;
  source_playlist_id: string;
  name: string;
  tracks: {
    track_name: string;
    /** undefined (key dropped from the JSON) when artists is an empty array — vanilla precedence. */
    artist_name: string | undefined;
    album_name: string;
    duration_ms: number;
    image_url: string | null | undefined;
    source_track_id: string | number;
    extra_data: unknown;
  }[];
  description: string;
  owner: string;
  image_url: string;
  /**
   * what the source never handed over (#1613). only a source that reports it
   * sets the key; null clears the stored note, a missing key keeps it.
   */
  source_skipped?: SourceSkipped | null;
}

/**
 * The POST /api/mirror-playlist body, tolerant field extraction verbatim.
 * The vanilla fires-and-forgets the fetch; the port keeps the payload pure so
 * the api layer owns the wire call.
 */
export function buildMirrorPayload(
  source: string,
  sourceId: string | number,
  name: string,
  tracks: MirrorSourceTrack[],
  metadata: {
    description?: string;
    owner?: string;
    image_url?: string;
    source_skipped?: SourceSkipped | null;
  } = {},
): MirrorPayload {
  // Operator precedence transcribed exactly (stats-automations.js 471-477):
  // the artist `|| ''` binds only to the NON-array branch, so empty-array
  // artists yield undefined and the key drops from the JSON body.
  const normalizedTracks = tracks.map((t) => ({
    track_name: t.track_name || t.name || '',
    artist_name:
      t.artist_name ||
      (Array.isArray(t.artists)
        ? typeof t.artists[0] === 'object'
          ? (t.artists[0] as { name?: string }).name
          : (t.artists[0] as string | undefined)
        : t.artists || ''),
    album_name:
      t.album_name || (typeof t.album === 'object' ? t.album && t.album.name : t.album) || '',
    duration_ms: t.duration_ms || 0,
    image_url:
      t.image_url ||
      (t.album && typeof t.album === 'object' && t.album.images && t.album.images[0]
        ? t.album.images[0].url
        : null),
    source_track_id: t.source_track_id || t.id || t.spotify_track_id || '',
    extra_data: t.extra_data || null,
  }));

  const payload: MirrorPayload = {
    source,
    source_playlist_id: String(sourceId),
    name,
    tracks: normalizedTracks as MirrorPayload['tracks'],
    description: metadata.description || '',
    owner: metadata.owner || '',
    image_url: metadata.image_url || '',
  };
  if (metadata.source_skipped !== undefined) payload.source_skipped = metadata.source_skipped;
  return payload;
}
