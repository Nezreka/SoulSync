import { createFileRoute } from '@tanstack/react-router';

import { profileAsksFirst } from '@/platform/shell/download-rights';
import { useProfile, useReactPageShell } from '@/platform/shell/route-controllers';
import { guardPageAccess } from '@/platform/shell/route-guard';

import { DashboardPage } from './-ui/dashboard-page';
import { VideoDashboardPage } from './-video/video-dashboard-page';

export interface DashboardSearch {
  /** 'video' is the video side's dashboard. absent = music. */
  media?: 'video';
}

/**
 * No loader — every card hydrates itself on mount (the socket seam plus its
 * own fallback fetch), exactly as the vanilla loadDashboardData fired its
 * loads in parallel and rendered around whatever hadn't answered yet.
 */
export const Route = createFileRoute('/dashboard')({
  validateSearch: (search: Record<string, unknown>): DashboardSearch =>
    search.media === 'video' ? { media: 'video' } : {},
  beforeLoad: ({ context, search }) => {
    // the video dashboard asks for video-dashboard (and the video side), not
    // the music dashboard's permission. same as the video tools page.
    guardPageAccess(context.shell.bridge, 'dashboard', search.media ? '?media=video' : undefined);
  },
  component: DashboardRoute,
});

function DashboardRoute() {
  const { media } = Route.useSearch();
  return media === 'video' ? <VideoDashboardRoute /> : <DashboardPage />;
}

function VideoDashboardRoute() {
  const profile = useProfile();
  useReactPageShell('dashboard', '?media=video');
  return (
    <VideoDashboardPage
      profileId={profile.profileId}
      isAdmin={profile.isAdmin}
      canDownload={!profileAsksFirst()}
    />
  );
}
