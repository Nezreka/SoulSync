/**
 * Opening a page this profile hid (#1909): a link, a bookmark, a button on
 * another page. The page opens like always, and a small note says it's
 * hidden with a one-click way back, so nobody wonders where its sidebar item
 * went.
 *
 * Which page is open comes from the sidebar: the shell marks the current
 * nav item `active` on both sides, React pages and vanilla alike, hidden or
 * not. Watching that one class is cheaper and truer than tracking routes.
 */

import type { FeatureDef, FeaturesPayload } from './features';

export const NOTE_ID = 'hidden-page-note';

/** pages someone closed the note on, this tab only */
const dismissed = new Set<string>();

export function forgetDismissed(): void {
  dismissed.clear();
}

/** the nav item for the page on screen, on whichever side is showing */
export function currentNavPage(doc: Document = document): string {
  const video = doc.body?.getAttribute('data-side') === 'video';
  const nav = doc.querySelector(video ? '.sidebar-nav.video-nav' : '.sidebar-nav:not(.video-nav)');
  const active = nav?.querySelector('.nav-button.active');
  return active?.getAttribute('data-page') || active?.getAttribute('data-video-page') || '';
}

/** the switched-off feature that owns this page, if any */
export function hiddenOwner(
  page: string,
  payload: Pick<FeaturesPayload, 'features' | 'off'> | null,
): FeatureDef | null {
  if (!page || !payload) return null;
  return payload.features.find((f) => payload.off.includes(f.id) && f.pages.includes(page)) ?? null;
}

function remove(doc: Document): void {
  doc.getElementById(NOTE_ID)?.remove();
}

export function refreshHiddenPageNote(
  payload: FeaturesPayload | null,
  showIt: (featureId: string) => Promise<unknown>,
  doc: Document = document,
): void {
  const page = currentNavPage(doc);
  const owner = hiddenOwner(page, payload);
  if (!owner || dismissed.has(page)) {
    remove(doc);
    return;
  }
  let note = doc.getElementById(NOTE_ID);
  if (note?.dataset.page === page) return;
  remove(doc);

  note = doc.createElement('div');
  note.id = NOTE_ID;
  note.className = 'hidden-page-note';
  note.setAttribute('role', 'status');
  note.dataset.page = page;

  const text = doc.createElement('span');
  text.textContent = `${owner.label} is hidden from your sidebar.`;

  const show = doc.createElement('button');
  show.type = 'button';
  show.className = 'hidden-page-note-show';
  show.textContent = 'Show it';
  show.addEventListener('click', () => {
    show.disabled = true;
    showIt(owner.id).catch(() => {
      show.disabled = false;
      text.textContent = `Couldn't turn ${owner.label} back on. Try again.`;
    });
  });

  const close = doc.createElement('button');
  close.type = 'button';
  close.className = 'hidden-page-note-close';
  close.setAttribute('aria-label', 'Dismiss');
  close.textContent = '×';
  close.addEventListener('click', () => {
    dismissed.add(page);
    remove(doc);
  });

  note.append(text, show, close);
  doc.body.appendChild(note);
}

/** re-check whenever the active nav item or the side changes */
export function watchHiddenPageNote(refresh: () => void, doc: Document = document): () => void {
  let queued = false;
  const run = () => {
    if (queued) return;
    queued = true;
    queueMicrotask(() => {
      queued = false;
      refresh();
    });
  };
  const observer = new MutationObserver((records) => {
    // our own note going in and out of body isn't a page change
    if (records.some((r) => r.type === 'attributes')) run();
  });
  const start = () => {
    const sidebar = doc.querySelector('.sidebar-scroll') ?? doc.querySelector('.sidebar');
    if (sidebar)
      observer.observe(sidebar, { subtree: true, attributes: true, attributeFilter: ['class'] });
    if (doc.body) observer.observe(doc.body, { attributes: true, attributeFilter: ['data-side'] });
    run();
  };
  if (doc.readyState === 'loading') doc.addEventListener('DOMContentLoaded', start, { once: true });
  else start();
  return () => observer.disconnect();
}
