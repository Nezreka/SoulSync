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
import type { LibraryFilter, LibrarySort } from '../-sync.library';
import type { MirroredPlaylistRow } from '../-sync.mirrored';
import type { PipelineController } from '../-sync.use-pipeline';
import type { SourceVertical } from '../-sync.use-vertical';
import type { AutoSyncWeeklyDraft } from './autosync-weekly';

import {
  clearMirroredDiscovery,
  deleteMirroredPlaylist,
  deleteMirroredPlaylists,
  fetchMirroredPlaylist,
  fetchMirroredPlaylists,
  fetchSourceDiscoveryStatus,
  prepareMirroredDiscovery,
  patchMirroredCustomName,
  postCopyToRequestList,
  patchRequestListSettings,
} from '../-sync.api';
import { patchMirroredSourceRef } from '../-sync.api';
import { patchMirroredPreferences } from '../-sync.api';
import { autoSyncSourceLabel, getMirroredSourceRef } from '../-sync.autosync';
import { autoSyncCanSchedulePlaylist } from '../-sync.autosync';
import { autoSyncPipelineCoversPlaylist } from '../-sync.autosync';
import { autoSyncPlaylistHealth, detectBrowserTimezone } from '../-sync.autosync';
import { autoSyncNextRunLabel } from '../-sync.autosync';
import { cardScheduleLabel, useCardSchedules } from '../-sync.card-schedule';
import { exportNotConnectedStatus } from '../-sync.export';
import {
  libraryCardState,
  librarySortedRows,
  libraryFilterCounts,
  LIBRARY_SORTS,
  librarySearch,
  librarySources,
  librarySummary,
  libraryVisibleFilters,
  libraryVisibleRows,
} from '../-sync.library';
import {
  mirroredDiscoveryReopenable,
  mirroredHash,
  mirroredPhaseLine,
  mirroredDiscoveryTracks,
  pipelinePhaseFor,
  timeAgo,
} from '../-sync.mirrored';
import { SOURCE_REF_FAILED, sourceRefUpdatedToast } from '../-sync.pipeline';
import { fetchRotationPreview, rotationConfirmMessage } from '../-sync.rotation';
import { SYNC_SOURCES } from '../-sync.sources';
import { asString } from '../-sync.url-tabs';
import { useExportJobs } from '../-sync.use-export';
import { playPlaylist } from '../../../features/playlists/play-playlist';
import {
  copyMirrorToUserPlaylist,
  createUserPlaylist,
  duplicateUserPlaylist,
  isUserPlaylist,
  pinLikedFirst,
  removeFromUserPlaylist,
  reorderUserPlaylist,
} from '../../../features/playlists/user-playlists';
import { AutoSyncWeeklyEditor } from './autosync-weekly';
import { CoverArtSheet } from './cover-art-sheet';
import { ExportModal, ExportStatusSpan } from './export-modal';
import { MirroredDetailModal } from './mirrored-detail-modal';
import { PlaylistCard, playlistCardPrimaryLabel } from './playlist-card';
import { PlaylistSearch } from './playlist-search';
import { PlaylistSettingsSheet } from './playlist-settings-sheet';
import styles from './playlists.module.css';
import { ScheduleMenu } from './schedule-menu';
import { SourceRefModal } from './source-ref-modal';
import { hydrateStatesForLoaded } from './url-import-tab';
import { usePopoverDismiss } from './use-popover-dismiss';
import { usePopoverPosition } from './use-popover-position';

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
  registerReload: (reload: () => void, key?: string) => void;
  /** a mirror's copy on the server, in the server editor; true once it opened */
  onOpenServerEditor?: (mirrorId: number, name?: string) => Promise<boolean>;
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
  /**
   * which playlists this instance shows. 'all' is the Mirrored library, every
   * playlist that came from somewhere. 'user' is My Playlists, the ones made
   * here (source 'soulsync'). 'requests' is Request Lists. same component so all
   * get every card action, schedule and the pipeline; each playlist lives in exactly one tab.
   */
  scope?: 'all' | 'user' | 'requests';
  /**
   * The page header's status line reads from here: how many playlists, how
   * many need attention, how many are running, and when the next scheduled run
   * is. Only the Mirrored instance reports; it is the library.
   */
  onSummary?: (summary: SyncLibrarySummary) => void;
  /**
   * Bumped by the page's Add sheet ("New playlist", "New request list"): each
   * new value opens this tab's create row.
   */
  createSignal?: number;
}

/** What the page header says about the library. */
export interface SyncLibrarySummary {
  playlists: number;
  attention: number;
  running: number;
  /** "next in 3h", or '' when nothing is scheduled */
  nextRun: string;
}

/** below this many playlists the Needs attention shelf stays hidden */
export const SHELF_MIN_LIBRARY = 12;

/** the scope's slice of the list. */
function rowsForScope<T extends { source?: string | null; is_request_list?: boolean }>(
  rows: readonly T[],
  scope: 'all' | 'user' | 'requests',
): T[] {
  if (scope === 'requests') {
    return rows.filter((r) => Boolean(r.is_request_list));
  }
  return rows.filter((r) => !r.is_request_list && isUserPlaylist(r) === (scope === 'user'));
}

/**
 * A mirrored card's overflow menu: ACTIONS only.
 *
 * It used to hold settings too (organize, quality profile, sync mode,
 * auto-remove, the source link, cover art, rename), thirteen items mixing
 * "do this now" with "how this playlist behaves". The settings live in the
 * playlist's Settings sheet now; this keeps what you do to a playlist.
 */
function MirroredCardMenu({
  row,
  top,
  left,
  anchor,
  onClose,
  onOpen,
  primary,
  onSchedule,
  onExport,
  onClear,
  onDelete,
  onSettings,
  onCopyToMine,
  onCopyToRequests,
}: {
  row: MirroredPlaylistRow;
  top: number;
  left: number;
  /** The trigger, so an outside click on IT does not fight the toggle. */
  anchor?: HTMLElement | null;
  onClose: () => void;
  onOpen: () => void;
  /** the card's own primary action, same label and handler as its button */
  primary?: { label: string; onClick: () => void } | null;
  /** the schedule picker; absent when this source can't be scheduled */
  onSchedule?: () => void;
  onExport: () => void;
  onClear: () => void;
  onDelete: () => void;
  onSettings: () => void;
  /** a mirror only: an editable copy in My Playlists */
  onCopyToMine?: () => void;
  /** copy as a new Request List */
  onCopyToRequests?: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const pos = usePopoverPosition({ top, left }, ref);

  usePopoverDismiss({ ref, anchor, onClose });

  const item = (label: string, run: () => void, danger = false) => (
    <button
      type="button"
      role="menuitem"
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
    <div
      className="pl-menu"
      ref={ref}
      style={{ top: `${pos.top}px`, left: `${pos.left}px` }}
      role="menu"
    >
      {item('Open', onOpen)}
      {primary && item(primary.label, primary.onClick)}
      {onSchedule && item('Schedule…', onSchedule)}
      <div className="pl-menu-sep" />
      {!isUserPlaylist(row) && onCopyToMine && item('Copy to My Playlists', onCopyToMine)}
      {!row.is_request_list && onCopyToRequests && item('Copy to Request Lists', onCopyToRequests)}
      {item('Export', onExport)}
      {/* Only offered when there IS a discovery to clear (575-582). */}
      {(row.discovered_count || 0) > 0 && item('Clear & re-identify', onClear)}
      <div className="pl-menu-sep" />
      {item('Settings', onSettings)}
      {item('Delete', onDelete, true)}
    </div>
  );
}

/** a source id as people say it: spotify_public is "Spotify Link" */
export function sourceFilterLabel(source: string): string {
  const known = autoSyncSourceLabel(source);
  if (known !== source) return known;
  return source
    .split('_')
    .map((w) => (w ? w[0].toUpperCase() + w.slice(1) : w))
    .join(' ');
}

export function MirroredTab({
  vertical,
  onOpen,
  pipeline,
  registerReload,
  registerOpenDetail,
  onOpenServerEditor,
  scope = 'all',
  onSummary,
  createSignal,
}: MirroredTabProps) {
  const config = SYNC_SOURCES.mirrored;
  const userScope = scope === 'user';
  const requestsScope = scope === 'requests';
  const loadingText = requestsScope
    ? 'Loading request lists...'
    : userScope
      ? 'Loading your playlists...'
      : 'Loading mirrored playlists...';
  const [rows, setRows] = useState<MirroredPlaylistRow[] | null>(null);
  const [placeholder, setPlaceholder] = useState(loadingText);
  /** The library view: filter by STATE, and optionally narrow by source. */
  const [filter, setFilter] = useState<LibraryFilter>('all');
  const [query, setQuery] = useState('');
  const [sort, setSort] = useState<LibrarySort>('state');
  const [sources, setSources] = useState<ReadonlySet<string>>(() => new Set());
  const clearSources = useCallback(() => {
    setSources(new Set());
  }, []);
  const setSourcesTo = useCallback((source: string) => {
    setSources(new Set([source]));
  }, []);

  /**
   * select mode (#1219): four tidal playlists shared a name, three got deleted
   * upstream, and the only way to drop the stale mirrors was the card menu one
   * card at a time. ids, not rows, so a refetch underneath the selection keeps
   * it honest; ids whose cards are gone are dropped before anything acts.
   */
  const [selecting, setSelecting] = useState(false);
  const [selected, setSelected] = useState<ReadonlySet<number>>(() => new Set());
  const toggleSelected = useCallback((id: number) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (!next.delete(id)) next.add(id);
      return next;
    });
  }, []);
  const exitSelecting = useCallback(() => {
    setSelecting(false);
    setSelected(new Set());
  }, []);

  /** Each card's own sync interval — see -sync.card-schedule. */
  const cardSchedules = useCardSchedules();

  /** The overflow menu: which row, and where its trigger sits. */
  const [menu, setMenu] = useState<{
    row: MirroredPlaylistRow;
    top: number;
    left: number;
    anchor: HTMLElement;
  } | null>(null);
  /** The schedule picker, and the weekly editor it can open. */
  const [schedMenu, setSchedMenu] = useState<{
    row: MirroredPlaylistRow;
    top: number;
    left: number;
    anchor: HTMLElement;
  } | null>(null);
  const [weeklyDraft, setWeeklyDraft] = useState<AutoSyncWeeklyDraft | null>(null);

  /** the sort / show / from menu */
  const [viewOpen, setViewOpen] = useState(false);
  /** the playlist whose Settings sheet is open, by id so a reload refreshes it */
  const [settingsFor, setSettingsFor] = useState<number | null>(null);
  /** The row whose export picker is open (exportMirroredPlaylist, 663). */
  const [exporting, setExporting] = useState<MirroredPlaylistRow | null>(null);
  const [coverArtFor, setCoverArtFor] = useState<MirroredPlaylistRow | null>(null);
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
  const loadCtx = useRef({ config, vertical, scope, loadingText });
  loadCtx.current = { config, vertical, scope, loadingText };

  /** loadMirroredPlaylists (500-524). */
  const load = useCallback(async () => {
    const { config, vertical, scope, loadingText } = loadCtx.current;
    setPlaceholder(loadingText);
    setRows(null);
    try {
      const list = rowsForScope(
        (await fetchMirroredPlaylists()) as unknown as MirroredPlaylistRow[],
        scope,
      );
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
      const scopeLabel = scope === 'requests' ? 'request' : scope === 'user' ? 'your' : 'mirrored';
      setPlaceholder(`Error loading ${scopeLabel} playlists: ${message}`);
    }
  }, []);

  // Mount only — `load` is stable by construction now (see loadCtx above).
  useEffect(() => {
    void load();
  }, [load]);

  /**
   * Memoised so the registration below does not re-fire on every render.
   *
   * Reloads the SCHEDULES as well as the rows. A cadence can be changed from
   * outside this tab entirely — the Bulk schedule modal owns its own copy of
   * the same automations — and this tab's Scheduled / Unscheduled tabs are
   * built from the map held here. Refetching rows alone left a playlist sitting
   * under "Scheduled" after it had been unscheduled somewhere else.
   */
  const reload = useCallback(() => {
    void load();
    void cardSchedules.reload();
  }, [load, cardSchedules.reload]);

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
    const key =
      scope === 'requests' ? 'request-lists' : scope === 'user' ? 'my-playlists' : 'mirrored';
    registerRef.current(reload, key);
  }, [reload, scope]);

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

  // runDiscovery is declared further down (it closes over state onClear
  // doesn't need); the clear reaches it through this
  const runDiscoveryRef = useRef<((playlistId: number) => Promise<void>) | null>(null);

  /**
   * clearMirroredDiscovery (1175-1205), then straight into a fresh
   * identification (#1910): clearing used to leave you with nowhere to run it
   * again from.
   */
  const onClear = useCallback(
    async (row: MirroredPlaylistRow) => {
      const name = row.name ?? '';
      const ok = await window.showConfirmDialog?.({
        title: 'Clear and re-identify',
        message: `Clear the identification for "${name}" and start over? The identification window opens so you can run it again.`,
      });
      if (!ok) return;
      try {
        const data = await clearMirroredDiscovery(row.id);
        if (!data.success) {
          window.showToast?.(data.error || 'Failed to clear identification', 'error');
          return;
        }
        window.showToast?.(
          `Cleared identification for ${name} (${data.cleared ?? 0} tracks)`,
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
        // a tick later, so it reads the render where the cleared state is gone
        // and opens a fresh identification rather than the old results
        window.setTimeout(() => void runDiscoveryRef.current?.(row.id), 0);
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
        // a mirror can be mirrored again; a user playlist is gone for good
        message: isUserPlaylist(row)
          ? `Delete "${name}"? This can't be undone.`
          : `Delete mirrored playlist "${name}"?`,
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
        window.showToast?.(
          isUserPlaylist(row) ? `Deleted ${name}` : `Deleted mirror: ${name}`,
          'success',
        );
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

  /** delete every selected mirror in one call, after one confirm. */
  const onDeleteSelected = useCallback(
    async (targets: MirroredPlaylistRow[]) => {
      if (targets.length === 0) return;
      const names = targets.map((r) => r.display_name || r.name || '');
      const shown = names.slice(0, 6);
      const rest = names.length - shown.length;
      const list = shown.map((n) => `• ${n}`).join('\n') + (rest > 0 ? `\n…and ${rest} more` : '');
      const ok = await window.showConfirmDialog?.({
        title: `Delete ${targets.length} Playlist${targets.length === 1 ? '' : 's'}`,
        message: `Delete ${targets.length} mirrored playlist${targets.length === 1 ? '' : 's'}?\n\n${list}`,
        confirmText: 'Delete',
        destructive: true,
      });
      if (!ok) return;
      try {
        const data = await deleteMirroredPlaylists(targets.map((r) => r.id));
        if (!data.success) {
          window.showToast?.(data.error || 'Failed to delete', 'error');
          return;
        }
        const gone = data.deleted?.length ?? 0;
        const missed = data.not_deleted?.length ?? 0;
        window.showToast?.(
          missed
            ? `Deleted ${gone} mirror${gone === 1 ? '' : 's'}, ${missed} couldn’t be deleted`
            : `Deleted ${gone} mirror${gone === 1 ? '' : 's'}`,
          missed ? 'warning' : 'success',
        );
        for (const r of targets) vertical.dropState(mirroredHash(r.id));
        exitSelecting();
        await load();
      } catch (err) {
        window.showToast?.(
          `Error: ${err instanceof Error ? err.message : 'unknown error'}`,
          'error',
        );
      }
    },
    [load, vertical, exitSelecting],
  );

  /** editMirroredCustomName (auto-sync.js 2377-2404), prompt → input row. */
  const renameTo = useCallback(
    async (row: MirroredPlaylistRow, value: string) => {
      const originalName = row.name ?? '';
      const trimmed = value.trim();
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
    [load],
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

  /** My Playlists / Request Lists' new-playlist row: null when closed, else the typed name. */
  const [newName, setNewName] = useState<string | null>(null);
  const createPlaylist = useCallback(async () => {
    const name = (newName ?? '').trim();
    if (!name) {
      setNewName(null);
      return;
    }
    try {
      const { id } = await createUserPlaylist(name, [], requestsScope);
      setNewName(null);
      window.showToast?.(
        requestsScope ? `Made request list "${name}"` : `Made "${name}"`,
        'success',
      );
      await load();
      await openDetail(id);
    } catch (err) {
      window.showToast?.(err instanceof Error ? err.message : 'Failed to make playlist', 'error');
    }
  }, [newName, load, openDetail, requestsScope]);

  /**
   * a user playlist's track edits. the detail is refetched quietly (no
   * overlay) so the modal stays put while you work through the list, and the
   * cards reload for the new count. on a failure the refetch still runs, so
   * what you see is what's saved.
   */
  const editTracks = useCallback(
    async (playlistId: number, edit: () => Promise<unknown>) => {
      try {
        await edit();
      } catch (err) {
        window.showToast?.(
          err instanceof Error ? err.message : 'Failed to update playlist',
          'error',
        );
      }
      try {
        const data = await fetchMirroredPlaylist(playlistId);
        if (!data.error) {
          setDetail((prev) => (prev?.playlistId === playlistId ? { playlistId, data } : prev));
        }
      } catch {
        // the next open shows it
      }
      void load();
    },
    [load],
  );

  /**
   * Rotating (#2039). Turning it on asks first: it is the one playlist
   * setting that ends in deleted files, and the confirm is where the keep
   * rules get said out loud. The server turns the cleaner on with it.
   */
  const setRotating = useCallback(
    async (row: MirroredPlaylistRow, enabled: boolean) => {
      const name = row.display_name || row.name || 'Playlist';
      try {
        if (enabled) {
          const preview = await fetchRotationPreview(row.id);
          const ok = await window.showConfirmDialog?.({
            title: 'Make this a rotating playlist',
            message: rotationConfirmMessage(name, preview),
            confirmText: 'Make rotating',
            destructive: preview?.dry_run === false,
          });
          if (!ok) return;
        }
        const res = await patchMirroredPreferences(row.id, { rotating: enabled });
        const data = (await res.json()) as { error?: string; cleaner_turned_on?: boolean };
        if (!res.ok || data.error) throw new Error(data.error || 'Failed to update preference');
        window.showToast?.(
          enabled
            ? data.cleaner_turned_on
              ? `"${name}" is rotating. Expired Download Cleaner turned on`
              : `"${name}" is rotating`
            : `"${name}" keeps its downloads`,
          'success',
        );
        await load();
      } catch (err) {
        window.showToast?.(`Error: ${err instanceof Error ? err.message : String(err)}`, 'error');
      }
    },
    [load],
  );

  /**
   * Organize-by-playlist, moved off the Auto-Sync board.
   *
   * Optimistic, then reconciled by the reload: the checkbox on the board was
   * instant and a round-trip before the tick moves would feel broken.
   */
  const setOrganize = useCallback(
    async (row: MirroredPlaylistRow, enabled: boolean) => {
      try {
        const res = await patchMirroredPreferences(row.id, { organize_by_playlist: enabled });
        const data = (await res.json()) as { error?: string };
        if (!res.ok || data.error) throw new Error(data.error || 'Failed to update preference');
        window.showToast?.(
          enabled
            ? `"${row.name ?? 'Playlist'}" downloads into its own folder`
            : `"${row.name ?? 'Playlist'}" downloads into the library root`,
          'success',
        );
        await load();
      } catch (err) {
        window.showToast?.(`Error: ${err instanceof Error ? err.message : String(err)}`, 'error');
      }
    },
    [load],
  );
  const setSyncMode = useCallback(
    async (row: MirroredPlaylistRow, mode: string) => {
      try {
        const res = await patchMirroredPreferences(row.id, { sync_mode: mode || null });
        const data = (await res.json()) as { error?: string };
        if (!res.ok || data.error) throw new Error(data.error || 'Failed to update preference');
        window.showToast?.(
          mode
            ? `"${row.display_name || row.name || 'Playlist'}" now syncs in ${mode} mode`
            : `"${row.display_name || row.name || 'Playlist'}" follows the global sync mode`,
          'success',
        );
        await load();
      } catch (err) {
        window.showToast?.(`Error: ${err instanceof Error ? err.message : String(err)}`, 'error');
      }
    },
    [load],
  );
  /** Same ref-not-dependency reasoning as registerReload above. */
  const registerOpenDetailRef = useRef(registerOpenDetail);
  registerOpenDetailRef.current = registerOpenDetail;
  useEffect(() => {
    // each tab instance gets its own opener prop (Mirrored's, and My
    // Playlists' for a copy landing there, #1913), so no instance can take
    // another's; Request Lists isn't handed one
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
      // ...but only when that state actually HAS the playlist. A state with no
      // tracks is a wedged one (a start that patched 'discovering' onto an empty
      // state, or a poll that died before the first payload), and reopening it
      // just re-showed "Playlist (0 tracks) / Starting discovery..." forever.
      // Falling through re-prepares and heals it.
      if (existing && mirroredDiscoveryReopenable(existing)) {
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
  runDiscoveryRef.current = runDiscovery;

  /**
   * the card always opens the tracks detail. it used to jump straight to the
   * discovery modal once a discovery existed, which hid Delete and Edit Source
   * for good (#1403), and after a clear + sync the pipeline phase with no
   * playlist behind it opened an empty discovery modal nobody could use
   * (#1405). the detail's Discover button reopens the discovery instead.
   */
  /** make a mirror yours, from its card menu or its detail: the copy opens,
   *  ready to edit, and the mirror keeps syncing from its source */
  const copyToMine = useCallback(async (mirrorId: number) => {
    try {
      const copy = await copyMirrorToUserPlaylist(mirrorId);
      const data = await fetchMirroredPlaylist(copy.id);
      if (!data.error) setDetail({ playlistId: copy.id, data });
      window.showToast?.(`“${copy.name}” is in My Playlists now`, 'success');
    } catch (err) {
      window.showToast?.(
        err instanceof Error ? err.message : 'Could not copy the playlist',
        'error',
      );
    }
  }, []);

  const copyToRequests = useCallback(
    async (mirrorId: number) => {
      try {
        const res = await postCopyToRequestList(mirrorId);
        if (res.new_playlist_id) {
          const data = await fetchMirroredPlaylist(res.new_playlist_id);
          if (!data.error) setDetail({ playlistId: res.new_playlist_id, data });
        }
        reload();
        window.showToast?.('Copied to Request Lists', 'success');
      } catch (err) {
        window.showToast?.(
          err instanceof Error ? err.message : 'Could not copy to Request Lists',
          'error',
        );
      }
    },
    [reload],
  );

  const toggleAutoRemove = useCallback(
    async (row: MirroredPlaylistRow) => {
      try {
        const next = !row.auto_remove_fulfilled;
        // turning it on deletes songs from the real spotify playlist, so ask first
        if (next) {
          const ok = await window.showConfirmDialog?.({
            title: 'Remove fulfilled tracks from Spotify?',
            message: `Once a track from "${row.name}" is in your library, SoulSync deletes it from the playlist on Spotify itself. A wrong match deletes the wrong song.`,
            confirmText: 'Turn on',
            destructive: true,
          });
          if (!ok) return;
        }
        await patchRequestListSettings(row.id, next);
        reload();
        window.showToast?.(
          next ? 'Auto-removal of fulfilled tracks enabled' : 'Auto-removal disabled',
          'success',
        );
      } catch (err) {
        window.showToast?.(
          err instanceof Error ? err.message : 'Could not update request list settings',
          'error',
        );
      }
    },
    [reload],
  );

  const onCardClick = useCallback(
    (row: MirroredPlaylistRow) => {
      void openDetail(row.id);
    },
    [openDetail],
  );

  /** "View progress" on a working card goes straight to discovery, but only
   *  when there is a playlist to show, otherwise it's the detail. */
  const onViewProgress = useCallback(
    (row: MirroredPlaylistRow) => {
      const hash = mirroredHash(row.id);
      if (mirroredDiscoveryReopenable(vertical.states[hash])) onOpen(hash);
      else void openDetail(row.id);
    },
    [vertical, onOpen, openDetail],
  );

  // One clock read per render — the vanilla calls timeAgo per card at 527.
  const now = Date.now();

  const allRows = rows ?? [];
  const settingsRow =
    settingsFor === null ? null : (allRows.find((r) => r.id === settingsFor) ?? null);
  /**
   * The ids that have a cadence, for the Scheduled / Unscheduled tabs. Derived
   * from the same map the cards read for their pills, so a tab can never
   * disagree with the pill on the card it is filtering to. #1289: an
   * all-playlists pipeline schedules every row.
   */
  const scheduledIds = new Set(
    cardSchedules.hasAllPipeline
      ? allRows
          .filter((r) => autoSyncPipelineCoversPlaylist(r))
          .map((r) => r.id)
          .filter((id): id is number => id !== undefined && !Number.isNaN(Number(id)))
      : allRows
          // per row, not the map's keys: a discovery mirror's schedule is
          // keyed by its personalized playlist, not by its id
          .filter((r) => cardSchedules.scheduleFor(r))
          .map((r) => r.id)
          .filter((id): id is number => id !== undefined && !Number.isNaN(Number(id))),
  );

  /**
   * Search narrows BEFORE the tabs count, so "Complete 2" describes what the
   * search left rather than the whole library — a count that ignored the
   * search would send you to a tab that then showed nothing.
   */
  const searchedRows = librarySearch(allRows, query);
  const counts = libraryFilterCounts(searchedRows, sources, scheduledIds);
  const visibleFilters = libraryVisibleFilters(counts, filter);
  // Source chips list every source in the LIBRARY, not in the search result:
  // chips that vanished as you typed would move under the cursor.
  const sourceList = librarySources(allRows);
  const visibleRows = libraryVisibleRows(searchedRows, filter, sources, scheduledIds);
  // the selection, as rows that still exist. a refetch can drop a card from
  // under a selected id; the count and the delete both read this, never the set.
  const selectedRows = selecting ? allRows.filter((r) => selected.has(r.id)) : [];
  const allVisibleSelected = visibleRows.length > 0 && visibleRows.every((r) => selected.has(r.id));

  // the summary the page header reads. primitives only, so the effect fires
  // when something it says changes, not on every render.
  const allCounts = libraryFilterCounts(allRows, new Set(), scheduledIds);
  let nextRunAt: string | null = null;
  for (const r of allRows) {
    const nr = cardSchedules.scheduleFor(r)?.nextRun;
    if (nr && (!nextRunAt || nr < nextRunAt)) nextRunAt = nr;
  }
  const nextRunText = autoSyncNextRunLabel(nextRunAt, now);
  const summaryRef = useRef(onSummary);
  summaryRef.current = onSummary;
  const summaryKey = `${allRows.length}|${allCounts.attention}|${allCounts.running}|${nextRunText}`;
  useEffect(() => {
    if (scope !== 'all' || rows === null) return;
    const [playlists, attention, running, nextRun] = summaryKey.split('|');
    summaryRef.current?.({
      playlists: Number(playlists),
      attention: Number(attention),
      running: Number(running),
      nextRun: nextRun ?? '',
    });
  }, [summaryKey, scope, rows]);

  // the Add sheet's "New playlist" / "New request list"
  // starts at 0, not at the prop: a tab first mounted BY that click arrives
  // already holding the new value and must still open the row
  const lastCreateSignal = useRef(0);
  useEffect(() => {
    if (!createSignal || createSignal === lastCreateSignal.current) return;
    lastCreateSignal.current = createSignal;
    setNewName('');
  }, [createSignal]);

  /** One card, the same wherever it shows (the shelf and the grid). */
  const renderCard = (row: MirroredPlaylistRow, keyPrefix = '') => {
    const displayName = row.display_name || row.name || '';
    // The live phase line, unchanged — its percentages and its Discovered N/M
    // are information the resting meta line cannot carry, so the existing
    // (tested) writer still produces them.
    const hash = mirroredHash(row.id);
    const live = vertical.states[hash];
    const hasLive = Boolean(live?.phase && live.phase !== 'fresh');
    const phaseLine = mirroredPhaseLine(
      hasLive ? live!.phase : pipelinePhaseFor(row),
      hasLive
        ? {
            discoveryProgress: live!.discoveryProgress,
            spotifyMatches: live!.spotifyMatches,
            spotifyTotal: live!.spotifyTotal,
            pipeline_progress: live!.pipeline_progress,
            pipeline_phase: live!.pipeline_phase,
          }
        : {
            pipeline_progress: row.pipeline_state?.progress,
            pipeline_phase: row.pipeline_state?.phase,
          },
      row,
    );
    const livePhase = hasLive ? live!.phase : pipelinePhaseFor(row);
    const running =
      livePhase === 'pipeline_running' ||
      livePhase === 'discovering' ||
      livePhase === 'syncing' ||
      livePhase === 'downloading';
    const progress = running
      ? livePhase === 'discovering'
        ? (live?.discoveryProgress ?? null)
        : ((hasLive ? live!.pipeline_progress : row.pipeline_state?.progress) ?? null)
      : null;
    const exportStatus = exportJobs.statuses[row.id];
    const state = libraryCardState(row);
    // #1289: an all-playlists pipeline schedules this card only if
    // the backend actually processes its source (file is skipped).
    const pipelineCovers = cardSchedules.hasAllPipeline && autoSyncPipelineCoversPlaylist(row);
    return (
      <PlaylistCard
        key={`${keyPrefix}${row.id}`}
        inShelf={Boolean(keyPrefix)}
        row={row}
        name={displayName}
        sourceLabel={isUserPlaylist(row) ? undefined : sourceFilterLabel(row.source ?? '')}
        syncsAs={row.sync_name && row.sync_name !== displayName ? row.sync_name : undefined}
        nameTitle={
          row.custom_name ? `${displayName} — originally "${row.name ?? ''}"` : displayName
        }
        // "Mirrored 30m ago", the vanilla's own wording — it names what the
        // timestamp actually is (the last mirror refresh).
        when={`${userScope ? 'Edited' : 'Mirrored'} ${timeAgo(row.updated_at || row.mirrored_at, Date.now())}`}
        schedule={cardScheduleLabel(cardSchedules.scheduleFor(row), Date.now(), pipelineCovers)}
        scheduled={Boolean(cardSchedules.scheduleFor(row)) || pipelineCovers}
        health={autoSyncPlaylistHealth(cardSchedules.history, row.id)}
        progress={progress}
        live={running || Boolean(exportStatus)}
        status={
          exportStatus ? (
            <ExportStatusSpan status={exportStatus} />
          ) : phaseLine ? (
            <span className="card-phase">{phaseLine.text}</span>
          ) : null
        }
        onOpen={() => onCardClick(row)}
        onPlay={async () => {
          try {
            const data = await fetchMirroredPlaylist(row.id);
            if (data.error) throw new Error(data.error);
            await playPlaylist(data.tracks ?? [], displayName);
          } catch {
            window.showToast?.(`Could not load ${displayName}`, 'error');
          }
        }}
        selecting={selecting}
        selected={selected.has(row.id)}
        onToggleSelect={() => toggleSelected(row.id)}
        onNeedsMatch={() => window.openWingItPoolModal?.(String(row.id))}
        primary={
          // The pipeline endpoint rejects file outright, and lastfm or a
          // frozen beatport mirror cannot be scheduled either — same guard
          // the board uses.
          !autoSyncCanSchedulePlaylist(row)
            ? null
            : {
                label: playlistCardPrimaryLabel(row),
                danger: state === 'error',
                onClick: () => {
                  // "View progress" opens the card; everything else runs the
                  // pipeline, which IS refresh + discover + sync + queue the
                  // missing tracks.
                  if (state === 'working') onViewProgress(row);
                  else void pipeline.run(row.id, row.name ?? '');
                },
              }
        }
        onSchedule={
          autoSyncCanSchedulePlaylist(row)
            ? (anchor) => {
                // Toggles, like the overflow trigger.
                const box = anchor.getBoundingClientRect();
                setSchedMenu((prev) =>
                  prev?.row.id === row.id
                    ? null
                    : { row, top: box.bottom + 6, left: box.left, anchor },
                );
              }
            : undefined
        }
        onMore={(anchor) => {
          // A TOGGLE, not a set: clicking the trigger of an open menu closes it.
          const box = anchor.getBoundingClientRect();
          setMenu((prev) =>
            prev?.row.id === row.id
              ? null
              : { row, top: box.bottom + 6, left: box.right - 188, anchor },
          );
        }}
      />
    );
  };

  // four orders is nothing to choose between on a library of three
  const sortable = allRows.length > 3;
  const sortLabel = sortable ? (LIBRARY_SORTS.find((s) => s.id === sort)?.label ?? 'Sort') : 'Show';
  const filterLabel = visibleFilters.find((f) => f.id === filter)?.label ?? '';
  const sourceActive = sources.size === 1 ? [...sources][0] : '';
  const narrowed = filter !== 'all' || Boolean(sourceActive);
  const chipLabel = [
    filter !== 'all' ? filterLabel : '',
    sourceActive ? sourceFilterLabel(sourceActive) : '',
  ]
    .filter(Boolean)
    .join(' · ');
  // the shelf: the library's Needs attention filter, worst first, on top of
  // the plain list. only on Mirrored at rest, where it answers "what needs me",
  // and only once the library is too long to scan at a glance; on a short one
  // it would just show the same cards twice
  const shelfRows =
    scope === 'all' &&
    allRows.length > SHELF_MIN_LIBRARY &&
    filter === 'all' &&
    !query.trim() &&
    !sourceActive &&
    !selecting
      ? librarySortedRows(
          libraryVisibleRows(searchedRows, 'attention', sources, scheduledIds),
          'state',
        ).slice(0, 12)
      : [];
  const sectionTitle = narrowed
    ? chipLabel
    : requestsScope
      ? 'Request Lists'
      : userScope
        ? 'My Playlists'
        : 'All mirrored';

  return (
    <div>
      <div className={styles.bar}>
        {/* finding one playlist among fifty had no answer but scrolling */}
        {allRows.length > 0 && (
          <PlaylistSearch
            value={query}
            onChange={setQuery}
            className={styles.search}
            icon={
              <svg
                width="18"
                height="18"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.2"
                strokeLinecap="round"
                aria-hidden="true"
              >
                <circle cx="11" cy="11" r="7" />
                <path d="M20 20l-3.6-3.6" />
              </svg>
            }
          />
        )}
        {narrowed && (
          <button
            type="button"
            className={styles.chipClear}
            aria-label={`Clear filter: ${chipLabel}`}
            onClick={() => {
              setFilter('all');
              clearSources();
            }}
          >
            {chipLabel}
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.6"
              strokeLinecap="round"
              aria-hidden="true"
            >
              <path d="M6 6l12 12" />
              <path d="M18 6L6 18" />
            </svg>
          </button>
        )}
        <span className={styles.grow} />
        {/* a toggle: Select opens it, Done closes it and forgets the selection */}
        {/* Select opens the selection bar, whose Done closes it again */}
        {allRows.length > 0 && !selecting && (
          <button
            type="button"
            className={`pl-select-toggle ${styles.txt}`}
            aria-pressed={false}
            onClick={() => setSelecting(true)}
          >
            Select
          </button>
        )}
        {rows !== null && rows.length > 0 && (
          <div className={`${styles.anchor}${viewOpen ? ` ${styles.anchorRaised}` : ''}`}>
            <button
              type="button"
              className={`library-view-btn ${styles.txt}`}
              aria-haspopup="menu"
              aria-expanded={viewOpen}
              onClick={() => setViewOpen((v) => !v)}
            >
              {sortLabel}
              <svg
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.4"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <path d="M6 9l6 6 6-6" />
              </svg>
            </button>
            {viewOpen && (
              <>
                <button
                  type="button"
                  className={styles.menuScrim}
                  aria-label="Close menu"
                  tabIndex={-1}
                  onClick={() => setViewOpen(false)}
                />
                <div className={`library-view-menu ${styles.menu}`} role="menu">
                  {sortable && <div className={styles.menuHead}>Sort by</div>}
                  {(sortable ? LIBRARY_SORTS : []).map((s) => (
                    <button
                      key={s.id}
                      type="button"
                      role="menuitemradio"
                      aria-checked={sort === s.id}
                      className={`${styles.menuItem}${sort === s.id ? ` ${styles.menuItemOn}` : ''}`}
                      onClick={() => {
                        setSort(s.id as LibrarySort);
                        setViewOpen(false);
                      }}
                    >
                      <span>{s.label}</span>
                    </button>
                  ))}
                  {sortable && <div className={styles.menuSep} />}
                  <div className={styles.menuHead}>Show</div>
                  {/* State first: what a user came to find out is which
                      playlists still need something doing to them. */}
                  {visibleFilters.map((f) => (
                    <button
                      key={f.id}
                      type="button"
                      role="menuitemradio"
                      aria-checked={filter === f.id}
                      className={`pl-show-option ${styles.menuItem}${filter === f.id ? ` active ${styles.menuItemOn}` : ''}`}
                      onClick={() => {
                        setFilter(f.id);
                        setViewOpen(false);
                      }}
                    >
                      <span>{f.label}</span>
                      <span className={`pl-show-count ${styles.menuCount}`}>{counts[f.id]}</span>
                    </button>
                  ))}
                  {/* Source is a filter, which is what it always actually was. */}
                  {sourceList.length > 1 && (
                    <>
                      <div className={styles.menuSep} />
                      <div className={styles.menuHead}>From</div>
                      {['', ...sourceList].map((source) => (
                        <button
                          key={source || 'all'}
                          type="button"
                          role="menuitemradio"
                          aria-checked={sourceActive === source}
                          className={`library-source-option ${styles.menuItem}${sourceActive === source ? ` ${styles.menuItemOn}` : ''}`}
                          data-source={source}
                          onClick={() => {
                            if (!source) clearSources();
                            else setSourcesTo(source);
                            setViewOpen(false);
                          }}
                        >
                          <span>{source ? sourceFilterLabel(source) : 'All sources'}</span>
                          <span className={styles.menuCount}>
                            {source
                              ? allRows.filter((r) => r.source === source).length
                              : allRows.length}
                          </span>
                        </button>
                      ))}
                    </>
                  )}
                </div>
              </>
            )}
          </div>
        )}
      </div>

      {shelfRows.length > 0 && (
        <section className={`library-shelf ${styles.sect}`} aria-label="Needs attention">
          <div className={styles.sectHead}>
            <div className={styles.sectHeadText}>
              <h3 className={styles.sectTitle}>Needs attention</h3>
              <p className={styles.sectSub}>
                {counts.attention} playlist{counts.attention === 1 ? '' : 's'} with tracks not found
                or a failed run
              </p>
            </div>
            <button type="button" className={styles.txt} onClick={() => setFilter('attention')}>
              Show all
            </button>
          </div>
          <div className={styles.shelf}>{shelfRows.map((row) => renderCard(row, 'shelf-'))}</div>
        </section>
      )}

      <section className={styles.sect}>
        <div className={styles.sectHead}>
          <div className={`pl-section-head ${styles.sectHeadText}`}>
            <h3 className={styles.sectTitle}>{sectionTitle}</h3>
            {/* Says something TRUE, and omits what it has nothing to say about.
                While searching, the count describes what you are LOOKING at. */}
            <p className={`pl-summary ${styles.sectSub}`}>
              {query.trim() || narrowed
                ? `${visibleRows.length} of ${allRows.length} playlists`
                : librarySummary(rows ?? [])}
            </p>
          </div>
          {(userScope || requestsScope) && (
            <button
              type="button"
              className={`pl-new-btn ${styles.ghost}`}
              onClick={() => setNewName((prev) => (prev === null ? '' : null))}
            >
              {requestsScope ? '+ New request list' : '+ New playlist'}
            </button>
          )}
        </div>

        <div id="mirrored-playlist-container">
          {rows === null || visibleRows.length === 0 ? (
            <div className={`pl-empty ${styles.empty}`}>
              {rows === null
                ? placeholder
                : rows.length === 0
                  ? requestsScope
                    ? 'Request Lists download tracks for your media server without creating a playlist on it. Start one with + New request list, or copy a playlist here from Mirrored or My Playlists.'
                    : userScope
                      ? 'Playlists you make live here. Start one with + New playlist, then add songs with the + on any track.'
                      : 'Playlists you add from any service will appear here.'
                  : query.trim()
                    ? // Naming the query beats "Nothing in this filter", which
                      // leaves you wondering whether it was the search or the tab.
                      `No playlist matches “${query.trim()}”.`
                    : 'Nothing in this filter.'}
            </div>
          ) : (
            <div className={`library-grid ${styles.grid}`}>
              {pinLikedFirst(librarySortedRows(visibleRows, sort)).map((row) => renderCard(row))}
            </div>
          )}
        </div>
      </section>

      {(userScope || requestsScope) && newName !== null && (
        <>
          <button
            type="button"
            className={styles.scrim}
            aria-label="Cancel"
            tabIndex={-1}
            onClick={() => setNewName(null)}
          />
          <form
            className={`pl-create-sheet ${styles.sheet} ${styles.sheetNarrow}`}
            onSubmit={(e) => {
              e.preventDefault();
              void createPlaylist();
            }}
          >
            <div className={styles.sheetHead}>
              <h2 className={styles.sheetTitle}>
                {requestsScope ? 'New request list' : 'New playlist'}
              </h2>
            </div>
            <input
              className={`pl-create-input ${styles.field} ${styles.fieldWide}`}
              autoFocus
              maxLength={200}
              placeholder={requestsScope ? 'Name your request list' : 'Name your playlist'}
              aria-label={requestsScope ? 'New request list name' : 'New playlist name'}
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Escape') setNewName(null);
              }}
            />
            <p className={styles.hint}>
              {requestsScope
                ? 'Downloads its tracks for your media server without creating a playlist on it.'
                : 'Add songs with the + on any track.'}
            </p>
            <div className={styles.foot}>
              <span />
              <span className={styles.footRight}>
                <button
                  type="button"
                  className={`pl-sel-btn ${styles.txt}`}
                  onClick={() => setNewName(null)}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className={`pl-create-btn ${styles.go}`}
                  disabled={!newName.trim()}
                >
                  Create
                </button>
              </span>
            </div>
          </form>
        </>
      )}

      {selecting && (
        /* one bar for the whole selection. "all visible" is the search and
           filter's result, so narrowing to a name and selecting all is the
           whole fix for the duplicate-name case in one gesture. */
        <div
          className={`pl-selection ${styles.selbar}`}
          role="toolbar"
          aria-label="Selected playlists"
        >
          <b className="pl-selection-count">{selectedRows.length} selected</b>
          <button
            type="button"
            className={`pl-sel-btn ${styles.sb}`}
            disabled={visibleRows.length === 0}
            onClick={() => {
              setSelected((prev) => {
                const next = new Set(prev);
                if (allVisibleSelected) visibleRows.forEach((r) => next.delete(r.id));
                else visibleRows.forEach((r) => next.add(r.id));
                return next;
              });
            }}
          >
            {allVisibleSelected ? 'Clear visible' : `Select all visible (${visibleRows.length})`}
          </button>
          {selectedRows.length > 0 && (
            <button
              type="button"
              className={`pl-sel-btn ${styles.sb}`}
              onClick={() => setSelected(new Set())}
            >
              Clear
            </button>
          )}
          <button
            type="button"
            className={`pl-selection-delete ${styles.sb} ${styles.sbDanger}`}
            disabled={selectedRows.length === 0}
            onClick={() => void onDeleteSelected(selectedRows)}
          >
            {selectedRows.length > 0 ? `Delete ${selectedRows.length}` : 'Delete'}
          </button>
          <button
            type="button"
            className={`pl-select-toggle active ${styles.sb} ${styles.sbDark}`}
            aria-pressed
            onClick={exitSelecting}
          >
            Done
          </button>
        </div>
      )}
      {schedMenu && (
        <ScheduleMenu
          row={schedMenu.row}
          hours={cardSchedules.scheduleFor(schedMenu.row)?.hours ?? null}
          weekly={Boolean(cardSchedules.scheduleFor(schedMenu.row)?.weekly)}
          anchor={{ top: schedMenu.top, left: schedMenu.left }}
          anchorEl={schedMenu.anchor}
          onClose={() => setSchedMenu(null)}
          onPickHours={(hours) => void cardSchedules.set(schedMenu.row, hours)}
          onPickWeekly={(days) => void cardSchedules.setWeekly(schedMenu.row, { days })}
          onCustomWeekly={() => {
            const current = cardSchedules.scheduleFor(schedMenu.row)?.weeklyConfig;
            setWeeklyDraft({
              playlistId: Number(schedMenu.row.id),
              days: current?.days ?? ['mon'],
              time: current?.time ?? '09:00',
              tz: current?.tz ?? detectBrowserTimezone(),
            });
          }}
        />
      )}
      {weeklyDraft && (
        /* All that survives of the weekly BOARD. The board rendered the same
           card once per selected day because a calendar has to; a draft in an
           editor does not. */
        <AutoSyncWeeklyEditor
          draft={weeklyDraft}
          playlistName={
            rows?.find((r) => r.id === weeklyDraft.playlistId)?.display_name ??
            rows?.find((r) => r.id === weeklyDraft.playlistId)?.name ??
            ''
          }
          hasExisting={Boolean(
            cardSchedules.scheduleFor(rows?.find((r) => r.id === weeklyDraft.playlistId))?.weekly,
          )}
          onChange={setWeeklyDraft}
          onSave={() => {
            const target = rows?.find((r) => r.id === weeklyDraft.playlistId);
            if (target) {
              void cardSchedules.setWeekly(target, {
                days: weeklyDraft.days,
                time: weeklyDraft.time,
                tz: weeklyDraft.tz,
              });
            }
            setWeeklyDraft(null);
          }}
          onUnschedule={() => {
            const target = rows?.find((r) => r.id === weeklyDraft.playlistId);
            if (target) void cardSchedules.set(target, null);
            setWeeklyDraft(null);
          }}
          onClose={() => setWeeklyDraft(null)}
        />
      )}
      {menu && (
        <MirroredCardMenu
          row={menu.row}
          top={menu.top}
          left={menu.left}
          anchor={menu.anchor}
          onClose={() => setMenu(null)}
          onOpen={() => onCardClick(menu.row)}
          primary={
            autoSyncCanSchedulePlaylist(menu.row)
              ? {
                  label: playlistCardPrimaryLabel(menu.row),
                  onClick: () => {
                    if (libraryCardState(menu.row) === 'working') onViewProgress(menu.row);
                    else void pipeline.run(menu.row.id, menu.row.name ?? '');
                  },
                }
              : null
          }
          onSchedule={
            autoSyncCanSchedulePlaylist(menu.row) ? () => setSchedMenu({ ...menu }) : undefined
          }
          onExport={() => setExporting(menu.row)}
          onClear={() => void onClear(menu.row)}
          onDelete={() => void onDelete(menu.row)}
          onSettings={() => setSettingsFor(menu.row.id)}
          onCopyToMine={() => void copyToMine(menu.row.id)}
          onCopyToRequests={() => void copyToRequests(menu.row.id)}
        />
      )}
      {settingsRow && (
        <PlaylistSettingsSheet
          row={settingsRow}
          name={settingsRow.display_name || settingsRow.name || ''}
          sourceLabel={sourceFilterLabel(settingsRow.source ?? '')}
          scheduleLabel={cardScheduleLabel(
            cardSchedules.scheduleFor(settingsRow),
            Date.now(),
            cardSchedules.hasAllPipeline && autoSyncPipelineCoversPlaylist(settingsRow),
          )}
          onSchedule={
            autoSyncCanSchedulePlaylist(settingsRow)
              ? (anchor) => {
                  const box = anchor.getBoundingClientRect();
                  setSchedMenu({
                    row: settingsRow,
                    top: box.bottom + 6,
                    left: box.left - 160,
                    anchor,
                  });
                }
              : undefined
          }
          onRename={(value) => void renameTo(settingsRow, value)}
          onToggleOrganize={(enabled) => void setOrganize(settingsRow, enabled)}
          onToggleRotating={(enabled) => void setRotating(settingsRow, enabled)}
          onSetSyncMode={(mode) => void setSyncMode(settingsRow, mode)}
          onToggleAutoRemove={() => void toggleAutoRemove(settingsRow)}
          onEditSource={() => {
            setSettingsFor(null);
            setRefEditFromDetail(false);
            setEditingRef(settingsRow);
          }}
          onGenerateCover={() => setCoverArtFor(settingsRow)}
          onClear={
            (settingsRow.discovered_count || 0) > 0 && !isUserPlaylist(settingsRow)
              ? () => {
                  setSettingsFor(null);
                  void onClear(settingsRow);
                }
              : undefined
          }
          onDelete={() => {
            setSettingsFor(null);
            setDetail(null);
            void onDelete(settingsRow);
          }}
          onClose={() => setSettingsFor(null)}
        />
      )}
      {coverArtFor && (
        <CoverArtSheet
          playlistId={coverArtFor.id}
          playlistName={coverArtFor.display_name || coverArtFor.name || 'this playlist'}
          onClose={() => setCoverArtFor(null)}
          // the card art is the cover, so the list refetches to show it
          onApplied={reload}
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
          asPage
          onOpenSettings={() => setSettingsFor(detail.playlistId)}
          onExport={() => {
            const row = rows?.find((r) => r.id === detail.playlistId);
            if (row) {
              setDetail(null);
              setExporting(row);
            }
          }}
          playlistId={detail.playlistId}
          data={detail.data}
          now={now}
          onClose={() => setDetail(null)}
          onFindMatches={() => {
            const id = detail.playlistId;
            setDetail(null);
            window.openWingItPoolModal?.(String(id));
          }}
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
          onRefreshFromSource={() => {
            setDetail(null);
            void pipeline.run(detail.playlistId, detail.data.name ?? '', { refreshOnly: true });
          }}
          onDiscover={() => void runDiscovery(detail.playlistId)}
          discoverLabel={
            mirroredDiscoveryReopenable(vertical.states[mirroredHash(detail.playlistId)])
              ? 'View identification'
              : 'Identify'
          }
          onRemoveTrack={
            isUserPlaylist(detail.data)
              ? (position) =>
                  void editTracks(detail.playlistId, () =>
                    removeFromUserPlaylist(detail.playlistId, position),
                  )
              : undefined
          }
          onReorder={
            isUserPlaylist(detail.data)
              ? (order) =>
                  void editTracks(detail.playlistId, () =>
                    reorderUserPlaylist(detail.playlistId, order),
                  )
              : undefined
          }
          onRefresh={() => editTracks(detail.playlistId, async () => {})}
          onMakeMine={isUserPlaylist(detail.data) ? undefined : () => copyToMine(detail.playlistId)}
          onOpenEditor={
            onOpenServerEditor && !isUserPlaylist(detail.data)
              ? async () => {
                  const id = detail.playlistId;
                  if (await onOpenServerEditor(id, detail.data.name)) {
                    setDetail((cur) => (cur?.playlistId === id ? null : cur));
                  }
                }
              : undefined
          }
          onDuplicate={
            isUserPlaylist(detail.data)
              ? async () => {
                  try {
                    const copy = await duplicateUserPlaylist(detail.playlistId);
                    const data = await fetchMirroredPlaylist(copy.id);
                    if (!data.error) {
                      setDetail({ playlistId: copy.id, data });
                    }
                    window.showToast?.(`Duplicated as “${copy.name}”`, 'success');
                  } catch (err) {
                    window.showToast?.(
                      err instanceof Error ? err.message : 'Could not duplicate the playlist',
                      'error',
                    );
                  }
                  void load();
                }
              : undefined
          }
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
