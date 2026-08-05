/**
 * Import-file tab parser suite + the mirror contract normalizer — ported 1:1
 * from stats-automations.js 13-495 (the file is misnamed; its first third is
 * sync-page content). All pure: the vanilla's DOM reads (format selects,
 * column-map selects) become parameters here.
 */

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

/**
 * M3U/M3U8 parser (stats-automations.js 113-165). Handles simple and extended
 * forms; a pending #EXTINF is flushed even with no path line after it, because
 * SoulSync's own export replaces un-located paths with "# MISSING:" comments —
 * exactly the tracks a user imports to go match/download.
 */
export function importFileParseM3u(text: string | null | undefined): {
  tracks: ImportTrack[];
  playlistName: string;
} {
  const lines = (text || '').split(/\r?\n/);
  const tracks: ImportTrack[] = [];
  let playlistName = '';
  let pending: { duration_ms: number; artist: string; title: string } | null = null;

  function splitArtistTitle(s: string): { artist: string; title: string } {
    const i = s.indexOf(' - ');
    return i !== -1
      ? { artist: s.slice(0, i).trim(), title: s.slice(i + 3).trim() }
      : { artist: '', title: s.trim() };
  }
  function pushTrack(artist: string, title: string, duration_ms: number): void {
    if (!title && !artist) return;
    tracks.push({
      track_name: title || '',
      artist_name: artist || '',
      album_name: '',
      duration_ms: duration_ms || 0,
    });
  }
  function flushPending(): void {
    if (pending) {
      pushTrack(pending.artist, pending.title, pending.duration_ms);
      pending = null;
    }
  }

  for (const raw of lines) {
    const line = raw.trim();
    if (!line) continue;
    if (line.charAt(0) === '#') {
      if (/^#EXTINF:/i.test(line)) {
        flushPending();
        const rest = line.slice(8);
        const comma = rest.indexOf(',');
        const secs = parseFloat(comma === -1 ? rest : rest.slice(0, comma));
        const meta = comma === -1 ? '' : rest.slice(comma + 1).trim();
        const { artist, title } = splitArtistTitle(meta);
        pending = {
          duration_ms: !isNaN(secs) && secs > 0 ? Math.round(secs * 1000) : 0,
          artist,
          title,
        };
      } else if (/^#PLAYLIST:/i.test(line)) {
        playlistName = line.slice(line.indexOf(':') + 1).trim();
      }
      continue;
    }
    if (pending && (pending.title || pending.artist)) {
      pushTrack(pending.artist, pending.title, pending.duration_ms);
      pending = null;
    } else {
      pending = null;
      const base = (line.split(/[\\/]/).pop() || line).replace(/\.[^.]+$/, '');
      const { artist, title } = splitArtistTitle(base);
      pushTrack(artist, title, 0);
    }
  }
  flushPending();
  return { tracks, playlistName };
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
    artist_name: string;
    album_name: string;
    duration_ms: number;
    image_url: string | null;
    source_track_id: string | number;
    extra_data: unknown;
  }[];
  description: string;
  owner: string;
  image_url: string;
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
  metadata: { description?: string; owner?: string; image_url?: string } = {},
): MirrorPayload {
  const normalizedTracks = tracks.map((t) => ({
    track_name: t.track_name || t.name || '',
    artist_name:
      t.artist_name ||
      (Array.isArray(t.artists)
        ? typeof t.artists[0] === 'object'
          ? (t.artists[0] as { name?: string }).name
          : t.artists[0]
        : t.artists) ||
      '',
    album_name:
      t.album_name || (typeof t.album === 'object' ? t.album && t.album.name : t.album) || '',
    duration_ms: t.duration_ms || 0,
    image_url:
      t.image_url ||
      (t.album && typeof t.album === 'object' && t.album.images && t.album.images[0]
        ? (t.album.images[0].url ?? null)
        : null),
    source_track_id: t.source_track_id || t.id || t.spotify_track_id || '',
    extra_data: t.extra_data || null,
  }));

  return {
    source,
    source_playlist_id: String(sourceId),
    name,
    tracks: normalizedTracks as MirrorPayload['tracks'],
    description: metadata.description || '',
    owner: metadata.owner || '',
    image_url: metadata.image_url || '',
  };
}
