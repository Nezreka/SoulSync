/**
 * The five cards that are a bit of state plus a button opening a modal that
 * stays vanilla.
 *
 * None of these modals moves with the page: two are backed by markup outside
 * the tools region (#tool-help-modal, #mcache-browse-modal), two are
 * self-contained files also reached from elsewhere (manual library match from
 * the sync-history rows, discovery pool from the sync page), and the config
 * export covers both the music and video sides. Reimplementing any of them
 * would fork behaviour that other pages depend on — see globals.d.ts.
 *
 * Copy is transcribed verbatim from index.html so nothing reads differently.
 */

import { useEffect, useState } from 'react';

import { fetchBlacklist, fetchDiscoveryPoolStats, fetchMetadataCacheStats } from '../-tools.api';
import { metadataCacheCardCount } from '../-tools.core';
import { ToolCard } from './tool-card';

const EM_DASH = '—';

/**
 * Discovery Pool. The counters start as em dashes and STAY em dashes if the
 * fetch fails — `fetchDiscoveryPoolStats` returns null rather than zero for
 * exactly this reason, because "0 matched" over a failed load reads as a real
 * (and alarming) answer.
 *
 * The Failed counter carries its own red pill styling inline in the vanilla
 * markup rather than a class, so it is reproduced inline here.
 */
export function DiscoveryPoolCard() {
  const [matched, setMatched] = useState<string>(EM_DASH);
  const [failed, setFailed] = useState<string>(EM_DASH);

  useEffect(() => {
    let cancelled = false;
    void fetchDiscoveryPoolStats().then((stats) => {
      if (cancelled || !stats) return;
      setMatched(String(stats.matched ?? 0));
      setFailed(String(stats.failed ?? 0));
    });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <ToolCard
      id="discovery-pool-card"
      title="Discovery Pool"
      info="View and fix matched/failed discovery results across all mirrored playlists"
      statsId="discovery-pool-stats"
      stats={[
        { label: 'Matched:', value: <span id="discovery-pool-matched-count">{matched}</span> },
        {
          label: 'Failed:',
          value: <span id="discovery-pool-failed-count">{failed}</span>,
          valueStyle: { backgroundColor: 'rgba(239, 68, 68, 0.15)', color: '#ef4444' },
        },
      ]}
      controls={
        <button type="button" onClick={() => window.openDiscoveryPoolModal?.()}>
          Open Discovery Pool
        </button>
      }
    />
  );
}

/**
 * Manual Library Match. Both stat values are static labels in the vanilla, not
 * live counts — they describe what the tool does rather than reporting numbers.
 */
export function ManualLibraryMatchCard() {
  return (
    <ToolCard
      id="manual-library-match-card"
      title="Manual Library Match"
      info="Map wishlist and playlist source tracks to library tracks you already own"
      statsClassName="tool-card-stats manual-library-match-stats"
      stats={[
        { label: 'Source:', value: 'Wishlist / Sync' },
        { label: 'Result:', value: 'Found' },
      ]}
      controls={
        <button type="button" onClick={() => window.openManualLibraryMatchTool?.()}>
          Open Library Match
        </button>
      }
    />
  );
}

/** Config Migration — export/import every setting for both sides as one JSON. */
export function ConfigMigrationCard() {
  return (
    <ToolCard
      id="config-migration-card"
      title="Config Migration"
      info="Export every setting (music + video) as one JSON file to move to a new install, or import one here."
      controls={
        <button
          type="button"
          id="config-export-button"
          onClick={() => window.openConfigExportModal?.()}
        >
          Export / Import Config
        </button>
      }
    />
  );
}

/**
 * Metadata Cache. The four counters sum only the FIRST-PARTY sources — discogs
 * and musicbrainz are deliberately excluded from this card even though the cache
 * holds them (see `metadataCacheCardCount`); the Cache Health modal is where
 * those show up.
 */
export function MetadataCacheCard() {
  const [counts, setCounts] = useState({ artists: 0, albums: 0, tracks: 0, hits: 0 });

  useEffect(() => {
    let cancelled = false;
    void fetchMetadataCacheStats().then((stats) => {
      if (cancelled || !stats) return;
      setCounts({
        artists: metadataCacheCardCount(stats.artists),
        albums: metadataCacheCardCount(stats.albums),
        tracks: metadataCacheCardCount(stats.tracks),
        hits: stats.total_hits || 0,
      });
    });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <ToolCard
      id="metadata-cache-card"
      title="Metadata Cache"
      helpTool="metadata-cache"
      info={<>Cached API responses from Spotify &amp; iTunes</>}
      stats={[
        { label: 'Artists:', value: <span id="mcache-stat-artists">{counts.artists}</span> },
        { label: 'Albums:', value: <span id="mcache-stat-albums">{counts.albums}</span> },
        { label: 'Tracks:', value: <span id="mcache-stat-tracks">{counts.tracks}</span> },
        { label: 'Hits:', value: <span id="mcache-stat-hits">{counts.hits}</span> },
      ]}
      controls={
        <>
          <button
            type="button"
            id="mcache-browse-button"
            onClick={() => window.openMetadataCacheModal?.()}
          >
            Browse Cache
          </button>
          <button
            type="button"
            className="tool-card-btn-secondary"
            onClick={() => window.openCacheHealthModal?.()}
          >
            Cache Health
          </button>
        </>
      }
    />
  );
}

/**
 * Download Blacklist. The count uses `entries.length` and ignores the `success`
 * flag, matching `loadBlacklistCount` — the MODAL is the caller that treats
 * `!success` as empty, and that modal stays vanilla.
 */
export function BlacklistCard() {
  const [count, setCount] = useState(0);

  useEffect(() => {
    let cancelled = false;
    void fetchBlacklist().then(({ entries }) => {
      if (!cancelled) setCount(entries.length);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <ToolCard
      id="blacklist-card"
      title="Download Blacklist"
      info="Blocked sources that won't be used for future downloads"
      stats={[{ label: 'Blocked:', value: <span id="blacklist-count">{count}</span> }]}
      controls={
        <button type="button" onClick={() => window.openBlacklistModal?.()}>
          View Blacklist
        </button>
      }
    />
  );
}
