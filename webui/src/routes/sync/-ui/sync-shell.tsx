/**
 * The playlists page's chrome: the header, the four collection pills, and the
 * source panels.
 *
 * The page used to be a strip of sixteen tabs, then five chips plus whichever
 * source tabs you had opened. Now the collections are pills (Mirrored, My
 * Playlists, Request Lists, Server Playlists) and every source tab, Beatport
 * included, opens from Add as a panel over the page. Closing a panel returns
 * to the collection under it.
 *
 * Panels stay MOUNTED once opened, collections and sources alike, so leaving
 * one keeps what it loaded (what the vanilla's one-shot load flags did).
 */

import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';

import { useFeatureOn } from '@/platform/features';

import type { SyncLibrarySummary } from './mirrored-tab';

import {
  SYNC_DEFAULT_TAB,
  SYNC_HEADER_ACTIONS,
  SYNC_TABS,
  isPrimarySyncTab,
  normalizeSyncTab,
  syncStripTabs,
  type SyncTabId,
} from '../-sync.shell';
import { ActionMenu } from '../../artist-detail/-ui/action-menu';
import styles from './playlists.module.css';

export interface SyncShellProps {
  /** One node per tab id. A tab with no entry renders an empty panel. */
  panels: Partial<Record<SyncTabId, ReactNode>>;
  onAutoSync: () => void;
  onActivity: () => void;
  /**
   * Opens the Add sheet. The PRIMARY action on this page, and the one door to
   * every source: the source tabs open from there as panels.
   */
  onAddPlaylist?: () => void;
  /** Tools › Update list: re-read the playlist list. */
  onUpdateList?: () => void;
  /** What the header's status line says; omitted until the library loads. */
  summary?: SyncLibrarySummary | null;
  /** Fired on every tab switch, including a click on the tab already active. */
  onTabChange?: () => void;
  /**
   * Hand the host a function that opens a tab programmatically. A collection
   * becomes the page; a source tab opens as a panel over it.
   */
  registerOpenTab?: (open: (tab: SyncTabId) => void) => void;
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
  onAddPlaylist,
  onUpdateList,
  onAutoSync,
  onActivity,
  onTabChange,
  registerOpenTab,
  summary,
}: SyncShellProps) {
  const [tab, setTab] = useState<SyncTabId>(SYNC_DEFAULT_TAB);
  /** the collection under an open source panel, which closing it returns to */
  const [base, setBase] = useState<SyncTabId>(SYNC_DEFAULT_TAB);
  // Which panels have ever been opened: a panel stays MOUNTED once opened, so
  // leaving a tab keeps what it loaded (the vanilla's one-shot load flags).
  const [opened, setOpened] = useState<Set<SyncTabId>>(() => new Set([SYNC_DEFAULT_TAB]));

  // Both props live in refs so `open` can be STABLE (an inline host callback
  // would otherwise re-fire the registration effect forever).
  const onTabChangeRef = useRef(onTabChange);
  onTabChangeRef.current = onTabChange;
  const registerOpenTabRef = useRef(registerOpenTab);
  registerOpenTabRef.current = registerOpenTab;

  const open = useCallback((next: SyncTabId) => {
    const id = normalizeSyncTab(next);
    onTabChangeRef.current?.();
    setTab(id);
    if (isPrimarySyncTab(id)) setBase(id);
    setOpened((prev) => (prev.has(id) ? prev : new Set(prev).add(id)));
  }, []);

  useEffect(() => {
    registerOpenTabRef.current?.(open);
  }, [open]);

  /** close a source panel: back to the collection under it */
  const closePanel = useCallback(() => {
    onTabChangeRef.current?.();
    setTab(base);
  }, [base]);

  const panelOpen = !isPrimarySyncTab(tab);
  useEffect(() => {
    if (!panelOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') closePanel();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [panelOpen, closePanel]);

  const shownCollection = panelOpen ? base : tab;
  // window globals, not the router: this shell also renders without one
  const explorerOn =
    useFeatureOn('explorer') &&
    (window.SoulSyncWebShellBridge?.isPageAllowed('playlist-explorer') ?? true);
  const sub = summary ? summaryParts(summary) : null;

  return (
    // `page-shell` plus the page id, matching the convention every flipped
    // route follows; the legacy chrome resolves a page by `${pageId}-page`.
    <div className="page-shell pgf-shell" id="sync-page">
      <div className="pgf-page pgf-page--plain">
        <div className={`pl-header ${styles.top}`}>
          <div className={styles.topText}>
            <h1 className="pl-title pgf-title">
              <span>Playlists</span>
            </h1>
            <p className={`pl-subtitle pgf-status ${styles.sub}`}>
              {sub ? (
                <>
                  <span>
                    <b>{sub.playlists}</b> mirrored
                  </span>
                  {sub.attention ? (
                    <>
                      <span className={styles.subDot} aria-hidden="true">
                        ·
                      </span>
                      <span className={styles.subWarn}>{sub.attention}</span>
                    </>
                  ) : null}
                  {sub.nextRun ? (
                    <>
                      <span className={styles.subDot} aria-hidden="true">
                        ·
                      </span>
                      <span>{sub.nextRun}</span>
                    </>
                  ) : null}
                  {sub.running ? (
                    <>
                      <span className={styles.subDot} aria-hidden="true">
                        ·
                      </span>
                      <span className={styles.subLive}>
                        <span className={styles.spin} aria-hidden="true" /> {sub.running}
                      </span>
                    </>
                  ) : null}
                </>
              ) : (
                'Manage, mirror, and synchronize your playlists with your media server'
              )}
            </p>
          </div>
          <div className={`pl-header-actions ${styles.topActions}`}>
            {/* the explorer is a view of these playlists, so it opens from here
              and has no sidebar entry of its own. hidden with its feature
              switch, and for a profile that was not given the page */}
            {explorerOn ? (
              <a
                href="/playlist-explorer"
                onClick={(event) => {
                  // a plain click stays in the app; a modified one opens a tab
                  if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0)
                    return;
                  if (!window.navigateToPage) return;
                  event.preventDefault();
                  void window.navigateToPage('playlist-explorer');
                }}
                className={`pl-explorer-btn ${styles.iconBtn}`}
                aria-label="Playlist Explorer"
                title="Playlist Explorer: see the albums and discographies behind a playlist"
              >
                <svg
                  viewBox="0 0 24 24"
                  width="20"
                  height="20"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.9"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  aria-hidden="true"
                >
                  <circle cx="12" cy="5" r="2.5" />
                  <path d="M12 7.5V12M12 12l-6.5 5M12 12l6.5 5M12 12v5" />
                  <circle cx="5" cy="19" r="2" />
                  <circle cx="12" cy="19" r="2" />
                  <circle cx="19" cy="19" r="2" />
                </svg>
              </a>
            ) : null}
            {/* everything else this page can do, behind one button (#1909).
              same names as the windows they open, with a line each. */}
            <ActionMenu
              align="right"
              className="sync-more-menu"
              trigger={(props) => (
                <button
                  type="button"
                  className={`pl-tools-btn ${styles.iconBtn}`}
                  aria-label="Playlist tools"
                  title="Scheduling, match review, activity and more"
                  {...props}
                >
                  <svg
                    viewBox="0 0 24 24"
                    width="20"
                    height="20"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                    aria-hidden="true"
                  >
                    <path d="M4 7h10" />
                    <path d="M18 7h2" />
                    <circle cx="16" cy="7" r="2" />
                    <path d="M4 17h4" />
                    <path d="M12 17h8" />
                    <circle cx="10" cy="17" r="2" />
                  </svg>
                </button>
              )}
              items={[
                ...SYNC_HEADER_ACTIONS.map((action) => ({
                  key: action.key,
                  // the vanilla hook classes ride on the items, so nothing that
                  // selects them loses them
                  className: `sync-history-btn${action.key === 'auto-sync' ? ' auto-sync-manager-btn' : ''}`,
                  title: action.title,
                  label: (
                    <span className={`sync-more-item ${styles.toolItem}`}>
                      <span className={`sync-more-name ${styles.toolName}`}>{action.label}</span>
                      <span className={`sync-more-desc ${styles.toolDesc}`}>{action.desc}</span>
                    </span>
                  ),
                  onSelect: () => runHeaderAction(action.key, onAutoSync, onActivity),
                })),
                ...(onUpdateList
                  ? [
                      {
                        key: 'update-list',
                        className: 'sync-history-btn',
                        title: 'Reload the playlist list',
                        label: (
                          <span className={`sync-more-item ${styles.toolItem}`}>
                            <span className={`sync-more-name ${styles.toolName}`}>Update list</span>
                            <span className={`sync-more-desc ${styles.toolDesc}`}>
                              Reload the playlist list.
                            </span>
                          </span>
                        ),
                        onSelect: onUpdateList,
                      },
                    ]
                  : []),
              ]}
            />
            {onAddPlaylist && (
              <button
                type="button"
                className={`pl-add-btn ${styles.cta}`}
                title="Paste a link, pick a source, import a file, or start a new playlist"
                onClick={() => onAddPlaylist()}
              >
                <svg
                  viewBox="0 0 24 24"
                  width="18"
                  height="18"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.6"
                  strokeLinecap="round"
                  aria-hidden="true"
                >
                  <path d="M12 5v14" />
                  <path d="M5 12h14" />
                </svg>
                Add playlist
              </button>
            )}
          </div>
        </div>

        {/* No framed panel inside the page: the artwork is the page. */}
        <div>
          <div>
            <div className={`pl-collections ${styles.pills}`} role="tablist">
              {syncStripTabs().map((t) => (
                <button
                  key={t.id}
                  type="button"
                  role="tab"
                  aria-selected={shownCollection === t.id}
                  className={`pl-collection ${styles.pill}${
                    shownCollection === t.id ? ` active ${styles.pillOn}` : ''
                  }`}
                  data-tab={t.id}
                  title={t.label}
                  onClick={() => {
                    open(t.id);
                  }}
                >
                  <span className="pl-collection-label">{t.label}</span>
                </button>
              ))}
            </div>

            {SYNC_TABS.filter((t) => isPrimarySyncTab(t.id)).map((t) => (
              <div
                key={t.id}
                className={`sync-tab-content${shownCollection === t.id ? ' active' : ''}`}
                id={`${t.id}-tab-content`}
                role="tabpanel"
              >
                {opened.has(t.id) ? panels[t.id] : null}
              </div>
            ))}
          </div>
        </div>

        {/* The source tabs, as panels over the page. Each stays mounted once
          opened, so closing and reopening one finds what it had loaded. */}
      </div>
      {panelOpen ? (
        <button
          type="button"
          className={styles.panelScrim}
          aria-label="Close"
          tabIndex={-1}
          onClick={closePanel}
        />
      ) : null}
      {SYNC_TABS.filter((t) => !isPrimarySyncTab(t.id) && opened.has(t.id)).map((t) => (
        <div
          key={t.id}
          className={`sync-source-panel ${styles.panel}${tab === t.id ? '' : ` ${styles.panelHidden}`}`}
          role="dialog"
          aria-modal={tab === t.id ? true : undefined}
          aria-label={t.label}
          aria-hidden={tab === t.id ? undefined : true}
        >
          <div className={styles.panelHead}>
            <span className={`tab-icon ${t.icon}`} aria-hidden="true" />
            <h3 className={styles.panelTitle}>{t.label}</h3>
            <button type="button" className={styles.done} onClick={closePanel}>
              Done
            </button>
          </div>
          <div
            className={`sync-tab-content active ${styles.panelBody}`}
            id={`${t.id}-tab-content`}
            role="tabpanel"
          >
            {panels[t.id]}
          </div>
        </div>
      ))}
    </div>
  );
}

/** The header status line, from the library summary. */
function summaryParts(summary: SyncLibrarySummary): {
  playlists: number;
  attention: string;
  running: string;
  nextRun: string;
} {
  return {
    playlists: summary.playlists,
    attention: summary.attention
      ? `${summary.attention} need${summary.attention === 1 ? 's' : ''} attention`
      : '',
    running: summary.running ? `${summary.running} working` : '',
    nextRun: summary.nextRun ? `next sync ${summary.nextRun.replace(/^next /, '')}` : '',
  };
}
