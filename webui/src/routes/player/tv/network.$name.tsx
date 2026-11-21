import { useQuery } from '@tanstack/react-query';
import { createFileRoute } from '@tanstack/react-router';
import { useMemo } from 'react';

import { plural } from '../-lib/format';
import { backdropUrl, showCard } from '../-lib/rails';
import { videoCatalogQuery } from '../-player.api';
import { CollectionPage } from '../-ui/collection';
import { NotPartOfProfile } from '../-ui/not-part';
import { useSections } from '../-ui/sections';

export const Route = createFileRoute('/player/tv/network/$name')({
  component: NetworkPage,
});

function NetworkPage() {
  const sections = useSections();
  const { name } = Route.useParams();
  const catalog = useQuery({ ...videoCatalogQuery(), enabled: sections.tv });
  const shows = useMemo(
    () =>
      (catalog.data?.shows ?? [])
        .filter((x) => (x.network ?? '').toLowerCase() === name.toLowerCase())
        .sort((a, b) => (b.rating ?? 0) - (a.rating ?? 0)),
    [catalog.data, name],
  );
  if (!sections.tv) return <NotPartOfProfile what="TV shows" />;
  const lead = shows.find((x) => x.has_backdrop);
  const episodes = shows.reduce((n, x) => n + x.episode_count, 0);
  return (
    <CollectionPage
      kicker="Network"
      title={name}
      meta={
        <>
          <span>{plural(shows.length, 'show')}</span>
          <span>{plural(episodes, 'episode')}</span>
        </>
      }
      backdrop={lead ? backdropUrl('show', lead.id) : null}
      cards={shows.map(showCard)}
      loading={catalog.isLoading}
      empty={`No shows from ${name} in your library.`}
    />
  );
}
