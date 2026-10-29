/**
 * mounts the library tab into the vanilla video detail page. video-detail.js
 * calls window.VideoLibraryTab.mount(el, {kind, id}) when the tab opens and
 * unmount(el) when the page moves on; a second mount on the same element
 * re-renders in place.
 */

import { createRoot, type Root } from 'react-dom/client';

import type { TitleKind } from './types';

import { LibraryTab } from './library-tab';

export interface VideoLibraryTabApi {
  mount: (el: HTMLElement, opts: { kind: TitleKind; id: number }) => void;
  unmount: (el: HTMLElement) => void;
}

declare global {
  interface Window {
    VideoLibraryTab?: VideoLibraryTabApi;
  }
}

const roots = new WeakMap<HTMLElement, Root>();

export const videoLibraryTab: VideoLibraryTabApi = {
  mount(el, { kind, id }) {
    if (!el || (kind !== 'movie' && kind !== 'show') || !Number.isFinite(Number(id))) return;
    let root = roots.get(el);
    if (!root) {
      root = createRoot(el);
      roots.set(el, root);
    }
    root.render(<LibraryTab key={`${kind}:${id}`} kind={kind} id={Number(id)} />);
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
