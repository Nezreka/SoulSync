/** the guide grid's window and the channels someone makes, kept per profile on this device */

import type { ChannelRule } from './channels';

export const GUIDE_WINDOW_HOURS = 4;
export const PX_PER_MINUTE = 6;
/** how much of the past the grid shows before the now line */
export const PAST_MINUTES = 30;

/** [from, to) in epoch ms: starts on the half hour before now minus a little */
export function guideWindow(now: number): { from: number; to: number } {
  const halfHour = 30 * 60 * 1000;
  const from = Math.floor((now - PAST_MINUTES * 60 * 1000) / halfHour) * halfHour;
  return { from, to: from + GUIDE_WINDOW_HOURS * 60 * 60 * 1000 };
}

const RULES_KEY = (profileId: number) => `ss-player-channels:${profileId}`;
const MAX_RULES = 30;

function isRule(value: unknown): value is ChannelRule {
  if (!value || typeof value !== 'object') return false;
  const r = value as Record<string, unknown>;
  return (
    typeof r.id === 'string' &&
    typeof r.name === 'string' &&
    Array.isArray(r.kinds) &&
    r.kinds.every((k) => k === 'movies' || k === 'tv')
  );
}

export function parseRules(raw: string | null): ChannelRule[] {
  if (!raw) return [];
  try {
    const value: unknown = JSON.parse(raw);
    return Array.isArray(value) ? value.filter(isRule).slice(0, MAX_RULES) : [];
  } catch {
    return [];
  }
}

export function loadRules(profileId: number): ChannelRule[] {
  try {
    return parseRules(localStorage.getItem(RULES_KEY(profileId)));
  } catch {
    return [];
  }
}

export function saveRules(profileId: number, rules: ChannelRule[]) {
  try {
    localStorage.setItem(RULES_KEY(profileId), JSON.stringify(rules.slice(0, MAX_RULES)));
  } catch {
    /* private mode: the channel lasts until the page closes */
  }
}
