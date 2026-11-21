/**
 * a song's own two buttons where it plays: the heart (Liked Songs, the
 * playlist every music app pins first) and add to playlist.
 */

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import type { PlayItem } from '../../-engine/types';

import { songFromItem } from '../../-lib/playlists';
import { playlistEdits, playlistsQuery } from '../../-player.api';
import { HeartIcon, PlusIcon } from '../icons';
import s from '../playback/playback.module.css';
import { openAddToPlaylist } from './add-to-playlist';

export function SongActions({ item }: { item: PlayItem }) {
  const song = songFromItem(item);
  const queryClient = useQueryClient();
  const lists = useQuery({ ...playlistsQuery(), enabled: !!song });
  const liked = lists.data?.yours.find((p) => p.liked) ?? null;
  const key = ['player', 'playlist-membership', song ? `${song.artist}|${song.title}` : ''];
  const membership = useQuery({
    queryKey: key,
    queryFn: () => playlistEdits.membership([song!]),
    enabled: !!song,
    staleTime: 60_000,
  });
  const [busy, setBusy] = useState(false);
  if (!song) return null;
  const isLiked = !!liked && (membership.data?.playlists[String(liked.id)] ?? 0) > 0;

  const toggle = async () => {
    setBusy(true);
    try {
      if (isLiked) await playlistEdits.unlike([song]);
      else await playlistEdits.like([song]);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['player', 'playlist-membership'] }),
        queryClient.invalidateQueries({ queryKey: ['player', 'playlists'] }),
        liked
          ? queryClient.invalidateQueries({ queryKey: ['player', 'playlist', liked.id] })
          : null,
      ]);
    } finally {
      setBusy(false);
    }
  };

  return (
    <span className={s.songActions}>
      <button
        type="button"
        className={s.ctl}
        data-on={isLiked ? 'true' : undefined}
        aria-pressed={isLiked}
        aria-label={isLiked ? 'Take out of Liked Songs' : 'Add to Liked Songs'}
        title={isLiked ? 'In your Liked Songs' : 'Like'}
        disabled={busy}
        onClick={() => void toggle()}
        data-nav
      >
        <HeartIcon filled={isLiked} />
      </button>
      <button
        type="button"
        className={s.ctl}
        aria-label="Add to playlist"
        title="Add to playlist"
        onClick={() => openAddToPlaylist([song])}
        data-nav
      >
        <PlusIcon />
      </button>
    </span>
  );
}
