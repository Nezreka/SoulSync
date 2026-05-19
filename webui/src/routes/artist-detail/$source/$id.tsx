import { createFileRoute } from '@tanstack/react-router';
import { useLayoutEffect } from 'react';

import { useShellBridge } from '@/platform/shell/route-controllers';

export const Route = createFileRoute('/artist-detail/$source/$id')({
  component: ArtistDetailPage,
});

// Thin legacy handoff: TanStack owns the URL shape here, but the vanilla JS
// artist-detail page still renders the actual experience for now.
function ArtistDetailPage() {
  const bridge = useShellBridge();
  const { source, id } = Route.useParams();

  useLayoutEffect(() => {
    if (!bridge) return;

    const normalizedSource = source.toLowerCase() === 'library' ? null : source.toLowerCase();
    bridge.navigateToArtistDetail(id, '', normalizedSource, {
      skipRouteChange: true,
    });
  }, [bridge, id, source]);

  return null;
}
