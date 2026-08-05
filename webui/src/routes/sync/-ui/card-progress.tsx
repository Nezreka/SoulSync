/**
 * The playlist card's progress line — ONE renderer for every vertical.
 *
 * The vanilla painted this element from two different writers and the second
 * is easy to miss: updateXCardProgress writes the DISCOVERY line (tidal
 * 961-966, deezer's check-note twin), and updateXCardSyncProgress writes a
 * different SYNC line (tidal 1159-1197, qobuz 2315-2348) whose percentage is
 * (matched+failed)/total, not matched/total. The sync writer only paints when
 * total_tracks > 0 and otherwise leaves the discovery line standing, so the
 * element's content is: sync numbers once a sync reports any, else the
 * discovery numbers, else empty.
 *
 * Which discovery format a source uses is the drift the config table already
 * encodes (SourceVerticalConfig.ux.cardProgressFormat) — read it here rather
 * than re-deciding per tab.
 */

import type { ReactNode } from 'react';

import type { SourceVerticalConfig } from '../-sync.sources';
import type { SourcePlaylistState } from '../-sync.state';

import { checkNoteCounts, slashTextProgressLine } from '../-sync.url-tabs';

/**
 * The sync line's counters (tidal 1172-1177). Null when there is no sync
 * progress to show, which is exactly when the vanilla left the discovery
 * line in place.
 */
export function syncCardCounts(
  progress: { total_tracks?: number; matched_tracks?: number; failed_tracks?: number } | undefined,
): { total: number; matched: number; failed: number; percentage: number } | null {
  if (!progress || !progress.total_tracks || progress.total_tracks <= 0) return null;
  const matched = progress.matched_tracks || 0;
  const failed = progress.failed_tracks || 0;
  const total = progress.total_tracks || 0;
  const processed = matched + failed;
  const percentage = total > 0 ? Math.round((processed / total) * 100) : 0;
  return { total, matched, failed, percentage };
}

/**
 * The whole line. `null` hides the element (fresh cards); `''` renders it
 * visible and empty (non-fresh with nothing to report yet).
 */
export function cardProgressLine(
  state: SourcePlaylistState,
  config: SourceVerticalConfig,
): ReactNode | null {
  if (state.phase === 'fresh') return null;

  // The sync writer wins whenever it has something (1192-1194).
  const sync = syncCardCounts(state.lastSyncProgress);
  if (sync) {
    return (
      <div className="playlist-card-sync-status">
        <span className="sync-stat total-tracks">♪ {sync.total}</span>
        <span className="sync-separator">/</span>
        <span className="sync-stat matched-tracks">✓ {sync.matched}</span>
        <span className="sync-separator">/</span>
        <span className="sync-stat failed-tracks">✗ {sync.failed}</span>
        <span className="sync-stat percentage">({sync.percentage}%)</span>
      </div>
    );
  }

  if (config.ux.cardProgressFormat === 'check-note-spans') {
    const counts = checkNoteCounts({
      spotify_total: state.spotifyTotal,
      spotify_matches: state.spotifyMatches,
    });
    if (!counts) return '';
    return (
      <div className="playlist-card-sync-status">
        <span className="sync-stat matched-tracks">✓ {counts.matches}</span>
        <span className="sync-separator">/</span>
        <span className="sync-stat total-tracks">♪ {counts.total}</span>
      </div>
    );
  }

  // NO total>0 gate here: the slash-text writers paint unconditionally
  // (tidal 961-967, qobuz 2165-2171, youtube 9120-9125) — a 0-track card
  // shows '♪ 0 / ✓ 0 / ✗ 0 / 0%'. Only the check-note writers gate (deezer
  // 3372, spotify-public 7298, itunes 8324), which checkNoteCounts does.
  return slashTextProgressLine({
    spotify_total: state.spotifyTotal,
    spotify_matches: state.spotifyMatches,
  });
}
