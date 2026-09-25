import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';

import {
  fetchLibraryV2JobStatus,
  fetchLibraryV2TagPreview,
  LIBRARY_V2_QUERY_KEY,
  writeLibraryV2Tags,
  type LibraryV2TagPreviewTrack,
} from '../-library-v2.api';
import styles from './library-v2-page.module.css';
import { LibraryToolDialog } from './tool-dialog';

function fieldValue(v: unknown): string {
  if (v === null || v === undefined || v === '') return '—';
  if (Array.isArray(v)) return v.join(', ');
  if (typeof v === 'string') return v;
  if (typeof v === 'number' || typeof v === 'boolean' || typeof v === 'bigint') return String(v);
  if (typeof v === 'object') return JSON.stringify(v);
  return '—';
}

function TagChanges({ track }: { track: LibraryV2TagPreviewTrack }) {
  return (
    <div className={styles.tagChanges}>
      {track.diff
        .filter((d) => !d.manual)
        .map((d) => (
          <div className={styles.tagChange} key={d.field}>
            <span className={styles.tagField}>{d.field}</span>
            <span className={styles.tagBefore}>{fieldValue(d.file_value)}</span>
            <span className={styles.tagArrow} aria-label="changes to">
              →
            </span>
            <span className={styles.tagAfter}>{fieldValue(d.db_value)}</span>
          </div>
        ))}
    </div>
  );
}

/** One key per released field. A blanket "overwrite everything" flag would let
 *  settling a track title hand the album title over with it. */
function releaseKey(trackId: number, field: string): string {
  return `${trackId}:${field}`;
}

/**
 * A field the user set by hand, shown as the choice it is.
 *
 * lib2 keeps a per-field override layer and every read path projects it, so a
 * corrected title IS the library's title and a re-tag keeps it. That is the
 * right default and the wrong thing to decide silently — someone who fixed a
 * title months ago and has since fixed the catalogue needs a way to say the
 * catalogue wins now. Both values are on screen; neither is preselected away.
 */
function ManualFieldChoice({
  row,
  released,
  onToggle,
  disabled,
}: {
  row: LibraryV2TagPreviewTrack['diff'][number];
  released: boolean;
  onToggle: () => void;
  disabled: boolean;
}) {
  const provider = fieldValue(row.provider_value);
  return (
    <div className={styles.retagManualRow}>
      <span className={styles.retagManualField}>
        {row.field}
        <small className={styles.retagManualNote}>set by hand</small>
      </span>
      <span className={styles.retagManualFile}>
        <span className={styles.retagChoiceLabel}>Current file</span>
        <strong>{fieldValue(row.file_value)}</strong>
      </span>
      <span className={styles.tagArrow} aria-hidden="true">
        →
      </span>
      <button
        type="button"
        className={released ? styles.retagChoice : styles.retagChoiceActive}
        disabled={disabled}
        aria-pressed={!released}
        aria-label={`Keep mine (${fieldValue(row.db_value)})`}
        onClick={() => released && onToggle()}
      >
        <span className={styles.retagChoiceLabel}>
          <span aria-hidden="true">{!released ? '●' : '○'}</span> Your manual edit · Keep mine
        </span>
        <strong>{fieldValue(row.db_value)}</strong>
      </button>
      <button
        type="button"
        className={released ? styles.retagChoiceActive : styles.retagChoice}
        disabled={disabled}
        aria-pressed={released}
        aria-label={`Use "${provider}" from discovery / provider`}
        onClick={() => !released && onToggle()}
      >
        <span className={styles.retagChoiceLabel}>
          <span aria-hidden="true">{released ? '●' : '○'}</span> Discovery / provider
        </span>
        <strong>{provider}</strong>
      </button>
    </div>
  );
}

function releaseTypeLabel(albumType: string | null | undefined): string {
  const normalized = albumType?.trim().toLowerCase();
  if (normalized === 'ep') return 'EP';
  if (normalized === 'single') return 'Single';
  if (normalized === 'compilation') return 'Compilation';
  if (normalized === 'live') return 'Live';
  return 'Album';
}

/** Lidarr-style "Preview Retag": show, per track, exactly which tag fields
 *  would change (file value → library value), let the user deselect tracks,
 *  then write. Unchanged tracks are listed but not selectable. */
export function RetagModal({
  entity,
  id,
  title,
  onClose,
}: {
  entity: 'artists' | 'albums';
  id: number;
  title: string;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const previewQuery = useQuery({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'tag-preview', entity, id],
    queryFn: () => fetchLibraryV2TagPreview(entity, id),
    staleTime: 0,
  });
  const tracks = useMemo(() => {
    const raw = previewQuery.data?.tracks ?? [];
    return raw.filter(
      (t) => t.file_path && t.error !== 'No file' && t.error !== 'File not found on disk',
    );
  }, [previewQuery.data]);
  const changed = useMemo(() => tracks.filter((t) => t.has_changes), [tracks]);
  const manualCount = tracks.reduce(
    (count, track) => count + track.diff.filter((field) => field.manual && field.manual_key).length,
    0,
  );

  const [showUnchanged, setShowUnchanged] = useState(false);
  const visibleTracks = useMemo(
    () => tracks.filter((t) => showUnchanged || t.has_changes || t.error),
    [tracks, showUnchanged],
  );
  const grouped = useMemo(() => {
    const byAlbum = new Map<
      number,
      {
        albumId: number;
        albumTitle: string;
        albumType: string | null;
        tracks: LibraryV2TagPreviewTrack[];
      }
    >();
    for (const t of visibleTracks) {
      let group = byAlbum.get(t.album_id);
      if (!group) {
        group = {
          albumId: t.album_id,
          albumTitle: t.album_title ?? 'Unknown Album',
          albumType: t.album_type,
          tracks: [],
        };
        byAlbum.set(t.album_id, group);
      }
      group.tracks.push(t);
    }
    return [...byAlbum.values()];
  }, [visibleTracks]);

  const [selected, setSelected] = useState<Set<number>>(new Set());
  // Hand-set fields the user handed back to the catalogue, as `trackId:key`.
  // Empty is the default and the safe answer: keep every one of them.
  const [released, setReleased] = useState<Set<string>>(new Set());
  const [phase, setPhase] = useState<'idle' | 'writing' | 'done' | 'error'>('idle');
  const [message, setMessage] = useState<string | null>(null);

  // Preselect every track that has changes once the preview lands.
  useEffect(() => {
    setSelected(new Set(changed.map((t) => t.track_id)));
  }, [changed]);

  function toggleRelease(trackId: number, field: string) {
    setReleased((s) => {
      const next = new Set(s);
      const key = releaseKey(trackId, field);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  function toggle(trackId: number) {
    setSelected((s) => {
      const next = new Set(s);
      if (next.has(trackId)) next.delete(trackId);
      else next.add(trackId);
      return next;
    });
  }

  async function write() {
    const ids = [...selected];
    if (ids.length === 0) return;
    setPhase('writing');
    setMessage(`Writing tags to ${ids.length} file(s)…`);
    try {
      const chosen = new Set(ids);
      const overwrite: [number, string][] = [];
      for (const key of released) {
        const [rawId, field] = key.split(':');
        const trackId = Number(rawId);
        // Only for tracks that are actually being written — a release on a
        // track the user then deselected is not a request to write it.
        if (field && chosen.has(trackId)) overwrite.push([trackId, field]);
      }
      const jobId = await writeLibraryV2Tags(ids, true, overwrite);
      // Poll this write only; other background jobs have independent ids.
      for (let i = 0; i < 600; i += 1) {
        const state = await fetchLibraryV2JobStatus(jobId);
        if (!state.running) {
          if (state.error) throw new Error(state.error);
          const r = state.result ?? {};
          setPhase('done');
          setMessage(
            `Done: ${r.written ?? 0} written, ${r.skipped ?? 0} already correct, ${r.failed ?? 0} failed.`,
          );
          await queryClient.invalidateQueries({ queryKey: LIBRARY_V2_QUERY_KEY });
          return;
        }
        setMessage(`Writing tags… ${state.current}/${state.total}`);
        await new Promise((res) => setTimeout(res, 1000));
      }
      throw new Error('Timed out waiting for the tag writer');
    } catch (e) {
      setPhase('error');
      setMessage(e instanceof Error ? e.message : 'Write failed');
    }
  }

  return (
    <LibraryToolDialog
      title={`Preview Retag — ${title}`}
      onClose={onClose}
      description="File tags → library values. Manual edits are kept unless you explicitly choose the discovery / provider value."
      footer={
        <div className={styles.modalActions}>
          {previewQuery.data?.truncated ? (
            <span className={styles.modalActionsText}>Showing the first 500 tracks.</span>
          ) : null}
          <button type="button" className={styles.btnGhost} onClick={onClose}>
            {phase === 'done' ? 'Close' : 'Cancel'}
          </button>
          <button
            type="button"
            className={styles.btnPrimary}
            disabled={selected.size === 0 || phase === 'writing' || phase === 'done'}
            onClick={() => void write()}
          >
            {phase === 'writing' ? 'Writing…' : `Write tags (${selected.size})`}
          </button>
        </div>
      }
    >
      <div className={styles.previewToolbar}>
        <span>
          <strong>{changed.length}</strong> files with changes{' '}
          <span className={styles.previewQuiet}>of {tracks.length} files</span>
        </span>
        <div className={styles.previewControls}>
          {manualCount > 0 ? (
            <button
              type="button"
              className={styles.btnGhost}
              disabled={phase === 'writing' || phase === 'done'}
              aria-pressed={released.size === 0}
              onClick={() => setReleased(new Set())}
            >
              Keep mine for all ({manualCount})
            </button>
          ) : null}
          <label className={styles.checkOption}>
            <input
              type="checkbox"
              checked={showUnchanged}
              onChange={(e) => setShowUnchanged(e.target.checked)}
            />
            Show unchanged
          </label>
          <button
            className={styles.btnGhost}
            type="button"
            disabled={phase === 'writing' || phase === 'done' || changed.length === 0}
            onClick={() =>
              setSelected(
                selected.size === changed.length
                  ? new Set()
                  : new Set(changed.map((t) => t.track_id)),
              )
            }
          >
            {selected.size === changed.length ? 'Deselect all' : 'Select all changes'}
          </button>
        </div>
      </div>
      {message ? (
        <div
          className={
            phase === 'error'
              ? styles.searchError
              : `${styles.grabBanner} ${phase === 'done' ? styles.grab_ok : styles.grab_busy}`
          }
        >
          {message}
        </div>
      ) : null}

      <div className={styles.resultsWrap}>
        {previewQuery.isLoading ? (
          <div className={styles.inlineLoading}>Reading file tags…</div>
        ) : previewQuery.isError ? (
          <div className={styles.searchError}>
            {previewQuery.error instanceof Error ? previewQuery.error.message : 'Preview failed'}
          </div>
        ) : tracks.length === 0 ? (
          <div className={styles.inlineLoading}>No tracks with files.</div>
        ) : visibleTracks.length === 0 ? (
          <div className={styles.inlineLoading}>
            All file tags match your library. Enable “Show unchanged” to review them.
          </div>
        ) : (
          <table className={`${styles.trackTable} ${styles.retagTable}`}>
            <thead>
              <tr>
                <th className={styles.colMonitor}></th>
                <th className={styles.colNum}>#</th>
                <th>Title</th>
                <th>Tag changes · current file → library value</th>
              </tr>
            </thead>
            {grouped.map((group) => {
              const changedInGroup = group.tracks.filter((track) => track.has_changes).length;
              return (
                <tbody key={group.albumId} className={styles.retagAlbumGroup}>
                  <tr className={styles.albumGroupHeaderRow}>
                    <td colSpan={4} className={styles.albumGroupHeader}>
                      <span className={styles.retagAlbumHeading}>
                        <span>{group.albumTitle}</span>
                        <span className={styles.retagReleaseType}>
                          {releaseTypeLabel(group.albumType)}
                        </span>
                        <span className={styles.retagAlbumCount}>
                          {changedInGroup} of{' '}
                          {tracks.filter((t) => t.album_id === group.albumId).length} changing
                        </span>
                      </span>
                    </td>
                  </tr>
                  {group.tracks.map((t) => (
                    <tr key={t.track_id} className={t.has_changes ? '' : styles.staticRow}>
                      <td>
                        {t.has_changes ? (
                          <input
                            type="checkbox"
                            aria-label={`Retag ${t.title || `track ${t.track_id}`}`}
                            checked={selected.has(t.track_id)}
                            disabled={phase === 'writing' || phase === 'done'}
                            onChange={() => toggle(t.track_id)}
                          />
                        ) : null}
                      </td>
                      <td className={styles.colNum}>{t.track_number ?? '—'}</td>
                      <td title={t.file_path ?? undefined}>{t.title ?? '—'}</td>
                      <td className={styles.diffCell}>
                        {t.error ? (
                          <span className={styles.statusWarn}>{t.error}</span>
                        ) : t.has_changes ? (
                          <>
                            <TagChanges track={t} />
                            {t.diff
                              .filter((d) => d.manual && d.manual_key)
                              .map((d) => (
                                <ManualFieldChoice
                                  key={d.manual_key}
                                  row={d}
                                  released={released.has(
                                    releaseKey(t.track_id, d.manual_key as string),
                                  )}
                                  onToggle={() => toggleRelease(t.track_id, d.manual_key as string)}
                                  disabled={phase === 'writing' || phase === 'done'}
                                />
                              ))}
                          </>
                        ) : (
                          <span className={styles.statusOk}>tags match</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              );
            })}
          </table>
        )}
      </div>
    </LibraryToolDialog>
  );
}
