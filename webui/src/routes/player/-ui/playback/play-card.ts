/**
 * a card's play button: plays the album, playlist, movie or show right
 * here (the card itself still opens its page).
 */

import { useQueryClient } from '@tanstack/react-query';

import type { RailCard } from '../../-lib/rails';

import { albumItems } from '../../-lib/play-items';
import { playlistItems } from '../../-lib/playlists';
import { albumQuery, discoverPlaylistQuery, playlistQuery } from '../../-player.api';
import { usePlayTitle } from './play-title';
import { playItems } from './use-player';

export function usePlayCard() {
  const queryClient = useQueryClient();
  const playTitle = usePlayTitle();
  return async (target: NonNullable<RailCard['play']>) => {
    if (target.kind === 'album') {
      const album = await queryClient.fetchQuery(albumQuery(target.id));
      const queue = album ? albumItems(album) : [];
      if (queue.length) playItems(queue);
      return queue.length > 0;
    }
    if (target.kind === 'discover') {
      const list = await queryClient.fetchQuery(discoverPlaylistQuery(target.key));
      const queue = playlistItems(
        { id: 0, name: list.name, songs: list.songs },
        `/player/music/discover/${target.key}`,
      );
      if (queue.length) playItems(queue);
      return queue.length > 0;
    }
    if (target.kind === 'playlist') {
      const queue = playlistItems(await queryClient.fetchQuery(playlistQuery(target.id)));
      if (queue.length) playItems(queue);
      return queue.length > 0;
    }
    await playTitle(target.kind, target.id);
    return true;
  };
}
