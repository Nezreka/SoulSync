/**
 * one of soulsync's own playlists: flow, a daily mix, fresh tape, hidden
 * gems… play what's in the library. the songs it found for you that you
 * don't have yet stay in the list, dimmed, and one button puts them on the
 * wishlist: soulsync gets them, and then they play here too.
 */

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { createFileRoute } from '@tanstack/react-router';
import { useState } from 'react';

import { runtime } from '../-lib/format';
import { albumArtwork, remoteImage } from '../-lib/images';
import { playlistItems, songCount, songFromRow } from '../-lib/playlists';
import { discoverPlaylistQuery, getDiscoverSongs } from '../-player.api';
import { DownloadButton } from '../-ui/download-button';
import { DownloadIcon, PlusIcon, ShuffleIcon } from '../-ui/icons';
import { PlayButton, styles as k } from '../-ui/kit';
import { openAddToPlaylist } from '../-ui/music/add-to-playlist';
import m from '../-ui/music/music.module.css';
import { PlaylistCover } from '../-ui/music/playlist-cover';
import { TrackList, type TrackRow } from '../-ui/music/track-list';
import { NotFound, NotPartOfProfile } from '../-ui/not-part';
import { getEngine, playItems } from '../-ui/playback/use-player';
import { useSections } from '../-ui/sections';

export const Route = createFileRoute('/player/music/discover/$key')({
  component: DiscoverPlaylistPage,
});

const KICKER: Record<string, string> = {
  made: 'Made for you',
  fresh: 'New for you',
  picks: 'Picked for you',
  listenbrainz: 'From ListenBrainz',
};

function DiscoverPlaylistPage() {
  const { key } = Route.useParams();
  const sections = useSections();
  const queryClient = useQueryClient();
  const detail = useQuery({ ...discoverPlaylistQuery(key), enabled: sections.music });
  const [note, setNote] = useState<string | null>(null);
  const [getting, setGetting] = useState(false);

  if (!sections.music) return <NotPartOfProfile what="Music" single />;
  if (detail.isLoading)
    return <div className={k.skeleton} style={{ height: '50vh', marginTop: '2rem' }} />;
  const p = detail.data;
  if (!p) return <NotFound what="Playlist" />;

  const back = `/player/music/discover/${key}`;
  const queue = playlistItems({ id: 0, name: p.name, songs: p.songs }, back);
  const hidden = p.songs.filter((x) => x.unplayable === 'explicit').length;
  const missing = p.songs.filter((x) => x.unplayable === 'missing');
  const toGet = missing.filter((x) => !x.wanted);
  const total = p.songs.reduce((n, x) => n + (x.duration_ms || 0), 0);

  const getThem = async () => {
    setGetting(true);
    setNote(null);
    try {
      const r = await getDiscoverSongs(key);
      setNote(
        r.added
          ? `${songCount(r.added)} on your wishlist. They play here once SoulSync has them.`
          : 'They were already on your wishlist.',
      );
      await queryClient.invalidateQueries({ queryKey: ['player', 'discover-playlist', key] });
    } catch {
      setNote("Couldn't add them to your wishlist. Try again.");
    } finally {
      setGetting(false);
    }
  };

  const rows: TrackRow[] = p.songs.map((x, i) => ({
    key: `${x.position}:${x.title}`,
    title: x.title,
    number: i + 1,
    trackId: x.track_id,
    artist: x.artist,
    artistHref: x.artist_id ? `/player/music/artist/${x.artist_id}` : undefined,
    album: x.album,
    albumHref: x.album_id ? `/player/music/album/${x.album_id}` : undefined,
    art: albumArtwork(x.album_id) ?? remoteImage(x.image_url),
    duration: x.duration_ms ? x.duration_ms / 1000 : null,
    why:
      x.unplayable === 'explicit'
        ? 'Explicit, hidden for this profile'
        : x.wanted
          ? 'On your wishlist'
          : 'Not in your library yet',
    song: songFromRow(x),
  }));

  return (
    <>
      <header className={m.plHero}>
        <div className={m.plBackdrop} aria-hidden="true">
          <PlaylistCover
            playlist={{ name: p.name, image_url: null, cover_tiles: p.cover_tiles, liked: false }}
            size="hero"
          />
        </div>
        <PlaylistCover
          playlist={{ name: p.name, image_url: null, cover_tiles: p.cover_tiles, liked: false }}
          size="hero"
        />
        <div className={m.plText}>
          <span className={m.plKicker}>{KICKER[p.section] ?? 'SoulSync'}</span>
          <h1 className={m.plTitle}>{p.name}</h1>
          {p.blurb ? <p className={m.plAbout}>{p.blurb}</p> : null}
          <div className={m.plMeta}>
            <span>{songCount(p.songs.length)}</span>
            {total ? <span>{runtime(total / 1000)}</span> : null}
            {missing.length ? <span>{missing.length.toLocaleString()} to get</span> : null}
            {hidden ? (
              <span>{hidden.toLocaleString()} explicit, hidden for this profile</span>
            ) : null}
          </div>
          <div className={m.plActions}>
            <PlayButton
              onPlay={queue.length ? () => playItems(queue) : undefined}
              why={
                p.songs.length
                  ? 'None of these are in your library yet. Get them and they play here.'
                  : 'Nothing in this one yet.'
              }
            />
            <button
              type="button"
              className={k.btn}
              disabled={queue.length < 2}
              onClick={() => {
                playItems(queue, Math.floor(Math.random() * queue.length));
                getEngine().toggleShuffle();
              }}
              data-nav
            >
              <ShuffleIcon />
              Shuffle
            </button>
            {queue.length ? (
              <DownloadButton type="playlist" id={`discover:${key}`} title={p.name} />
            ) : null}
            {toGet.length ? (
              <button
                type="button"
                className={k.btn}
                disabled={getting}
                onClick={() => void getThem()}
                title="Puts them on your wishlist: SoulSync downloads them, then they play here"
                data-nav
              >
                <DownloadIcon />
                {getting
                  ? 'Adding…'
                  : toGet.length === missing.length
                    ? `Get the ${songCount(toGet.length)}`
                    : `Get the other ${songCount(toGet.length)}`}
              </button>
            ) : null}
            {queue.length ? (
              <button
                type="button"
                className={k.btn}
                onClick={() =>
                  openAddToPlaylist(p.songs.filter((x) => x.track_id !== null).map(songFromRow))
                }
                title="Add these songs to one of your playlists"
                data-nav
              >
                <PlusIcon />
                Add to playlist
              </button>
            ) : null}
          </div>
          {note ? (
            <p className={m.plNote} role="status">
              {note}
            </p>
          ) : null}
        </div>
      </header>

      <TrackList
        rows={rows}
        label={`Songs in ${p.name}`}
        showArt
        showAlbum
        onPlay={(index) => {
          const target = p.songs[index];
          const at = queue.findIndex((q) => q.id === String(target.track_id));
          if (at >= 0) playItems(queue, at);
        }}
      />
    </>
  );
}
