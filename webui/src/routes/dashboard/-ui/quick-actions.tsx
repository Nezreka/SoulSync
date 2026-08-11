/**
 * The Quick Actions launcher (dash-card data-card="tools") — the asymmetric
 * bento of three tiles. Markup 1:1 from index.html incl. every signature-
 * animation SVG (artefact differential pins it).
 *
 * ADOPTED CLASS: core.js's 2s interval toggles `is-live` on
 * .qa-tile--sync/tools/auto via document-wide querySelector (driven by
 * qaSignal pings from the socket handlers). This component is deliberately
 * STATIC — no state, no props — so React never re-renders the tiles and the
 * vanilla's classList toggles survive untouched.
 */

export function QuickActionsCard() {
  return (
    <article className="dash-card dash-card--quick-actions" data-card="tools">
      <header className="dash-card__head">
        <h3 className="dash-card__title">Quick Actions</h3>
        <p className="dash-card__sub">Three control rooms inside SoulSync.</p>
      </header>
      <div className="dash-card__body qa-bento">
        <button
          className="qa-tile qa-tile--hero qa-tile--sync"
          aria-label="Open Auto-Sync"
          onClick={() => void window.openAutoSyncScheduleModal?.()}
        >
          <div className="qa-tile__bg" aria-hidden="true">
            <div className="qa-tile__eq">
              <span></span>
              <span></span>
              <span></span>
              <span></span>
              <span></span>
              <span></span>
              <span></span>
              <span></span>
              <span></span>
              <span></span>
              <span></span>
              <span></span>
              <span></span>
              <span></span>
              <span></span>
              <span></span>
              <span></span>
              <span></span>
              <span></span>
              <span></span>
            </div>
          </div>
          <div className="qa-tile__topline">
            <span className="qa-tile__pulse" aria-hidden="true"></span>
            <span className="qa-tile__kicker">Playlist pipeline</span>
          </div>
          <div className="qa-tile__icon">
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M3 12a9 9 0 0 1 15.5-6.2" />
              <path d="M18.5 3.5v5h-5" />
              <path d="M21 12a9 9 0 0 1-15.5 6.2" />
              <path d="M5.5 20.5v-5h5" />
            </svg>
          </div>
          <div className="qa-tile__heading">
            <strong className="qa-tile__title">Auto-Sync</strong>
            <p className="qa-tile__desc">
              Refresh, discover, sync, wishlist — running on a schedule you set.
            </p>
          </div>
          <div className="qa-tile__cta">
            <span className="qa-tile__cta-label">Manage Schedule</span>
            <span className="qa-tile__cta-arrow" aria-hidden="true">
              <svg
                width="16"
                height="16"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <line x1="5" y1="12" x2="19" y2="12" />
                <polyline points="13 6 19 12 13 18" />
              </svg>
            </span>
          </div>
        </button>
        <button
          className="qa-tile qa-tile--minor qa-tile--tools"
          aria-label="Open Tools"
          onClick={() => void window.navigateToPage?.('tools')}
        >
          <div className="qa-tile__bg" aria-hidden="true">
            <div className="qa-tile__gear">
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <circle cx="12" cy="12" r="3" />
                <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
              </svg>
            </div>
          </div>
          <div className="qa-tile__topline">
            <span className="qa-tile__kicker">Maintenance</span>
          </div>
          <div className="qa-tile__heading">
            <strong className="qa-tile__title">Tools</strong>
            <p className="qa-tile__desc">Database, scanning, repair, backups.</p>
          </div>
          <div className="qa-tile__cta">
            <span className="qa-tile__cta-label">Open</span>
            <span className="qa-tile__cta-arrow" aria-hidden="true">
              <svg
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <line x1="5" y1="12" x2="19" y2="12" />
                <polyline points="13 6 19 12 13 18" />
              </svg>
            </span>
          </div>
        </button>
        <button
          className="qa-tile qa-tile--minor qa-tile--auto"
          aria-label="Open Automations"
          onClick={() => void window.navigateToPage?.('automations')}
        >
          <div className="qa-tile__bg" aria-hidden="true">
            <div className="qa-tile__flow">
              <span className="qa-flow-node"></span>
              <span className="qa-flow-line"></span>
              <span className="qa-flow-node"></span>
              <span className="qa-flow-line"></span>
              <span className="qa-flow-node"></span>
            </div>
          </div>
          <div className="qa-tile__topline">
            <span className="qa-tile__kicker">Trigger → action</span>
          </div>
          <div className="qa-tile__heading">
            <strong className="qa-tile__title">Automations</strong>
            <p className="qa-tile__desc">Events, schedules, signals, then-actions.</p>
          </div>
          <div className="qa-tile__cta">
            <span className="qa-tile__cta-label">Open</span>
            <span className="qa-tile__cta-arrow" aria-hidden="true">
              <svg
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <line x1="5" y1="12" x2="19" y2="12" />
                <polyline points="13 6 19 12 13 18" />
              </svg>
            </span>
          </div>
        </button>
      </div>
    </article>
  );
}
