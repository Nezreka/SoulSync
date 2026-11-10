/**
 * What this profile chose to see (#1909).
 *
 * The server owns the list (core/ui_features.py). This keeps the current
 * answer, hides what's off in the sidebar, and tells the React pages through
 * window.SoulSyncFeatures + an event. It's a preference, not access: a hidden
 * page still opens from a link, permissions stay in init.js.
 *
 * The last answer is cached in localStorage so the inline script under the
 * sidebar can hide the same items before first paint. Without that every
 * page load would flash the full sidebar and then shrink it.
 */

import { refreshHiddenPageNote, watchHiddenPageNote } from './hidden-page-note';

export const FEATURES_CHANGED_EVENT = 'ss:features-changed';
export const FEATURES_CACHE_KEY = 'ss_features';
const HIDDEN = 'nav-feature-hidden';

export interface FeatureDef {
  id: string;
  label: string;
  desc: string;
  group: string;
  pages: string[];
}

export interface FeaturesPayload {
  /** false until this profile saves a choice; the dashboard offers the picker */
  chosen?: boolean;
  /** admins only: podcasts / audiobooks jobs, paused and hidden-by-everyone */
  background?: Record<string, { paused: boolean; everyone_hid: boolean }>;
  features: FeatureDef[];
  groups: { id: string; label: string }[];
  presets: { id: string; label: string; desc: string; off: string[] }[];
  off: string[];
  preset: string;
  hidden_pages: string[];
}

export type FeaturesSave = { off: string[] } | { preset: string } | { purposes: string[] };

interface State {
  loaded: boolean;
  payload: FeaturesPayload | null;
  off: string[];
  hiddenPages: string[];
}

const state: State = { loaded: false, payload: null, off: [], hiddenPages: [] };

function readCache(): { off: string[]; pages: string[] } {
  try {
    const raw = JSON.parse(localStorage.getItem(FEATURES_CACHE_KEY) || 'null') as {
      off?: unknown;
      pages?: unknown;
    } | null;
    const list = (v: unknown) => (Array.isArray(v) ? v.map(String) : []);
    return { off: list(raw?.off), pages: list(raw?.pages) };
  } catch {
    return { off: [], pages: [] };
  }
}

function writeCache(off: string[], pages: string[]): void {
  try {
    localStorage.setItem(FEATURES_CACHE_KEY, JSON.stringify({ off, pages }));
  } catch {
    /* storage blocked: the sidebar just flashes once per load */
  }
}

function navPage(btn: Element): string {
  return btn.getAttribute('data-page') || btn.getAttribute('data-video-page') || '';
}

/** a section title with nothing visible under it goes too */
export function refreshSectionLabels(root: ParentNode = document): void {
  root.querySelectorAll<HTMLElement>('.sidebar-nav .nav-section-label').forEach((label) => {
    let el = label.nextElementSibling as HTMLElement | null;
    let any = false;
    while (el && !el.classList.contains('nav-section-label')) {
      if (
        el.classList.contains('nav-button') &&
        !el.classList.contains(HIDDEN) &&
        el.style.display !== 'none'
      ) {
        any = true;
        break;
      }
      el = el.nextElementSibling as HTMLElement | null;
    }
    label.classList.toggle(HIDDEN, !any);
  });
}

/** hide what's off. safe to call again at any time. */
export function applyFeatures(
  off: string[],
  hiddenPages: string[],
  root: Document = document,
): void {
  const pages = new Set(hiddenPages);
  root.documentElement.setAttribute('data-features-off', off.join(' '));
  root.querySelectorAll<HTMLElement>('.sidebar-nav .nav-button').forEach((btn) => {
    btn.classList.toggle(HIDDEN, pages.has(navPage(btn)));
  });
  refreshSectionLabels(root);

  const videoOff = off.includes('video');
  root.querySelector('.side-toggle')?.classList.toggle(HIDDEN, videoOff);
  if (videoOff && root.body?.getAttribute('data-side') === 'video') {
    const sw = (window as { _switchAppSide?: (s: string, o?: object) => void })._switchAppSide;
    if (typeof sw === 'function') sw('music', { force: true });
  }
}

function publish(payload: FeaturesPayload): void {
  state.loaded = true;
  state.payload = payload;
  state.off = payload.off.slice();
  state.hiddenPages = payload.hidden_pages.slice();
  writeCache(state.off, state.hiddenPages);
  applyFeatures(state.off, state.hiddenPages);
  refreshNote();
  renderOffer(payload.chosen);
  window.dispatchEvent(new CustomEvent(FEATURES_CHANGED_EVENT, { detail: { off: state.off } }));
}

async function readJson(res: Response): Promise<FeaturesPayload> {
  const data = (await res.json().catch(() => null)) as
    | (FeaturesPayload & { success?: boolean; error?: string })
    | null;
  if (!res.ok || !data?.success) throw new Error(data?.error || `HTTP ${res.status}`);
  return data;
}

/** ask the server for this profile's answer. called on boot and on every
 *  profile switch, after init.js has applied the permissions. */
export async function loadFeatures(): Promise<FeaturesPayload | null> {
  try {
    const payload = await readJson(
      await fetch('/api/profiles/me/features', { headers: { Accept: 'application/json' } }),
    );
    publish(payload);
    return payload;
  } catch {
    // keep whatever the cache showed. a failed read never empties the app.
    return state.payload;
  }
}

export async function saveFeatures(body: FeaturesSave): Promise<FeaturesPayload> {
  const payload = await readJson(
    await fetch('/api/profiles/me/features', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(body),
    }),
  );
  publish(payload);
  return payload;
}

export function isFeatureOn(id: string): boolean {
  return !state.off.includes(id);
}

export function getFeatures(): { loaded: boolean; off: string[]; payload: FeaturesPayload | null } {
  return { loaded: state.loaded, off: state.off.slice(), payload: state.payload };
}

/** before the server answers, trust the cache so React's first render
 *  matches the sidebar the inline script already trimmed. */
export function primeFromCache(): void {
  const cached = readCache();
  state.off = cached.off;
  state.hiddenPages = cached.pages;
  if (document.documentElement) {
    document.documentElement.setAttribute('data-features-off', cached.off.join(' '));
  }
}

export interface SoulSyncFeaturesApi {
  load: typeof loadFeatures;
  save: typeof saveFeatures;
  isOn: typeof isFeatureOn;
  get: typeof getFeatures;
  apply: () => void;
}

declare global {
  interface Window {
    SoulSyncFeatures?: SoulSyncFeaturesApi;
    openFeaturePicker?: () => void;
  }
}

/** turn one feature back on, from the hidden-page note */
export function showFeature(id: string): Promise<FeaturesPayload> {
  return saveFeatures({ off: state.off.filter((x) => x !== id) });
}

function refreshNote(): void {
  refreshHiddenPageNote(state.payload, showFeature);
}

const OFFER_CLASS = 'sidebar-customize-offer';

/** once per profile, until they pick anything (keep everything counts):
 *  people who installed before #1909 never see setup again, so the
 *  customize button says what it's for, right where it lives */
export function renderOffer(chosen: boolean | undefined, root: Document = document): void {
  const box = root.querySelector<HTMLElement>('.sidebar-customize');
  if (!box) return;
  const existing = box.querySelector(`.${OFFER_CLASS}`);
  const offer = chosen === false;
  box.classList.toggle('has-offer', offer);
  if (!offer) {
    existing?.remove();
    return;
  }
  if (existing) return;
  const card = root.createElement('div');
  card.className = OFFER_CLASS;
  const title = root.createElement('strong');
  title.textContent = 'Only use part of SoulSync?';
  const text = root.createElement('span');
  text.textContent = 'Hide the rest. It keeps working and comes back any time.';
  const keep = root.createElement('button');
  keep.type = 'button';
  keep.className = 'sidebar-customize-keep';
  keep.textContent = 'Keep everything';
  keep.addEventListener('click', () => {
    keep.disabled = true;
    saveFeatures({ preset: 'full' }).catch(() => {
      keep.disabled = false;
    });
  });
  card.append(title, text, keep);
  box.prepend(card);
}

export function installFeatures(): void {
  primeFromCache();
  watchHiddenPageNote(refreshNote);
  window.SoulSyncFeatures = {
    load: loadFeatures,
    save: saveFeatures,
    isOn: isFeatureOn,
    get: getFeatures,
    // init.js re-runs the permission filter on a profile switch; this puts
    // the feature filter back on top of it
    apply: () => applyFeatures(state.off, state.hiddenPages),
  };
}
