import { createFileRoute } from '@tanstack/react-router';
import { useLayoutEffect } from 'react';

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
}

export const Route = createFileRoute('/settings')({
  validateSearch: (search: Record<string, unknown>): SettingsSearch =>
    search.media === 'video' ? { media: 'video' } : {},
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
  const { media } = Route.useSearch();
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
