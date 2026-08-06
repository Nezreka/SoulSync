/**
 * The Mirrored Playlists tab (stats-automations.js 500-656 + 1175-1205 +
 * 2023-2042, auto-sync.js 2377-2404; markup at index.html's
 * mirrored-tab-content).
 *
 * Mirrored is the odd vertical — see -sync.mirrored.ts for why none of the
 * shared card/phase machinery applies here.
 *
 * DECLARED DIVERGENCES
 * - Rename uses a real input row, NOT window.prompt (auto-sync.js 2381). The
 *   repo forbids the native dialogs; Escape/Cancel is the `null` return the
 *   vanilla checked for, and a blank value still clears the alias.
 * - Card clicks open the React DiscoveryModal for every non-fresh phase; the
 *   vanilla's downloading/download_complete arms reopened the vanilla ENGINE
 *   modal through the script-scoped activeDownloadProcesses registry, which
 *   React cannot reach (the P5a/P5b pattern).
 * - The phase line is ONE derived renderer; the vanilla's three writers
 *   disagree (documented in -sync.mirrored.ts).
 * - Export (📤), Auto-Sync, the 🔗 source-ref edit and the two pool modals all
 *   own their controllers outright; this tab supplies no handler props at all
 *   beyond the shared discovery-modal opener.
 * - The 🔗 editor is a modal, NOT window.prompt (auto-sync.js 2414) — the same
 *   repo rule the rename follows.
 * - DEFERRED, not dropped: the per-card quality-profile select (600-602) and
 *   its hydrate (645-650). It needs the profiles api + the hydration protocol
 *   that no React sync surface has yet — the discovery-modal footer's copy of
 *   the same control is deferred for the same reason (organize-toggle.tsx).
 *   row.quality_profile_id is carried on the row type ready for it.
 */

import { useCallback, useEffect, useState } from 'react';

import type { ExportMode } from '../-sync.export';
import type { MirroredPlaylistRow } from '../-sync.mirrored';
import type { MirroredPipelineState } from '../-sync.pipeline';
import type { SourceVertical } from '../-sync.use-vertical';

import {
  clearMirroredDiscovery,
  deleteMirroredPlaylist,
  fetchMirroredPlaylists,
  patchMirroredCustomName,
} from '../-sync.api';
import { patchMirroredSourceRef } from '../-sync.api';
import { getMirroredSourceRef } from '../-sync.autosync';
import { exportNotConnectedStatus } from '../-sync.export';
import {
  mirroredHash,
  mirroredPhaseLine,
  mirroredRatio,
  mirroredSourceIcon,
  pipelinePhaseFor,
  timeAgo,
} from '../-sync.mirrored';
import { SOURCE_REF_FAILED, applyPipelineState } from '../-sync.pipeline';
import { SYNC_SOURCES } from '../-sync.sources';
import { asString } from '../-sync.url-tabs';
import { useExportJobs } from '../-sync.use-export';
import { useMirroredPipeline } from '../-sync.use-pipeline';
import { DiscoveryPoolModal } from './discovery-pool-modal';
import { ExportModal, ExportStatusSpan } from './export-modal';
import { SourceRefModal } from './source-ref-modal';
import { hydrateStatesForLoaded } from './url-import-tab';
import { WingItPoolModal } from './wingit-pool-modal';

export interface MirroredTabProps {
  vertical: SourceVertical;
  /** Show the shared discovery modal for this mirrored hash. */
  onOpen: (sourceId: string) => void;
  /** The metadata source the ratio line names (currentMusicSourceName). */
  sourceName?: string;
}

export function MirroredTab({ vertical, onOpen, sourceName = 'Spotify' }: MirroredTabProps) {
  const config = SYNC_SOURCES.mirrored;
  const [rows, setRows] = useState<MirroredPlaylistRow[] | null>(null);
  const [placeholder, setPlaceholder] = useState('Loading mirrored playlists...');
  const [renaming, setRenaming] = useState<number | null>(null);
  const [renameValue, setRenameValue] = useState('');
  /** The row whose export picker is open (exportMirroredPlaylist, 663). */
  const [exporting, setExporting] = useState<MirroredPlaylistRow | null>(null);
  /** The row whose 🔗 source-ref editor is open (auto-sync.js 2410). */
  const [editingRef, setEditingRef] = useState<MirroredPlaylistRow | null>(null);
  /** Which pool modal is open, if any (the two header buttons, 1217/1373). */
  const [pool, setPool] = useState<'discovery' | 'wingit' | null>(null);
  const exportJobs = useExportJobs();
  /** loadMirroredPlaylists (500-524). */
  const load = useCallback(async () => {
    setPlaceholder('Loading mirrored playlists...');
    setRows(null);
    try {
      const list = (await fetchMirroredPlaylists()) as unknown as MirroredPlaylistRow[];
      setRows(list);
      if (list.length === 0) return;
      // The saved discovery states, after the list is up (520), resuming any
      // row the backend reports mid-flight.
      // The mirrored states endpoint sends playlist_id as a bare int and the
      // real key as url_hash (web_server.py 38508-38509), so the row key has
      // to be url_hash — matching on playlist_id can never hit.
      await hydrateStatesForLoaded(
        config,
        vertical,
        (pid) => {
          const found = list.find((p) => mirroredHash(p.id) === pid);
          return found ? (found as unknown as Record<string, unknown>) : undefined;
        },
        undefined,
        (row) => asString(row.url_hash),
      );
    } catch (err) {
      const message = err instanceof Error ? err.message : 'unknown error';
      setPlaceholder(`Error loading mirrored playlists: ${message}`);
    }
  }, [config, vertical]);

  useEffect(() => {
    void load();
  }, [load]);

  /**
   * applyMirroredPipelineState (auto-sync.js 2443-2464). patchState both
   * materialises an absent entry and merges, which is what the vanilla's
   * `{ ...(youtubePlaylistStates[hash] || {}), ... }` assignment does.
   */
  const onPipelineState = useCallback(
    (playlistId: number, state: MirroredPipelineState) => {
      vertical.patchState(mirroredHash(playlistId), (s) => ({
        ...s,
        ...applyPipelineState(s.phase, state),
      }));
    },
    [vertical],
  );

  // Memoised so the controller's callbacks stay stable and the resume effect
  // below does not re-run on every render.
  const reload = useCallback(() => {
    void load();
  }, [load]);

  const pipeline = useMirroredPipeline({ onState: onPipelineState, reload });

  /**
   * The render-time poller resume (stats-automations.js 653-655): a row the
   * backend says is running picks its poll back up, so a pipeline started by a
   * schedule or another client keeps advancing here. In the vanilla this sits
   * inside renderMirroredCard; an effect is the React equivalent, and the
   * hook's own registry makes it idempotent across re-renders.
   */
  useEffect(() => {
    for (const row of rows ?? []) {
      if (row.pipeline_state?.status === 'running') pipeline.resume(row.id, row.name ?? '');
    }
  }, [rows, pipeline]);

  /** clearMirroredDiscovery (1175-1205). */
  const onClear = useCallback(
    async (row: MirroredPlaylistRow) => {
      const name = row.name ?? '';
      const ok = await window.showConfirmDialog?.({
        title: 'Clear Discovery Data',
        message: `Clear discovery data for "${name}"? You can re-discover afterwards to get updated cover art.`,
      });
      if (!ok) return;
      try {
        const data = await clearMirroredDiscovery(row.id);
        if (!data.success) {
          window.showToast?.(data.error || 'Failed to clear discovery', 'error');
          return;
        }
        window.showToast?.(
          `Cleared discovery for ${name} (${data.cleared ?? 0} tracks)`,
          'success',
        );
        // 1184-1187: the 'cancelled' write is the running worker's cancel
        // signal and is GUARDED by the entry existing; the entry is then
        // DELETED. patchState alone would both skip the guard (it
        // materialises a fresh state) and leave a 'cancelled' entry behind,
        // which the next card click would read as non-fresh.
        const hash = mirroredHash(row.id);
        // The guard mirrors the vanilla's `if (youtubePlaylistStates[hash])`
        // (1185), which protects a property write on a possibly-absent
        // object. In React it is unobservable — patchState would just
        // materialise a state that dropState immediately removes — so no
        // test can pin it. Kept for faithfulness, flagged so nobody hunts.
        if (vertical.states[hash]) {
          vertical.patchState(hash, (s) => ({ ...s, phase: 'cancelled' }));
        }
        vertical.dropState(hash);
        await load();
      } catch (err) {
        window.showToast?.(
          `Error: ${err instanceof Error ? err.message : 'unknown error'}`,
          'error',
        );
      }
    },
    [vertical, load],
  );

  /** deleteMirroredPlaylist (2023-2042). */
  const onDelete = useCallback(
    async (row: MirroredPlaylistRow) => {
      const name = row.name ?? '';
      const ok = await window.showConfirmDialog?.({
        title: 'Delete Playlist',
        message: `Delete mirrored playlist "${name}"?`,
        confirmText: 'Delete',
        destructive: true,
      });
      if (!ok) return;
      try {
        const data = await deleteMirroredPlaylist(row.id);
        if (!data.success) {
          window.showToast?.(data.error || 'Failed to delete', 'error');
          return;
        }
        window.showToast?.(`Deleted mirror: ${name}`, 'success');
        await load();
      } catch (err) {
        window.showToast?.(
          `Error: ${err instanceof Error ? err.message : 'unknown error'}`,
          'error',
        );
      }
    },
    [load],
  );

  /** editMirroredCustomName (auto-sync.js 2377-2404), prompt → input row. */
  const commitRename = useCallback(
    async (row: MirroredPlaylistRow) => {
      const originalName = row.name ?? '';
      const trimmed = renameValue.trim();
      setRenaming(null);
      try {
        await patchMirroredCustomName(row.id, trimmed);
        window.showToast?.(
          trimmed ? `Renamed to "${trimmed}"` : `Reverted to "${originalName}"`,
          'success',
        );
        await load();
      } catch (err) {
        window.showToast?.(
          `Error: ${err instanceof Error ? err.message : 'Failed to update name'}`,
          'error',
        );
      }
    },
    [renameValue, load],
  );

  /** editMirroredSourceRef's PATCH half (auto-sync.js 2422-2440). */
  const commitSourceRef = useCallback(
    async (row: MirroredPlaylistRow, sourceRef: string) => {
      const name = row.name ?? '';
      setEditingRef(null);
      try {
        await patchMirroredSourceRef(row.id, sourceRef);
        window.showToast?.(`Updated source for ${name}`, 'success');
        // The vanilla also reopens the tracks-detail modal when it was open
        // (2434-2438); that modal is its own wave, so there is nothing to
        // reopen here.
        await load();
      } catch (err) {
        window.showToast?.(
          `Error: ${err instanceof Error ? err.message : SOURCE_REF_FAILED}`,
          'error',
        );
      }
    },
    [load],
  );

  /** handleMirroredCardClick (610-643). */
  const onCardClick = useCallback(
    (row: MirroredPlaylistRow) => {
      const hash = mirroredHash(row.id);
      const state = vertical.states[hash];
      if (state && state.phase && state.phase !== 'fresh') {
        onOpen(hash);
        return;
      }
      // Nothing running: the vanilla opens the TRACKS detail modal instead
      // (641). That modal is its own wave; until it exists the card seeds and
      // opens the shared modal rather than doing nothing.
      vertical.seed(hash, row as unknown as Record<string, unknown>);
      onOpen(hash);
    },
    [vertical, onOpen],
  );

  // One clock read per render — the vanilla calls timeAgo per card at 527.
  const now = Date.now();

  return (
    <div>
      <div className="playlist-header">
        <h3>Mirrored Playlists</h3>
        <button
          type="button"
          className="pool-trigger-btn"
          title="View matched and failed discovery tracks"
          onClick={() => setPool('discovery')}
        >
          Discovery Pool
        </button>
        <button
          type="button"
          className="pool-trigger-btn"
          title="Review tracks Wing It auto-matched on a best-effort guess — verify or re-match them"
          onClick={() => setPool('wingit')}
        >
          Wing It Pool
        </button>
        <button
          type="button"
          className="refresh-button mirrored"
          id="mirrored-refresh-btn"
          onClick={() => void load()}
        >
          Update list
        </button>
      </div>
      <div className="playlist-scroll-container" id="mirrored-playlist-container">
        {rows === null || rows.length === 0 ? (
          <div className="playlist-placeholder">
            {rows === null
              ? placeholder
              : 'Playlists you parse from any service will appear here as persistent backups.'}
          </div>
        ) : (
          rows.map((row) => {
            const hash = mirroredHash(row.id);
            const state = vertical.states[hash];
            // The live state wins; the pipeline phase is synthesised only in
            // its absence (534-542).
            const phase = state?.phase ?? pipelinePhaseFor(row);
            const line = mirroredPhaseLine(
              phase,
              state
                ? {
                    discoveryProgress: state.discoveryProgress,
                    spotifyMatches: state.spotifyMatches,
                    spotifyTotal: state.spotifyTotal,
                    // A live Auto-Sync run writes these onto the same state
                    // (applyPipelineState), so the pipeline arms read them
                    // from there rather than the stale row.
                    pipeline_progress: state.pipeline_progress,
                    pipeline_phase: state.pipeline_phase,
                  }
                : {
                    pipeline_progress: row.pipeline_state?.progress,
                    pipeline_phase: row.pipeline_state?.phase,
                  },
              row,
            );
            const ratio = mirroredRatio(row, sourceName);
            const discovered = row.discovered_count || 0;
            return (
              <div
                key={row.id}
                className="mirrored-playlist-card"
                id={`mirrored-card-${row.id}`}
                onClick={() => onCardClick(row)}
              >
                <div className={`source-icon ${row.source ?? ''}`}>
                  {mirroredSourceIcon(row.source)}
                </div>
                <div className="mirrored-card-info">
                  {renaming === row.id ? (
                    <input
                      className="card-name mirrored-rename-input"
                      autoFocus
                      value={renameValue}
                      placeholder="Leave blank to use the original name"
                      onClick={(e) => e.stopPropagation()}
                      onChange={(e) => setRenameValue(e.target.value)}
                      onKeyDown={(e) => {
                        e.stopPropagation();
                        if (e.key === 'Enter') void commitRename(row);
                        // Escape is the vanilla's null-return cancel (2386).
                        if (e.key === 'Escape') setRenaming(null);
                      }}
                      onBlur={() => setRenaming(null)}
                    />
                  ) : (
                    <div className="card-name">{row.display_name || row.name}</div>
                  )}
                  {row.custom_name && (
                    <div
                      className="card-original-name"
                      title="Original (upstream) playlist name — still tracked"
                    >
                      ↳ {row.name}
                    </div>
                  )}
                  <div className="card-meta">
                    <span className={`source-badge ${row.source ?? ''}`}>{row.source}</span>
                    <span>{row.track_count} tracks</span>
                    <span>Mirrored {timeAgo(row.updated_at || row.mirrored_at, now)}</span>
                    {ratio && (
                      <span className={`discovery-ratio${ratio.complete ? ' complete' : ''}`}>
                        {ratio.text}
                      </span>
                    )}
                    {line && <span style={{ color: line.color }}>{line.text}</span>}
                    {exportJobs.statuses[row.id] && (
                      <ExportStatusSpan status={exportJobs.statuses[row.id]} />
                    )}
                  </div>
                </div>
                {discovered > 0 && (
                  <button
                    type="button"
                    className="mirrored-card-clear"
                    title="Clear discovery data"
                    onClick={(e) => {
                      e.stopPropagation();
                      void onClear(row);
                    }}
                  >
                    ↺
                  </button>
                )}
                <button
                  type="button"
                  className="mirrored-card-pipeline"
                  title="Refresh, discover, sync, and queue missing tracks"
                  onClick={(e) => {
                    e.stopPropagation();
                    void pipeline.run(row.id, row.name ?? '');
                  }}
                >
                  Auto-Sync
                </button>
                <button
                  type="button"
                  className="mirrored-card-rename"
                  title="Rename (changes the name shown here and used when syncing)"
                  onClick={(e) => {
                    e.stopPropagation();
                    setRenameValue(row.custom_name ?? '');
                    setRenaming(row.id);
                  }}
                >
                  ✏️
                </button>
                <button
                  type="button"
                  className="mirrored-card-link"
                  title="Edit original playlist link"
                  onClick={(e) => {
                    e.stopPropagation();
                    setEditingRef(row);
                  }}
                >
                  🔗
                </button>
                <button
                  type="button"
                  className="mirrored-card-export"
                  title="Export to ListenBrainz / JSPF"
                  onClick={(e) => {
                    e.stopPropagation();
                    setExporting(row);
                  }}
                >
                  📤
                </button>
                <button
                  type="button"
                  className="mirrored-card-delete"
                  title="Delete mirror"
                  onClick={(e) => {
                    e.stopPropagation();
                    void onDelete(row);
                  }}
                >
                  ✕
                </button>
              </div>
            );
          })
        )}
      </div>
      {exporting && (
        <ExportModal
          // The picker's subtitle uses the card's shown name (607).
          name={exporting.display_name || exporting.name || ''}
          onClose={() => setExporting(null)}
          onChoose={(mode: ExportMode, backfill: boolean) => {
            const id = exporting.id;
            setExporting(null);
            void exportJobs.start(id, mode, backfill);
          }}
          onGated={(mode: ExportMode) => {
            const id = exporting.id;
            setExporting(null);
            exportJobs.paint(id, exportNotConnectedStatus(mode));
          }}
        />
      )}
      {pool === 'discovery' && <DiscoveryPoolModal onClose={() => setPool(null)} />}
      {pool === 'wingit' && <WingItPoolModal onClose={() => setPool(null)} />}
      {editingRef && (
        <SourceRefModal
          row={editingRef}
          currentRef={getMirroredSourceRef(editingRef)}
          onClose={() => setEditingRef(null)}
          onSubmit={(sourceRef) => void commitSourceRef(editingRef, sourceRef)}
        />
      )}
    </div>
  );
}
