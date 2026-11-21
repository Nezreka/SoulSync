/**
 * one playlist. yours: play it, shuffle it, and change everything (name,
 * description, the songs, their order, duplicate it, delete it). mirrored:
 * play it, or make it yours (an editable copy; the mirror keeps following
 * its source). songs you can't play stay in the list, dimmed, with why.
 */

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { createFileRoute, useNavigate } from '@tanstack/react-router';
import { useMemo, useState } from 'react';

import type { PlaylistDetail } from '../-player.api';

import { runtime } from '../-lib/format';
import { albumArtwork, remoteImage } from '../-lib/images';
import { playlistItems, songCount, songFromRow, sourceName } from '../-lib/playlists';
import { playlistEdits, playlistQuery } from '../-player.api';
import { CopyIcon, PencilIcon, PlusIcon, SearchIcon, ShuffleIcon, TrashIcon } from '../-ui/icons';
import { Empty, PlayButton, styles as k } from '../-ui/kit';
import { openAddToPlaylist } from '../-ui/music/add-to-playlist';
import m from '../-ui/music/music.module.css';
import { PlaylistCover } from '../-ui/music/playlist-cover';
import { TrackList, type TrackRow } from '../-ui/music/track-list';
import { NotFound, NotPartOfProfile } from '../-ui/not-part';
import { getEngine, playItems } from '../-ui/playback/use-player';
import { useSections } from '../-ui/sections';
import { ConfirmSheet, Sheet } from '../-ui/sheet';
import sh from '../-ui/sheet.module.css';

export const Route = createFileRoute('/player/music/playlist/$playlistId')({
  component: PlaylistPage,
});

type Dialog = 'edit' | 'delete' | 'copy' | null;

function EditPlaylist({ playlist, onClose }: { playlist: PlaylistDetail; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState(playlist.name);
  const [about, setAbout] = useState(playlist.description ?? '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      if (name.trim() && name.trim() !== playlist.name)
        await playlistEdits.rename(playlist.id, name.trim());
      if (about.trim() !== (playlist.description ?? ''))
        await playlistEdits.describe(playlist.id, about.trim());
      await queryClient.invalidateQueries({ queryKey: ['player', 'playlist', playlist.id] });
      await queryClient.invalidateQueries({ queryKey: ['player', 'playlists'] });
      onClose();
    } catch {
      setError("Couldn't save that. Try again.");
      setBusy(false);
    }
  };
  return (
    <Sheet
      title="Edit playlist"
      onClose={onClose}
      footer={
        <>
          <button type="button" className={sh.btn} onClick={onClose} data-nav>
            Cancel
          </button>
          <button
            type="button"
            className={sh.btnPrimary}
            disabled={!name.trim() || busy}
            onClick={() => void save()}
            data-nav
          >
            {busy ? 'Saving…' : 'Save'}
          </button>
        </>
      }
    >
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        {!playlist.liked ? (
          <label className={sh.field}>
            Name
            <input
              className={sh.input}
              value={name}
              maxLength={120}
              onChange={(e) => setName(e.target.value)}
            />
          </label>
        ) : null}
        <label className={sh.field}>
          Description
          <textarea
            className={sh.input}
            value={about}
            maxLength={500}
            placeholder="What it's for, when to play it"
            onChange={(e) => setAbout(e.target.value)}
          />
        </label>
        {error ? <p className={sh.error}>{error}</p> : null}
      </form>
    </Sheet>
  );
}

function PlaylistPage() {
  const sections = useSections();
  const id = Number(Route.useParams().playlistId);
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const detail = useQuery({
    ...playlistQuery(id),
    enabled: sections.music && Number.isFinite(id),
    retry: false,
  });
  const [find, setFind] = useState('');
  const [dialog, setDialog] = useState<Dialog>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  // an edit shows at once; the server's answer replaces it
  const [order, setOrder] = useState<number[] | null>(null);

  const p = detail.data;
  const songs = useMemo(() => {
    if (!p) return [];
    if (!order || order.length !== p.songs.length) return p.songs;
    return order.map((i) => p.songs[i]);
  }, [p, order]);

  if (!sections.music) return <NotPartOfProfile what="Music" single />;
  if (detail.isLoading)
    return <div className={k.skeleton} style={{ height: '50vh', marginTop: '2rem' }} />;
  if (!p) return <NotFound what="Playlist" />;

  const yours = p.kind === 'yours';
  const queue = playlistItems({ ...p, songs });
  const missing = p.songs.length - p.playable_count;
  const from = sourceName(p.source);
  const needle = find.trim().toLowerCase();
  const shown = songs
    .map((x, i) => ({ x, i }))
    .filter(
      ({ x }) =>
        !needle ||
        x.title.toLowerCase().includes(needle) ||
        x.artist.toLowerCase().includes(needle) ||
        x.album.toLowerCase().includes(needle),
    );

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ['player', 'playlist', id] });
    await queryClient.invalidateQueries({ queryKey: ['player', 'playlists'] });
    setOrder(null);
  };

  const playAt = (songIndex: number) => {
    const target = songs[songIndex];
    const at = queue.findIndex((q) => q.id === String(target.track_id));
    if (at >= 0) playItems(queue, at);
  };

  const remove = async (songIndex: number) => {
    const target = songs[songIndex];
    setNote(null);
    try {
      await playlistEdits.removeAt(p.id, target.position);
      setNote(`Took out ${target.title}`);
      await refresh();
    } catch {
      setNote(`Couldn't take out ${target.title}. Reload and try again.`);
    }
  };

  const move = async (from: number, to: number) => {
    const next = songs.map((x) => p.songs.indexOf(x));
    const [picked] = next.splice(from, 1);
    next.splice(to, 0, picked);
    setOrder(next);
    try {
      // every position, 1-based, in the new order
      await playlistEdits.reorder(
        p.id,
        next.map((i) => p.songs[i].position),
      );
      await refresh();
    } catch {
      setOrder(null);
      setNote('The playlist changed somewhere else. It has been reloaded.');
      await refresh();
    }
  };

  const rows: TrackRow[] = shown.map(({ x, i }) => ({
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
    why: 'Not in your library',
    song: songFromRow(x),
  }));
  // a filtered list's index is not the playlist's
  const realIndex = (shownIndex: number) => shown[shownIndex].i;

  return (
    <>
      <header className={m.plHero}>
        <div className={m.plBackdrop} aria-hidden="true">
          <PlaylistCover playlist={p} size="hero" />
        </div>
        <PlaylistCover playlist={p} size="hero" />
        <div className={m.plText}>
          <span className={m.plKicker}>
            {yours ? 'Your playlist' : from ? `Mirrored from ${from}` : 'Mirrored playlist'}
          </span>
          <h1 className={m.plTitle}>{p.name}</h1>
          {p.description ? <p className={m.plAbout}>{p.description}</p> : null}
          <div className={m.plMeta}>
            <span>{songCount(p.songs.length)}</span>
            {p.duration_ms ? <span>{runtime(p.duration_ms / 1000)}</span> : null}
            {missing ? <span>{missing.toLocaleString()} not in your library</span> : null}
            {!yours && p.owner ? <span>by {p.owner}</span> : null}
          </div>
          <div className={m.plActions}>
            <PlayButton
              onPlay={queue.length ? () => playItems(queue) : undefined}
              why={
                p.songs.length
                  ? 'None of these songs are in your library.'
                  : 'This playlist is empty.'
              }
            />
            <button
              type="button"
              className={k.btn}
              disabled={!queue.length}
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
              <button
                type="button"
                className={k.btn}
                onClick={() =>
                  openAddToPlaylist(p.songs.filter((x) => x.track_id !== null).map(songFromRow))
                }
                title="Add these songs to another playlist"
                data-nav
              >
                <PlusIcon />
                Add to playlist
              </button>
            ) : null}
            {yours ? (
              <>
                <button
                  type="button"
                  className={m.plIconBtn}
                  aria-label="Edit"
                  title="Edit"
                  onClick={() => setDialog('edit')}
                  data-nav
                >
                  <PencilIcon />
                </button>
                <button
                  type="button"
                  className={m.plIconBtn}
                  aria-label="Duplicate"
                  title="Duplicate"
                  disabled={busy}
                  onClick={() => {
                    setBusy(true);
                    void playlistEdits
                      .duplicate(p.id)
                      .then(async ({ id: copy }) => {
                        await queryClient.invalidateQueries({ queryKey: ['player', 'playlists'] });
                        void navigate({
                          to: '/player/music/playlist/$playlistId',
                          params: { playlistId: String(copy) },
                        });
                      })
                      .catch(() => setNote("Couldn't duplicate it. Try again."))
                      .finally(() => setBusy(false));
                  }}
                  data-nav
                >
                  <CopyIcon />
                </button>
                {!p.liked ? (
                  <button
                    type="button"
                    className={m.plIconBtn}
                    aria-label="Delete"
                    title="Delete"
                    onClick={() => setDialog('delete')}
                    data-nav
                  >
                    <TrashIcon />
                  </button>
                ) : null}
              </>
            ) : (
              <button type="button" className={k.btn} onClick={() => setDialog('copy')} data-nav>
                <CopyIcon />
                Make it yours
              </button>
            )}
          </div>
        </div>
      </header>

      {note ? (
        <p className={m.plNote} role="status">
          {note}
        </p>
      ) : null}

      {p.songs.length > 12 ? (
        <label className={m.plFind}>
          <SearchIcon />
          <input
            type="search"
            placeholder="Find in this playlist"
            value={find}
            onChange={(e) => setFind(e.target.value)}
            aria-label="Find in this playlist"
          />
        </label>
      ) : null}

      {p.songs.length ? (
        rows.length ? (
          <TrackList
            rows={rows}
            label={`Songs in ${p.name}`}
            showArt
            showAlbum
            onPlay={(i) => playAt(realIndex(i))}
            onRemove={yours ? (i) => void remove(realIndex(i)) : undefined}
            // dragging a filtered list would be guessing: only the whole list moves
            onMove={yours && !needle ? (a, b) => void move(a, b) : undefined}
          />
        ) : (
          <Empty title="Nothing matches">Try another word.</Empty>
        )
      ) : (
        <Empty title="No songs yet">
          {yours
            ? 'Add songs from any album, the queue or what’s playing: look for the + on a song.'
            : 'Its source has no songs right now.'}
        </Empty>
      )}

      {dialog === 'edit' ? <EditPlaylist playlist={p} onClose={() => setDialog(null)} /> : null}
      {dialog === 'delete' ? (
        <ConfirmSheet
          title={`Delete ${p.name}?`}
          confirm="Delete"
          danger
          busy={busy}
          onClose={() => setDialog(null)}
          onConfirm={() => {
            setBusy(true);
            void playlistEdits
              .remove(p.id)
              .then(async () => {
                await queryClient.invalidateQueries({ queryKey: ['player', 'playlists'] });
                void navigate({ to: '/player/music/playlists' });
              })
              .catch(() => {
                setNote("Couldn't delete it. Try again.");
                setDialog(null);
              })
              .finally(() => setBusy(false));
          }}
        >
          The playlist goes for good, here and in SoulSync. The songs stay in your library.
        </ConfirmSheet>
      ) : null}
      {dialog === 'copy' ? (
        <ConfirmSheet
          title="Make it yours?"
          confirm="Make a copy"
          busy={busy}
          onClose={() => setDialog(null)}
          onConfirm={() => {
            setBusy(true);
            void playlistEdits
              .copyMirror(p.id)
              .then(async ({ id: copy }) => {
                await queryClient.invalidateQueries({ queryKey: ['player', 'playlists'] });
                setDialog(null);
                void navigate({
                  to: '/player/music/playlist/$playlistId',
                  params: { playlistId: String(copy) },
                });
              })
              .catch(() => {
                setNote("Couldn't copy it. Try again.");
                setDialog(null);
              })
              .finally(() => setBusy(false));
          }}
        >
          A copy goes into your playlists for you to change. This one keeps following{' '}
          {from ?? 'its source'}.
        </ConfirmSheet>
      ) : null}
    </>
  );
}
