/**
 * The sync page's chrome — header, the fifteen-tab strip, and the panel
 * switch. index.html 2226-2295 plus the tab handler at sync-services.js
 * 3694-3803.
 *
 * THE VANILLA'S TAB HANDLER DOES FOUR THINGS; only one survives as code here.
 * It moved the `active` class on the buttons and panels (that is this
 * component), re-hid the sidebar (S2 owns that), ran a one-shot lazy load per
 * tab, and computed an `isMobile` const it never read.
 *
 * The lazy loads dissolve. Each was `if (tabId === 'x' && !xLoaded) { xLoaded
 * = true; loadX(); }` against a script-scoped or `window` flag — a hand-rolled
 * mount hook, needed because the panels all exist in the DOM from page load
 * and only their `active` class changes. Here each panel's content is a
 * component that fetches on mount, so "first time this tab is opened" is just
 * "first time this subtree renders". The flags have no counterpart — but the
 * `opened` Set below does the other half of their job: a panel stays MOUNTED
 * once opened rather than unmounting on the way out, which is what makes
 * leaving a tab keep what it loaded, exactly as the one-shot flags did.
 *
 * Beatport is the one exception in the vanilla — it has a teardown
 * (`cleanupBeatportContent`) on leaving. That belongs to the Beatport wave's
 * own components, not the shell.
 */

import { Fragment, useCallback, useEffect, useRef, useState, type ReactNode } from 'react';

import {
  SYNC_DEFAULT_TAB,
  SYNC_HEADER_ACTIONS,
  SYNC_TABS,
  syncStripTabs,
  normalizeSyncTab,
  type SyncTabId,
} from '../-sync.shell';

export interface SyncShellProps {
  /** One node per tab id. A tab with no entry renders an empty panel. */
  panels: Partial<Record<SyncTabId, ReactNode>>;
  onAutoSync: () => void;
  /** The right-hand sidebar (S2). Rendered as the second grid column. */
  sidebar?: ReactNode;
  /**
   * Whether that sidebar is currently shown. Drives the grid only — the
   * sidebar owns its own display. The vanilla wrote both inline together
   * (`showSyncSidebar`/`hideSyncSidebar`, downloads.js 4041-4057); splitting
   * them keeps each element's visibility in the component that renders it,
   * and the two classes are transcriptions of those inline styles.
   */
  sidebarVisible?: boolean;
  /**
   * Fired on every tab switch, including a click on the tab already active —
   * the vanilla's handler runs its whole body regardless (sync-services.js
   * 3732), and its unconditional sidebar re-hide is what the page keys off.
   * Filtering out same-tab clicks here would change that.
   */
  /**
   * Opens the Add-playlist sheet. The PRIMARY action on this page: it is the
   * one entry point that replaces choosing a source tab before you have even
   * pasted anything.
   */
  onAddPlaylist?: (anchor: { top: number; left: number }) => void;
  onTabChange?: () => void;
  /**
   * Hand the host a function that opens a tab programmatically.
   *
   * The shell owns tab state, so a panel that needs to send the user elsewhere
   * cannot do it alone — the import tab is the case: importFileSubmit's tail
   * clicked the mirrored tab button and reloaded that list (sync-services.js
   * 449-455). Registration upward is the idiom this port already uses for
   * MirroredTab's reload and the Spotify tab's row order, rather than lifting
   * tab state into the page and re-plumbing every panel.
   *
   * Optional: the shell stands alone in its own tests, and nothing breaks if a
   * host does not want it. Opening a tab this way marks it opened and fires
   * `onTabChange`, exactly as a click does — the vanilla got there BY clicking
   * the button, so the sidebar re-hide must happen too.
   */
  registerOpenTab?: (open: (tab: SyncTabId) => void) => void;
}

/** 2237-2241. Three of the four are vanilla seams; see -sync.shell.ts. */
function runHeaderAction(key: string, onAutoSync: () => void) {
  if (key === 'discovery-pool') {
    window.openDiscoveryPoolModal?.();
    return;
  }
  if (key === 'wing-it-pool') {
    window.openWingItPoolModal?.();
    return;
  }
  if (key === 'auto-sync') {
    onAutoSync();
    return;
  }
  if (key === 'library-match') {
    window.openManualLibraryMatchTool?.();
    return;
  }
  if (key === 'sync-history') {
    window.openSyncHistoryModal?.();
    return;
  }
  // 2241 passes the literal 'playlist' — the modal is shared with other pages
  // and filters on it.
  window.openDownloadOriginsModal?.('playlist');
}

export function SyncShell({
  panels,
  onAddPlaylist,
  onAutoSync,
  sidebar,
  sidebarVisible,
  onTabChange,
  registerOpenTab,
}: SyncShellProps) {
  const [tab, setTab] = useState<SyncTabId>(SYNC_DEFAULT_TAB);
  // Which panels have ever been opened. See the header note: the vanilla's
  // one-shot load flags mean a tab keeps what it loaded after you leave it.
  const [opened, setOpened] = useState<Set<SyncTabId>>(() => new Set([SYNC_DEFAULT_TAB]));

  // Both props live in refs so `open` can be STABLE. A host that writes
  // `onTabChange={() => …}` inline hands a new function every render, and an
  // `open` rebuilt each time would re-fire the registration effect forever —
  // the trap useAutoSync's `now` fell into, applied here while writing.
  const onTabChangeRef = useRef(onTabChange);
  onTabChangeRef.current = onTabChange;
  const registerOpenTabRef = useRef(registerOpenTab);
  registerOpenTabRef.current = registerOpenTab;

  const open = useCallback((next: SyncTabId) => {
    const id = normalizeSyncTab(next);
    onTabChangeRef.current?.();
    setTab(id);
    setOpened((prev) => (prev.has(id) ? prev : new Set(prev).add(id)));
  }, []);

  useEffect(() => {
    registerOpenTabRef.current?.(open);
  }, [open]);

  return (
    // `page-shell` plus the page id, matching the convention every flipped
    // route follows (dashboard-page.tsx 38). The vanilla nests page-shell
    // inside `<div class="page" id="sync-page">`; the React roots collapse the
    // two and keep the id, which is how the legacy chrome still resolves a
    // page by `${pageId}-page`.
    <div className="page-shell" id="sync-page">
      <div className="sync-header">
        <div className="sync-header-row">
          <div>
            <h2 className="sync-title">
              <img src="/static/sync.png" className="page-header-icon" alt="" />
              <span>Playlist Sync</span>
            </h2>
            <p className="sync-subtitle">
              Synchronize your Spotify, Tidal, and YouTube playlists with your media server
            </p>
          </div>
          {/* The vanilla styles this row inline (2236). A class is used here
              because the port does not emit inline styles; the rule is a 1:1
              transcription of those three declarations. */}
          <div className="sync-header-actions">
            {/* Primary, and first: adding a playlist is what this page is FOR.
                The four buttons beside it are all "what happened" surfaces. */}
            {onAddPlaylist && (
              <button
                type="button"
                className="btn btn--sm sync-add-playlist-btn"
                title="Paste a link, pick a connected account, or import a file"
                onClick={(e) => {
                  // Pops in AT the button, like the card's overflow menu.
                  const box = e.currentTarget.getBoundingClientRect();
                  onAddPlaylist({ top: box.bottom + 8, left: box.left });
                }}
              >
                + Add playlist
              </button>
            )}
            {SYNC_HEADER_ACTIONS.map((action, index) => (
              <Fragment key={action.key}>
                {/* Divides the two actions that CHANGE something from the four
                    that only report what already happened. */}
                {index === 1 && <span className="sync-header-divider" />}
              <button
                type="button"
                className={`btn btn--sm btn--secondary sync-history-btn${
                  action.key === 'auto-sync' ? ' auto-sync-manager-btn' : ''
                }`}
                title={action.title}
                onClick={() => {
                  runHeaderAction(action.key, onAutoSync);
                }}
              >
                {action.label}
              </button>
              </Fragment>
            ))}
          </div>
        </div>
      </div>

      <div
        className={`sync-content-area${sidebarVisible ? ' sync-content-area--with-sidebar' : ''}`}
      >
        <div className="sync-main-panel">
          <div className="sync-tabs" role="tablist">
            {syncStripTabs(tab).map((t) => (
              <Fragment key={t.id}>
                <button
                  type="button"
                  role="tab"
                  aria-selected={tab === t.id}
                  className={`sync-tab-button${t.id === 'server' ? ' sync-tab-server' : ''}${
                    tab === t.id ? ' active' : ''
                  }`}
                  data-tab={t.id}
                  {...(t.link ? { 'data-link': 'true' } : {})}
                  title={t.label}
                  onClick={() => {
                    open(t.id);
                  }}
                >
                  <span className={`tab-icon ${t.icon}`} />
                  <span className="sync-tab-label">{t.label}</span>
                </button>
                {/* 2253: the divider sits after Server Playlists only. */}
              </Fragment>
            ))}
          </div>

          {SYNC_TABS.map((t) => (
            <div
              key={t.id}
              className={`sync-tab-content${tab === t.id ? ' active' : ''}`}
              id={`${t.id}-tab-content`}
              role="tabpanel"
            >
              {opened.has(t.id) ? panels[t.id] : null}
            </div>
          ))}
        </div>
        {sidebar}
      </div>
    </div>
  );
}
