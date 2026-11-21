/**
 * playing youtube: a video you downloaded plays in the player (resume,
 * queue, progress); one that isn't plays in youtube's own embed, since the
 * bytes are youtube's. the page gets one play function and the embed's
 * dialog to render.
 */

import { useState, type ReactNode } from 'react';

import type { YtVideo } from '../../-player.types';
import type { CardOpen } from '../kit';

import { youtubeItems } from '../../-lib/play-items';
import { VideoDialog } from '../trailer';
import { playItems } from './use-player';

type PlayYoutube = (videos: YtVideo[], start?: number) => void;

/**
 * a video card's click: play that video, the list's downloaded ones after
 * it. (its href is the channel, which is the page you may already be on.)
 */
export function openVideoFrom(play: PlayYoutube, videos: YtVideo[]): CardOpen {
  return (card) => {
    const at = videos.findIndex((v) => `yt:${v.id}` === card.key);
    if (at >= 0) play(videos, at);
  };
}

export function useYoutubePlay(): [PlayYoutube, ReactNode] {
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
