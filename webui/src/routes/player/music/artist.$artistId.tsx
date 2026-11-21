import { useQuery } from '@tanstack/react-query';
import { createFileRoute } from '@tanstack/react-router';
import { useState } from 'react';

import type { RailCard } from '../-lib/rails';
import type { LibRelease } from '../-player.types';

import { plainText } from '../-lib/format';
import { albumArtwork, libraryThumb, remoteImage } from '../-lib/images';
import { ownedReleases } from '../-lib/music';
import { artistItems } from '../-lib/play-items';
import { artistQuery, artistQueue, topTracksQuery } from '../-player.api';
import { CardRail, Empty, Hero, PlayButton, styles as s } from '../-ui/kit';
import { LabelLink, MusicGenreLinks } from '../-ui/links';
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
    subtitle: r.year ? String(r.year) : undefined,
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
            {more ? 'Show fewer' : 'Show five more'}
          </button>
        ) : null}
      </div>
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
    </section>
  );
}

function ArtistPage() {
  const sections = useSections();
  const id = Number(Route.useParams().artistId);
  const artist = useQuery({ ...artistQuery(id), enabled: sections.music && Number.isFinite(id) });

  if (!sections.music) return <NotPartOfProfile what="Music" single />;
  if (artist.isLoading)
    return <div className={s.skeleton} style={{ height: '50vh', marginTop: '2rem' }} />;
  const a = artist.data;
  if (!a) return <NotFound what="Artist" />;

  const albums = ownedReleases(a.albums);
  const eps = ownedReleases(a.eps);
  const singles = ownedReleases(a.singles);
  const appears = ownedReleases(a.appears_on);
  const about = plainText(a.about?.bio) || plainText(a.summary);
  const image = a.image_url ?? a.remote_image_url;
  // no photo of the artist: their newest cover sets the scene instead
  const scene =
    image ??
    [...albums, ...eps, ...singles].map((r) => r.image_url ?? r.remote_image_url).find(Boolean);

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
            <MusicGenreLinks genres={(a.genres ?? []).slice(0, 4)} />
            {a.label ? <LabelLink label={a.label} /> : null}
          </>
        }
        overview={about}
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
      <Popular artistId={a.id} artistName={a.name} />
      <CardRail title="Albums" cards={releaseCards(albums)} />
      <CardRail title="EPs" cards={releaseCards(eps)} />
      <CardRail title="Singles" cards={releaseCards(singles)} />
      <CardRail title="Appears on" cards={releaseCards(appears)} />
      {!albums.length && !eps.length && !singles.length && !appears.length ? (
        <div className={s.rail}>
          <Empty title="Nothing by this artist on disk yet">
            Releases show up here once at least one of their tracks is in your library.
          </Empty>
        </div>
      ) : null}
    </>
  );
}
