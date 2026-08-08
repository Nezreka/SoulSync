/**
 * The sync page — the assembly. `useSyncPage()` owns the wiring; this owns the
 * markup and the panel map.
 *
 * BEATPORT IS DELIBERATELY ABSENT from `panels`, and that is not an oversight.
 * The panel map in the dossier lists it as "the Beatport*Section set", but the
 * vanilla tab (index.html 2395+) is a sub-shell of its own: two inner tabs (My
 * Playlists / Rebuild), a hero slider with its own nav and indicators, three
 * nav buttons that switch to a genre browser and two Top-100 views, a download
 * bubble region, and then the section stack. Every piece exists as a component;
 * the composition and its view state do not, and inventing them here would mean
 * guessing a layout rather than transcribing one. `SyncShell` renders an empty
 * panel for a tab with no entry, so the gap is VISIBLE rather than subtly
 * wrong — which is the point. It is the next slice.
 *
 * Two panels are views, not lists. The server tab swaps between the playlist
 * list and the compare editor; that state lives here because `ServerPlaylistList`
 * signals `onOpenCompare` and has no idea what a compare view is, and
 * `ServerCompareEditor` had no mount site anywhere before this file.
 */

import { useCallback, useMemo, useRef, useState } from 'react';

import type { LbCardData } from '../-sync.lb-tabs';
import type { MirroredMatch, ServerPlaylist } from '../-sync.server';
import type { SyncTabId } from '../-sync.shell';

import { metadataSourceLabel } from '../-sync.modal-core';
import { useAutoSync } from '../-sync.use-autosync';
import { useSyncPage } from '../-sync.use-page';
import { QobuzTab, TidalTab } from './account-tab';
import { DeezerArlTab, SpotifyTab } from './account-tabs';
import { AutoSyncModal } from './autosync-modal';
import { ImportFileTab } from './import-file-tab';
import { LastfmSyncTab } from './lastfm-sync-tab';
import { ListenBrainzSyncTab } from './lb-sync-tab';
import { MirroredTab } from './mirrored-tab';
import { ServerCompareEditor } from './server-compare-editor';
import { ServerPlaylistList } from './server-playlist-list';
import { SoulsyncDiscoveryTab } from './soulsync-discovery-tab';
import { SyncModals } from './sync-modals';
import { SyncShell } from './sync-shell';
import { SyncSidebar } from './sync-sidebar';
import { DeezerLinkTab, ITunesLinkTab, SpotifyPublicTab, YouTubeTab } from './url-import-tab';

/** The server tab's two views (2226-2236 vs the compare editor). */
interface CompareView {
  playlist: ServerPlaylist;
  mirrored: MirroredMatch | null;
}

export function SyncPage() {
  const page = useSyncPage();
  const [autoSyncOpen, setAutoSyncOpen] = useState(false);
  const [compare, setCompare] = useState<CompareView | null>(null);

  /**
   * The shell's tab opener, filled on its mount. Only the import tab needs it,
   * and only after a successful write.
   */
  const openTab = useRef<((tab: SyncTabId) => void) | undefined>(undefined);
  const registerOpenTab = useCallback((open: (tab: SyncTabId) => void) => {
    openTab.current = open;
  }, []);

  /**
   * `runPipeline` is the page's ONE controller, injected rather than reached
   * for on `window` — `runMirroredPlaylistPipeline` lives in auto-sync.js,
   * which the flip deletes.
   */
  const autoSync = useAutoSync({ open: autoSyncOpen, runPipeline: page.pipeline.run });

  /**
   * The live metadata-source name. The vanilla interpolates
   * `currentMusicSourceName`, which is declared 'Spotify' at core.js 15 and
   * never assigned — so its labels always said Spotify whatever the configured
   * source was. `metadataSourceLabel()` is the knowing fix already made for the
   * discovery modal's headers; using it here keeps ONE answer to "what is the
   * metadata source called" rather than two that disagree.
   */
  const sourceName = metadataSourceLabel();

  /**
   * THE TRAP: both groups extend AutoSyncCardActions, so three of their four
   * members are identical — and the fourth, `onUnschedule`, must bind to a
   * DIFFERENT function in each. Wire both to the same one and unscheduling a
   * weekly playlist leaves its weekly automation running while the UI says it
   * is gone; that is the one-schedule-per-playlist invariant this port has
   * already fixed twice in the vanilla.
   */
  const boardActions = useMemo(
    () => ({
      onRun: autoSync.runNow,
      onUnschedule: autoSync.unscheduleHourly,
      onOrganizeChange: autoSync.setOrganize,
      onDrop: autoSync.saveHourly,
    }),
    [autoSync.runNow, autoSync.unscheduleHourly, autoSync.setOrganize, autoSync.saveHourly],
  );

  const weeklyActions = useMemo(
    () => ({
      onRun: autoSync.runNow,
      onUnschedule: autoSync.unscheduleWeekly,
      onOrganizeChange: autoSync.setOrganize,
      onSave: autoSync.saveWeekly,
    }),
    [autoSync.runNow, autoSync.unscheduleWeekly, autoSync.setOrganize, autoSync.saveWeekly],
  );

  const openSourceModal = page.modals.openModal;
  const openLbCard = useCallback(
    // The vertical applies its own `listenbrainz_` prefix downstream, so the
    // page passes the BARE mbid. Last.fm radios live in the same table and
    // share the LB vertical (sync-lastfm.js) — one source id, two tabs.
    (card: LbCardData) => openSourceModal('listenbrainz', card.mbid),
    [openSourceModal],
  );

  const onImported = useCallback(() => {
    // importFileSubmit's tail (449-455): show the mirrored tab, then reload it.
    openTab.current?.('mirrored');
    page.reloadMirrored();
  }, [page]);

  const panels: Partial<Record<SyncTabId, React.ReactNode>> = {
    server: compare ? (
      <ServerCompareEditor
        playlist={compare.playlist}
        mirrored={compare.mirrored}
        onBack={() => setCompare(null)}
      />
    ) : (
      <ServerPlaylistList
        onOpenCompare={(playlist, mirrored) => setCompare({ playlist, mirrored })}
      />
    ),
    spotify: (
      <SpotifyTab
        selectedIds={page.selection.selected}
        onToggleSelect={page.selection.toggle}
        registerRows={page.registerSpotifyRows}
      />
    ),
    'spotify-public': (
      <SpotifyPublicTab
        vertical={page.verticals.spotify_public}
        onOpen={(sourceId) => openSourceModal('spotify_public', sourceId)}
      />
    ),
    'itunes-link': (
      <ITunesLinkTab
        vertical={page.verticals.itunes_link}
        onOpen={(sourceId) => openSourceModal('itunes_link', sourceId)}
      />
    ),
    tidal: (
      <TidalTab
        vertical={page.verticals.tidal}
        onOpen={(sourceId) => openSourceModal('tidal', sourceId)}
      />
    ),
    qobuz: (
      <QobuzTab
        vertical={page.verticals.qobuz}
        onOpen={(sourceId) => openSourceModal('qobuz', sourceId)}
      />
    ),
    deezer: <DeezerArlTab />,
    // The link variant is the same SERVICE: one config, one vertical, one
    // modal id space. Only the way a playlist arrives differs.
    'deezer-link': (
      <DeezerLinkTab
        vertical={page.verticals.deezer}
        onOpen={(sourceId) => openSourceModal('deezer', sourceId)}
      />
    ),
    youtube: (
      <YouTubeTab
        vertical={page.verticals.youtube}
        onOpen={(sourceId) => openSourceModal('youtube', sourceId)}
      />
    ),
    'listenbrainz-sync': (
      <ListenBrainzSyncTab vertical={page.verticals.listenbrainz} onOpen={openLbCard} />
    ),
    'lastfm-sync': <LastfmSyncTab vertical={page.verticals.listenbrainz} onOpen={openLbCard} />,
    'soulsync-discovery-sync': <SoulsyncDiscoveryTab />,
    'import-file': <ImportFileTab onImported={onImported} />,
    mirrored: (
      <MirroredTab
        vertical={page.verticals.mirrored}
        onOpen={(sourceId) => openSourceModal('mirrored', sourceId)}
        sourceName={sourceName}
        pipeline={page.pipeline}
        registerReload={page.registerMirroredReload}
      />
    ),
  };

  return (
    <>
      <SyncShell
        panels={panels}
        onAutoSync={() => setAutoSyncOpen(true)}
        sidebar={
          <SyncSidebar
            state={page.actions}
            onStartSync={page.onStartSync}
            visible={page.sidebarVisible}
          />
        }
        sidebarVisible={page.sidebarVisible}
        onTabChange={page.onTabChange}
        registerOpenTab={registerOpenTab}
      />

      <SyncModals
        verticals={page.verticals}
        modals={page.modals}
        standalone={page.standalone}
        mirroredSource={sourceName}
      />

      {autoSyncOpen ? (
        <AutoSyncModal
          state={autoSync.state}
          loading={autoSync.loading}
          loadError={autoSync.loadError}
          now={autoSync.now}
          historyFilter={autoSync.historyFilter}
          onHistoryFilterChange={autoSync.setHistoryFilter}
          onLoadMoreHistory={autoSync.loadMoreHistory}
          onRefresh={autoSync.refresh}
          onClose={() => setAutoSyncOpen(false)}
          boardActions={boardActions}
          weeklyActions={weeklyActions}
          onBulkSchedule={autoSync.bulkSchedule}
          onBulkUnschedule={autoSync.bulkUnschedule}
          // The monitor's Details button (auto-sync.js 1180) opens the shared
          // vanilla mirrored modal, which five vanilla files call and the flip
          // does not delete. MirroredTab's own React detail modal is internal
          // to that tab, so this is the vanilla's own target, not a second one.
          onOpenDetails={(playlistId) => window.openMirroredPlaylistModal?.(playlistId)}
          // runNow takes only the id; the modal offers the name too. Dropping
          // it here is the adapter, not a lost argument.
          onRunAgain={(playlistId) => autoSync.runNow(playlistId)}
        />
      ) : null}
    </>
  );
}
