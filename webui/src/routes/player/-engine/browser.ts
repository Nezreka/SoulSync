/**
 * the engine's connections to the browser and the server: real media
 * elements, the playable and progress routes, localStorage and the
 * lock screen. everything the engine needs from outside is here, in one
 * place, so the engine itself stays testable.
 */

import { apiClient } from '@/app/api-client';
import { appURL } from '@/platform/url-base';

import type { EngineDeps, Playable, Quality, SavedPosition, TrackPick } from './engine';
import type { Tracks } from './tracks';
import type { MediaKind, PlayItem } from './types';

import { progressPayload } from '../-lib/progress';

const STORE_PREFIX = 'ss-player:';
const PLAYABLE_TIMEOUT_MS = 90_000;

function createMedia(kind: MediaKind): HTMLMediaElement {
  const el = document.createElement(kind);
  if (el instanceof HTMLVideoElement) {
    el.playsInline = true;
    el.disablePictureInPicture = false;
  }
  return el;
}

async function playable(item: PlayItem, quality: Quality, pick: TrackPick): Promise<Playable> {
  if (item.type === 'podcast_episode') {
    if (!item.url) throw new Error('This episode has no audio address.');
    // podcast audio lives on the feed's host: the podcast side's checked proxy fetches it
    return {
      url: appURL(`/api/podcasts/audio-proxy?url=${encodeURIComponent(item.url)}`),
      duration: item.duration ?? null,
    };
  }
  const res = await apiClient.get(`player/playable/${item.type}/${encodeURIComponent(item.id)}`, {
    searchParams: {
      ...(item.part ? { part: item.part } : {}),
      ...(quality !== 'original' ? { q: quality } : {}),
      ...(pick.audio ? { audio: pick.audio } : {}),
      ...(pick.burn !== null ? { burn: pick.burn } : {}),
      ...(pick.delay ? { delay: pick.delay } : {}),
    },
    throwHttpErrors: false,
    // finding the file can mean a sleeping drive spinning up (half a minute is normal)
    timeout: PLAYABLE_TIMEOUT_MS,
  });
  const body = (await res.json().catch(() => ({}))) as Partial<Playable> & {
    success?: boolean;
    message?: string;
  };
  if (!res.ok || !body.success || !body.url) {
    throw new Error(body.message || "This can't be played right now.");
  }
  return {
    ...body,
    url: appURL(body.url),
    fallback_url: body.fallback_url ? appURL(body.fallback_url) : undefined,
  } as Playable;
}

async function progress(item: PlayItem): Promise<SavedPosition | null> {
  const body = await apiClient
    .get('player/progress', { searchParams: { item_type: item.type, item_id: item.id } })
    .json<{ progress?: { position_seconds: number; finished: boolean } | null }>();
  const row = body.progress;
  return row ? { position: row.position_seconds, finished: row.finished } : null;
}

async function tracks(item: PlayItem): Promise<Tracks> {
  const body = await apiClient
    .get(`player/tracks/${item.type}/${encodeURIComponent(item.id)}`, {
      searchParams: item.part ? { part: item.part } : {},
      timeout: 90_000,
    })
    .json<Partial<Tracks>>();
  return { audio: body.audio ?? [], subtitles: body.subtitles ?? [] };
}

async function subtitle(item: PlayItem, id: string): Promise<string> {
  // an embedded subtitle is read out of the whole file the first time: give it time
  return await apiClient
    .get(`player/subtitle/${item.type}/${encodeURIComponent(item.id)}`, {
      searchParams: { sub: id, ...(item.part ? { part: item.part } : {}) },
      timeout: 600_000,
    })
    .text();
}

/** where a converted stream (copied video) really starts if asked for ``t`` */
async function transcodeStart(url: string, t: number): Promise<{ start: number; seek: number }> {
  const [path, query] = url.split('?');
  const res = await fetch(`${path}/start?${query ? `${query}&` : ''}t=${t}`, {
    credentials: 'same-origin',
  });
  const body = (await res.json()) as { start?: number; seek?: number };
  return { start: body.start ?? t, seek: body.seek ?? t };
}

/** into the credits: it counts as watched (fire and forget) */
function watched(item: PlayItem) {
  void fetch(appURL('/api/player/watched'), {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ item_type: item.type, item_id: item.id }),
    keepalive: true,
    credentials: 'same-origin',
  }).catch(() => undefined);
}

/** fire and forget, and still sent when the page is closing (keepalive) */
function save(item: PlayItem, position: number, duration: number | null, finished: boolean) {
  const meta = {
    title: item.title,
    subtitle: item.subtitle,
    artwork: item.artwork ?? undefined,
    backdrop: item.backdrop ?? undefined,
    href: item.href,
    parent_id: item.parentId,
    parent_title: item.parentTitle,
  };
  if (finished) {
    void fetch(appURL('/api/player/progress/finish'), {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ item_type: item.type, item_id: item.id, meta }),
      keepalive: true,
      credentials: 'same-origin',
    }).catch(() => undefined);
    return;
  }
  const payload = progressPayload(item.type, item.id, position, duration, meta);
  if (!payload) return;
  void fetch(appURL('/api/player/progress'), {
    method: 'PUT',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(payload),
    keepalive: true,
    credentials: 'same-origin',
  }).catch(() => undefined);
}

const store = {
  get(key: string) {
    try {
      return localStorage.getItem(STORE_PREFIX + key);
    } catch {
      return null;
    }
  },
  set(key: string, value: string) {
    try {
      localStorage.setItem(STORE_PREFIX + key, value);
    } catch {
      /* private window: settings last the visit */
    }
  },
};

/**
 * past full volume: the element's sound through a web audio gain. made the
 * first time it's needed (a person turning it up, so the audio context may
 * start) and kept, since an element can be wired to only one.
 */
const gains = new WeakMap<HTMLMediaElement, { ctx: AudioContext; gain: GainNode }>();

function boost(el: HTMLMediaElement, gain: number) {
  let node = gains.get(el);
  if (!node) {
    if (gain <= 1 || typeof AudioContext === 'undefined') return;
    try {
      const ctx = new AudioContext();
      const g = ctx.createGain();
      ctx.createMediaElementSource(el).connect(g).connect(ctx.destination);
      node = { ctx, gain: g };
      gains.set(el, node);
    } catch {
      return; // no web audio: it stops at full volume
    }
  }
  if (node.ctx.state === 'suspended') void node.ctx.resume();
  node.gain.gain.value = gain;
}

export function browserDeps(): EngineDeps {
  return {
    createMedia,
    playable,
    progress,
    transcodeStart,
    boost,
    tracks,
    subtitle,
    save,
    watched,
    store,
    now: () => Date.now(),
    mediaSession:
      typeof navigator !== 'undefined' && 'mediaSession' in navigator
        ? navigator.mediaSession
        : null,
    setInterval: (fn, ms) => window.setInterval(fn, ms),
    clearInterval: (h) => window.clearInterval(h as number),
  };
}
