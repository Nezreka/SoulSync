/**
 * The playlist card's progress line for the URL-import and account
 * verticals. NOT every vertical: the ListenBrainz/Last.fm cards render
 * lbCardProgressLine (-sync.lb-tabs.ts), whose sync percentage is
 * matched/total — real parity, sync-listenbrainz.js 271, not a drift to
 * unify away — and the SoulSync Discovery tab paints its own result line.
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

import type { CardCoverageValue } from './card-coverage';

import { checkNoteCounts, slashTextCounts } from '../-sync.url-tabs';
import { CardCoverage } from './card-coverage';

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
 * Normalise a card's state into the ONE coverage shape, or null/'' for the two
 * non-rendering cases. The three branches are the three writers, unchanged —
 * this function decides WHICH numbers apply, `CardCoverage` decides how they
 * look, and neither recomputes the other's percentage.
 *
 * Returns:
 *   null   the element is hidden entirely (fresh cards)
 *   ''     visible but empty — the check-note sources' total===0 state
 *   value  the coverage numbers to render
 */
export function cardCoverageValue(
  state: SourcePlaylistState,
  config: SourceVerticalConfig,
): CardCoverageValue | '' | null {
  if (state.phase === 'fresh') return null;

  // The sync writer wins whenever it has something (1192-1194). Its percentage
  // is (matched+failed)/total and must stay that way.
  const sync = syncCardCounts(state.lastSyncProgress);
  if (sync) {
    return {
      total: sync.total,
      matched: sync.matched,
      failed: sync.failed,
      percentage: sync.percentage,
    };
  }

  if (config.ux.cardProgressFormat === 'check-note-spans') {
    const counts = checkNoteCounts({
      spotify_total: state.spotifyTotal,
      spotify_matches: state.spotifyMatches,
    });
    // The check-note writers gate on total>0 (deezer 3372, spotify-public
    // 7298, itunes 8324) and leave the element visible but empty below it.
    if (!counts) return '';
    // These sources never counted failures and never printed a percentage;
    // passing nulls keeps it that way while still filling the bar.
    return { total: counts.total, matched: counts.matches, failed: null, percentage: null };
  }

  // NO total>0 gate here: the slash-text writers paint unconditionally
  // (tidal 961-967, qobuz 2165-2171, youtube 9120-9125), so a 0-track card
  // still renders a (zeroed) coverage line rather than an empty element.
  const slash = slashTextCounts({
    spotify_total: state.spotifyTotal,
    spotify_matches: state.spotifyMatches,
  });
  return {
    total: slash.total,
    matched: slash.matches,
    failed: slash.failed,
    percentage: slash.percentage,
  };
}

/**
 * The whole line. `null` hides the element (fresh cards); `''` renders it
 * visible and empty (non-fresh with nothing to report yet).
 */
export function cardProgressLine(
  state: SourcePlaylistState,
  config: SourceVerticalConfig,
): ReactNode | null {
  const value = cardCoverageValue(state, config);
  if (value === null) return null;
  if (value === '') return '';
  return <CardCoverage {...value} />;
}
