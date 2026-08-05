/**
 * The Last.fm Radio sync tab (sync-lastfm.js, whole file) — intentionally
 * thin: the radios live in the same listenbrainz_playlists table
 * (playlist_type='lastfm_radio') and share the LB vertical, card list and
 * click flow; only the fetch, copy and chrome differ. Generation stays on
 * the Discover page — this tab lists + syncs.
 */

import { useCallback, useEffect, useState } from 'react';

import type { LbCardData } from '../-sync.lb-tabs';
import type { SourceVertical } from '../-sync.use-vertical';

import { fetchLastfmRadios, unwrapJspfPlaylist } from '../-sync.lb-tabs';
import { LbCardList } from './lb-sync-tab';

export function LastfmSyncTab({
  vertical,
  onOpen,
}: {
  vertical: SourceVertical;
  onOpen: (card: LbCardData) => void;
}) {
  const [playlists, setPlaylists] = useState<unknown[] | null>(null);
  const [placeholder, setPlaceholder] = useState('🔄 Loading Last.fm Radio playlists...');
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    setPlaceholder('🔄 Loading Last.fm Radio playlists...');
    setPlaylists(null);
    setRefreshing(true);
    try {
      const { playlists: radios, error } = await fetchLastfmRadios();
      if (error) {
        setPlaceholder(`❌ ${error}`);
        return;
      }
      setPlaylists(radios);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'unknown error';
      setPlaceholder(`❌ Error loading Last.fm radios: ${message}`);
      window.showToast?.(`Error loading Last.fm radios: ${message}`, 'error');
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const cards = (playlists ?? []).map((entry) =>
    unwrapJspfPlaylist(
      entry,
      { title: 'Last.fm Radio', creator: 'Last.fm' },
      { nameFallback: false },
    ),
  );

  return (
    <div>
      <div className="playlist-header">
        <h3>Last.fm Radio</h3>
        <button
          type="button"
          className="refresh-button"
          disabled={refreshing}
          onClick={() => void load()}
        >
          {refreshing ? '🔄 Loading...' : '🔄 Refresh'}
        </button>
      </div>
      <div className="playlist-scroll-container">
        {playlists === null || cards.length === 0 ? (
          <div className="playlist-placeholder">
            {playlists === null
              ? placeholder
              : 'No Last.fm Radio playlists yet. Generate one from the Discover page by picking a seed track.'}
          </div>
        ) : (
          <LbCardList
            cards={cards}
            vertical={vertical}
            icon="📻"
            cardClassName="lastfm-playlist-card"
            idPrefix="lastfm-sync-card"
            onOpen={onOpen}
          />
        )}
      </div>
    </div>
  );
}
