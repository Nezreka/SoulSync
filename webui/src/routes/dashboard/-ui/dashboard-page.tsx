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

import { FinishSetupCard } from '@/features/onboarding/checklist-card';
import { useFeatureOn } from '@/platform/features';

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
import { WeeklyDigest } from './weekly-digest';

export function DashboardPage() {
  const header = useDashboardHeader();
  // cards for parts this profile switched off stay off the dashboard (#1909)
  const statsOn = useFeatureOn('stats');
  const playlistsOn = useFeatureOn('playlists');
  const automationsOn = useFeatureOn('automations');

  useEffect(() => {
    window.workerOrbs?.setPage('dashboard');
  }, []);

  return (
    <div className="page-shell dashboard-container dash-room" id="dashboard-page">
      <DashboardHeaderView header={header} library={<LibraryCard />} />
      {/* The exception surface: renders NOTHING while every core connection
          is healthy — the one place that shouts when a human is needed. */}
      <AlertsBand />
      <div className="room-top">
        {/* What setup left for later. Renders nothing once it's all done,
            dismissed, or for anyone who isn't an admin. */}
        <FinishSetupCard />
        {/* Only while something is downloading. The download cards are
            painted by vanilla into this shell. */}
        <ActiveDownloadsShell />
        {/* What you came to play: Library Radio and the mixes, first thing
            under the hero. */}
        <ListenBand />
      </div>
      {/* The music comes first, top to bottom: the mixes, then what you
          played and what just arrived. The system sits under it. The two
          shelves share a row once the page is wide enough for both (the wrap
          is what they measure against); one alone takes the row. */}
      <div className="room-shelves-wrap">
        <div className="room-shelves">
          {/* What you've been playing. Renders nothing until history exists. */}
          <ListeningHistoryBand />
          {/* What's new in the library. Renders nothing until a feed has rows. */}
          <ContentBand />
        </div>
      </div>
      {/* The system as a board: your week, what you watch and wish for,
          playlist sync, what runs next. Two by two, and all four in one row
          once the page is wide enough to give each its own column (the
          wrap is what the board measures itself against). Each card renders
          nothing when its part is off or empty, and the grid closes up. */}
      <div className="room-board-wrap">
        <section className="room-board" aria-label="Your system">
          {statsOn ? <WeeklyDigest /> : null}
          <QuickNavTiles header={header} />
          {playlistsOn ? <SyncRail /> : null}
          {automationsOn ? <AutomationsCard compact timeline /> : null}
        </section>
      </div>
      <footer className="dash-footer">
        <QuickSettings />
      </footer>
    </div>
  );
}
