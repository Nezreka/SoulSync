import { useQuery } from '@tanstack/react-query';
import { createFileRoute } from '@tanstack/react-router';

import type { LibTrack } from '../-player.types';

import { runtime } from '../-lib/format';
import { fileQuality } from '../-lib/music';
import { albumItems } from '../-lib/play-items';
import { albumQuery } from '../-player.api';
import { Comments } from '../-ui/comments';
import { PlusIcon } from '../-ui/icons';
import { Badge, Hero, PlayButton, PlayerLink, styles as s } from '../-ui/kit';
import { LabelLink, MusicGenreLinks } from '../-ui/links';
import { openAddToPlaylist } from '../-ui/music/add-to-playlist';
import { TrackList } from '../-ui/music/track-list';
import { NotFound, NotPartOfProfile } from '../-ui/not-part';
import { getEngine, playItems } from '../-ui/playback/use-player';
import { useSections } from '../-ui/sections';

export const Route = createFileRoute('/player/music/album/$albumId')({
  component: AlbumPage,
});

function trackArtists(track: LibTrack): string {
  return (track.artists ?? [])
    .map((a) => (typeof a === 'string' ? a : a.name))
    .filter(Boolean)
    .join(', ');
}

function AlbumPage() {
  const sections = useSections();
  const id = Number(Route.useParams().albumId);
  const album = useQuery({ ...albumQuery(id), enabled: sections.music && Number.isFinite(id) });

  if (!sections.music) return <NotPartOfProfile what="Music" single />;
  if (album.isLoading)
    return <div className={s.skeleton} style={{ height: '50vh', marginTop: '2rem' }} />;
  const a = album.data;
  if (!a) return <NotFound what="Album" />;

  const owned = a.tracks.filter((t) => !t.is_missing && t.id);
  const totalMs = owned.reduce((sum, t) => sum + (t.duration ?? 0), 0);
  const discs = new Set(a.tracks.map((t) => t.disc_number ?? 1)).size;
  const qualities = [...new Set(owned.map((t) => fileQuality(t.file)).filter(Boolean))];
  const albumArtist = a.primary_artist?.name;
  const queue = albumItems(a);
  /** a track plays with the rest of the album after it */
  const playTrack = (trackId: number | null) => {
    const at = queue.findIndex((x) => x.id === String(trackId));
    if (at >= 0) playItems(queue, at);
  };

  return (
    <>
      <Hero
        backdrop={a.image_url}
        poster={a.image_url}
        posterShape="square"
        kicker={a.album_type ? a.album_type : 'Album'}
        title={a.title}
        meta={
          <>
            {a.primary_artist ? (
              <PlayerLink
                href={`/player/music/artist/${a.primary_artist.id}`}
                className={s.railMore}
                data-nav
              >
                {albumArtist}
              </PlayerLink>
            ) : null}
            {a.year ? <span>{a.year}</span> : null}
            <span>
              {owned.length} of {a.tracks.length} tracks
            </span>
            {totalMs ? <span>{runtime(totalMs / 1000)}</span> : null}
            <MusicGenreLinks genres={(a.genres ?? []).slice(0, 3)} />
            {a.label ? <LabelLink label={a.label} /> : null}
          </>
        }
        badges={
          qualities.length ? (
            <div className={s.badges}>
              {qualities.slice(0, 3).map((q) => (
                <Badge key={q}>{q}</Badge>
              ))}
            </div>
          ) : null
        }
        actions={
          <>
            <PlayButton
              onPlay={queue.length ? () => playItems(queue) : undefined}
              why="None of this album's tracks are on disk."
            />
            <PlayButton
              label="Shuffle"
              primary={false}
              onPlay={
                queue.length
                  ? () => {
                      playItems(queue, Math.floor(Math.random() * queue.length));
                      getEngine().toggleShuffle();
                    }
                  : undefined
              }
              why="None of this album's tracks are on disk."
            />
            {queue.length ? (
              <button
                type="button"
                className={s.btn}
                onClick={() =>
                  openAddToPlaylist(
                    a.tracks
                      .filter((t) => !t.is_missing && t.id)
                      .map((t) => ({
                        title: t.title,
                        artist: trackArtists(t) || albumArtist || '',
                        album: a.title,
                        duration_ms: t.duration ?? null,
                        lib2_track_id: Number(t.id),
                      })),
                  )
                }
                data-nav
              >
                <PlusIcon />
                Add to playlist
              </button>
            ) : null}
          </>
        }
      />

      <TrackList
        label="Tracks"
        rows={a.tracks.map((track, index) => {
          const artists = trackArtists(track);
          const playable = !track.is_missing && !!track.id;
          return {
            key: String(track.id ?? `missing-${index}`),
            title: track.title,
            trackId: playable ? Number(track.id) : null,
            number:
              discs > 1
                ? `${track.disc_number ?? 1}.${track.track_number ?? index + 1}`
                : (track.track_number ?? index + 1),
            artist: artists && artists !== albumArtist ? artists : undefined,
            duration: track.duration ? track.duration / 1000 : null,
            explicit: !!track.explicit,
            end: playable ? fileQuality(track.file) : null,
            why: 'Not in your library',
            song: playable
              ? {
                  title: track.title,
                  artist: artists || albumArtist || '',
                  album: a.title,
                  duration_ms: track.duration ?? null,
                  lib2_track_id: Number(track.id),
                }
              : null,
          };
        })}
        onPlay={(i) => playTrack(a.tracks[i].id)}
      />
      <Comments itemType="album" itemId={String(id)} title={a.title} />
    </>
  );
}
