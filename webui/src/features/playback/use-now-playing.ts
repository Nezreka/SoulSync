import { useEffect, useState } from 'react';

/**
 * What the player is on, for a list that marks the playing song: the
 * track's id (null when nothing is loaded) and whether it's playing or
 * paused. The player announces track changes as `ss:now-playing`
 * (media-player.js setTrackInfo / clearTrack); play and pause come from the
 * audio element itself.
 */
export interface NowPlaying {
  id: string | null;
  /** the Library v2 track id, when the track came from the library */
  lib2Id: string | null;
  playing: boolean;
}

type PlayerTrack = { id?: unknown; lib2_track_id?: unknown } | null | undefined;

function asId(value: unknown): string | null {
  return typeof value === 'string' || typeof value === 'number' ? String(value) : null;
}

function idOf(track: PlayerTrack): string | null {
  return asId(track?.id);
}

function lib2IdOf(track: PlayerTrack): string | null {
  return asId(track?.lib2_track_id);
}

function currentTrack(): PlayerTrack {
  return (window as { __ssCurrentTrack?: PlayerTrack }).__ssCurrentTrack;
}

function audio(): HTMLAudioElement | null {
  return document.getElementById('audio-player') as HTMLAudioElement | null;
}

export function useNowPlaying(): NowPlaying {
  const [state, setState] = useState<NowPlaying>(() => ({
    id: idOf(currentTrack()),
    lib2Id: lib2IdOf(currentTrack()),
    playing: Boolean(audio() && !audio()!.paused),
  }));

  useEffect(() => {
    const onTrack = (event: Event) => {
      const track = (event as CustomEvent<PlayerTrack | null>).detail;
      setState((s) => ({ ...s, id: idOf(track), lib2Id: lib2IdOf(track) }));
    };
    const onPlay = () => setState((s) => ({ ...s, playing: true }));
    const onPause = () => setState((s) => ({ ...s, playing: false }));
    window.addEventListener('ss:now-playing', onTrack);
    const el = audio();
    el?.addEventListener('play', onPlay);
    el?.addEventListener('pause', onPause);
    el?.addEventListener('ended', onPause);
    return () => {
      window.removeEventListener('ss:now-playing', onTrack);
      el?.removeEventListener('play', onPlay);
      el?.removeEventListener('pause', onPause);
      el?.removeEventListener('ended', onPause);
    };
  }, []);

  return state;
}
