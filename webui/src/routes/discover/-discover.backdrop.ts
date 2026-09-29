import { useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';

import { apiClient, readJson } from '@/app/api-client';

/**
 * what a banner needs to come alive: its music video (looked up only once the
 * banner has been on screen, and remembered server side), and the colour of
 * its artwork, so the glow around it matches the picture instead of always
 * being green.
 */

/** muted, looping, chrome-less, from youtube's no-cookie domain. starts 20s
 * in because the first seconds of a music video are usually a title card. */
export function backdropEmbedUrl(videoId: string): string {
  const id = encodeURIComponent(videoId);
  const params = new URLSearchParams({
    autoplay: '1',
    mute: '1',
    controls: '0',
    loop: '1',
    playlist: videoId,
    playsinline: '1',
    modestbranding: '1',
    rel: '0',
    iv_load_policy: '3',
    disablekb: '1',
    fs: '0',
    start: '20',
  });
  return `https://www.youtube-nocookie.com/embed/${id}?${params.toString()}`;
}

export function fetchBackdropVideo(
  artist: string,
  title?: string | null,
): Promise<{ success?: boolean; video_id?: string | null }> {
  const searchParams: Record<string, string> = { artist };
  if (title) searchParams.title = title;
  return readJson(apiClient.get('discover/backdrop-video', { searchParams }));
}

/** the video for a banner, fetched only once `wanted` (it has been seen). */
export function useBackdropVideoId(
  artist: string | null | undefined,
  title: string | null | undefined,
  wanted: boolean,
): string | null {
  const query = useQuery({
    queryKey: ['discover', 'backdrop-video', artist ?? '', title ?? ''] as const,
    queryFn: () => fetchBackdropVideo(artist ?? '', title),
    enabled: wanted && Boolean(artist),
    staleTime: Number.POSITIVE_INFINITY,
    gcTime: 60 * 60 * 1000,
    retry: false,
  });
  return query.data?.video_id ?? null;
}

/**
 * the average of an image's vivid pixels, as 'r, g, b'. greys and near-blacks
 * are skipped so a dark photo with one red jacket glows red, not grey. null
 * when the image can't be read (a cross-origin host without cors taints the
 * canvas) and the page keeps its default glow.
 */
export function vividAverage(data: ArrayLike<number>): string | null {
  let r = 0;
  let g = 0;
  let b = 0;
  let n = 0;
  for (let i = 0; i + 3 < data.length; i += 4) {
    const pr = data[i];
    const pg = data[i + 1];
    const pb = data[i + 2];
    const max = Math.max(pr, pg, pb);
    const min = Math.min(pr, pg, pb);
    if (max < 40 || max - min < 30) continue; // too dark, or grey
    r += pr;
    g += pg;
    b += pb;
    n += 1;
  }
  if (n === 0) return null;
  return `${Math.round(r / n)}, ${Math.round(g / n)}, ${Math.round(b / n)}`;
}

export function useDominantColor(url: string | null | undefined): string | null {
  const [rgb, setRgb] = useState<string | null>(null);
  useEffect(() => {
    setRgb(null);
    if (!url || typeof document === 'undefined') return;
    let cancelled = false;
    const img = new Image();
    img.crossOrigin = 'anonymous';
    img.decoding = 'async';
    img.onload = () => {
      if (cancelled) return;
      try {
        const canvas = document.createElement('canvas');
        canvas.width = 24;
        canvas.height = 24;
        const ctx = canvas.getContext('2d');
        if (!ctx) return;
        ctx.drawImage(img, 0, 0, 24, 24);
        setRgb(vividAverage(ctx.getImageData(0, 0, 24, 24).data));
      } catch {
        /* a tainted canvas: keep the default glow */
      }
    };
    img.src = url;
    return () => {
      cancelled = true;
    };
  }, [url]);
  return rgb;
}
