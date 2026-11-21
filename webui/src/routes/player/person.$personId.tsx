import { useQuery } from '@tanstack/react-query';
import { createFileRoute } from '@tanstack/react-router';
import { useMemo, useState } from 'react';

import type { RailCard } from './-lib/rails';
import type { PersonCredit } from './-player.types';

import { plainText, plural, shortDate } from './-lib/format';
import { posterSet, remoteImage } from './-lib/images';
import { dedupeCredits } from './-lib/people';
import { backdropUrl } from './-lib/rails';
import { personQuery } from './-player.api';
import { DetailLayout, ImageRow, InfoPanel, ReadMore, Section } from './-ui/detail';
import { CardGrid, CardRail, Hero, Tabs, useViewMode, ViewToggle, styles as s } from './-ui/kit';
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
    ...(owned
      ? {
          art: posterSet(c.kind, c.library_id!).src,
          artSet: posterSet(c.kind, c.library_id!).srcSet,
        }
      : { art: remoteImage(c.poster) }),
    // yours opens its page; anything else opens a page to ask for it
    href: owned
      ? `/player/${c.kind === 'movie' ? 'movies' : 'tv'}/${c.library_id}`
      : `/player/title/${c.kind}/${c.tmdb_id}`,
    shape: 'poster',
    have: owned ? 'In your library' : undefined,
    muted: owned ? undefined : 'Not in your library',
    preview: owned ? { kind: c.kind, id: c.library_id! } : undefined,
  };
}

function PersonPage() {
  const sections = useSections();
  const id = Number(Route.useParams().personId);
  const person = useQuery({ ...personQuery(id), enabled: sections.video && Number.isFinite(id) });
  const [filter, setFilter] = useState<Filter>('all');
  const [view, setView] = useViewMode('filmography');

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

  // tmdb sends credits most-known first: that order is "known for"
  const knownFor = credits.slice(0, 14);
  const years = credits.map(yearOf).filter((y) => y > 1800);
  const active = years.length ? `${Math.min(...years)}–${Math.max(...years)}` : null;
  const bio = plainText(p.biography);

  return (
    <>
      <Hero
        backdrop={scene?.library_id ? backdropUrl(scene.kind, scene.library_id) : null}
        poster={remoteImage(p.photo, 'hero')}
        kicker={p.known_for ?? 'Person'}
        title={p.name}
        meta={
          <>
            {active ? <span>{active}</span> : null}
            <span>{plural(credits.length, 'credit')}</span>
            {owned.length ? <span>{owned.length} in your library</span> : null}
          </>
        }
        overview={bio ? bio.split('\n')[0] : null}
      />

      <DetailLayout
        main={
          <>
            <div className={s.detailRail}>
              <CardRail title="Known for" cards={knownFor.map(creditCard)} />
            </div>
            {owned.length ? (
              <div className={s.detailRail}>
                <CardRail title="In your library" cards={owned.map(creditCard)} />
              </div>
            ) : null}
            {bio && bio.includes('\n') ? (
              <Section title="Biography">
                <ReadMore text={bio} />
              </Section>
            ) : null}
            {(p.photos?.length ?? 0) > 1 ? (
              <Section title="Photos">
                <ImageRow images={p.photos!.slice(0, 20)} shape="portrait" />
              </Section>
            ) : null}
            <section className={s.detailSection} aria-label="Filmography">
              <div className={s.toolbar}>
                <h2 className={s.sectionTitle}>Filmography</h2>
                <div className={s.fieldRow}>
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
                  <ViewToggle value={view} onChange={setView} />
                </div>
              </div>
              <CardGrid cards={shown.map(creditCard)} view={view} />
            </section>
          </>
        }
        side={
          <InfoPanel title="Personal info">
            <Facts
              rows={[
                ['Known for', p.known_for],
                [
                  'Born',
                  p.birthday
                    ? `${shortDate(p.birthday)}${!p.deathday && age ? ` (age ${age})` : ''}`
                    : null,
                ],
                [
                  'Died',
                  p.deathday ? `${shortDate(p.deathday)}${age ? ` (aged ${age})` : ''}` : null,
                ],
                ['Birthplace', p.place_of_birth],
                ['Years active', active],
                ['Acting', acting.length ? plural(acting.length, 'credit') : null],
                ['Behind the camera', crew.length ? plural(crew.length, 'credit') : null],
                ['In your library', owned.length ? plural(owned.length, 'title') : null],
                [
                  'Also known as',
                  p.also_known_as?.length ? p.also_known_as.slice(0, 3).join(', ') : null,
                ],
              ]}
            />
          </InfoPanel>
        }
      />
    </>
  );
}
