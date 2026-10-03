import { useEffect, useState } from 'react';

/**
 * a slim live banner for whatever soulsync is playing right now, with the one
 * thing you'd want from it on discover: more like this. it follows the media
 * player (getCurrentTrack plus the audio element's play/pause), and it's
 * simply not there when nothing is playing.
 */

export interface NowPlayingTrack {
  title?: string;
  artist?: string;
  album?: string;
  image_url?: string | null;
  artist_id?: string | number | null;
  artist_source?: string | null;
}

export interface NowPlayingState {
  track: NowPlayingTrack | null;
  playing: boolean;
}

/** the display title: the player keeps some titles as "<id>||<title>". */
export function cleanTitle(title: string | undefined): string {
  if (!title) return '';
  const cut = title.indexOf('||');
  return cut >= 0 ? title.slice(cut + 2) : title;
}

function read(): NowPlayingState {
  const track = (window.getCurrentTrack?.() ?? null) as NowPlayingTrack | null;
  const audio = document.getElementById('audio-player') as HTMLAudioElement | null;
  return { track: track && track.title ? track : null, playing: Boolean(audio && !audio.paused) };
}

/** the player's current track and whether it's playing. */
export function useNowPlaying(pollMs = 1000): NowPlayingState {
  const [state, setState] = useState<NowPlayingState>(() => ({ track: null, playing: false }));
  useEffect(() => {
    const update = () =>
      setState((prev) => {
        const next = read();
        return prev.track === next.track && prev.playing === next.playing ? prev : next;
      });
    update();
    const audio = document.getElementById('audio-player');
    audio?.addEventListener('play', update);
    audio?.addEventListener('pause', update);
    audio?.addEventListener('ended', update);
    // track changes don't fire an event the page can hear, so a light poll
    const timer = setInterval(update, pollMs);
    return () => {
      clearInterval(timer);
      audio?.removeEventListener('play', update);
      audio?.removeEventListener('pause', update);
      audio?.removeEventListener('ended', update);
    };
  }, [pollMs]);
  return state;
}

export interface NowPlayingBannerProps {
  state: NowPlayingState;
  onMoreLikeThis: (track: NowPlayingTrack) => void;
  artistHref?: string | null;
}

export function NowPlayingBanner({ state, onMoreLikeThis, artistHref }: NowPlayingBannerProps) {
  const { track, playing } = state;
  if (!track) return null;
  const art = track.image_url || null;
  return (
    <section className={`dsc-now${playing ? ' playing' : ''}`} aria-label="Now playing">
      {art ? (
        <div
          className="dsc-now-wash"
          aria-hidden="true"
          style={{ backgroundImage: `url('${art}')` }}
        />
      ) : null}
      <div
        className="dsc-now-art"
        aria-hidden="true"
        style={art ? { backgroundImage: `url('${art}')` } : undefined}
      />
      <div className="dsc-now-text">
        <span className="dsc-now-eyebrow">
          <span className="dsc-eq" aria-hidden="true">
            <span />
            <span />
            <span />
            <span />
          </span>
          {playing ? 'Now playing' : 'Paused'}
        </span>
        <span className="dsc-now-title">{cleanTitle(track.title)}</span>
        {track.artist ? (
          artistHref ? (
            <a className="dsc-now-artist" href={artistHref}>
              {track.artist}
            </a>
          ) : (
            <span className="dsc-now-artist">{track.artist}</span>
          )
        ) : null}
      </div>
      {track.artist_id ? (
        <button type="button" className="dsc-pulse-btn" onClick={() => onMoreLikeThis(track)}>
          More like this
        </button>
      ) : null}
    </section>
  );
}
