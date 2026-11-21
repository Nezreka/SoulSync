/**
 * the engine's connections to the browser and the server: real media
 * elements, the playable and progress routes, localStorage and the
 * lock screen. everything the engine needs from outside is here, in one
 * place, so the engine itself stays testable.
 */

import { apiClient } from '@/app/api-client';
import { appURL } from '@/platform/url-base';

import type { EngineDeps, Playable, SavedPosition } from './engine';
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

async function playable(item: PlayItem): Promise<Playable> {
  if (item.type === 'podcast_episode') {
    if (!item.url) throw new Error('This episode has no audio address.');
    // podcast audio lives on the feed's host: the podcast side's checked proxy fetches it
    return {
      url: appURL(`/api/podcasts/audio-proxy?url=${encodeURIComponent(item.url)}`),
      duration: item.duration ?? null,
    };
  }
  const res = await apiClient.get(`player/playable/${item.type}/${encodeURIComponent(item.id)}`, {
    searchParams: item.part ? { part: item.part } : {},
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
  return { ...body, url: appURL(body.url) } as Playable;
}

async function progress(item: PlayItem): Promise<SavedPosition | null> {
  const body = await apiClient
    .get('player/progress', { searchParams: { item_type: item.type, item_id: item.id } })
    .json<{ progress?: { position_seconds: number; finished: boolean } | null }>();
  const row = body.progress;
  return row ? { position: row.position_seconds, finished: row.finished } : null;
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

export function browserDeps(): EngineDeps {
  return {
    createMedia,
    playable,
    progress,
    save,
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
