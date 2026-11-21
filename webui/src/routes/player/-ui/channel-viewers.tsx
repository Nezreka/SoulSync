/**
 * who else is on a channel: their faces on its row in the guide, a marker
 * where they are in what's on, and in the panel, join them where they are,
 * watch it live, or start it over. from watching now (people who share).
 */

import { useQuery } from '@tanstack/react-query';
import { useMemo } from 'react';

import type { Channel } from '../-lib/channels';
import type { GuideProgram } from '../-lib/schedule';
import type { WatchingSession } from '../-player.api';

import { clock } from '../-engine/timeline';
import { remoteImage } from '../-lib/images';
import { channelQueue } from '../-lib/play-items';
import { watchingNowQuery } from '../-player.api';
import v from './channel-viewers.module.css';
import { styles as k } from './kit';
import { playItems } from './playback/use-player';
import so from './social.module.css';

/** sessions on a channel, by channel id */
export function useChannelViewers(): Map<string, WatchingSession[]> {
  const q = useQuery(watchingNowQuery());
  return useMemo(() => {
    const out = new Map<string, WatchingSession[]>();
    for (const x of q.data?.sessions ?? []) {
      if (!x.channel) continue;
      const list = out.get(x.channel.id);
      if (list) list.push(x);
      else out.set(x.channel.id, [x]);
    }
    return out;
  }, [q.data]);
}

export function Avatar({ profile }: { profile: WatchingSession['profile'] }) {
  return (
    <span
      className={so.avatar}
      style={profile.avatar_color ? { background: profile.avatar_color } : undefined}
      aria-hidden="true"
    >
      {profile.avatar_url ? (
        <img src={remoteImage(profile.avatar_url) ?? undefined} alt="" />
      ) : (
        profile.name.charAt(0).toUpperCase()
      )}
    </span>
  );
}

/** "You" for your own screen */
export const nameOf = (x: WatchingSession) => (x.you ? 'You' : x.profile.name);
const names = (xs: readonly WatchingSession[]) => xs.map(nameOf).join(', ');

/** up to three faces on a channel's row */
export function ViewerFaces({ sessions }: { sessions?: readonly WatchingSession[] }) {
  if (!sessions?.length) return null;
  return (
    <span className={v.faces} title={`Watching now: ${names(sessions)}`}>
      {sessions.slice(0, 3).map((x) => (
        <Avatar key={`${x.profile_id}:${x.started}`} profile={x.profile} />
      ))}
      {sessions.length > 3 ? <span className={v.more}>+{sessions.length - 3}</span> : null}
    </span>
  );
}

/** the program a session is in, of a channel's day */
export function programOf(x: WatchingSession, programs: readonly GuideProgram[]) {
  const key = `${x.item_type}:${x.item_id}`;
  let found: GuideProgram | null = null;
  for (const p of programs) if (p.key === key && p.start <= Date.now()) found = p;
  return found;
}

/** where each one is, as a pin on the program's cell */
export function ViewerPins({
  sessions,
  programs,
  x,
  width,
}: {
  sessions?: readonly WatchingSession[];
  programs: readonly GuideProgram[];
  x: (ms: number) => number;
  width: number;
}) {
  if (!sessions?.length) return null;
  return (
    <>
      {sessions.map((s) => {
        const p = programOf(s, programs);
        if (!p) return null;
        const at = x(Math.min(p.end, p.start + s.position * 1000));
        if (at < 0 || at > width) return null;
        return (
          <span
            key={`${s.profile_id}:${s.started}`}
            className={v.pin}
            style={{ left: at }}
            title={`${nameOf(s)} · ${clock(s.position)} in${s.playing ? '' : ' (paused)'}`}
            aria-hidden="true"
          >
            <Avatar profile={s.profile} />
          </span>
        );
      })}
    </>
  );
}

/** in the panel: each person on this channel, and join them where they are */
export function ViewersHere({
  channel,
  sessions,
  programs,
  onDone,
}: {
  channel: Channel;
  sessions?: readonly WatchingSession[];
  programs: readonly GuideProgram[];
  onDone?: () => void;
}) {
  if (!sessions?.length || channel.music) return null;
  return (
    <ul className={v.here} aria-label={`Watching ${channel.name}`}>
      {sessions.map((s) => {
        const p = programOf(s, programs);
        return (
          <li key={`${s.profile_id}:${s.started}`} className={v.person}>
            <Avatar profile={s.profile} />
            <span className={v.personText}>
              <strong>{nameOf(s)}</strong>
              <span>
                {s.meta.title ?? 'watching'} · {clock(s.position)} in
                {s.playing ? '' : ', paused'}
              </span>
            </span>
            {p && !s.you ? (
              <button
                type="button"
                className={k.btn}
                onClick={() => {
                  // their spot, then the channel on from there
                  playItems(channelQueue(programs, p), 0, { startAt: s.position });
                  onDone?.();
                }}
                data-nav
              >
                Join {s.profile.name}
              </button>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}
