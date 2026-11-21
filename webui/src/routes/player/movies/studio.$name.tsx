import { useQuery } from '@tanstack/react-query';
import { createFileRoute } from '@tanstack/react-router';
import { useMemo } from 'react';

import { plural } from '../-lib/format';
import { backdropUrl, movieCard } from '../-lib/rails';
import { videoCatalogQuery } from '../-player.api';
import { CollectionPage } from '../-ui/collection';
import { NotPartOfProfile } from '../-ui/not-part';
import { useSections } from '../-ui/sections';

export const Route = createFileRoute('/player/movies/studio/$name')({
  component: StudioPage,
});

function StudioPage() {
  const sections = useSections();
  const { name } = Route.useParams();
  const catalog = useQuery({ ...videoCatalogQuery(), enabled: sections.movies });
  const films = useMemo(
    () =>
      (catalog.data?.movies ?? [])
        .filter((m) => (m.studio ?? '').toLowerCase() === name.toLowerCase())
        .sort((a, b) => (b.year ?? 0) - (a.year ?? 0)),
    [catalog.data, name],
  );
  if (!sections.movies) return <NotPartOfProfile what="Movies" />;
  const lead = [...films]
    .sort((a, b) => (b.rating ?? 0) - (a.rating ?? 0))
    .find((m) => m.has_backdrop);
  return (
    <CollectionPage
      kicker="Studio"
      title={name}
      meta={<span>{plural(films.length, 'film')} in your library</span>}
      backdrop={lead ? backdropUrl('movie', lead.id) : null}
      cards={films.map(movieCard)}
      loading={catalog.isLoading}
      empty={`No films from ${name} in your library.`}
    />
  );
}
