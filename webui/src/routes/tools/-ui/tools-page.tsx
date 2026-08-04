/**
 * The Tools page shell.
 *
 * `page-shell tools-page-container` and the section structure are transcribed
 * from index.html so style.css applies unchanged. Note what is NOT here: the
 * `page` class. The shell styles `.page { display: none }` and only adds
 * `.active` to vanilla pages, so a React page carrying it renders invisible with
 * every test still passing — the trap that cost a round on the label-detail port.
 *
 * The `#tools-page` id IS kept: helper.js's search walks up the DOM matching
 * HELPER_CONTENT selectors, and the bugfix PR pointed its page hint at 'tools'.
 *
 * All eleven cards are present. Section order, and card order within each
 * section, both come from index.html. The route is not flipped until P7.
 */

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

export function ToolsPage() {
  return (
    <div className="page-shell tools-page-container" id="tools-page">
      <div className="tools-page-header">
        <div className="tools-page-header-left">
          <h2 className="tools-page-title">
            <svg
              width="22"
              height="22"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />
            </svg>
            Tools &amp; Operations
          </h2>
          <p className="tools-page-subtitle">
            Database management, library scanning, metadata, backups
          </p>
        </div>
      </div>

      <MaintenanceHero />

      <ToolsSection title="Database & Scanning">
        <DbUpdaterCard />
        <ReconcileIdsCard />
        <DuplicateCleanerCard />
        <MediaScanCard />
      </ToolsSection>

      <ToolsSection title="Metadata & Cache">
        <MetadataUpdaterCard />
        <DiscoveryPoolCard />
        <ManualLibraryMatchCard />
      </ToolsSection>

      <ToolsSection title="Management">
        <BackupManagerCard />
        <ConfigMigrationCard />
        <MetadataCacheCard />
        <BlacklistCard />
      </ToolsSection>
    </div>
  );
}
