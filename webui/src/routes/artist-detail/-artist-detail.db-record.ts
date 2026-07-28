/**
 * The artist "DB Record" inspector, ported from setupArtistRecordButton /
 * openArtistRecordModal and the _arec* helpers (library.js:9171-9300).
 *
 * The helpers stay in library.js as well: the watchlist/library Export modal
 * reuses _arecEsc / _arecCopy / _jsonSyntaxHighlight, so the vanilla copies are
 * still load-bearing for a page this migration has not reached.
 */

export interface ArtistRecordPayload {
  success?: boolean;
  error?: string;
  artist_id?: string | number;
  counts?: { albums?: number; tracks?: number };
  record?: Record<string, unknown>;
}

export interface RecordRow {
  key: string;
  /** Empty values render as a dimmed literal "null", whatever their real type. */
  isEmpty: boolean;
  /** Objects are shown as compact JSON; everything else as its string form. */
  isJson: boolean;
  /** What the row displays, and what its copy button puts on the clipboard. */
  text: string;
  copyValue: string;
  /** Lowercased "key value" haystack the filter matches against. */
  filterKey: string;
}

export function recordRows(record: Record<string, unknown>): RecordRow[] {
  return Object.entries(record).map(([key, value]) => {
    const isEmpty = value === null || value === undefined || value === '';
    if (isEmpty) {
      // Copying an empty row yields '' — not the string "null", which would be
      // a lie about what the column holds.
      return {
        key,
        isEmpty,
        isJson: false,
        text: 'null',
        copyValue: '',
        filterKey: filterKeyFor(key, ''),
      };
    }
    if (typeof value === 'object') {
      const json = JSON.stringify(value);
      return {
        key,
        isEmpty,
        isJson: true,
        text: json,
        copyValue: json,
        filterKey: filterKeyFor(key, json),
      };
    }
    const text = String(value);
    return {
      key,
      isEmpty,
      isJson: false,
      text,
      copyValue: text,
      filterKey: filterKeyFor(key, text),
    };
  });
}

function filterKeyFor(key: string, value: string): string {
  return `${key.toLowerCase()} ${value.toLowerCase()}`;
}

/** The filter searches field NAMES and values alike, so "spotify" finds both. */
export function matchesRecordFilter(row: RecordRow, query: string): boolean {
  const q = (query || '').trim().toLowerCase();
  return !q || row.filterKey.includes(q);
}

export interface RecordFooterStats {
  fields: number;
  albums: string;
  tracks: string;
  matched: number;
  id: string;
}

/**
 * The footer stat line.
 *
 * "sources matched" counts fields whose NAME ends in match_status and whose
 * value is exactly 'matched' — a per-source enrichment tally, not a count of
 * non-empty ids.
 */
export function recordFooterStats(payload: ArtistRecordPayload): RecordFooterStats {
  const record = payload.record ?? {};
  const counts = payload.counts ?? {};
  return {
    fields: Object.keys(record).length,
    // An en dash, not 0: a missing count is unknown, not zero albums.
    albums: counts.albums != null ? String(counts.albums) : '–',
    tracks: counts.tracks != null ? String(counts.tracks) : '–',
    matched: Object.entries(record).filter(
      ([key, value]) => /match_status$/.test(key) && value === 'matched',
    ).length,
    id: String(payload.artist_id),
  };
}

export interface JsonToken {
  text: string;
  /** null for the structural whitespace and punctuation between tokens. */
  className: string | null;
}

const JSON_TOKEN =
  /("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false)\b|\bnull\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?)/g;

/**
 * Split pretty-printed JSON into highlight tokens.
 *
 * The vanilla built an HTML string; this returns segments so React can render
 * spans without dangerouslySetInnerHTML. The regex and the class names are
 * verbatim — a key is a string token that is FOLLOWED by a colon, which is why
 * the colon is captured as part of the match.
 */
export function jsonHighlightTokens(value: unknown): JsonToken[] {
  const json = JSON.stringify(value, null, 2) ?? '';
  const tokens: JsonToken[] = [];
  let lastIndex = 0;

  for (const match of json.matchAll(JSON_TOKEN)) {
    const text = match[0];
    const index = match.index ?? 0;
    if (index > lastIndex) tokens.push({ text: json.slice(lastIndex, index), className: null });

    let className = 'tok-num';
    if (/^"/.test(text)) className = /:$/.test(text) ? 'tok-key' : 'tok-str';
    else if (/true|false/.test(text)) className = 'tok-bool';
    else if (/null/.test(text)) className = 'tok-null';

    tokens.push({ text, className });
    lastIndex = index + text.length;
  }

  if (lastIndex < json.length) tokens.push({ text: json.slice(lastIndex), className: null });
  return tokens;
}

/** Filesystem-safe download name, capped at 60 characters. */
export function recordFileName(artistName: string | undefined): string {
  const safe = String(artistName || 'artist')
    .replace(/[^a-z0-9._-]+/gi, '_')
    .slice(0, 60);
  return `${safe || 'artist'}_db_record.json`;
}

/**
 * Copy text, falling back to a hidden textarea.
 *
 * navigator.clipboard only exists in a secure context, and SoulSync is very
 * often served over plain http on a LAN — so the fallback is the common path,
 * not an edge case.
 */
export async function copyRecordText(text: string | null | undefined): Promise<void> {
  const value = text == null ? '' : String(text);
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(value);
      return;
    } catch {
      // Permission denied or a detached document — fall through.
    }
  }
  copyViaTextarea(value);
}

function copyViaTextarea(value: string): void {
  const textarea = document.createElement('textarea');
  textarea.value = value;
  textarea.style.cssText = 'position:fixed;left:-9999px';
  document.body.appendChild(textarea);
  textarea.select();
  try {
    document.execCommand('copy');
  } catch {
    // Nothing left to try; the toast still fires, as it did in the vanilla.
  }
  document.body.removeChild(textarea);
}

/** Save the record as a .json file. */
export function downloadRecord(
  record: Record<string, unknown>,
  artistName: string | undefined,
): string {
  const fileName = recordFileName(artistName);
  const blob = new Blob([JSON.stringify(record, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = fileName;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  return fileName;
}

/** The button only exists for LIBRARY artists — there is no row to inspect otherwise. */
export function showsDbRecordButton(
  artist: { id?: unknown } | undefined,
  isSourceArtist: boolean,
): boolean {
  return Boolean(artist?.id) && !isSourceArtist;
}
