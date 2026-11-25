/**
 * Tours, wired in. installTours() (idempotent) does three things:
 *
 *   - listens for ss:help-start-tour { id }, which the sheet's "Tour: ..."
 *     row fires (home.tsx) and the palette can fire too
 *   - mounts the tour layer in its own root the first time one starts
 *   - offers each tour on the pages it is about (page help's Read more)
 *
 * window.SoulSyncTours = { start(id), list(), running() } for the vanilla
 * side and the palette.
 *
 * mountHelpHost() (host.tsx) calls it as the bundle starts, so the listener
 * and the page offers are there from the first page. Importing this file
 * does nothing on its own.
 */

import { createRoot, type Root } from 'react-dom/client';

import { START_TOUR_EVENT } from '../home';
import { registerPageHelp } from '../pages';
import { end, isTourRunning, spotlight, startTour, useTourStore } from './engine';
import { TourLayer } from './layer';
import { TOURS, type Tour } from './tours';

const ROOT_ID = 'soulsync-tour-root';

declare global {
  interface Window {
    SoulSyncTours?: {
      start: (id: string) => boolean;
      list: () => { id: string; title: string; detail: string }[];
      running: () => boolean;
      end: () => void;
    };
  }
}

let root: Root | null = null;
let installed = false;

export function mountTourLayer(): void {
  if (typeof document === 'undefined') return;
  const existing = document.getElementById(ROOT_ID);
  if (existing && root) return;
  const el = existing ?? document.createElement('div');
  el.id = ROOT_ID;
  // last in <body>: the same top layer as the floating buttons, and above them
  if (!existing) document.body.appendChild(el);
  root = createRoot(el);
  root.render(<TourLayer />);
}

/** for tests */
export function unmountTourLayer(): void {
  end(false);
  root?.unmount();
  root = null;
  document.getElementById(ROOT_ID)?.remove();
}

export function runTour(id: string): boolean {
  mountTourLayer();
  return startTour(id);
}

/** What's new's Show me: one spotlight, on its page */
export function showMe(step: Parameters<typeof spotlight>[0]): boolean {
  mountTourLayer();
  return spotlight(step);
}

function onStartEvent(event: Event): void {
  const id = (event as CustomEvent<{ id?: string }>).detail?.id;
  if (typeof id === 'string') runTour(id);
}

/** the "Tour: ..." row under Read more, on each page a tour is about */
export function offerTours(tours: Tour[] = TOURS): void {
  for (const tour of tours) {
    for (const page of tour.pages) {
      registerPageHelp({ id: page, tour: { id: tour.id, title: tour.title, detail: tour.detail } });
    }
  }
}

export function installTours(): void {
  if (installed || typeof window === 'undefined') return;
  installed = true;
  window.addEventListener(START_TOUR_EVENT, onStartEvent);
  offerTours();
  window.SoulSyncTours = {
    start: runTour,
    list: () => TOURS.map(({ id, title, detail }) => ({ id, title, detail })),
    running: isTourRunning,
    end: () => end(false),
  };
}

/** for tests */
export function uninstallTours(): void {
  window.removeEventListener(START_TOUR_EVENT, onStartEvent);
  delete window.SoulSyncTours;
  installed = false;
}

export { START_TOUR_EVENT, TOURS, useTourStore };
