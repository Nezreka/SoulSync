/**
 * One SIN chart as a card, for both the dashboard rail and the Discover
 * shelf. Built from the pages' existing card markup (`ya-card` and parts), so
 * it looks like its neighbours on either page; `className` adds the page's
 * own card class (dash-rail-card / discover-album-card).
 */

import { useState } from 'react';

import { type SinChart, sinChartStatus } from './charts';

export function SinChartCard({
  chart,
  className,
  busy,
  onOpen,
}: {
  chart: SinChart;
  className: string;
  busy?: boolean;
  onOpen: (chart: SinChart) => void;
}) {
  const [failed, setFailed] = useState(false);
  const cover = chart.image_url || chart.cover_tiles[0] || '';
  return (
    <button
      type="button"
      className={`ya-card ${className}`}
      title={`Open ${chart.name} — nothing downloads until you choose to`}
      aria-label={`${chart.name}, ${sinChartStatus(chart)}`}
      aria-busy={busy || undefined}
      onClick={() => onOpen(chart)}
    >
      <div className="ya-card-img">
        {cover && !failed && (
          <img src={cover} alt="" loading="lazy" onError={() => setFailed(true)} />
        )}
        {(!cover || failed) && <div className="ya-card-placeholder">♫</div>}
      </div>
      <div className="ya-card-gradient" />
      <div className="ya-card-info">
        <div className="ya-card-name">{chart.name}</div>
        <div className="ya-card-sub">{busy ? 'Opening…' : sinChartStatus(chart)}</div>
      </div>
    </button>
  );
}
