/**
 * player settings: a list of sections on the left, one section at a time on
 * the right. "you" holds your own things, each saying where it is kept (every
 * screen with the profile, or just this screen). "this server" is for admins
 * only. the open section is ?section=, so links and back work; on a phone
 * the list is the first screen and a section opens on its own.
 * every change saves at once, exactly where it always did.
 */

import { useQuery } from '@tanstack/react-query';
import { createFileRoute, Link, useRouteContext } from '@tanstack/react-router';
import { useEffect, useRef, useState, type KeyboardEvent } from 'react';

import { commercialsConfigQuery, playerIssuesQuery } from './-player.api';
import { BackIcon, CheckIcon, ChevronIcon } from './-ui/icons';
import { PageTitle } from './-ui/kit';
import {
  isSectionId,
  pickSection,
  sectionsFor,
  type SectionId,
  type SectionInfo,
} from './-ui/settings/catalog';
import { ConvertingSection } from './-ui/settings/converting';
import { SavedContext } from './-ui/settings/kit';
import { NotificationsSection } from './-ui/settings/notifications';
import {
  DownloadsSection,
  IssuesSection,
  LiveGuideSection,
  SetupSection,
} from './-ui/settings/server';
import p from './-ui/settings/settings.module.css';
import {
  LookSection,
  MusicSection,
  PictureSection,
  PlaybackSection,
  SubtitlesSection,
} from './-ui/settings/you';
import { SidebarSettings } from './-ui/sidebar-settings';

export const Route = createFileRoute('/player/settings')({
  validateSearch: (search: Record<string, unknown>): { section?: SectionId } =>
    isSectionId(search.section) ? { section: search.section } : {},
  component: SettingsPage,
});

function SectionBody({ id, isAdmin }: { id: SectionId; isAdmin: boolean }) {
  switch (id) {
    case 'playback':
      return <PlaybackSection />;
    case 'picture':
      return <PictureSection />;
    case 'subtitles':
      return <SubtitlesSection />;
    case 'music':
      return <MusicSection />;
    case 'look':
      return <LookSection />;
    case 'sidebar':
      return (
        <div className={p.card}>
          <SidebarSettings />
        </div>
      );
    case 'notifications':
      return <NotificationsSection />;
    case 'setup':
      return <SetupSection />;
    case 'downloads':
      return <DownloadsSection />;
    case 'converting':
      return <ConvertingSection />;
    case 'guide':
      return <LiveGuideSection isAdmin={isAdmin} />;
    case 'issues':
      return <IssuesSection />;
  }
}

/** up and down (and home, end) move through the list */
function onListKeys(e: KeyboardEvent<HTMLElement>) {
  const keys = ['ArrowDown', 'ArrowUp', 'Home', 'End'];
  if (!keys.includes(e.key)) return;
  const items = Array.from(e.currentTarget.querySelectorAll<HTMLElement>('[data-section-link]'));
  const at = items.indexOf(document.activeElement as HTMLElement);
  const next =
    e.key === 'Home'
      ? 0
      : e.key === 'End'
        ? items.length - 1
        : Math.min(items.length - 1, Math.max(0, at + (e.key === 'ArrowDown' ? 1 : -1)));
  if (!items[next]) return;
  e.preventDefault();
  items[next].focus();
}

function SectionLink({
  info,
  current,
  count,
}: {
  info: SectionInfo;
  current: boolean;
  count?: number;
}) {
  return (
    <Link
      to="/player/settings"
      search={{ section: info.id }}
      className={p.navItem}
      aria-current={current ? 'page' : undefined}
      data-section-link={info.id}
      data-nav
    >
      <span className={p.navIcon} aria-hidden="true">
        {info.icon}
      </span>
      <span className={p.navText}>
        <span className={p.navName}>{info.title}</span>
        <span className={p.navInside}>{info.inside}</span>
      </span>
      {count ? (
        <span className={p.navCount} aria-label={`${count} open`}>
          {count}
        </span>
      ) : null}
      <span className={p.navChevron} aria-hidden="true">
        <ChevronIcon />
      </span>
    </Link>
  );
}

function SettingsPage() {
  const { shell } = useRouteContext({ from: '/player' });
  const { section: asked } = Route.useSearch();
  const commConfig = useQuery(commercialsConfigQuery());
  const isAdmin = Boolean(commConfig.data?.can_edit ?? shell.profile?.isAdmin);
  const issues = useQuery(playerIssuesQuery());
  const openIssues = issues.data?.open_count ?? 0;
  const sections = sectionsFor(isAdmin);
  const current = pickSection(asked, sections);
  // a phone shows the list until a section is picked
  const view = asked && current.id === asked ? 'section' : 'list';

  const [saved, setSaved] = useState(false);
  const timerRef = useRef<number | null>(null);
  const flashSaved = () => {
    setSaved(true);
    if (timerRef.current) window.clearTimeout(timerRef.current);
    timerRef.current = window.setTimeout(() => setSaved(false), 1800);
  };
  useEffect(
    () => () => {
      if (timerRef.current) window.clearTimeout(timerRef.current);
    },
    [],
  );

  // a new section starts at its top
  const headRef = useRef<HTMLDivElement>(null);
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    headRef.current?.closest('main')?.scrollTo({ top: 0 });
  }, [asked]);

  const you = sections.filter((x) => x.group === 'you');
  const server = sections.filter((x) => x.group === 'server');
  const name = shell.profile?.name;

  return (
    <SavedContext.Provider value={flashSaved}>
      <div className={p.page} data-view={view} ref={headRef}>
        <header className={p.header}>
          <div>
            <PageTitle>Settings</PageTitle>
            <p className={p.lead}>
              {name ? (
                <>
                  <span className={p.avatar} aria-hidden="true">
                    {name.charAt(0).toUpperCase()}
                  </span>
                  <b>{name}</b>
                  <span aria-hidden="true">·</span>
                </>
              ) : null}
              <span>Each section says where it's saved: every screen, or just this one.</span>
            </p>
          </div>
        </header>

        <div className={p.frame}>
          <div
            className={p.nav}
            role="navigation"
            aria-label="Settings sections"
            onKeyDown={onListKeys}
          >
            <div className={p.navCap} id="settings-you">
              You
            </div>
            <div className={p.navGroup} role="list" aria-labelledby="settings-you">
              {you.map((info) => (
                <div role="listitem" key={info.id}>
                  <SectionLink
                    info={info}
                    current={info.id === current.id}
                    count={info.id === 'issues' ? openIssues : undefined}
                  />
                </div>
              ))}
            </div>
            {server.length ? (
              <>
                <div className={p.navCap} id="settings-server">
                  This server · admin
                </div>
                <div className={p.navGroup} role="list" aria-labelledby="settings-server">
                  {server.map((info) => (
                    <div role="listitem" key={info.id}>
                      <SectionLink
                        info={info}
                        current={info.id === current.id}
                        count={info.id === 'issues' ? openIssues : undefined}
                      />
                    </div>
                  ))}
                </div>
              </>
            ) : null}
          </div>

          <section className={p.pane} aria-labelledby="settings-section-title">
            <Link to="/player/settings" search={{}} className={p.back} data-nav>
              <BackIcon />
              <span>All settings</span>
            </Link>
            <div className={p.paneHead}>
              <div className={p.paneTitleRow}>
                <span className={p.paneIcon} aria-hidden="true">
                  {current.icon}
                </span>
                <h2 className={p.paneTitle} id="settings-section-title">
                  {current.title}
                </h2>
              </div>
              <p className={p.paneLead}>{current.description}</p>
              <div className={p.scopeRow}>
                <span className={p.scope} title="Where these settings are saved">
                  <span className={p.scopeDot} aria-hidden="true" />
                  {current.scope}
                  {current.id === 'issues' && openIssues ? ` · ${openIssues} open` : ''}
                </span>
                <span
                  className={p.savedBadge}
                  data-visible={saved ? 'true' : undefined}
                  role="status"
                >
                  <CheckIcon /> Saved
                </span>
              </div>
            </div>
            <div className={p.paneBody} key={current.id}>
              <SectionBody id={current.id} isAdmin={isAdmin} />
            </div>
          </section>
        </div>
      </div>
    </SavedContext.Provider>
  );
}
