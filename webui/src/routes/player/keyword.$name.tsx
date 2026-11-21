import { useQuery } from '@tanstack/react-query';
import { createFileRoute } from '@tanstack/react-router';
import { useMemo } from 'react';

import { plural } from './-lib/format';
import { keywordQuery, videoCatalogQuery } from './-player.api';
import { extraCard, ownedByTmdb } from './-ui/detail';
import { CardGrid, CardRail, Empty, PageTitle, styles as s } from './-ui/kit';
import { NotPartOfProfile } from './-ui/not-part';
import { useSections } from './-ui/sections';

export const Route = createFileRoute('/player/keyword/$name')({
  component: KeywordPage,
});

/** everything tagged with a tmdb keyword: what you own first, then the rest */
function KeywordPage() {
  const sections = useSections();
  const { name } = Route.useParams();
  const titles = useQuery({ ...keywordQuery(name), enabled: sections.video });
  const catalog = useQuery({ ...videoCatalogQuery(), enabled: sections.video });
  const owned = useMemo(
    () => ownedByTmdb([...(catalog.data?.movies ?? []), ...(catalog.data?.shows ?? [])]),
    [catalog.data],
  );

  if (!sections.video) return <NotPartOfProfile what="Movies and TV" />;

  const all = [...(titles.data?.movies ?? []), ...(titles.data?.shows ?? [])];
  const mine = all.filter((t) => t.library_id ?? owned.get(t.tmdb_id));
  const movies = (titles.data?.movies ?? []).filter((t) => !(t.library_id ?? owned.get(t.tmdb_id)));
  const shows = (titles.data?.shows ?? []).filter((t) => !(t.library_id ?? owned.get(t.tmdb_id)));

  return (
    <>
      <div className={s.heroKicker} style={{ marginTop: '1.5rem' }}>
        Keyword
      </div>
      <PageTitle>{name}</PageTitle>
      {titles.isLoading ? <div className={s.skeleton} style={{ height: '40vh' }} /> : null}
      {titles.data && !all.length ? (
        <Empty title="Nothing tagged with this yet">TMDB has no titles under “{name}”.</Empty>
      ) : null}
      {mine.length ? (
        <section className={s.rail} aria-label="In your library">
          <div className={s.railHead}>
            <h2 className={s.sectionTitle}>In your library</h2>
            <span className={s.notYet}>{plural(mine.length, 'title')}</span>
          </div>
          <CardGrid cards={mine.map((t) => extraCard(t, owned))} />
        </section>
      ) : null}
      <CardRail title="Movies" cards={movies.map((t) => extraCard(t, owned))} />
      <CardRail title="TV" cards={shows.map((t) => extraCard(t, owned))} />
    </>
  );
}
