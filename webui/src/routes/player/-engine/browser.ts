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
import { getCachedTrackBlobUrl } from './crossfade';

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

/**
 * this screen, to the converter: a new conversion from it stops the one it
 * was watching before (hopping channels filled every slot, and the next one
 * came up black)
 */
const SCREEN = (() => {
  try {
    return crypto.randomUUID();
  } catch {
    return `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
  }
})();

/** this screen's id: the converter's owner, and its watching-now session */
export function screenId(): string {
  return SCREEN;
}

function tagScreen(url: string): string {
  if (!url.includes('/api/player/transcode/')) return url;
  return `${url}${url.includes('?') ? '&' : '?'}ps=${SCREEN}`;
}

async function playable(item: PlayItem, quality: Quality, pick: TrackPick): Promise<Playable> {
  if (item.type === 'track') {
    const cachedBlob = getCachedTrackBlobUrl(item.id);
    if (cachedBlob) {
      return { url: cachedBlob, duration: item.duration ?? null, via: 'cache' };
    }
  }
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
    url: tagScreen(appURL(body.url)),
    fallback_url: body.fallback_url ? tagScreen(appURL(body.fallback_url)) : undefined,
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
  // a probe that never answers would leave the player on "resuming" for good
  const res = await fetch(`${path}/start?${query ? `${query}&` : ''}t=${t}`, {
    credentials: 'same-origin',
    signal: AbortSignal.timeout(10_000),
  });
  if (!res.ok) throw new Error(`start probe ${res.status}`);
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
interface Graph {
  ctx: AudioContext;
  gain: GainNode;
  analyser: AnalyserNode;
}
const graphs = new WeakMap<HTMLMediaElement, Graph>();

/**
 * an element's web audio: source, gain (volume past full), and an analyser
 * on the side (what the music visuals read). made once per element, the
 * first time something needs it: an element can be wired to only one.
 * made from a click (turning it up, opening the music view), so the audio
 * context is allowed to start.
 */
function graph(el: HTMLMediaElement): Graph | null {
  const have = graphs.get(el);
  if (have) {
    if (have.ctx.state === 'suspended') void have.ctx.resume();
    return have;
  }
  if (typeof AudioContext === 'undefined') return null;
  try {
    const ctx = new AudioContext();
    const gain = ctx.createGain();
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 2048;
    analyser.smoothingTimeConstant = 0.6;
    ctx.createMediaElementSource(el).connect(gain).connect(ctx.destination);
    gain.connect(analyser);
    const made = { ctx, gain, analyser };
    graphs.set(el, made);
    if (ctx.state === 'suspended') void ctx.resume();
    // a context the browser held back plays nothing: start it on the next play
    el.addEventListener('play', () => {
      if (ctx.state === 'suspended') void ctx.resume();
    });
    return made;
  } catch {
    return null; // no web audio: no boost, no visuals that follow the music
  }
}

function boost(el: HTMLMediaElement, gain: number) {
  if (gain <= 1 && !graphs.has(el)) return;
  const g = graph(el);
  if (g) g.gain.gain.value = gain;
}

function analyser(el: HTMLMediaElement): AnalyserNode | null {
  return graph(el)?.analyser ?? null;
}

/** a song heard here goes into the listening history, as a play from the old
 *  web player does: the same endpoint, so one pile, one set of rules */
async function listened(item: PlayItem, duration: number | null): Promise<number | null> {
  const numeric = /^\d+$/.test(item.id);
  const out = await apiClient
    .post('library/log-play', {
      json: {
        track: {
          id: item.id,
          lib2_track_id: numeric ? Number(item.id) : null,
          title: item.title,
          artist: item.subtitle ?? '',
          album: item.parentTitle ?? '',
        },
        duration_ms: duration ? Math.round(duration * 1000) : 0,
      },
    })
    .json<{ play_id?: number | null }>();
  return typeof out.play_id === 'number' ? out.play_id : null;
}

function counted(playId: number) {
  void apiClient.post('library/log-play/eligible', { json: { play_id: playId } }).catch(() => {});
}

export function browserDeps(): EngineDeps {
  return {
    createMedia,
    playable,
    progress,
    transcodeStart,
    boost,
    analyser,
    tracks,
    subtitle,
    save,
    watched,
    listened,
    counted,
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
