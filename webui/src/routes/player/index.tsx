import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { createFileRoute } from '@tanstack/react-router';
import { useCallback, useMemo, useState, type ReactNode } from 'react';

import type { RailCard } from './-lib/rails';
import type { CatalogMovie, CatalogShow, ExtraTitle } from './-player.types';

import { autoChannels, numberChannels } from './-lib/channels';
import { deviceId } from './-lib/device';
import { guideScheduleOptions, sharedCommercials } from './-lib/guide-schedule';
import { parseGenreRow, type HomeLayout, type RowStyle } from './-lib/home-layout';
import { backdropSet, remoteImage, WIDE_SIZES } from './-lib/images';
import {
  continueRails,
  movieCard,
  newestFirst,
  recentAlbumCards,
  recentBookCards,
  recentEpisodeCards,
  recentMovieCards,
  recentYoutubeCards,
  showCard,
} from './-lib/rails';
import { hashSeed, programsInWindow, seededShuffle } from './-lib/schedule';
import { genresOf } from './-lib/video';
import {
  albumsQuery,
  booksQuery,
  commercialsConfigQuery,
  continueQuery,
  discoverListQuery,
  discoverMoreLikeQuery,
  discoverPlaylistsQuery,
  finishProgress,
  homeLayoutQuery,
  podcastSubsQuery,
  removeProgress,
  resetHomeLayout,
  saveHomeLayout,
  serverResumeQuery,
  studioCollectionsQuery,
  videoCatalogQuery,
} from './-player.api';
import { Billboard, type Slide } from './-ui/billboard';
import { HomeEditor } from './-ui/home-editor';
import { CardRail, Empty, Rail, SkeletonRail, styles as s, type CardAction } from './-ui/kit';
import { DiscoverCard } from './-ui/music/discover-card';
import { useSections } from './-ui/sections';
import { RankedRail } from './-ui/top10';

export const Route = createFileRoute('/player/')({
  validateSearch: (search: Record<string, unknown>): { customize?: boolean } =>
    search.customize === true || search.customize === '1' || search.customize === 1
      ? { customize: true }
      : {},
  component: PlayerHome,
});

const FALLBACK_ROWS = [
  'continue',
  'live-guide',
  'new-episodes',
  'collections',
  'top-movies',
  'recent-movies',
  'discovery-playlists',
  'seasonal',
  'because',
  'recent-music',
];

const day = () => new Date().toISOString().slice(0, 10);

/** a poster card as a wide still: the title's backdrop when it has one */
function toWide(card: RailCard): RailCard {
  if (card.shape === 'wide' || !card.preview) return card;
  const set = backdropSet(card.preview.kind, card.preview.id);
  return { ...card, shape: 'wide', art: set.src, artSet: set.srcSet, artSizes: WIDE_SIZES };
}

function StyledRow({
  style,
  title,
  cards,
  more,
  actionsFor,
}: {
  style: RowStyle;
  title: string;
  cards: RailCard[];
  more?: { to: string; label?: string };
  actionsFor?: (card: RailCard) => CardAction[];
}) {
  if (!cards.length) return null;
  if (style === 'ranked') return <RankedRail title={title} cards={cards} />;
  const shown = style === 'wide' ? cards.map(toWide) : cards;
  const rail = <CardRail title={title} cards={shown} more={more} actionsFor={actionsFor} />;
  return style === 'large' ? <div className={s.rowLarge}>{rail}</div> : rail;
}

/** today's chart from tmdb, narrowed to what you have, as your library's cards */
function useTopToday(
  kind: 'movie' | 'show',
  items: (CatalogMovie | CatalogShow)[] | undefined,
  on: boolean,
) {
  const trending = useQuery({
    ...discoverListQuery(kind === 'movie' ? 'trending_movies_today' : 'trending_tv_today', 1),
    enabled: on && !!items,
  });
  const popular = useQuery({
    ...discoverListQuery(kind === 'movie' ? 'popular_movies' : 'popular_shows', 3),
    enabled: on && !!items,
  });
  return useMemo(() => {
    if (!items) return [];
    const byTmdb = new Map(items.filter((x) => x.tmdb_id).map((x) => [x.tmdb_id!, x]));
    const seen = new Set<number>();
    const out: RailCard[] = [];
    for (const t of [...(trending.data ?? []), ...(popular.data ?? [])]) {
      const mine = t.kind === kind ? byTmdb.get(t.tmdb_id) : undefined;
      if (!mine || seen.has(mine.id)) continue;
      seen.add(mine.id);
      out.push(kind === 'movie' ? movieCard(mine as CatalogMovie) : showCard(mine as CatalogShow));
    }
    return out.slice(0, 10);
  }, [items, trending.data, popular.data, kind]);
}

/** "because you have x": tmdb's picks for a title you own, only the ones you also own */
function useBecause(
  movies: CatalogMovie[] | undefined,
  shows: CatalogShow[] | undefined,
  on: boolean,
) {
  const rows = useQuery({ ...discoverMoreLikeQuery(), enabled: on && !!movies });
  return useMemo(() => {
    const byKey = new Map<string, CatalogMovie | CatalogShow>();
    for (const m of movies ?? []) if (m.tmdb_id) byKey.set(`movie:${m.tmdb_id}`, m);
    for (const x of shows ?? []) if (x.tmdb_id) byKey.set(`show:${x.tmdb_id}`, x);
    const card = (t: ExtraTitle) => {
      const mine = byKey.get(`${t.kind}:${t.tmdb_id}`);
      if (!mine) return null;
      return 'episode_count' in mine ? showCard(mine) : movieCard(mine);
    };
    return (rows.data ?? [])
      .map((row) => ({
        title: row.title.replace(/^More like /, 'Because you have '),
        cards: row.items.map(card).filter((c): c is RailCard => !!c),
      }))
      .filter((row) => row.cards.length >= 4)
      .slice(0, 2);
  }, [rows.data, movies, shows]);
}

function PlayerHome() {
  const sections = useSections();
  const queryClient = useQueryClient();
  const device = useMemo(() => deviceId(), []);
  const progress = useQuery(continueQuery());
  const resume = useQuery({ ...serverResumeQuery(), enabled: sections.video });
  const catalog = useQuery({ ...videoCatalogQuery(), enabled: sections.video });
  // Defer below-the-fold rails until the primary hero/catalog is ready so the
  // Billboard, Continue Watching & New Episodes win 100% of initial bandwidth.
  const secondaryReady = !sections.video || !!catalog.data || catalog.isError;
  const collections = useQuery({
    ...studioCollectionsQuery(),
    enabled: sections.video && secondaryReady,
    retry: false,
  });
  const discoverPlaylists = useQuery({
    ...discoverPlaylistsQuery(),
    enabled: sections.music && secondaryReady,
    retry: false,
  });
  const albums = useQuery({
    ...albumsQuery({ sort: 'added', limit: 20 }),
    enabled: sections.music && secondaryReady,
  });
  const books = useQuery({ ...booksQuery(), enabled: sections.audiobooks && secondaryReady });
  const podcasts = useQuery({
    ...podcastSubsQuery(),
    enabled: sections.podcasts && secondaryReady,
  });
  const saved = useQuery(homeLayoutQuery(device));
  const { customize } = Route.useSearch();
  const [draft, setDraft] = useState<HomeLayout | null>(null);
  const [editing, setEditing] = useState(!!customize);
  // the server's layout (already clean); while it loads, or if it can't be
  // read, every row in the order home builds them
  const layout = useMemo<HomeLayout>(
    () =>
      draft ??
      saved.data?.layout ?? {
        billboard: true,
        rows: FALLBACK_ROWS.map((id) => ({ id, style: 'standard', on: true })),
      },
    [draft, saved.data],
  );
  const isOn = (id: string) => layout.rows.some((r) => r.on && r.id === id);

  const refresh = () => void queryClient.invalidateQueries({ queryKey: ['player', 'continue'] });
  const remove = useMutation({
    mutationFn: (card: RailCard) => removeProgress(card.source!.itemType, card.source!.itemId),
    onSettled: refresh,
  });
  const finish = useMutation({
    mutationFn: (card: RailCard) => finishProgress(card.source!.itemType, card.source!.itemId),
    onSettled: refresh,
  });
  const continueActions = (card: RailCard): CardAction[] =>
    card.source
      ? [
          { label: 'Mark as finished', onSelect: () => finish.mutate(card) },
          { label: 'Remove from this row', onSelect: () => remove.mutate(card) },
        ]
      : [];

  const cat = catalog.data;
  const movies = sections.movies ? cat?.movies : undefined;
  const shows = sections.tv ? cat?.shows : undefined;
  const topMovies = useTopToday('movie', movies, isOn('top-movies'));
  const topShows = useTopToday('show', shows, isOn('top-shows'));
  const because = useBecause(movies, shows, isOn('because'));
  const collectionCards = useMemo<RailCard[]>(() => {
    const movieSet = new Set((movies ?? []).map((m) => m.id));
    const showSet = new Set((shows ?? []).map((x) => x.id));
    const out: RailCard[] = [];
    for (const c of collections.data ?? []) {
      const visible = c.members.filter((id) =>
        c.media_type === 'movie' ? movieSet.has(id) : showSet.has(id),
      );
      if (!visible.length) continue;
      const fallbackPoster = `/api/video/poster/${c.media_type === 'movie' ? 'movie' : 'show'}/${visible[0]}?w=342`;
      const rawPoster =
        c.poster && c.poster.startsWith('/api/video/collections/')
          ? `/api/player/collections/${c.poster.slice('/api/video/collections/'.length)}`
          : c.poster;
      out.push({
        key: `studio-col:${c.id}`,
        title: c.name,
        subtitle: `${visible.length} ${visible.length === 1 ? 'title' : 'titles'}`,
        art: remoteImage(rawPoster ?? c.member_posters?.[0] ?? fallbackPoster),
        href: `/player/collections/${c.id}`,
        shape: 'poster',
        badge: c.pinned ? 'Pinned' : undefined,
      });
    }
    return out;
  }, [collections.data, movies, shows]);
  const discoverCards = useMemo(
    () => (discoverPlaylists.data ?? []).flatMap((sec) => sec.playlists),
    [discoverPlaylists.data],
  );
  const commConfig = useQuery(commercialsConfigQuery());
  const commercials = useMemo(() => sharedCommercials(commConfig.data), [commConfig.data]);
  // Live TV right now: top 8 deterministic Guide channels with what's airing at this exact moment
  const liveGuideCards = useMemo<RailCard[]>(() => {
    if (!cat) return [];
    const now = Date.now();
    const chans = numberChannels(autoChannels(cat, [])).slice(0, 10);
    const out: RailCard[] = [];
    for (const ch of chans) {
      // the guide's own options, commercials included: the same thing is on here as there
      const progs = programsInWindow(ch.id, ch.pool, now - 120_000, now + 3_600_000, {
        ...guideScheduleOptions(now, commercials),
        ordered: ch.ordered,
      });
      const airing = progs.find((p) => p.start <= now && p.end > now) ?? progs[0];
      if (!airing) continue;
      const dur = Math.max(1, airing.end - airing.start);
      const frac = Math.min(1, Math.max(0, (now - airing.start) / dur));
      out.push({
        key: `live-ch:${ch.id}`,
        title: airing.title,
        subtitle: `CH ${ch.number ?? ''} · ${ch.name}`,
        art: airing.art ?? null,
        href: `/player/guide`,
        shape: 'wide',
        badge: 'LIVE',
        progress: frac,
      });
    }
    return out;
  }, [cat, commercials]);

  // Seasonal spotlight: timely holiday/seasonal picks based on the current month
  const seasonalInfo = useMemo(() => {
    const month = new Date().getMonth() + 1; // 1..12
    const theme =
      month === 10
        ? {
            title: 'October Frights · Horror & Thriller',
            genres: ['horror', 'thriller', 'mystery'],
          }
        : month === 11 || month === 12
          ? { title: 'Holiday & Cozy Season', genres: ['family', 'animation', 'comedy', 'fantasy'] }
          : month >= 6 && month <= 8
            ? { title: 'Summer Blockbusters', genres: ['action', 'adventure', 'science fiction'] }
            : month >= 1 && month <= 3
              ? { title: 'Awards & Acclaimed Drama', genres: ['drama', 'history', 'biography'] }
              : {
                  title: 'Spring Escapes · Sci-Fi & Adventure',
                  genres: ['science fiction', 'fantasy', 'adventure'],
                };
    const pool = (movies ?? []).filter(
      (m) => m.genres.some((g) => theme.genres.includes(g.toLowerCase())) && (m.rating ?? 0) >= 6.5,
    );
    const cards = seededShuffle(pool, hashSeed(`seasonal-${day()}`))
      .slice(0, 18)
      .map(movieCard);
    return { title: theme.title, cards };
  }, [movies]);
  const watching = useMemo(
    () => continueRails(progress.data ?? [], resume.data ?? []),
    [progress.data, resume.data],
  );

  // every row home can draw, from the library only
  const rows = useMemo(() => {
    const out: Record<string, { title: string; cards: RailCard[]; more?: { to: string } }[]> = {};
    const one = (id: string, title: string, cards: RailCard[], more?: string) => {
      out[id] = [{ title, cards, more: more ? { to: more } : undefined }];
    };
    out.continue = watching.map((r) => ({ title: r.label, cards: r.cards }));
    one('live-guide', 'On Live TV right now', liveGuideCards, '/player/guide');
    one(
      'new-episodes',
      'New episodes',
      cat && shows ? recentEpisodeCards(cat.recent_episodes) : [],
      '/player/tv',
    );
    one('collections', 'Collections', collectionCards, '/player/movies?tab=collections');
    one('seasonal', seasonalInfo.title, seasonalInfo.cards);
    one('top-movies', 'Top 10 movies today', topMovies);
    one(
      'recent-movies',
      'Recently added movies',
      movies ? recentMovieCards(movies) : [],
      '/player/movies?tab=library',
    );
    out.because = because;
    one('top-shows', 'Top 10 shows today', topShows);
    one(
      'recent-shows',
      'Recently added shows',
      shows
        ? newestFirst(shows, (x) => x.added_at)
            .slice(0, 20)
            .map(showCard)
        : [],
      '/player/tv?tab=library',
    );
    one(
      'recent-music',
      'Recently added music',
      recentAlbumCards(albums.data?.albums ?? []),
      '/player/music',
    );
    one('youtube', 'New on YouTube', cat ? recentYoutubeCards(cat.youtube) : [], '/player/youtube');
    const topGenre = movies ? genresOf(movies)[0] : undefined;
    one(
      'genre-picks',
      topGenre ? `${topGenre} picks for today` : "Today's picks",
      movies && topGenre
        ? seededShuffle(
            movies.filter((m) => m.genres.includes(topGenre) && (m.rating ?? 0) >= 6.5),
            hashSeed(`home-${topGenre}-${day()}`),
          )
            .slice(0, 20)
            .map(movieCard)
        : [],
      topGenre ? `/player/movies?tab=library&genre=${encodeURIComponent(topGenre)}` : undefined,
    );
    one(
      'top-rated',
      'Top rated in your library',
      movies
        ? movies
            .filter((m) => (m.rating ?? 0) > 0)
            .sort((a, b) => (b.rating ?? 0) - (a.rating ?? 0))
            .slice(0, 20)
            .map(movieCard)
        : [],
    );
    const yearAgo = new Date(Date.now() - 365 * 86_400_000).toISOString();
    one(
      'rediscover',
      'Rediscover',
      movies
        ? seededShuffle(
            movies.filter((m) => m.added_at && m.added_at < yearAgo),
            hashSeed(`rediscover-${day()}`),
          )
            .slice(0, 20)
            .map(movieCard)
        : [],
    );
    one(
      'audiobooks',
      'Recently added audiobooks',
      recentBookCards(books.data ?? []),
      '/player/audiobooks',
    );
    one(
      'podcasts',
      'Your podcasts',
      (podcasts.data ?? []).slice(0, 20).map((p) => ({
        key: `podcast:${p.id}`,
        title: p.title,
        subtitle: p.author ?? undefined,
        art: remoteImage(p.artwork_url),
        href: `/player/podcasts/${p.id}`,
        shape: 'square' as const,
      })),
      '/player/podcasts',
    );
    for (const r of layout.rows) {
      const g = parseGenreRow(r.id);
      if (!g) continue;
      const pool: (CatalogMovie | CatalogShow)[] = (g.kind === 'movie' ? movies : shows) ?? [];
      const picked = pool
        .filter((x) => x.genres.includes(g.genre))
        .sort((a, b) => (b.rating ?? 0) - (a.rating ?? 0))
        .slice(0, 24)
        .map((x) => ('episode_count' in x ? showCard(x) : movieCard(x)));
      one(
        r.id,
        `${g.genre} ${g.kind === 'movie' ? 'movies' : 'shows'}`,
        picked,
        `/player/${g.kind === 'movie' ? 'movies' : 'tv'}?tab=library&genre=${encodeURIComponent(g.genre)}`,
      );
    }
    return out;
  }, [
    watching,
    liveGuideCards,
    collectionCards,
    seasonalInfo,
    cat,
    movies,
    shows,
    topMovies,
    topShows,
    because,
    albums.data,
    books.data,
    podcasts.data,
    layout.rows,
  ]);

  // the billboard: the newest films and the shows with new episodes, in turn
  const slides = useMemo<Slide[]>(() => {
    if (!cat) return [];
    const films = movies
      ? newestFirst(
          movies.filter((m) => m.has_backdrop),
          (m) => m.added_at,
        ).slice(0, 3)
      : [];
    const showIds: number[] = [];
    if (shows)
      for (const e of cat.recent_episodes)
        if (!showIds.includes(e.show_id)) showIds.push(e.show_id);
    const byId = new Map((shows ?? []).map((x) => [x.id, x]));
    const fresh = showIds
      .map((id) => byId.get(id))
      .filter((x): x is CatalogShow => !!x && x.has_backdrop)
      .slice(0, 3);
    const out: Slide[] = [];
    for (let i = 0; i < 3; i++) {
      if (fresh[i]) out.push({ kind: 'show', item: fresh[i], why: 'New episodes' });
      if (films[i]) out.push({ kind: 'movie', item: films[i], why: 'Recently added' });
    }
    return out;
  }, [cat, movies, shows]);

  const loading =
    (sections.video && catalog.isLoading && !catalog.data) ||
    (!sections.video && albums.isLoading && !albums.data);
  const drawn: ReactNode[] = [];
  for (const r of layout.rows) {
    if (!r.on || r.id === 'watchlist') continue;
    if (r.id === 'discovery-playlists') {
      if (discoverCards.length > 0) {
        drawn.push(
          <Rail
            key="discovery-playlists"
            title="SoulSync Discovery Playlists"
            more={{ to: '/player/music?tab=playlists', label: `All ${discoverCards.length}` }}
          >
            {discoverCards.slice(0, 24).map((pl) => (
              <DiscoverCard key={pl.key} card={pl} />
            ))}
          </Rail>,
        );
      }
      continue;
    }
    for (const [i, row] of (rows[r.id] ?? []).entries())
      drawn.push(
        <StyledRow
          key={`${r.id}-${i}`}
          style={r.style}
          title={row.title}
          cards={row.cards}
          more={row.more}
          actionsFor={r.id === 'continue' ? continueActions : undefined}
        />,
      );
  }
  const nothing =
    !loading &&
    discoverCards.length === 0 &&
    !Object.values(rows).some((list) => list.some((x) => x.cards.length));
  const billboard = layout.billboard && slides.length > 0;

  const close = useCallback(() => {
    setEditing(false);
    setDraft(null);
  }, []);

  return (
    <>
      {billboard ? <Billboard slides={slides} /> : <h1 className={s.pageTitle}>Home</h1>}

      <div className={billboard ? s.homeRows : undefined}>
        {drawn}
        {loading ? <SkeletonRail /> : null}

        {nothing ? (
          <div className={s.rail}>
            <Empty title="Nothing here yet">
              Your library is empty for this profile, or none of its sections are part of it. Things
              you add in SoulSync show up here.
            </Empty>
          </div>
        ) : null}

        <div className={s.customizeRow}>
          <button
            type="button"
            className={s.btn}
            onClick={() => {
              setDraft(layout);
              setEditing(true);
            }}
            data-nav
          >
            Customize home
          </button>
        </div>
      </div>

      {editing && draft ? (
        <HomeEditor
          layout={draft}
          catalog={saved.data?.catalog}
          scope={saved.data?.scope ?? null}
          movieGenres={movies ? genresOf(movies) : []}
          showGenres={shows ? genresOf(shows) : []}
          onChange={setDraft}
          onSave={async (where) => {
            await saveHomeLayout(
              draft,
              where === 'all' ? '*' : device,
              where === 'all' ? device : undefined,
            );
            await queryClient.invalidateQueries({ queryKey: ['player', 'home-layout'] });
          }}
          onReset={async (what) => {
            await resetHomeLayout(what === 'device' ? device : undefined);
            await queryClient.invalidateQueries({ queryKey: ['player', 'home-layout'] });
            setDraft(null);
            setEditing(false);
          }}
          onClose={close}
        />
      ) : null}
    </>
  );
}
