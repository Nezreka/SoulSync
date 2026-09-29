import { useCallback, useEffect, useState } from 'react';

import { useBackdropVideoId, useDominantColor } from './-discover.backdrop';
import { useVideoSlot } from './-discover.video-stage';

/**
 * everything one promo banner needs: its slot on the video stage, its video
 * (looked up once the banner is first seen), what to do when youtube refuses
 * that video, and the glow colour of its artwork.
 */
export interface PromoVideo {
  ref: (el: HTMLElement | null) => void;
  playing: boolean;
  videoId: string | null;
  onUnplayable: (videoId: string) => void;
  glowRgb: string | null;
}

export function usePromoVideo(
  slotId: string,
  artist: string | null | undefined,
  title: string | null | undefined,
  art: string | null | undefined,
  videosOn: boolean,
): PromoVideo {
  const [videoId, setVideoId] = useState<string | null>(null);
  const [refused, setRefused] = useState<ReadonlySet<string>>(() => new Set());
  const slot = useVideoSlot(slotId, Boolean(videoId));
  const fetched = useBackdropVideoId(artist, title, slot.seen && videosOn);
  const usable = fetched && !refused.has(fetched) ? fetched : null;
  useEffect(() => setVideoId(usable), [usable]);
  // refused: this banner falls back to its artwork, and the stage moves on
  const onUnplayable = useCallback(
    (id: string) => setRefused((prev) => (prev.has(id) ? prev : new Set(prev).add(id))),
    [],
  );
  const glowRgb = useDominantColor(art ?? null);
  return { ref: slot.ref, playing: slot.playing, videoId, onUnplayable, glowRgb };
}
