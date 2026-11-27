/**
 * the player and the server, kept in step while it plays:
 *   * the profile's playback settings go to the engine (autoplay the next
 *     episode, when to ask "are you still watching?")
 *   * watching now: while something plays, this screen tells the server
 *     what, where and how it plays, every 8 seconds and at once when it
 *     starts, pauses or changes; and that it stopped. every screen says so:
 *     the admin's Server Activity sees every stream, while other members see
 *     only profiles that share (the server keeps that line).
 *   * the answer carries what the admin said from Server Activity: a stop
 *     (playback stops and the admin's words show) or a message (shown, still
 *     playing). a stop lands within one beat, so within 8 seconds.
 */

import { useQuery } from '@tanstack/react-query';
import { useEffect, useRef } from 'react';

import { appURL } from '@/platform/url-base';

import type { PlayerState } from '../../-engine/engine';

import { screenId } from '../../-engine/browser';
import { levelPrefsFrom } from '../../-engine/loudness';
import { playerSettingsQuery } from '../../-player.api';
import { showServerOrder } from './server-orders';
import { currentChannel } from './tuner';
import { getEngine, usePlayer } from './use-player';

export const BEAT_MS = 8_000;

/** how it plays, once the server has said where the bytes come from */
function stream(state: PlayerState) {
  if (!state.source) return undefined;
  return {
    via: state.source.via,
    file_id: state.source.fileId ?? undefined,
    mime: state.source.mime ?? undefined,
    transcode: state.transcode
      ? {
          video: state.transcode.video,
          audio: state.transcode.audio,
          quality: state.transcode.quality,
          reasons: state.transcode.reasons,
          encoder: state.transcode.encoder ?? null,
        }
      : null,
  };
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
    stream: stream(state),
  };
}

export interface BeatAnswer {
  stop?: { message?: string };
  message?: { header?: string; text?: string };
}

const STOPPED = 'The server administrator stopped this stream.';

/** the admin's orders for this screen: stop and say why, or just say */
export function actOn(answer: BeatAnswer | null | undefined) {
  if (!answer) return;
  if (answer.stop) {
    getEngine().stop();
    showServerOrder({
      kind: 'stop',
      title: 'Stream stopped',
      text: answer.stop.message?.trim() || STOPPED,
    });
    return;
  }
  const text = answer.message?.text?.trim();
  if (text) {
    showServerOrder({
      kind: 'message',
      title: answer.message?.header?.trim() || 'A message from the server',
      text,
    });
  }
}

function beat(body: unknown) {
  void fetch(appURL('/api/player/presence'), {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
    keepalive: true,
    credentials: 'same-origin',
  })
    .then((res) => (res.ok ? (res.json() as Promise<BeatAnswer>) : null))
    .then(actOn)
    .catch(() => undefined);
}

function goodbye(session: string) {
  void fetch(appURL(`/api/player/presence?session=${encodeURIComponent(session)}`), {
    method: 'DELETE',
    keepalive: true,
    credentials: 'same-origin',
  }).catch(() => undefined);
}

export function usePlayerSync() {
  const settings = useQuery(playerSettingsQuery());
  const state = usePlayer();
  // one id per page load, the same one the converter knows this screen by,
  // so an admin's stop also ends its conversion
  const session = useRef(screenId());

  useEffect(() => {
    const s = settings.data?.settings;
    if (s) {
      getEngine().configure({
        autoplayNext: s.autoplay_next,
        stillWatchingAfter: s.still_watching_after,
      });
      getEngine().configureLevelling(levelPrefsFrom(s));
    }
  }, [settings.data]);

  const key = state.item ? `${state.item.type}:${state.item.id}` : null;
  const playing = state.status === 'playing' || state.status === 'buffering';
  const active = !!key && state.status !== 'idle' && state.status !== 'error';
  const transcoding = state.transcode ? `${state.transcode.video}:${state.transcode.audio}` : '';

  // at once: a start, a pause, a change (of item, or of how it plays)
  useEffect(() => {
    if (active) beat(payload(getEngine().getState(), session.current));
    else goodbye(session.current);
  }, [key, playing, active, transcoding]);

  // and every few seconds while it plays
  useEffect(() => {
    if (!active) return;
    const t = window.setInterval(() => {
      const now = getEngine().getState();
      if (now.item) beat(payload(now, session.current));
    }, BEAT_MS);
    const gone = () => goodbye(session.current);
    window.addEventListener('pagehide', gone);
    return () => {
      window.clearInterval(t);
      window.removeEventListener('pagehide', gone);
    };
  }, [active]);
}
