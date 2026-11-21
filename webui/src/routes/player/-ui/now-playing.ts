/**
 * what is playing, for the shell: the transport bar's row is there only
 * while something is (an idle bar of dead buttons is clutter).
 */

import { current } from '../-engine/queue';
import { usePlayer } from './playback/use-player';

export interface NowPlaying {
  title: string;
  subtitle?: string;
}

export function useNowPlaying(): NowPlaying | null {
  const item = current(usePlayer().queue);
  return item ? { title: item.title, subtitle: item.subtitle } : null;
}
