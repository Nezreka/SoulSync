/**
 * Spotify streaming history from the privacy export, read in the browser.
 *
 * The export (a zip, or the json files out of it) never leaves the device.
 * Only music plays of 30 seconds or more are sent, as plain rows; podcasts,
 * audiobooks, videos and plays made in a private session are dropped here.
 *
 * Two export shapes:
 * - Account data: StreamingHistory_music_*.json (older: StreamingHistory*.json)
 *   rows of { endTime, artistName, trackName, msPlayed }, minute precision.
 * - Extended streaming history: Streaming_History_Audio_*.json or
 *   endsong_*.json rows of { ts, ms_played, master_metadata_track_name,
 *   master_metadata_album_artist_name, master_metadata_album_album_name,
 *   spotify_track_uri, incognito_mode, episode_name, audiobook_title, ... }.
 */

export const MIN_PLAY_MS = 30_000;
export const BATCH_SIZE = 2_000;

/** One play as the server takes it. */
export type SpotifyPlayRow = {
  ended_at: string;
  ms_played: number;
  track: string;
  artist: string;
  album?: string;
  uri?: string;
  /** account data only knows the minute */
  coarse?: boolean;
};

export type ParseTally = {
  files: number;
  kept: number;
  short: number;
  podcast: number;
  private: number;
  unreadable: number;
  /** account-data plays the extended history in the same export already has */
  overlap: number;
};

export function emptyTally(): ParseTally {
  return { files: 0, kept: 0, short: 0, podcast: 0, private: 0, unreadable: 0, overlap: 0 };
}

type JsonRecord = Record<string, unknown>;

function text(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function number(value: unknown): number {
  const n = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(n) ? n : 0;
}

function present(value: unknown): boolean {
  return value !== null && value !== undefined && value !== '';
}

/** One export record as a row to send, or the reason it stays here. */
export function parseRecord(
  record: unknown,
): SpotifyPlayRow | 'short' | 'podcast' | 'private' | 'unreadable' {
  if (!record || typeof record !== 'object' || Array.isArray(record)) return 'unreadable';
  const r = record as JsonRecord;

  if ('ts' in r && ('ms_played' in r || 'master_metadata_track_name' in r)) {
    if (r.incognito_mode === true) return 'private';
    if (
      present(r.episode_name) ||
      present(r.spotify_episode_uri) ||
      present(r.audiobook_title) ||
      present(r.audiobook_uri) ||
      present(r.audiobook_chapter_uri)
    )
      return 'podcast';
    const track = text(r.master_metadata_track_name);
    const artist = text(r.master_metadata_album_artist_name);
    // no track name and no episode: a video or a local file Spotify kept no name for
    if (!track || !artist) return 'podcast';
    const ms = number(r.ms_played);
    if (ms < MIN_PLAY_MS) return 'short';
    const ts = text(r.ts);
    if (!ts) return 'unreadable';
    const uri = text(r.spotify_track_uri);
    return {
      ended_at: ts,
      ms_played: Math.round(ms),
      track,
      artist,
      album: text(r.master_metadata_album_album_name),
      ...(uri ? { uri } : {}),
    };
  }

  if ('endTime' in r && 'msPlayed' in r) {
    // StreamingHistory_podcast_*.json
    if (present(r.podcastName) || present(r.episodeName)) return 'podcast';
    const track = text(r.trackName);
    const artist = text(r.artistName);
    if (!track || !artist) return 'unreadable';
    const ms = number(r.msPlayed);
    if (ms < MIN_PLAY_MS) return 'short';
    const end = text(r.endTime);
    if (!end) return 'unreadable';
    return { ended_at: end, ms_played: Math.round(ms), track, artist, coarse: true };
  }

  return 'unreadable';
}

/** Every play worth sending out of one parsed json file. */
export function parseHistoryJson(data: unknown, tally: ParseTally): SpotifyPlayRow[] {
  const rows: SpotifyPlayRow[] = [];
  if (!Array.isArray(data)) {
    tally.unreadable += 1;
    return rows;
  }
  for (const record of data) {
    const parsed = parseRecord(record);
    if (typeof parsed === 'string') {
      tally[parsed] += 1;
    } else {
      rows.push(parsed);
      tally.kept += 1;
    }
  }
  return rows;
}

/** The files in an export that hold music history. Video and podcast files stay out. */
export function isHistoryFile(name: string): boolean {
  const base = name.split('/').pop() ?? name;
  if (!/\.json$/i.test(base)) return false;
  if (/video|podcast/i.test(base)) return false;
  return /^(StreamingHistory|Streaming_History_Audio|endsong)/i.test(base);
}

// ── zip ─────────────────────────────────────────────────────────────────────
// a small reader for the export's zip: the central directory, then each
// wanted entry, stored or deflated (inflated by the browser's own
// DecompressionStream). no zip64, no encryption: spotify's exports use neither.

const EOCD = 0x06054b50;
const CENTRAL = 0x02014b50;
const LOCAL = 0x04034b50;

export function isZip(bytes: Uint8Array): boolean {
  return bytes.length >= 4 && bytes[0] === 0x50 && bytes[1] === 0x4b && bytes[2] === 3;
}

async function inflateRaw(data: Uint8Array): Promise<Uint8Array> {
  const inflate = new DecompressionStream('deflate-raw');
  const writer = inflate.writable.getWriter();
  // not awaited: the write only settles as the response below reads it
  void writer.write(data as Uint8Array<ArrayBuffer>).catch(() => undefined);
  void writer.close().catch(() => undefined);
  return new Uint8Array(await new Response(inflate.readable).arrayBuffer());
}

/** The text of each entry `want` picks, by name. */
export async function readZipEntries(
  buffer: ArrayBuffer,
  want: (name: string) => boolean,
): Promise<{ name: string; text: string }[]> {
  const bytes = new Uint8Array(buffer);
  const view = new DataView(buffer);
  let end = -1;
  for (let i = bytes.length - 22; i >= Math.max(0, bytes.length - 65_557); i -= 1) {
    if (view.getUint32(i, true) === EOCD) {
      end = i;
      break;
    }
  }
  if (end < 0) throw new Error('This is not a zip file Spotify made.');
  const count = view.getUint16(end + 10, true);
  let offset = view.getUint32(end + 16, true);
  if (offset === 0xffffffff) throw new Error('This zip is too large to read here.');
  const decoder = new TextDecoder('utf-8');
  const out: { name: string; text: string }[] = [];
  for (let n = 0; n < count; n += 1) {
    if (offset + 46 > bytes.length || view.getUint32(offset, true) !== CENTRAL) {
      throw new Error('This zip looks damaged.');
    }
    const flags = view.getUint16(offset + 8, true);
    const method = view.getUint16(offset + 10, true);
    const size = view.getUint32(offset + 20, true);
    const nameLength = view.getUint16(offset + 28, true);
    const extraLength = view.getUint16(offset + 30, true);
    const commentLength = view.getUint16(offset + 32, true);
    const local = view.getUint32(offset + 42, true);
    const name = decoder.decode(bytes.subarray(offset + 46, offset + 46 + nameLength));
    offset += 46 + nameLength + extraLength + commentLength;
    if (!want(name) || flags & 1) continue;
    if (view.getUint32(local, true) !== LOCAL) throw new Error('This zip looks damaged.');
    const start = local + 30 + view.getUint16(local + 26, true) + view.getUint16(local + 28, true);
    const data = bytes.subarray(start, start + size);
    let raw: Uint8Array;
    if (method === 0) raw = data;
    else if (method === 8) raw = await inflateRaw(data);
    else continue;
    out.push({ name, text: decoder.decode(raw) });
  }
  return out;
}

/** Every music play in the files the user picked (a zip, json files, or both). */
export async function readSpotifyExport(
  files: Iterable<File>,
): Promise<{ rows: SpotifyPlayRow[]; tally: ParseTally }> {
  const tally = emptyTally();
  const rows: SpotifyPlayRow[] = [];
  const addJson = (raw: string) => {
    let data: unknown;
    try {
      data = JSON.parse(raw);
    } catch {
      tally.unreadable += 1;
      return;
    }
    tally.files += 1;
    for (const row of parseHistoryJson(data, tally)) rows.push(row);
  };
  for (const file of files) {
    const buffer = await file.arrayBuffer();
    if (isZip(new Uint8Array(buffer, 0, Math.min(4, buffer.byteLength)))) {
      for (const entry of await readZipEntries(buffer, isHistoryFile)) addJson(entry.text);
    } else if (/podcast|video/i.test(file.name)) {
      continue;
    } else {
      addJson(new TextDecoder('utf-8').decode(buffer));
    }
  }
  return { rows: inTimeOrder(withoutOverlap(rows, tally)), tally };
}

/** a play's end as epoch ms: iso with a Z, or account data's "YYYY-MM-DD HH:MM" (utc) */
export function endedMs(row: SpotifyPlayRow): number {
  const value = row.coarse ? `${row.ended_at.replace(' ', 'T')}:00Z` : row.ended_at;
  const ms = Date.parse(value);
  return Number.isFinite(ms) ? ms : 0;
}

/**
 * The account data and the extended history cover the same plays for the
 * last year. When an export holds both, the extended rows win (they know the
 * second and the track), and account rows inside their span stay out.
 */
export function withoutOverlap(rows: SpotifyPlayRow[], tally: ParseTally): SpotifyPlayRow[] {
  let first = Infinity;
  let last = -Infinity;
  for (const row of rows) {
    if (row.coarse) continue;
    const ms = endedMs(row);
    if (ms < first) first = ms;
    if (ms > last) last = ms;
  }
  if (first === Infinity) return rows;
  // an account row knows only the minute, so its end can sit a little either
  // side of the extended play it repeats
  const from = first - 60_000;
  const until = last + 120_000;
  return rows.filter((row) => {
    if (!row.coarse) return true;
    const ms = endedMs(row);
    if (ms >= from && ms <= until) {
      tally.overlap += 1;
      tally.kept -= 1;
      return false;
    }
    return true;
  });
}

/** oldest first, so each batch covers a short stretch of time */
function inTimeOrder(rows: SpotifyPlayRow[]): SpotifyPlayRow[] {
  return rows
    .map((row) => ({ row, ms: endedMs(row) }))
    .sort((a, b) => a.ms - b.ms)
    .map((entry) => entry.row);
}

export function batches<T>(rows: readonly T[], size = BATCH_SIZE): T[][] {
  const out: T[][] = [];
  for (let i = 0; i < rows.length; i += size) out.push(rows.slice(i, i + size));
  return out;
}

export type SpotifyHistorySummary = {
  success?: boolean;
  imported: boolean;
  include_in_stats: boolean;
  plays: number;
  first_played: string | null;
  last_played: string | null;
  artists: number;
  matched: number;
  linked: number;
  updated_at?: string | null;
  max_batch?: number;
};

function count(n: number, one: string, many: string): string {
  return `${n.toLocaleString('en-US')} ${n === 1 ? one : many}`;
}

/** "12,340 plays from 2015 to 2024, 3,100 artists, 9,800 matched to your library" */
export function summaryLine(s: SpotifyHistorySummary): string {
  const first = s.first_played?.slice(0, 4);
  const last = s.last_played?.slice(0, 4);
  const span = first && last ? (first === last ? ` in ${first}` : ` from ${first} to ${last}`) : '';
  return (
    `${count(s.plays, 'play', 'plays')}${span}, ${count(s.artists, 'artist', 'artists')}, ` +
    `${s.matched.toLocaleString('en-US')} matched to your library`
  );
}

/** What stayed on the device, in words, or '' when nothing did. */
export function leftOutLine(t: ParseTally): string {
  const parts: string[] = [];
  if (t.short) parts.push(`${count(t.short, 'play', 'plays')} under 30 seconds`);
  if (t.podcast) parts.push(`${count(t.podcast, 'podcast or video', 'podcasts and videos')}`);
  if (t.private) parts.push(`${count(t.private, 'private session play', 'private session plays')}`);
  return parts.length ? `Left out: ${parts.join(', ')}.` : '';
}
