/**
 * what the player says while it gets something going: the engine's phase,
 * how long it's been in it, and where the bytes come from, in words. one
 * place, so the transport bar and the video player say the same thing.
 *
 * it only says what it knows: "the drive may be waking up" while the
 * server is still looking after a few seconds, and "the drive was asleep"
 * only once the server has said finding the file took that long.
 */

import type { PlayerState } from './engine';

import { clock } from './timeline';

export interface Description {
  title: string;
  detail?: string;
}

/** a lookup slower than this was a drive spinning up, not a slow database */
export const ASLEEP_MS = 5_000;
/** after this long looking, say what's probably happening */
const STILL_LOOKING_MS = 4_000;

const SERVER_NAMES: Record<string, string> = {
  plex: 'Plex',
  jellyfin: 'Jellyfin',
  emby: 'Emby',
  navidrome: 'Navidrome',
  'media server': 'your media server',
};

export function viaName(via: string | undefined | null): string {
  if (!via || via === 'disk') return 'the drive';
  return SERVER_NAMES[via.toLowerCase()] ?? via;
}

/** "AC3 audio to AAC, on your NVIDIA GPU" */
export function converting(state: PlayerState): string | undefined {
  const t = state.transcode;
  if (!t) return undefined;
  const what = t.reasons.length ? t.reasons.join(', ') : 'Converting for this browser';
  return t.encoder && t.encoder !== 'CPU' ? `${what}, on your ${t.encoder}` : what;
}

function woke(state: PlayerState): string | undefined {
  const took = state.source?.tookMs;
  if (!took || took < ASLEEP_MS) return undefined;
  return `Found it in ${Math.round(took / 1000)} seconds. The drive was asleep.`;
}

export function describe(state: PlayerState, now: number): Description | null {
  const elapsed = Math.max(0, now - state.phaseSince);
  const via = state.source?.via;
  const fromServer = !!via && via !== 'disk';
  switch (state.phase) {
    case 'locating':
      return elapsed < STILL_LOOKING_MS
        ? { title: 'Finding the file' }
        : {
            title: 'Still looking for the file',
            detail: `${Math.round(elapsed / 1000)}s. The drive it's on may be waking up. That can take half a minute.`,
          };
    case 'opening':
      if (state.transcode) return { title: 'Starting the converter', detail: converting(state) };
      return {
        title: fromServer ? `Connecting to ${viaName(via)}` : 'Opening the file',
        detail: woke(state),
      };
    case 'resuming':
      return {
        title: `Picking up at ${clock(state.position)}`,
        detail:
          converting(state) ?? woke(state) ?? (fromServer ? `From ${viaName(via)}` : undefined),
      };
    case 'seeking':
      if (state.transcode)
        return {
          title: `Jumping to ${clock(state.position)}`,
          detail: 'The converter starts again from there',
        };
      return { title: `Jumping to ${clock(state.position)}` };
    case 'buffering': {
      const ahead = Math.max(0, state.buffered - state.position);
      return {
        title: 'Buffering',
        detail:
          elapsed > 3_000
            ? `Waiting on ${viaName(via)}${ahead >= 1 ? `, ${Math.floor(ahead)}s ready` : ''}`
            : undefined,
      };
    }
    default:
      return null;
  }
}
