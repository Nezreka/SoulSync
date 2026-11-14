/**
 * mounts the library manager into the video side's library page.
 * video-side.js shows the page; this fills it. the vanilla library script is
 * gone, so the page is React from the title down.
 */

import { createRoot, type Root } from 'react-dom/client';

import { ManagerPage } from './manager-page';

const PAGE_ID = 'video-library';
let root: Root | null = null;

function mount(): void {
  const el = document.querySelector<HTMLElement>('[data-video-manager-root]');
  if (!el || root) return;
  root = createRoot(el);
  root.render(<ManagerPage />);
}

export function mountVideoLibraryManagerHost(): void {
  if (typeof document === 'undefined') return;
  // mounted the first time the page is shown, so it costs nothing until then
  document.addEventListener('soulsync:video-page-shown', (e) => {
    if ((e as CustomEvent).detail === PAGE_ID) mount();
  });
  const section = document.querySelector<HTMLElement>(`[data-video-subpage="${PAGE_ID}"]`);
  if (section && !section.hidden) mount();
}
