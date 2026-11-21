import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { createFileRoute } from '@tanstack/react-router';
import { useMemo } from 'react';

import type { RailCard } from './-lib/rails';
import type { CatalogMovie, CatalogShow } from './-player.types';

import { runtime } from './-lib/format';
import { backdropSet, remoteImage } from './-lib/images';
import {
  backdropUrl,
  continueRails,
  newestFirst,
  movieCard,
  recentAlbumCards,
  recentBookCards,
  recentEpisodeCards,
  recentMovieCards,
  recentYoutubeCards,
} from './-lib/rails';
import {
  albumsQuery,
  booksQuery,
  continueQuery,
  finishProgress,
  podcastSubsQuery,
  removeProgress,
  videoCatalogQuery,
} from './-player.api';
import {
  Badge,
  CardRail,
  Empty,
  Hero,
  PlayButton,
  PlayerLink,
  SkeletonRail,
  styles as s,
} from './-ui/kit';
import { useSections } from './-ui/sections';

export const Route = createFileRoute('/player/')({
  component: PlayerHome,
});

type Spotlight = { kind: 'movie'; item: CatalogMovie } | { kind: 'show'; item: CatalogShow };

/** the newest thing with real backdrop art leads the page */
function pickSpotlight(movies: CatalogMovie[], shows: CatalogShow[]): Spotlight | null {
  const candidates: (Spotlight & { at: string })[] = [
    ...movies
      .filter((m) => m.has_backdrop)
      .map((m) => ({ kind: 'movie' as const, item: m, at: m.added_at ?? '' })),
    ...shows
      .filter((x) => x.has_backdrop)
      .map((x) => ({ kind: 'show' as const, item: x, at: x.added_at ?? '' })),
  ];
  return newestFirst(candidates, (c) => c.at)[0] ?? null;
}

function PlayerHome() {
  const sections = useSections();
  const queryClient = useQueryClient();
  const progress = useQuery(continueQuery());
  const catalog = useQuery({ ...videoCatalogQuery(), enabled: sections.video });
  const albums = useQuery({
    ...albumsQuery({ sort: 'added', limit: 20 }),
    enabled: sections.music,
  });
  const books = useQuery({ ...booksQuery(), enabled: sections.audiobooks });
  const podcasts = useQuery({ ...podcastSubsQuery(), enabled: sections.podcasts });

  const refresh = () => void queryClient.invalidateQueries({ queryKey: ['player', 'continue'] });
  const remove = useMutation({
    mutationFn: (card: RailCard) => removeProgress(card.source!.itemType, card.source!.itemId),
    onSettled: refresh,
  });
  const finish = useMutation({
    mutationFn: (card: RailCard) => finishProgress(card.source!.itemType, card.source!.itemId),
    onSettled: refresh,
  });

  const rails = useMemo(() => continueRails(progress.data ?? []), [progress.data]);
  const cat = catalog.data;
  const spotlight = useMemo(() => (cat ? pickSpotlight(cat.movies, cat.shows) : null), [cat]);

  // a library's worth of sorting: once per catalog, not once per render
  const { recentMovies, topRated } = useMemo(() => {
    if (!cat || !sections.movies) return { recentMovies: [], topRated: [] };
    return {
      recentMovies: recentMovieCards(cat.movies),
      topRated: cat.movies
        .filter((m) => (m.rating ?? 0) > 0)
        .sort((a, b) => (b.rating ?? 0) - (a.rating ?? 0))
        .slice(0, 20)
        .map((m) => ({ ...movieCard(m), key: `top:${m.id}` })),
    };
  }, [cat, sections.movies]);
  const recentEpisodes = cat && sections.tv ? recentEpisodeCards(cat.recent_episodes) : [];
  const recentVideos = cat ? recentYoutubeCards(cat.youtube) : [];
  const recentAlbums = recentAlbumCards(albums.data?.albums ?? []);
  const recentBooks = recentBookCards(books.data ?? []);
  const podcastCards: RailCard[] = (podcasts.data ?? []).slice(0, 20).map((p) => ({
    key: `podcast:${p.id}`,
    title: p.title,
    subtitle: p.author ?? undefined,
    art: remoteImage(p.artwork_url),
    href: `/player/podcasts/${p.id}`,
    shape: 'square',
  }));

  const loading = progress.isLoading || catalog.isLoading || albums.isLoading;
  const nothing =
    !loading &&
    !rails.length &&
    !recentMovies.length &&
    !recentEpisodes.length &&
    !recentVideos.length &&
    !recentAlbums.length &&
    !recentBooks.length &&
    !podcastCards.length;

  return (
    <>
      {spotlight ? (
        <Hero
          backdrop={backdropUrl(spotlight.kind, spotlight.item.id)}
          backdropSrcSet={backdropSet(spotlight.kind, spotlight.item.id).srcSet}
          kicker={spotlight.kind === 'movie' ? 'New in movies' : 'New episodes'}
          title={spotlight.item.title}
          meta={
            <>
              {spotlight.item.year ? <span>{spotlight.item.year}</span> : null}
              {spotlight.kind === 'movie' && spotlight.item.runtime_seconds ? (
                <span>{runtime(spotlight.item.runtime_seconds)}</span>
              ) : null}
              {spotlight.kind === 'show' ? (
                <span>
                  {spotlight.item.episode_count} episode
                  {spotlight.item.episode_count === 1 ? '' : 's'}
                </span>
              ) : null}
              {spotlight.item.genres.slice(0, 3).map((g) => (
                <span key={g}>{g}</span>
              ))}
            </>
          }
          badges={
            spotlight.item.content_rating ? (
              <div className={s.badges}>
                <Badge>{spotlight.item.content_rating}</Badge>
              </div>
            ) : null
          }
          actions={
            <>
              <PlayButton />
              <PlayerLink
                href={
                  spotlight.kind === 'movie'
                    ? `/player/movies/${spotlight.item.id}`
                    : `/player/tv/${spotlight.item.id}`
                }
                className={s.btn}
                data-nav
              >
                More info
              </PlayerLink>
            </>
          }
        />
      ) : (
        <h1 className={s.pageTitle}>Home</h1>
      )}

      {rails.map((rail) => (
        <CardRail
          key={rail.id}
          title={rail.label}
          cards={rail.cards}
          actionsFor={(card) => [
            { label: 'Mark as finished', onSelect: () => finish.mutate(card) },
            { label: 'Remove from this row', onSelect: () => remove.mutate(card) },
          ]}
        />
      ))}

      {loading ? <SkeletonRail /> : null}

      <CardRail
        title="Recently added movies"
        cards={recentMovies}
        more={{ to: '/player/movies' }}
      />
      <CardRail title="New episodes" cards={recentEpisodes} more={{ to: '/player/tv' }} />
      <CardRail title="Recently added music" cards={recentAlbums} more={{ to: '/player/music' }} />
      <CardRail title="New on YouTube" cards={recentVideos} more={{ to: '/player/youtube' }} />
      <CardRail
        title="Recently added audiobooks"
        cards={recentBooks}
        more={{ to: '/player/audiobooks' }}
      />
      <CardRail title="Your podcasts" cards={podcastCards} more={{ to: '/player/podcasts' }} />

      {spotlight && sections.movies && cat ? (
        <CardRail title="Top rated in your library" cards={topRated} />
      ) : null}

      {nothing ? (
        <div className={s.rail}>
          <Empty title="Nothing here yet">
            Your library is empty for this profile, or none of its sections are part of it. Things
            you add in SoulSync show up here.
          </Empty>
        </div>
      ) : null}

      {!loading && !rails.length && !nothing ? (
        <div className={s.rail}>
          <Empty title="Continue watching and listening">
            Once playback lands, whatever you start shows up here so you can pick up where you left
            off.
          </Empty>
        </div>
      ) : null}
    </>
  );
}
