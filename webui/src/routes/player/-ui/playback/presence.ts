/**
 * the player and the server, kept in step while it plays:
 *   * the profile's playback settings go to the engine (autoplay the next
 *     episode, when to ask "are you still watching?")
 *   * watching now: while something plays, this screen tells the server
 *     what and where, every 15 seconds and at once when it starts, pauses or
 *     changes; and that it stopped. a profile that doesn't share sends
 *     nothing (and the server wouldn't keep it anyway).
 */

import { useQuery } from '@tanstack/react-query';
import { useEffect, useRef } from 'react';

import { appURL } from '@/platform/url-base';

import type { PlayerState } from '../../-engine/engine';

import { playerSettingsQuery } from '../../-player.api';
import { currentChannel } from './tuner';
import { getEngine, usePlayer } from './use-player';

const BEAT_MS = 15_000;

// one id per page load. sessionStorage was copied when a tab was
// duplicated, so two tabs shared an id and one's goodbye removed the other
const PAGE_SESSION = Math.random().toString(36).slice(2, 12);
function sessionId(): string {
  return PAGE_SESSION;
}

function payload(state: PlayerState, session: string) {
  const item = state.item!;
  return {
    session,
    item_type: item.type,
    item_id: item.id,
    position: Math.max(0, Math.round(state.position)),
    duration: state.duration ? Math.round(state.duration) : null,
    playing:
      state.status === 'playing' || state.status === 'buffering' || state.status === 'loading',
    live: state.live,
    channel: state.live ? currentChannel() : null,
    meta: {
      title: item.title,
      subtitle: item.subtitle,
      artwork: item.artwork ?? undefined,
      backdrop: item.backdrop ?? undefined,
      href: item.href,
      parent_id: item.parentId,
      parent_title: item.parentTitle,
    },
  };
}

function send(method: 'POST' | 'DELETE', body?: unknown, session?: string) {
  const url = appURL(
    method === 'DELETE' ? `/api/player/presence?session=${session ?? ''}` : '/api/player/presence',
  );
  void fetch(url, {
    method,
    headers: body ? { 'content-type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
    keepalive: true,
    credentials: 'same-origin',
  }).catch(() => undefined);
}

export function usePlayerSync() {
  const settings = useQuery(playerSettingsQuery());
  const state = usePlayer();
  const session = useRef(sessionId());
  const sharing = settings.data?.settings.share_activity ?? false;

  useEffect(() => {
    const s = settings.data?.settings;
    if (s)
      getEngine().configure({
        autoplayNext: s.autoplay_next,
        stillWatchingAfter: s.still_watching_after,
      });
  }, [settings.data]);

  const key = state.item ? `${state.item.type}:${state.item.id}` : null;
  const playing = state.status === 'playing' || state.status === 'buffering';
  const active = !!key && state.status !== 'idle' && state.status !== 'error';

  // at once: a start, a pause, a change
  useEffect(() => {
    if (!sharing) return;
    if (active) send('POST', payload(getEngine().getState(), session.current));
    else send('DELETE', undefined, session.current);
  }, [sharing, key, playing, active]);

  // and every 15 seconds while it plays
  useEffect(() => {
    if (!sharing || !active) return;
    const t = window.setInterval(() => {
      const now = getEngine().getState();
      if (now.item) send('POST', payload(now, session.current));
    }, BEAT_MS);
    const gone = () => send('DELETE', undefined, session.current);
    window.addEventListener('pagehide', gone);
    return () => {
      window.clearInterval(t);
      window.removeEventListener('pagehide', gone);
    };
  }, [sharing, active]);
}
