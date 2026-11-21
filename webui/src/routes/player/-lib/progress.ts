/**
 * the body the player sends to PUT /api/player/progress. the server checks
 * the same things; shaping it here keeps junk (NaN from a media element that
 * hasn't loaded, image-cache links that expire) from ever being sent.
 */

import type { ProgressMeta } from '../-player.types';

export interface ProgressPayload {
  item_type: string;
  item_id: string;
  position_seconds: number;
  duration_seconds?: number;
  meta?: ProgressMeta;
}

const ITEM_TYPE = /^[a-z][a-z0-9_]{0,31}$/;
const META_KEYS: (keyof ProgressMeta)[] = [
  'title',
  'subtitle',
  'artwork',
  'backdrop',
  'href',
  'parent_id',
  'parent_title',
];

/** a media element reports NaN or Infinity before it knows; those are not times */
export function seconds(value: number | null | undefined): number | undefined {
  if (value === null || value === undefined || !Number.isFinite(value) || value < 0)
    return undefined;
  return Math.round(value * 10) / 10;
}

export function cleanMeta(meta: ProgressMeta | null | undefined): ProgressMeta | undefined {
  if (!meta) return undefined;
  const out: ProgressMeta = {};
  for (const key of META_KEYS) {
    const value = meta[key];
    if (typeof value !== 'string') continue;
    const text = value.trim().slice(0, 500);
    if (!text) continue;
    if ((key === 'artwork' || key === 'backdrop') && text.includes('/api/image-cache')) continue;
    out[key] = text;
  }
  return Object.keys(out).length ? out : undefined;
}

export function progressPayload(
  itemType: string,
  itemId: string | number,
  position: number | null | undefined,
  duration?: number | null,
  meta?: ProgressMeta | null,
): ProgressPayload | null {
  const type = itemType.trim().toLowerCase();
  const id = String(itemId ?? '').trim();
  const pos = seconds(position);
  if (!ITEM_TYPE.test(type) || !id || pos === undefined) return null;
  const dur = seconds(duration ?? undefined);
  const payload: ProgressPayload = {
    item_type: type,
    item_id: id,
    position_seconds: dur ? Math.min(pos, dur) : pos,
  };
  if (dur) payload.duration_seconds = dur;
  const m = cleanMeta(meta);
  if (m) payload.meta = m;
  return payload;
}
