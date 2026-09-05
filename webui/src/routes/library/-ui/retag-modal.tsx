import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';

import { useModalA11y } from '@/components/dialog/use-modal-a11y';

import {
  fetchLibraryV2JobStatus,
  fetchLibraryV2TagPreview,
  LIBRARY_V2_QUERY_KEY,
  writeLibraryV2Tags,
  type LibraryV2TagPreviewTrack,
} from '../-library-v2.api';
import styles from './library-v2-page.module.css';

function fieldValue(v: unknown): string {
  if (v === null || v === undefined || v === '') return '—';
  if (Array.isArray(v)) return v.join(', ');
  if (typeof v === 'string') return v;
  if (typeof v === 'number' || typeof v === 'boolean' || typeof v === 'bigint') return String(v);
  if (typeof v === 'object') return JSON.stringify(v);
  return '—';
}

function diffSummary(t: LibraryV2TagPreviewTrack): string {
  return t.diff
    .filter((d) => !d.manual)
    .map((d) => `${d.field}: ${fieldValue(d.file_value)} → ${fieldValue(d.db_value)}`)
    .join('  ·  ');
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
      <span className={styles.retagManualField}>{row.field}</span>
      <span className={styles.retagManualNote}>set by hand</span>
      <button
        type="button"
        className={released ? styles.retagChoice : styles.retagChoiceActive}
        disabled={disabled}
        onClick={() => released && onToggle()}
      >
        Keep mine ({fieldValue(row.db_value)})
      </button>
      <button
        type="button"
        className={released ? styles.retagChoiceActive : styles.retagChoice}
        disabled={disabled}
        onClick={() => !released && onToggle()}
      >
        Use "{provider}"
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
  const a11yRef = useModalA11y<HTMLDivElement>(onClose);
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
    for (const t of tracks) {
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
  }, [tracks]);

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
    <div className={styles.modalBackdrop} role="presentation" onClick={onClose}>
      <div
        className={`${styles.modal} ${styles.modalWide}`}
        ref={a11yRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        onClick={(e) => e.stopPropagation()}
      >
        <div className={styles.modalHeader}>
          <h3>Preview Retag — {title}</h3>
          <button type="button" className={styles.iconAction} title="Close" onClick={onClose}>
            ✕
          </button>
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
          ) : (
            <table className={styles.trackTable}>
              <thead>
                <tr>
                  <th className={styles.colMonitor}></th>
                  <th className={styles.colNum}>#</th>
                  <th>Title</th>
                  <th>Changes (file → library)</th>
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
                            {changedInGroup} of {group.tracks.length} changing
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
                              checked={selected.has(t.track_id)}
                              disabled={phase === 'writing'}
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
                              {diffSummary(t)}
                              {t.diff
                                .filter((d) => d.manual && d.manual_key)
                                .map((d) => (
                                  <ManualFieldChoice
                                    key={d.manual_key}
                                    row={d}
                                    released={released.has(
                                      releaseKey(t.track_id, d.manual_key as string),
                                    )}
                                    onToggle={() =>
                                      toggleRelease(t.track_id, d.manual_key as string)
                                    }
                                    disabled={phase === 'writing'}
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
      </div>
    </div>
  );
}
