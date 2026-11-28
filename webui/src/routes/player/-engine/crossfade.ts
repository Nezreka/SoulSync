/**
 * Plexamp-grade music transitions & lookahead pre-caching for Desktop /player:
 * - Equal-power (cos/sin) crossfade between tracks so there is no mid-fade volume dip
 * - Smart album gapless: automatically disables crossfade for consecutive tracks
 *   from the same album so concept & live albums play seamlessly
 * - Lookahead pre-cache: silently fetches the next N queued tracks into a bounded
 *   Blob URL ring buffer so track transitions and crossfades start in 0ms
 */

import { useSyncExternalStore } from 'react';

import { appURL } from '@/platform/url-base';

import type { Loudness } from './loudness';
import type { PlayItem } from './types';

import { loudnessFromHeaders } from './loudness';

export const CROSSFADE_OPTIONS = [0, 2, 4, 6, 8, 10, 12] as const;
export const PREFETCH_OPTIONS = [0, 3, 5, 10] as const;

const KEY_CROSSFADE = 'ss-player:crossfade_sec';
const KEY_SMART_GAPLESS = 'ss-player:smart_gapless';
const KEY_PREFETCH = 'ss-player:prefetch_count';

const listeners = new Set<() => void>();
function emit() {
  for (const fn of listeners) fn();
}
function subscribe(fn: () => void) {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

export function getCrossfadeSeconds(): number {
  try {
    const raw = localStorage.getItem(KEY_CROSSFADE);
    if (raw === null) return 4;
    const n = Number(raw);
    return Number.isFinite(n) && n >= 0 && n <= 12 ? n : 4;
  } catch {
    return 4;
  }
}

export function setCrossfadeSeconds(sec: number) {
  try {
    localStorage.setItem(KEY_CROSSFADE, String(Math.max(0, Math.min(12, Math.round(sec)))));
  } catch {
    /* private mode */
  }
  emit();
}

export function getSmartGapless(): boolean {
  try {
    const raw = localStorage.getItem(KEY_SMART_GAPLESS);
    return raw === null ? true : raw === '1';
  } catch {
    return true;
  }
}

export function setSmartGapless(on: boolean) {
  try {
    localStorage.setItem(KEY_SMART_GAPLESS, on ? '1' : '0');
  } catch {
    /* private mode */
  }
  emit();
}

export function getPrefetchCount(): number {
  try {
    const raw = localStorage.getItem(KEY_PREFETCH);
    if (raw === null) return 5;
    const n = Number(raw);
    return Number.isFinite(n) && n >= 0 && n <= 10 ? n : 5;
  } catch {
    return 5;
  }
}

export function setPrefetchCount(count: number) {
  try {
    localStorage.setItem(KEY_PREFETCH, String(Math.max(0, Math.min(10, Math.round(count)))));
  } catch {
    /* private mode */
  }
  emit();
}

export function useCrossfadeSeconds(): number {
  return useSyncExternalStore(subscribe, getCrossfadeSeconds, () => 4);
}

export function useSmartGapless(): boolean {
  return useSyncExternalStore(subscribe, getSmartGapless, () => true);
}

export function usePrefetchCount(): number {
  return useSyncExternalStore(subscribe, getPrefetchCount, () => 5);
}

/** Smart album rule: pure gapless between consecutive tracks on the same album. */
export function shouldCrossfade(
  curr: PlayItem | null | undefined,
  next: PlayItem | null | undefined,
  crossfadeSec = getCrossfadeSeconds(),
  smartGapless = getSmartGapless(),
): boolean {
  if (crossfadeSec <= 0 || !curr || !next) return false;
  if (curr.type !== 'track' || next.type !== 'track') return false;
  if (curr.id === next.id) return false;
  if (smartGapless && curr.parentId && next.parentId && curr.parentId === next.parentId) {
    return false;
  }
  return true;
}

// ── Lookahead Track Blob Pre-Cache ───────────────────────────────────────────

const MAX_CACHED_BLOBS = 12;
// whole files held in page memory: hi-res flac runs 50-150 MB a track, and
// a count alone let the cache reach a gigabyte. a byte budget too, and one
// file over the per-track limit isn't kept (it streams as usual)
const MAX_CACHED_BYTES = 200 * 1024 * 1024;
const MAX_TRACK_BYTES = 80 * 1024 * 1024;
const blobCache = new Map<string, string>();
const blobBytes = new Map<string, number>();
/** each cached song's loudness, from its response headers */
const blobLoudness = new Map<string, Loudness>();
const inflight = new Set<string>();

function cachedBytes(): number {
  let total = 0;
  for (const n of blobBytes.values()) total += n;
  return total;
}

function evictOldest() {
  const oldestKey = blobCache.keys().next().value;
  if (oldestKey === undefined) return false;
  const oldestUrl = blobCache.get(oldestKey);
  blobCache.delete(oldestKey);
  blobBytes.delete(oldestKey);
  blobLoudness.delete(oldestKey);
  if (oldestUrl) URL.revokeObjectURL(oldestUrl);
  return true;
}

/** the browser says data is metered or saving: don't fetch ahead */
function savingData(): boolean {
  const c = (navigator as Navigator & { connection?: { saveData?: boolean } }).connection;
  return !!c?.saveData;
}

export function getCachedTrackBlobUrl(trackId: string): string | null {
  const hit = blobCache.get(trackId);
  if (!hit) return null;
  // Refresh LRU order
  blobCache.delete(trackId);
  blobCache.set(trackId, hit);
  return hit;
}

/** a pre-cached song's loudness, when its bytes brought it */
export function getCachedTrackLoudness(trackId: string): Loudness | null {
  return blobLoudness.get(trackId) ?? null;
}

export function prefetchUpcomingTracks(items: readonly PlayItem[], currentIndex: number) {
  if (typeof window === 'undefined' || typeof fetch !== 'function') return;
  const count = getPrefetchCount();
  if (count <= 0 || currentIndex < 0 || savingData()) return;
  const upcoming = items
    .slice(currentIndex + 1, currentIndex + 1 + count)
    .filter((it) => it.type === 'track' && !blobCache.has(it.id) && !inflight.has(it.id));

  for (const track of upcoming) {
    inflight.add(track.id);
    const url = appURL(`/api/player/stream/track/${encodeURIComponent(track.id)}`);
    let loud: Loudness | null = null;
    void fetch(url, { credentials: 'same-origin' })
      .then((res) => {
        if (!res.ok) return null;
        loud = loudnessFromHeaders(res.headers);
        return res.blob();
      })
      .then((blob) => {
        if (!blob || blob.size > MAX_TRACK_BYTES) return;
        const objectUrl = URL.createObjectURL(blob);
        blobCache.set(track.id, objectUrl);
        blobBytes.set(track.id, blob.size);
        if (loud) blobLoudness.set(track.id, loud);
        while (
          (blobCache.size > MAX_CACHED_BLOBS || cachedBytes() > MAX_CACHED_BYTES) &&
          evictOldest()
        );
      })
      .catch(() => undefined)
      .finally(() => {
        inflight.delete(track.id);
      });
  }
}

/**
 * Spawns a detached fade-out clone of the currently playing audio element and
 * smoothly crossfades out (equal-power cosine curve) while ramping the primary
 * incoming deck in (equal-power sine curve).
 */
export interface CrossfadeLevels {
  /** the outgoing song's levelling (linear): its tail keeps the level it played at */
  outLevel?: number;
  /** level the outgoing copy through web audio (a boost); null: it can't be */
  levelOut?: (el: HTMLMediaElement, linear: number) => (() => void) | null;
  /** the incoming element's full volume, read as it fades in (it may change mid-fade) */
  inVolume?: () => number;
}

export function startEqualPowerCrossfade(
  primaryEl: HTMLMediaElement,
  durationSec: number,
  targetVolume: number,
  levels: CrossfadeLevels = {},
): () => void {
  if (typeof document === 'undefined' || durationSec <= 0) return () => undefined;
  const clampedVol = Math.max(0, Math.min(1, targetVolume));
  const outLevel = levels.outLevel ?? 1;
  const outEl = document.createElement('audio');
  outEl.src = primaryEl.currentSrc || primaryEl.src;
  outEl.currentTime = primaryEl.currentTime;
  outEl.playbackRate = primaryEl.playbackRate;
  // the tail plays at the level the song had: turned down on the element,
  // turned up through web audio when it can be
  const unwire = outLevel > 1 ? (levels.levelOut?.(outEl, outLevel) ?? null) : null;
  const outVol = clampedVol * (unwire ? 1 : Math.min(1, outLevel));
  outEl.volume = outVol;
  void outEl.play().catch(() => undefined);
  const inVolume = () => Math.max(0, Math.min(1, levels.inVolume?.() ?? clampedVol));

  primaryEl.volume = 0;
  const startedAt = Date.now();
  const totalMs = Math.max(400, durationSec * 1000);
  const release = () => {
    outEl.pause();
    outEl.removeAttribute('src');
    outEl.load();
    unwire?.();
  };
  const timer = window.setInterval(() => {
    const t = Math.min(1, (Date.now() - startedAt) / totalMs);
    const outGain = Math.cos((t * Math.PI) / 2);
    const inGain = Math.sin((t * Math.PI) / 2);
    outEl.volume = Math.max(0, Math.min(1, outGain * outVol));
    primaryEl.volume = Math.max(0, Math.min(1, inGain * inVolume()));
    if (t >= 1) {
      window.clearInterval(timer);
      release();
    }
  }, 50);

  return () => {
    window.clearInterval(timer);
    release();
    primaryEl.volume = inVolume();
  };
}
