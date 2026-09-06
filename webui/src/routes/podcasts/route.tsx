import { createFileRoute } from '@tanstack/react-router';

import { guardPageAccess } from '@/platform/shell/route-guard';

import { PodcastsPage } from './-ui/podcasts-page';

export const Route = createFileRoute('/podcasts')({
  beforeLoad: ({ context }) => {
    guardPageAccess(context.shell.bridge, 'podcasts');
  },
  component: PodcastsPage,
});
