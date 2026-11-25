/**
 * the body of a movie, show or channel page: a wide main column of content
 * (about, extras, cast, collection, more like this, gallery) beside a slim
 * info panel that stays in view. no column ever sits half empty: sections
 * with nothing in them aren't drawn.
 */

import { useQueries, useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { useEffect, useMemo, useState, type ReactNode } from 'react';

import type { RailCard } from '../-lib/rails';
import type {
  CastMember,
  CatalogMovie,
  CatalogShow,
  ExtraTitle,
  Extras,
  VideoCatalog,
} from '../-player.types';

import { remoteImage, youtubeThumb } from '../-lib/images';
import { movieCard, showCard } from '../-lib/rails';
import { personQuery, studioCollectionsQuery } from '../-player.api';
import { BackIcon, ChevronIcon, PlayIcon } from './icons';
import { Art, CardRail, HScroll, styles as s } from './kit';
import { inPlayer, VideoDialog } from './trailer';

export function DetailLayout({ main, side }: { main: ReactNode; side: ReactNode }) {
  return (
    <div className={s.detailGrid}>
      <div className={s.detailMain}>{main}</div>
      <aside className={s.detailSide} aria-label="Details">
        {side}
      </aside>
    </div>
  );
}

export function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className={s.detailSection} aria-label={title}>
      <h2 className={s.sectionTitle}>{title}</h2>
      {children}
    </section>
  );
}

/** long text folded to a few lines, opened in place */
export function ReadMore({ text, lines = 6 }: { text: string; lines?: number }) {
  const [open, setOpen] = useState(false);
  const long = text.length > 420;
  return (
    <div className={s.readMore}>
      <p
        className={s.readMoreText}
        style={open || !long ? undefined : { WebkitLineClamp: lines }}
        data-clamped={!open && long ? 'true' : undefined}
      >
        {text}
      </p>
      {long ? (
        <button type="button" className={s.moreLink} onClick={() => setOpen((v) => !v)} data-nav>
          {open ? 'Show less' : 'Read more'}
        </button>
      ) : null}
    </div>
  );
}

export function InfoPanel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className={s.infoPanel}>
      <div className={s.pickerLabel}>{title}</div>
      {children}
    </div>
  );
}

/** a title's tmdb keywords; each opens everything tagged with it */
export function Keywords({ words }: { words?: string[] }) {
  if (!words?.length) return null;
  return (
    <div className={s.keywords}>
      {words.slice(0, 14).map((w) => (
        <Link
          key={w}
          to="/player/keyword/$name"
          params={{ name: w }}
          className={s.keyword}
          data-nav
        >
          {w}
        </Link>
      ))}
    </div>
  );
}

/** trailers, teasers, featurettes, clips: each plays in the video dialog */
export function ExtrasVideos({ videos }: { videos?: Extras['videos'] }) {
  const [playing, setPlaying] = useState<{ key: string; name: string } | null>(null);
  if (!videos?.length) return null;
  return (
    <Section title="Trailers & extras">
      <HScroll className={s.inlineRail}>
        {videos.slice(0, 16).map((v) => (
          <button
            key={v.key}
            type="button"
            className={s.videoTile}
            onClick={() => setPlaying({ key: v.key, name: v.name ?? 'Video' })}
            data-nav
          >
            <span className={s.art} data-wide="true">
              <Art src={youtubeThumb(null, 'card', v.key)} alt={v.name ?? ''} />
              <span className={s.videoPlay} aria-hidden="true">
                <PlayIcon />
              </span>
            </span>
            <span className={s.cardTitle}>{v.name}</span>
            {v.type ? <span className={s.cardSub}>{v.type}</span> : null}
          </button>
        ))}
      </HScroll>
      {playing ? (
        <VideoDialog videoKey={playing.key} title={playing.name} onClose={() => setPlaying(null)} />
      ) : null}
    </Section>
  );
}

/**
 * images full screen: arrows (keys or buttons) go through them, escape or a
 * click outside closes. the gallery and person photos both open this.
 */
export function Lightbox({
  images,
  index,
  onIndex,
  onClose,
}: {
  images: { thumb: string; full: string }[];
  index: number;
  onIndex: (index: number) => void;
  onClose: () => void;
}) {
  const go = (step: number) => onIndex((index + step + images.length) % images.length);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
      else if (event.key === 'ArrowRight') go(1);
      else if (event.key === 'ArrowLeft') go(-1);
      else return;
      event.preventDefault();
      event.stopImmediatePropagation();
    };
    document.addEventListener('keydown', onKey, true);
    return () => document.removeEventListener('keydown', onKey, true);
  });
  return inPlayer(
    <div
      className={s.trailerModal}
      role="dialog"
      aria-modal="true"
      aria-label="Images"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <img
        className={s.lightboxImg}
        src={remoteImage(images[index].full, 'hero') ?? ''}
        alt={`Image ${index + 1} of ${images.length}`}
      />
      {images.length > 1 ? (
        <>
          <button
            type="button"
            className={s.lightboxNav}
            data-side="prev"
            onClick={() => go(-1)}
            aria-label="Previous image"
          >
            <BackIcon />
          </button>
          <button
            type="button"
            className={s.lightboxNav}
            data-side="next"
            onClick={() => go(1)}
            aria-label="Next image"
          >
            <ChevronIcon />
          </button>
        </>
      ) : null}
      <span className={s.lightboxCount}>
        {index + 1} / {images.length}
      </span>
      <button type="button" className={s.lightboxClose} onClick={onClose}>
        Close
      </button>
    </div>,
  );
}

/** a row of image tiles that open the lightbox */
export function ImageRow({
  images,
  shape = 'wide',
}: {
  images: { thumb: string; full: string }[];
  shape?: 'wide' | 'portrait';
}) {
  const [open, setOpen] = useState<number | null>(null);
  return (
    <>
      <HScroll className={shape === 'wide' ? s.inlineRail : s.people}>
        {images.map((img, i) => (
          <button
            key={img.thumb}
            type="button"
            className={shape === 'wide' ? s.galleryTile : s.photo}
            onClick={() => setOpen(i)}
            aria-label={`Image ${i + 1} of ${images.length}`}
            data-nav
          >
            <Art src={remoteImage(img.thumb)} alt="" />
          </button>
        ))}
      </HScroll>
      {open !== null ? (
        <Lightbox images={images} index={open} onIndex={setOpen} onClose={() => setOpen(null)} />
      ) : null}
    </>
  );
}

/** the title's stills; one opens full screen */
export function Gallery({ gallery }: { gallery?: Extras['gallery'] }) {
  const images = gallery?.backdrops ?? [];
  if (images.length < 2) return null;
  return (
    <Section title="Gallery">
      <ImageRow images={images} />
    </Section>
  );
}

/** a tmdb title as a card: yours opens its page, the rest say they aren't here */
export function extraCard(t: ExtraTitle, owned: Map<number, number>): RailCard {
  const libraryId = t.library_id ?? owned.get(t.tmdb_id);
  return {
    key: `${t.kind}:${t.tmdb_id}`,
    title: t.title,
    subtitle: t.year ? String(t.year) : undefined,
    art: remoteImage(t.poster),
    // yours opens its page; anything else opens a page to ask for it
    href: libraryId
      ? `/player/${t.kind === 'movie' ? 'movies' : 'tv'}/${libraryId}`
      : `/player/title/${t.kind}/${t.tmdb_id}`,
    shape: 'poster',
    have: libraryId ? 'In your library' : undefined,
    muted: libraryId ? undefined : 'Not in your library',
    preview: libraryId ? { kind: t.kind, id: libraryId } : undefined,
  };
}

/** tmdb id -> library id, from the catalog the player already has */
export function ownedByTmdb(items: readonly (CatalogMovie | CatalogShow)[]): Map<number, number> {
  const map = new Map<number, number>();
  for (const item of items) if (item.tmdb_id) map.set(item.tmdb_id, item.id);
  return map;
}

/**
 * more like this: tmdb's picks you own first, then the rest of its picks,
 * then genre matches from your library to fill the row
 */
export function MoreLikeThis({
  extras,
  owned,
  fallback,
  selfTmdb,
}: {
  extras?: Extras;
  owned: Map<number, number>;
  fallback: (CatalogMovie | CatalogShow)[];
  selfTmdb?: number | null;
}) {
  const picks = [...(extras?.recommendations ?? []), ...(extras?.similar ?? [])].filter(
    (t, i, all) => t.tmdb_id !== selfTmdb && all.findIndex((x) => x.tmdb_id === t.tmdb_id) === i,
  );
  const mine = picks.filter((t) => t.library_id ?? owned.get(t.tmdb_id));
  const seen = new Set(mine.map((t) => t.library_id ?? owned.get(t.tmdb_id)));
  const cards: RailCard[] = [
    ...mine.map((t) => extraCard(t, owned)),
    ...fallback
      .filter((x) => !seen.has(x.id))
      .map((x) => ('episode_count' in x ? showCard(x) : movieCard(x))),
  ].slice(0, 24);
  const elsewhere = picks.filter((t) => !(t.library_id ?? owned.get(t.tmdb_id))).slice(0, 16);
  return (
    <>
      {cards.length ? (
        <div className={s.detailRail}>
          <CardRail title="More like this, in your library" cards={cards} />
        </div>
      ) : null}
      {elsewhere.length ? (
        <div className={s.detailRail}>
          <CardRail
            title={`Similar ${elsewhere.every((t) => t.kind === 'show') ? 'shows' : elsewhere.every((t) => t.kind === 'movie') ? 'movies' : 'titles'} to add`}
            cards={elsewhere.map((t) => extraCard(t, owned))}
          />
        </div>
      ) : null}
    </>
  );
}

export function CollectionRail({
  collection,
  owned,
  selfTmdb,
}: {
  collection?: Extras['collection'];
  owned: Map<number, number>;
  selfTmdb?: number | null;
}) {
  const items = (collection?.items ?? []).filter((t) => t.tmdb_id !== selfTmdb);
  if (!collection?.name || !items.length) return null;
  const have = items.filter((t) => owned.get(t.tmdb_id)).length;
  return (
    <div className={s.detailRail}>
      <CardRail
        title={`${collection.name} · ${have} of ${items.length} more in your library`}
        cards={items
          .slice()
          .sort((a, b) => String(a.year ?? '').localeCompare(String(b.year ?? '')))
          .map((t) => extraCard(t, owned))}
      />
    </div>
  );
}

/**
 * Plex-style discovery rails on movie & TV show pages:
 * 1. Collection Studio collections containing this title
 * 2. More from the director / creator & top-billed cast (other titles in your library)
 * 3. More from the same studio (movies) or network (TV shows)
 */
export function RelatedDiscoveryRails({
  kind,
  selfId,
  cast,
  leadCrew,
  studioOrNetwork,
  catalog,
  franchiseCollectionName,
}: {
  kind: 'movie' | 'show';
  selfId: number;
  cast?: CastMember[];
  leadCrew?: { name: string; tmdb_id?: number | null }[];
  studioOrNetwork?: string | null;
  catalog?: VideoCatalog;
  franchiseCollectionName?: string | null;
}) {
  const studioCols = useQuery({ ...studioCollectionsQuery(), retry: false });

  const movieById = useMemo(() => {
    const m = new Map<number, CatalogMovie>();
    for (const item of catalog?.movies ?? []) m.set(item.id, item);
    return m;
  }, [catalog?.movies]);

  const showById = useMemo(() => {
    const m = new Map<number, CatalogShow>();
    for (const item of catalog?.shows ?? []) m.set(item.id, item);
    return m;
  }, [catalog?.shows]);

  const ownedMoviesByTmdb = useMemo(() => ownedByTmdb(catalog?.movies ?? []), [catalog?.movies]);
  const ownedShowsByTmdb = useMemo(() => ownedByTmdb(catalog?.shows ?? []), [catalog?.shows]);

  // 1. Collection Studio collections that include this title
  const collectionRails = useMemo(() => {
    const skipName = franchiseCollectionName?.trim().toLowerCase() ?? '';
    return (studioCols.data ?? [])
      .filter((col) => {
        if (col.media_type !== kind) return false;
        if (!col.members.includes(selfId)) return false;
        if (skipName && col.name.trim().toLowerCase() === skipName) return false;
        return true;
      })
      .map((col) => {
        const cards: RailCard[] = [];
        for (const memberId of col.members) {
          if (memberId === selfId) continue;
          if (kind === 'movie') {
            const mv = movieById.get(memberId);
            if (mv) cards.push(movieCard(mv));
          } else {
            const sh = showById.get(memberId);
            if (sh) cards.push(showCard(sh));
          }
        }
        return { col, cards: cards.slice(0, 24) };
      })
      .filter((entry) => entry.cards.length > 0);
  }, [studioCols.data, kind, selfId, franchiseCollectionName, movieById, showById]);

  // 2. Pick up to 3 featured people: lead director/creator + top 2 cast members
  const featuredPeople = useMemo(() => {
    const out: { tmdbId: number; name: string; title: string }[] = [];
    const seen = new Set<number>();
    const firstCrew = (leadCrew ?? []).find((c) => c.tmdb_id && c.tmdb_id > 0);
    if (firstCrew?.tmdb_id) {
      seen.add(firstCrew.tmdb_id);
      out.push({
        tmdbId: firstCrew.tmdb_id,
        name: firstCrew.name,
        title: `More from ${firstCrew.name}`,
      });
    }
    for (const member of cast ?? []) {
      if (out.length >= 3) break;
      if (!member.tmdb_id || member.tmdb_id <= 0 || seen.has(member.tmdb_id)) continue;
      seen.add(member.tmdb_id);
      out.push({
        tmdbId: member.tmdb_id,
        name: member.name,
        title: `More with ${member.name}`,
      });
    }
    return out;
  }, [leadCrew, cast]);

  const personQueries = useQueries({
    queries: featuredPeople.map((p) => ({
      ...personQuery(p.tmdbId),
      retry: false,
    })),
  });

  const peopleRails = useMemo(() => {
    return featuredPeople
      .map((person, idx) => {
        const credits = personQueries[idx]?.data?.credits ?? [];
        const seenKeys = new Set<string>();
        const cards: RailCard[] = [];
        for (const credit of credits) {
          const libId =
            credit.library_id ??
            (credit.kind === 'movie'
              ? ownedMoviesByTmdb.get(credit.tmdb_id)
              : ownedShowsByTmdb.get(credit.tmdb_id));
          if (!libId) continue;
          if (credit.kind === kind && libId === selfId) continue;
          const key = `${credit.kind}:${libId}`;
          if (seenKeys.has(key)) continue;
          seenKeys.add(key);
          if (credit.kind === 'movie') {
            const mv = movieById.get(libId);
            cards.push(mv ? movieCard(mv) : extraCard(credit, ownedMoviesByTmdb));
          } else {
            const sh = showById.get(libId);
            cards.push(sh ? showCard(sh) : extraCard(credit, ownedShowsByTmdb));
          }
        }
        return { person, cards: cards.slice(0, 18) };
      })
      .filter((entry) => entry.cards.length > 0);
  }, [
    featuredPeople,
    personQueries,
    ownedMoviesByTmdb,
    ownedShowsByTmdb,
    kind,
    selfId,
    movieById,
    showById,
  ]);

  // 3. Same studio (movies) or network (TV shows)
  const makerRail = useMemo(() => {
    const label = studioOrNetwork?.trim();
    if (!label) return null;
    const needle = label.toLowerCase();
    if (kind === 'movie') {
      const matches = (catalog?.movies ?? [])
        .filter((m) => m.id !== selfId && m.studio?.trim().toLowerCase() === needle)
        .sort((a, b) => (b.rating ?? 0) - (a.rating ?? 0) || (b.year ?? 0) - (a.year ?? 0))
        .slice(0, 18)
        .map((m) => movieCard(m));
      if (matches.length < 2) return null;
      return {
        title: `More from ${label}`,
        cards: matches,
        more: { to: `/player/movies/studio/${encodeURIComponent(label)}`, label: 'See all' },
      };
    }
    const matches = (catalog?.shows ?? [])
      .filter((sh) => sh.id !== selfId && sh.network?.trim().toLowerCase() === needle)
      .sort((a, b) => (b.rating ?? 0) - (a.rating ?? 0) || (b.year ?? 0) - (a.year ?? 0))
      .slice(0, 18)
      .map((sh) => showCard(sh));
    if (matches.length < 2) return null;
    return {
      title: `More on ${label}`,
      cards: matches,
      more: { to: `/player/tv/network/${encodeURIComponent(label)}`, label: 'See all' },
    };
  }, [studioOrNetwork, kind, catalog, selfId]);

  return (
    <>
      {collectionRails.map(({ col, cards }) => (
        <div key={`col-${col.id}`} className={s.detailRail}>
          <CardRail
            title={`In ${col.name}`}
            cards={cards}
            more={{ to: `/player/collections/${col.id}`, label: 'View collection' }}
          />
        </div>
      ))}
      {peopleRails.map(({ person, cards }) => (
        <div key={`person-${person.tmdbId}`} className={s.detailRail}>
          <CardRail
            title={person.title}
            cards={cards}
            more={{ to: `/player/person/${person.tmdbId}`, label: 'Filmography' }}
          />
        </div>
      ))}
      {makerRail ? (
        <div className={s.detailRail}>
          <CardRail title={makerRail.title} cards={makerRail.cards} more={makerRail.more} />
        </div>
      ) : null}
    </>
  );
}
