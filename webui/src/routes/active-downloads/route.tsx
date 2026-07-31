import { createFileRoute, redirect } from '@tanstack/react-router';

import { getProfileHomePath } from '@/platform/shell/bridge';

import { ActiveDownloadsPage } from './-ui/active-downloads-page';

/**
 * The Downloads page.
 *
 * No loader: everything here is live and polled, so anything fetched before
 * render would be stale by the time it painted. The controllers fetch on mount.
 */
export const Route = createFileRoute('/active-downloads')({
  beforeLoad: ({ context }) => {
    const { bridge } = context.shell;

    if (!bridge.isPageAllowed('active-downloads')) {
      throw redirect({ href: getProfileHomePath(bridge), replace: true });
    }
  },
  component: ActiveDownloadsPage,
});
