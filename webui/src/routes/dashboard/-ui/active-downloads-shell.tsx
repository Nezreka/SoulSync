/**
 * The Active Downloads card (dash-card data-card="active-downloads") — an
 * ADOPTED REGION, not a port.
 *
 * updateDashboardDownloads (wishlist-tools.js) renders this card's content
 * from FOUR script-scoped bubble registries (artistDownloadBubbles,
 * searchDownloadBubbles, discoverDownloads, beatportDownloadBubbles) that are
 * fed event-driven from artist/search/discover/beatport flows all over the
 * app, with card builders and modal openers that other pages share. None of
 * that state is reachable from a module, and duplicating the four builders
 * would drift.
 *
 * So React renders ONLY the static shell — the article (display:none), the
 * header, and the empty #dashboard-downloads-container — and the vanilla
 * keeps writing innerHTML into the container and toggling the section's
 * display, exactly as it does today. That is safe because this component is
 * STATIC: it renders once, its vdom never changes, so React never touches
 * the container's children or the section's style again.
 *
 * On mount it triggers the vanilla pair loadDashboardData used to run:
 * checkForActiveProcesses (rehydrates the registries from
 * /api/active-processes) and then updateDashboardDownloads (first paint).
 */

import { useEffect } from 'react';

export function ActiveDownloadsShell() {
  useEffect(() => {
    void (async () => {
      try {
        await window.checkForActiveProcesses?.();
      } catch {
        // the vanilla load path swallows this too
      }
      window.updateDashboardDownloads?.();
    })();
  }, []);

  return (
    <article
      className="dash-card dash-card--full"
      id="dashboard-active-downloads-section"
      style={{ display: 'none' }}
      data-card="active-downloads"
    >
      <header className="dash-card__head">
        <h3 className="dash-card__title">Active Downloads</h3>
        <p className="dash-card__sub">In-flight transfers from your sources.</p>
      </header>
      <div className="dash-card__body">
        <div id="dashboard-downloads-container"></div>
      </div>
    </article>
  );
}
