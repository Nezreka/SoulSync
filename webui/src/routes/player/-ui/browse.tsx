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
import { useMemo } from 'react';

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
import { Art, CardRail, Empty, Tabs, styles as s } from './kit';

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
}: {
  title: string;
  items: ExtraTitle[] | undefined;
  owned: Map<number, number>;
  kind: 'movie' | 'show';
}) {
  const cards = (items ?? []).filter((t) => t.kind === kind).map((t) => extraCard(t, owned));
  if (cards.length < 4) return null;
  return <CardRail title={title} cards={cards.slice(0, 30)} />;
}

/** a curated tmdb list as a rail ("popular right now") */
function CuratedRail({
  listKey,
  title,
  owned,
  kind,
}: {
  listKey: string;
  title: string;
  owned: Map<number, number>;
  kind: 'movie' | 'show';
}) {
  const list = useQuery(discoverListQuery(listKey));
  return <TmdbRail title={title} items={list.data} owned={owned} kind={kind} />;
}

export function VideoForYou({
  kind,
  movies,
  shows,
  recentEpisodes,
}: {
  kind: 'movie' | 'show';
  movies: CatalogMovie[];
  shows: CatalogShow[];
  recentEpisodes?: Parameters<typeof recentEpisodeCards>[0];
}) {
  const continuing = useContinueWatching(kind === 'movie' ? ['movie:'] : ['episode:', 'show:']);
  const forYou = useQuery(discoverForYouQuery());
  const moreLike = useQuery(discoverMoreLikeQuery());
  const owned = useMemo(() => ownedByTmdb([...movies, ...shows]), [movies, shows]);
  const items: (CatalogMovie | CatalogShow)[] = kind === 'movie' ? movies : shows;
  const card = (x: CatalogMovie | CatalogShow) =>
    kind === 'movie' ? movieCard(x as CatalogMovie) : showCard(x as CatalogShow);

  const mine = useMemo(() => {
    const byAdded = items
      .slice()
      .sort((a, b) => ((a.added_at ?? '') < (b.added_at ?? '') ? 1 : -1));
    const rated = items
      .filter((x) => (x.rating ?? 0) >= 7.5)
      .sort((a, b) => (b.rating ?? 0) - (a.rating ?? 0));
    // a year or more on the shelf: worth another look
    const cutoff = new Date(Date.now() - 365 * 86_400_000).toISOString();
    const older = items.filter((x) => (x.added_at ?? '') && (x.added_at ?? '') < cutoff);
    const genres = topGenres(items, 3).map((g) => ({
      genre: g,
      cards: todays(
        items.filter((x) => x.genres.includes(g)),
        `${kind}-genre-${g}`,
      )
        .slice(0, 24)
        .map(card),
    }));
    return {
      recent: byAdded.slice(0, 24).map(card),
      rated: rated.slice(0, 24).map((x) => ({ ...card(x), key: `top:${x.id}` })),
      rediscover: todays(older, `${kind}-rediscover`)
        .slice(0, 24)
        .map((x) => ({ ...card(x), key: `again:${x.id}` })),
      genres,
    };
    // card is a pure function of kind
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items, kind]);

  const episodes = kind === 'show' && recentEpisodes ? recentEpisodeCards(recentEpisodes) : [];

  if (!items.length)
    return (
      <div className={s.rail}>
        <Empty title={kind === 'movie' ? 'No movies on disk yet' : 'No shows on disk yet'}>
          What you add in SoulSync shows up here once it's downloaded.
        </Empty>
      </div>
    );

  return (
    <>
      <CardRail title="Continue watching" cards={continuing} />
      {kind === 'show' ? <CardRail title="New episodes" cards={episodes} /> : null}
      <TmdbRail title="Recommended for you" items={forYou.data} owned={owned} kind={kind} />
      <CardRail
        title="Recently added"
        cards={mine.recent}
        more={{ to: `/player/${kind === 'movie' ? 'movies' : 'tv'}?tab=library` }}
      />
      {(moreLike.data ?? []).map((rail) => (
        <TmdbRail
          key={rail.title}
          title={rail.title}
          items={rail.items}
          owned={owned}
          kind={kind}
        />
      ))}
      <CardRail title="Top rated in your library" cards={mine.rated} />
      {mine.genres.map(({ genre, cards }) =>
        cards.length >= 4 ? (
          <CardRail
            key={genre}
            title={`${genre} in your library`}
            cards={cards}
            more={{
              to: `/player/${kind === 'movie' ? 'movies' : 'tv'}?tab=library&genre=${encodeURIComponent(genre)}`,
            }}
          />
        ) : null,
      )}
      {kind === 'movie' ? (
        <>
          <CuratedRail
            listKey="popular_movies"
            title="Popular right now"
            owned={owned}
            kind="movie"
          />
          <CuratedRail listKey="now_playing" title="In theaters" owned={owned} kind="movie" />
          <CuratedRail listKey="upcoming_movies" title="Coming soon" owned={owned} kind="movie" />
          <CuratedRail
            listKey="top_movies"
            title="Top rated of all time"
            owned={owned}
            kind="movie"
          />
        </>
      ) : (
        <>
          <CuratedRail
            listKey="popular_shows"
            title="Popular right now"
            owned={owned}
            kind="show"
          />
          <CuratedRail listKey="airing_today" title="Airing today" owned={owned} kind="show" />
          <CuratedRail
            listKey="on_the_air"
            title="On the air this week"
            owned={owned}
            kind="show"
          />
          <CuratedRail
            listKey="top_shows"
            title="Top rated of all time"
            owned={owned}
            kind="show"
          />
        </>
      )}
      <CardRail title="Rediscover" cards={mine.rediscover} />
    </>
  );
}

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
}: {
  kind: 'movie' | 'show';
  items: (CatalogMovie | CatalogShow)[];
}) {
  const base = kind === 'movie' ? '/player/movies' : '/player/tv';
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
        href: `${base}?tab=library&genre=${encodeURIComponent(g)}`,
        art: coverOf(group, kind, used),
      };
    });
    const decades: Tile[] = [...grouped(items, (x) => decadeOf(x.year)).entries()]
      .sort((a, b) => Number(b[0]) - Number(a[0]))
      .map(([d, group]) => ({
        key: `d:${d}`,
        label: Number(d) >= 2000 ? `${d}s` : `${String(d).slice(2)}s`,
        sub: noun(group.length),
        href: `${base}?tab=library&decade=${d}`,
        art: coverOf(group, kind, used),
      }));
    const makerOf = (x: CatalogMovie | CatalogShow) =>
      kind === 'movie' ? (x as CatalogMovie).studio : (x as CatalogShow).network;
    const makers: Tile[] = [...grouped(items, makerOf).entries()]
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
    return { genres, decades, makers };
    // noun and base follow kind
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items, kind]);

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

/** the shelves an admin built in collection studio, for one kind */
function StudioShelves({
  kind,
  items,
}: {
  kind: 'movie' | 'show';
  items: (CatalogMovie | CatalogShow)[];
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
  if (shelves.isLoading) return <div className={s.skeleton} style={{ height: '30vh' }} />;
  return <TileGrid title="Your collections" tiles={tiles} />;
}

/** tv: the collection studio's shelves; shows have no franchises */
export function ShowCollections({ shows }: { shows: CatalogShow[] }) {
  const shelves = useQuery(studioCollectionsQuery());
  const any = (shelves.data ?? []).some((c) => c.media_type === 'show');
  return (
    <>
      <StudioShelves kind="show" items={shows} />
      {!shelves.isLoading && !any ? (
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
export function MovieCollections({ movies }: { movies: CatalogMovie[] }) {
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
      <StudioShelves kind="movie" items={movies} />
      {tiles.length ? (
        <TileGrid title="Franchises" tiles={tiles} />
      ) : (
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
