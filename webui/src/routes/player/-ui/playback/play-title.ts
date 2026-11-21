/**
 * play a movie or a show from anywhere a card or banner names one (the home
 * billboard, a hover preview): a movie plays its best file; a show plays
 * from the episode up next, the rest of the show queued after it.
 */

import { useQueryClient } from '@tanstack/react-query';

import { episodeItems, movieItem } from '../../-lib/play-items';
import { movieDetailQuery, showDetailQuery } from '../../-player.api';
import { playItems } from './use-player';

export function usePlayTitle() {
  const queryClient = useQueryClient();
  /** ``from``: a show from this episode on (a continue-watching row), else the one up next */
  return async (kind: 'movie' | 'show', id: number, from?: number) => {
    if (kind === 'movie') {
      const m = await queryClient.fetchQuery(movieDetailQuery(id));
      playItems([movieItem({ id, title: m.title, year: m.year })]);
      return;
    }
    const show = await queryClient.fetchQuery(showDetailQuery(id));
    const seasons = show.seasons ?? [];
    const next = show.next_up;
    const start = from
      ? from
      : next
        ? seasons
            .find((x) => x.season_number === next.season_number)
            ?.episodes.find((e) => e.episode_number === next.episode_number && e.owned !== false)
            ?.id
        : undefined;
    const queue = episodeItems({ id, title: show.title }, seasons, start);
    if (queue.length) playItems(queue);
  };
}
