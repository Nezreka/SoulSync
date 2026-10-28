/**
 * The NewPipe branch of the import tab: read the backup, tick playlists,
 * mirror them. Re-importing a newer backup updates the same mirrors.
 */

import { useEffect, useState } from 'react';

import type { NewPipePlaylist } from '../-sync.newpipe';

import { postMirrorPlaylist } from '../-sync.api';
import { newpipeImportable, newpipeMirrorPayload, parseNewPipeBackup } from '../-sync.newpipe';

type Phase =
  | { kind: 'reading' }
  | { kind: 'error'; message: string }
  | { kind: 'ready'; playlists: NewPipePlaylist[]; bookmarked: number };

export function NewPipeImport({
  file,
  onClear,
  onImported,
}: {
  file: File;
  onClear: () => void;
  onImported: () => void;
}) {
  const [phase, setPhase] = useState<Phase>({ kind: 'reading' });
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [importing, setImporting] = useState(false);

  useEffect(() => {
    let live = true;
    parseNewPipeBackup(file)
      .then((res) => {
        if (!live) return;
        if (!res.success) {
          setPhase({ kind: 'error', message: res.error || 'Could not read that backup.' });
          return;
        }
        const playlists = res.playlists ?? [];
        setPhase({ kind: 'ready', playlists, bookmarked: res.bookmarked ?? 0 });
        setPicked(new Set(playlists.filter(newpipeImportable).map((p) => p.id)));
      })
      .catch((error: unknown) => {
        if (live)
          setPhase({
            kind: 'error',
            message: error instanceof Error ? error.message : 'Could not read that backup.',
          });
      });
    return () => {
      live = false;
    };
  }, [file]);

  const toggle = (id: string) =>
    setPicked((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const importPicked = async () => {
    if (phase.kind !== 'ready') return;
    const chosen = phase.playlists.filter((p) => picked.has(p.id));
    if (chosen.length === 0) return;
    setImporting(true);
    const failed: string[] = [];
    // one at a time: each is a full mirror write, and a failure names its playlist
    for (const playlist of chosen) {
      try {
        const res = await postMirrorPlaylist(newpipeMirrorPayload(playlist));
        if (res.success === false || res.error) failed.push(playlist.name);
      } catch {
        failed.push(playlist.name);
      }
    }
    setImporting(false);
    const done = chosen.length - failed.length;
    if (done > 0) {
      window.showToast?.(
        `Imported ${done} NewPipe playlist${done !== 1 ? 's' : ''}`,
        failed.length ? 'warning' : 'success',
      );
    }
    if (failed.length) {
      window.showToast?.(`Couldn't import: ${failed.join(', ')}`, 'error');
      return;
    }
    onClear();
    onImported();
  };

  const playlists = phase.kind === 'ready' ? phase.playlists : [];
  const count = playlists.length;

  return (
    <div id="newpipe-import-section">
      <div className="import-file-info-bar">
        <span className="import-file-info-filename">{file.name}</span>
        <span className="import-file-info-count">
          {phase.kind === 'ready'
            ? `NewPipe backup · ${count} playlist${count !== 1 ? 's' : ''}`
            : phase.kind === 'reading'
              ? 'Reading NewPipe backup...'
              : ''}
        </span>
        <button
          type="button"
          className="import-file-clear-btn"
          title="Clear and start over"
          onClick={onClear}
        >
          ✕
        </button>
      </div>

      {phase.kind === 'error' && <div className="playlist-placeholder">{phase.message}</div>}

      {phase.kind === 'ready' && count === 0 && (
        <div className="playlist-placeholder">This backup has no local playlists.</div>
      )}

      {phase.kind === 'ready' && count > 0 && (
        <>
          <div className="import-file-preview-table-wrap">
            <table className="import-file-preview-table" id="newpipe-import-table">
              <thead>
                <tr>
                  <th aria-label="Import" />
                  <th>Playlist</th>
                  <th>Tracks</th>
                </tr>
              </thead>
              <tbody>
                {playlists.map((p) => {
                  const importable = newpipeImportable(p);
                  return (
                    <tr key={p.id} className={importable ? '' : 'invalid-row'}>
                      <td>
                        <input
                          type="checkbox"
                          aria-label={`Import ${p.name}`}
                          checked={picked.has(p.id)}
                          disabled={!importable || importing}
                          onChange={() => toggle(p.id)}
                        />
                      </td>
                      <td>{p.name}</td>
                      <td>{importable ? p.track_count : 'empty'}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="newpipe-import-note">
            Playlists you imported from NewPipe before are updated in place.
            {phase.bookmarked > 0 &&
              ` ${phase.bookmarked} bookmarked YouTube playlist${
                phase.bookmarked !== 1 ? 's are' : ' is'
              } only a link in the backup; add ${
                phase.bookmarked !== 1 ? 'those' : 'it'
              } from the YouTube tab.`}
          </p>
          <div className="import-file-action-bar">
            <button
              type="button"
              className="import-file-import-btn"
              id="newpipe-import-btn"
              disabled={picked.size === 0 || importing}
              onClick={() => void importPicked()}
            >
              {importing
                ? 'Importing...'
                : `Import ${picked.size} playlist${picked.size !== 1 ? 's' : ''}`}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
