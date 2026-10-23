/**
 * The SoulSync network's weekly charts, as a Discover shelf (MetaSync / SIN).
 *
 * The page fetches the charts (it needs the count to decide whether the
 * section shows at all) and hands them in. Clicking one opens it as your own,
 * already matched playlist in Sync; nothing downloads until you press Sync &
 * download there (features/metasync/charts.ts).
 */

import { useCallback, useState } from 'react';

import { openSinChart, type SinChart } from '@/features/metasync/charts';
import { SinChartCard } from '@/features/metasync/sin-chart-card';

import { DiscoverSection } from './discover-section';

export function SinChartsShelf({
  charts,
  loaded,
  onToast,
}: {
  charts: SinChart[];
  loaded: boolean;
  onToast?: (message: string) => void;
}) {
  const [opening, setOpening] = useState<string | null>(null);
  const open = useCallback(
    async (chart: SinChart) => {
      setOpening(chart.key);
      try {
        const error = await openSinChart(chart);
        if (error) onToast?.(error);
      } finally {
        setOpening(null);
      }
    },
    [onToast],
  );
  return (
    <DiscoverSection
      id="sin-charts"
      title="SoulSync charts"
      subtitle="What the SoulSync network is collecting this week. Open one to sync or download it — nothing downloads until you do."
      count={charts.length}
      loaded={loaded}
    >
      <div className="discover-grid">
        {charts.map((chart) => (
          <SinChartCard
            key={chart.key}
            chart={chart}
            className="discover-album-card"
            busy={opening === chart.key}
            onOpen={(c) => void open(c)}
          />
        ))}
      </div>
    </DiscoverSection>
  );
}
