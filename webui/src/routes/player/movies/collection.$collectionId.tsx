import { useQuery } from '@tanstack/react-query';
import { createFileRoute } from '@tanstack/react-router';
import { useMemo } from 'react';

import { backdropUrl, movieCard } from '../-lib/rails';
import { extrasQuery, videoCatalogQuery } from '../-player.api';
import { CollectionPage } from '../-ui/collection';
import { extraCard, ownedByTmdb } from '../-ui/detail';
import { NotFound, NotPartOfProfile } from '../-ui/not-part';
import { useSections } from '../-ui/sections';

export const Route = createFileRoute('/player/movies/collection/$collectionId')({
  component: FranchisePage,
});

/**
 * a franchise, in release order: the films you own, and the ones you
 * don't (from tmdb's part list, via one owned film's extras), each of
 * which opens a page to ask for it.
 */
function FranchisePage() {
  const sections = useSections();
  const id = Number(Route.useParams().collectionId);
  const catalog = useQuery({ ...videoCatalogQuery(), enabled: sections.movies });
  const owned = useMemo(
    () => (catalog.data?.movies ?? []).filter((m) => m.collection_id === id),
    [catalog.data, id],
  );
  const lead = owned.find((m) => m.has_backdrop) ?? owned[0];
  const extras = useQuery({ ...extrasQuery('movie', lead?.id ?? 0), enabled: !!lead });
  const byTmdb = useMemo(() => ownedByTmdb(catalog.data?.movies ?? []), [catalog.data]);

  const cards = useMemo(() => {
    const parts = extras.data?.collection?.items ?? [];
    if (!parts.length)
      return owned
        .slice()
        .sort((a, b) => (a.year ?? 0) - (b.year ?? 0))
        .map(movieCard);
    return parts
      .slice()
      .sort((a, b) => String(a.year ?? '9999').localeCompare(String(b.year ?? '9999')))
      .map((t) => {
        const mine = owned.find((m) => m.tmdb_id === t.tmdb_id);
        return mine ? movieCard(mine) : extraCard(t, byTmdb);
      });
  }, [extras.data, owned, byTmdb]);

  if (!sections.movies) return <NotPartOfProfile what="Movies" />;
  if (catalog.data && !owned.length) return <NotFound what="Collection" />;
  const total = extras.data?.collection?.items?.length ?? owned.length;
  return (
    <CollectionPage
      kicker="Collection"
      title={(owned[0]?.collection ?? extras.data?.collection?.name ?? '').replace(
        / Collection$/,
        '',
      )}
      meta={
        <span>
          {owned.length} of {total} films in your library
        </span>
      }
      backdrop={lead ? backdropUrl('movie', lead.id) : null}
      cards={cards}
      loading={catalog.isLoading}
      empty="None of these films are part of your profile."
    />
  );
}
