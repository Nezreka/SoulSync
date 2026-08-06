/**
 * The Wing It Pool modal (stats-automations.js 1373-1589).
 *
 * Wing It auto-matches a track on a best-effort guess and marks it discovered,
 * which means the Discovery Pool HIDES it — this modal is the only place those
 * guesses can be reviewed or corrected.
 *
 * Both its lists hold ordinary track rows (not cache entries), so both
 * "Fix Match" and "Re-match" here carry a TRACK id into /discovery-pool/fix.
 * A corrected guess drops out of the attention list on the next refresh.
 *
 * Two drifts from its twin, both preserved: the counts are ARRAY LENGTHS
 * rather than a stats object, and the matched card never gets a mosaic.
 */

import { useCallback, useEffect, useState } from 'react';

import type { PoolFixTarget, PoolTrackRow, WingItPoolData } from '../-sync.pools';

import { fetchWingItPool } from '../-sync.api';
import {
  poolQuery,
  poolTrackMatches,
  wingItMatchedName,
  wingItPoolCounts,
  wingItPoolEmptyMessage,
  wingItPoolListTitle,
} from '../-sync.pools';
import { PoolFixModal } from './pool-fix-modal';
import { PoolCategoryCard, PoolEmpty, PoolModal } from './pool-modal';

export interface WingItPoolModalProps {
  playlistId?: number | null;
  onClose: () => void;
}

export function WingItPoolModal({ playlistId = null, onClose }: WingItPoolModalProps) {
  const [data, setData] = useState<WingItPoolData | null>(null);
  const [view, setView] = useState<'categories' | 'attention' | 'matched'>('categories');
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState(playlistId ? String(playlistId) : '');
  const [fixTarget, setFixTarget] = useState<PoolFixTarget | null>(null);

  /** openWingItPoolModal (1376-1385) / filterWingItPool (1548-1562). */
  const load = useCallback(
    async (playlistFilter: string, initial: boolean) => {
      try {
        setData(await fetchWingItPool(playlistFilter || null));
      } catch {
        window.showToast?.(
          initial ? 'Failed to load Wing It pool' : 'Failed to filter Wing It pool',
          'error',
        );
        if (initial) onClose();
      }
    },
    [onClose],
  );

  useEffect(() => {
    void load(playlistId ? String(playlistId) : '', true);
  }, [load, playlistId]);

  /** refreshWingItPool (1579-1581) — what a committed Fix triggers. */
  const refresh = useCallback(() => void load(filter, false), [load, filter]);

  const counts = wingItPoolCounts(data);
  const q = poolQuery(query);

  /** showWingItList clears the search on entry (1471). */
  const openList = (next: 'attention' | 'matched') => {
    setQuery('');
    setView(next);
  };

  const isMatched = view === 'matched';
  const rows = ((isMatched ? data?.matched : data?.tracks) || []).filter((t) =>
    poolTrackMatches(t, q),
  );

  const openFix = (track: PoolTrackRow) =>
    setFixTarget({
      mode: 'fix',
      trackId: track.id,
      trackName: track.track_name || '',
      artistName: track.artist_name || '',
    });

  return (
    <>
      <PoolModal
        id="wing-it-pool-overlay"
        title="Wing It Pool"
        chips={
          <>
            <span
              className={`playlist-owner${
                counts.attention > 0 ? ' pool-header-failed-highlight' : ''
              }`}
              id="wing-it-header-attention"
            >
              {counts.attention} to review
            </span>
            <span className="playlist-track-count" id="wing-it-header-matched">
              {counts.matched} resolved
            </span>
          </>
        }
        playlists={data?.playlists || []}
        playlistFilter={filter}
        onPlaylistFilter={(value) => {
          setFilter(value);
          void load(value, false);
        }}
        onClose={onClose}
        cards={
          <>
            <PoolCategoryCard
              tone="failed"
              icon="⚡"
              count={counts.attention}
              label="guesses to review"
              onOpen={() => openList('attention')}
            />
            {/* No mosaic here — Wing It keeps the flat gradient (1428). */}
            <PoolCategoryCard
              tone="matched"
              icon="✓"
              count={counts.matched}
              label="resolved manually"
              onOpen={() => openList('matched')}
            />
          </>
        }
        list={
          view === 'categories'
            ? null
            : {
                title: wingItPoolListTitle(view),
                query,
                onQuery: setQuery,
                onBack: () => setView('categories'),
                children:
                  rows.length === 0 ? (
                    <PoolEmpty>{wingItPoolEmptyMessage(view, Boolean(q))}</PoolEmpty>
                  ) : (
                    rows.map((track) => {
                      const matchedName = isMatched ? wingItMatchedName(track) : '';
                      return (
                        <div
                          className={`pool-track-row ${isMatched ? 'pool-matched' : 'pool-failed'}`}
                          key={track.id}
                        >
                          <div className="pool-track-info">
                            <div className="pool-track-name">{track.track_name}</div>
                            <div className="pool-track-meta">
                              <span className="pool-track-artist">{track.artist_name}</span>
                              {matchedName && (
                                <>
                                  <span className="pool-track-arrow">→</span>
                                  <span className="pool-match-name">{matchedName}</span>
                                </>
                              )}
                              <span className="pool-track-playlist-badge">
                                {track.playlist_name}
                              </span>
                            </div>
                          </div>
                          {isMatched ? (
                            <button
                              type="button"
                              className="pool-rematch-btn"
                              title="Change this match"
                              onClick={() => openFix(track)}
                            >
                              Re-match
                            </button>
                          ) : (
                            <button
                              type="button"
                              className="playlist-modal-btn playlist-modal-btn-primary pool-fix-btn"
                              onClick={() => openFix(track)}
                            >
                              Fix Match
                            </button>
                          )}
                        </div>
                      );
                    })
                  ),
              }
        }
      />
      {fixTarget && (
        <PoolFixModal target={fixTarget} onClose={() => setFixTarget(null)} onMatched={refresh} />
      )}
    </>
  );
}
