import { createFileRoute } from '@tanstack/react-router';
import { useEffect, useLayoutEffect } from 'react';

import { nav, navToSearch } from '@/features/settings/nav';
import { SettingsPage } from '@/features/settings/settings-page';
import { LegacyRouteController } from '@/platform/shell/route-controllers';
import { guardPageAccess } from '@/platform/shell/route-guard';
import { getShellRouteByPageId } from '@/platform/shell/route-manifest';

/**
 * Settings (SETTINGS_PORT.md step V4c). The video side's settings are this
 * page on ?media=video, handed off by video-side.js like Tools and the
 * calendar: the side stays video, and the video sections load when they hear
 * the video settings page was shown, as when the vanilla page was shared.
 */

function isReactOwned(): boolean {
  return getShellRouteByPageId('settings')?.kind === 'react';
}

export interface SettingsSearch {
  media?: 'video';
  /** where on the page (features/settings/nav.ts): the tab, its pill, the
   *  kind of media a pill shows, and the open sheet */
  tab?: string;
  view?: string;
  kind?: string;
  open?: string;
}

const word = (value: unknown) =>
  typeof value === 'string' && /^[\w-]{1,40}$/.test(value) ? value : undefined;

export const Route = createFileRoute('/settings')({
  validateSearch: (search: Record<string, unknown>): SettingsSearch => {
    const out: SettingsSearch = search.media === 'video' ? { media: 'video' } : {};
    for (const key of ['tab', 'view', 'kind', 'open'] as const) {
      const value = word(search[key]);
      if (value) out[key] = value;
    }
    return out;
  },
  beforeLoad: ({ context, search }) => {
    // the video side's settings ask for video-settings, as the dashboard and
    // automations do
    guardPageAccess(context.shell.bridge, 'settings', search.media ? '?media=video' : undefined);
  },
  component: SettingsRouteComponent,
});

function SettingsRouteComponent() {
  if (!isReactOwned()) return <LegacyRouteController pathname="/settings" />;
  return <SettingsRoutePage />;
}

function SettingsRoutePage() {
  const search = Route.useSearch();
  const { media, tab, view, kind, open } = search;
  const navigate = Route.useNavigate();
  // the address is where the page is: a link, Back and a reload all land
  // here first, before the page opens
  useLayoutEffect(() => {
    nav.fromAddress({ tab, view, kind, open });
  }, [tab, view, kind, open]);
  // and the page moving (a tab, a pill, a sheet) is written back. a tab or a
  // pill is a new entry, so Back returns from it; the rest replaces
  useEffect(() => {
    const write = (how: 'push' | 'replace') =>
      void navigate({
        search: (prev) => ({
          ...(prev.media ? { media: prev.media } : {}),
          ...navToSearch(nav.get()),
        }),
        replace: how === 'replace',
      });
    // a bare /settings gets its tab written in
    if (!tab) write('replace');
    return nav.subscribe((_state, how) => {
      if (how !== 'address') write(how);
    });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  // each visit opens the page as init.js did (the first tab, the load), once
  // its markup is in the document
  useLayoutEffect(() => {
    // the shell bundle's (window): one copy of its state, the pending tab too
    void (window as Window & { openSettingsPage?: () => Promise<void> }).openSettingsPage?.();
    if (media === 'video')
      document.dispatchEvent(
        new CustomEvent('soulsync:video-page-shown', { detail: 'video-settings' }),
      );
  }, [media]);
  return <SettingsPage />;
}
