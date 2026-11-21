import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { createFileRoute } from '@tanstack/react-router';
import { useMemo } from 'react';

import type { RailCard } from './-lib/rails';
import type { CatalogMovie, CatalogShow } from './-player.types';

import { remoteImage } from './-lib/images';
import {
  continueRails,
  newestFirst,
  movieCard,
  recentAlbumCards,
  recentBookCards,
  recentEpisodeCards,
  recentMovieCards,
  recentYoutubeCards,
} from './-lib/rails';
import { hashSeed, seededShuffle } from './-lib/schedule';
import { genresOf } from './-lib/video';
import {
  albumsQuery,
  booksQuery,
  continueQuery,
  discoverMoreLikeQuery,
  finishProgress,
  podcastSubsQuery,
  removeProgress,
  serverResumeQuery,
  videoCatalogQuery,
} from './-player.api';
import { Billboard, type Slide } from './-ui/billboard';
import { extraCard, ownedByTmdb } from './-ui/detail';
import { CardRail, Empty, SkeletonRail, styles as s } from './-ui/kit';
import { useSections } from './-ui/sections';
import { Top10Rail } from './-ui/top10';

export const Route = createFileRoute('/player/')({
  component: PlayerHome,
});

function PlayerHome() {
  const sections = useSections();
  const queryClient = useQueryClient();
  const progress = useQuery(continueQuery());
  const resume = useQuery({ ...serverResumeQuery(), enabled: sections.video });
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

  const rails = useMemo(
    () => continueRails(progress.data ?? [], resume.data ?? []),
    [progress.data, resume.data],
  );
  const cat = catalog.data;

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

  // the billboard: the newest films and the shows with new episodes, in turn
  const slides = useMemo<Slide[]>(() => {
    if (!cat) return [];
    const films = sections.movies
      ? newestFirst(
          cat.movies.filter((m) => m.has_backdrop),
          (m) => m.added_at,
        ).slice(0, 3)
      : [];
    const showIds: number[] = [];
    if (sections.tv)
      for (const e of cat.recent_episodes)
        if (!showIds.includes(e.show_id)) showIds.push(e.show_id);
    const byId = new Map(cat.shows.map((x) => [x.id, x]));
    const shows = showIds
      .map((id) => byId.get(id))
      .filter((x): x is CatalogShow => !!x && x.has_backdrop)
      .slice(0, 3);
    const out: Slide[] = [];
    for (let i = 0; i < 3; i++) {
      if (shows[i]) out.push({ kind: 'show', item: shows[i], why: 'New episodes' });
      if (films[i]) out.push({ kind: 'movie', item: films[i], why: 'Recently added' });
    }
    return out;
  }, [cat, sections.movies, sections.tv]);

  // a genre you have plenty of, its best, shuffled for the day
  const genreRow = useMemo(() => {
    if (!cat || !sections.movies) return null;
    const genre = genresOf(cat.movies)[0];
    if (!genre) return null;
    const day = new Date().toISOString().slice(0, 10);
    const pool = cat.movies.filter((m) => m.genres.includes(genre) && (m.rating ?? 0) >= 6.5);
    return {
      genre,
      cards: seededShuffle(pool, hashSeed(`home-${genre}-${day}`))
        .slice(0, 20)
        .map((m) => ({ ...movieCard(m), key: `genre:${m.id}` })),
    };
  }, [cat, sections.movies]);

  const continueRows = rails.map((rail) => (
    <CardRail
      key={rail.id}
      title={rail.label}
      cards={rail.cards}
      actionsFor={(card) =>
        card.source
          ? [
              { label: 'Mark as finished', onSelect: () => finish.mutate(card) },
              { label: 'Remove from this row', onSelect: () => remove.mutate(card) },
            ]
          : []
      }
    />
  ));

  return (
    <>
      {slides.length ? <Billboard slides={slides} /> : <h1 className={s.pageTitle}>Home</h1>}

      <div className={slides.length ? s.homeRows : undefined}>
        {continueRows}
        {loading ? <SkeletonRail /> : null}

        <CardRail title="New episodes" cards={recentEpisodes} more={{ to: '/player/tv' }} />
        {cat && sections.movies ? (
          <Top10Rail
            title="Top 10 movies today"
            listKey="trending_movies_today"
            kind="movie"
            owned={cat.movies}
          />
        ) : null}
        <CardRail
          title="Recently added movies"
          cards={recentMovies}
          more={{ to: '/player/movies?tab=library' }}
        />
        {cat ? <MoreLikeRows movies={cat.movies} shows={cat.shows} /> : null}
        {cat && sections.tv ? (
          <Top10Rail
            title="Top 10 shows today"
            listKey="trending_tv_today"
            kind="show"
            owned={cat.shows}
          />
        ) : null}
        <CardRail
          title="Recently added music"
          cards={recentAlbums}
          more={{ to: '/player/music' }}
        />
        <CardRail title="New on YouTube" cards={recentVideos} more={{ to: '/player/youtube' }} />
        {genreRow && genreRow.cards.length >= 6 ? (
          <CardRail
            title={`${genreRow.genre} picks for today`}
            cards={genreRow.cards}
            more={{
              to: `/player/movies?tab=library&genre=${encodeURIComponent(genreRow.genre)}`,
            }}
          />
        ) : null}
        {sections.movies && cat ? (
          <CardRail title="Top rated in your library" cards={topRated} />
        ) : null}
        <CardRail
          title="Recently added audiobooks"
          cards={recentBooks}
          more={{ to: '/player/audiobooks' }}
        />
        <CardRail title="Your podcasts" cards={podcastCards} more={{ to: '/player/podcasts' }} />

        {nothing ? (
          <div className={s.rail}>
            <Empty title="Nothing here yet">
              Your library is empty for this profile, or none of its sections are part of it. Things
              you add in SoulSync show up here.
            </Empty>
          </div>
        ) : null}
      </div>
    </>
  );
}

/** "because you have X": tmdb's picks for a few titles you own, yours first */
function MoreLikeRows({ movies, shows }: { movies: CatalogMovie[]; shows: CatalogShow[] }) {
  const rows = useQuery(discoverMoreLikeQuery());
  const owned = useMemo(() => ownedByTmdb([...movies, ...shows]), [movies, shows]);
  return (
    <>
      {(rows.data ?? []).slice(0, 2).map((row) => {
        const cards = row.items
          .map((t) => extraCard(t, owned))
          .sort((a, b) => Number(!!a.muted) - Number(!!b.muted));
        return cards.length >= 6 ? (
          <CardRail
            key={row.title}
            title={row.title.replace(/^More like /, 'Because you have ')}
            cards={cards.slice(0, 24)}
          />
        ) : null;
      })}
    </>
  );
}
