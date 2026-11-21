import { useQuery } from '@tanstack/react-query';
import { createFileRoute } from '@tanstack/react-router';
import { useMemo, useState } from 'react';

import type { RailCard } from './-lib/rails';
import type { PersonCredit } from './-player.types';

import { plainText, plural, shortDate } from './-lib/format';
import { dedupeCredits } from './-lib/people';
import { backdropUrl, posterUrl, proxiedImage } from './-lib/rails';
import { personQuery } from './-player.api';
import { CardGrid, CardRail, Hero, Tabs, styles as s } from './-ui/kit';
import { NotFound, NotPartOfProfile } from './-ui/not-part';
import { useSections } from './-ui/sections';
import { Facts } from './-ui/video-facts';

export const Route = createFileRoute('/player/person/$personId')({
  component: PersonPage,
});

type Filter = 'all' | 'acting' | 'crew';

function yearOf(c: PersonCredit): number {
  return Number.parseInt(String(c.year ?? ''), 10) || 0;
}

/** an owned credit opens the player's page; the rest show what they are */
function creditCard(c: PersonCredit): RailCard {
  const owned = !!c.library_id;
  return {
    key: `${c.kind}:${c.tmdb_id}:${c.role ?? ''}`,
    title: c.title,
    subtitle: [c.year, c.role].filter(Boolean).join(' · '),
    art: owned ? posterUrl(c.kind, c.library_id!) : c.poster ? proxiedImage(c.poster) : null,
    href: owned ? `/player/${c.kind === 'movie' ? 'movies' : 'tv'}/${c.library_id}` : undefined,
    shape: 'poster',
    flag: owned ? { label: 'In your library', tone: 'yes' } : undefined,
    preview: owned ? { kind: c.kind, id: c.library_id! } : undefined,
  };
}

function PersonPage() {
  const sections = useSections();
  const id = Number(Route.useParams().personId);
  const person = useQuery({ ...personQuery(id), enabled: sections.video && Number.isFinite(id) });
  const [filter, setFilter] = useState<Filter>('all');

  const credits = useMemo(() => dedupeCredits(person.data?.credits ?? []), [person.data]);
  if (!sections.video) return <NotPartOfProfile what="Movies and TV" />;
  if (person.isLoading)
    return <div className={s.skeleton} style={{ height: '60vh', marginTop: '2rem' }} />;
  const p = person.data;
  if (!p || person.isError) return <NotFound what="Person" />;

  const owned = credits.filter((c) => c.library_id).sort((a, b) => yearOf(b) - yearOf(a));
  const acting = credits.filter((c) => (c.department ?? 'Acting') === 'Acting');
  const crew = credits.filter((c) => (c.department ?? 'Acting') !== 'Acting');
  const shown = (filter === 'acting' ? acting : filter === 'crew' ? crew : credits)
    .slice()
    .sort((a, b) => yearOf(b) - yearOf(a));
  // the backdrop: the best-known owned title, else nothing
  const scene = owned[0];
  const age = p.birthday
    ? Math.floor(
        ((p.deathday ? new Date(p.deathday) : new Date()).getTime() -
          new Date(p.birthday).getTime()) /
          31_557_600_000,
      )
    : null;

  return (
    <>
      <Hero
        backdrop={scene?.library_id ? backdropUrl(scene.kind, scene.library_id) : null}
        poster={proxiedImage(p.photo)}
        kicker={p.known_for ?? 'Person'}
        title={p.name}
        meta={
          <>
            <span>{plural(credits.length, 'credit')}</span>
            {owned.length ? <span>{owned.length} in your library</span> : null}
          </>
        }
        overview={plainText(p.biography)}
      />

      <div className={s.rail}>
        <Facts
          rows={[
            ['Known for', p.known_for],
            [
              'Born',
              p.birthday
                ? `${shortDate(p.birthday)}${!p.deathday && age ? ` (age ${age})` : ''}`
                : null,
            ],
            ['Died', p.deathday ? `${shortDate(p.deathday)}${age ? ` (aged ${age})` : ''}` : null],
            ['Birthplace', p.place_of_birth],
            [
              'Also known as',
              p.also_known_as?.length ? p.also_known_as.slice(0, 3).join(', ') : null,
            ],
          ]}
        />
      </div>

      <CardRail title="In your library" cards={owned.map(creditCard)} />

      {(p.photos?.length ?? 0) > 1 ? (
        <section className={s.rail} aria-label="Photos">
          <div className={s.railHead}>
            <h2 className={s.sectionTitle}>Photos</h2>
          </div>
          <div className={s.people}>
            {p.photos!.slice(0, 16).map((url) => (
              <div key={url} className={s.photo}>
                <img src={proxiedImage(url) ?? ''} alt="" loading="lazy" />
              </div>
            ))}
          </div>
        </section>
      ) : null}

      <section className={s.rail} aria-label="Filmography">
        <div className={s.toolbar}>
          <h2 className={s.sectionTitle}>Filmography</h2>
          {acting.length && crew.length ? (
            <Tabs
              label="Credits"
              value={filter}
              options={[
                { value: 'all', label: `All ${credits.length}` },
                { value: 'acting', label: `Acting ${acting.length}` },
                { value: 'crew', label: `Behind the camera ${crew.length}` },
              ]}
              onChange={setFilter}
            />
          ) : null}
        </div>
        <CardGrid cards={shown.map(creditCard)} />
      </section>
    </>
  );
}
