import { describe, expect, it } from 'vitest';

import type { DetailSeason } from '../-player.types';

import { channelQueue, episodeItems, guideItem } from './play-items';

const ep = (id: number, n: number, owned = true) => ({ id, episode_number: n, owned });
const seasons: DetailSeason[] = [
  { id: 2, season_number: 2, episodes: [ep(21, 1), ep(22, 2)] },
  { id: 0, season_number: 0, episodes: [ep(1, 1)] },
  { id: 1, season_number: 1, episodes: [ep(12, 2), ep(11, 1), ep(13, 3, false)] },
];

describe('play items', () => {
  it('queues a show in airing order, specials last, skipping what is not on disk', () => {
    const ids = episodeItems({ id: 5, title: 'Show' }, seasons).map((x) => x.id);
    expect(ids).toEqual(['11', '12', '21', '22', '1']);
    const fromMid = episodeItems({ id: 5, title: 'Show' }, seasons, 21).map((x) => x.id);
    expect(fromMid).toEqual(['21', '22', '1']);
    expect(episodeItems({ id: 5, title: 'Show' }, seasons, 21)[0].subtitle).toBe('Show · S2 E1');
  });

  it('turns guide programs into a channel that plays on', () => {
    const prog = (key: string, start: number) => ({
      key,
      title: key,
      seconds: 60,
      start,
      end: start + 60_000,
    });
    const programs = [
      prog('movie:1', 0),
      prog('episode:9', 60_000),
      prog('type:x', 120_000),
      prog('movie:2', 9 * 3_600_000),
    ];
    expect(guideItem(programs[0])).toMatchObject({ type: 'movie', id: '1' });
    expect(guideItem(programs[2])).toBeNull();
    expect(channelQueue(programs, programs[0]).map((x) => x.id)).toEqual(['1', '9']);
  });
});
