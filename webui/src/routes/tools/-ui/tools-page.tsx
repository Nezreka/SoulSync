/**
 * The Tools page shell: the title, the four pills (Findings, Jobs, History,
 * Tools) and the panels behind them.
 *
 * Note what is NOT on the root: the `page` class. The shell styles
 * `.page { display: none }` and only adds `.active` to vanilla pages, so a
 * React page carrying it renders invisible with every test still passing.
 *
 * The `#tools-page` id IS kept: helper.js's search walks up the DOM matching
 * HELPER_CONTENT selectors, and its page hint is 'tools'.
 */

import { useEffect, useState } from 'react';

import type { ToolsMedia } from '../-tools.media';

import { setToolsMedia, ToolsMediaContext } from '../-tools.media';
import {
  BlacklistCard,
  ConfigMigrationCard,
  DiscoveryPoolCard,
  ManualLibraryMatchCard,
  MetadataCacheCard,
} from './launcher-cards';
import { MaintenanceHero } from './maintenance-hero';
import { DbUpdaterCard, DuplicateCleanerCard, ReconcileIdsCard } from './scanning-cards';
import { BackupManagerCard, MediaScanCard, MetadataUpdaterCard } from './server-cards';
import { ToolsSection } from './tool-card';
import {
  VideoBackupsCard,
  VideoLibraryScanCard,
  VideoMassRenameCard,
  VideoServerScanCard,
  VideoStudioCards,
} from './video-tool-cards';

export interface ToolsPageProps {
  /** 'video' is the video side's tools page (/tools?media=video). */
  media?: ToolsMedia;
  /** the video maintenance api, backups, rename and the studios are
   *  admin-only on the server, so other profiles don't get them on screen. */
  isAdmin?: boolean;
}

export function ToolsPage({ media = 'music', isAdmin = true }: ToolsPageProps) {
  // during render, not in an effect: children's effects run BEFORE a parent's,
  // so an effect here would let the first fetches go to the other side's api.
  // see -tools.media.ts.
  setToolsMedia(media);
  return (
    <ToolsMediaContext.Provider value={media}>
      {/* keyed: switching sides must not carry one side's jobs, filters or
          open groups into the other */}
      <ToolsPageBody key={media} media={media} isAdmin={isAdmin} />
    </ToolsMediaContext.Provider>
  );
}

/** the four places on the page. findings first: it is the one with work
 *  waiting in it. */
export type ToolsTab = 'findings' | 'jobs' | 'history' | 'tools';

const MAINTENANCE_TABS: ReadonlyArray<readonly [ToolsTab, string]> = [
  ['findings', 'Findings'],
  ['jobs', 'Jobs'],
  ['history', 'History'],
];

function ToolsPageBody({ media, isAdmin }: { media: ToolsMedia; isAdmin: boolean }) {
  const video = media === 'video';
  /* library maintenance changes the shared library, so on the video side it
     lives with the admin profile. everyone else gets the tools they may use. */
  const maintenance = !video || isAdmin;
  const [tab, setTab] = useState<ToolsTab>(maintenance ? 'findings' : 'tools');
  const [pending, setPending] = useState(0);
  /** where the maintenance switch renders: the header is the page's, the
   *  switch's state is the maintenance surface's */
  const [headerSlot, setHeaderSlot] = useState<HTMLDivElement | null>(null);

  /* Anything that jumps straight to a tool card - the helper's search, the
     onboarding checklist's "Run First Library Scan" - finds it MOUNTED but
     hidden once the cards live behind a tab, and helper.js decides an element
     with no offsetParent is not on this page. It then scrolls to nothing and
     anchors a popover to an invisible node.
     So the page publishes a way to ask for the right tab. Returns true when it
     actually had to switch, which is the caller's cue to wait a frame for the
     panel to paint before measuring anything. */
  useEffect(() => {
    const reveal = (selector: string): boolean => {
      if (!selector) return false;
      const target = document.querySelector(selector);
      if (!target) return false;
      const panel = target.closest<HTMLElement>('[role="tabpanel"]');
      if (!panel || !panel.hidden) return false;
      const wanted = panel.id.replace('tools-panel-', '');
      if (wanted !== 'findings' && wanted !== 'jobs' && wanted !== 'history' && wanted !== 'tools')
        return false;
      setTab(wanted);
      return true;
    };
    window.revealToolsTabFor = reveal;
    return () => {
      if (window.revealToolsTabFor === reveal) delete window.revealToolsTabFor;
    };
  }, []);

  const tabs: ReadonlyArray<readonly [ToolsTab, string]> = maintenance
    ? [...MAINTENANCE_TABS, ['tools', 'Tools']]
    : [['tools', 'Tools']];

  return (
    <div
      className={`page-shell tools-page-container${video ? ' tools-page--video' : ''}`}
      id="tools-page"
      data-tools-media={media}
    >
      <div className="tools-top">
        <div className="tools-top-text">
          <h2 className="tools-title">Tools</h2>
          <p className="tools-sub">
            {/* Says what the Issues page ISN'T, so the two stop reading as
                duplicates of each other (#1210). */}
            {video
              ? 'Library maintenance, scans, backups and the studios. Hand-reported problems live on the Issues page.'
              : 'Library maintenance, scans and backups. Hand-reported problems live on the Issues page.'}
          </p>
        </div>
        <div className="tools-top-actions" ref={setHeaderSlot} />
      </div>

      {/* One page, four places. There used to be a Simple and an Advanced
          mode on top of two tabs: the findings only lived in Advanced, so
          every link in Simple quietly switched modes under you. Now each
          thing has one home and the pills say where. */}
      <div className="tools-tabs" role="tablist" aria-label="Tools sections">
        {tabs.map(([value, label]) => (
          <button
            type="button"
            key={value}
            role="tab"
            id={`tools-tab-${value}`}
            aria-selected={tab === value}
            aria-controls={`tools-panel-${value}`}
            className="tools-tab"
            onClick={() => setTab(value)}
          >
            {label}
            {value === 'findings' ? (
              <span
                className="tools-tab-count"
                id="repair-findings-tab-badge"
                style={{ display: pending > 0 ? '' : 'none' }}
              >
                {pending.toLocaleString()}
              </span>
            ) : null}
          </button>
        ))}
      </div>

      {/* The maintenance panels stay MOUNTED, hidden with the hidden attribute
          rather than unmounted. The surface polls job progress and holds the
          findings filter/page you were on; tearing it down on every pill
          would restart the polling and throw that state away. */}
      {maintenance ? (
        <MaintenanceHero
          tab={tab}
          onTab={setTab}
          headerSlot={headerSlot}
          onPendingChanged={setPending}
        />
      ) : (
        <div className="tools-locked-note" role="note">
          <strong>Admins only</strong>
          <span>
            Library maintenance changes the shared library, so it lives with the admin profile.
          </span>
        </div>
      )}

      <div
        role="tabpanel"
        id="tools-panel-tools"
        aria-labelledby="tools-tab-tools"
        hidden={tab !== 'tools'}
      >
        {video ? (
          <>
            <ToolsSection
              title="Database &amp; Scanning"
              blurb="Reading your media server into SoulSync, and keeping the two in step."
            >
              <VideoLibraryScanCard />
              <VideoServerScanCard />
            </ToolsSection>
            {isAdmin ? (
              <>
                <ToolsSection
                  title="Studios"
                  blurb="The visual editors for posters and collections."
                >
                  <VideoStudioCards />
                </ToolsSection>
                <ToolsSection
                  title="Management"
                  blurb="Backups of the video database, and file names that follow your templates."
                >
                  <VideoBackupsCard />
                  <VideoMassRenameCard />
                </ToolsSection>
              </>
            ) : null}
          </>
        ) : (
          <MusicToolSections />
        )}
      </div>
    </div>
  );
}

function MusicToolSections() {
  return (
    <>
      <ToolsSection
        title="Database &amp; Scanning"
        blurb="Reading your media server into SoulSync, and keeping the two in step."
      >
        <DbUpdaterCard />
        <ReconcileIdsCard />
        <DuplicateCleanerCard />
        <MediaScanCard />
      </ToolsSection>

      <ToolsSection
        title="Metadata &amp; Cache"
        blurb="Enriching what SoulSync knows, and fixing what it got wrong."
      >
        <MetadataUpdaterCard />
        <DiscoveryPoolCard />
        <ManualLibraryMatchCard />
      </ToolsSection>

      <ToolsSection
        title="Management"
        blurb="Backups, moving install, and the lists you have told SoulSync to respect."
      >
        <BackupManagerCard />
        <ConfigMigrationCard />
        <MetadataCacheCard />
        <BlacklistCard />
      </ToolsSection>
    </>
  );
}
