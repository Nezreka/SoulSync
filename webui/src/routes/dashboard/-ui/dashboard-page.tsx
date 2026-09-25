/**
 * The Dashboard page: a hero (greeting, library size and controls, the worker
 * orbs on their own stage), then two columns. Music on the left, what you
 * came to play and what's new. The system on the right, calm and compact:
 * playlist sync health, what the engine runs next, watchlist and wishlist.
 * Boulder's quick switches sit in a quiet footer.
 *
 * The `page` class is NOT here (the label-detail trap: the shell styles
 * `.page { display:none }` and only vanilla pages get `.active`). The
 * `#dashboard-page` id IS kept: worker-orbs.js anchors
 * `#dashboard-page .orb-stage`, the CSS overrides are descendant selectors,
 * and the tour targets live ids.
 *
 * The page owns the header hook once: its watchlist/wishlist counts feed the
 * rail tiles as well as the hero, and a second hook would double every fetch
 * and socket subscription behind it.
 *
 * The mount effect re-pings worker-orbs: the shell bridge calls
 * setPage('dashboard') BEFORE React paints, so the orb layer's lazy re-anchor
 * needs one call at a moment the stage actually exists. (setPage is
 * idempotent — re-anchoring only runs when its anchor is missing or
 * unmounted.)
 */

import { useEffect } from 'react';

import { useDashboardHeader } from '../-dash.header';
import { ActiveDownloadsShell } from './active-downloads-shell';
import { AlertsBand } from './alerts-band';
import { AutomationsCard, QuickSettings } from './automations-card';
import { ContentBand } from './content-rails';
import { DashboardHeaderView, QuickNavTiles } from './dashboard-header';
import { LibraryCard } from './library-card';
import { ListenBand } from './listen-band';
import { ListeningHistoryBand } from './listening-history-band';
import { SyncRail } from './sync-band';

export function DashboardPage() {
  const header = useDashboardHeader();

  useEffect(() => {
    window.workerOrbs?.setPage('dashboard');
  }, []);

  return (
    <div className="page-shell dashboard-container" id="dashboard-page">
      <DashboardHeaderView header={header} library={<LibraryCard />} />
      {/* The exception surface: renders NOTHING while every core connection
          is healthy — the one place that shouts when a human is needed. */}
      <AlertsBand />
      <div className="dash-layout">
        <div className="dash-main">
          {/* Only while something is downloading. It stays full width: the
              download cards are painted by vanilla into this shell. */}
          <ActiveDownloadsShell />
          {/* The payoff: Library Radio and the Mixes doorway. */}
          <ListenBand />
          {/* What's new in the library. Renders nothing until a feed has rows. */}
          <ContentBand />
          {/* What you've been playing. Renders nothing until history exists. */}
          <ListeningHistoryBand />
        </div>
        <aside className="dash-side" aria-label="Your system">
          <SyncRail />
          <AutomationsCard compact />
          <QuickNavTiles header={header} />
        </aside>
      </div>
      <footer className="dash-footer">
        <QuickSettings />
      </footer>
    </div>
  );
}
