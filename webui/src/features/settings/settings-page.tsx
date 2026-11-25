/**
 * The Settings page (settings-bic DESIGN.md section 3): a title and the
 * search, eight tabs, the open tab's title with the save status, its pills,
 * and its content. Where the page is (tab, pill, kind, open sheet) is the
 * address (nav.ts), so a reload stays put and Back goes back.
 *
 * Every tab is mounted all the time and shown or hidden, never unmounted: an
 * edit that has not saved yet survives any tab or pill switch, and the search,
 * the help palette and the key guard find every field. Each tab renders its
 * own page and hides itself while another tab is open; People shows while
 * it is the open tab.
 *
 * `#webui-react-root` is the `.page` the vanilla page was, so this is not one
 * (two would pad twice); `active` is kept for the stylesheet's `:has()`.
 */

import clsx from 'clsx';
import {
  type ComponentType,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react';

import type { SettingsTabProps } from './host';

import { SETTINGS_TABS } from './host';
import { type NavState, TABS, type TabDef, legacyKey, nav, tabDef, useNav, viewsFor } from './nav';
import { pageTabs, usePageTabs } from './page-tabs';
import { type SettingsStore, settingsStore } from './settings-store';
import { SettingsSearch } from './shell/search';
import { useSourcesAttention } from './tabs/downloads/sources-view';
import { useViewer } from './tabs/people/data';
import { Btn, Icon, Note, Pills } from './ui';
import s from './ui/settings.module.css';

/** the shell's functions, as the shell bundle put them on window (shell.ts
 *  in src/shell/index.ts): one copy of its state (the pending tab, the save
 *  button's timer), not a second in this bundle */
type ShellWindow = Window & {
  switchSettingsTab?: (tab: string) => void;
  saveSettings?: (quiet?: boolean) => Promise<void>;
  initializeSettings?: () => void;
  loadSettingsData?: () => Promise<void>;
};
const shell = () => window as ShellWindow;
const saveSettings = (quiet: boolean) => shell().saveSettings?.(quiet);

type Tabs = Partial<Record<string, ComponentType<SettingsTabProps>>>;

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
  tabs: Tabs;
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

function useSide(): 'music' | 'video' {
  return useSyncExternalStore(
    (listener) => {
      const observer = new MutationObserver(listener);
      observer.observe(document.body, { attributes: true, attributeFilter: ['data-side'] });
      return () => observer.disconnect();
    },
    () => (document.body.getAttribute('data-side') === 'video' ? 'video' : 'music'),
    () => 'music',
  );
}

// ── the tab strip ──

function TabStrip({
  state,
  admin,
  counts,
}: {
  state: NavState;
  admin: boolean;
  counts: Partial<Record<string, number>>;
}) {
  const strip = useRef<HTMLDivElement>(null);
  const [atEnd, setAtEnd] = useState(false);
  // the fade on the right says there are more tabs that way (phones)
  useEffect(() => {
    const el = strip.current;
    if (!el) return;
    const check = () => setAtEnd(el.scrollLeft + el.clientWidth >= el.scrollWidth - 4);
    check();
    el.addEventListener('scroll', check, { passive: true });
    window.addEventListener('resize', check);
    return () => {
      el.removeEventListener('scroll', check);
      window.removeEventListener('resize', check);
    };
  }, []);
  // the open tab is on screen in the strip
  useEffect(() => {
    strip.current
      ?.querySelector<HTMLElement>('[aria-current="page"]')
      ?.scrollIntoView?.({ block: 'nearest', inline: 'nearest' });
  }, [state.tab]);
  return (
    <nav
      className={s.tabsWrap}
      aria-label="Settings categories"
      data-ui=""
      data-end={atEnd ? '' : undefined}
    >
      <div className={clsx(s.tabs, 'stg-tabbar')} ref={strip}>
        {TABS.map((tab) => {
          const on = tab.key === state.tab;
          const count = counts[tab.key] ?? 0;
          return (
            <button
              key={tab.key}
              type="button"
              className={clsx(s.tab, on && s.tabOn, 'stg-tab', on && 'active')}
              data-tab={tab.key}
              aria-current={on ? 'page' : undefined}
              hidden={(tab.key === 'people' || tab.key === 'devices') && !admin}
              onClick={() => nav.go(tab.key)}
            >
              <span className={s.tabIcon}>
                <Icon name={tab.icon} />
              </span>
              <span className="stg-tab-label">{tab.label}</span>
              {count ? (
                <span className={s.tabCount} title={`${count} to look at`}>
                  {count}
                </span>
              ) : null}
            </button>
          );
        })}
      </div>
    </nav>
  );
}

// ── the save status ──

interface SaveNote {
  state: string;
  count: number;
  note: string;
}

/** what the shell last said about saving (shell.ts setSettingsSaveState) */
function useSaveNote(): SaveNote {
  const [note, setNote] = useState<SaveNote>({ state: 'rest', count: 0, note: '' });
  useEffect(() => {
    const onState = (event: Event) => setNote((event as CustomEvent<SaveNote>).detail);
    window.addEventListener('ss:settings-save-state', onState);
    return () => window.removeEventListener('ss:settings-save-state', onState);
  }, []);
  return note;
}

function SaveStatus({ hidden }: { hidden: boolean }) {
  const said = useSaveNote();
  const waiting = said.state === 'pending' || said.state === 'error' || said.state === 'blocked';
  return (
    <>
      <div className={s.saveWrap} hidden={hidden}>
        {/* settings save as you go; this is their status and a save-now
            (shell.ts setSettingsSaveState, #1909). it opens at rest, hidden
            on the music side until something changes (#2187) */}
        <button
          type="button"
          className={clsx(s.save, 'save-button')}
          id="save-settings"
          data-state="rest"
          aria-live="polite"
          title="Settings save as you go. Click to save again now."
          onClick={() => void saveSettings(false)}
        >
          <span className={s.saveIcon}>
            <Icon name="check" size={14} />
          </span>
          <span>All changes saved</span>
        </button>
        {/* why a save is held, or what the server said when one failed */}
        <div className={s.saveNote} id="stg-save-note" role="status" hidden={!said.note}>
          {said.note}
        </div>
      </div>
      {/* a phone has no room beside the title: a bar above the home bar,
          only while something waits */}
      {waiting && !hidden ? (
        <div className={s.saveBar} data-save-bar="" data-ui="">
          <span className={s.saveBarText}>
            {said.state === 'blocked'
              ? said.note || 'Something needs fixing before this can save'
              : said.state === 'error'
                ? said.note || 'Some settings did not save'
                : said.count > 1
                  ? `${said.count} changes not saved`
                  : '1 change not saved'}
          </span>
          <Btn kind="primary" small onClick={() => void saveSettings(false)}>
            {said.state === 'blocked' ? 'Show me' : said.state === 'error' ? 'Retry' : 'Save now'}
          </Btn>
        </div>
      ) : null}
    </>
  );
}

/** the settings did not load: said where they would be, with a way to try
 *  again. Until a good load the page shows defaults and saves nothing (#879) */
function LoadFailed() {
  const [failed, setFailed] = useState<string | null>(null);
  const [trying, setTrying] = useState(false);
  useEffect(() => {
    const onLoad = (event: Event) => {
      const detail = (event as CustomEvent<{ failed: boolean; message?: string }>).detail;
      setFailed(detail.failed ? detail.message || 'The server did not answer.' : null);
    };
    window.addEventListener('ss:settings-load', onLoad);
    return () => window.removeEventListener('ss:settings-load', onLoad);
  }, []);
  if (failed === null) return null;
  return (
    <Note
      action={
        <Btn
          small
          icon="refresh"
          busy={trying}
          onClick={() => {
            setTrying(true);
            void shell()
              .loadSettingsData?.()
              .finally(() => setTrying(false));
          }}
        >
          Try again
        </Btn>
      }
    >
      <strong>Your settings did not load.</strong> What you see here are defaults, and nothing will
      be saved until they load. Your saved settings are untouched. ({failed})
    </Note>
  );
}

// ── the page ──

/** the pills of the open tab that this side shows (the open one always) */
function pillsOf(def: TabDef, state: NavState, side: 'music' | 'video') {
  return viewsFor(def, side).filter(
    (view) => !view.side || view.side === side || view.key === state.view,
  );
}

export function SettingsPage({
  store = settingsStore,
  tabs = SETTINGS_TABS,
}: {
  store?: SettingsStore;
  tabs?: Tabs;
}) {
  const state = useNav();
  const side = useSide();
  // People and Devices are for admins only (the page is too, but a member
  // never sees them)
  const viewer = useViewer();
  const attention = useSourcesAttention();
  const key = legacyKey(state);
  const def = tabDef(state.tab);
  const pills = pillsOf(def, state, side);

  // a visit starts with no tab open and none opened yet, as the first did:
  // the next visit's tabs mount as on the first
  useEffect(
    () => () => {
      pageTabs.reset();
      nav.reset();
    },
    [],
  );

  // the open tab: its content is told (what reloads on arrival does)
  useLayoutEffect(() => {
    pageTabs.show(key);
    if (key === 'library')
      document.dispatchEvent(new CustomEvent('soulsync:library-settings-shown'));
  }, [key]);

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

  const slot = (tab: string, part?: string) => (
    <Slot key={`${tab}/${part ?? ''}`} tab={tab} part={part} store={store} tabs={tabs} />
  );

  return (
    <div id="settings-page" className="active" data-tab={state.tab}>
      <div className={s.panel}>
        <div className={s.top} data-ui="">
          <h1 className={s.title}>Settings</h1>
          <SettingsSearch side={side} />
        </div>

        <TabStrip state={state} admin={viewer.admin} counts={{ downloads: attention }} />
        <LoadFailed />

        <div className={s.head} data-ui="">
          <div>
            <h2 className={s.headTitle} id="stg-active-title">
              {def.title}
            </h2>
            <p className={s.headSub} id="stg-active-sub">
              {def.sub}
            </p>
          </div>
          {/* People and Devices save each thing with its own button */}
          <SaveStatus hidden={state.tab === 'people' || state.tab === 'devices'} />
        </div>

        {pills.length > 1 ? (
          <Pills
            label={`${def.label} sections`}
            idPrefix={`stg-pill-${def.key}`}
            items={pills.map((view) => ({
              key: view.key,
              label: view.label,
              dot: def.key === 'downloads' && view.key === 'sources' && attention > 0,
            }))}
            value={state.view}
            onChange={(view) => nav.view(view)}
          />
        ) : null}

        {/* rebuilt tabs: their own pages, one panel per pill */}
        {slot('connections', 'page')}
        {slot('downloads', 'page')}
        {slot('quality', 'page')}
        {slot('library', 'page')}
        {slot('discovery', 'page')}
        {slot('appearance', 'page')}
        {slot('advanced', 'page')}

        {/* the service cards' hidden homes: a tile opens one in its sheet */}
        <div className="settings-columns">
          <div className="settings-left-column">
            {slot('connections', 'api-music')}
            {slot('connections', 'api-video')}
          </div>
        </div>
        {slot('people')}
        {slot('devices', 'page')}
      </div>
    </div>
  );
}
