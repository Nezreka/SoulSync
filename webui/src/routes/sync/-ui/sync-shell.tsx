/**
 * The sync page's chrome — header, the sixteen-tab strip, and the panel
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
  SYNC_PRIMARY_TAB_IDS,
  SYNC_HEADER_ACTIONS,
  SYNC_TABS,
  forgetRoutedTab,
  normalizeSyncMode,
  readRememberedRoutedTabs,
  readSyncMode,
  rememberRoutedTab,
  syncStripTabs,
  normalizeSyncTab,
  writeSyncMode,
  type SyncModeId,
  type SyncTabId,
} from '../-sync.shell';
import { SYNC_VIEWS, normalizeSyncView, type SyncViewId } from '../-sync.views';
import './sync-overhaul.css';
// The Library view's stylesheet. Loaded by the shell, which owns the
// `.pl-library` wrapper — every rule is scoped under it, so nothing leaks
// onto other views. (It used to ride on PlaylistCard, but an empty mirrored
// list renders zero cards and the Server tab uses its own card, leaving the
// library chrome unstyled in both cases.)
import './sync-overhaul-library.css';

export interface SyncShellProps {
  /** One node per tab id. A tab with no entry renders an empty panel. */
  panels: Partial<Record<SyncTabId, ReactNode>>;
  /** The Overview view — mission control. Mounted only when active. */
  overview?: ReactNode;
  /** The Discover view — the curation pipeline. Mounted only when active. */
  discover?: ReactNode;
  onAutoSync: () => void;
  onActivity: () => void;
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
  onAddPlaylist?: (anchor: { top: number; left: number; el: HTMLElement }) => void;
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

/** 2237-2241. The rest are vanilla seams; see -sync.shell.ts.
 *
 * Exported so the Discover view's stage cards and the Overview's action rows
 * can reach the same actions the old header buttons ran — one implementation,
 * not three copies.
 */
export function runSyncHeaderAction(key: string, onAutoSync: () => void, onActivity: () => void) {
  runHeaderAction(key, onAutoSync, onActivity);
}

/** The header actions that keep a button in the new header: the two actions
 *  that CHANGE what the page will do. Everything else moved to the view
 *  where it belongs — see the view map in the plan. */
const HEADER_BUTTON_KEYS: readonly string[] = ['auto-sync'];

function headerButtons() {
  return SYNC_HEADER_ACTIONS.filter((a) => HEADER_BUTTON_KEYS.includes(a.key));
}

/** 2237-2241. The rest are vanilla seams; see -sync.shell.ts. */
function runHeaderAction(key: string, onAutoSync: () => void, onActivity: () => void) {
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
  if (key === 'activity') {
    // React now, not window.openSyncHistoryModal: Activity holds the sync
    // history AND the scheduled-run history, and the vanilla modal knows about
    // only the first of those.
    onActivity();
    return;
  }
  // 2241 passes the literal 'playlist' — the modal is shared with other pages
  // and filters on it.
  window.openDownloadOriginsModal?.('playlist');
}

export function SyncShell({
  panels,
  overview,
  discover,
  onAddPlaylist,
  onAutoSync,
  onActivity,
  sidebar,
  sidebarVisible,
  onTabChange,
  registerOpenTab,
}: SyncShellProps) {
  /**
   * The page's two compositions. Standard is the overhaul (Overview / Library
   * / Discover) and the default; Advanced is the page as it was before — the
   * full tab strip and the six-button header, unreskinned. The choice persists;
   * everything underneath (panels, modals, vanilla seams) is shared.
   */
  const [mode, setMode] = useState<SyncModeId>(() => readSyncMode());
  const goMode = useCallback((next: SyncModeId) => {
    const m = normalizeSyncMode(next);
    setMode(m);
    writeSyncMode(m);
  }, []);
  const [view, setView] = useState<SyncViewId>('overview');
  /**
   * The library's panels keep the port's one-shot mounting ACROSS views, not
   * just across tabs: once the library has been opened, its subtree stays
   * mounted and is only hidden, so leaving for Overview or Discover does not
   * throw away what the tabs loaded. Overview and Discover are new views
   * designed fetch-on-mount — mission control should be fresh each visit —
   * so only the library gets the keep-alive treatment.
   */
  const [librarySeen, setLibrarySeen] = useState(false);
  const goView = useCallback((next: SyncViewId) => {
    const v = normalizeSyncView(next);
    if (v === 'library') setLibrarySeen(true);
    setView(v);
  }, []);
  const [tab, setTab] = useState<SyncTabId>(SYNC_DEFAULT_TAB);
  // Which panels have ever been opened. See the header note: the vanilla's
  // one-shot load flags mean a tab keeps what it loaded after you leave it.
  // Seeded with routed tabs remembered from earlier visits, so a link tab's
  // chip survives a reload the way its cached playlists already did.
  const [opened, setOpened] = useState<Set<SyncTabId>>(
    () => new Set([SYNC_DEFAULT_TAB, ...readRememberedRoutedTabs()]),
  );
  /** routed chips the user hid with × (#1402). */
  const [hidden, setHidden] = useState<ReadonlySet<SyncTabId>>(() => new Set());

  // Both props live in refs so `open` can be STABLE. A host that writes
  // `onTabChange={() => …}` inline hands a new function every render, and an
  // `open` rebuilt each time would re-fire the registration effect forever —
  // the trap useAutoSync's `now` fell into, applied here while writing.
  const onTabChangeRef = useRef(onTabChange);
  onTabChangeRef.current = onTabChange;
  const registerOpenTabRef = useRef(registerOpenTab);
  registerOpenTabRef.current = registerOpenTab;

  const open = useCallback(
    (next: SyncTabId) => {
      const id = normalizeSyncTab(next);
      onTabChangeRef.current?.();
      if (mode === 'standard') {
        // Beatport and SoulSync Discovery live in the Discover view now; every
        // other tab is library. Routing through the view keeps one navigation
        // model: Add playlist can still land you on a source tab from any view.
        // (Discovery must route here, not to the library strip — DiscoverView
        // already mounts that panel, and a second mount in the hidden library
        // div would double-fetch and diverge.)
        if (id === 'beatport' || id === 'soulsync-discovery-sync') {
          goView('discover');
          return;
        }
        goView('library');
      }
      // Advanced mode has no views — the strip IS the navigation, exactly as
      // before the overhaul, so beatport and discovery open as plain tabs.
      setTab(id);
      setOpened((prev) => (prev.has(id) ? prev : new Set(prev).add(id)));
      setHidden((prev) => {
        if (!prev.has(id)) return prev;
        const nextHidden = new Set(prev);
        nextHidden.delete(id);
        return nextHidden;
      });
      rememberRoutedTab(id);
    },
    [goView, mode],
  );

  useEffect(() => {
    registerOpenTabRef.current?.(open);
  }, [open]);

  /** the × on a routed chip (#1402). hides the chip only, the panel stays
   *  mounted like it does everywhere else here, so reopening the source from
   *  Add playlist finds what it had. the three permanent tabs have no ×. */
  const close = useCallback(
    (id: SyncTabId) => {
      forgetRoutedTab(id);
      setHidden((prev) => new Set(prev).add(id));
      if (tab === id) {
        onTabChangeRef.current?.();
        setTab(SYNC_DEFAULT_TAB);
      }
    },
    [tab],
  );

  // The tab strip and its panels, shared by both modes. Standard wraps them in
  // .pl-library (the reskin) and hides the two tabs that moved to Discover;
  // Advanced renders them bare, exactly as before the overhaul.
  const stripTabs = syncStripTabs(
    tab,
    [...opened].filter((id) => !hidden.has(id)),
  );
  // Beatport and SoulSync Discovery moved to the Discover view; their chips no
  // longer belong in the library's strip — in standard mode. Advanced keeps
  // the strip exactly as it was, both chips included. The panels still exist
  // either way and `open(...)` routes to Discover in standard mode.
  const visibleStripTabs =
    mode === 'standard'
      ? stripTabs.filter((t) => t.id !== 'beatport' && t.id !== 'soulsync-discovery-sync')
      : stripTabs;

  const libraryChrome = (
    <>
      <div className="sync-tabs" role="tablist">
        {visibleStripTabs.map((t) => (
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
            {SYNC_PRIMARY_TAB_IDS.includes(t.id) ? null : (
              <button
                type="button"
                className="sync-tab-close"
                title={`Hide ${t.label}. Add playlist brings it back`}
                aria-label={`Hide ${t.label} tab`}
                onClick={() => {
                  close(t.id);
                }}
              >
                ×
              </button>
            )}
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
    </>
  );

  // First in the action row, in both modes: it chooses the page's whole
  // composition, so it outranks the view switcher and the action buttons.
  const modeToggle = (
    <div className="pl-mode-switch" role="tablist" aria-label="Playlists page mode">
      {(
        [
          {
            id: 'standard',
            label: 'Standard',
            title: 'The new layout: Overview, Library and Discover',
          },
          {
            id: 'advanced',
            label: 'Advanced',
            title: 'The classic layout: the full tab strip and every header button',
          },
        ] as const
      ).map((m) => (
        <button
          key={m.id}
          type="button"
          role="tab"
          aria-selected={mode === m.id}
          title={m.title}
          onClick={() => goMode(m.id)}
        >
          {m.label}
        </button>
      ))}
    </div>
  );

  // Primary, and first among the actions: adding a playlist is what this page
  // is FOR. Shared by both modes.
  const addPlaylistButton = onAddPlaylist && (
    <button
      type="button"
      className="btn btn--sm sync-add-playlist-btn"
      title="Paste a link, pick a connected account, or import a file"
      onClick={(e) => {
        // Pops in AT the button, like the card's overflow menu. The
        // element rides along so clicking the button again closes the
        // sheet instead of racing the outside-click handler.
        const box = e.currentTarget.getBoundingClientRect();
        onAddPlaylist({ top: box.bottom + 8, left: box.left, el: e.currentTarget });
      }}
    >
      + Add playlist
    </button>
  );

  return (
    // `page-shell` plus the page id, matching the convention every flipped
    // route follows (dashboard-page.tsx 38). The vanilla nests page-shell
    // inside `<div class="page" id="sync-page">`; the React roots collapse the
    // two and keep the id, which is how the legacy chrome still resolves a
    // page by `${pageId}-page`. Standard adds pl-overhaul (the reskin);
    // Advanced renders bare, exactly as before the overhaul.
    <div className={mode === 'standard' ? 'page-shell pl-overhaul' : 'page-shell'} id="sync-page">
      <div className="sync-header">
        <div className="sync-header-row">
          <div>
            <h2 className="sync-title">
              <img src="/static/sync.png" className="page-header-icon" alt="" />
              <span>Playlists</span>
            </h2>
            <p className="sync-subtitle">
              Manage, mirror, and synchronize your playlists with your media server
            </p>
          </div>
          {/* The vanilla styles this row inline (2236). A class is used here
              because the port does not emit inline styles; the rule is a 1:1
              transcription of those three declarations. */}
          <div className="sync-header-actions">
            {modeToggle}
            {mode === 'standard' ? (
              <>
                {/* The view switcher: one control, three intents. It sits with the
                    primary actions because switching views IS the page's primary
                    navigation now. */}
                <div className="pl-view-switch" role="tablist" aria-label="Playlists views">
                  {SYNC_VIEWS.map((v) => (
                    <button
                      key={v.id}
                      type="button"
                      role="tab"
                      aria-selected={view === v.id}
                      onClick={() => goView(v.id)}
                    >
                      {v.label}
                    </button>
                  ))}
                </div>
                {addPlaylistButton}
                {/* Bulk schedule is the only header action left: the two actions
                    that CHANGE what the page will do. Match Review, Wing It Pool
                    and Library Match moved to the Discover view's pipeline;
                    Activity and Download Origins moved to the Overview. The
                    vanilla seams are untouched — runSyncHeaderAction is exported
                    so those views call the same implementation. */}
                {headerButtons().map((action) => (
                  <button
                    key={action.key}
                    type="button"
                    className={`btn btn--sm btn--secondary sync-history-btn${
                      action.key === 'auto-sync' ? ' auto-sync-manager-btn' : ''
                    }`}
                    title={action.title}
                    onClick={() => {
                      runHeaderAction(action.key, onAutoSync, onActivity);
                    }}
                  >
                    {action.label}
                  </button>
                ))}
              </>
            ) : (
              <>
                {addPlaylistButton}
                {SYNC_HEADER_ACTIONS.map((action, index) => (
                  <Fragment key={action.key}>
                    {/* Divides the action that CHANGES something from the ones
                        that only report what already happened. */}
                    {index === 1 && <span className="sync-header-divider" />}
                    <button
                      type="button"
                      className={`btn btn--sm btn--secondary sync-history-btn${
                        action.key === 'auto-sync' ? ' auto-sync-manager-btn' : ''
                      }`}
                      title={action.title}
                      onClick={() => {
                        runHeaderAction(action.key, onAutoSync, onActivity);
                      }}
                    >
                      {action.label}
                    </button>
                  </Fragment>
                ))}
              </>
            )}
          </div>
        </div>
      </div>

      <div
        className={`sync-content-area${sidebarVisible ? ' sync-content-area--with-sidebar' : ''}`}
      >
        <div className="sync-main-panel">
          {mode === 'standard' ? (
            <>
              {view === 'overview' && overview}
              {view === 'discover' && discover}
              {librarySeen && (
                <div
                  className="pl-library"
                  style={view === 'library' ? undefined : { display: 'none' }}
                >
                  {libraryChrome}
                </div>
              )}
            </>
          ) : (
            libraryChrome
          )}
        </div>
        {sidebar}
      </div>
    </div>
  );
}
