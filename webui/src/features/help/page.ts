/**
 * Which page the sheet is about, and who is looking. The page id is the one
 * the sidebar uses: a music page by its route ('active-downloads',
 * 'settings'), a video page with its video- prefix ('video-library'), so
 * pages/*.ts can key help by the same ids the nav buttons carry.
 */

import { normalizeShellPath } from '@/platform/shell/route-manifest';

const VIDEO_MEDIA = new Set(['video', 'tv', 'movie', 'movies', 'shows']);

export function pageIdFrom(pathname: string, search = ''): string {
  let path = pathname;
  try {
    path = normalizeShellPath(pathname);
  } catch {
    /* no document in a worker: the raw path is fine */
  }
  const first = path.replace(/^\/+/, '').split('/')[0] || 'dashboard';
  const media = new URLSearchParams(search).get('media');
  if (media && VIDEO_MEDIA.has(media.toLowerCase()) && !first.startsWith('video-')) {
    return `video-${first}`;
  }
  return first;
}

export function currentPageId(): string {
  if (typeof window === 'undefined') return 'dashboard';
  // the video side keeps its page on <body> (video-side.js showPage)
  const videoPage = isVideoSide() ? document.body.getAttribute('data-video-page') : null;
  if (videoPage) return videoPage;
  return pageIdFrom(window.location.pathname, window.location.search);
}

/** the same rule the shell uses for the activity drawer (#2155) */
export function viewerIsAdmin(): boolean {
  const ctx = typeof window === 'undefined' ? null : window.getCurrentProfileContext?.();
  return !!ctx && (ctx.isAdmin === true || ctx.profileId === 1);
}

export function isVideoSide(): boolean {
  return typeof document !== 'undefined' && document.body?.getAttribute('data-side') === 'video';
}
