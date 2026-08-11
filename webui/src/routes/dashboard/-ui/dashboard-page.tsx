/**
 * The Dashboard page shell — the header plus the eight bento cards in the
 * vanilla dash-grid order (index.html 2225-2954, recorded in
 * dash-vanilla-fixture.html): services, stats, library, syncs, quick-actions,
 * activity, active-downloads, rate-monitor.
 *
 * The `page` class is NOT here (the label-detail trap: the shell styles
 * `.page { display:none }` and only vanilla pages get `.active`). The
 * `#dashboard-page` id IS kept, merged onto the page-shell div as the tools
 * flip did — worker-orbs.js anchors `#dashboard-page .dashboard-header`, the
 * CSS overrides are all descendant selectors, and the tour targets live ids.
 *
 * The mount effect re-pings worker-orbs: the shell bridge calls
 * setPage('dashboard') BEFORE React paints, so the orb layer's lazy re-anchor
 * needs one call at a moment the header actually exists. (setPage is
 * idempotent — re-anchoring only runs when its header ref is missing or
 * unmounted.)
 */

import { useEffect } from 'react';

import { ActiveDownloadsShell } from './active-downloads-shell';
import { ContentBand } from './content-rails';
import { DashboardHeader } from './dashboard-header';
import { LibraryCard } from './library-card';
import { ServicesCard } from './services-merged';
import { SyncsCard } from './syncs-card';
import { SystemStatsCard } from './system-stats';

export function DashboardPage() {
  useEffect(() => {
    window.workerOrbs?.setPage('dashboard');
  }, []);

  return (
    <div className="page-shell dashboard-container" id="dashboard-page">
      <DashboardHeader />
      <div className="dash-grid">
        {/* ONE full-width content band: Recently Added | Fresh Releases behind
            a tab switcher. Renders nothing until a feed has rows, so a fresh
            install sees the ops grid it always saw. */}
        <ContentBand />
        {/* One quiet full-width strip of numbers, then the ops row. Recent
            Activity (the tray owns it) and Quick Actions (the sidebar again,
            as buttons) are gone; Service Status and the rate equalizer are
            ONE Services card. */}
        <SystemStatsCard />
        <ServicesCard />
        <LibraryCard />
        <SyncsCard />
        <ActiveDownloadsShell />
      </div>
    </div>
  );
}
