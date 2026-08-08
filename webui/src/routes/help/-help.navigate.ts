/**
 * The app-wide `navigateToDocsSection(sectionId)` contract, React edition.
 *
 * Vanilla callers (toast "Learn more →" links and the notification panel in
 * downloads.js, helper-mode deep links) call `window.navigateToDocsSection`
 * without knowing which runtime owns the help page. The legacy docs.js used
 * to define it; after the flip this module does:
 *
 *  - not on /help yet → stash the target and route through the shell bridge;
 *    the page consumes the pending id on mount.
 *  - already on /help → dispatch an event the mounted page listens for.
 *
 * Registered from the route module, which the generated route tree imports
 * eagerly — so the global exists as soon as the React bundle boots, matching
 * the script-tag timing vanilla callers were written against.
 */

export const DOCS_NAVIGATE_EVENT = 'ss:docs-navigate';

let pendingSectionId: string | null = null;

export function consumePendingDocsSection(): string | null {
  const id = pendingSectionId;
  pendingSectionId = null;
  return id;
}

declare global {
  interface Window {
    navigateToDocsSection?: (sectionId: string) => void;
  }
}

export function registerDocsNavigationBridge(): void {
  window.navigateToDocsSection = (sectionId: string) => {
    pendingSectionId = sectionId;
    window.dispatchEvent(new CustomEvent(DOCS_NAVIGATE_EVENT, { detail: { sectionId } }));
    void window.SoulSyncWebRouter?.navigateToPage('help');
  };
}
