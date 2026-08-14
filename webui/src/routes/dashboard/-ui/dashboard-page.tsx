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
import { AlertsBand } from './alerts-band';
import { AutomationsCard } from './automations-card';
import { ContentBand } from './content-rails';
import { DashboardHeader } from './dashboard-header';
import { LibraryCard } from './library-card';
import { ListenBand } from './listen-band';
import { ListeningHistoryBand } from './listening-history-band';
import { SyncBand } from './sync-band';

export function DashboardPage() {
  useEffect(() => {
    window.workerOrbs?.setPage('dashboard');
  }, []);

  return (
    <div className="page-shell dashboard-container" id="dashboard-page">
      <DashboardHeader />
      <div className="dash-grid">
        {/* The exception surface: renders NOTHING while every core
            connection is healthy — the one place that shouts when a human
            is needed, which is what buys the rest of the page its calm. */}
        <AlertsBand />
        {/* The Library strip leads — whose collection this is, then what's
            new in it. The content band (Recently Added | Fresh Releases
            behind a tab switcher) renders nothing until a feed has rows, so
            a fresh install sees the ops grid it always saw. */}
        <LibraryCard />
        <ContentBand />
        {/* What you've been PLAYING — recent listens from the same
            listening_history spine the stats page reads. Renders nothing
            until history exists. Click-through goes to the stats page. */}
        <ListeningHistoryBand />
        {/* The payoff band: everything above is about OWNING music, this is
            about playing it — Library Radio's front door + the Mixes
            doorway. */}
        <ListenBand />
        {/* The Sync band — Auto Sync and Recent Syncs merged into one
            full-width section (they were the same system explained twice):
            one row per playlist with schedule, latest run, ownership, and
            live pipeline state. Active Downloads appears below only while
            transfers exist. Everything else the old ops grid held is
            rehomed where you act on it: the Services card retired (status
            dots + Test buttons live on the sidebar's Service Status rows,
            rate graphs in the Manage Workers modal), System Stats went to
            the notification tray with 3.2.0, Recent Activity to the tray,
            Quick Actions back to the sidebar. */}
        <SyncBand />
        {/* Beside it: the rest of the engine — every automation that ISN'T a
            playlist pipeline, plus the quick performance switches. */}
        <AutomationsCard />
        <ActiveDownloadsShell />
      </div>
    </div>
  );
}
