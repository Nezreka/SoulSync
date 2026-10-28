import { createFileRoute } from '@tanstack/react-router';

import { useProfile, useReactPageShell } from '@/platform/shell/route-controllers';
import { guardPageAccess } from '@/platform/shell/route-guard';

import { parseToolsMedia } from './-tools.media';
import { ToolsPage } from './-ui/tools-page';

export interface ToolsSearch {
  /** 'video' is the video side's tools page. absent = music. */
  media?: 'video';
  /** /tools?job=<id> lights up one job card (the origins modal links here). */
  job?: string;
}

/**
 * No loader. Every card on this page hydrates itself on mount — the database
 * stats, the active media server, the backup list, the maintenance jobs — which
 * is exactly what the vanilla did from its own DOMContentLoaded handlers. Warming
 * any of it here would gate the whole page on a backend that the vanilla was
 * happy to render around.
 */
export const Route = createFileRoute('/tools')({
  validateSearch: (search: Record<string, unknown>): ToolsSearch => ({
    ...(parseToolsMedia(search.media) === 'video' ? { media: 'video' as const } : {}),
    ...(typeof search.job === 'string' && search.job ? { job: search.job } : {}),
  }),
  beforeLoad: ({ context, search }) => {
    // the video tools page asks for video-tools (and the video side), not the
    // music tools permission. same as the wishlist's video tabs.
    guardPageAccess(
      context.shell.bridge,
      'tools',
      search.media ? `?media=${search.media}` : undefined,
    );
  },
  component: ToolsRoute,
});

function ToolsRoute() {
  const { media } = Route.useSearch();
  const profile = useProfile();
  useReactPageShell('tools', media ? `?media=${media}` : undefined);
  return <ToolsPage media={parseToolsMedia(media)} isAdmin={profile.isAdmin} />;
}
