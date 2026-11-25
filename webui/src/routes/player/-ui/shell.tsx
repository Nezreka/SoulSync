/**
 * the player app's frame: sidebar (a tab bar on phones), header with search
 * and the profile chip, the page, and the transport bar along the bottom.
 * it covers the manager entirely; the manager underneath is untouched.
 */

import { useQuery } from '@tanstack/react-query';
import { Link, Outlet, useRouter, useRouterState } from '@tanstack/react-router';
import { useEffect, useRef, useState, type CSSProperties } from 'react';
import { createPortal } from 'react-dom';

import { useCardSize, type CardSizeRange } from '@/components/card-size';
import { appURL } from '@/platform/url-base';

import type { PlayerLibrary } from '../-player.types';

import { arrangeNav } from '../-lib/nav-order';
import {
  playerSettingsQuery,
  profilesQuery,
  playerLibrariesQuery,
  watchingNowQuery,
  type PlayerAccentColor,
} from '../-player.api';

const ACCENT_PALETTE: Record<PlayerAccentColor, { rgb: string; lightRgb: string }> = {
  emerald: { rgb: '29, 185, 84', lightRgb: '30, 215, 96' },
  violet: { rgb: '139, 92, 246', lightRgb: '167, 139, 250' },
  ocean: { rgb: '56, 189, 248', lightRgb: '125, 211, 252' },
  amber: { rgb: '245, 158, 11', lightRgb: '251, 191, 36' },
  rose: { rgb: '244, 63, 94', lightRgb: '251, 113, 133' },
  crimson: { rgb: '239, 68, 68', lightRgb: '248, 113, 113' },
};
import { PlayerBootOverlay } from './boot-loader';
import { ContextMenuHost } from './context-menu';
import { BackIcon, ChevronIcon, ExitIcon, GearIcon, MoreIcon } from './icons';
import { AddToPlaylistHost } from './music/add-to-playlist';
import { NoticeHost } from './notice';
import { useNowPlaying } from './now-playing';
import { MusicView } from './playback/music-view';
import { usePlayerSync } from './playback/presence';
import { ReminderBell } from './playback/reminders';
import { TransportBar } from './playback/transport-bar';
import { ChannelBanner } from './playback/tuner';
import { VideoPlayer } from './playback/video-player';
import s from './player.module.css';
import { gateNeeded, ProfileChip, WhoGate } from './profiles';
import { Search } from './search';
import { NAV, NAV_GROUPS, useSections, type NavEntry } from './sections';
import { Sheet } from './sheet';
import { focusFirstIn, useSpatialNav } from './use-spatial-nav';

const TOP_LEVEL_PATHS = new Set([
  '/player',
  '/player/',
  '/player/guide',
  '/player/calendar',
  '/player/movies',
  '/player/tv',
  '/player/music',
  '/player/youtube',
  '/player/podcasts',
  '/player/audiobooks',
  '/player/watchlist',
  '/player/watching',
  '/player/downloads',
  '/player/admin-users',
  '/player/collections',
  '/player/settings',
]);

function fallbackParentFor(pathname: string): string {
  if (pathname.startsWith('/player/movies/')) return '/player/movies';
  if (pathname.startsWith('/player/tv/')) return '/player/tv';
  if (pathname.startsWith('/player/music/')) return '/player/music';
  if (pathname.startsWith('/player/youtube/')) return '/player/youtube';
  if (pathname.startsWith('/player/podcasts/')) return '/player/podcasts';
  if (pathname.startsWith('/player/audiobooks/')) return '/player/audiobooks';
  if (pathname.startsWith('/player/collections/')) return '/player/collections';
  return '/player';
}

const CARD_SCALE_RANGE: CardSizeRange = { min: 64, max: 160, base: 100 };

const COLLAPSE_KEY = 'ss-player-side-collapsed';

function SmartBackButton({ pathname }: { pathname: string }) {
  const router = useRouter();
  const handleBack = () => {
    if (window.history.length > 1) {
      router.history.back();
    } else {
      void router.navigate({ to: fallbackParentFor(pathname) });
    }
  };
  return (
    <button
      type="button"
      className={s.smartBackBtn}
      onClick={handleBack}
      aria-label="Go back"
      data-nav
    >
      <BackIcon />
      <span>Back</span>
    </button>
  );
}

function readCollapsed(): boolean {
  try {
    return localStorage.getItem(COLLAPSE_KEY) === '1';
  } catch {
    return false;
  }
}

/**
 * the nav entry a page belongs to: the longest one its path starts with,
 * so /player/music/playlists is Playlists, not Music too
 */
function currentEntry(entries: NavEntry[], path: string): NavEntry | null {
  let best: NavEntry | null = null;
  let bestLength = -1;
  for (const e of entries) {
    for (const to of [e.to, ...(e.also ?? [])]) {
      const hit = e.exact
        ? path === to || path === `${to}/`
        : path === to || path.startsWith(`${to}/`);
      if (hit && to.length > bestLength) {
        best = e;
        bestLength = to.length;
      }
    }
  }
  return best;
}

/** a phone's More: every place that isn't one of its tabs, and settings */
function MoreSheet({
  entries,
  watching,
  onClose,
}: {
  entries: NavEntry[];
  watching: number;
  onClose: () => void;
}) {
  const pathname = useRouterState({ select: (r) => r.location.pathname });
  const first = useRef(pathname);
  // going somewhere closes it
  useEffect(() => {
    if (pathname !== first.current) onClose();
  }, [pathname, onClose]);
  return (
    <Sheet title="More" onClose={onClose}>
      <nav className={s.moreList} aria-label="More">
        {entries.map((e) => (
          <Link key={e.to} to={e.to as '/player'} className={s.moreItem} data-nav>
            <span className={s.navIcon}>{e.icon}</span>
            <span>{e.label}</span>
            {e.to === '/player/watching' && watching ? (
              <span className={s.navBadge}>{watching}</span>
            ) : null}
          </Link>
        ))}
        <Link to="/player/settings" className={s.moreItem} data-nav>
          <span className={s.navIcon}>
            <GearIcon />
          </span>
          <span>Settings</span>
        </Link>
        <a className={s.moreItem} href={appURL('/')} data-nav>
          <span className={s.navIcon}>
            <ExitIcon />
          </span>
          <span>Back to SoulSync</span>
        </a>
      </nav>
    </Sheet>
  );
}

/** a place in the sidebar: a page, or a library with a page of its own */
export interface NavPlace {
  id: string;
  label: string;
  entry: NavEntry;
  library?: PlayerLibrary;
}

/** the sidebar's places in their usual order */
export function navPlaces(entries: NavEntry[], ownPages: PlayerLibrary[]): NavPlace[] {
  const out: NavPlace[] = [];
  for (const entry of entries) {
    out.push({ id: entry.to, label: entry.label, entry });
    for (const lib of ownPages)
      if (entry.to === (lib.kind === 'movie' ? '/player/movies' : '/player/tv'))
        out.push({ id: `lib:${lib.id}`, label: lib.name, entry, library: lib });
  }
  return out;
}

function Sidebar({ collapsed, onToggle }: { collapsed: boolean; onToggle: () => void }) {
  const sections = useSections();
  const entries = NAV.filter((entry) => entry.visible(sections));
  const pathname = useRouterState({ select: (r) => r.location.pathname });
  const current = currentEntry(entries, pathname);
  const onSettings = pathname === '/player/settings' || pathname.startsWith('/player/settings/');
  // how many others are playing something right now
  const watching = useQuery(watchingNowQuery()).data?.sessions.filter((x) => !x.you).length ?? 0;
  const [more, setMore] = useState(false);
  // libraries with a page of their own (none until an admin sets one up)
  const ownPages = (
    useQuery({ ...playerLibrariesQuery(), enabled: sections.video }).data ?? []
  ).filter((lib) => lib.own_page);
  const libraryParam = useRouterState({
    select: (r) => Number((r.location.search as { library?: unknown }).library) || 0,
  });
  const onLibrary =
    (pathname === '/player/movies' || pathname === '/player/tv') &&
    ownPages.some((lib) => lib.id === libraryParam)
      ? libraryParam
      : 0;
  // every place, in this profile's order: the pages, and each library with a
  // page of its own after the page it is a kind of
  const mine = useQuery(playerSettingsQuery()).data?.settings;
  const places = arrangeNav(
    navPlaces(entries, ownPages),
    mine?.sidebar_order,
    mine?.sidebar_hidden,
  );
  const currentId = onLibrary ? `lib:${onLibrary}` : (current?.to ?? '');
  return (
    <nav className={s.side} aria-label="Player" data-collapsed={collapsed ? 'true' : undefined}>
      <div className={s.brand}>
        <img className={s.brandLogo} src={appURL('/static/trans2.png')} alt="" />
        <span className={s.brandWord}>SoulSync</span>
      </div>
      {NAV_GROUPS.map((group) => {
        const items = places.filter((p) => p.entry.group === group.id);
        if (!items.length) return null;
        return (
          <div
            key={group.id}
            className={s.navGroup}
            role="group"
            aria-label={group.label ?? 'Main'}
          >
            {group.label ? <div className={s.navGroupLabel}>{group.label}</div> : null}
            {items.map((item) => (
              <Link
                key={item.id}
                to={item.entry.to as '/player/movies'}
                search={item.library ? { library: item.library.id } : undefined}
                className={s.navLink}
                data-tab={item.entry.tab && !item.library ? 'true' : undefined}
                activeOptions={{ exact: true }}
                data-current={item.id === currentId ? 'true' : undefined}
                aria-current={item.id === currentId ? 'page' : undefined}
                title={collapsed ? item.label : undefined}
                data-nav
              >
                <span className={s.navIcon}>{item.entry.icon}</span>
                <span className={s.navLabel}>{item.label}</span>
                {item.id === '/player/watching' && watching ? (
                  <span className={s.navBadge} aria-label={`${watching} playing`}>
                    {watching}
                  </span>
                ) : null}
                {item.entry.short && !item.library ? (
                  <span className={s.navShort}>{item.entry.short}</span>
                ) : null}
              </Link>
            ))}
          </div>
        );
      })}
      <button
        type="button"
        className={s.navMore}
        aria-haspopup="dialog"
        aria-expanded={more}
        data-current={current && !current.tab ? 'true' : undefined}
        onClick={() => setMore(true)}
        data-nav
      >
        <span className={s.navIcon}>
          <MoreIcon />
        </span>
        <span className={s.navLabel}>More</span>
        {watching ? <span className={s.navBadge}>{watching}</span> : null}
      </button>
      {more ? (
        <MoreSheet
          entries={entries.filter((e) => !e.tab)}
          watching={watching}
          onClose={() => setMore(false)}
        />
      ) : null}
      <div className={s.sideFoot}>
        {sections.isAdmin ? (
          <Link
            to="/player/admin-users"
            className={s.managerLink}
            data-current={
              pathname === '/player/admin-users' || pathname === '/player/downloads'
                ? 'true'
                : undefined
            }
            aria-current={pathname === '/player/admin-users' ? 'page' : undefined}
            title={collapsed ? 'Users & Downloads' : undefined}
            data-nav
          >
            <span className={s.navIcon}>
              <svg
                width="18"
                height="18"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
              </svg>
            </span>
            <span className={s.navLabel}>Users &amp; Downloads</span>
          </Link>
        ) : (
          <Link
            to="/player/downloads"
            className={s.managerLink}
            data-current={pathname === '/player/downloads' ? 'true' : undefined}
            aria-current={pathname === '/player/downloads' ? 'page' : undefined}
            title={collapsed ? 'Downloads' : undefined}
            data-nav
          >
            <span className={s.navIcon}>
              <svg
                width="18"
                height="18"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                <polyline points="7 10 12 15 17 10" />
                <line x1="12" y1="15" x2="12" y2="3" />
              </svg>
            </span>
            <span className={s.navLabel}>Downloads</span>
          </Link>
        )}
        <Link
          to="/player/settings"
          className={s.managerLink}
          data-current={onSettings ? 'true' : undefined}
          aria-current={onSettings ? 'page' : undefined}
          title={collapsed ? 'Player settings' : undefined}
          data-nav
        >
          <span className={s.navIcon}>
            <GearIcon />
          </span>
          <span className={s.navLabel}>Settings</span>
        </Link>
        <a
          className={s.managerLink}
          href={appURL('/')}
          title={collapsed ? 'Back to SoulSync' : undefined}
          data-nav
        >
          <span className={s.navIcon}>
            <ExitIcon />
          </span>
          <span className={s.navLabel}>Back to SoulSync</span>
        </a>
        <button
          type="button"
          className={s.collapseBtn}
          onClick={onToggle}
          aria-label={collapsed ? 'Expand the sidebar' : 'Collapse the sidebar'}
          aria-expanded={!collapsed}
          data-nav
        >
          <span className={s.navIcon}>{collapsed ? <ChevronIcon /> : <BackIcon />}</span>
          <span className={s.navLabel}>Collapse</span>
        </button>
      </div>
    </nav>
  );
}

/**
 * where the now-playing bar will be. its layout is final; every control is
 * inert and says why, because nothing plays until the player core lands.
 */
export function PlayerShell({ profileId }: { profileId: number | null }) {
  const rootRef = useRef<HTMLDivElement>(null);
  const mainRef = useRef<HTMLElement>(null);
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const { data: profiles } = useQuery(profilesQuery());
  const { data: playerSettings } = useQuery(playerSettingsQuery());
  const accentChoice = playerSettings?.settings.accent_color ?? 'emerald';
  const accentTokens = ACCENT_PALETTE[accentChoice] ?? ACCENT_PALETTE.emerald;
  const [gate, setGate] = useState<boolean | null>(null);

  useSpatialNav(rootRef);
  usePlayerSync();
  const nowPlaying = useNowPlaying();
  // the header sits clear over a hero at the top, and goes solid once the
  // page scrolls under it, so nothing reads through the search bar
  const [scrolled, setScrolled] = useState(false);
  const onMainScroll = () => {
    const next = (mainRef.current?.scrollTop ?? 0) > 4;
    setScrolled((was) => (was === next ? was : next));
  };
  // one card size for the whole player on this device, as a percent
  const [cardSize, setCardSize] = useCardSize('ss-player-card-scale', CARD_SCALE_RANGE);
  const cardScale = (cardSize ?? CARD_SCALE_RANGE.base) / 100;
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const toggleCollapsed = () =>
    setCollapsed((was) => {
      try {
        localStorage.setItem(COLLAPSE_KEY, was ? '0' : '1');
      } catch {
        /* private mode */
      }
      return !was;
    });

  // decided once per entry, when the profile list is in
  useEffect(() => {
    if (gate !== null || !profiles) return;
    setGate(gateNeeded(profiles, profileId));
  }, [profiles, profileId, gate]);

  // each page starts at the top, and a remote lands on its first control
  useEffect(() => {
    const main = mainRef.current;
    if (!main) return;
    main.scrollTo({ top: 0 });
    const id = requestAnimationFrame(() =>
      focusFirstIn(main.querySelector('[data-page]') as HTMLElement),
    );
    return () => cancelAnimationFrame(id);
  }, [pathname]);

  // the manager's page title would otherwise stay, and its floating buttons
  // hide while the player is open (player.module.css)
  useEffect(() => {
    const before = document.title;
    document.title = 'SoulSync Player';
    document.body.dataset.playerOpen = '';
    // index.html marks a /player load before first paint so the manager never
    // flashes; keep it while the player is up, and drop it on the way out so
    // Back into a manager page shows the manager
    document.documentElement.classList.add('ss-player-boot');
    return () => {
      document.title = before;
      delete document.body.dataset.playerOpen;
      document.documentElement.classList.remove('ss-player-boot');
    };
  }, []);

  // straight under <body>: the manager's react host sits in a stacking
  // context of its own, which would hold the player under the manager chrome
  return createPortal(
    <div
      className={s.app}
      ref={rootRef}
      data-player-root
      data-bar={nowPlaying ? 'true' : undefined}
      data-side-collapsed={collapsed ? 'true' : undefined}
      style={
        {
          '--pl-card-scale': cardScale,
          '--accent-rgb': accentTokens.rgb,
          '--accent-light-rgb': accentTokens.lightRgb,
          '--accent': `rgb(${accentTokens.rgb})`,
          '--accent-light': `rgb(${accentTokens.lightRgb})`,
          '--accent-color': `rgb(${accentTokens.rgb})`,
        } as CSSProperties
      }
    >
      <Sidebar collapsed={collapsed} onToggle={toggleCollapsed} />
      <main className={s.main} ref={mainRef} onScroll={onMainScroll}>
        <div className={s.header} data-scrolled={scrolled ? 'true' : undefined}>
          {!TOP_LEVEL_PATHS.has(pathname) ? <SmartBackButton pathname={pathname} /> : null}
          <Search />
          <div className={s.headerEnd}>
            <ProfileChip
              currentId={profileId}
              cardSize={cardSize}
              cardRange={CARD_SCALE_RANGE}
              onCardSize={setCardSize}
            />
          </div>
        </div>
        <div className={s.page} data-page key={pathname}>
          <Outlet />
        </div>
      </main>
      {nowPlaying ? <TransportBar /> : null}
      <VideoPlayer />
      <MusicView />
      <ChannelBanner />
      <NoticeHost />
      <ReminderBell />
      <AddToPlaylistHost />
      <ContextMenuHost />
      {gate && profiles ? (
        <WhoGate profiles={profiles} currentId={profileId} onDone={() => setGate(false)} />
      ) : null}
      <PlayerBootOverlay ready={!!playerSettings} />
    </div>,
    document.body,
  );
}
