/**
 * the parts of the old page the manager keeps as they were: the Channels tab
 * (followed YouTube channels), and plain posters for a profile the manager is
 * not for. Both read the page's original /api/video/library.
 */

import { useEffect, useState } from 'react';

import type { Side } from './types';

import { type LegacyItem, fetchLegacyLibrary } from './api';
import { openTitle } from './manager-page';
import s from './video-manager.module.css';

function usePaged(kind: 'movies' | 'shows' | 'channels', search: string) {
  const [items, setItems] = useState<LegacyItem[]>([]);
  const [page, setPage] = useState(1);
  const [pages, setPages] = useState(1);
  const [loading, setLoading] = useState(true);
  useEffect(() => setPage(1), [kind, search]);
  useEffect(() => {
    let live = true;
    setLoading(true);
    fetchLegacyLibrary(kind, search, 'title', page, 60)
      .then((out) => {
        if (!live) return;
        setItems(out.items ?? []);
        setPages(out.pagination?.total_pages ?? 1);
      })
      .catch(() => live && setItems([]))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [kind, search, page]);
  return { items, page, pages, loading, setPage };
}

function Pager({
  page,
  pages,
  setPage,
}: {
  page: number;
  pages: number;
  setPage: (n: number) => void;
}) {
  if (pages <= 1) return null;
  return (
    <nav className={s.pager} aria-label="Pages">
      <button
        type="button"
        className="pgf-btn"
        disabled={page <= 1}
        onClick={() => setPage(page - 1)}
      >
        Previous
      </button>
      <span>
        Page <b>{page}</b> of {pages.toLocaleString()}
      </span>
      <button
        type="button"
        className="pgf-btn"
        disabled={page >= pages}
        onClick={() => setPage(page + 1)}
      >
        Next
      </button>
    </nav>
  );
}

export function LegacyPosters({ side, search }: { side: Side; search: string }) {
  const { items, page, pages, loading, setPage } = usePaged(side, search);
  const kind = side === 'shows' ? 'show' : 'movie';
  if (!loading && !items.length) return <div className={s.empty}>Nothing here yet</div>;
  return (
    <>
      <div className={s.grid}>
        {items.map((it) => (
          <article key={it.id} className={s.card}>
            <a
              className={s.cardArt}
              aria-label={it.title}
              href={`/video-detail/library/${kind}/${it.id}`}
              onClick={(e) => {
                if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
                e.preventDefault();
                openTitle(kind, Number(it.id));
              }}
            >
              {it.has_poster ? (
                <img
                  className={s.posterImg}
                  src={`/api/video/poster/${kind}/${it.id}?w=342`}
                  alt=""
                  loading="lazy"
                />
              ) : (
                <div className={s.posterEmpty} aria-hidden="true">
                  {String(it.title || '?')
                    .slice(0, 1)
                    .toUpperCase()}
                </div>
              )}
            </a>
            <div className={s.cardBody}>
              <h3 className={s.cardTitle} title={it.title}>
                {it.title}
              </h3>
              <p className={s.cardMeta}>{it.year ?? ''}</p>
            </div>
          </article>
        ))}
      </div>
      <Pager page={page} pages={pages} setPage={setPage} />
    </>
  );
}

interface Channel extends LegacyItem {
  poster_url?: string | null;
  owned_count?: number;
  followed?: boolean;
}

export function ChannelsGrid() {
  const { items, page, pages, loading, setPage } = usePaged('channels', '');
  if (!loading && !items.length)
    return (
      <div className={s.empty}>
        <strong>No channels yet</strong>
        <span>Follow a YouTube channel from Search and it shows up here.</span>
      </div>
    );
  return (
    <>
      <div className={s.grid}>
        {(items as Channel[]).map((it) => {
          const art = it.poster_url
            ? ((
                window as unknown as { VideoYoutube?: { img: (u: string) => string } }
              ).VideoYoutube?.img(it.poster_url) ?? it.poster_url)
            : '';
          return (
            <article key={it.id} className={s.card}>
              <a
                className={s.cardArt}
                aria-label={it.title}
                data-shape="square"
                href="#"
                onClick={(e) => {
                  e.preventDefault();
                  document.dispatchEvent(
                    new CustomEvent('soulsync:video-open-detail', {
                      detail: { kind: 'channel', id: String(it.id), source: 'youtube' },
                    }),
                  );
                }}
              >
                {art ? (
                  <img className={s.posterImg} src={art} alt="" loading="lazy" />
                ) : (
                  <div className={s.posterEmpty} aria-hidden="true">
                    {String(it.title || '?')
                      .slice(0, 1)
                      .toUpperCase()}
                  </div>
                )}
              </a>
              <div className={s.cardBody}>
                <h3 className={s.cardTitle} title={it.title}>
                  {it.title}
                </h3>
                <p className={s.cardMeta}>
                  {it.video_count
                    ? `${it.owned_count || 0} of ${it.video_count} downloaded`
                    : `${it.owned_count || 0} downloaded`}
                  {it.followed === false ? ' · not following' : ''}
                </p>
              </div>
            </article>
          );
        })}
      </div>
      <Pager page={page} pages={pages} setPage={setPage} />
    </>
  );
}
