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
 * - The TRACKS detail modal is PORTED (mirrored-detail-modal.tsx), not adopted
 *   like the pools. Its Discover button calls openYouTubeDiscoveryModal, which
 *   the flip deletes, so adoption would break its primary action; three of its
 *   other buttons already have React implementations here. The vanilla
 *   openMirroredPlaylistModal STAYS for auto-sync.js's three callers and
 *   shared-helpers.js, and retires with the auto-sync board phase.
 * - Card clicks open the React DiscoveryModal for every non-fresh phase; the
 *   vanilla's downloading/download_complete arms reopened the vanilla ENGINE
 *   modal through the script-scoped activeDownloadProcesses registry, which
 *   React cannot reach (the P5a/P5b pattern).
 * - The phase line is ONE derived renderer; the vanilla's three writers
 *   disagree (documented in -sync.mirrored.ts).
 * - Export (📤), Auto-Sync and the 🔗 source-ref edit own their controllers
 *   outright; this tab supplies no handler props beyond the discovery-modal
 *   opener.
 * - The two POOL modals are ADOPTED, not reimplemented. They are app-level
 *   overlays: the Tools page opens the Discovery Pool through the same
 *   window.openDiscoveryPoolModal seam (routes/tools/-ui/launcher-cards.tsx),
 *   and globals.d.ts records them as modals that stay vanilla. The header
 *   buttons call the globals; the flip must NOT delete them from
 *   stats-automations.js.
 * - The 🔗 editor is a modal, NOT window.prompt (auto-sync.js 2414) — the same
 *   repo rule the rename follows.
 * - DEFERRED, not dropped: the per-card quality-profile select (600-602) and
 *   its hydrate (645-650). It needs the profiles api + the hydration protocol
 *   that no React sync surface has yet — the discovery-modal footer's copy of
 *   the same control is deferred for the same reason (organize-toggle.tsx).
 *   row.quality_profile_id is carried on the row type ready for it.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import type { MirroredPlaylistDetail } from '../-sync.api';
import type { ExportMode } from '../-sync.export';
import type { MirroredPlaylistRow } from '../-sync.mirrored';
import type { PipelineController } from '../-sync.use-pipeline';
import type { SourceVertical } from '../-sync.use-vertical';

import {
  clearMirroredDiscovery,
  deleteMirroredPlaylist,
  fetchMirroredPlaylist,
  fetchMirroredPlaylists,
  fetchSourceDiscoveryStatus,
  prepareMirroredDiscovery,
  patchMirroredCustomName,
} from '../-sync.api';
import { patchMirroredSourceRef } from '../-sync.api';
import { getMirroredSourceRef } from '../-sync.autosync';
import { exportNotConnectedStatus } from '../-sync.export';
import {
  mirroredHash,
  mirroredPhaseLine,
  mirroredDiscoveryTracks,
  pipelinePhaseFor,
  timeAgo,
} from '../-sync.mirrored';
import { SOURCE_REF_FAILED, sourceRefUpdatedToast } from '../-sync.pipeline';
import { SYNC_SOURCES } from '../-sync.sources';
import { asString } from '../-sync.url-tabs';
import { useExportJobs } from '../-sync.use-export';
import type { LibraryFilter } from '../-sync.library';

import {
  libraryCardState,
  librarySortedRows,
  libraryFilterCounts,
  librarySources,
  librarySummary,
  libraryVisibleFilters,
  libraryVisibleRows,
} from '../-sync.library';
import { autoSyncCanSchedulePlaylist } from '../-sync.autosync';
import { cardScheduleLabel, useCardSchedules } from '../-sync.card-schedule';
import { PlaylistCard, playlistCardPrimaryLabel } from './playlist-card';
import { SourceIcon } from './source-icon';
import { ExportModal, ExportStatusSpan } from './export-modal';
import { MirroredDetailModal } from './mirrored-detail-modal';
import { SourceRefModal } from './source-ref-modal';
import { hydrateStatesForLoaded } from './url-import-tab';

export interface MirroredTabProps {
  vertical: SourceVertical;
  /** Show the shared discovery modal for this mirrored hash. */
  onOpen: (sourceId: string) => void;
  /** The metadata source the ratio line names (currentMusicSourceName). */
  sourceName?: string;
  /**
   * The page's ONE pipeline controller. REQUIRED, not optional-with-fallback:
   * the Auto-Sync board's Run-now needs the same controller this tab uses, and
   * a tab that quietly built its own would give the app two poller maps
   * hammering the same status endpoint for the same playlist. An optional prop
   * would make that failure silent; a required one makes it a compile error.
   * Same reasoning as useAutoSync's runPipeline.
   */
  pipeline: PipelineController;
  /**
   * Hand this tab's row refetch up to whoever owns the controller. The
   * controller needs a `reload` at construction and only this tab knows how to
   * do one, so the owner holds a slot and the tab fills it — which is how the
   * hook already treats its collaborators ("the collaborators live in refs so
   * the returned controller is STABLE").
   */
  registerReload: (reload: () => void) => void;
  /**
   * Hand the page this tab's detail-modal opener.
   *
   * The React modal and the legacy `openMirroredPlaylistModal` are two
   * implementations of the same thing over the same endpoint, and the page had
   * surfaces opening each — so the same playlist looked different depending on
   * which button you pressed. Registering upward lets everything on this page
   * use the React one. (The vanilla function stays alive: other pages still
   * call it, so it is the sync page's duplicate that goes, not the function.)
   */
  registerOpenDetail?: (open: (playlistId: number) => void) => void;
}

/**
 * A mirrored card's overflow menu.
 *
 * Five actions that used to be five icon-only buttons on every card — ↺ ✏️ 🔗
 * 📤 ✕ — each with tooltip-only meaning, on every row of the library at once.
 * They are all real, and none of them is what you came to the page to do.
 */
function MirroredCardMenu({
  row,
  top,
  left,
  onClose,
  onRename,
  onEditSource,
  onExport,
  onClear,
  onDelete,
}: {
  row: MirroredPlaylistRow;
  top: number;
  left: number;
  onClose: () => void;
  onRename: () => void;
  onEditSource: () => void;
  onExport: () => void;
  onClear: () => void;
  onDelete: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    // Deferred by a tick, or the click that OPENED the menu closes it again.
    const onDocClick = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) onClose();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    const id = setTimeout(() => document.addEventListener('click', onDocClick), 0);
    document.addEventListener('keydown', onKey);
    return () => {
      clearTimeout(id);
      document.removeEventListener('click', onDocClick);
      document.removeEventListener('keydown', onKey);
    };
  }, [onClose]);

  const item = (label: string, run: () => void, danger = false) => (
    <button
      type="button"
      className={`pl-menu-item${danger ? ' pl-menu-item--danger' : ''}`}
      onClick={() => {
        run();
        onClose();
      }}
    >
      {label}
    </button>
  );

  return (
    <div className="pl-menu" ref={ref} style={{ top: `${top}px`, left: `${left}px` }} role="menu">
      {item('Rename', onRename)}
      {item('Edit source link', onEditSource)}
      {item('Export', onExport)}
      {/* Only offered when there IS a discovery to clear (575-582). */}
      {(row.discovered_count || 0) > 0 && item('Clear discovery', onClear)}
      {item('Delete', onDelete, true)}
    </div>
  );
}

export function MirroredTab({
  vertical,
  onOpen,
  pipeline,
  registerReload,
  registerOpenDetail,
}: MirroredTabProps) {
  const config = SYNC_SOURCES.mirrored;
  const [rows, setRows] = useState<MirroredPlaylistRow[] | null>(null);
  const [placeholder, setPlaceholder] = useState('Loading mirrored playlists...');
  /** The library view: filter by STATE, and optionally narrow by source. */
  const [filter, setFilter] = useState<LibraryFilter>('all');
  const [sources, setSources] = useState<ReadonlySet<string>>(() => new Set());
  const toggleSource = useCallback((source: string) => {
    setSources((prev) => {
      const next = new Set(prev);
      if (!next.delete(source)) next.add(source);
      return next;
    });
  }, []);
  const clearSources = useCallback(() => {
    setSources(new Set());
  }, []);

  /** Each card's own sync interval — see -sync.card-schedule. */
  const cardSchedules = useCardSchedules();

  /** The overflow menu: which row, and where its trigger sits. */
  const [menu, setMenu] = useState<{ row: MirroredPlaylistRow; top: number; left: number } | null>(
    null,
  );

  const [renaming, setRenaming] = useState<number | null>(null);
  const [renameValue, setRenameValue] = useState('');
  /** The row whose export picker is open (exportMirroredPlaylist, 663). */
  const [exporting, setExporting] = useState<MirroredPlaylistRow | null>(null);
  /** The row whose 🔗 source-ref editor is open (auto-sync.js 2410). */
  const [editingRef, setEditingRef] = useState<MirroredPlaylistRow | null>(null);
  /**
   * Whether the open source-ref editor was launched FROM the detail modal.
   * The vanilla decides this by probing for #mirrored-track-modal at commit
   * time (2434); React closes the modal to show the editor, so the origin is
   * remembered instead of detected.
   *
   * Set at BOTH open sites and nowhere else — that is what makes it correct
   * without any cleanup on cancel: whichever entry point opened the editor has
   * already stated where it came from.
   */
  const [refEditFromDetail, setRefEditFromDetail] = useState(false);
  /** The playlist whose TRACKS detail modal is open (1066-1165). */
  const [detail, setDetail] = useState<{
    playlistId: number;
    data: MirroredPlaylistDetail;
  } | null>(null);
  const exportJobs = useExportJobs();
  /**
   * `config` and `vertical` are read through a ref instead of being
   * dependencies, so `load`'s identity is stable.
   *
   * As dependencies they made this self-feeding: `vertical` is a useMemo keyed
   * on `states`, `load` calls hydrateStatesForLoaded which WRITES those states,
   * so every load minted a new `vertical` -> a new `load` -> the mount effect
   * below re-fired -> load again. An endless refetch throttled only by the
   * round-trip, which read on screen as the list reloading every ~2s (and
   * blanking each time, because load starts with setRows(null)).
   *
   * Same trap the registerReload ref below documents, and the one useAutoSync's
   * `now` fell into. Only an explicit reload() should re-fetch this list.
   */
  const loadCtx = useRef({ config, vertical });
  loadCtx.current = { config, vertical };

  /** loadMirroredPlaylists (500-524). */
  const load = useCallback(async () => {
    const { config, vertical } = loadCtx.current;
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
  }, []);

  // Mount only — `load` is stable by construction now (see loadCtx above).
  useEffect(() => {
    void load();
  }, [load]);

  // Memoised so the registration below does not re-fire on every render.
  const reload = useCallback(() => {
    void load();
  }, [load]);

  /**
   * The controller is the page's; this is the half of it only this tab can
   * supply. See MirroredTabProps.registerReload.
   *
   * `registerReload` is held in a ref rather than listed as a dependency: it
   * is a prop, so a caller writing `registerReload={(fn) => …}` inline hands
   * us a NEW function every render, and the effect would re-fire on every one.
   * Same trap `useAutoSync`'s `now` fell into — there it looped forever
   * refetching five endpoints. Only `reload` should decide when to re-register.
   */
  const registerRef = useRef(registerReload);
  registerRef.current = registerReload;
  useEffect(() => {
    registerRef.current(reload);
  }, [reload]);

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
  /** openMirroredPlaylistModal's fetch half (1067-1073). */
  const openDetail = useCallback(async (playlistId: number) => {
    window.showLoadingOverlay?.('Loading mirrored playlist...');
    try {
      const data = await fetchMirroredPlaylist(playlistId);
      if (data.error) throw new Error(data.error);
      window.hideLoadingOverlay?.();
      setDetail({ playlistId, data });
    } catch (err) {
      window.hideLoadingOverlay?.();
      window.showToast?.(`Error: ${err instanceof Error ? err.message : 'unknown error'}`, 'error');
    }
  }, []);

  /** Same ref-not-dependency reasoning as registerReload above. */
  const registerOpenDetailRef = useRef(registerOpenDetail);
  registerOpenDetailRef.current = registerOpenDetail;
  useEffect(() => {
    registerOpenDetailRef.current?.((playlistId) => void openDetail(playlistId));
  }, [openDetail]);

  const commitSourceRef = useCallback(
    async (row: MirroredPlaylistRow, sourceRef: string, fromDetail: boolean) => {
      const name = row.name ?? '';
      setEditingRef(null);
      try {
        await patchMirroredSourceRef(row.id, sourceRef);
        window.showToast?.(sourceRefUpdatedToast(name), 'success');
        await load();
        // 2434-2438: an open detail modal is closed and REOPENED so it shows
        // the new source. Only when the edit came from there — the card's 🔗
        // leaves nothing open, and the vanilla's probe would find nothing.
        if (fromDetail) await openDetail(row.id);
      } catch (err) {
        window.showToast?.(
          `Error: ${err instanceof Error ? err.message : SOURCE_REF_FAILED}`,
          'error',
        );
      }
    },
    [load, openDetail],
  );

  /**
   * discoverMirroredPlaylist (2043-2149).
   *
   * The prepare-discovery POST at 2062 is the load-bearing step this port was
   * missing entirely: it REGISTERS the mirror with the backend so the discovery
   * pipeline can find it. Without it the discovery start had nothing to run on.
   */
  const runDiscovery = useCallback(
    async (playlistId: number) => {
      setDetail(null); // closeMirroredModal (2044)
      const hash = mirroredHash(playlistId);

      // Already discovering or discovered → just reopen, resuming a stalled
      // poller (2048-2057). React has no open-modal DOM to probe, so the state
      // itself is the whole test.
      const existing = vertical.states[hash];
      if (existing && existing.phase !== 'fresh') {
        onOpen(hash);
        if (existing.phase === 'discovering') vertical.resumeDiscovery(hash);
        return;
      }

      window.showLoadingOverlay?.('Preparing discovery...');
      try {
        const prep = await prepareMirroredDiscovery(playlistId);
        if (prep.error) throw new Error(prep.error);

        const data = await fetchMirroredPlaylist(playlistId);
        if (data.error) throw new Error(data.error);
        window.hideLoadingOverlay?.();

        const tracks = mirroredDiscoveryTracks(data.tracks ?? []);
        const playlist = { name: data.name, tracks, track_count: tracks.length };

        if (prep.from_cache) {
          // The backend already holds results: hydrate from the status endpoint
          // rather than re-running discovery (2082-2116).
          const status = await fetchSourceDiscoveryStatus(config, hash);
          if (status.error) throw new Error(String(status.error));
          vertical.hydrate(hash, {
            playlist,
            phase: status.phase || 'discovered',
            results: status.results || [],
            // `|| 100`, not `?? 100` — a 0 reads as complete here (2097).
            discovery_progress: (status.progress as number) || 100,
            spotify_matches: status.spotify_matches || 0,
            spotify_total: tracks.length,
          });
          const cached = prep.cached_matches || 0;
          const total = prep.total_tracks || tracks.length;
          window.showToast?.(`Loaded ${cached}/${total} cached discovery results`, 'success');
        } else {
          vertical.hydrate(hash, {
            playlist,
            phase: 'fresh',
            results: [],
            discovery_progress: 0,
            spotify_matches: 0,
            spotify_total: tracks.length,
          });
        }
        onOpen(hash);
      } catch (err) {
        window.hideLoadingOverlay?.();
        window.showToast?.(
          `Error: ${err instanceof Error ? err.message : 'unknown error'}`,
          'error',
        );
      }
    },
    [config, vertical, onOpen],
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
      // Nothing running → the TRACKS detail modal (641).
      void openDetail(row.id);
    },
    [vertical, onOpen, openDetail],
  );

  // One clock read per render — the vanilla calls timeAgo per card at 527.
  const now = Date.now();

  const allRows = rows ?? [];
  const counts = libraryFilterCounts(allRows, sources);
  const visibleFilters = libraryVisibleFilters(counts, filter);
  const sourceList = librarySources(allRows);
  const visibleRows = libraryVisibleRows(allRows, filter, sources);

  return (
    <div>
      <div className="playlist-header">
        <div className="library-heading">
          <h3>Your playlists</h3>
          {/* Says something TRUE, and omits what it has nothing to say about —
              a fresh install gets a sentence, not a row of zeroes. */}
          <p className="library-summary">{librarySummary(rows ?? [])}</p>
        </div>
        <button
          type="button"
          className="refresh-button mirrored"
          id="mirrored-refresh-btn"
          onClick={() => void load()}
        >
          Update list
        </button>
      </div>
      {rows !== null && rows.length > 0 && (
        <div className="library-filters">
          {/* State first: what a user came to find out is which playlists still
              need something doing to them, not where they came from. */}
          <div className="library-tabs" role="tablist">
            {visibleFilters.map((f) => (
              <button
                key={f.id}
                type="button"
                role="tab"
                aria-selected={filter === f.id}
                className={`library-tab${filter === f.id ? ' active' : ''}`}
                onClick={() => setFilter(f.id)}
              >
                {f.label} <span className="library-tab-count">{counts[f.id]}</span>
              </button>
            ))}
          </div>
          {/* Source demoted to a filter, which is what it always actually was. */}
          {sourceList.length > 1 && (
            <div className="library-sources">
              {sourceList.map((source) => (
                <button
                  key={source}
                  type="button"
                  className={`library-source${sources.has(source) ? ' active' : ''}`}
                  onClick={() => toggleSource(source)}
                >
                  <SourceIcon source={source} /> {source}
                </button>
              ))}
              {sources.size > 0 && (
                <button type="button" className="library-source-clear" onClick={clearSources}>
                  Clear
                </button>
              )}
            </div>
          )}
        </div>
      )}
      <div className="playlist-scroll-container pl-grid-scroll" id="mirrored-playlist-container">
        {rows === null || visibleRows.length === 0 ? (
          <div className="playlist-placeholder">
            {rows === null
              ? placeholder
              : rows.length === 0
                ? 'Playlists you add from any service will appear here.'
                : 'Nothing in this filter.'}
          </div>
        ) : (
          <div className="pl-grid">
            {librarySortedRows(visibleRows).map((row) => {
            const displayName = row.display_name || row.name || '';
            // The live phase line, unchanged — its percentages and its
            // Discovered N/M are information the resting meta line cannot
            // carry, so the existing (tested) writer still produces them.
            const hash = mirroredHash(row.id);
            const live = vertical.states[hash];
            const phaseLine = mirroredPhaseLine(
              live?.phase ?? pipelinePhaseFor(row),
              live
                ? {
                    discoveryProgress: live.discoveryProgress,
                    spotifyMatches: live.spotifyMatches,
                    spotifyTotal: live.spotifyTotal,
                    pipeline_progress: live.pipeline_progress,
                    pipeline_phase: live.pipeline_phase,
                  }
                : {
                    pipeline_progress: row.pipeline_state?.progress,
                    pipeline_phase: row.pipeline_state?.phase,
                  },
              row,
            );
            const exportStatus = exportJobs.statuses[row.id];
            const state = libraryCardState(row);
            return renaming === row.id ? (
              // Rename stays INLINE on the card: a modal for one text field is
              // heavier than the edit itself.
              <div className="pl-card pl-card--renaming" key={row.id}>
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
                    if (e.key === 'Escape') setRenaming(null);
                  }}
                  onBlur={() => setRenaming(null)}
                />
              </div>
            ) : (
              <PlaylistCard
                key={row.id}
                row={row}
                name={displayName}
                nameTitle={
                  row.custom_name ? `${displayName} — originally "${row.name ?? ''}"` : displayName
                }
                // "Mirrored 30m ago", the vanilla's own wording — it names what
                // the timestamp actually is (the last mirror refresh), where a
                // bare "30m ago" leaves you guessing.
                when={`Mirrored ${timeAgo(row.updated_at || row.mirrored_at, Date.now())}`}
                schedule={cardScheduleLabel(cardSchedules.schedules[String(row.id)])}
                status={
                  exportStatus ? (
                    <ExportStatusSpan status={exportStatus} />
                  ) : phaseLine ? (
                    <span className="card-phase" style={{ color: phaseLine.color }}>
                      {phaseLine.text}
                    </span>
                  ) : null
                }
                onOpen={() => onCardClick(row)}
                primary={
                  // The pipeline endpoint rejects file/beatport outright, and
                  // lastfm cannot be scheduled either — same guard the board
                  // uses, so the card never offers a run that 400s.
                  !autoSyncCanSchedulePlaylist(row)
                    ? null
                    : {
                  label: playlistCardPrimaryLabel(row),
                  danger: state === 'error',
                  onClick: () => {
                    // "View progress" opens the card; everything else runs the
                    // pipeline, which IS refresh + discover + sync + queue the
                    // missing tracks — so "Find N missing" is literal, not a
                    // friendlier name for something else.
                    if (state === 'working') onCardClick(row);
                    else void pipeline.run(row.id, row.name ?? '');
                  },
                      }
                }
                onMore={(anchor) => {
                  const box = anchor.getBoundingClientRect();
                  setMenu({ row, top: box.bottom + 6, left: box.right - 188 });
                }}
              />
            );
            })}
          </div>
        )}
      </div>
      {menu && (
        <MirroredCardMenu
          row={menu.row}
          top={menu.top}
          left={menu.left}
          onClose={() => setMenu(null)}
          onRename={() => {
            setRenameValue(menu.row.custom_name ?? '');
            setRenaming(menu.row.id);
          }}
          onEditSource={() => {
            setRefEditFromDetail(false);
            setEditingRef(menu.row);
          }}
          onExport={() => setExporting(menu.row)}
          onClear={() => void onClear(menu.row)}
          onDelete={() => void onDelete(menu.row)}
        />
      )}
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
      {detail && (
        <MirroredDetailModal
          playlistId={detail.playlistId}
          data={detail.data}
          now={now}
          onClose={() => setDetail(null)}
          onDelete={() => {
            const row = rows?.find((r) => r.id === detail.playlistId);
            if (row) void onDelete(row);
          }}
          onEditSource={() => {
            // The vanilla's Edit Source passes the DETAIL payload's fields,
            // not the list row's: data.name, `data.source || 'unknown'`, and
            // getMirroredSourceRef(DATA) (1084-1085, 1150). The list can be
            // staler than the modal you are looking at, so the editor is
            // seeded from what the modal actually shows.
            setDetail(null);
            setRefEditFromDetail(true);
            setEditingRef({
              id: detail.playlistId,
              name: detail.data.name,
              source: detail.data.source || 'unknown',
              source_ref: detail.data.source_ref,
              description: detail.data.description,
              source_playlist_id: detail.data.source_playlist_id,
            });
          }}
          onRunPipeline={() => {
            setDetail(null);
            void pipeline.run(detail.playlistId, detail.data.name ?? '');
          }}
          onDiscover={() => void runDiscovery(detail.playlistId)}
        />
      )}
      {editingRef && (
        <SourceRefModal
          row={editingRef}
          currentRef={getMirroredSourceRef(editingRef)}
          onClose={() => setEditingRef(null)}
          onSubmit={(sourceRef) => void commitSourceRef(editingRef, sourceRef, refEditFromDetail)}
        />
      )}
    </div>
  );
}
