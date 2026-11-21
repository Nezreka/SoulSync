/**
 * playing youtube: a video you downloaded plays in the player (resume,
 * queue, progress); one that isn't plays in youtube's own embed, since the
 * bytes are youtube's. the page gets one play function and the embed's
 * dialog to render.
 */

import { useState, type ReactNode } from 'react';

import type { YtVideo } from '../../-player.types';

import { youtubeItems } from '../../-lib/play-items';
import { VideoDialog } from '../trailer';
import { playItems } from './use-player';

export function useYoutubePlay(): [(videos: YtVideo[], start?: number) => void, ReactNode] {
  const [embed, setEmbed] = useState<YtVideo | null>(null);
  const play = (videos: YtVideo[], start = 0) => {
    const first = videos[start];
    if (!first) return;
    if (!first.downloaded) {
      setEmbed(first);
      return;
    }
    // the downloaded ones from here on, in order, play on into each other
    const queue = youtubeItems(videos.slice(start));
    playItems(queue);
  };
  const dialog = embed ? (
    <VideoDialog videoKey={embed.id} title={embed.title} onClose={() => setEmbed(null)} />
  ) : null;
  return [play, dialog];
}
