import { PageHeader } from '@/components/page-header';
import { useReactPageShell } from '@/platform/shell/route-controllers';

export function PodcastsPage() {
  useReactPageShell('podcasts');

  return (
    <div className="page-shell podcasts-page-container">
      <PageHeader
        title="Podcasts"
        subtitle="Discover, search, and download podcast episodes"
      />
    </div>
  );
}
