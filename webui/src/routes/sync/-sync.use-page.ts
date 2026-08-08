/**
 * The sync page's controller: everything the page needs, assembled once.
 *
 * Split from the JSX deliberately. The wiring is where this port's failures
 * have lived — a second pipeline controller, an unreachable global, a superset
 * standing in for display order — and none of them are visible in markup. The
 * panel mounting is mechanical by comparison, so it stays in the component and
 * this stays testable on its own.
 *
 * THE ENGINE IS RESOLVED AT CALL TIME, not at mount. Every member reads its
 * `window` function inside the closure, so a page that mounts before the
 * vanilla scripts finish still works, and a missing global is a no-op rather
 * than a crash at construction. `isSyncing` and the playlist list are the two
 * that needed accessors added (core.js) — `activeSyncPollers` and
 * `spotifyPlaylists` are top-level `let`s and have no window property at all.
 */

import { useCallback, useMemo, useRef, useState } from 'react';

import type { SyncActionsState } from './-sync.sidebar';
import type { SyncModals } from './-sync.use-modals';
import type { PipelineController } from './-sync.use-pipeline';
import type { SyncSelection } from './-sync.use-selection';
import type { SequentialSyncEngine } from './-sync.use-sequential';
import type { SyncVerticals } from './-sync.verticals';

import { mirroredPipelineStateWriter } from './-sync.mirrored';
import { syncSidebarVisible } from './-sync.sidebar';
import { useSyncModals } from './-sync.use-modals';
import { useMirroredPipeline } from './-sync.use-pipeline';
import { useSyncSelection } from './-sync.use-selection';
import { useSequentialSync } from './-sync.use-sequential';
import { useStandalone } from './-sync.use-standalone';
import { useSyncVerticals } from './-sync.verticals';

export interface SyncPage {
  verticals: SyncVerticals;
  selection: SyncSelection;
  modals: SyncModals;
  /** The page's ONE controller — see -sync.use-pipeline and S3b-i. */
  pipeline: PipelineController;
  standalone: boolean;
  /** What the sidebar renders. */
  actions: SyncActionsState;
  /** Playlist selection is frozen for the duration of a run. */
  locked: boolean;
  /** Start Sync's single toggle. */
  onStartSync: () => void;
  sidebarVisible: boolean;
  /** The shell calls this on every tab switch — the vanilla re-hides there. */
  onTabChange: () => void;
  /** Handed to MirroredTab. */
  registerMirroredReload: (reload: () => void) => void;
  /** Handed to SpotifyTab. */
  registerSpotifyRows: (playlistIds: string[]) => void;
}

export function useSyncPage(): SyncPage {
  const standalone = useStandalone();
  const selection = useSyncSelection();
  const modals = useSyncModals();

  /** The Spotify tab's rendered order — the sequential queue's order. */
  const spotifyOrder = useRef<string[]>([]);
  const registerSpotifyRows = useCallback((playlistIds: string[]) => {
    spotifyOrder.current = playlistIds;
  }, []);

  /** The mirrored tab's row refetch, filled on its mount. */
  const mirroredReload = useRef<(() => void) | undefined>(undefined);
  const registerMirroredReload = useCallback((reload: () => void) => {
    mirroredReload.current = reload;
  }, []);

  const verticals = useSyncVerticals();

  const onPipelineState = useMemo(
    () => mirroredPipelineStateWriter(verticals.mirrored),
    [verticals.mirrored],
  );
  const reloadMirrored = useCallback(() => mirroredReload.current?.(), []);
  const pipeline = useMirroredPipeline({ onState: onPipelineState, reload: reloadMirrored });

  /**
   * Names come from the ENGINE's array, which is what `updateUI` resolves
   * against (core.js 1413) — including its 'Unknown' fallback for anything it
   * cannot find. ORDER does not: that array is never pruned and also holds
   * virtual playlists, so it is a superset of what is on screen.
   */
  const nameFor = useCallback((playlistId: string) => {
    const rows = window.getSyncAccountPlaylists?.() ?? [];
    const match = rows.find((row) => String(row.id) === playlistId);
    return match?.name;
  }, []);

  const engine = useMemo<SequentialSyncEngine>(
    () => ({
      startPlaylistSync: async (playlistId) => {
        await window.startPlaylistSync?.(playlistId);
      },
      isSyncing: (playlistId) => Boolean(window.isPlaylistSyncing?.(playlistId)),
      setSelectionDisabled: (disabled) => window.disablePlaylistSelection?.(disabled),
      refreshButtons: () => window.updateRefreshButtonState?.(),
      toast: (message, kind) => window.showToast?.(message, kind),
    }),
    [],
  );

  const sequential = useSequentialSync({
    engine,
    selectedCount: selection.count,
    nameFor,
  });

  /**
   * The vanilla's tab handler re-hides the sidebar on EVERY switch, without
   * asking whether a run is in progress (sync-services.js 3759). Transcribed:
   * switching tabs mid-sync drops the progress panel until the next run.
   */
  const [hiddenByTabSwitch, setHiddenByTabSwitch] = useState(false);
  const onTabChange = useCallback(() => {
    setHiddenByTabSwitch(true);
  }, []);

  const onStartSync = useCallback(() => {
    // A start un-hides: showSyncSidebar runs on every start (downloads.js 4092).
    setHiddenByTabSwitch(false);
    sequential.toggle(spotifyOrder.current, selection.selected);
  }, [sequential, selection.selected]);

  return {
    verticals,
    selection,
    modals,
    pipeline,
    standalone,
    actions: sequential.actions,
    locked: sequential.locked,
    onStartSync,
    sidebarVisible: syncSidebarVisible(sequential.state.running, hiddenByTabSwitch),
    onTabChange,
    registerMirroredReload,
    registerSpotifyRows,
  };
}
