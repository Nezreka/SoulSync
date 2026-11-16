import { createFileRoute } from '@tanstack/react-router';

import { guardPageAccess } from '@/platform/shell/route-guard';

import { UnifiedDownloadsPage } from './-ui/unified-downloads-page';

/**
 * The unified Downloads page — music + video behind a Music | Video
 * switcher (?media=music|video). The old /video-downloads route redirects
 * here with media=video.
 *
 * No loader: everything here is live and polled, so anything fetched before
 * render would be stale by the time it painted. The controllers fetch on mount.
 */
export const Route = createFileRoute('/active-downloads')({
  validateSearch: (
    search: Record<string, unknown>,
  ): { media: 'music' | 'video'; open?: 'recycle' } => ({
    media: search.media === 'video' ? 'video' : 'music',
    // #2128: the Library's recycle bin button lands straight on the bin
    ...(search.open === 'recycle' ? { open: 'recycle' as const } : {}),
  }),
  beforeLoad: ({ context }) => {
    guardPageAccess(context.shell.bridge, 'active-downloads');
  },
  component: UnifiedDownloadsPage,
});
