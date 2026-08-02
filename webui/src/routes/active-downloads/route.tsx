import { createFileRoute } from '@tanstack/react-router';

import { guardPageAccess } from '@/platform/shell/route-guard';

import { ActiveDownloadsPage } from './-ui/active-downloads-page';

/**
 * The Downloads page.
 *
 * No loader: everything here is live and polled, so anything fetched before
 * render would be stale by the time it painted. The controllers fetch on mount.
 */
export const Route = createFileRoute('/active-downloads')({
  beforeLoad: ({ context }) => {
    guardPageAccess(context.shell.bridge, 'active-downloads');
  },
  component: ActiveDownloadsPage,
});
