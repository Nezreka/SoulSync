/**
 * The pool fix / rematch sub-modal (stats-automations.js 1739-2022).
 *
 * ONE modal, two modes. The vanilla builds the same `#pool-fix-overlay` from
 * two entry points and smuggles the mode through `fixOverlay.dataset`, then
 * branches on `dataset.mode === 'rematch'` at submit time; here the mode is a
 * discriminated union prop (PoolFixTarget) and the dataset goes away.
 *
 * The two differ in three places and three only: the heading, the source
 * label, and which endpoint the chosen track is posted to — a MIRRORED TRACK
 * id to /discovery-pool/fix, or a CACHE id plus the original pair to
 * /discovery-pool/rematch.
 *
 * DECLARED DIVERGENCE: the vanilla serialises each result into an inline
 * onclick with `JSON.stringify(track).replace(/'/g, '&#39;')`. React passes
 * the object straight through, so that escaping has no counterpart.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import type { PoolFixSearchState, PoolFixTarget, PoolSearchTrack } from '../-sync.pools';

import { postPoolFix, postPoolRematch, searchPoolFixTracks } from '../-sync.api';
import { formatDuration } from '../-sync.core';
import {
  POOL_FIX_AUTOSEARCH_MS,
  POOL_FIX_NEEDS_QUERY,
  POOL_FIX_NO_RESULTS,
  poolFixConfirmMessage,
  poolFixHeading,
  poolFixMatchedToast,
  poolFixSearchError,
  poolFixSearchFailed,
  poolFixSourceLabel,
  poolFixThrewMessage,
} from '../-sync.pools';

export interface PoolFixModalProps {
  target: PoolFixTarget;
  onClose: () => void;
  /** A committed match — the caller refetches its pool (2007-2012). */
  onMatched: () => void;
}

export function PoolFixModal({ target, onClose, onMatched }: PoolFixModalProps) {
  const [track, setTrack] = useState(target.trackName);
  const [artist, setArtist] = useState(target.artistName);
  const [state, setState] = useState<PoolFixSearchState>({ kind: 'searching' });

  const trackRef = useRef<HTMLInputElement>(null);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  // The latest values, so the auto-search timer does not capture the initial
  // ones and re-run stale.
  const latest = useRef({ track, artist });
  useEffect(() => {
    latest.current = { track, artist };
  });

  const search = useCallback(async () => {
    const trackVal = latest.current.track.trim();
    const artistVal = latest.current.artist.trim();
    if (!trackVal && !artistVal) {
      setState({ kind: 'idle', message: POOL_FIX_NEEDS_QUERY });
      return;
    }
    setState({ kind: 'searching' });
    try {
      const { ok, status, statusText, data } = await searchPoolFixTracks(trackVal, artistVal);
      if (!alive.current) return;
      if (poolFixSearchFailed(ok, data.error)) {
        setState({ kind: 'error', message: poolFixSearchError(status, statusText, data.error) });
        return;
      }
      const tracks = data.tracks || [];
      if (tracks.length === 0) {
        setState({ kind: 'empty', message: POOL_FIX_NO_RESULTS });
        return;
      }
      setState({ kind: 'results', tracks });
    } catch (err) {
      if (!alive.current) return;
      setState({
        kind: 'error',
        message: poolFixThrewMessage(err instanceof Error ? err.message : 'unknown error'),
      });
    }
  }, []);

  // Focus + select the track input, then auto-search (1804-1809).
  useEffect(() => {
    trackRef.current?.focus();
    trackRef.current?.select();
    const timer = setTimeout(() => void search(), POOL_FIX_AUTOSEARCH_MS);
    return () => clearTimeout(timer);
  }, [search]);

  const select = useCallback(
    async (chosen: PoolSearchTrack) => {
      const ok = await window.showConfirmDialog?.({
        title: 'Confirm Match',
        message: poolFixConfirmMessage(chosen),
        confirmText: 'Confirm',
      });
      if (!ok) return;
      try {
        const data =
          target.mode === 'rematch'
            ? await postPoolRematch(
                target.cacheId,
                target.originalTitle,
                target.originalArtist,
                chosen,
              )
            : await postPoolFix(target.trackId, chosen);
        if (!alive.current) return;
        if (!data.success) {
          window.showToast?.(data.error || 'Failed to fix track', 'error');
          return;
        }
        window.showToast?.(poolFixMatchedToast(chosen), 'success');
        onClose();
        onMatched();
      } catch (err) {
        if (!alive.current) return;
        window.showToast?.(
          `Error: ${err instanceof Error ? err.message : 'unknown error'}`,
          'error',
        );
      }
    },
    [target, onClose, onMatched],
  );

  return (
    <div
      className="pool-fix-overlay"
      id="pool-fix-overlay"
      // mousedown, not click, and preventDefault — so dismissing does not first
      // steal focus from the inputs (1840-1846).
      //
      // The target check is BELT AND BRACES: the dialog below stops the event
      // itself, and it is the overlay's only child, so no inner mousedown can
      // reach here anyway. The vanilla carries both guards too; the redundancy
      // is transcribed, not invented, and no test can distinguish them.
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) {
          e.preventDefault();
          onClose();
        }
      }}
    >
      <div className="pool-fix-modal" onMouseDown={(e) => e.stopPropagation()}>
        <div className="pool-fix-header">
          <h2>{poolFixHeading(target)}</h2>
          <button type="button" className="pool-fix-close" onClick={onClose} title="Close">
            ✕
          </button>
        </div>
        <div className="pool-fix-body">
          <div className="pool-fix-source">
            <div className="pool-fix-source-label">{poolFixSourceLabel(target)}</div>
            <div className="pool-fix-source-row">
              <span className="pool-fix-source-title">{target.trackName}</span>
              <span className="pool-fix-source-sep">—</span>
              <span className="pool-fix-source-artist">{target.artistName}</span>
            </div>
          </div>
          <div className="pool-fix-search">
            <div className="pool-fix-input-row">
              <div className="pool-fix-input-wrap">
                <label htmlFor="pool-fix-track-input">Track</label>
                <input
                  type="text"
                  id="pool-fix-track-input"
                  ref={trackRef}
                  placeholder="Track name"
                  value={track}
                  onChange={(e) => setTrack(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') void search();
                  }}
                />
              </div>
              <div className="pool-fix-input-wrap">
                <label htmlFor="pool-fix-artist-input">Artist</label>
                <input
                  type="text"
                  id="pool-fix-artist-input"
                  placeholder="Artist name"
                  value={artist}
                  onChange={(e) => setArtist(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') void search();
                  }}
                />
              </div>
              <button type="button" className="pool-fix-search-btn" onClick={() => void search()}>
                Search
              </button>
            </div>
          </div>
          <div className="pool-fix-results-area">
            <div id="pool-fix-results" className="pool-fix-results-list">
              {state.kind === 'searching' ? (
                <div className="pool-fix-empty">
                  <div className="pool-fix-spinner" />
                  Searching...
                </div>
              ) : state.kind === 'results' ? (
                state.tracks.map((result, index) => (
                  <div className="pool-fix-result" key={index} onClick={() => void select(result)}>
                    <div className="pool-fix-result-main">
                      <div className="pool-fix-result-title">{result.name || 'Unknown'}</div>
                      <div className="pool-fix-result-meta">
                        {(result.artists || []).join(', ')}
                        {result.album ? ` · ${result.album}` : ''}
                      </div>
                    </div>
                    {result.duration_ms ? (
                      <div className="pool-fix-result-dur">
                        {formatDuration(result.duration_ms)}
                      </div>
                    ) : null}
                  </div>
                ))
              ) : (
                <div className="pool-fix-empty">{state.message}</div>
              )}
            </div>
          </div>
        </div>
        <div className="pool-fix-footer">
          <button type="button" className="pool-fix-cancel" onClick={onClose}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
