import { useQuery } from '@tanstack/react-query';
import { createFileRoute } from '@tanstack/react-router';
import { useMemo } from 'react';

import { backdropUrl, movieCard, showCard } from './-lib/rails';
import { studioCollectionsQuery, videoCatalogQuery } from './-player.api';
import { CollectionPage } from './-ui/collection';
import { NotFound, NotPartOfProfile } from './-ui/not-part';
import { useSections } from './-ui/sections';

export const Route = createFileRoute('/player/collections/$collectionId')({
  component: StudioCollectionPage,
});

/** one collection studio shelf: what's on it that this profile can see */
function StudioCollectionPage() {
  const sections = useSections();
  const id = Number(Route.useParams().collectionId);
  const shelves = useQuery({ ...studioCollectionsQuery(), enabled: sections.video });
  const catalog = useQuery({ ...videoCatalogQuery(), enabled: sections.video });
  const shelf = shelves.data?.find((c) => c.id === id);

  const cards = useMemo(() => {
    if (!shelf || !catalog.data) return [];
    if (shelf.media_type === 'movie') {
      const byId = new Map(catalog.data.movies.map((m) => [m.id, m]));
      return shelf.members.flatMap((m) => (byId.has(m) ? [movieCard(byId.get(m)!)] : []));
    }
    const byId = new Map(catalog.data.shows.map((x) => [x.id, x]));
    return shelf.members.flatMap((m) => (byId.has(m) ? [showCard(byId.get(m)!)] : []));
  }, [shelf, catalog.data]);

  if (!sections.video) return <NotPartOfProfile what="Movies and TV" />;
  if (!shelves.isLoading && !shelf) return <NotFound what="Collection" />;
  const lead = shelf?.members[0];
  return (
    <CollectionPage
      kicker="Collection"
      title={shelf?.name ?? ''}
      meta={
        shelf ? (
          <span>
            {cards.length} {shelf.media_type === 'movie' ? 'films' : 'shows'}
            {shelf.missing ? ` · ${shelf.missing} more not in your library` : ''}
          </span>
        ) : null
      }
      backdrop={lead && shelf ? backdropUrl(shelf.media_type, lead) : null}
      cards={cards}
      loading={shelves.isLoading || catalog.isLoading}
      empty="Nothing on this shelf is part of your profile."
    />
  );
}
