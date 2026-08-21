/**
 * The sync page — the assembly. `useSyncPage()` owns the wiring; this owns the
 * markup and the panel map.
 *
 * CORRECTION — Beatport. This header previously said the tab was "a sub-shell
 * of its own: two inner tabs (My Playlists / Rebuild) … three nav buttons that
 * switch to a genre browser and two Top-100 views". That was inferred from a
 * skim and the read disproved all of it. The inner tab strip is
 * `style="display: none"` with its own comment saying Browse is the only view,
 * so there is ONE reachable pane; and the two Top-100 buttons do not switch
 * views at all, they scrape and open the download modal. `BeatportTab` is that
 * one pane. The lesson is the usual one: the skim invented structure the
 * vanilla does not have.
 *
 * Two panels are views, not lists. The server tab swaps between the playlist
 * list and the compare editor; that state lives here because `ServerPlaylistList`
 * signals `onOpenCompare` and has no idea what a compare view is, and
 * `ServerCompareEditor` had no mount site anywhere before this file.
 */

import { useCallback, useMemo, useRef, useState } from 'react';

import type { LbCardData } from '../-sync.lb-tabs';
import type { MirroredMatch, ServerPlaylist } from '../-sync.server';
import { normalizeSyncTab } from '../-sync.shell';
import type { SyncTabId } from '../-sync.shell';

import { metadataSourceLabel } from '../-sync.modal-core';
import { useAutoSync } from '../-sync.use-autosync';
import { useSyncHistory } from '../-sync.use-history';
import { useSyncPage } from '../-sync.use-page';
import { QobuzTab, TidalTab } from './account-tab';
import { DeezerArlTab, SpotifyTab } from './account-tabs';
import { ActivityModal, type ActivityTab } from './activity-modal';
import { AutoSyncModal } from './autosync-modal';
import { BeatportTab } from './beatport-tab';
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
import { AddPlaylistSheet } from './add-playlist-sheet';
import { DeezerLinkTab, ITunesLinkTab, SpotifyPublicTab, YouTubeTab } from './url-import-tab';

/** The server tab's two views (2226-2236 vs the compare editor). */
interface CompareView {
  playlist: ServerPlaylist;
  mirrored: MirroredMatch | null;
}

/**
 * The legacy mirrored detail modal. This page no longer opens it — the React
 * modal is used everywhere here — but the declaration stays for the one
 * fallback below, and because other pages still call the vanilla function.
 */
declare global {
  interface Window {
    openMirroredPlaylistModal?: (playlistId: number) => Promise<void> | void;
  }
}

export function SyncPage() {
  const page = useSyncPage();
  const [autoSyncOpen, setAutoSyncOpen] = useState(false);
  const [activityOpen, setActivityOpen] = useState(false);
  const [activityTab, setActivityTab] = useState<ActivityTab>('syncs');
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
   * The Add-playlist sheet, and the link it routed.
   *
   * The sheet only DECIDES which tab a link belongs to; the tab still loads it,
   * so the four bespoke loaders are untouched. The pending link is held here
   * because the tab that must parse it may not be mounted at the moment the
   * sheet closes — opening it is what mounts it.
   */
  const [addAnchor, setAddAnchor] = useState<{ top: number; left: number } | null>(null);
  const [pending, setPending] = useState<{ tab: string; url: string } | null>(null);
  const routeAdd = useCallback((tab: string, url?: string) => {
    if (url) setPending({ tab, url });
    openTab.current?.(normalizeSyncTab(tab));
  }, []);
  const clearPending = useCallback(() => {
    setPending(null);
  }, []);

  /**
   * The mirrored tab's detail-modal opener.
   *
   * Everything on this page opens the SAME modal now. The legacy
   * `openMirroredPlaylistModal` is a second implementation over the same
   * endpoint with different visuals, and the Auto-Sync monitor and the
   * SoulSync Discovery tab were reaching for it — so the same playlist looked
   * different depending on which button you pressed. The vanilla function is
   * left alive because other PAGES still call it.
   */
  const openMirroredDetail = useRef<((playlistId: number) => void) | undefined>(undefined);
  const registerOpenDetail = useCallback((open: (playlistId: number) => void) => {
    openMirroredDetail.current = open;
  }, []);
  const showMirroredDetail = useCallback((playlistId: number) => {
    // Mirrored is the default tab, so the opener is registered before anything
    // can ask for it. The fallback covers a future where it is not.
    if (openMirroredDetail.current) openMirroredDetail.current(playlistId);
    else void window.openMirroredPlaylistModal?.(playlistId);
  }, []);
  /** The pending url, but only for the tab it was meant for. */
  const pendingFor = useCallback(
    (tab: string) => (pending && pending.tab === tab ? pending.url : undefined),
    [pending],
  );

  /**
   * `runPipeline` is the page's ONE controller, injected rather than reached
   * for on `window` — `runMirroredPlaylistPipeline` lives in auto-sync.js,
   * which the flip deletes.
   */
  /**
   * Open for the Auto-Sync modal OR for Activity's scheduled-runs tab, which
   * reads the same run history. Leaving it closed there would show an empty
   * list on a tab whose whole job is that list.
   */
  const autoSync = useAutoSync({
    open: autoSyncOpen || (activityOpen && activityTab === 'runs'),
    runPipeline: page.pipeline.run,
  });
  const syncHistory = useSyncHistory({
    active: activityOpen && activityTab === 'syncs',
    // The download-type re-sync path is the vanilla download modal, which this
    // port does not own and does not replace.
    openDownloadModal: async (entry) => {
      setActivityOpen(false);
      await window.openDownloadMissingModalForArtistAlbum?.(
        String(entry.playlist_id ?? `resync_${entry.id}`),
        entry.playlist_name ?? '',
        entry.tracks ?? [],
        entry.album_context ?? {
          id: `resync_album_${entry.id}`,
          name: entry.playlist_name,
          album_type: entry.sync_type === 'album' ? 'album' : 'compilation',
          images: entry.thumb_url ? [{ url: entry.thumb_url }] : [],
          total_tracks: entry.total_tracks,
        },
        entry.artist_context ?? { id: 'resync_artist', name: 'Various Artists' },
        false,
        entry.sync_type === 'album' ? 'artist_album' : 'playlist',
      );
    },
  });

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
        pendingUrl={pendingFor('spotify-public')}
        onPendingConsumed={clearPending}
      />
    ),
    'itunes-link': (
      <ITunesLinkTab
        vertical={page.verticals.itunes_link}
        onOpen={(sourceId) => openSourceModal('itunes_link', sourceId)}
        pendingUrl={pendingFor('itunes-link')}
        onPendingConsumed={clearPending}
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
    beatport: <BeatportTab />,
    deezer: <DeezerArlTab />,
    // The link variant is the same SERVICE: one config, one vertical, one
    // modal id space. Only the way a playlist arrives differs.
    'deezer-link': (
      <DeezerLinkTab
        vertical={page.verticals.deezer}
        onOpen={(sourceId) => openSourceModal('deezer', sourceId)}
        pendingUrl={pendingFor('deezer-link')}
        onPendingConsumed={clearPending}
      />
    ),
    youtube: (
      <YouTubeTab
        vertical={page.verticals.youtube}
        onOpen={(sourceId) => openSourceModal('youtube', sourceId)}
        pendingUrl={pendingFor('youtube')}
        onPendingConsumed={clearPending}
      />
    ),
    'listenbrainz-sync': (
      <ListenBrainzSyncTab vertical={page.verticals.listenbrainz} onOpen={openLbCard} />
    ),
    'lastfm-sync': <LastfmSyncTab vertical={page.verticals.listenbrainz} onOpen={openLbCard} />,
    'soulsync-discovery-sync': <SoulsyncDiscoveryTab onOpenMirrored={showMirroredDetail} />,
    'import-file': <ImportFileTab onImported={onImported} />,
    mirrored: (
      <MirroredTab
        vertical={page.verticals.mirrored}
        onOpen={(sourceId) => openSourceModal('mirrored', sourceId)}
        sourceName={sourceName}
        pipeline={page.pipeline}
        registerReload={page.registerMirroredReload}
        registerOpenDetail={registerOpenDetail}
      />
    ),
  };

  return (
    <>
      {addAnchor && (
        <AddPlaylistSheet
          anchor={addAnchor}
          onRoute={routeAdd}
          onClose={() => setAddAnchor(null)}
        />
      )}
      <SyncShell
        panels={panels}
        onAddPlaylist={setAddAnchor}
        onAutoSync={() => setAutoSyncOpen(true)}
        onActivity={() => setActivityOpen(true)}
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

      <ActivityModal
        open={activityOpen}
        tab={activityTab}
        onTab={setActivityTab}
        onClose={() => setActivityOpen(false)}
        now={autoSync.now}
        failedRuns={autoSync.state.runHistory.filter((r) => r.status === 'error').length}
        syncs={{
          entries: syncHistory.entries,
          stats: syncHistory.stats,
          total: syncHistory.total,
          page: syncHistory.page,
          pageSize: syncHistory.pageSize,
          source: syncHistory.source,
          loading: syncHistory.loading,
          error: syncHistory.error,
          resyncs: syncHistory.resyncs,
          onSelectSource: syncHistory.selectSource,
          onPage: syncHistory.goToPage,
          onResync: syncHistory.resync,
          onCancel: syncHistory.cancel,
          onDelete: syncHistory.remove,
        }}
        runs={{
          history: autoSync.state.runHistory,
          total: autoSync.state.runHistoryTotal,
          filter: autoSync.historyFilter,
          onFilterChange: autoSync.setHistoryFilter,
          onLoadMore: autoSync.loadMoreHistory,
          onRunAgain: (playlistId) => autoSync.runNow(playlistId),
        }}
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
          onOpenDetails={showMirroredDetail}
          // runNow takes only the id; the modal offers the name too. Dropping
          // it here is the adapter, not a lost argument.
          onRunAgain={(playlistId) => autoSync.runNow(playlistId)}
        />
      ) : null}
    </>
  );
}
