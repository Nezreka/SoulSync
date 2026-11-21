/**
 * the header search: one box across everything the profile can open.
 *
 * - your library first, ranked by how well it matches (-lib/search.ts:
 *   accents, punctuation and word order don't matter, a typo is forgiven,
 *   "alien 1979" means the 1979 one): movies, shows, artists, albums,
 *   songs, youtube, podcasts, audiobooks, and the things that group them
 *   (genres, studios, networks, franchises, channels)
 * - then people and titles you don't have, from tmdb, each one click from
 *   its page or a request
 * - a top result up front, filters by type, arrows and enter to pick, "/"
 *   from anywhere to start, and the last few searches kept on
 *   this device
 */

import { useQuery } from '@tanstack/react-query';
import { useNavigate, useRouterState } from '@tanstack/react-router';
import { useEffect, useId, useMemo, useRef, useState } from 'react';

import { appURL } from '@/platform/url-base';

import { albumArtwork, libraryThumb, remoteImage, youtubeThumb } from '../-lib/images';
import { bookCover, posterUrl } from '../-lib/rails';
import { parseQuery, rank, scoreHit, type Query } from '../-lib/search';
import { genresOf } from '../-lib/video';
import {
  albumsQuery,
  artistsQuery,
  booksQuery,
  podcastSubsQuery,
  songsQuery,
  tmdbSearchQuery,
  videoCatalogQuery,
  youtubeHomeQuery,
} from '../-player.api';
import { SearchIcon } from './icons';
import { PlayerLink } from './kit';
import s from './player.module.css';
import { useSections } from './sections';

interface Hit {
  key: string;
  title: string;
  sub?: string;
  art?: string | null;
  href: string;
  round?: boolean;
  score: number;
  /** what it is, for the top result ("Movie", "Artist") */
  type: string;
}

interface Group {
  id: string;
  label: string;
  hits: Hit[];
}

const PER_GROUP = 5;
const EXPANDED = 30;
const RECENT_KEY = 'ss-player-recent-searches';

function useDebounced<T>(value: T, ms: number): T {
  const [out, setOut] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setOut(value), ms);
    return () => clearTimeout(id);
  }, [value, ms]);
  return out;
}

function readRecent(): string[] {
  try {
    const raw = JSON.parse(localStorage.getItem(RECENT_KEY) ?? '[]');
    return Array.isArray(raw) ? raw.filter((x) => typeof x === 'string').slice(0, 8) : [];
  } catch {
    return [];
  }
}

function writeRecent(list: string[]) {
  try {
    localStorage.setItem(RECENT_KEY, JSON.stringify(list.slice(0, 8)));
  } catch {
    // private window or blocked storage: recents just aren't kept
  }
}

/** a substring match, for callers that don't rank */
export function matches(text: string | null | undefined, query: string): boolean {
  if (!text) return false;
  return text.toLocaleLowerCase().includes(query);
}

function ranked<T>(
  items: readonly T[],
  q: Query,
  fields: (
    item: T,
  ) => [string | null | undefined, (string | null | undefined)[]?, (number | string | null)?],
  toHit: (item: T, score: number) => Omit<Hit, 'score'>,
  limit = EXPANDED,
): Hit[] {
  const scored = items.map((item) => {
    const [main, others, year] = fields(item);
    return { item, score: scoreHit(q, main, others ?? [], year) };
  });
  return rank(scored, (x) => x.score, limit).map((x) => ({
    ...toHit(x.item, x.score),
    score: x.score,
  }));
}

export function Search() {
  const sections = useSections();
  const [text, setText] = useState('');
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState('all');
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [active, setActive] = useState(0);
  const [recent, setRecent] = useState<string[]>(() => readRecent());
  const raw = useDebounced(text.trim(), 160);
  const q = useMemo(() => parseQuery(raw), [raw]);
  const ref = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const listId = useId();
  const navigate = useNavigate();
  const searching = raw.length >= 2 && q.text.length >= 1;
  const remote = raw.length >= 2 ? raw : '';

  const artists = useQuery({
    ...artistsQuery({ search: q.text, limit: 12 }),
    enabled: searching && sections.music,
  });
  const albums = useQuery({
    ...albumsQuery({ search: q.text, limit: 12, sort: 'title' }),
    enabled: searching && sections.music,
  });
  const songs = useQuery({
    ...songsQuery({ search: q.text, limit: 12 }),
    enabled: searching && sections.music,
  });
  const catalog = useQuery({ ...videoCatalogQuery(), enabled: open && sections.video });
  const youtube = useQuery({ ...youtubeHomeQuery(), enabled: open && sections.video });
  const tmdb = useQuery({ ...tmdbSearchQuery(remote), enabled: searching && sections.video });
  const podcasts = useQuery({ ...podcastSubsQuery(), enabled: open && sections.podcasts });
  const books = useQuery({ ...booksQuery(), enabled: open && sections.audiobooks });

  const genres = useMemo(() => {
    const cat = catalog.data;
    return cat ? { movie: genresOf(cat.movies), show: genresOf(cat.shows) } : null;
  }, [catalog.data]);

  const groups = useMemo<Group[]>(() => {
    if (!searching) return [];
    const out: Group[] = [];
    const push = (id: string, label: string, hits: Hit[]) =>
      hits.length && out.push({ id, label, hits });
    const cat = catalog.data;
    if (cat) {
      if (sections.movies)
        push(
          'movies',
          'Movies',
          ranked(
            cat.movies,
            q,
            (m) => [m.title, [], m.year],
            (m) => ({
              key: `m${m.id}`,
              title: m.title,
              sub: [m.year, m.studio].filter(Boolean).join(' · ') || 'Movie',
              art: m.has_poster ? posterUrl('movie', m.id, 154) : null,
              href: `/player/movies/${m.id}`,
              type: 'Movie',
            }),
          ),
        );
      if (sections.tv)
        push(
          'tv',
          'TV shows',
          ranked(
            cat.shows,
            q,
            (x) => [x.title, [], x.year],
            (x) => ({
              key: `s${x.id}`,
              title: x.title,
              sub: [x.year, x.network].filter(Boolean).join(' · ') || 'Show',
              art: x.has_poster ? posterUrl('show', x.id, 154) : null,
              href: `/player/tv/${x.id}`,
              type: 'TV show',
            }),
          ),
        );
    }
    // people from tmdb: cast and crew, each with a person page
    const people = (tmdb.data ?? []).filter((r) => r.kind === 'person');
    push(
      'people',
      'People',
      ranked(
        people,
        q,
        (p) => [p.title],
        (p) => ({
          key: `p${p.tmdb_id}`,
          title: p.title,
          sub: [p.department === 'Acting' ? 'Actor' : p.department, p.known_for]
            .filter(Boolean)
            .join(' · '),
          art: remoteImage(p.poster),
          href: `/player/person/${p.tmdb_id}`,
          round: true,
          type: 'Person',
        }),
        12,
      ),
    );
    if (sections.music) {
      push(
        'artists',
        'Artists',
        ranked(
          artists.data?.artists ?? [],
          q,
          (a) => [a.name],
          (a) => ({
            key: `ar${a.id}`,
            title: a.name,
            sub: 'Artist',
            art: libraryThumb(a.image_url) ?? remoteImage(a.remote_image_url),
            href: `/player/music/artist/${a.id}`,
            round: true,
            type: 'Artist',
          }),
        ),
      );
      push(
        'albums',
        'Albums',
        ranked(
          albums.data?.albums ?? [],
          q,
          (a) => [a.title, [a.artist_name], a.year],
          (a) => ({
            key: `al${a.id}`,
            title: a.title,
            sub: [a.artist_name, a.year].filter(Boolean).join(' · ') || 'Album',
            art: libraryThumb(a.image_url) ?? remoteImage(a.remote_image_url),
            href: `/player/music/album/${a.id}`,
            type: 'Album',
          }),
        ),
      );
      push(
        'songs',
        'Songs',
        ranked(
          (songs.data?.songs ?? []).filter((x) => x.album_id),
          q,
          (x) => [x.title, [x.artist_name, x.album_title]],
          (x) => ({
            key: `so${x.id}`,
            title: x.title,
            sub: [x.artist_name, x.album_title].filter(Boolean).join(' · ') || 'Song',
            art: albumArtwork(x.album_id),
            href: `/player/music/album/${x.album_id}`,
            type: 'Song',
          }),
        ),
      );
    }
    if (sections.video && youtube.data) {
      const seen = new Set<string>();
      const videos = [...youtube.data.downloads, ...youtube.data.latest].filter(
        (v) => !seen.has(v.id) && seen.add(v.id),
      );
      push(
        'channels',
        'Channels',
        ranked(
          youtube.data.channels,
          q,
          (c) => [c.title, [c.handle]],
          (c) => ({
            key: `yc${c.id}`,
            title: c.title,
            sub: c.followed ? 'Channel you follow' : 'Channel',
            art: remoteImage(c.avatar),
            href: `/player/youtube/${encodeURIComponent(c.id)}`,
            round: true,
            type: 'Channel',
          }),
        ),
      );
      push(
        'youtube',
        'YouTube',
        ranked(
          videos,
          q,
          (v) => [v.title, [v.channel_title]],
          (v) => ({
            key: `y${v.id}`,
            title: v.title,
            sub: [v.channel_title, v.downloaded ? 'Downloaded' : null].filter(Boolean).join(' · '),
            art: youtubeThumb(v.thumbnail, 'card', v.id),
            href: v.channel_id
              ? `/player/youtube/${encodeURIComponent(v.channel_id)}`
              : '/player/youtube',
            type: 'Video',
          }),
        ),
      );
    }
    push(
      'podcasts',
      'Podcasts',
      ranked(
        podcasts.data ?? [],
        q,
        (p) => [p.title, [p.author]],
        (p) => ({
          key: `pc${p.id}`,
          title: p.title,
          sub: p.author ?? 'Podcast',
          art: remoteImage(p.artwork_url),
          href: `/player/podcasts/${p.id}`,
          type: 'Podcast',
        }),
      ),
    );
    push(
      'books',
      'Audiobooks',
      ranked(
        books.data ?? [],
        q,
        (b) => [b.title, [b.author, b.series_title, b.narrator]],
        (b) => ({
          key: `b${b.asin}`,
          title: b.title,
          sub: [b.author, b.series_title].filter(Boolean).join(' · ') || 'Audiobook',
          art: bookCover(b),
          href: `/player/audiobooks/${encodeURIComponent(b.asin)}`,
          type: 'Audiobook',
        }),
      ),
    );
    // the things that group titles: genres, studios, networks, franchises
    if (cat) {
      const cover = (
        list: { id: number; rating: number | null; has_poster: boolean }[],
        kind: 'movie' | 'show',
      ) => {
        const pick = list
          .filter((x) => x.has_poster)
          .sort((a, b) => (b.rating ?? 0) - (a.rating ?? 0))[0];
        return pick ? posterUrl(kind, pick.id, 154) : null;
      };
      const browse: Hit[] = [];
      const add = (list: Hit[]) => browse.push(...list);
      if (genres && sections.movies)
        add(
          ranked(
            genres.movie,
            q,
            (g) => [g],
            (g) => ({
              key: `gm${g}`,
              title: g,
              sub: 'Movie genre',
              art: cover(
                cat.movies.filter((m) => m.genres.includes(g)),
                'movie',
              ),
              href: `/player/movies?tab=library&genre=${encodeURIComponent(g)}`,
              type: 'Genre',
            }),
          ),
        );
      if (genres && sections.tv)
        add(
          ranked(
            genres.show,
            q,
            (g) => [g],
            (g) => ({
              key: `gs${g}`,
              title: g,
              sub: 'TV genre',
              art: cover(
                cat.shows.filter((x) => x.genres.includes(g)),
                'show',
              ),
              href: `/player/tv?tab=library&genre=${encodeURIComponent(g)}`,
              type: 'Genre',
            }),
          ),
        );
      const names = (values: (string | null | undefined)[]) => [
        ...new Set(values.filter((v): v is string => !!v)),
      ];
      if (sections.movies) {
        add(
          ranked(
            names(cat.movies.map((m) => m.studio)),
            q,
            (n) => [n],
            (n) => ({
              key: `st${n}`,
              title: n,
              sub: 'Studio',
              art: cover(
                cat.movies.filter((m) => m.studio === n),
                'movie',
              ),
              href: `/player/movies/studio/${encodeURIComponent(n)}`,
              type: 'Studio',
            }),
          ),
        );
        const franchises = new Map<number, string>();
        for (const m of cat.movies)
          if (m.collection_id && m.collection) franchises.set(m.collection_id, m.collection);
        add(
          ranked(
            [...franchises.entries()],
            q,
            ([, n]) => [n.replace(/ Collection$/, '')],
            ([id, n]) => ({
              key: `fr${id}`,
              title: n.replace(/ Collection$/, ''),
              sub: 'Collection',
              art: cover(
                cat.movies.filter((m) => m.collection_id === id),
                'movie',
              ),
              href: `/player/movies/collection/${id}`,
              type: 'Collection',
            }),
          ),
        );
      }
      if (sections.tv)
        add(
          ranked(
            names(cat.shows.map((x) => x.network)),
            q,
            (n) => [n],
            (n) => ({
              key: `nw${n}`,
              title: n,
              sub: 'Network',
              art: cover(
                cat.shows.filter((x) => x.network === n),
                'show',
              ),
              href: `/player/tv/network/${encodeURIComponent(n)}`,
              type: 'Network',
            }),
          ),
        );
      push(
        'browse',
        'Genres, studios & collections',
        rank(browse, (h) => h.score),
      );
    }
    // titles you don't have: tmdb's, owned ones left to the groups above
    const ownedTmdb = new Set(
      [...(cat?.movies ?? []), ...(cat?.shows ?? [])].map((x) => x.tmdb_id).filter(Boolean),
    );
    push(
      'elsewhere',
      'Not in your library',
      ranked(
        (tmdb.data ?? []).filter(
          (r) =>
            r.kind !== 'person' &&
            !r.library_id &&
            !ownedTmdb.has(r.tmdb_id) &&
            (r.kind === 'movie' ? sections.movies : sections.tv),
        ),
        q,
        (r) => [r.title, [], r.year],
        (r) => ({
          key: `t${r.kind}${r.tmdb_id}`,
          title: r.title,
          sub: [r.kind === 'movie' ? 'Movie' : 'TV show', r.year].filter(Boolean).join(' · '),
          art: remoteImage(r.poster),
          href: `/player/title/${r.kind}/${r.tmdb_id}`,
          type:
            r.kind === 'movie' ? 'Movie · not in your library' : 'TV show · not in your library',
        }),
        12,
      ),
    );
    return out;
  }, [
    searching,
    q,
    sections,
    catalog.data,
    tmdb.data,
    artists.data,
    albums.data,
    songs.data,
    youtube.data,
    podcasts.data,
    books.data,
    genres,
  ]);

  // the single best thing, if it clearly leads: shown big up front
  const top = useMemo(() => {
    const best = groups
      .filter((g) => g.id !== 'elsewhere')
      .flatMap((g) => g.hits.slice(0, 1))
      .sort((a, b) => b.score - a.score)[0];
    return best && best.score >= 65 ? best : null;
  }, [groups]);

  const shown = useMemo(
    () =>
      groups
        .filter((g) => filter === 'all' || g.id === filter)
        .map((g) => ({
          ...g,
          total: g.hits.length,
          hits: g.hits
            .filter((h) => filter !== 'all' || h.key !== top?.key)
            .slice(0, filter !== 'all' || expanded.has(g.id) ? EXPANDED : PER_GROUP),
        }))
        .filter((g) => g.hits.length),
    [groups, filter, expanded, top],
  );
  const flat = useMemo(
    () => [...(filter === 'all' && top ? [top] : []), ...shown.flatMap((g) => g.hits)],
    [shown, top, filter],
  );

  useEffect(() => setActive(0), [raw, filter]);

  // a result opened (by mouse, enter or a remote): the page changed, so the
  // panel goes and the box lets go of focus
  const href = useRouterState({ select: (st) => st.location.href });
  const lastHref = useRef(href);
  useEffect(() => {
    if (href === lastHref.current) return;
    lastHref.current = href;
    setOpen(false);
    if (document.activeElement === input.current) input.current?.blur();
  }, [href]);
  useEffect(() => {
    if (filter !== 'all' && !groups.some((g) => g.id === filter)) setFilter('all');
  }, [groups, filter]);

  const loading =
    searching &&
    (artists.isFetching ||
      albums.isFetching ||
      songs.isFetching ||
      tmdb.isFetching ||
      catalog.isLoading);

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (!ref.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [open]);

  // "/" from anywhere in the player starts a search (ctrl/cmd+k is the help panel's, app wide)
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const typing =
        target &&
        (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable);
      if (event.key === '/' && !typing && !event.ctrlKey && !event.metaKey) {
        event.preventDefault();
        input.current?.focus();
        input.current?.select();
        setOpen(true);
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, []);

  const remember = (query: string) => {
    const next = [query, ...recent.filter((r) => r.toLowerCase() !== query.toLowerCase())].slice(
      0,
      8,
    );
    setRecent(next);
    writeRecent(next);
  };

  const go = (hit: Hit) => {
    remember(raw);
    setOpen(false);
    input.current?.blur();
    void navigate({ to: hit.href as '/player' });
  };

  const hitRow = (hit: Hit, index: number, big = false) => (
    <PlayerLink
      key={`${big ? 'top-' : ''}${hit.key}`}
      id={`${listId}-${index}`}
      href={hit.href}
      className={big ? s.searchTop : s.searchHit}
      data-active={index === active ? 'true' : undefined}
      role="option"
      aria-selected={index === active}
      onMouseEnter={() => setActive(index)}
      onClick={() => {
        remember(raw);
        setOpen(false);
      }}
      data-nav
    >
      {hit.art ? (
        <img
          className={big ? s.searchTopThumb : s.searchThumb}
          data-round={hit.round ? 'true' : undefined}
          src={hit.art.startsWith('/') ? appURL(hit.art) : hit.art}
          alt=""
          loading="lazy"
          // no cover: the empty tile, never a broken image
          onError={(e) => {
            e.currentTarget.style.visibility = 'hidden';
          }}
        />
      ) : (
        <span
          className={big ? s.searchTopThumb : s.searchThumb}
          data-round={hit.round ? 'true' : undefined}
        />
      )}
      <span className={s.searchHitText}>
        {big ? <div className={s.searchTopType}>{hit.type}</div> : null}
        <div className={big ? s.searchTopTitle : s.searchHitTitle}>{hit.title}</div>
        {hit.sub ? <div className={s.searchHitSub}>{hit.sub}</div> : null}
      </span>
    </PlayerLink>
  );

  let index = 0;
  const showRecent = open && !searching && recent.length > 0;

  return (
    <div className={s.search} ref={ref} role="search">
      <span className={s.searchIcon}>
        <SearchIcon />
      </span>
      <input
        ref={input}
        className={s.searchInput}
        type="search"
        placeholder="Search movies, shows, music, people…"
        aria-label="Search"
        role="combobox"
        aria-expanded={open && searching}
        aria-controls={listId}
        aria-activedescendant={open && flat.length ? `${listId}-${active}` : undefined}
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === 'Escape') {
            if (text) setText('');
            else {
              setOpen(false);
              input.current?.blur();
            }
            e.preventDefault();
          } else if (e.key === 'ArrowDown' && flat.length) {
            setActive((a) => Math.min(flat.length - 1, a + 1));
            e.preventDefault();
          } else if (e.key === 'ArrowUp' && flat.length) {
            setActive((a) => Math.max(0, a - 1));
            e.preventDefault();
          } else if (e.key === 'Enter' && flat[active]) {
            go(flat[active]);
            e.preventDefault();
          }
        }}
        data-nav
        data-own-arrows
      />
      {!text ? (
        <kbd className={s.searchKey} aria-hidden="true">
          /
        </kbd>
      ) : null}

      {showRecent ? (
        <div className={s.searchPanel}>
          <div className={s.searchGroupHead}>
            <span className={s.searchGroupLabel}>Recent searches</span>
            <button
              type="button"
              className={s.searchMore}
              onClick={() => {
                setRecent([]);
                writeRecent([]);
              }}
            >
              Clear
            </button>
          </div>
          {recent.map((r) => (
            <button
              key={r}
              type="button"
              className={s.searchRecent}
              onClick={() => {
                setText(r);
                input.current?.focus();
              }}
              data-nav
            >
              <SearchIcon />
              {r}
            </button>
          ))}
        </div>
      ) : null}

      {open && searching ? (
        <div className={s.searchPanel} id={listId} role="listbox" aria-label="Search results">
          {groups.length > 1 ? (
            <div className={s.searchFilters} role="tablist" aria-label="Show">
              {[{ id: 'all', label: 'All' }, ...groups].map((g) => (
                <button
                  key={g.id}
                  type="button"
                  role="tab"
                  aria-selected={filter === g.id}
                  className={s.searchFilter}
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => setFilter(g.id)}
                >
                  {g.label === 'Genres, studios & collections' ? 'Browse' : g.label}
                </button>
              ))}
            </div>
          ) : null}

          {filter === 'all' && top ? (
            <div className={s.searchGroup}>
              <div className={s.searchGroupLabel}>Top result</div>
              {hitRow(top, index++, true)}
            </div>
          ) : null}

          {shown.map((group) => (
            <div key={group.id} className={s.searchGroup}>
              <div className={s.searchGroupHead}>
                <span className={s.searchGroupLabel}>{group.label}</span>
                {filter === 'all' &&
                group.total > PER_GROUP + (group.hits.length < PER_GROUP ? 1 : 0) ? (
                  <button
                    type="button"
                    className={s.searchMore}
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() =>
                      setExpanded((prev) => {
                        const next = new Set(prev);
                        if (next.has(group.id)) next.delete(group.id);
                        else next.add(group.id);
                        return next;
                      })
                    }
                  >
                    {expanded.has(group.id) ? 'Fewer' : `All ${group.total}`}
                  </button>
                ) : null}
              </div>
              {group.hits.map((hit) => hitRow(hit, index++))}
            </div>
          ))}

          {!shown.length && !top ? (
            <div className={s.searchEmpty}>
              {loading ? 'Searching…' : `Nothing matches “${raw}”`}
            </div>
          ) : loading ? (
            <div className={s.searchStatus}>Still looking…</div>
          ) : null}
          <div className={s.searchHint} aria-hidden="true">
            <span>
              <kbd>↑</kbd>
              <kbd>↓</kbd> to move
            </span>
            <span>
              <kbd>enter</kbd> to open
            </span>
            <span>
              <kbd>esc</kbd> to clear
            </span>
          </div>
        </div>
      ) : null}
    </div>
  );
}
