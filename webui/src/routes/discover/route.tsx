import { createFileRoute } from '@tanstack/react-router';

import { guardPageAccess } from '@/platform/shell/route-guard';

import { DiscoverPage } from './-ui/discover-page';

/**
 * The Discover page.
 *
 * No loader: the page hook owns the tiered fetch order (above-the-fold shelves
 * release tier 2 as they settle), and a route loader in front of that would
 * either duplicate it or serialize it. The controllers all run on mount.
 */
export const Route = createFileRoute('/discover')({
  beforeLoad: ({ context }) => {
    guardPageAccess(context.shell.bridge, 'discover');
  },
  component: DiscoverPage,
});
