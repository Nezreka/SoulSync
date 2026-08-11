/**
 * The System Stats card (dash-card data-card="stats") — six stat tiles.
 * Markup 1:1 from index.html (artefact differential pins it).
 *
 * Data: `ss:dashboard-stats` (the socket push through handleDashboardStats)
 * plus the vanilla poller twin's fallback — /api/system/stats every 10s, the
 * loadDashboardData cadence — gated on window._socketConnected and
 * document.hidden exactly like fetchAndUpdateSystemStats, with one ungated
 * mount hydrate.
 *
 * The value/subtitle pairs are MATERIALIZED per card: the initial texts are
 * the markup's, and a payload field that is absent keeps the previous text
 * rather than rendering the string "undefined" (the only divergence from
 * `updateStatCard`, whose raw textContent write would; real payloads always
 * carry every field). Note the vanilla's initial subtitles differ from the
 * update-time ones on purpose ("Completed this session" → "Completed
 * downloads") — both preserved.
 */

import { useCallback, useEffect, useState } from 'react';

import type { SystemStats } from '../-dash.api';

import { fetchSystemStats } from '../-dash.api';
import { useDashboardStatsEvent } from '../-dash.events';

interface StatTile {
  value: string;
  subtitle: string;
}

interface StatsState {
  active: StatTile;
  finished: StatTile;
  speed: StatTile;
  syncs: StatTile;
  uptime: StatTile;
  memory: StatTile;
}

const INITIAL: StatsState = {
  active: { value: '0', subtitle: 'Currently downloading' },
  finished: { value: '0', subtitle: 'Completed this session' },
  speed: { value: '0 KB/s', subtitle: 'Combined speed' },
  syncs: { value: '0', subtitle: 'Playlists syncing' },
  uptime: { value: '0m', subtitle: 'Application runtime' },
  memory: { value: '--', subtitle: 'Current usage' },
};

function tile(value: string | number | undefined, subtitle: string, prev: StatTile): StatTile {
  return value === undefined ? prev : { value: String(value), subtitle };
}

export function useSystemStats(): StatsState {
  const [stats, setStats] = useState(INITIAL);

  const apply = useCallback((data: SystemStats) => {
    setStats((prev) => ({
      active: tile(data.active_downloads, 'Currently downloading', prev.active),
      finished: tile(data.finished_downloads, 'Completed downloads', prev.finished),
      speed: tile(data.download_speed, 'Combined speed', prev.speed),
      syncs: tile(data.active_syncs, 'Playlists syncing', prev.syncs),
      uptime: tile(data.uptime, 'Application runtime', prev.uptime),
      // Headline is system memory %; subtitle shows SoulSync's own RSS.
      memory: tile(
        data.memory_usage,
        data.process_memory ? `SoulSync · ${data.process_memory}` : 'Current usage',
        prev.memory,
      ),
    }));
  }, []);

  useDashboardStatsEvent(apply);

  useEffect(() => {
    let cancelled = false;
    const hydrate = async () => {
      const data = await fetchSystemStats();
      if (!cancelled && data) apply(data);
    };
    const pollTick = async () => {
      if (window._socketConnected) return; // the socket push owns live updates
      if (document.hidden) return;
      await hydrate();
    };
    void hydrate();
    const timer = setInterval(() => void pollTick(), 10000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [apply]);

  return stats;
}

function StatCard({ id, title, tile }: { id: string; title: string; tile: StatTile }) {
  // The subtitle moves into the tooltip: a strip earns its quiet by showing
  // number + label only. The .stat-card-value/-subtitle classes stay because
  // the helper tour and the behaviour tests both target them.
  return (
    <div className="stat-tile" id={id} title={tile.subtitle}>
      <p className="stat-card-value">{tile.value}</p>
      <p className="stat-card-title">{title}</p>
      <p className="stat-card-subtitle" style={{ display: 'none' }}>{tile.subtitle}</p>
    </div>
  );
}

export function SystemStatsCard() {
  const stats = useSystemStats();
  return (
    // A full-width STRIP, not a box of boxes: six numbers with small labels,
    // subtitles demoted to tooltips. The header went with the box — the
    // numbers are their own title.
    <article className="dash-card dash-card--tiles" data-card="stats">
      <div className="stats-strip">
        <StatCard id="active-downloads-card" title="Active Downloads" tile={stats.active} />
        <StatCard id="finished-downloads-card" title="Finished Downloads" tile={stats.finished} />
        <StatCard id="download-speed-card" title="Download Speed" tile={stats.speed} />
        <StatCard id="active-syncs-card" title="Active Syncs" tile={stats.syncs} />
        <StatCard id="uptime-card" title="System Uptime" tile={stats.uptime} />
        <StatCard id="memory-card" title="Memory Usage" tile={stats.memory} />
      </div>
    </article>
  );
}
