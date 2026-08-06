/**
 * The Discovery Pool modal (stats-automations.js 1217-1372, 1591-1732, 1812).
 *
 * Two lists behind two cards: FAILED tracks that never matched (each offering
 * a Fix Match), and the MATCHED cache — the rich list, with covers, a
 * confidence badge, a use count, a Rematch and a remove.
 *
 * The two "re-match" affordances are NOT the same thing and the ids prove it:
 * a failed row carries a MIRRORED TRACK id into /discovery-pool/fix, a matched
 * row carries a CACHE id into /discovery-pool/rematch (1734-1737 vs 1830).
 *
 * DECLARED DIVERGENCE: the vanilla toasts 'Failed to load discovery pool' and
 * never opens on the first fetch (1229-1231), but 'Failed to filter discovery
 * pool' and stays open when a later filter fails (1633-1635). Both messages
 * are kept, and so is the difference in what happens to the modal.
 */

import { useCallback, useEffect, useState } from 'react';

import type { DiscoveryPoolData, PoolCacheEntry, PoolFixTarget } from '../-sync.pools';

import { deletePoolCacheEntry, fetchDiscoveryPool } from '../-sync.api';
import {
  POOL_REMOVE_CACHE_MESSAGE,
  discoveryPoolCounts,
  discoveryPoolEmptyMessage,
  discoveryPoolListTitle,
  poolCacheMatches,
  poolConfidence,
  poolMatchImage,
  poolMosaicImages,
  poolMosaicRows,
  poolQuery,
  poolTrackMatches,
} from '../-sync.pools';
import { PoolFixModal } from './pool-fix-modal';
import { PoolCategoryCard, PoolEmpty, PoolModal } from './pool-modal';

export interface DiscoveryPoolModalProps {
  /** Preselect a playlist, as the card entry points do (1217). */
  playlistId?: number | null;
  onClose: () => void;
}

export function DiscoveryPoolModal({ playlistId = null, onClose }: DiscoveryPoolModalProps) {
  const [data, setData] = useState<DiscoveryPoolData | null>(null);
  const [view, setView] = useState<'categories' | 'failed' | 'matched'>('categories');
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState(playlistId ? String(playlistId) : '');
  const [fixTarget, setFixTarget] = useState<PoolFixTarget | null>(null);

  /** filterDiscoveryPool (1616-1636) — and the first load, which toasts differently. */
  const load = useCallback(
    async (playlistFilter: string, initial: boolean) => {
      try {
        setData(await fetchDiscoveryPool(playlistFilter || null));
      } catch {
        window.showToast?.(
          initial ? 'Failed to load discovery pool' : 'Failed to filter discovery pool',
          'error',
        );
        // The first failure never opens the modal at all (1230).
        if (initial) onClose();
      }
    },
    [onClose],
  );

  useEffect(() => {
    void load(playlistId ? String(playlistId) : '', true);
  }, [load, playlistId]);

  const refresh = useCallback(() => void load(filter, false), [load, filter]);

  /** removePoolCacheEntry (1812-1828). */
  const removeEntry = useCallback(
    async (entryId: number) => {
      const ok = await window.showConfirmDialog?.({
        title: 'Remove Cache Entry',
        message: POOL_REMOVE_CACHE_MESSAGE,
      });
      if (!ok) return;
      try {
        const result = await deletePoolCacheEntry(entryId);
        if (result.success) {
          window.showToast?.('Cache entry removed', 'success');
          refresh();
        } else {
          window.showToast?.(result.error || 'Failed to remove', 'error');
        }
      } catch (err) {
        window.showToast?.(
          `Error: ${err instanceof Error ? err.message : 'unknown error'}`,
          'error',
        );
      }
    },
    [refresh],
  );

  const counts = discoveryPoolCounts(data);
  const q = poolQuery(query);

  /** showPoolList clears the search on every entry (1610-1611). */
  const openList = (next: 'failed' | 'matched') => {
    setQuery('');
    setView(next);
  };

  const failedRows = (data?.failed || []).filter((t) => poolTrackMatches(t, q));
  const matchedRows = (data?.matched || []).filter((e) => poolCacheMatches(e, q));

  const renderMatched = (entry: PoolCacheEntry) => {
    const { percent, band } = poolConfidence(entry.confidence);
    const image = poolMatchImage(entry);
    return (
      <div className="pool-track-row pool-matched" key={entry.id}>
        {image ? (
          <img
            className="pool-match-image"
            src={image}
            alt=""
            onError={(e) => {
              (e.currentTarget as HTMLImageElement).style.display = 'none';
            }}
          />
        ) : (
          <div className="pool-match-image-placeholder" />
        )}
        <div className="pool-track-info">
          <div className="pool-track-name">{entry.original_title}</div>
          <div className="pool-track-meta">
            <span className="pool-track-artist">{entry.original_artist}</span>
            <span className="pool-track-arrow">→</span>
            {/* The vanilla shows a bare '?' when the cache has no name (1719). */}
            <span className="pool-match-name">{entry.matched_data?.name || '?'}</span>
            <span className="pool-match-provider">{entry.provider}</span>
          </div>
        </div>
        <span className={`pool-confidence-badge ${band}`}>{percent}%</span>
        <span className="pool-use-count">{entry.use_count}×</span>
        <button
          type="button"
          className="pool-rematch-btn"
          title="Rematch this track"
          onClick={() =>
            setFixTarget({
              mode: 'rematch',
              cacheId: entry.id,
              originalTitle: entry.original_title || '',
              originalArtist: entry.original_artist || '',
              trackName: entry.original_title || '',
              artistName: entry.original_artist || '',
            })
          }
        >
          Rematch
        </button>
        <button
          type="button"
          className="pool-remove-btn"
          title="Remove cached match"
          onClick={() => void removeEntry(entry.id)}
        >
          ×
        </button>
      </div>
    );
  };

  return (
    <>
      <PoolModal
        id="discovery-pool-overlay"
        title="Discovery Pool"
        chips={
          <>
            <span className="playlist-track-count" id="pool-header-matched">
              {counts.matched} Matched
            </span>
            <span
              className={`playlist-owner${counts.failed > 0 ? ' pool-header-failed-highlight' : ''}`}
              id="pool-header-failed"
            >
              {counts.failed} Failed
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
              icon="⚠"
              count={counts.failed}
              label="tracks need attention"
              onOpen={() => openList('failed')}
            />
            <PoolCategoryCard
              tone="matched"
              icon="✓"
              count={counts.matched}
              label="cached matches"
              backgroundId="pool-matched-bg"
              mosaic={poolMosaicRows(poolMosaicImages(data?.matched || []))}
              onOpen={() => openList('matched')}
            />
          </>
        }
        list={
          view === 'categories'
            ? null
            : {
                title: discoveryPoolListTitle(view),
                query,
                onQuery: setQuery,
                onBack: () => setView('categories'),
                children:
                  view === 'failed' ? (
                    failedRows.length === 0 ? (
                      <PoolEmpty>{discoveryPoolEmptyMessage('failed', Boolean(q))}</PoolEmpty>
                    ) : (
                      failedRows.map((track) => (
                        <div className="pool-track-row pool-failed" key={track.id}>
                          <div className="pool-track-info">
                            <div className="pool-track-name">{track.track_name}</div>
                            <div className="pool-track-meta">
                              <span className="pool-track-artist">{track.artist_name}</span>
                              <span className="pool-track-playlist-badge">
                                {track.playlist_name}
                              </span>
                            </div>
                          </div>
                          <button
                            type="button"
                            className="playlist-modal-btn playlist-modal-btn-primary pool-fix-btn"
                            onClick={() =>
                              setFixTarget({
                                mode: 'fix',
                                trackId: track.id,
                                trackName: track.track_name || '',
                                artistName: track.artist_name || '',
                              })
                            }
                          >
                            Fix Match
                          </button>
                        </div>
                      ))
                    )
                  ) : matchedRows.length === 0 ? (
                    <PoolEmpty>{discoveryPoolEmptyMessage('matched', Boolean(q))}</PoolEmpty>
                  ) : (
                    matchedRows.map(renderMatched)
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
