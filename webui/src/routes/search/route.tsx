import { createFileRoute } from '@tanstack/react-router';

import { guardPageAccess } from '@/platform/shell/route-guard';

import { SearchPage } from './-ui/search-page';

/**
 * The Search page.
 *
 * No loader: nothing is worth fetching before the user types. The controller
 * runs its own init on mount (/status then config-status) because the source
 * picker's initial state depends on both, and a search only happens once there
 * is a query.
 */
export const Route = createFileRoute('/search')({
  beforeLoad: ({ context }) => {
    guardPageAccess(context.shell.bridge, 'search');
  },
  component: SearchPage,
});
