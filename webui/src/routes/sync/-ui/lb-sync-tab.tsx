/**
 * The ListenBrainz sync tab (sync-listenbrainz.js, whole file) and its
 * Last.fm Radio sibling's shared card list. Three JSPF categories behind
 * sub-tabs; cards read the shared listenbrainz vertical (Last.fm radios live
 * in the same table and state machine, playlist_type='lastfm_radio').
 *
 * The click flow diverges from the vanilla ON PURPOSE: the vanilla handed off
 * to openDownloadModalForListenBrainzPlaylist, whose downstream fills the
 * script-scoped registries and opens the VANILLA modal. Here the click
 * fetches + caches tracks (the same on-demand fetch, 175-195), seeds the
 * React vertical, and opens the React DiscoveryModal — the same
 * discover→sync→download machine, one implementation. The vanilla's 500ms
 * card refresh loop is gone: cards re-render when the vertical's state does.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import type { LbCardData, LbSubTabType, LbTrack } from '../-sync.lb-tabs';
import type { SourceVertical } from '../-sync.use-vertical';

import {
  LB_EMPTY_MESSAGES,
  LB_SUB_TABS,
  fetchLbCategories,
  fetchLbPlaylistTracks,
  lbCardProgressLine,
  postLbCacheRefresh,
  unwrapJspfPlaylist,
} from '../-sync.lb-tabs';
import { SYNC_SOURCES } from '../-sync.sources';
import { freshSourceState } from '../-sync.state';
import { SourceCard } from './source-card';

/** The shared card list both MB-style tabs render. */
export function LbCardList({
  cards,
  vertical,
  icon,
  cardClassName,
  idPrefix,
  onOpen,
}: {
  cards: LbCardData[];
  vertical: SourceVertical;
  icon: string;
  cardClassName: string;
  idPrefix: string;
  onOpen: (card: LbCardData) => void;
}) {
  return (
    <>
      {cards.map((card) => {
        const state =
          vertical.states[card.mbid] ?? freshSourceState(SYNC_SOURCES.listenbrainz, card.mbid);
        return (
          <SourceCard
            key={card.mbid}
            id={`${idPrefix}-${card.mbid}`}
            cardClassName={cardClassName}
            icon={icon}
            name={card.title}
            countText={`${card.count} tracks`}
            owner={`by ${card.creator}`}
            phase={state.phase}
            progressLine={lbCardProgressLine(state)}
            onClick={() => onOpen(card)}
          />
        );
      })}
    </>
  );
}

/**
 * The on-demand track fetch + seed + open (handleListenBrainzSyncCardClick
 * 159-218), with the module-level tracks cache the vanilla kept in
 * listenbrainzTracksCache.
 */
export function useLbCardOpen(
  vertical: SourceVertical,
  setOpenId: (mbid: string) => void,
): (card: LbCardData) => Promise<void> {
  const tracksCache = useRef<Record<string, LbTrack[]>>({});
  return useCallback(
    async (card: LbCardData) => {
      if (!card.mbid) {
        window.showToast?.('Missing playlist ID', 'error');
        return;
      }
      try {
        window.showLoadingOverlay?.(`Loading ${card.title}...`);
        let tracks = tracksCache.current[card.mbid];
        if (!tracks || tracks.length === 0) {
          tracks = await fetchLbPlaylistTracks(card.mbid);
          tracksCache.current[card.mbid] = tracks;
        }
        if (!tracks || tracks.length === 0) throw new Error('Playlist has no tracks');
        window.hideLoadingOverlay?.();
        vertical.seed(card.mbid, { name: card.title, tracks });
        setOpenId(card.mbid);
      } catch (error) {
        window.hideLoadingOverlay?.();
        window.showToast?.(
          `Could not open playlist: ${error instanceof Error ? error.message : 'unknown error'}`,
          'error',
        );
      }
    },
    [vertical, setOpenId],
  );
}

export function ListenBrainzSyncTab({
  vertical,
  onOpen,
}: {
  vertical: SourceVertical;
  onOpen: (card: LbCardData) => void;
}) {
  const [subTab, setSubTab] = useState<LbSubTabType>('created_for_user');
  const [byType, setByType] = useState<Record<LbSubTabType, unknown[]> | null>(null);
  const [placeholder, setPlaceholder] = useState('🔄 Loading ListenBrainz playlists...');
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    setPlaceholder('🔄 Loading ListenBrainz playlists...');
    setByType(null);
    setRefreshing(true);
    try {
      const categories = await fetchLbCategories();
      if (categories.unauthenticated) {
        setPlaceholder(
          'ListenBrainz not connected. Add your token in Settings → Connections to see your playlists here.',
        );
        return;
      }
      setByType(categories.playlists);
    } catch (error) {
      const message = error instanceof Error ? error.message : 'unknown error';
      setPlaceholder(`❌ Error loading ListenBrainz playlists: ${message}`);
      window.showToast?.(`Error loading ListenBrainz playlists: ${message}`, 'error');
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const cards = (byType?.[subTab] ?? []).map((entry) =>
    unwrapJspfPlaylist(entry, { title: 'ListenBrainz Playlist', creator: 'ListenBrainz' }),
  );

  return (
    <div>
      {/* The sub-tabs live INSIDE the header row (index.html 3202-3210) —
          .listenbrainz-sub-tabs is inline-flex with margin-left:16px
          (style.css 14864), so as a sibling they'd drop to their own line. */}
      <div className="playlist-header">
        <h3>Your ListenBrainz Playlists</h3>
        <div className="listenbrainz-sub-tabs">
          {LB_SUB_TABS.map((tab) => (
            <button
              key={tab.type}
              type="button"
              className={`listenbrainz-sub-tab-btn ${subTab === tab.type ? 'active' : ''}`}
              data-lb-type={tab.type}
              onClick={() => setSubTab(tab.type)}
            >
              {tab.label}
            </button>
          ))}
        </div>
        <button
          type="button"
          className="refresh-button listenbrainz"
          id="listenbrainz-sync-refresh-btn"
          disabled={refreshing}
          onClick={() => {
            // Backend refetch first (best-effort), then reload the caches (348-356).
            void postLbCacheRefresh().then(load);
          }}
        >
          {refreshing ? '🔄 Loading...' : '🔄 Refresh'}
        </button>
      </div>
      <div className="playlist-scroll-container">
        {byType === null || cards.length === 0 ? (
          <div className="playlist-placeholder">
            {byType === null ? placeholder : LB_EMPTY_MESSAGES[subTab]}
          </div>
        ) : (
          <LbCardList
            cards={cards}
            vertical={vertical}
            icon="🎧"
            cardClassName="listenbrainz-playlist-card"
            idPrefix="listenbrainz-sync-card"
            onOpen={onOpen}
          />
        )}
      </div>
    </div>
  );
}
