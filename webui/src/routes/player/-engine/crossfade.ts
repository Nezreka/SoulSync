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

import type { PlayItem } from './types';

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
const blobCache = new Map<string, string>();
const inflight = new Set<string>();

export function getCachedTrackBlobUrl(trackId: string): string | null {
  const hit = blobCache.get(trackId);
  if (!hit) return null;
  // Refresh LRU order
  blobCache.delete(trackId);
  blobCache.set(trackId, hit);
  return hit;
}

export function prefetchUpcomingTracks(items: readonly PlayItem[], currentIndex: number) {
  if (typeof window === 'undefined' || typeof fetch !== 'function') return;
  const count = getPrefetchCount();
  if (count <= 0 || currentIndex < 0) return;
  const upcoming = items
    .slice(currentIndex + 1, currentIndex + 1 + count)
    .filter((it) => it.type === 'track' && !blobCache.has(it.id) && !inflight.has(it.id));

  for (const track of upcoming) {
    inflight.add(track.id);
    const url = appURL(`/api/player/stream/track/${encodeURIComponent(track.id)}`);
    void fetch(url, { credentials: 'same-origin' })
      .then((res) => (res.ok ? res.blob() : null))
      .then((blob) => {
        if (!blob) return;
        const objectUrl = URL.createObjectURL(blob);
        blobCache.set(track.id, objectUrl);
        while (blobCache.size > MAX_CACHED_BLOBS) {
          const oldestKey = blobCache.keys().next().value;
          if (oldestKey === undefined) break;
          const oldestUrl = blobCache.get(oldestKey);
          blobCache.delete(oldestKey);
          if (oldestUrl) URL.revokeObjectURL(oldestUrl);
        }
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
export function startEqualPowerCrossfade(
  primaryEl: HTMLMediaElement,
  durationSec: number,
  targetVolume: number,
): () => void {
  if (typeof document === 'undefined' || durationSec <= 0) return () => undefined;
  const clampedVol = Math.max(0, Math.min(1, targetVolume));
  const outEl = document.createElement('audio');
  outEl.src = primaryEl.currentSrc || primaryEl.src;
  outEl.currentTime = primaryEl.currentTime;
  outEl.playbackRate = primaryEl.playbackRate;
  outEl.volume = clampedVol;
  void outEl.play().catch(() => undefined);

  primaryEl.volume = 0;
  const startedAt = Date.now();
  const totalMs = Math.max(400, durationSec * 1000);
  const timer = window.setInterval(() => {
    const t = Math.min(1, (Date.now() - startedAt) / totalMs);
    const outGain = Math.cos((t * Math.PI) / 2);
    const inGain = Math.sin((t * Math.PI) / 2);
    outEl.volume = Math.max(0, Math.min(1, outGain * clampedVol));
    primaryEl.volume = Math.max(0, Math.min(1, inGain * clampedVol));
    if (t >= 1) {
      window.clearInterval(timer);
      outEl.pause();
      outEl.removeAttribute('src');
      outEl.load();
    }
  }, 50);

  return () => {
    window.clearInterval(timer);
    outEl.pause();
    outEl.removeAttribute('src');
    outEl.load();
    primaryEl.volume = clampedVol;
  };
}
