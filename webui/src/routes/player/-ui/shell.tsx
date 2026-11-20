/**
 * the player app's frame: sidebar (a tab bar on phones), header with search
 * and the profile chip, the page, and the transport bar along the bottom.
 * it covers the manager entirely; the manager underneath is untouched.
 */

import { useQuery } from '@tanstack/react-query';
import { Link, Outlet, useRouterState } from '@tanstack/react-router';
import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

import { appURL } from '@/platform/url-base';

import { profilesQuery } from '../-player.api';
import { ExitIcon, NextIcon, PlayIcon, PrevIcon } from './icons';
import { PLAYBACK_NOT_YET } from './kit';
import s from './player.module.css';
import { gateNeeded, ProfileChip, WhoGate } from './profiles';
import { Search } from './search';
import { NAV, useSections } from './sections';
import { focusFirstIn, useSpatialNav } from './use-spatial-nav';

function Sidebar() {
  const sections = useSections();
  return (
    <nav className={s.side} aria-label="Player">
      <div className={s.brand}>
        <span className={s.brandMark} aria-hidden="true">
          ▶
        </span>
        SoulSync
      </div>
      {NAV.filter((entry) => entry.visible(sections)).map((entry) => (
        <Link
          key={entry.to}
          to={entry.to as '/player'}
          className={s.navLink}
          activeOptions={{ exact: !!entry.exact }}
          data-nav
        >
          <span className={s.navIcon}>{entry.icon}</span>
          <span className={s.navLabel}>{entry.label}</span>
          {entry.short ? <span className={s.navShort}>{entry.short}</span> : null}
        </Link>
      ))}
      <div className={s.sideFoot}>
        <a className={s.managerLink} href={appURL('/')} data-nav>
          <span className={s.navIcon}>
            <ExitIcon />
          </span>
          Back to SoulSync
        </a>
      </div>
    </nav>
  );
}

/**
 * where the now-playing bar will be. its layout is final; every control is
 * inert and says why, because nothing plays until the player core lands.
 */
function TransportBar() {
  return (
    <section className={s.bar} aria-label="Now playing">
      <div className={s.barNow}>
        <div className={s.barArt} aria-hidden="true" />
        <div className={s.barTitle}>Nothing playing</div>
      </div>
      <div className={s.barCenter}>
        <div className={s.barControls}>
          <button type="button" aria-disabled="true" aria-label="Previous" title={PLAYBACK_NOT_YET}>
            <PrevIcon />
          </button>
          <button
            type="button"
            className={s.barPlay}
            aria-disabled="true"
            aria-label="Play"
            title={PLAYBACK_NOT_YET}
          >
            <PlayIcon />
          </button>
          <button type="button" aria-disabled="true" aria-label="Next" title={PLAYBACK_NOT_YET}>
            <NextIcon />
          </button>
        </div>
        <div className={s.barScrub} aria-hidden="true">
          <span>0:00</span>
          <span className={s.barTrack} />
          <span>0:00</span>
        </div>
      </div>
      <div className={s.barEnd}>Playback arrives in the next stage</div>
    </section>
  );
}

export function PlayerShell({ profileId }: { profileId: number | null }) {
  const rootRef = useRef<HTMLDivElement>(null);
  const mainRef = useRef<HTMLElement>(null);
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const { data: profiles } = useQuery(profilesQuery());
  const [gate, setGate] = useState<boolean | null>(null);

  useSpatialNav(rootRef);

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
    return () => {
      document.title = before;
      delete document.body.dataset.playerOpen;
    };
  }, []);

  // straight under <body>: the manager's react host sits in a stacking
  // context of its own, which would hold the player under the manager chrome
  return createPortal(
    <div className={s.app} ref={rootRef}>
      <Sidebar />
      <main className={s.main} ref={mainRef}>
        <div className={s.header}>
          <Search />
          <ProfileChip currentId={profileId} />
        </div>
        <div className={s.page} data-page key={pathname}>
          <Outlet />
        </div>
      </main>
      <TransportBar />
      {gate && profiles ? (
        <WhoGate profiles={profiles} currentId={profileId} onDone={() => setGate(false)} />
      ) : null}
    </div>,
    document.body,
  );
}
