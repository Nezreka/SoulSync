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
  playing: boolean;
}

type PlayingTrack = { id?: unknown; lib2_track_id?: unknown } | null | undefined;

/** Library v2: the catalogue id is the typed `lib2_track_id`; a queue row's
 *  `id` is the media server's (or null), never the id a library row shows. */
function idOf(track: PlayingTrack): string | null {
  const id = track?.lib2_track_id ?? track?.id;
  return typeof id === 'string' || typeof id === 'number' ? String(id) : null;
}

function currentId(): string | null {
  return idOf((window as { __ssCurrentTrack?: PlayingTrack }).__ssCurrentTrack);
}

function audio(): HTMLAudioElement | null {
  return document.getElementById('audio-player') as HTMLAudioElement | null;
}

export function useNowPlaying(): NowPlaying {
  const [state, setState] = useState<NowPlaying>(() => ({
    id: currentId(),
    playing: Boolean(audio() && !audio()!.paused),
  }));

  useEffect(() => {
    const onTrack = (event: Event) => {
      const track = (event as CustomEvent<PlayingTrack>).detail;
      setState((s) => ({ ...s, id: idOf(track) }));
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
