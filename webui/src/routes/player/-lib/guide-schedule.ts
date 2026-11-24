/**
 * the one way a guide channel's schedule is asked for. the guide, the home
 * "live tv" rail and the tuner all show what's airing; built three ways
 * (home left the commercials out), home said one thing was on and the
 * guide another.
 */

import type { CommercialScheduleConfig, CommercialSpot, ScheduleOptions } from './schedule';

import { startOfLocalDay } from './schedule';

export interface CommercialsShape {
  enabled: boolean;
  era: string;
  mode: string;
  clips?: readonly CommercialSpot[] | null;
}

/** the server's commercials, as the schedule takes them (undefined: none) */
export function sharedCommercials(
  cfg: CommercialsShape | null | undefined,
): CommercialScheduleConfig | undefined {
  if (!cfg?.enabled || !cfg.clips?.length) return undefined;
  const clips = cfg.clips;
  const key = `1:${cfg.era}:${cfg.mode}:${clips.length}:${clips[0]?.id ?? ''}:${clips[clips.length - 1]?.id ?? ''}`;
  return { enabled: true, era: cfg.era, mode: cfg.mode, clips, key };
}

/** a video channel's shared broadcast options for the day ``today`` is in */
export function guideScheduleOptions(
  today: number,
  commercials: CommercialScheduleConfig | undefined,
): ScheduleOptions {
  return {
    daypart: true,
    blocks: 1,
    rotateShows: true,
    today: startOfLocalDay(today),
    commercials,
  };
}
