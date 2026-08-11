/**
 * The Recent Syncs card (dash-card data-card="syncs") — loadDashboardSyncHistory
 * as a component. Mount load + the 30s refresh cycle (the vanilla's
 * pages-extra interval was gated on currentPage === 'dashboard'; React's
 * unmount-clears-interval is the same gate). The app-locked body-class guard
 * and the 401 → unlock-screen handling are verbatim; the card markup
 * (health class, source badge, relative time, pct bar, counts) comes from
 * -dash.library.ts syncCardView.
 *
 * Deletion is reimplemented rather than handed to deleteSyncHistoryCard —
 * the vanilla removes the card NODE directly, which would fight React's
 * ownership; same endpoint, same 200ms fade, state-driven removal.
 */

import { useCallback, useEffect, useState } from 'react';

import type { SyncHistoryEntry } from '../-dash.api';

import { fetchDashboardSyncHistory } from '../-dash.api';
import { useDashboardStatsEvent } from '../-dash.events';
import { syncCardView } from '../-dash.library';

export function useSyncHistory() {
  /** null until the first successful load — the container starts empty. */
  const [entries, setEntries] = useState<SyncHistoryEntry[] | null>(null);
  const [fadingIds, setFadingIds] = useState<Set<number | string>>(new Set());

  const load = useCallback(async () => {
    // Don't poll the auth-gated endpoint while the app is locked — resumes on
    // unlock, same as the vanilla guard.
    if (document.body.classList.contains('app-locked')) return;
    const result = await fetchDashboardSyncHistory();
    if (result.status === 'unauthorized') {
      // Session lapsed while the tab believed it was unlocked — surface the
      // correct unlock screen (both add 'app-locked', stopping this cycle).
      if (result.loginRequired && window.showLoginScreen) window.showLoginScreen();
      else window.showLaunchPinScreen?.();
      return;
    }
    if (result.status !== 'ok') return; // failures keep the previous cards
    setEntries(result.entries);
  }, []);

  useEffect(() => {
    void load();
    // Auto-refresh sync cards every 30 seconds when on dashboard.
    const timer = setInterval(() => void load(), 30000);
    return () => clearInterval(timer);
  }, [load]);

  const remove = useCallback(async (id: number | string) => {
    setFadingIds((prev) => new Set(prev).add(id));
    try {
      const resp = await fetch(`/api/sync/history/${id}`, { method: 'DELETE' });
      if (resp.ok) {
        setTimeout(() => {
          setEntries((prev) => (prev ? prev.filter((entry) => entry.id !== id) : prev));
          setFadingIds((prev) => {
            const next = new Set(prev);
            next.delete(id);
            return next;
          });
        }, 200);
      } else {
        setFadingIds((prev) => {
          const next = new Set(prev);
          next.delete(id);
          return next;
        });
      }
    } catch {
      // Failed deletes leave the card in place, like the vanilla's warn-only catch.
      setFadingIds((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    }
  }, []);

  /** Listen: the synced playlist resolved against the LIBRARY server-side,
   *  handed to the player's queue. Tracks that never landed are skipped and
   *  the toast says so — playing 40 of 50 honestly beats silence. */
  const listen = useCallback(async (id: number | string, name: string) => {
    try {
      const resp = await fetch(`/api/sync/history/${id}/play`);
      const data = (await resp.json()) as {
        success?: boolean;
        error?: string;
        name?: string;
        total?: number;
        tracks?: unknown[];
      };
      if (!data.success) throw new Error(data.error || 'failed');
      const tracks = data.tracks || [];
      if (!tracks.length) {
        window.showToast?.('None of those tracks are in your library yet', 'info');
        return;
      }
      if (data.total && tracks.length < data.total) {
        window.showToast?.(
          `Playing ${tracks.length} of ${data.total} — the rest aren't in your library`,
          'info',
        );
      }
      await window.playTrackList?.(tracks, data.name || name);
    } catch {
      window.showToast?.('Could not load that playlist', 'error');
    }
  }, []);

  return { entries, fadingIds, remove, listen };
}

export function SyncsCard() {
  const { entries, fadingIds, remove, listen } = useSyncHistory();
  const nowMs = Date.now();

  // Live "N syncing now" pill — the retired stats strip's Active Syncs
  // number, rehomed where syncs actually live (fed by the same global
  // dashboard:stats re-broadcast the strip used).
  const [activeSyncs, setActiveSyncs] = useState(0);
  useDashboardStatsEvent(
    useCallback((data) => {
      if (typeof data.active_syncs === 'number') setActiveSyncs(data.active_syncs);
    }, []),
  );

  return (
    <article className="dash-card" data-card="syncs">
      <header className="dash-card__head">
        <h3 className="dash-card__title">
          Recent Syncs
          {activeSyncs > 0 ? (
            <span className="dash-syncs-live">
              {activeSyncs} syncing now
            </span>
          ) : null}
        </h3>
        <p className="dash-card__sub">Playlists you&apos;ve synced.</p>
      </header>
      <div className="dash-card__body">
        <div className="sync-history-cards" id="sync-history-cards">
          {entries === null ? null : entries.length === 0 ? (
            <div className="sync-history-empty">No syncs yet</div>
          ) : (
            entries.map((entry, cardIndex) => {
              const view = syncCardView(entry, nowMs);
              const fading = view.id !== undefined && fadingIds.has(view.id);
              return (
                <div
                  key={view.id ?? cardIndex}
                  className={`sync-history-card ${view.healthClass}`}
                  style={{
                    animationDelay: `${cardIndex * 0.05}s`,
                    ...(fading ? { opacity: 0, transform: 'scale(0.9)' } : null),
                  }}
                  onClick={() => window.openSyncDetailModal?.(view.id as number)}
                >
                  <button
                    className="sync-card-delete"
                    title="Remove"
                    onClick={(event) => {
                      event.stopPropagation();
                      if (view.id !== undefined) void remove(view.id);
                    }}
                  >
                    ×
                  </button>
                  <div className="sync-card-thumb">
                    {view.thumbUrl ? (
                      <img src={view.thumbUrl} alt="" loading="lazy" />
                    ) : (
                      <div className="sync-card-thumb-placeholder">♫</div>
                    )}
                    <button
                      className="sync-card-listen"
                      title="Listen — play this playlist from your library"
                      onClick={(event) => {
                        event.stopPropagation();
                        if (view.id !== undefined) void listen(view.id, view.name);
                      }}
                    >
                      ▶
                    </button>
                  </div>
                  <div className="sync-card-info">
                    <div className="sync-card-name">{view.name}</div>
                    <div className="sync-card-meta">
                      <span className="sync-card-source">{view.sourceLabel}</span>
                      {view.typeLabel ? (
                        <span className="sync-card-type">{view.typeLabel}</span>
                      ) : null}
                      <span className="sync-card-time">{view.timeStr}</span>
                    </div>
                    {/* The roomier dashboard card spends its height on detail:
                        matched / downloaded / failed as colored chips, plus
                        wall time when the sync recorded both stamps. The
                        plain counts line stays for narrow widths (CSS swaps
                        them). */}
                    <div className="sync-card-chips">
                      <span className="sync-card-chip">
                        {view.found}/{view.total} matched
                      </span>
                      {view.downloaded > 0 ? (
                        <span className="sync-card-chip sync-card-chip--dl">
                          ⬇ {view.downloaded}
                        </span>
                      ) : null}
                      {view.failed > 0 ? (
                        <span className="sync-card-chip sync-card-chip--fail">
                          ✗ {view.failed}
                        </span>
                      ) : null}
                      {view.duration ? (
                        <span className="sync-card-chip sync-card-chip--dim">{view.duration}</span>
                      ) : null}
                    </div>
                  </div>
                  <div className="sync-card-stats">
                    <div className="sync-card-pct">{view.pct}%</div>
                    <div className="sync-card-bar">
                      <div className="sync-card-bar-fill" style={{ width: `${view.pct}%` }}></div>
                    </div>
                    <div className="sync-card-counts">{view.counts}</div>
                  </div>
                </div>
              );
            })
          )}
        </div>
      </div>
    </article>
  );
}
