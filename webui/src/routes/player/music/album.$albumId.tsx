import { useQuery } from '@tanstack/react-query';
import { createFileRoute } from '@tanstack/react-router';

import type { LibTrack } from '../-player.types';

import { runtime } from '../-lib/format';
import { fileQuality } from '../-lib/music';
import { albumQuery } from '../-player.api';
import { Badge, Hero, PlayButton, PlayerLink, styles as s } from '../-ui/kit';
import { LabelLink, MusicGenreLinks } from '../-ui/links';
import { NotFound, NotPartOfProfile } from '../-ui/not-part';
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
            <PlayButton />
            <PlayButton label="Shuffle" primary={false} />
          </>
        }
      />

      <div className={s.list} role="list" aria-label="Tracks">
        {a.tracks.map((track, index) => {
          const showDisc =
            discs > 1 &&
            (index === 0 || (a.tracks[index - 1].disc_number ?? 1) !== (track.disc_number ?? 1));
          const artists = trackArtists(track);
          return (
            <div key={track.id ?? `missing-${index}`} role="listitem">
              {showDisc ? (
                <div className={s.searchGroupLabel}>Disc {track.disc_number ?? 1}</div>
              ) : null}
              <div
                className={s.row}
                data-missing={track.is_missing || !track.id ? 'true' : undefined}
                tabIndex={track.is_missing ? -1 : 0}
                data-nav={track.is_missing ? undefined : true}
                title={track.is_missing ? 'Not in your library' : undefined}
              >
                <span className={s.rowNum}>{track.track_number ?? index + 1}</span>
                <span className={s.rowMain}>
                  <span className={s.rowTitle}>
                    {track.title}
                    {track.explicit ? ' 🅴' : ''}
                  </span>
                  {artists && artists !== albumArtist ? (
                    <span className={s.rowSub}>{artists}</span>
                  ) : null}
                </span>
                <span className={s.rowEnd}>
                  {track.is_missing ? 'missing' : fileQuality(track.file)}
                  <span>{runtime((track.duration ?? 0) / 1000)}</span>
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </>
  );
}
