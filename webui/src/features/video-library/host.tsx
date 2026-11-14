/**
 * mounts the library tab into the vanilla video detail page. video-detail.js
 * calls window.VideoLibraryTab.mount(el, {kind, id}) for the island under the
 * hero and mountEpisodes(el, props) for a show's season, and unmount(el) when
 * the page moves on; a second mount on the same element re-renders in place.
 */

import { createRoot, type Root } from 'react-dom/client';

import type { EpisodeListProps, TitleKind } from './types';

import { EpisodeList } from './episode-list';
import { type PageCommand, sendCommand } from './library-store';
import { LibraryTab, type PageSlots } from './library-tab';

export interface VideoLibraryTabApi {
  mount: (
    el: HTMLElement,
    opts: { kind: TitleKind; id: number; heroSlot?: HTMLElement | null; slots?: PageSlots | null },
  ) => void;
  unmount: (el: HTMLElement) => void;
  /** a show's season, its bar and its episodes; call again to re-render */
  mountEpisodes: (el: HTMLElement, props: EpisodeListProps) => void;
  /** the page's More menu opens one of the island's dialogs */
  open: (what: PageCommand) => void;
}

declare global {
  interface Window {
    VideoLibraryTab?: VideoLibraryTabApi;
  }
}

const roots = new WeakMap<HTMLElement, Root>();

export const videoLibraryTab: VideoLibraryTabApi = {
  mountEpisodes(el, props) {
    if (!el || !props?.season || !Number.isFinite(Number(props.show?.id))) return;
    let root = roots.get(el);
    if (!root) {
      root = createRoot(el);
      roots.set(el, root);
    }
    // no key: the same root follows the page from season to season, and an
    // opened episode stays open through a re-render
    root.render(<EpisodeList {...props} />);
  },
  open(what) {
    sendCommand(what);
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
