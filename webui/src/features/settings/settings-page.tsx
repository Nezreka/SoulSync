/**
 * The Settings page (SETTINGS_PORT.md step V4c): index.html's #settings-page,
 * as React. The same markup, ids and classes; each tab renders its parts into
 * the same `data-stg-react` slot wrappers they had in the vanilla page, so the
 * shell's tab switch, search and keyboard (shell.ts) and the stylesheets work
 * on it as before. Its inline handlers are React handlers doing the same.
 *
 * `#webui-react-root` is the `.page` the vanilla page was, so this is not one
 * (two would pad twice); `active` is kept for the stylesheet's `:has()`.
 */

import { type ComponentType, type ReactNode, useEffect } from 'react';

import type { SettingsTabProps } from './host';

import { SETTINGS_TABS } from './host';
import { pageTabs, usePageTabs } from './page-tabs';
import { type SettingsStore, settingsStore } from './settings-store';
import { useViewer } from './tabs/people/data';

/** the shell's functions, as the shell bundle put them on window (shell.ts
 *  in src/shell/index.ts): one copy of its state (the pending tab, the save
 *  button's timer), not a second in this bundle */
type ShellWindow = Window & {
  switchSettingsTab?: (tab: string) => void;
  filterSettings?: (query: string) => void;
  saveSettings?: (quiet?: boolean) => Promise<void>;
  initializeSettings?: () => void;
};
const shell = () => window as ShellWindow;
const switchSettingsTab = (tab: string) => shell().switchSettingsTab?.(tab);
const filterSettings = (query: string) => shell().filterSettings?.(query);
const saveSettings = (quiet: boolean) => shell().saveSettings?.(quiet);

const TABS: Array<[key: string, label: string, path: string]> = [
  [
    'connections',
    'Connections',
    'M18 10h-2V7a4 4 0 0 0-8 0v3H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-8a2 2 0 0 0-2-2zm-8-3a2 2 0 0 1 4 0v3h-4V7zm8 13H6v-8h12v8z',
  ],
  [
    'sources',
    'Sources',
    'M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2zm1 14.93V18a1 1 0 0 1-2 0v-1.07A7 7 0 0 1 5.07 11H4a1 1 0 0 1 0-2h1.07A7 7 0 0 1 11 5.07V4a1 1 0 0 1 2 0v1.07A7 7 0 0 1 18.93 11H20a1 1 0 0 1 0 2h-1.07A7 7 0 0 1 13 16.93zM12 7a5 5 0 1 0 5 5 5 5 0 0 0-5-5z',
  ],
  ['downloads', 'Downloads', 'M19 9h-4V3H9v6H5l7 7 7-7zM5 18v2h14v-2H5z'],
  [
    'quality',
    'Quality',
    'M3 17v2h6v-2H3zM3 5v2h10V5H3zm10 16v-2h8v-2h-8v-2h-2v6h2zM7 9v2H3v2h4v2h2V9H7zm14 4v-2H11v2h10zm-6-4h2V7h4V5h-4V3h-2v6z',
  ],
  [
    'library',
    'Library',
    'M4 6H2v14c0 1.1.9 2 2 2h14v-2H4V6zm16-4H8c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm0 14H8V4h12v12z',
  ],
  [
    'discovery',
    'Discovery',
    'M12 2a10 10 0 1 0 10 10A10 10 0 0 0 12 2zm4.24 5.76-2.12 6.36-6.36 2.12 2.12-6.36zM12 13a1 1 0 1 0-1-1 1 1 0 0 0 1 1z',
  ],
  [
    'people',
    'People',
    'M16 11c1.66 0 2.99-1.34 2.99-3S17.66 5 16 5c-1.66 0-3 1.34-3 3s1.34 3 3 3zm-8 0c1.66 0 2.99-1.34 2.99-3S9.66 5 8 5C6.34 5 5 6.34 5 8s1.34 3 3 3zm0 2c-2.33 0-7 1.17-7 3.5V19h14v-2.5c0-2.33-4.67-3.5-7-3.5zm8 0c-.29 0-.62.02-.97.05 1.16.84 1.97 1.97 1.97 3.45V19h6v-2.5c0-2.33-4.67-3.5-7-3.5z',
  ],
  [
    'appearance',
    'Appearance',
    'M12 3c-4.97 0-9 4.03-9 9 0 2.12.74 4.07 1.97 5.61L4.35 19.4a1 1 0 0 0 .71 1.6h4.52A8.96 8.96 0 0 0 12 21c4.97 0 9-4.03 9-9s-4.03-9-9-9zm-5 9c-.55 0-1-.45-1-1s.45-1 1-1 1 .45 1 1-.45 1-1 1zm3-4c-.55 0-1-.45-1-1s.45-1 1-1 1 .45 1 1-.45 1-1 1zm4 0c-.55 0-1-.45-1-1s.45-1 1-1 1 .45 1 1-.45 1-1 1zm3 4c-.55 0-1-.45-1-1s.45-1 1-1 1 .45 1 1-.45 1-1 1z',
  ],
  [
    'advanced',
    'System',
    'M19.14 12.94c.04-.3.06-.61.06-.94 0-.32-.02-.64-.07-.94l2.03-1.58a.49.49 0 0 0 .12-.61l-1.92-3.32a.49.49 0 0 0-.59-.22l-2.39.96c-.5-.38-1.03-.7-1.62-.94l-.36-2.54A.48.48 0 0 0 13.9 2h-3.8a.49.49 0 0 0-.49.41l-.36 2.54c-.59.24-1.13.57-1.62.94l-2.39-.96c-.22-.08-.47 0-.59.22L2.73 8.47c-.12.21-.08.47.12.61l2.03 1.58c-.05.3-.09.63-.09.94s.02.64.07.94l-2.03 1.58a.48.48 0 0 0-.12.61l1.92 3.32c.12.22.37.29.59.22l2.39-.96c.5.38 1.03.7 1.62.94l.36 2.54c.05.24.24.41.48.41h3.8c.24 0 .44-.17.49-.41l.36-2.54c.59-.24 1.13-.56 1.62-.94l2.39.96c.22.08.47 0 .59-.22l1.92-3.32c.12-.22.07-.47-.12-.61l-2.01-1.58zM12 15.6c-1.98 0-3.6-1.62-3.6-3.6s1.62-3.6 3.6-3.6 3.6 1.62 3.6 3.6-1.62 3.6-3.6 3.6z',
  ],
  [
    'logs',
    'Logs',
    'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6zm2 16H8v-2h8v2zm0-4H8v-2h8v2zm-3-5V3.5L18.5 9H13z',
  ],
];

/** a tab's part in the wrapper it had in the vanilla page */
function Slot({
  tab,
  part,
  store,
  tabs,
}: {
  tab: string;
  part?: string;
  store: SettingsStore;
  tabs: Partial<Record<string, ComponentType<SettingsTabProps>>>;
}) {
  const { tab: open, opened } = usePageTabs();
  const Tab = tabs[tab];
  return (
    <div data-stg-react={tab} data-stg-part={part} style={{ display: 'contents' }}>
      {Tab ? (
        <Tab store={store} part={part} opened={opened[tab] ?? 0} active={open === tab} />
      ) : null}
    </div>
  );
}

/** the Sources tab's note on filesystem access, folded until clicked */
function HeroToggle({ children }: { children: ReactNode }) {
  return (
    <div
      className="ind-hero-warning-title"
      style={{ cursor: 'pointer', userSelect: 'none' }}
      onClick={(event) => {
        // as its inline handler did: the body below, and the caret
        const b = event.currentTarget.nextElementSibling as HTMLElement | null;
        if (!b) return;
        b.style.display = b.style.display === 'none' ? '' : 'none';
        const c = event.currentTarget.querySelector('.ind-hero-warning-caret');
        if (c) c.textContent = b.style.display === 'none' ? '▾' : '▴';
      }}
    >
      {children}
    </div>
  );
}

export function SettingsPage({
  store = settingsStore,
  tabs = SETTINGS_TABS,
}: {
  store?: SettingsStore;
  tabs?: Partial<Record<string, ComponentType<SettingsTabProps>>>;
}) {
  // a visit starts with no tab open and none opened yet, as the first did:
  // the next visit's tabs mount as on the first
  useEffect(() => () => pageTabs.reset(), []);
  // the password managers kept off fields React adds after the page opened
  // (rows added, fields shown later), as off every field the vanilla page had
  // at load. init.js's own sweep leaves password fields alone
  useEffect(() => {
    const page = document.getElementById('settings-page');
    if (!page) return;
    let frame = 0;
    const observer = new MutationObserver(() => {
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        shell().initializeSettings?.();
      });
    });
    observer.observe(page, { childList: true, subtree: true });
    return () => {
      observer.disconnect();
      if (frame) cancelAnimationFrame(frame);
    };
  }, []);
  // People is for admins only (the page is too, but a member never sees it)
  const viewer = useViewer();
  const slot = (tab: string, part?: string) => (
    <Slot key={`${tab}/${part ?? ''}`} tab={tab} part={part} store={store} tabs={tabs} />
  );
  return (
    <div id="settings-page" className="active">
      <div className="page-shell">
        <div className="dashboard-header settings-header-compact">
          <div className="dashboard-header-sweep" aria-hidden="true">
            <span></span>
          </div>
          <div className="header-text">
            <h2 className="header-title">
              <img src="/static/settings.png" className="page-header-icon" alt="" />
              <span>Settings</span>
            </h2>
          </div>
        </div>
        <div className="settings-content">
          <nav className="stg-category-nav" aria-label="Settings categories">
            <div className="stg-navigation-heading">
              <div>
                <h2>Settings</h2>
              </div>
              <div className="stg-nav-search-wrap">
                <svg className="stg-search-icon" viewBox="0 0 24 24">
                  <path d="M21.71 20.29l-5.4-5.4a8 8 0 1 0-1.42 1.42l5.4 5.4a1 1 0 0 0 1.42-1.42zM4 10a6 6 0 1 1 6 6 6 6 0 0 1-6-6z" />
                </svg>
                <input
                  type="text"
                  id="stg-search-input"
                  placeholder="Search settings  (/)"
                  aria-label="Search settings"
                  autoComplete="off"
                  aria-controls="stg-search-results"
                  onKeyDown={(event) => {
                    if (event.key === 'Escape') {
                      event.currentTarget.value = '';
                      filterSettings('');
                    }
                  }}
                  onInput={(event) => filterSettings(event.currentTarget.value)}
                />
              </div>
            </div>
            <div id="stg-search-results" className="stg-search-results" hidden></div>
            <div className="stg-tabbar">
              {TABS.map(([key, label, path]) => (
                <button
                  key={key}
                  type="button"
                  className={key === 'connections' ? 'stg-tab active' : 'stg-tab'}
                  data-tab={key}
                  hidden={key === 'people' && !viewer.admin}
                  onClick={() => switchSettingsTab(key)}
                >
                  <span className={`stg-tab-badge badge-${key}`}>
                    <svg viewBox="0 0 24 24">
                      <path d={path} />
                    </svg>
                  </span>
                  <span className="stg-tab-label">{label}</span>
                </button>
              ))}
            </div>
          </nav>

          <div className="stg-detail-pane">
            <div className="settings-nav-row">
              <div className="stg-active-section-title">
                <span className="stg-active-title-text" id="stg-active-title">
                  Connections
                </span>
                <span className="stg-active-sub-text" id="stg-active-sub">
                  Accounts, media servers, and API keys SoulSync connects to
                </span>
              </div>
              <div className="header-actions">
                {/* settings save as you go; this is their status and a
                    save-now (shell.ts setSettingsSaveState, #1909) */}
                <button
                  className="save-button"
                  id="save-settings"
                  data-state="idle"
                  aria-live="polite"
                  title="Settings save as you go. Click to save again now."
                  onClick={() => void saveSettings(false)}
                >
                  <svg
                    className="save-state-icon"
                    viewBox="0 0 24 24"
                    width="16"
                    height="16"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2.4"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    aria-hidden="true"
                  >
                    <path d="M5 12.5l4.5 4.5L19 7.5" />
                  </svg>
                  <span>All changes saved</span>
                </button>
              </div>
            </div>
            <div className="settings-columns">
              <div className="settings-left-column">
                {slot('connections', 'metadata')}
                <div className="stg-cardgroup" data-stg="connections">
                  <span className="stg-cardgroup-label">Services</span>
                  <span className="stg-cardgroup-sub">
                    Accounts and API keys SoulSync connects to
                  </span>
                </div>
                {slot('connections', 'services')}
                {slot('connections', 'api-music')}
                {slot('connections', 'api-video')}
                {slot('connections', 'video-server')}
                {slot('connections', 'servers')}
              </div>
              <div className="settings-right-column">
                {slot('quality', 'video')}
                {slot('downloads', 'chain')}
                <div className="settings-group" data-stg="sources">
                  <h3>Download Sources</h3>
                  <small className="settings-hint">
                    Set up any source here, then put it in the chain on the Downloads tab. A source
                    does not have to be enabled to be configured.
                  </small>
                  {slot('sources', 'tiles')}
                  <div className="ind-hero-warning" style={{ marginBottom: '12px' }}>
                    <HeroToggle>
                      💡 Filesystem access — easiest setup{' '}
                      <span className="ind-hero-warning-caret" style={{ opacity: 0.6 }}>
                        ▾
                      </span>
                    </HeroToggle>
                    <div className="ind-hero-warning-body" style={{ display: 'none' }}>
                      Torrent and usenet clients write files to{' '}
                      <strong>their own download folders</strong>, not Soulseek&apos;s. SoulSync
                      needs read access to those folders to import the files.{' '}
                      <strong>Simplest fix:</strong> point all your downloaders (Soulseek,
                      qBittorrent, SABnzbd / NZBGet) at the <em>same</em> download folder. One
                      folder, one path to worry about, everything just works.
                      <ul>
                        <li>
                          <strong>Bare-metal:</strong> just set every client&apos;s download path to
                          the same folder (e.g. your existing slskd folder). Done.
                        </li>
                        <li>
                          <strong>Docker:</strong> easiest is to reuse the <code>./downloads</code>{' '}
                          mount you already have for slskd — point qBit and SAB at{' '}
                          <code>/downloads</code> inside their containers too. Or use the commented
                          placeholders in <code>docker-compose.yml</code> if you want separate
                          folders.
                        </li>
                        <li>
                          <strong>Remote client (NAS / different host):</strong> mount the remote
                          folder locally (SMB / NFS / rclone) before SoulSync can see it.
                        </li>
                      </ul>
                    </div>
                  </div>
                  {/* the config panels park here; opening a source moves its
                      panel into the modal and closing moves it back */}
                  <div id="source-config-home" hidden>
                    {slot('sources', 'soulseek')}
                    {slot('sources', 'tidal-qobuz')}
                    {slot('sources', 'hifi')}
                    {slot('sources', 'deezer-soundcloud-lidarr')}
                    {slot('sources', 'youtube')}
                    {slot('sources', 'prowlarr-torrent-usenet')}
                  </div>
                </div>
                {slot('sources', 'modal')}
                {slot('quality', 'profiles')}
                {slot('downloads', 'behaviour')}
                {slot('downloads', 'retry')}
                {slot('library', 'folders')}
                {slot('library', 'organization')}
                {slot('library', 'processing')}
                {slot('downloads', 'filtering')}
                {slot('discovery', 'main')}
                {slot('library', 'video-prefs')}
                {slot('advanced', 'saving')}
                {slot('advanced', 'security')}
                {slot('discovery', 'pool')}
              </div>
              <div className="settings-third-column">
                {slot('appearance', 'ui')}
                {slot('advanced', 'database')}
                {slot('advanced', 'tools')}
                {slot('appearance', 'weather')}
                {slot('connections', 'metasync')}
                {slot('advanced', 'developer')}
                {slot('connections', 'notifications')}
                {slot('discovery', 'import-lists')}
                {slot('advanced', 'experimental')}
              </div>
            </div>
            {slot('people')}
            {slot('logs')}
          </div>
        </div>
      </div>
    </div>
  );
}
