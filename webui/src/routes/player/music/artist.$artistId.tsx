import { useQuery } from '@tanstack/react-query';
import { createFileRoute, Link } from '@tanstack/react-router';
import { useState } from 'react';

import type { RailCard } from '../-lib/rails';
import type { LibRelease } from '../-player.types';

import { plainText } from '../-lib/format';
import { albumArtwork, libraryThumb, remoteImage } from '../-lib/images';
import { ownedReleases } from '../-lib/music';
import { artistItems } from '../-lib/play-items';
import { artistQuery, artistQueue, topTracksQuery } from '../-player.api';
import { Art, CardRail, Empty, Hero, PlayButton, styles as s } from '../-ui/kit';
import { LabelLink, MusicGenreLinks } from '../-ui/links';
import m from '../-ui/music/music.module.css';
import { TrackList } from '../-ui/music/track-list';
import { NotFound, NotPartOfProfile } from '../-ui/not-part';
import { getEngine, playItems } from '../-ui/playback/use-player';
import { useSections } from '../-ui/sections';

/** everything of theirs on disk, in order or shuffled */
async function playArtist(artistId: number, shuffle: boolean) {
  const items = artistItems(await artistQueue(artistId));
  if (!items.length) return;
  playItems(items, shuffle ? Math.floor(Math.random() * items.length) : 0);
  if (shuffle) getEngine().toggleShuffle();
}

export const Route = createFileRoute('/player/music/artist/$artistId')({
  component: ArtistPage,
});

function releaseCards(releases: LibRelease[]): RailCard[] {
  return releases.map((r) => ({
    key: `rel${r.id}`,
    title: r.title,
    subtitle: [r.year ? String(r.year) : '', r.album_type ?? ''].filter(Boolean).join(' · ') || undefined,
    art: libraryThumb(r.image_url) ?? remoteImage(r.remote_image_url),
    href: `/player/music/album/${r.id}`,
    play: { kind: 'album' as const, id: Number(r.id) },
    shape: 'square',
  }));
}

/** their most played songs you can play: five, and five more on asking */
function Popular({ artistId, artistName }: { artistId: number; artistName: string }) {
  const top = useQuery(topTracksQuery(artistId));
  const [more, setMore] = useState(false);
  const tracks = top.data ?? [];
  if (!tracks.length) return null;
  const queue = tracks.map((t) => ({
    type: 'track' as const,
    id: String(t.track_id),
    title: t.title,
    subtitle: t.artist,
    artwork: albumArtwork(t.album_id),
    href: `/player/music/album/${t.album_id}`,
    duration: t.duration_ms ? t.duration_ms / 1000 : null,
    parentId: String(t.album_id),
    parentTitle: t.album,
  }));
  const shown = more ? tracks : tracks.slice(0, 5);
  return (
    <section className={s.rail} aria-label="Popular">
      <div className={s.railHead}>
        <h2 className={s.sectionTitle}>Popular</h2>
        {tracks.length > 5 ? (
          <button type="button" className={s.railMore} onClick={() => setMore((v) => !v)} data-nav>
            {more ? 'Show fewer' : 'Show more'}
          </button>
        ) : null}
      </div>
      <div className={m.albumTrackCard}>
        <TrackList
          label={`Popular songs by ${artistName}`}
          showArt
          showAlbum
          onPlay={(i) => playItems(queue, i)}
          rows={shown.map((t, i) => ({
            key: String(t.track_id),
            title: t.title,
            number: i + 1,
            trackId: t.track_id,
            artist: t.artist !== artistName ? t.artist : undefined,
            album: t.album,
            albumHref: `/player/music/album/${t.album_id}`,
            art: albumArtwork(t.album_id),
            duration: t.duration_ms ? t.duration_ms / 1000 : null,
            explicit: t.explicit,
            end: t.plays ? `${t.plays.toLocaleString()} ${t.plays === 1 ? 'play' : 'plays'}` : null,
            song: {
              title: t.title,
              artist: t.artist,
              album: t.album,
              duration_ms: t.duration_ms,
              lib2_track_id: t.track_id,
            },
          }))}
        />
      </div>
    </section>
  );
}

type DiscFilter = 'all' | 'albums' | 'eps' | 'singles' | 'appears';

function ArtistPage() {
  const sections = useSections();
  const id = Number(Route.useParams().artistId);
  const artist = useQuery({ ...artistQuery(id), enabled: sections.music && Number.isFinite(id) });
  const [discFilter, setDiscFilter] = useState<DiscFilter>('all');
  const [bioOpen, setBioOpen] = useState(false);

  if (!sections.music) return <NotPartOfProfile what="Music" single />;
  if (artist.isLoading)
    return <div className={s.skeleton} style={{ height: '50vh', marginTop: '2rem' }} />;
  const a = artist.data;
  if (!a) return <NotFound what="Artist" />;

  const albums = ownedReleases(a.albums);
  const eps = ownedReleases(a.eps);
  const singles = ownedReleases(a.singles);
  const appears = ownedReleases(a.appears_on);
  const allOwned = [...albums, ...eps, ...singles];
  const latest = allOwned
    .slice()
    .sort((x, y) => (y.year ?? 0) - (x.year ?? 0))[0];
  const about = plainText(a.about?.bio) || plainText(a.summary);
  const tags = [
    ...(a.about?.tags ?? []),
    ...(a.style ? [a.style] : []),
    ...(a.mood ? [a.mood] : []),
  ].filter((t, i, arr) => t && arr.indexOf(t) === i);
  const image = a.image_url ?? a.remote_image_url;
  const scene =
    image ??
    allOwned.map((r) => r.image_url ?? r.remote_image_url).find(Boolean);

  const discAll: { id: DiscFilter; label: string; count: number }[] = [
    { id: 'all', label: 'All releases', count: allOwned.length + appears.length },
    { id: 'albums', label: 'Albums', count: albums.length },
    { id: 'eps', label: 'EPs', count: eps.length },
    { id: 'singles', label: 'Singles', count: singles.length },
    { id: 'appears', label: 'Appears on', count: appears.length },
  ];
  const discCounts = discAll.filter((x) => x.id === 'all' || x.count > 0);

  return (
    <>
      <Hero
        backdrop={scene}
        poster={image}
        posterShape="square"
        posterRound
        kicker="Artist"
        title={a.name}
        meta={
          <>
            {albums.length ? (
              <span>
                {albums.length} album{albums.length === 1 ? '' : 's'}
              </span>
            ) : null}
            {eps.length + singles.length ? (
              <span>
                {eps.length + singles.length} single{eps.length + singles.length === 1 ? '' : 's'} &amp; EPs
              </span>
            ) : null}
            <MusicGenreLinks genres={(a.genres ?? []).slice(0, 4)} />
            {a.label ? <LabelLink label={a.label} /> : null}
          </>
        }
        actions={
          <>
            <PlayButton onPlay={() => void playArtist(a.id, false)} />
            <PlayButton
              label="Shuffle"
              primary={false}
              onPlay={() => void playArtist(a.id, true)}
            />
          </>
        }
      />

      {/* Latest Release Spotlight (Apple Music / Plexamp signature) */}
      {latest ? (
        <div className={m.latestCard}>
          <Link to="/player/music/album/$albumId" params={{ albumId: String(latest.id) }} className={m.latestArt}>
            <Art
              src={libraryThumb(latest.image_url) ?? remoteImage(latest.remote_image_url)}
              alt={latest.title}
            />
          </Link>
          <div className={m.latestBody}>
            <span className={m.latestKicker}>Latest Release</span>
            <Link
              to="/player/music/album/$albumId"
              params={{ albumId: String(latest.id) }}
              className={m.latestTitle}
              data-nav
            >
              {latest.title}
            </Link>
            <span className={m.latestSub}>
              {[
                latest.year ? String(latest.year) : null,
                latest.album_type ?? 'Album',
                latest.track_count ? `${latest.track_count} tracks` : null,
              ]
                .filter(Boolean)
                .join(' · ')}
            </span>
            <div className={m.latestActions}>
              <Link
                to="/player/music/album/$albumId"
                params={{ albumId: String(latest.id) }}
                className={s.epActionPrimary}
                style={{ textDecoration: 'none' }}
                data-nav
              >
                Open release
              </Link>
            </div>
          </div>
        </div>
      ) : null}

      <Popular artistId={a.id} artistName={a.name} />

      {/* Filterable Discography */}
      {discCounts.length > 2 ? (
        <div className={m.discFilterRow}>
          {discCounts.map((f) => (
            <button
              key={f.id}
              type="button"
              className={s.filterPill}
              aria-pressed={discFilter === f.id}
              onClick={() => setDiscFilter(f.id)}
              data-nav
            >
              {f.label} ({f.count})
            </button>
          ))}
        </div>
      ) : null}

      {discFilter === 'all' || discFilter === 'albums' ? (
        <CardRail title="Albums" cards={releaseCards(albums)} />
      ) : null}
      {discFilter === 'all' || discFilter === 'eps' ? (
        <CardRail title="EPs" cards={releaseCards(eps)} />
      ) : null}
      {discFilter === 'all' || discFilter === 'singles' ? (
        <CardRail title="Singles" cards={releaseCards(singles)} />
      ) : null}
      {discFilter === 'all' || discFilter === 'appears' ? (
        <CardRail title="Appears on" cards={releaseCards(appears)} />
      ) : null}

      {!albums.length && !eps.length && !singles.length && !appears.length ? (
        <div className={s.rail}>
          <Empty title="Nothing by this artist on disk yet">
            Releases show up here once at least one of their tracks is in your library.
          </Empty>
        </div>
      ) : null}

      {/* Editorial About Artist Card */}
      {about || tags.length > 0 ? (
        <section className={m.bioCard} aria-label={`About ${a.name}`}>
          <h2 className={s.sectionTitle}>About {a.name}</h2>
          {about ? (
            <>
              <p
                className={m.bioText}
                style={
                  bioOpen || about.length <= 360
                    ? undefined
                    : {
                        display: '-webkit-box',
                        WebkitLineClamp: 4,
                        WebkitBoxOrient: 'vertical',
                        overflow: 'hidden',
                      }
                }
              >
                {about}
              </p>
              {about.length > 360 ? (
                <button
                  type="button"
                  className={s.railMore}
                  style={{ alignSelf: 'flex-start' }}
                  onClick={() => setBioOpen((v) => !v)}
                  data-nav
                >
                  {bioOpen ? 'Show less' : 'Read full bio'}
                </button>
              ) : null}
            </>
          ) : null}
          {tags.length > 0 ? (
            <div className={m.tagRow}>
              {tags.slice(0, 14).map((tag) => (
                <span key={tag} className={m.tagPill}>
                  {tag}
                </span>
              ))}
            </div>
          ) : null}
        </section>
      ) : null}
    </>
  );
}
