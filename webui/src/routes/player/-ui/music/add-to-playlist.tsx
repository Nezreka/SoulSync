/**
 * add to playlist: from any song (a track row, the queue, what's playing),
 * into one of your playlists. ticks show where the songs already are; a tick
 * takes them out again. a new playlist can be made right here with them in
 * it. mirrored playlists follow their source, so they're not offered.
 *
 * one picker for the whole player: openAddToPlaylist(songs) from anywhere,
 * AddToPlaylistHost mounted once in the shell.
 */

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, useSyncExternalStore } from 'react';

import type { SongRef } from '../../-player.api';

import { songCount } from '../../-lib/playlists';
import { playlistEdits, playlistsQuery } from '../../-player.api';
import { CheckIcon, PlusIcon } from '../icons';
import { Sheet } from '../sheet';
import sh from '../sheet.module.css';
import s from './music.module.css';
import { PlaylistCover } from './playlist-cover';

let pending: SongRef[] | null = null;
const listeners = new Set<() => void>();

function emit() {
  for (const l of listeners) l();
}

/** open the picker for these songs (one, or a whole album) */
export function openAddToPlaylist(songs: SongRef[]) {
  const usable = songs.filter((x) => x.title && x.artist);
  if (!usable.length) return;
  pending = usable;
  emit();
}

function usePending() {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => pending,
    () => null,
  );
}

export function AddToPlaylistHost() {
  const songs = usePending();
  if (!songs) return null;
  return (
    <AddToPlaylist
      songs={songs}
      onClose={() => {
        pending = null;
        emit();
      }}
    />
  );
}

function AddToPlaylist({ songs, onClose }: { songs: SongRef[]; onClose: () => void }) {
  const queryClient = useQueryClient();
  const lists = useQuery(playlistsQuery());
  const membership = useQuery({
    queryKey: [
      'player',
      'playlist-membership',
      songs.map((x) => `${x.artist}|${x.title}`).join('/'),
    ],
    queryFn: () => playlistEdits.membership(songs),
    staleTime: 0,
  });
  const [busy, setBusy] = useState<number | 'new' | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [filter, setFilter] = useState('');
  const [making, setMaking] = useState(false);
  const [name, setName] = useState('');

  const yours = (lists.data?.yours ?? []).filter((p) =>
    p.name.toLowerCase().includes(filter.trim().toLowerCase()),
  );
  const holds = (id: number) => membership.data?.playlists[String(id)] ?? 0;

  const refresh = async (id?: number) => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['player', 'playlists'] }),
      queryClient.invalidateQueries({ queryKey: ['player', 'playlist-membership'] }),
      id ? queryClient.invalidateQueries({ queryKey: ['player', 'playlist', id] }) : null,
    ]);
  };

  const toggle = async (id: number, listName: string) => {
    setBusy(id);
    setNote(null);
    try {
      if (holds(id) >= songs.length) {
        await playlistEdits.removeSongs(id, songs);
        setNote(`Taken out of ${listName}`);
      } else {
        const res = await playlistEdits.add(id, songs);
        setNote(res.added ? `Added to ${listName}` : `Already in ${listName}`);
      }
      await refresh(id);
    } catch {
      setNote(`Couldn't change ${listName}. Try again.`);
    } finally {
      setBusy(null);
    }
  };

  const create = async () => {
    const clean = name.trim();
    if (!clean) return;
    setBusy('new');
    setNote(null);
    try {
      await playlistEdits.create(clean, songs);
      await refresh();
      setNote(`Made ${clean}`);
      setMaking(false);
      setName('');
    } catch {
      setNote("Couldn't make that playlist. Try another name.");
    } finally {
      setBusy(null);
    }
  };

  const what = songs.length === 1 ? songs[0].title : songCount(songs.length);
  return (
    <Sheet
      title="Add to playlist"
      subtitle={songs.length === 1 ? `${what} · ${songs[0].artist}` : what}
      onClose={onClose}
    >
      {(lists.data?.yours.length ?? 0) > 8 ? (
        <input
          className={sh.input}
          type="search"
          placeholder="Find a playlist"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          aria-label="Find a playlist"
        />
      ) : null}
      <div className={s.pickList} role="list">
        {making ? (
          <form
            className={s.pickNew}
            onSubmit={(e) => {
              e.preventDefault();
              void create();
            }}
          >
            <input
              className={sh.input}
              placeholder="Name your playlist"
              value={name}
              maxLength={120}
              autoFocus
              onChange={(e) => setName(e.target.value)}
              aria-label="New playlist name"
            />
            <button
              type="submit"
              className={sh.btnPrimary}
              disabled={!name.trim() || busy === 'new'}
            >
              {busy === 'new' ? 'Making…' : 'Make it'}
            </button>
          </form>
        ) : (
          <button type="button" className={s.pickRow} onClick={() => setMaking(true)} data-nav>
            <span className={s.pickPlus}>
              <PlusIcon />
            </span>
            <span className={s.pickName}>New playlist</span>
          </button>
        )}
        {lists.isLoading ? <p className={s.pickNote}>Loading your playlists…</p> : null}
        {yours.map((p) => {
          const n = holds(p.id);
          const all = n >= songs.length;
          return (
            <button
              key={p.id}
              type="button"
              role="listitem"
              className={s.pickRow}
              aria-pressed={all}
              disabled={busy !== null}
              onClick={() => void toggle(p.id, p.name)}
              data-nav
            >
              <PlaylistCover playlist={p} size="thumb" />
              <span className={s.pickText}>
                <span className={s.pickName}>{p.name}</span>
                <span className={s.pickSub}>
                  {songCount(p.track_count)}
                  {n && !all ? ` · ${n} of these already` : ''}
                </span>
              </span>
              <span className={s.pickTick} data-on={all ? 'true' : undefined} aria-hidden="true">
                {busy === p.id ? '…' : all ? <CheckIcon /> : <PlusIcon />}
              </span>
            </button>
          );
        })}
        {lists.isSuccess && !yours.length && filter ? (
          <p className={s.pickNote}>No playlist called that.</p>
        ) : null}
      </div>
      {note ? (
        <p className={s.pickStatus} role="status">
          {note}
        </p>
      ) : null}
    </Sheet>
  );
}
