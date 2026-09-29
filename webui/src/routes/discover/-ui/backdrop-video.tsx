import { useEffect, useState } from 'react';

import { backdropEmbedUrl } from '../-discover.backdrop';

/**
 * a music video playing muted behind a banner.
 *
 * mounted ONLY while its banner holds the stage (see -discover.video-stage),
 * so leaving the screen tears the player down instead of leaving it running.
 * it fades in a moment after loading: youtube shows a black frame and a
 * spinner first, and the banner's photo covers that until real video plays.
 */

export interface BackdropVideoProps {
  videoId: string | null;
  playing: boolean;
}

/** how long after the frame loads before it fades in over the photo */
export const BACKDROP_FADE_DELAY_MS = 1400;

export function BackdropVideo({ videoId, playing }: BackdropVideoProps) {
  const [ready, setReady] = useState(false);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    setReady(false);
    setLoaded(false);
  }, [videoId, playing]);

  useEffect(() => {
    if (!loaded) return;
    const timer = setTimeout(() => setReady(true), BACKDROP_FADE_DELAY_MS);
    return () => clearTimeout(timer);
  }, [loaded]);

  if (!videoId || !playing) return null;
  return (
    <div className={`dsc-backdrop-video${ready ? ' ready' : ''}`} aria-hidden="true">
      <iframe
        src={backdropEmbedUrl(videoId)}
        title="Background video"
        tabIndex={-1}
        allow="autoplay; encrypted-media; picture-in-picture"
        referrerPolicy="strict-origin-when-cross-origin"
        onLoad={() => setLoaded(true)}
      />
    </div>
  );
}
