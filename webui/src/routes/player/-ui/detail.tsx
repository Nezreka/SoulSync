/**
 * the body of a movie, show or channel page: a wide main column of content
 * (about, extras, cast, collection, more like this, gallery) beside a slim
 * info panel that stays in view. no column ever sits half empty: sections
 * with nothing in them aren't drawn.
 */

import { useEffect, useState, type ReactNode } from 'react';

import type { RailCard } from '../-lib/rails';
import type { CatalogMovie, CatalogShow, ExtraTitle, Extras } from '../-player.types';

import { remoteImage, youtubeThumb } from '../-lib/images';
import { movieCard, showCard } from '../-lib/rails';
import { PlayIcon } from './icons';
import { Art, CardRail, styles as s } from './kit';
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

export function InfoPanel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className={s.infoPanel}>
      <div className={s.pickerLabel}>{title}</div>
      {children}
    </div>
  );
}

export function Keywords({ words }: { words?: string[] }) {
  if (!words?.length) return null;
  return (
    <div className={s.keywords}>
      {words.slice(0, 12).map((w) => (
        <span key={w} className={s.keyword}>
          {w}
        </span>
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
      <div className={s.inlineRail}>
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
      </div>
      {playing ? (
        <VideoDialog videoKey={playing.key} title={playing.name} onClose={() => setPlaying(null)} />
      ) : null}
    </Section>
  );
}

/** the title's stills and posters; one opens full screen, arrows go through them */
export function Gallery({ gallery }: { gallery?: Extras['gallery'] }) {
  const images = gallery?.backdrops ?? [];
  const [open, setOpen] = useState<number | null>(null);

  useEffect(() => {
    if (open === null) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(null);
      else if (event.key === 'ArrowRight')
        setOpen((i) => (i === null ? i : (i + 1) % images.length));
      else if (event.key === 'ArrowLeft')
        setOpen((i) => (i === null ? i : (i - 1 + images.length) % images.length));
      else return;
      event.preventDefault();
      event.stopImmediatePropagation();
    };
    document.addEventListener('keydown', onKey, true);
    return () => document.removeEventListener('keydown', onKey, true);
  }, [open, images.length]);

  if (images.length < 2) return null;
  return (
    <Section title="Gallery">
      <div className={s.inlineRail}>
        {images.map((img, i) => (
          <button
            key={img.thumb}
            type="button"
            className={s.galleryTile}
            onClick={() => setOpen(i)}
            aria-label={`Image ${i + 1} of ${images.length}`}
            data-nav
          >
            <Art src={remoteImage(img.thumb)} alt="" />
          </button>
        ))}
      </div>
      {open !== null
        ? inPlayer(
            <div
              className={s.trailerModal}
              role="dialog"
              aria-modal="true"
              aria-label="Gallery"
              onClick={() => setOpen(null)}
            >
              <img
                className={s.lightboxImg}
                src={remoteImage(images[open].full, 'hero') ?? ''}
                alt={`Image ${open + 1} of ${images.length}`}
              />
              <span className={s.lightboxCount}>
                {open + 1} / {images.length} · arrows to browse, esc to close
              </span>
            </div>,
          )
        : null}
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
    href: libraryId ? `/player/${t.kind === 'movie' ? 'movies' : 'tv'}/${libraryId}` : undefined,
    shape: 'poster',
    flag: libraryId ? undefined : { label: 'Not in library' },
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
          <CardRail title="Fans also watch" cards={elsewhere.map((t) => extraCard(t, owned))} />
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
