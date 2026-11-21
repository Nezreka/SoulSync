/**
 * the header search: one box across everything the profile can open.
 * music asks the library (artists, albums); video, podcasts and books are
 * already loaded for the home page, so they are matched here in the browser.
 */

import { useQuery } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { useEffect, useMemo, useRef, useState } from 'react';

import { appURL } from '@/platform/url-base';

import { bookCover, posterUrl, proxiedImage } from '../-lib/rails';
import {
  albumsQuery,
  artistsQuery,
  booksQuery,
  podcastSubsQuery,
  videoCatalogQuery,
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
}

const PER_GROUP = 6;

function useDebounced<T>(value: T, ms: number): T {
  const [out, setOut] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setOut(value), ms);
    return () => clearTimeout(id);
  }, [value, ms]);
  return out;
}

export function matches(text: string | null | undefined, query: string): boolean {
  if (!text) return false;
  return text.toLocaleLowerCase().includes(query);
}

export function Search() {
  const sections = useSections();
  const [text, setText] = useState('');
  const [open, setOpen] = useState(false);
  const q = useDebounced(text.trim(), 220);
  const needle = q.toLocaleLowerCase();
  const ref = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();
  const searching = q.length >= 2;

  const artists = useQuery({
    ...artistsQuery({ search: q, limit: PER_GROUP }),
    enabled: searching && sections.music,
  });
  const albums = useQuery({
    ...albumsQuery({ search: q, limit: PER_GROUP, sort: 'title' }),
    enabled: searching && sections.music,
  });
  const catalog = useQuery({ ...videoCatalogQuery(), enabled: searching && sections.video });
  const podcasts = useQuery({ ...podcastSubsQuery(), enabled: searching && sections.podcasts });
  const books = useQuery({ ...booksQuery(), enabled: searching && sections.audiobooks });

  const groups = useMemo(() => {
    if (!searching) return [];
    const out: { label: string; hits: Hit[] }[] = [];
    const push = (label: string, hits: Hit[]) =>
      hits.length && out.push({ label, hits: hits.slice(0, PER_GROUP) });
    push(
      'Artists',
      (artists.data?.artists ?? []).map((a) => ({
        key: `ar${a.id}`,
        title: a.name,
        sub: 'Artist',
        art: a.image_url ?? a.remote_image_url,
        href: `/player/music/artist/${a.id}`,
      })),
    );
    push(
      'Albums',
      (albums.data?.albums ?? []).map((a) => ({
        key: `al${a.id}`,
        title: a.title,
        sub: a.artist_name ?? 'Album',
        art: a.image_url ?? a.remote_image_url,
        href: `/player/music/album/${a.id}`,
      })),
    );
    const cat = catalog.data;
    if (cat) {
      push(
        'Movies',
        cat.movies
          .filter((m) => matches(m.title, needle))
          .map((m) => ({
            key: `m${m.id}`,
            title: m.title,
            sub: m.year ? String(m.year) : 'Movie',
            art: m.has_poster ? posterUrl('movie', m.id, 154) : null,
            href: `/player/movies/${m.id}`,
          })),
      );
      push(
        'TV',
        cat.shows
          .filter((sh) => matches(sh.title, needle))
          .map((sh) => ({
            key: `s${sh.id}`,
            title: sh.title,
            sub: sh.network ?? 'Show',
            art: sh.has_poster ? posterUrl('show', sh.id, 154) : null,
            href: `/player/tv/${sh.id}`,
          })),
      );
      push(
        'YouTube',
        cat.youtube
          .filter((v) => matches(v.title, needle) || matches(v.channel_title, needle))
          .map((v) => ({
            key: `y${v.id}`,
            title: v.title,
            sub: v.channel_title ?? 'YouTube',
            art: proxiedImage(v.thumbnail_url),
            href: v.channel_id
              ? `/player/youtube/${encodeURIComponent(v.channel_id)}`
              : '/player/youtube',
          })),
      );
    }
    push(
      'Podcasts',
      (podcasts.data ?? [])
        .filter((p) => matches(p.title, needle) || matches(p.author, needle))
        .map((p) => ({
          key: `p${p.id}`,
          title: p.title,
          sub: p.author ?? 'Podcast',
          art: p.artwork_url,
          href: `/player/podcasts/${p.id}`,
        })),
    );
    push(
      'Audiobooks',
      (books.data ?? [])
        .filter(
          (b) =>
            matches(b.title, needle) ||
            matches(b.author, needle) ||
            matches(b.series_title, needle),
        )
        .map((b) => ({
          key: `b${b.asin}`,
          title: b.title,
          sub: b.author ?? 'Audiobook',
          art: bookCover(b),
          href: `/player/audiobooks/${encodeURIComponent(b.asin)}`,
        })),
    );
    return out;
  }, [searching, needle, artists.data, albums.data, catalog.data, podcasts.data, books.data]);

  const loading =
    searching &&
    (artists.isFetching ||
      albums.isFetching ||
      catalog.isLoading ||
      podcasts.isLoading ||
      books.isLoading);

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (!ref.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [open]);

  const first = groups[0]?.hits[0];

  return (
    <div className={s.search} ref={ref} role="search">
      <span className={s.searchIcon}>
        <SearchIcon />
      </span>
      <input
        className={s.searchInput}
        type="search"
        placeholder="Search your library"
        aria-label="Search your library"
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={(e) => {
          if (e.key === 'Escape') {
            setOpen(false);
            setText('');
          }
          if (e.key === 'Enter' && first) {
            setOpen(false);
            void navigate({ to: first.href as '/player' });
          }
        }}
        data-nav
      />
      {open && searching ? (
        <div className={s.searchPanel}>
          {groups.map((group) => (
            <div key={group.label} className={s.searchGroup}>
              <div className={s.searchGroupLabel}>{group.label}</div>
              {group.hits.map((hit) => (
                <PlayerLink
                  key={hit.key}
                  href={hit.href}
                  className={s.searchHit}
                  onClick={() => setOpen(false)}
                  data-nav
                >
                  {hit.art ? (
                    <img
                      className={s.searchThumb}
                      src={hit.art.startsWith('/') ? appURL(hit.art) : hit.art}
                      alt=""
                      loading="lazy"
                    />
                  ) : (
                    <span className={s.searchThumb} />
                  )}
                  <span className={s.searchHitText}>
                    <div className={s.searchHitTitle}>{hit.title}</div>
                    {hit.sub ? <div className={s.searchHitSub}>{hit.sub}</div> : null}
                  </span>
                </PlayerLink>
              ))}
            </div>
          ))}
          {!groups.length ? (
            <div className={s.searchEmpty}>
              {loading ? 'Searching…' : `Nothing in your library matches “${q}”`}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
