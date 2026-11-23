import { useQuery } from '@tanstack/react-query';
import { createFileRoute } from '@tanstack/react-router';
import { useEffect, useState } from 'react';

import type { RailCard } from '../-lib/rails';
import type { LibTrack } from '../-player.types';

import { runtime } from '../-lib/format';
import { libraryThumb, remoteImage } from '../-lib/images';
import { fileQuality, ownedReleases } from '../-lib/music';
import { albumItems, isLibTrackOwned } from '../-lib/play-items';
import { albumQuery, artistQuery, requestMusicTrack } from '../-player.api';
import { Comments } from '../-ui/comments';
import { DownloadIcon, PlusIcon } from '../-ui/icons';
import { CardRail, Hero, PlayButton, PlayerLink, styles as s } from '../-ui/kit';
import { LabelLink, MusicGenreLinks } from '../-ui/links';
import { openAddToPlaylist } from '../-ui/music/add-to-playlist';
import m from '../-ui/music/music.module.css';
import { TrackList } from '../-ui/music/track-list';
import { NotFound, NotPartOfProfile } from '../-ui/not-part';
import { getEngine, playItems } from '../-ui/playback/use-player';
import { useSections } from '../-ui/sections';

export const Route = createFileRoute('/player/music/album/$albumId')({
  validateSearch: (search: Record<string, unknown>): { track?: number } => {
    const n = Number(search.track);
    return Number.isInteger(n) && n > 0 ? { track: n } : {};
  },
  component: AlbumPage,
});

function trackArtists(track: LibTrack): string {
  return (track.artists ?? [])
    .map((a) => (typeof a === 'string' ? a : a.name))
    .filter(Boolean)
    .join(', ');
}

function qualityBadgeClass(label: string): string {
  const up = label.toUpperCase();
  if (up.includes('24') || up.includes('HI-RES') || up.includes('96') || up.includes('192') || up.includes('DSD')) {
    return m.qualityHiRes;
  }
  if (up.includes('FLAC') || up.includes('ALAC') || up.includes('WAV') || up.includes('LOSSLESS')) {
    return m.qualityLossless;
  }
  return m.qualityBadge;
}

function AlbumPage() {
  const sections = useSections();
  const id = Number(Route.useParams().albumId);
  const { track: highlightTrack } = Route.useSearch();
  const album = useQuery({ ...albumQuery(id), enabled: sections.music && Number.isFinite(id) });

  useEffect(() => {
    if (!highlightTrack || !album.data) return;
    const t = window.setTimeout(() => {
      const el = document.querySelector(`[data-track-id="${highlightTrack}"]`);
      el?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }, 140);
    return () => window.clearTimeout(t);
  }, [highlightTrack, album.data]);
  const primaryArtistId = album.data?.primary_artist?.id;
  const artistDetail = useQuery({
    ...artistQuery(primaryArtistId ?? 0),
    enabled: sections.music && !!primaryArtistId && primaryArtistId > 0,
  });
  const [reqState, setReqState] = useState<
    Record<string, 'sending' | 'wishlist' | 'requested' | 'owned' | 'error'>
  >({});
  const [requestingAll, setRequestingAll] = useState(false);

  if (!sections.music) return <NotPartOfProfile what="Music" single />;
  if (album.isLoading)
    return <div className={s.skeleton} style={{ height: '50vh', marginTop: '2rem' }} />;
  const a = album.data;
  if (!a) return <NotFound what="Album" />;

  const owned = a.tracks.filter(isLibTrackOwned);
  const missing = a.tracks
    .map((track, index) => ({ track, index, key: String(track.id ?? `missing-${index}`) }))
    .filter(({ track }) => !isLibTrackOwned(track));
  const totalMs = owned.reduce((sum, t) => sum + (t.duration ?? 0), 0);
  const discs = new Set(a.tracks.map((t) => t.disc_number ?? 1)).size;
  const qualities = [...new Set(owned.map((t) => fileQuality(t.file)).filter((q): q is string => !!q))];
  const albumArtist = a.primary_artist?.name;
  const queue = albumItems(a);

  const moreByArtist: RailCard[] = ownedReleases([
    ...(artistDetail.data?.albums ?? []),
    ...(artistDetail.data?.eps ?? []),
  ])
    .filter((r) => Number(r.id) !== a.id)
    .slice(0, 16)
    .map((r) => ({
      key: `more-al-${r.id}`,
      title: r.title,
      subtitle: r.year ? String(r.year) : undefined,
      art: libraryThumb(r.image_url) ?? remoteImage(r.remote_image_url),
      href: `/player/music/album/${r.id}`,
      play: { kind: 'album' as const, id: Number(r.id) },
      shape: 'square',
    }));

  const playTrack = (trackId: number | null) => {
    const at = queue.findIndex((x) => x.id === String(trackId));
    if (at >= 0) playItems(queue, at);
  };

  const requestOne = async (rowKey: string, track: LibTrack, index: number) => {
    const cur = reqState[rowKey];
    if (cur === 'sending' || cur === 'wishlist' || cur === 'requested' || cur === 'owned') return;
    setReqState((prev) => ({ ...prev, [rowKey]: 'sending' }));
    const res = await requestMusicTrack({
      title: track.title,
      artist: trackArtists(track) || albumArtist || 'Unknown Artist',
      album: a.title,
      album_id: a.id,
      artist_id: a.primary_artist?.id ?? null,
      track_id: track.id ?? null,
      track_number: track.track_number ?? index + 1,
      disc_number: track.disc_number ?? 1,
      duration_ms: track.duration ?? null,
      year: a.year ?? null,
      image_url: a.image_url ?? null,
    });
    setReqState((prev) => ({ ...prev, [rowKey]: res.state }));
  };

  const requestAllMissing = async () => {
    if (requestingAll || !missing.length) return;
    setRequestingAll(true);
    try {
      for (const item of missing) {
        await requestOne(item.key, item.track, item.index);
      }
    } finally {
      setRequestingAll(false);
    }
  };

  const unrequestedMissing = missing.filter(({ key }) => {
    const st = reqState[key];
    return !st || st === 'error';
  });

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
              {owned.length === a.tracks.length
                ? `${a.tracks.length} tracks`
                : `${owned.length} of ${a.tracks.length} tracks`}
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
                <span key={q} className={qualityBadgeClass(q)}>
                  {q}
                </span>
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
                      .filter(isLibTrackOwned)
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
            {unrequestedMissing.length ? (
              <button
                type="button"
                className={s.btn}
                disabled={requestingAll}
                onClick={() => void requestAllMissing()}
                title="Request missing tracks so SoulSync can grab them for your library"
                data-nav
              >
                <DownloadIcon />
                {requestingAll ? 'Requesting…' : `Request ${unrequestedMissing.length} missing`}
              </button>
            ) : null}
          </>
        }
      />

      <div className={m.albumTrackCard}>
        <TrackList
          label="Tracks"
          rows={a.tracks.map((track, index) => {
            const artists = trackArtists(track);
            const playable = isLibTrackOwned(track);
            const rowKey = String(track.id ?? `missing-${index}`);
            const st = reqState[rowKey];
            const missingLabel =
              st === 'sending'
                ? 'Requesting…'
                : st === 'wishlist'
                  ? 'On wishlist'
                  : st === 'requested'
                    ? 'Requested'
                    : st === 'owned'
                      ? 'In library'
                      : st === 'error'
                        ? 'Try again'
                        : 'Request';
            const trackQ = playable ? fileQuality(track.file) : null;
            return {
              key: rowKey,
              title: track.title,
              trackId: playable ? Number(track.id) : null,
              number:
                discs > 1
                  ? `${track.disc_number ?? 1}.${track.track_number ?? index + 1}`
                  : (track.track_number ?? index + 1),
              artist: artists && artists !== albumArtist ? artists : undefined,
              duration: track.duration ? track.duration / 1000 : null,
              explicit: track.explicit,
              playable,
              why: playable ? undefined : 'not in your library',
              end: playable ? trackQ : undefined,
              onMissingAction: playable ? undefined : () => void requestOne(rowKey, track, index),
              missingActionLabel: playable ? undefined : missingLabel,
              missingActionDone: st === 'wishlist' || st === 'requested' || st === 'owned',
              missingActionBusy: st === 'sending',
              song: playable
                ? {
                    title: track.title,
                    artist: artists || albumArtist || '',
                    album: a.title,
                    duration_ms: track.duration ?? null,
                    lib2_track_id: Number(track.id),
                  }
                : undefined,
            };
          })}
          onPlay={(i) => playTrack(a.tracks[i]?.id ?? null)}
        />
      </div>

      {/* Album Technical & Release Footer */}
      <div className={m.albumFooter}>
        {a.release_date ? <span>Released {a.release_date}</span> : a.year ? <span>{a.year}</span> : null}
        <span>
          {a.tracks.length} {a.tracks.length === 1 ? 'track' : 'tracks'}
          {totalMs ? ` · ${runtime(totalMs / 1000)}` : ''}
        </span>
        {discs > 1 ? <span>{discs} discs</span> : null}
        {a.label ? <span>℗ {a.label}</span> : null}
        {qualities.length ? <span>{qualities.join(' · ')}</span> : null}
      </div>

      {moreByArtist.length > 0 && albumArtist ? (
        <CardRail
          title={`More by ${albumArtist}`}
          cards={moreByArtist}
          more={
            primaryArtistId
              ? { to: `/player/music/artist/${primaryArtistId}`, label: 'View artist' }
              : undefined
          }
        />
      ) : null}

      <Comments itemType="album" itemId={String(id)} title={a.title} />
    </>
  );
}
