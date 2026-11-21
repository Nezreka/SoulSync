/**
 * what is playing, for the shell's transport bar. nothing plays until the
 * player core lands (stage b), and the bar only shows while something does:
 * an idle bar of dead buttons is clutter, not a feature.
 */

export interface NowPlaying {
  title: string;
  subtitle?: string;
  art?: string | null;
}

/** stage b replaces this with the player core's state */
export function useNowPlaying(): NowPlaying | null {
  return null;
}
