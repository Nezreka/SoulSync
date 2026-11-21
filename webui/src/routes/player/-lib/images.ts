/**
 * every image the player shows, at the size it is shown. a 170px card never
 * downloads a 1400px master.
 *
 * - our own video art (/api/video/poster|backdrop) takes ?w=, so cards get a
 *   srcset and the browser picks with sizes="auto" (its real rendered width
 *   times the screen's density);
 * - library artwork takes ?size=thumb (a 256px copy kept beside the master);
 * - known CDNs size by url: tmdb, youtube, google avatars, deezer, apple,
 *   audible, cover art archive;
 * - anything else goes through the image cache's 320px "rail" copy.
 */

import { appURL } from '@/platform/url-base';

export type ImgSize = 'card' | 'hero';

/** sizes="auto" is the rendered width (lazy images); the px is for older browsers */
export const CARD_SIZES = 'auto, 220px';
export const WIDE_SIZES = 'auto, 320px';
export const HERO_SIZES = '100vw';

const POSTER_WIDTHS = [185, 342, 500];
const STILL_WIDTHS = [300, 500, 780];
const BACKDROP_WIDTHS = [780, 1280, 1600];

function srcSet(base: string, widths: number[]): string {
  const join = base.includes('?') ? '&' : '?';
  return widths.map((w) => `${appURL(`${base}${join}w=${w}`)} ${w}w`).join(', ');
}

/** a poster card's src and srcset from our own poster route */
export function posterSet(kind: 'movie' | 'show' | 'season', id: number) {
  return {
    src: appURL(`/api/video/poster/${kind}/${id}?w=342`),
    srcSet: srcSet(`/api/video/poster/${kind}/${id}`, POSTER_WIDTHS),
  };
}

/** an episode still (16:9) */
export function stillSet(id: number) {
  return {
    src: appURL(`/api/video/poster/episode/${id}?w=500`),
    srcSet: srcSet(`/api/video/poster/episode/${id}`, STILL_WIDTHS),
  };
}

/** a hero or preview backdrop */
export function backdropSet(kind: 'movie' | 'show', id: number) {
  return {
    src: appURL(`/api/video/backdrop/${kind}/${id}?w=1280`),
    srcSet: srcSet(`/api/video/backdrop/${kind}/${id}`, BACKDROP_WIDTHS),
  };
}

/** library v2 artwork at card size: the 256px thumb kept beside the master */
export function libraryThumb(url: string | null | undefined): string | null {
  if (!url) return null;
  if (url.startsWith('/api/library/v2/artwork/')) {
    return appURL(url.includes('size=') ? url : `${url}${url.includes('?') ? '&' : '?'}size=thumb`);
  }
  return remoteImage(url, 'card');
}

const YT_ID = /i\.ytimg\.com\/vi(?:_webp)?\/([\w-]{6,})\//;

/** a youtube video's still: 320x180 on a card, 1280x720 for a hero */
export function youtubeThumb(
  url: string | null | undefined,
  size: ImgSize,
  id?: string,
): string | null {
  const match = url ? YT_ID.exec(url) : null;
  const videoId = id || match?.[1];
  // a hero keeps the still it was given (hq720 when the video has one);
  // maxres isn't there for every video
  if (size === 'hero' && url) return proxy(url);
  if (videoId) return proxy(`https://i.ytimg.com/vi/${videoId}/mqdefault.jpg`);
  return url ? proxy(url) : null;
}

/** youtube (and other google) images go through the video side's allowlisted proxy */
function proxy(url: string): string {
  return appURL(`/api/video/img?u=${encodeURIComponent(url)}`);
}

/**
 * a remote image at the size it is shown, by its CDN's own sizing rules;
 * anything unknown gets the image cache's 320px copy on a card.
 */
export function remoteImage(url: string | null | undefined, size: ImgSize = 'card'): string | null {
  if (!url) return null;
  if (url.startsWith('/')) return appURL(url);
  if (!/^https?:\/\//.test(url)) return null;
  const card = size === 'card';

  // tmdb: /t/p/<size>/
  if (url.includes('image.tmdb.org/t/p/')) {
    return proxy(url.replace(/\/t\/p\/[^/]+\//, `/t/p/${card ? 'w342' : 'w1280'}/`));
  }
  // youtube stills
  if (url.includes('ytimg.com/')) return youtubeThumb(url, size);
  // google avatars and banners: =s<px> or =w<px>-... at the end
  if (/(googleusercontent|ggpht)\.com\//.test(url)) {
    const sized = /=[swh]\d+[^/]*$/.test(url)
      ? url.replace(/=[swh]\d+[^/]*$/, card ? '=s240' : '=w1600')
      : `${url}${card ? '=s240' : '=w1600'}`;
    return proxy(sized);
  }
  // deezer: /<n>x<n>-
  if (url.includes('dzcdn.net/')) {
    return url.replace(/\/\d+x\d+-/, card ? '/250x250-' : '/1000x1000-');
  }
  // apple (itunes, podcasts): /<n>x<n>bb.
  if (url.includes('mzstatic.com/')) {
    return url.replace(/\/\d+x\d+(bb|cc|sr)?\./, card ? '/300x300bb.' : '/1200x1200bb.');
  }
  // audible / amazon: ._SL500_. style size tokens
  if (/media-amazon\.com|images-amazon\.com/.test(url)) {
    return url.replace(/\._[A-Z]{2}\d+_\./, card ? '._SL300_.' : '._SL1000_.');
  }
  // cover art archive: /front -> /front-250
  if (url.includes('coverartarchive.org/')) {
    return card ? url.replace(/\/(front|back)(-\d+)?(\.\w+)?$/, '/$1-250') : url;
  }
  return card ? appURL(`/api/image-proxy?url=${encodeURIComponent(url)}&v=rail`) : url;
}
