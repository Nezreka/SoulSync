/**
 * the four ways into movies and tv, the way a streaming app has them:
 *
 * - for you: what you're part-way through, then picks (tmdb's, blended from
 *   what you own), your own library cut a few ways, and what's popular out
 *   there. anything you don't own opens a page to ask for it.
 * - library: every title you have (the page's grid).
 * - collections: box sets. movies by franchise, shows by network.
 * - categories: genres, decades, studios or networks, as tiles.
 */

import { useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { useMemo, type ReactNode } from 'react';

import type { RowStyle } from '../-lib/home-layout';
import type { BrowseTab } from '../-lib/video';
import type { CatalogMovie, CatalogShow, ExtraTitle } from '../-player.types';

import { backdropSet, remoteImage, WIDE_SIZES } from '../-lib/images';
import {
  continueRails,
  movieCard,
  recentEpisodeCards,
  showCard,
  type RailCard,
} from '../-lib/rails';
import { dayKey, hashSeed, seededShuffle } from '../-lib/schedule';
import { decadeOf, genresOf } from '../-lib/video';
import {
  continueQuery,
  discoverForYouQuery,
  discoverListQuery,
  discoverMoreLikeQuery,
  serverResumeQuery,
  studioCollectionsQuery,
} from '../-player.api';
import { extraCard, ownedByTmdb } from './detail';
import { Art, Empty, Tabs, styles as s } from './kit';
import { StyledRow, usePageRows } from './page-rows';

export const TAB_OPTIONS: { value: BrowseTab; label: string }[] = [
  { value: 'foryou', label: 'For you' },
  { value: 'library', label: 'Library' },
  { value: 'collections', label: 'Collections' },
  { value: 'categories', label: 'Categories' },
];

export function BrowseTabs({
  value,
  onChange,
  options = TAB_OPTIONS,
}: {
  value: BrowseTab;
  onChange: (tab: BrowseTab) => void;
  options?: { value: BrowseTab; label: string }[];
}) {
  return (
    <div className={s.browseTabs}>
      <Tabs label="Browse" value={value} options={options} onChange={onChange} />
    </div>
  );
}

/** continue watching, narrowed to the keys this page is about */
export function useContinueWatching(prefixes: string[]): RailCard[] {
  const own = useQuery(continueQuery());
  const server = useQuery(serverResumeQuery());
  return useMemo(() => {
    const watching = continueRails(own.data ?? [], server.data ?? []).find(
      (r) => r.id === 'watching',
    );
    return (watching?.cards ?? []).filter((c) => prefixes.some((p) => c.key.startsWith(p)));
    // prefixes is a literal at every call site
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [own.data, server.data]);
}

/** a day-stable shuffle: the same order all day, a new one tomorrow */
function todays<T>(items: readonly T[], salt: string): T[] {
  return seededShuffle(items, hashSeed(`${salt}|${dayKey(new Date())}`));
}

function topGenres(items: readonly { genres: string[] }[], n: number): string[] {
  return genresOf(items).slice(0, n);
}

function TmdbRail({
  title,
  items,
  owned,
  kind,
  style,
}: {
  title: string;
  items: ExtraTitle[] | undefined;
  owned: Map<number, number>;
  kind: 'movie' | 'show';
  style?: RowStyle;
}) {
  const cards = (items ?? []).filter((t) => t.kind === kind).map((t) => extraCard(t, owned));
  if (cards.length < 4) return null;
  return <StyledRow style={style ?? 'standard'} title={title} cards={cards.slice(0, 30)} />;
}

/** a curated tmdb list as a rail ("popular right now") */
function CuratedRail({
  listKey,
  title,
  owned,
  kind,
  style,
}: {
  listKey: string;
  title: string;
  owned: Map<number, number>;
  kind: 'movie' | 'show';
  style?: RowStyle;
}) {
  const list = useQuery(discoverListQuery(listKey));
  return <TmdbRail title={title} items={list.data} owned={owned} kind={kind} style={style} />;
}

/** the rows a page shows before its layout has loaded: the defaults that are on */
const MOVIE_ROWS = [
  'continue',
  'recommended',
  'recent',
  'recommended-add',
  'more-like',
  'top-rated',
  'genres',
  'popular',
  'in-theaters',
  'coming-soon',
  'top-all-time',
  'rediscover',
] as const;
const SHOW_ROWS = [
  'continue',
  'new-episodes',
  'recommended',
  'recent',
  'recommended-add',
  'more-like',
  'top-rated',
  'genres',
  'popular',
  'airing-today',
  'on-the-air',
  'top-all-time',
  'rediscover',
] as const;

/** the tmdb list behind a curated row */
const CURATED: Record<'movie' | 'show', Record<string, string>> = {
  movie: {
    popular: 'popular_movies',
    'in-theaters': 'now_playing',
    'coming-soon': 'upcoming_movies',
    'top-all-time': 'top_movies',
  },
  show: {
    popular: 'popular_shows',
    'airing-today': 'airing_today',
    'on-the-air': 'on_the_air',
    'top-all-time': 'top_shows',
  },
};

const FAMILY = {
  movie: new Set(['G', 'PG']),
  show: new Set(['TV-Y', 'TV-Y7', 'TV-G', 'TV-PG']),
};

/** the rows that are a plain filter of the library: [row id, title, test] */
function shelfRules(kind: 'movie' | 'show', year: number) {
  type Item = CatalogMovie | CatalogShow;
  const has = (x: Item, ...genres: string[]) => genres.some((g) => x.genres.includes(g));
  const minutes = (x: Item) => ((x as CatalogMovie).runtime_seconds ?? 0) / 60;
  const rules: [string, string, (x: Item) => boolean][] = [
    ['acclaimed', 'Critically acclaimed', (x) => (x.rating ?? 0) >= 8],
    ['family', 'Family night', (x) => FAMILY[kind].has(x.content_rating ?? '')],
    [
      'classics',
      'Timeless classics',
      (x) => !!x.year && x.year <= year - (kind === 'movie' ? 30 : 20),
    ],
  ];
  if (kind === 'movie')
    rules.push(
      ['new-releases', 'New releases', (x) => !!x.year && x.year >= year - 1],
      ['thrills', 'Edge of your seat', (x) => has(x, 'Thriller', 'Horror', 'Action')],
      ['feel-good', 'Feel-good', (x) => has(x, 'Comedy', 'Romance', 'Family')],
      ['quick-watches', 'Quick watches', (x) => minutes(x) > 0 && minutes(x) < 100],
      ['epics', 'Epic cinema', (x) => minutes(x) >= 150],
    );
  else
    rules.push(
      ['new-series', 'New series', (x) => !!x.year && x.year >= year - 1],
      ['comfort', 'Comfort and comedy', (x) => has(x, 'Comedy', 'Family', 'Kids')],
      [
        'drama-scifi',
        'Drama and sci-fi',
        (x) => has(x, 'Drama', 'Sci-Fi & Fantasy', 'Science Fiction', 'Fantasy'),
      ],
    );
  return rules;
}

export function VideoForYou({
  kind,
  movies,
  shows,
  recentEpisodes,
  library,
}: {
  kind: 'movie' | 'show';
  movies: CatalogMovie[];
  shows: CatalogShow[];
  recentEpisodes?: Parameters<typeof recentEpisodeCards>[0];
  /** one library's own page: its rows are kept apart, and its links stay in it */
  library?: { id: number; name: string };
}) {
  const items: (CatalogMovie | CatalogShow)[] = kind === 'movie' ? movies : shows;
  const base = kind === 'movie' ? '/player/movies' : '/player/tv';
  // carried on every link back into this page's library tab
  const inLibrary = library ? `&library=${library.id}` : '';
  const { rows, editor, customize } = usePageRows({
    page: `${kind === 'movie' ? 'movies' : 'tv'}${library ? `:lib:${library.id}` : ''}`,
    name: library?.name ?? (kind === 'movie' ? 'Movies' : 'TV Shows'),
    fallback: kind === 'movie' ? MOVIE_ROWS : SHOW_ROWS,
    movieGenres: kind === 'movie' ? genresOf(movies) : [],
    showGenres: kind === 'show' ? genresOf(shows) : [],
  });
  const on = useMemo(() => new Set(rows.map((r) => r.id)), [rows]);

  const continuing = useContinueWatching(kind === 'movie' ? ['movie:'] : ['episode:', 'show:']);
  const forYou = useQuery({
    ...discoverForYouQuery(),
    enabled: on.has('recommended') || on.has('recommended-add'),
  });
  const moreLike = useQuery({ ...discoverMoreLikeQuery(), enabled: on.has('more-like') });
  const owned = useMemo(() => ownedByTmdb([...movies, ...shows]), [movies, shows]);
  const itemById = useMemo(() => new Map(items.map((x) => [x.id, x])), [items]);
  const card = (x: CatalogMovie | CatalogShow) =>
    kind === 'movie' ? movieCard(x as CatalogMovie) : showCard(x as CatalogShow);

  const recommended = useMemo(() => {
    const matching = (forYou.data ?? []).filter((t) => t.kind === kind);
    const inLibrary: RailCard[] = [];
    const toAdd: RailCard[] = [];
    const seenOwned = new Set<number>();
    const seenAdd = new Set<number>();
    for (const t of matching) {
      const libId = t.library_id ?? owned.get(t.tmdb_id);
      if (libId) {
        if (seenOwned.has(libId)) continue;
        seenOwned.add(libId);
        const local = itemById.get(libId);
        inLibrary.push(local ? card(local) : extraCard(t, owned));
      } else {
        if (seenAdd.has(t.tmdb_id)) continue;
        seenAdd.add(t.tmdb_id);
        toAdd.push(extraCard(t, owned));
      }
    }
    return { inLibrary: inLibrary.slice(0, 30), toAdd: toAdd.slice(0, 30) };
    // card is a pure function of kind
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [forYou.data, kind, owned, itemById]);

  /** every row that is built from the library alone: id -> the rails it draws */
  const mine = useMemo(() => {
    type Shelf = { title: string; cards: RailCard[]; more?: { to: string } };
    const out: Record<string, Shelf[]> = {};
    const cards = (id: string, list: (CatalogMovie | CatalogShow)[]) =>
      list.slice(0, 24).map((x) => ({ ...card(x), key: `${id}:${x.id}` }));
    const byAdded = items
      .slice()
      .sort((a, b) => ((a.added_at ?? '') < (b.added_at ?? '') ? 1 : -1));
    const byRating = items.slice().sort((a, b) => (b.rating ?? 0) - (a.rating ?? 0));
    out.recent = [
      {
        title: 'Recently added',
        cards: cards('recent', byAdded),
        more: { to: `${base}?tab=library${inLibrary}` },
      },
    ];
    out['top-rated'] = [
      {
        title: 'Top rated in your library',
        cards: cards(
          'top',
          byRating.filter((x) => (x.rating ?? 0) >= 7.5),
        ),
      },
    ];
    out['top-10'] = [
      {
        title: 'Top 10 in your library',
        cards: cards(
          'top10',
          byRating.filter((x) => (x.rating ?? 0) > 0),
        ).slice(0, 10),
      },
    ];
    // a year or more on the shelf: worth another look
    const cutoff = new Date(Date.now() - 365 * 86_400_000).toISOString();
    const older = items.filter((x) => (x.added_at ?? '') && (x.added_at ?? '') < cutoff);
    out.rediscover = [
      { title: 'Rediscover', cards: cards('again', todays(older, `${kind}-rediscover`)) },
    ];
    const genreShelf = (g: string): Shelf => ({
      title: `${g} in your library`,
      cards: cards(
        `genre-${g}`,
        todays(
          items.filter((x) => x.genres.includes(g)),
          `${kind}-genre-${g}`,
        ),
      ),
      more: { to: `${base}?tab=library&genre=${encodeURIComponent(g)}${inLibrary}` },
    });
    out.genres = topGenres(items, 3)
      .map(genreShelf)
      .filter((x) => x.cards.length >= 4);
    for (const g of genresOf(items)) out[`genre:${kind}:${g}`] = [genreShelf(g)];

    const year = new Date().getFullYear();
    for (const [id, title, test] of shelfRules(kind, year)) {
      const picked = items.filter(test);
      // the newest first where the row is about being new; a fresh handful a day otherwise
      const ordered =
        id === 'new-releases' || id === 'new-series'
          ? picked.sort(
              (a, b) => (b.year ?? 0) - (a.year ?? 0) || (b.rating ?? 0) - (a.rating ?? 0),
            )
          : id === 'acclaimed'
            ? picked.sort((a, b) => (b.rating ?? 0) - (a.rating ?? 0))
            : todays(picked, `${kind}-${id}`);
      out[id] = [{ title, cards: cards(id, ordered) }];
    }

    if (kind === 'movie') {
      const decades = [...grouped(items, (x) => decadeOf(x.year)).entries()]
        .sort((a, b) => b[1].length - a[1].length)
        .slice(0, 3)
        .sort((a, b) => Number(b[0]) - Number(a[0]));
      out.decades = decades.map(([d, group]) => ({
        title: `From the ${Number(d) >= 2000 ? d : String(d).slice(2)}s`,
        cards: cards(`decade-${d}`, todays(group, `movie-decade-${d}`)),
        more: { to: `${base}?tab=library&decade=${d}${inLibrary}` },
      }));
    } else {
      const networks = [...grouped(shows, (x) => x.network).entries()].sort(
        (a, b) => b[1].length - a[1].length,
      );
      const top = networks[0];
      out['top-network'] =
        top && top[1].length >= 4
          ? [
              {
                title: `Best of ${top[0]}`,
                cards: cards(
                  'network',
                  top[1].slice().sort((a, b) => (b.rating ?? 0) - (a.rating ?? 0)),
                ),
                more: { to: `${base}/network/${encodeURIComponent(String(top[0]))}` },
              },
            ]
          : [];
    }
    return out;
    // card and base follow kind
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items, shows, kind, inLibrary]);

  const episodes = kind === 'show' && recentEpisodes ? recentEpisodeCards(recentEpisodes) : [];

  if (!items.length)
    return (
      <div className={s.rail}>
        <Empty title={kind === 'movie' ? 'No movies on disk yet' : 'No shows on disk yet'}>
          What you add in SoulSync shows up here once it's downloaded.
        </Empty>
      </div>
    );

  const drawn: ReactNode[] = [];
  for (const r of rows) {
    const styled = (title: string, cards: RailCard[], more?: { to: string }, key = r.id) =>
      drawn.push(<StyledRow key={key} style={r.style} title={title} cards={cards} more={more} />);
    if (r.id === 'continue') styled('Continue watching', continuing);
    else if (r.id === 'new-episodes') styled('New episodes', episodes);
    else if (r.id === 'recommended') styled('Recommended for you', recommended.inLibrary);
    else if (r.id === 'recommended-add')
      styled(
        kind === 'movie' ? 'Recommended movies to add' : 'Recommended shows to add',
        recommended.toAdd,
      );
    else if (r.id === 'more-like')
      for (const rail of moreLike.data ?? [])
        drawn.push(
          <TmdbRail
            key={`more:${rail.title}`}
            title={rail.title}
            items={rail.items}
            owned={owned}
            kind={kind}
            style={r.style}
          />,
        );
    else if (CURATED[kind][r.id])
      drawn.push(
        <CuratedRail
          key={r.id}
          listKey={CURATED[kind][r.id]}
          title={CURATED_TITLES[r.id]}
          owned={owned}
          kind={kind}
          style={r.style}
        />,
      );
    else if (r.id === 'collections')
      drawn.push(
        kind === 'movie' ? (
          <MovieCollections key="collections" movies={movies} quiet />
        ) : (
          <ShowCollections key="collections" shows={shows} quiet />
        ),
      );
    else if (r.id === 'studios' || r.id === 'networks')
      drawn.push(<MakerTiles key={r.id} kind={kind} items={items} />);
    else
      for (const [i, shelf] of (mine[r.id] ?? []).entries())
        styled(shelf.title, shelf.cards, shelf.more, `${r.id}-${i}`);
  }

  return (
    <>
      {drawn}
      {customize}
      {editor}
    </>
  );
}

const CURATED_TITLES: Record<string, string> = {
  popular: 'Popular right now',
  'in-theaters': 'In theaters',
  'coming-soon': 'Coming soon',
  'top-all-time': 'Top rated of all time',
  'airing-today': 'Airing today',
  'on-the-air': 'On the air this week',
};

// ── tiles ─────────────────────────────────────────────────────────────

export interface Tile {
  key: string;
  label: string;
  sub?: string;
  href: string;
  art?: { src: string; srcSet: string } | null;
}

function TileGrid({ title, tiles }: { title: string; tiles: Tile[] }) {
  if (!tiles.length) return null;
  return (
    <section className={s.rail} aria-label={title}>
      <div className={s.railHead}>
        <h2 className={s.sectionTitle}>{title}</h2>
      </div>
      <div className={s.tileGrid}>
        {tiles.map((t) => (
          <Link key={t.key} to={t.href as never} className={s.tile} data-nav>
            {t.art ? <Art src={t.art.src} srcSet={t.art.srcSet} sizes={WIDE_SIZES} alt="" /> : null}
            <span className={s.tileShade} aria-hidden="true" />
            <span className={s.tileText}>
              <span className={s.tileLabel}>{t.label}</span>
              {t.sub ? <span className={s.tileSub}>{t.sub}</span> : null}
            </span>
          </Link>
        ))}
      </div>
    </section>
  );
}

/** the best-looking title of a group: highest rated with a backdrop */
function coverOf(
  group: readonly (CatalogMovie | CatalogShow)[],
  kind: 'movie' | 'show',
  used: Set<number>,
) {
  const pick =
    group
      .filter((x) => x.has_backdrop && !used.has(x.id))
      .sort((a, b) => (b.rating ?? 0) - (a.rating ?? 0))[0] ?? group.find((x) => x.has_backdrop);
  if (!pick) return null;
  used.add(pick.id);
  return backdropSet(kind, pick.id);
}

function grouped<T>(items: readonly T[], keyOf: (x: T) => string | number | null | undefined) {
  const out = new Map<string | number, T[]>();
  for (const x of items) {
    const k = keyOf(x);
    if (k === null || k === undefined || k === '') continue;
    const list = out.get(k);
    if (list) list.push(x);
    else out.set(k, [x]);
  }
  return out;
}

export function VideoCategories({
  kind,
  items,
  library,
}: {
  kind: 'movie' | 'show';
  items: (CatalogMovie | CatalogShow)[];
  /** one library's own page: its genre and decade links stay in it */
  library?: number;
}) {
  const base = kind === 'movie' ? '/player/movies' : '/player/tv';
  const inLibrary = library ? `&library=${library}` : '';
  const noun = (n: number) =>
    kind === 'movie' ? `${n} film${n === 1 ? '' : 's'}` : `${n} show${n === 1 ? '' : 's'}`;
  const { genres, decades, makers } = useMemo(() => {
    const used = new Set<number>();
    const genres: Tile[] = genresOf(items).map((g) => {
      const group = items.filter((x) => x.genres.includes(g));
      return {
        key: `g:${g}`,
        label: g,
        sub: noun(group.length),
        href: `${base}?tab=library&genre=${encodeURIComponent(g)}${inLibrary}`,
        art: coverOf(group, kind, used),
      };
    });
    const decades: Tile[] = [...grouped(items, (x) => decadeOf(x.year)).entries()]
      .sort((a, b) => Number(b[0]) - Number(a[0]))
      .map(([d, group]) => ({
        key: `d:${d}`,
        label: Number(d) >= 2000 ? `${d}s` : `${String(d).slice(2)}s`,
        sub: noun(group.length),
        href: `${base}?tab=library&decade=${d}${inLibrary}`,
        art: coverOf(group, kind, used),
      }));
    const makers = makerTiles(items, kind, used);
    return { genres, decades, makers };
    // noun and base follow kind
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items, kind, inLibrary]);

  if (!items.length)
    return (
      <div className={s.rail}>
        <Empty title="Nothing to sort yet">Categories fill in as your library does.</Empty>
      </div>
    );
  return (
    <>
      <TileGrid title="Genres" tiles={genres} />
      <TileGrid title={kind === 'movie' ? 'Studios' : 'Networks'} tiles={makers} />
      <TileGrid title="Decades" tiles={decades} />
    </>
  );
}

/** the studios (movies) or networks (shows) there are two or more titles of, biggest first */
function makerTiles(
  items: (CatalogMovie | CatalogShow)[],
  kind: 'movie' | 'show',
  used: Set<number>,
): Tile[] {
  const base = kind === 'movie' ? '/player/movies' : '/player/tv';
  const noun = (n: number) =>
    kind === 'movie' ? `${n} film${n === 1 ? '' : 's'}` : `${n} show${n === 1 ? '' : 's'}`;
  const makerOf = (x: CatalogMovie | CatalogShow) =>
    kind === 'movie' ? (x as CatalogMovie).studio : (x as CatalogShow).network;
  return [...grouped(items, makerOf).entries()]
    .filter(([, group]) => group.length >= 2)
    .sort((a, b) => b[1].length - a[1].length)
    .slice(0, 24)
    .map(([name, group]) => ({
      key: `m:${name}`,
      label: String(name),
      sub: noun(group.length),
      href: `${base}/${kind === 'movie' ? 'studio' : 'network'}/${encodeURIComponent(String(name))}`,
      art: coverOf(group, kind, used),
    }));
}

function MakerTiles({
  kind,
  items,
}: {
  kind: 'movie' | 'show';
  items: (CatalogMovie | CatalogShow)[];
}) {
  const tiles = useMemo(() => makerTiles(items, kind, new Set()), [items, kind]);
  return <TileGrid title={kind === 'movie' ? 'Studios' : 'Networks'} tiles={tiles} />;
}

/** the shelves an admin built in collection studio, for one kind */
function StudioShelves({
  kind,
  items,
  quiet,
}: {
  kind: 'movie' | 'show';
  items: (CatalogMovie | CatalogShow)[];
  quiet?: boolean;
}) {
  const shelves = useQuery(studioCollectionsQuery());
  const tiles = useMemo(() => {
    const byId = new Map(items.map((x) => [x.id, x]));
    const used = new Set<number>();
    return (shelves.data ?? [])
      .filter((c) => c.media_type === kind)
      .map((c): Tile => {
        const group = c.members.map((id) => byId.get(id)).filter((x) => !!x);
        const art = c.poster ? remoteImage(c.poster, 'hero') : null;
        return {
          key: `s:${c.id}`,
          label: c.name,
          sub: kind === 'movie' ? `${group.length} films` : `${group.length} shows`,
          href: `/player/collections/${c.id}`,
          art: coverOf(group, kind, used) ?? (art ? { src: art, srcSet: '' } : null),
        };
      });
  }, [shelves.data, items, kind]);
  if (shelves.isLoading)
    return quiet ? null : <div className={s.skeleton} style={{ height: '30vh' }} />;
  return <TileGrid title="Your collections" tiles={tiles} />;
}

/** tv: the collection studio's shelves; shows have no franchises */
export function ShowCollections({ shows, quiet }: { shows: CatalogShow[]; quiet?: boolean }) {
  const shelves = useQuery(studioCollectionsQuery());
  const any = (shelves.data ?? []).some((c) => c.media_type === 'show');
  return (
    <>
      <StudioShelves kind="show" items={shows} quiet={quiet} />
      {!quiet && !shelves.isLoading && !any ? (
        <div className={s.rail}>
          <Empty title="No TV collections yet">
            Collections you build in SoulSync's Collection Studio show up here.
          </Empty>
        </div>
      ) : null}
    </>
  );
}

/** movie box sets: every franchise you own two or more films of */
export function MovieCollections({
  movies,
  quiet,
}: {
  movies: CatalogMovie[];
  /** as a row on For you: nothing at all when there is nothing to show */
  quiet?: boolean;
}) {
  const tiles = useMemo(() => {
    const used = new Set<number>();
    return [...grouped(movies, (m) => m.collection_id).entries()]
      .filter(([, group]) => group.length >= 2)
      .sort(
        (a, b) =>
          b[1].length - a[1].length ||
          String(a[1][0].collection).localeCompare(String(b[1][0].collection)),
      )
      .map(([id, group]): Tile => {
        const years = group.map((m) => m.year ?? 0).filter(Boolean);
        return {
          key: `c:${id}`,
          label: (group[0].collection ?? '').replace(/ Collection$/, ''),
          sub: `${group.length} films${years.length ? ` · ${Math.min(...years)}–${Math.max(...years)}` : ''}`,
          href: `/player/movies/collection/${id}`,
          art: coverOf(group, 'movie', used),
        };
      });
  }, [movies]);
  return (
    <>
      <StudioShelves kind="movie" items={movies} quiet={quiet} />
      {tiles.length ? (
        <TileGrid title="Franchises" tiles={tiles} />
      ) : quiet ? null : (
        <div className={s.rail}>
          <Empty title="No franchises yet">
            Films that belong to a series (a trilogy, a franchise) group here once you have two of
            them.
          </Empty>
        </div>
      )}
    </>
  );
}
