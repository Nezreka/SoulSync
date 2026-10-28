/**
 * which side's maintenance the Tools page is showing.
 *
 * /tools is music, /tools?media=video is the video side's tools page. same
 * page, same components: the video api speaks the music shapes
 * (api/video/repair.py), so the only thing that changes is where requests go
 * and a handful of things only one side has (album grids and the cache bar are
 * music, poster panels are video).
 *
 * the base url is module state, not context, on purpose. twenty-odd api
 * functions are called from a dozen components and callbacks; threading a
 * context through every one of them to choose between two strings would touch
 * all of it for nothing. only one tools page is ever mounted, and ToolsPage
 * sets this during render, before any child can fetch, and keys its subtree
 * on the media so nothing from the other side survives the switch.
 */

import { createContext, useContext } from 'react';

export type ToolsMedia = 'music' | 'video';

let current: ToolsMedia = 'music';

export function setToolsMedia(media: ToolsMedia): void {
  current = media;
}

export function toolsMedia(): ToolsMedia {
  return current;
}

/** '/api/repair' + path for music, '/api/video/repair' + path for video. */
export function repairUrl(path: string): string {
  return (current === 'video' ? '/api/video/repair' : '/api/repair') + path;
}

/** for rendering decisions (which detail panel, which grid). the api layer
 *  reads the module value above instead. */
export const ToolsMediaContext = createContext<ToolsMedia>('music');

export function useToolsMedia(): ToolsMedia {
  return useContext(ToolsMediaContext);
}

/** `?media=video` -> 'video', anything else -> 'music'. */
export function parseToolsMedia(value: unknown): ToolsMedia {
  return value === 'video' ? 'video' : 'music';
}
