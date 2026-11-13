/**
 * mounts the library tab into the vanilla video detail page. video-detail.js
 * calls window.VideoLibraryTab.mount(el, {kind, id}) when the tab opens and
 * unmount(el) when the page moves on; a second mount on the same element
 * re-renders in place.
 */

import { createRoot, type Root } from 'react-dom/client';

import type { TitleKind } from './types';

import { EpisodeFiles } from './episode-files';
import { LibraryTab, type PageSlots } from './library-tab';

export interface VideoLibraryTabApi {
  mount: (
    el: HTMLElement,
    opts: { kind: TitleKind; id: number; heroSlot?: HTMLElement | null; slots?: PageSlots | null },
  ) => void;
  unmount: (el: HTMLElement) => void;
  /** one episode's file cards, into the page's own episode list */
  mountEpisode: (
    el: HTMLElement,
    opts: { showId: number; season: number; episodeId: number },
  ) => void;
}

declare global {
  interface Window {
    VideoLibraryTab?: VideoLibraryTabApi;
  }
}

const roots = new WeakMap<HTMLElement, Root>();

export const videoLibraryTab: VideoLibraryTabApi = {
  mountEpisode(el, { showId, season, episodeId }) {
    if (!el || ![showId, season, episodeId].every((n) => Number.isFinite(Number(n)))) return;
    let root = roots.get(el);
    if (!root) {
      root = createRoot(el);
      roots.set(el, root);
    }
    root.render(
      <EpisodeFiles
        key={`${showId}:${season}:${episodeId}`}
        showId={Number(showId)}
        season={Number(season)}
        episodeId={Number(episodeId)}
      />,
    );
  },
  mount(el, { kind, id, heroSlot, slots }) {
    if (!el || (kind !== 'movie' && kind !== 'show') || !Number.isFinite(Number(id))) return;
    let root = roots.get(el);
    if (!root) {
      root = createRoot(el);
      roots.set(el, root);
    }
    root.render(
      <LibraryTab
        key={`${kind}:${id}`}
        kind={kind}
        id={Number(id)}
        heroSlot={heroSlot ?? null}
        slots={slots ?? null}
      />,
    );
  },
  unmount(el) {
    const root = roots.get(el);
    if (!root) return;
    root.unmount();
    roots.delete(el);
  },
};

export function mountVideoLibraryTabHost(): void {
  if (typeof window === 'undefined') return;
  window.VideoLibraryTab = videoLibraryTab;
  // the page may have asked before this module loaded (main.tsx is deferred)
  document.dispatchEvent(new CustomEvent('soulsync:video-library-tab-ready'));
}
