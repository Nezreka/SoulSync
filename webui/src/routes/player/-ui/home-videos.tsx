/**
 * home videos (scrap/home-videos-mockup.html): a library's page with On
 * this day, its folders as stacked albums and a camera-roll timeline by
 * month; an album's page; and the edit sheet (name, date filmed, picture,
 * notes) for whoever may edit.
 *
 * thousands of clips stay quick: the page reads the months and their counts
 * once, and each month reads its clips only as it comes near the screen,
 * a page at a time (content-visibility keeps the far ones from laying out).
 */

import {
  useInfiniteQuery,
  useQuery,
  useQueryClient,
  type InfiniteData,
} from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';

import { refusalText } from '@/app/api-client';
import { appURL } from '@/platform/url-base';

import { clockDuration } from '../-lib/format';
import {
  countLine,
  dayBadge,
  dayLabel,
  fetchHomeVideos,
  filmedHint,
  homeOverviewQuery,
  homeQueue,
  homeVideoQuery,
  homeVideosQuery,
  inputWhen,
  longWhen,
  monthLabel,
  PAGE,
  placeholderHue,
  saveHomeVideo,
  sourcesLine,
  spanLabel,
  thumbSrc,
  yearsAgo,
  type HomeAlbum,
  type HomeEdit,
  type HomeOverview,
  type HomeVideo,
} from '../-lib/home-videos';
import { DownloadButton } from './download-button';
import s from './home-videos.module.css';
import { CamcorderIcon, PencilIcon, PlayIcon } from './icons';
import { Empty, styles as k } from './kit';
import { longPress } from './long-press';
import { notify } from './notice';
import { playItems } from './playback/use-player';
import { openPlayerSetup } from './player-setup';
import { useSections } from './sections';
import { Sheet } from './sheet';

export type HomeView = 'timeline' | 'folders';

// ── a clip ───────────────────────────────────────────────────────────────

function Picture({ video, alt = '' }: { video: HomeVideo; alt?: string }) {
  const src = thumbSrc(video);
  const [broken, setBroken] = useState(false);
  if (src && !broken)
    return (
      <img src={src} alt={alt} loading="lazy" decoding="async" onError={() => setBroken(true)} />
    );
  return (
    <span
      className={s.ph}
      style={{ '--hue': placeholderHue(video.id) } as CSSProperties}
      aria-hidden="true"
    >
      <CamcorderIcon />
    </span>
  );
}

export function ClipCard({
  video,
  onPlay,
  onEdit,
  showDay = true,
  withYear = false,
}: {
  video: HomeVideo;
  onPlay: () => void;
  onEdit?: () => void;
  showDay?: boolean;
  /** an album or a search spans years: the badge says which */
  withYear?: boolean;
}) {
  const dur = clockDuration(video.duration_s);
  const pct =
    video.duration_s && video.progress.position_s > 0 && !video.progress.watched
      ? Math.min(100, (video.progress.position_s / video.duration_s) * 100)
      : 0;
  const hold = longPress(() => onEdit?.());
  return (
    <div className={s.clipWrap}>
      <button
        type="button"
        className={s.clip}
        data-missing={video.missing ? 'true' : undefined}
        aria-label={`${video.title}, ${dayLabel(video.filmed_at)}${dur ? `, ${dur}` : ''}${
          video.missing ? ', file missing' : ''
        }`}
        title={video.missing ? 'The file is missing. It shows again when it’s back.' : video.title}
        onClick={() => {
          if (!video.missing) onPlay();
        }}
        onContextMenu={(e) => {
          if (!onEdit) return;
          e.preventDefault();
          onEdit();
        }}
        {...(onEdit ? hold : {})}
        data-nav
      >
        <Picture video={video} />
        {showDay && video.filmed_at ? (
          <span className={s.badge}>
            {withYear ? dayLabel(video.filmed_at) : dayBadge(video.filmed_at)}
          </span>
        ) : null}
        {video.missing ? <span className={s.badge}>Missing</span> : null}
        <span className={s.clipTitle}>{video.title}</span>
        {dur ? <span className={s.dur}>{dur}</span> : null}
        {pct ? <span className={s.prog} style={{ width: `${pct}%` }} /> : null}
        {video.progress.watched ? (
          <span className={s.watched} aria-hidden="true">
            ✓
          </span>
        ) : null}
      </button>
      {!video.missing ? (
        <div className={s.actions}>
          {onEdit ? (
            <button
              type="button"
              className={s.action}
              aria-label={`Edit ${video.title}`}
              title="Edit"
              onClick={onEdit}
              data-nav
            >
              <PencilIcon />
            </button>
          ) : null}
          <DownloadButton type="home" id={String(video.id)} title={video.title} compact />
        </div>
      ) : null}
    </div>
  );
}

function TileGhosts({ n }: { n: number }) {
  return (
    <>
      {Array.from({ length: Math.min(n, 12) }, (_, i) => (
        <div key={i} className={s.tileGhost} aria-hidden="true" />
      ))}
    </>
  );
}

// ── on this day ──────────────────────────────────────────────────────────

function OnThisDay({
  videos,
  library,
  onEdit,
}: {
  videos: HomeVideo[];
  library: number;
  onEdit?: (id: number) => void;
}) {
  const today = new Date();
  const label = today.toLocaleDateString(undefined, { month: 'long', day: 'numeric' });
  return (
    <section className={s.section} aria-label="On this day">
      <h2 className={s.h2}>
        On this day <small>{label}</small>
      </h2>
      <div className={s.thisDay}>
        {videos.map((v, i) => (
          <button
            key={v.id}
            type="button"
            className={s.memory}
            aria-label={`${v.title}, ${yearsAgo(v.filmed_at, today).toLowerCase()}`}
            onClick={() => playItems(homeQueue(videos, i, library))}
            onContextMenu={(e) => {
              if (!onEdit) return;
              e.preventDefault();
              onEdit(v.id);
            }}
            data-nav
          >
            <Picture video={v} />
            {v.duration_s ? <span className={s.dur}>{clockDuration(v.duration_s)}</span> : null}
            <span className={s.memoryText}>
              <span className={s.years}>{yearsAgo(v.filmed_at, today)}</span>
              <span className={s.memoryTitle}>{v.title}</span>
              <span className={s.memorySub}>
                {[dayLabel(v.filmed_at), v.album].filter(Boolean).join(' · ')}
              </span>
            </span>
          </button>
        ))}
      </div>
    </section>
  );
}

// ── folders as albums ────────────────────────────────────────────────────

function AlbumCard({ album, onOpen }: { album: HomeAlbum; onOpen: () => void }) {
  const covers = album.covers.slice(0, 3);
  // the back cards first: the newest clip is the one on top
  const layers = [covers[2], covers[1], covers[0]];
  const span = spanLabel(album.first, album.last);
  return (
    <button
      type="button"
      className={s.album}
      onClick={onOpen}
      aria-label={`${album.name}, ${album.count} videos`}
      data-nav
    >
      <span className={s.stack} aria-hidden="true">
        {layers.map((id, i) => (
          <span key={i}>
            {id !== undefined ? (
              <img
                src={appURL(`/api/player/home/thumb/${id}`)}
                alt=""
                loading="lazy"
                onError={(e) => {
                  e.currentTarget.style.visibility = 'hidden';
                }}
              />
            ) : null}
          </span>
        ))}
      </span>
      <span className={s.albumName}>{album.name}</span>
      <span className={s.albumSub}>
        {album.count.toLocaleString()} {album.count === 1 ? 'video' : 'videos'}
        {span ? ` · ${span}` : ''}
      </span>
    </button>
  );
}

// ── the timeline ─────────────────────────────────────────────────────────

/** a list of clips read a page at a time once it is near the screen */
function useClipPages(
  args: { library: number; month?: string; album?: string; q?: string },
  total: number,
  on: boolean,
) {
  return useInfiniteQuery({
    queryKey: ['player', 'home', 'pages', args],
    initialPageParam: 0,
    enabled: on && total > 0,
    queryFn: async ({ pageParam }) =>
      await fetchHomeVideos({ ...args, offset: pageParam, limit: PAGE }),
    getNextPageParam: (last, all) => {
      const have = all.reduce((n, p) => n + p.videos.length, 0);
      return have < last.total && last.videos.length ? have : undefined;
    },
    staleTime: 2 * 60_000,
  });
}

function flat(data: InfiniteData<{ videos: HomeVideo[]; total: number }> | undefined) {
  return data?.pages.flatMap((p) => p.videos) ?? [];
}

/** asks for more as the end of a list comes near */
function MoreWhenNear({ onNear, on }: { onNear: () => void; on: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  const near = useRef(onNear);
  near.current = onNear;
  useEffect(() => {
    const el = ref.current;
    if (!el || !on) return;
    if (typeof IntersectionObserver === 'undefined') {
      near.current();
      return;
    }
    const io = new IntersectionObserver(
      (seen) => seen.some((e) => e.isIntersecting) && near.current(),
      {
        rootMargin: '900px 0px',
      },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [on]);
  return <div ref={ref} className={s.loadMore} aria-hidden="true" />;
}

function useNear<T extends HTMLElement>(): [React.RefObject<T | null>, boolean] {
  const ref = useRef<T>(null);
  const [seen, setSeen] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el || seen) return;
    if (typeof IntersectionObserver === 'undefined') {
      setSeen(true);
      return;
    }
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) setSeen(true);
      },
      { rootMargin: '1200px 0px' },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [seen]);
  return [ref, seen];
}

function ClipGrid({
  args,
  total,
  onEdit,
  showDay = true,
}: {
  args: { library: number; month?: string; album?: string; q?: string };
  total: number;
  onEdit?: (id: number) => void;
  showDay?: boolean;
}) {
  const [ref, near] = useNear<HTMLDivElement>();
  const pages = useClipPages(args, total, near);
  const videos = flat(pages.data);
  const left = Math.max(0, total - videos.length);
  return (
    <div ref={ref} className={s.grid}>
      {videos.map((v, i) => (
        <ClipCard
          key={v.id}
          video={v}
          showDay={showDay}
          withYear={!args.month}
          onPlay={() => playItems(homeQueue(videos, i, args.library))}
          onEdit={onEdit ? () => onEdit(v.id) : undefined}
        />
      ))}
      {left ? <TileGhosts n={pages.hasNextPage || !videos.length ? left : 0} /> : null}
      <MoreWhenNear
        on={!!pages.hasNextPage && !pages.isFetchingNextPage}
        onNear={() => void pages.fetchNextPage()}
      />
    </div>
  );
}

function Month({
  month,
  count,
  library,
  onEdit,
}: {
  month: string;
  count: number;
  library: number;
  onEdit?: (id: number) => void;
}) {
  return (
    <section className={s.month} aria-label={monthLabel(month)}>
      <div className={s.mhead}>
        <b>{monthLabel(month)}</b>
        <span>
          {count.toLocaleString()} {count === 1 ? 'video' : 'videos'}
        </span>
      </div>
      <ClipGrid args={{ library, month }} total={count} onEdit={onEdit} />
    </section>
  );
}

// ── the page ─────────────────────────────────────────────────────────────

const FOLDERS_ON_TIMELINE = 6;

export function HomeVideosPage({
  library,
  view = 'timeline',
}: {
  library?: number;
  view?: HomeView;
}) {
  const navigate = useNavigate();
  const sections = useSections();
  const overview = useQuery(homeOverviewQuery(library));
  const [editing, setEditing] = useState<number | null>(null);
  const [q, setQ] = useState('');
  const [query, setQuery] = useState('');
  useEffect(() => {
    const t = window.setTimeout(() => setQuery(q.trim()), 250);
    return () => window.clearTimeout(t);
  }, [q]);

  const o = overview.data;
  const libId = o?.library.id ?? library ?? 0;
  const go = (next: { view?: HomeView; album?: string }) =>
    void navigate({
      to: '/player/home-videos',
      search: { library: libId || undefined, ...next },
    });

  if (overview.isLoading) return <HomeSkeleton />;
  if (overview.isError || !o)
    return (
      <div className={s.page}>
        <h1 className={s.title}>Home videos</h1>
        <Empty title="No home videos here yet">
          {sections.isAdmin ? (
            <>
              Add a folder of clips, or your Plex or Jellyfin home videos library.{' '}
              <button
                type="button"
                className={k.btnPrimary}
                onClick={() => openPlayerSetup(HOME_STEP)}
              >
                Set up home videos
              </button>
            </>
          ) : (
            'Once whoever runs this SoulSync adds some, they show here.'
          )}
        </Empty>
      </div>
    );

  const onEdit = o.can_edit ? (id: number) => setEditing(id) : undefined;
  const from = sourcesLine(o.sources);
  const albums = view === 'folders' ? o.albums : o.albums.slice(0, FOLDERS_ON_TIMELINE);

  return (
    <div className={s.page}>
      <div className={s.top}>
        <div className={s.titles}>
          <h1 className={s.title}>{o.library.name}</h1>
          <p className={s.sub}>{from ? `${countLine(o)} · ${from}` : countLine(o)}</p>
        </div>
        <div className={s.tools}>
          {o.scanning ? <span className={s.scanning}>Reading new videos</span> : null}
          <input
            className={s.search}
            type="search"
            value={q}
            placeholder="Find a video"
            aria-label="Find a home video"
            onChange={(e) => setQ(e.target.value)}
            data-nav
          />
          <div className={s.seg} role="group" aria-label="Show">
            {(['timeline', 'folders'] as const).map((v) => (
              <button
                key={v}
                type="button"
                aria-pressed={view === v}
                onClick={() => go({ view: v === 'timeline' ? undefined : v })}
                data-nav
              >
                {v === 'timeline' ? 'Timeline' : 'Folders'}
              </button>
            ))}
          </div>
        </div>
      </div>

      {o.counts.videos === 0 ? (
        <Empty title={o.scanning ? 'Reading your videos…' : 'Nothing here yet'}>
          {o.scanning
            ? 'They show up here as they are found.'
            : 'Clips added to the folders or the server library show up after the next scan.'}
        </Empty>
      ) : query ? (
        <SearchResults library={libId} q={query} onEdit={onEdit} />
      ) : (
        <>
          {view === 'timeline' && o.on_this_day.length ? (
            <OnThisDay videos={o.on_this_day} library={libId} onEdit={onEdit} />
          ) : null}
          {albums.length > 1 || view === 'folders' ? (
            <section className={s.section} aria-label="Folders">
              <h2 className={s.h2}>
                Folders <small>each folder is an album</small>
                {view === 'timeline' && o.albums.length > FOLDERS_ON_TIMELINE ? (
                  <button type="button" className={s.more} onClick={() => go({ view: 'folders' })}>
                    All {o.albums.length} folders
                  </button>
                ) : null}
              </h2>
              <div className={s.albums} data-capped={view === 'timeline' ? 'true' : undefined}>
                {albums.map((a) => (
                  <AlbumCard key={a.key} album={a} onOpen={() => go({ album: a.key })} />
                ))}
              </div>
            </section>
          ) : null}
          {view === 'timeline' ? (
            <section className={s.section} aria-label="Timeline">
              {o.months.map((m) => (
                <Month
                  key={m.month}
                  month={m.month}
                  count={m.count}
                  library={libId}
                  onEdit={onEdit}
                />
              ))}
            </section>
          ) : null}
        </>
      )}
      {editing !== null ? <HomeEditSheet id={editing} onClose={() => setEditing(null)} /> : null}
    </div>
  );
}

/** the step of player setup that sets home videos up */
export const HOME_STEP = 3;

function SearchResults({
  library,
  q,
  onEdit,
}: {
  library: number;
  q: string;
  onEdit?: (id: number) => void;
}) {
  const first = useQuery(homeVideosQuery({ library, q, limit: 1 }));
  const total = first.data?.total ?? 0;
  if (first.isLoading)
    return (
      <div className={s.grid}>
        <TileGhosts n={6} />
      </div>
    );
  if (!total)
    return <Empty title={`Nothing called “${q}”`}>Names, notes and folders are searched.</Empty>;
  return (
    <section className={s.section} aria-label="Found">
      <h2 className={s.h2}>
        Found <small>{total.toLocaleString()}</small>
      </h2>
      <ClipGrid args={{ library, q }} total={total} onEdit={onEdit} />
    </section>
  );
}

function HomeSkeleton() {
  return (
    <div className={s.page} aria-busy="true">
      <div className={s.top}>
        <div className={s.titles}>
          <h1 className={s.title}>Home videos</h1>
        </div>
      </div>
      <div className={s.grid}>
        <TileGhosts n={12} />
      </div>
    </div>
  );
}

// ── an album ─────────────────────────────────────────────────────────────

export function HomeAlbumPage({ library, album }: { library?: number; album: string }) {
  const navigate = useNavigate();
  const overview = useQuery(homeOverviewQuery(library));
  const [editing, setEditing] = useState<number | null>(null);
  const o: HomeOverview | undefined = overview.data;
  const meta = o?.albums.find((a) => a.key === album);
  const libId = o?.library.id ?? library ?? 0;
  const first = useQuery({
    ...homeVideosQuery({ library: libId, album, limit: PAGE }),
    enabled: !!o,
  });
  if (overview.isLoading) return <HomeSkeleton />;
  if (!o || !meta)
    return (
      <div className={s.page}>
        <h1 className={s.title}>Album not found</h1>
        <Empty title="This folder isn't in your home videos">
          It may have been moved or emptied.
        </Empty>
      </div>
    );
  const onEdit = o.can_edit ? (id: number) => setEditing(id) : undefined;
  const span = spanLabel(meta.first, meta.last);
  const playAll = () => {
    const vids = first.data?.videos ?? [];
    if (vids.length) playItems(homeQueue(vids, 0, libId));
  };
  const cover = meta.covers[0];
  return (
    <div className={s.page}>
      <div className={s.albumHead}>
        <div className={s.albumCover} aria-hidden="true">
          {cover !== undefined ? (
            <img src={appURL(`/api/player/home/thumb/${cover}`)} alt="" />
          ) : null}
        </div>
        <div className={s.titles}>
          <button
            type="button"
            className={s.crumb}
            onClick={() =>
              void navigate({ to: '/player/home-videos', search: { library: libId || undefined } })
            }
            data-nav
          >
            ‹ {o.library.name}
          </button>
          <h1 className={s.title}>{meta.name}</h1>
          <p className={s.sub}>
            {meta.count.toLocaleString()} {meta.count === 1 ? 'video' : 'videos'}
            {span ? ` · ${span}` : ''}
            {meta.path && meta.path.includes('/') ? ` · ${meta.path}` : ''}
          </p>
          <div className={s.headButtons}>
            <button type="button" className={k.btnPrimary} onClick={playAll} data-nav>
              <PlayIcon /> Play all
            </button>
            <DownloadButton type="home_album" id={album} title={meta.name} />
          </div>
        </div>
      </div>
      <ClipGrid args={{ library: libId, album }} total={meta.count} onEdit={onEdit} />
      {editing !== null ? <HomeEditSheet id={editing} onClose={() => setEditing(null)} /> : null}
    </div>
  );
}

// ── the edit sheet ───────────────────────────────────────────────────────

function nearest(frames: { at_s: number }[], at: number | null | undefined): number {
  if (at == null || !frames.length) return -1;
  let best = 0;
  for (let i = 1; i < frames.length; i++)
    if (Math.abs(frames[i].at_s - at) < Math.abs(frames[best].at_s - at)) best = i;
  return Math.abs(frames[best].at_s - at) < 0.6 ? best : -1;
}

export function HomeEditSheet({ id, onClose }: { id: number; onClose: () => void }) {
  const queryClient = useQueryClient();
  const detail = useQuery(homeVideoQuery(id));
  const d = detail.data;
  const [title, setTitle] = useState<string | null>(null);
  const [when, setWhen] = useState<string | null>(null);
  const [frame, setFrame] = useState<number | null>(null);
  const [notes, setNotes] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const startTitle = d?.edited?.title ? d.title : '';
  const startWhen = d?.edited?.filmed_at ? inputWhen(d.filmed_at) : '';
  const picked = useMemo(
    () => (frame !== null ? frame : d ? nearest(d.frames, d.thumb_at_s) : -1),
    [frame, d],
  );

  const save = async () => {
    if (!d) return;
    const changes: HomeEdit = {};
    if (title !== null && title.trim() !== startTitle) changes.title = title.trim() || null;
    if (when !== null && when !== startWhen) changes.filmed_at = when || null;
    if (notes !== null && notes.trim() !== (d.notes ?? '')) changes.notes = notes.trim() || null;
    if (frame !== null && d.frames[frame]) changes.thumb_at_s = d.frames[frame].at_s;
    if (!Object.keys(changes).length) {
      onClose();
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await saveHomeVideo(id, changes);
      await queryClient.invalidateQueries({ queryKey: ['player', 'home'] });
      notify('Saved');
      onClose();
    } catch (e) {
      setError(await refusalText(e, 'Couldn’t save. Try again in a moment.'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Sheet
      title="Edit video"
      onClose={onClose}
      footer={
        <>
          {error ? (
            <span className={s.hint} role="alert">
              {error}
            </span>
          ) : null}
          <button type="button" className={k.btn} onClick={onClose} data-nav>
            Cancel
          </button>
          <button
            type="button"
            className={k.btnPrimary}
            onClick={() => void save()}
            disabled={busy || !d}
            data-nav
          >
            {busy ? 'Saving…' : 'Save'}
          </button>
        </>
      }
    >
      {!d ? (
        <div className={s.form} aria-busy="true">
          <div className={s.tileGhost} />
        </div>
      ) : (
        <div className={s.form}>
          <div className={s.editHead}>
            <div className={s.clip}>
              <Picture video={d} />
              {d.duration_s ? <span className={s.dur}>{clockDuration(d.duration_s)}</span> : null}
            </div>
            <div>
              <div className={s.editTitle}>{d.title}</div>
              <div className={s.editFile}>{[d.album, d.file_name].filter(Boolean).join(' · ')}</div>
            </div>
          </div>

          <div className={s.field}>
            <label className={s.label} htmlFor="home-edit-name">
              Name
              {d.edited?.title ? (
                <button type="button" className={s.reset} onClick={() => setTitle('')}>
                  Use the automatic name
                </button>
              ) : null}
            </label>
            <input
              id="home-edit-name"
              className={s.input}
              value={title ?? startTitle}
              placeholder={d.auto_title ?? d.title}
              maxLength={200}
              onChange={(e) => setTitle(e.target.value)}
              data-nav
            />
            <span className={s.hint}>
              {d.file_name ? `Was “${d.file_name}”. ` : ''}Your name is kept, even after a rescan.
            </span>
          </div>

          <div className={s.field}>
            <label className={s.label} htmlFor="home-edit-when">
              Date filmed
              {d.edited?.filmed_at ? (
                <button type="button" className={s.reset} onClick={() => setWhen('')}>
                  Use the date from the video
                </button>
              ) : null}
            </label>
            <input
              id="home-edit-when"
              className={s.input}
              type="datetime-local"
              value={when ?? (startWhen || inputWhen(d.filmed_at))}
              onChange={(e) => setWhen(e.target.value)}
              data-nav
            />
            <span className={s.hint}>
              {when === ''
                ? `Back to ${longWhen(d.auto_filmed_at) || 'the automatic date'}.`
                : filmedHint(d.filmed_source)}
            </span>
          </div>

          <div className={s.field}>
            <span className={s.label} id="home-edit-picture">
              Picture
            </span>
            <div className={s.frames} role="group" aria-labelledby="home-edit-picture">
              {d.frames.map((f, i) => (
                <button
                  key={f.at_s}
                  type="button"
                  className={s.frame}
                  aria-pressed={picked === i}
                  aria-label={`The frame at ${clockDuration(f.at_s) || `${f.at_s}s`}`}
                  onClick={() => setFrame(i)}
                  data-nav
                >
                  <img src={appURL(f.url)} alt="" loading="lazy" />
                  <span>{clockDuration(f.at_s) || '0:00'}</span>
                </button>
              ))}
            </div>
            <span className={s.hint}>Pick a frame from the video.</span>
          </div>

          <div className={s.field}>
            <label className={s.label} htmlFor="home-edit-notes">
              Notes
            </label>
            <textarea
              id="home-edit-notes"
              className={s.input}
              value={notes ?? d.notes ?? ''}
              placeholder="Who’s in it, where it was…"
              maxLength={4000}
              onChange={(e) => setNotes(e.target.value)}
              data-nav
            />
          </div>
        </div>
      )}
    </Sheet>
  );
}
