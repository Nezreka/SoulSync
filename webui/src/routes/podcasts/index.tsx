import { createFileRoute } from '@tanstack/react-router';

import { PodcastsBrowsePage } from './-ui/podcasts-page';

export const Route = createFileRoute('/podcasts/')({
  component: PodcastsBrowsePage,
});
