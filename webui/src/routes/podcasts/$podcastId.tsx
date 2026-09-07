import { createFileRoute } from '@tanstack/react-router';

import { PodcastDetailPage } from './-ui/podcast-detail-page';

export const Route = createFileRoute('/podcasts/$podcastId')({
  component: PodcastDetailRouteComponent,
});

function PodcastDetailRouteComponent() {
  const { podcastId } = Route.useParams();
  return <PodcastDetailPage podcastId={podcastId} />;
}
