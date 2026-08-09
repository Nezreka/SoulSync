/**
 * The Import Playlist from File tab (stats-automations.js 1-456 + the markup
 * at index.html's import-file-tab-content): drop or browse a CSV/TSV/TXT/M3U
 * file, parse it entirely client-side, tune the reading (text order +
 * separator, or per-column CSV mapping), preview every row, then mirror it as
 * source 'file'.
 *
 * All parsing is the P1 pure core (-sync.import.ts, differential-tested
 * against these very vanilla bodies); this component owns only the file read,
 * the DOM, and the submit. The vanilla's module-scoped _importFileState
 * becomes component state, so the hide/show step dance
 * (upload-zone vs preview-section) is just conditional rendering.
 *
 * Declared divergences:
 * - The name pre-fill runs once per file read. The vanilla re-ran it inside
 *   _importFileRenderPreview (328-334), so flipping a column or the line
 *   format re-filled a field the user had deliberately cleared. Its
 *   "only if empty" guard is moot here: the name input does not exist before
 *   the first read, and clear() empties it, so the field is always empty
 *   when a read resolves.
 * - importFileSubmit's tail (449-455) clicked the mirrored tab button and
 *   re-loaded that list after 500ms; the page shell owns tab state now, so
 *   the host passes onImported (required, so a mount site cannot drop it).
 * - The empty-name toast + focus (427-429) is unreachable in both worlds:
 *   the button is disabled whenever the trimmed name is empty. Kept anyway.
 */

import { useCallback, useRef, useState } from 'react';

import type { ImportColumnRole, ImportTrack } from '../-sync.import';

import { postMirrorPlaylist } from '../-sync.api';
import {
  IMPORT_FILE_EXTENSIONS,
  buildMirrorPayload,
  importFileAutoMapColumns,
  importFileDetectDelimiter,
  importFileParseCsv,
  importFileParseM3u,
  importFileTypeForExtension,
  importTracksFromCsv,
  importTracksFromText,
} from '../-sync.import';

type TextOrder = 'artist-title' | 'title-artist';

/** The column-mapping dropdown options + labels (357-363). */
const MAP_OPTIONS: readonly (ImportColumnRole | 'skip')[] = [
  'skip',
  'track_name',
  'artist_name',
  'album_name',
  'duration',
];
const MAP_LABELS: Record<string, string> = {
  skip: 'Skip',
  track_name: 'Track',
  artist_name: 'Artist',
  album_name: 'Album',
  duration: 'Duration',
};
/** The mapped-role card classes (369-371) — duration gets none, as in vanilla. */
const MAP_CLASSES: Record<string, string> = {
  track_name: 'mapped-track',
  artist_name: 'mapped-artist',
  album_name: 'mapped-album',
};

interface ParsedFile {
  fileName: string;
  fileType: 'csv' | 'text' | 'm3u';
  headers: string[];
  /** string[][] for csv, string[] for text, ImportTrack[] for m3u. */
  rows: string[][] | string[] | ImportTrack[];
  m3uPlaylistName: string;
}

/** A row counts toward the import when either name field survived (309). */
function isValidTrack(t: ImportTrack): boolean {
  return Boolean(t.track_name || t.artist_name);
}

export function ImportFileTab({ onImported }: { onImported: () => void }) {
  const [parsed, setParsed] = useState<ParsedFile | null>(null);
  const [columnMap, setColumnMap] = useState<Record<number, ImportColumnRole>>({});
  const [textOrder, setTextOrder] = useState<TextOrder>('artist-title');
  const [textSeparator, setTextSeparator] = useState(' - ');
  const [playlistName, setPlaylistName] = useState('');
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  /** _importFileRead (42-58) — extension gate, then FileReader.readAsText. */
  const readFile = useCallback((file: File) => {
    const ext = (file.name.split('.').pop() ?? '').toLowerCase();
    if (!IMPORT_FILE_EXTENSIONS.includes(ext)) {
      window.showToast?.('Unsupported file type. Use CSV, TSV, TXT, M3U, or M3U8.', 'error');
      return;
    }
    // Total over IMPORT_FILE_EXTENSIONS, which the gate above already
    // enforced — the fallback only satisfies the type.
    const fileType = importFileTypeForExtension(ext) ?? 'csv';

    const reader = new FileReader();
    reader.onload = (e) => {
      // readAsText always yields a string; the union is FileReader's, not ours.
      const result = e.target?.result;
      const rawText = typeof result === 'string' ? result : '';
      // _importFileParseAndPreview (199-228): the per-type parse.
      let next: ParsedFile;
      if (fileType === 'm3u') {
        const { tracks, playlistName: m3uName } = importFileParseM3u(rawText);
        next = {
          fileName: file.name,
          fileType,
          headers: [],
          rows: tracks,
          m3uPlaylistName: m3uName || '',
        };
        setColumnMap({});
      } else if (fileType === 'text') {
        next = {
          fileName: file.name,
          fileType,
          headers: [],
          rows: rawText.split(/\r?\n/).filter((l) => l.trim()),
          m3uPlaylistName: '',
        };
        setColumnMap({});
      } else {
        const firstLine = rawText.split(/\r?\n/)[0] || '';
        const { headers, rows } = importFileParseCsv(rawText, importFileDetectDelimiter(firstLine));
        next = { fileName: file.name, fileType, headers, rows, m3uPlaylistName: '' };
        setColumnMap(importFileAutoMapColumns(headers));
      }
      setParsed(next);
      // The M3U's #PLAYLIST: directive wins the pre-fill, else the filename
      // without its extension (328-333).
      setPlaylistName(
        next.fileType === 'm3u' && next.m3uPlaylistName
          ? next.m3uPlaylistName
          : next.fileName.replace(/\.[^.]+$/, ''),
      );
    };
    reader.readAsText(file);
  }, []);

  /** _importFileBuildTracks (230-305), derived instead of stored. */
  const tracks: ImportTrack[] = !parsed
    ? []
    : parsed.fileType === 'm3u'
      ? (parsed.rows as ImportTrack[]).map((t) => ({
          track_name: t.track_name || '',
          artist_name: t.artist_name || '',
          album_name: t.album_name || '',
          duration_ms: t.duration_ms || 0,
        }))
      : parsed.fileType === 'text'
        ? importTracksFromText(parsed.rows as string[], textOrder, textSeparator)
        : importTracksFromCsv(parsed.rows as string[][], columnMap);

  const validCount = tracks.filter(isValidTrack).length;

  /** importFileClear (412-421). */
  const clear = useCallback(() => {
    setParsed(null);
    setColumnMap({});
    setPlaylistName('');
  }, []);

  /** A mapping change claims the role exclusively (387-399). */
  const remapColumn = useCallback((idx: number, value: ImportColumnRole | 'skip') => {
    setColumnMap((current) => {
      const next = { ...current };
      if (value === 'skip') {
        delete next[idx];
        return next;
      }
      for (const key of Object.keys(next)) {
        if (next[Number(key)] === value) delete next[Number(key)];
      }
      next[idx] = value;
      return next;
    });
  }, []);

  /** importFileSubmit (423-456). */
  const submit = useCallback(() => {
    const name = playlistName.trim();
    if (!name) {
      window.showToast?.('Please enter a playlist name.', 'error');
      return;
    }
    const valid = tracks.filter(isValidTrack);
    if (valid.length === 0) {
      window.showToast?.('No valid tracks to import.', 'error');
      return;
    }
    // Timestamped id so repeated imports never collide (438-439).
    const sourceId = `file_${Date.now()}`;
    void postMirrorPlaylist(
      buildMirrorPayload('file', sourceId, name, valid, {
        description: `Imported from ${parsed?.fileName ?? ''}`,
        owner: 'local',
      }),
    ).catch(() => undefined);
    window.showToast?.(`Imported "${name}" with ${valid.length} tracks`, 'success');
    clear();
    // The vanilla clicked the mirrored tab button and reloaded that list
    // (449-455); the page shell owns tab state now, so it gets a callback.
    onImported();
  }, [playlistName, tracks, parsed, clear, onImported]);

  return (
    <div>
      <div className="playlist-header">
        <h3>Import Playlist from File</h3>
      </div>

      {parsed === null ? (
        <div id="import-file-upload-zone" className="import-file-zone">
          <div
            className={dragOver ? 'import-file-zone-inner drag-over' : 'import-file-zone-inner'}
            id="import-file-dropzone"
            onClick={() => fileInputRef.current?.click()}
            onDragOver={(e) => {
              e.preventDefault();
              setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragOver(false);
              const file = e.dataTransfer.files[0];
              if (file) readFile(file);
            }}
          >
            <div className="import-file-zone-icon">📄</div>
            <div className="import-file-zone-title">Drop your file here</div>
            <div className="import-file-zone-subtitle">or click to browse</div>
            <div className="import-file-zone-formats">Supported: CSV, TSV, TXT, M3U, M3U8</div>
            <input
              type="file"
              id="import-file-input"
              ref={fileInputRef}
              accept=".csv,.tsv,.txt,.m3u,.m3u8"
              style={{ display: 'none' }}
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) readFile(file);
                // The vanilla clears the input so the same file re-triggers (29).
                e.target.value = '';
              }}
            />
          </div>
          <div className="import-file-format-hints">
            <div className="import-file-hint">
              <span className="import-file-hint-label">CSV / TSV</span>
              <span className="import-file-hint-text">
                First row as headers (e.g. Title, Artist, Album). Columns are auto-detected or can
                be mapped manually.
              </span>
            </div>
            <div className="import-file-hint">
              <span className="import-file-hint-label">TXT</span>
              <span className="import-file-hint-text">
                One track per line (e.g. Artist - Title). Format and separator can be adjusted after
                upload.
              </span>
            </div>
            <div className="import-file-hint">
              <span className="import-file-hint-label">M3U / M3U8</span>
              <span className="import-file-hint-text">
                Standard playlist files. Artist, title and duration are read automatically from
                #EXTINF lines (or the file name for simple playlists).
              </span>
            </div>
          </div>
        </div>
      ) : (
        <div id="import-file-preview-section">
          <div className="import-file-info-bar">
            <span id="import-file-name-label" className="import-file-info-filename">
              {parsed.fileName}
            </span>
            <span id="import-file-track-count" className="import-file-info-count">
              {`${validCount} track${validCount !== 1 ? 's' : ''} parsed`}
            </span>
            <button
              type="button"
              className="import-file-clear-btn"
              title="Clear and start over"
              onClick={clear}
            >
              ✕
            </button>
          </div>

          {parsed.fileType === 'text' && (
            <div id="import-file-text-format" className="import-file-format-bar">
              <span className="import-file-format-label">Line format:</span>
              <select
                id="import-file-text-order"
                className="import-file-select"
                value={textOrder}
                onChange={(e) => setTextOrder(e.target.value as TextOrder)}
              >
                <option value="artist-title">Artist - Title</option>
                <option value="title-artist">Title - Artist</option>
              </select>
              <span className="import-file-format-label" style={{ marginLeft: '12px' }}>
                Separator:
              </span>
              <select
                id="import-file-text-separator"
                className="import-file-select"
                value={textSeparator}
                onChange={(e) => setTextSeparator(e.target.value)}
              >
                <option value=" - "> - </option>
                <option value=" — "> — </option>
                <option value="|">|</option>
                <option value="/"> / </option>
              </select>
            </div>
          )}

          {parsed.fileType === 'csv' && (
            <div id="import-file-column-mapping" className="import-file-mapping-bar">
              <span className="import-file-format-label">Column mapping:</span>
              <div id="import-file-mapping-selects" className="import-file-mapping-selects">
                {parsed.headers.map((header, idx) => {
                  const mapped = columnMap[idx] ?? 'skip';
                  return (
                    <div
                      key={`${header}-${idx}`}
                      className={`import-file-col-map ${MAP_CLASSES[mapped] ?? ''}`}
                    >
                      <span className="import-file-col-label" title={header}>
                        {header}
                      </span>
                      <select
                        className="import-file-select"
                        value={mapped}
                        onChange={(e) =>
                          remapColumn(idx, e.target.value as ImportColumnRole | 'skip')
                        }
                      >
                        {MAP_OPTIONS.map((o) => (
                          <option key={o} value={o}>
                            {MAP_LABELS[o]}
                          </option>
                        ))}
                      </select>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          <div className="import-file-preview-table-wrap">
            <table className="import-file-preview-table" id="import-file-preview-table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Track</th>
                  <th>Artist</th>
                  <th>Album</th>
                </tr>
              </thead>
              <tbody id="import-file-preview-tbody">
                {tracks.map((t, i) => (
                  // eslint-disable-next-line react/no-array-index-key
                  <tr key={i} className={isValidTrack(t) ? '' : 'invalid-row'}>
                    <td>{i + 1}</td>
                    <td>{t.track_name}</td>
                    <td>{t.artist_name}</td>
                    <td>{t.album_name}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="import-file-action-bar">
            <input
              type="text"
              id="import-file-playlist-name"
              className="import-file-name-input"
              placeholder="Enter playlist name..."
              maxLength={200}
              value={playlistName}
              onChange={(e) => setPlaylistName(e.target.value)}
            />
            <button
              type="button"
              className="import-file-import-btn"
              id="import-file-import-btn"
              disabled={!playlistName.trim()}
              onClick={submit}
            >
              Import as Mirrored Playlist
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
