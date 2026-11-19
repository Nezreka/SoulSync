/**
 * The page's search (settings-bic DESIGN.md 3.5). One box that finds every
 * setting: the help palette's curated entries (search-entries.ts) and every
 * label and heading on the page itself, People and the sheets included, since
 * every tab stays mounted. A result says where it lives ("Downloads › Sources
 * › Soulseek") and what it is set to, and Enter opens the tab, the pill and
 * the sheet and lights the field.
 *
 * Labels and headings only, never a saved value or a secret.
 */

import { type KeyboardEvent, type ReactNode, useEffect, useMemo, useRef, useState } from 'react';

import { type TabKey, nav, resolveTab, tabDef } from '../nav';
import {
  type Anchor,
  type Opener,
  SETTINGS_ENTRIES,
  type SettingsEntry,
  findAnchor,
  flash,
  reveal,
} from '../search-entries';
import { nameOf } from '../tabs/downloads/words';
import { SOURCE_CONFIG_ID_BY_SRC } from '../tabs/sources-model';
import { Icon } from '../ui/icons';
import s from '../ui/settings.module.css';

export interface Hit {
  key: string;
  label: string;
  /** "Downloads › Sources › Soulseek" */
  path: string;
  /** what else matches it (never shown) */
  words: string;
  tab: TabKey;
  view?: string;
  open?: Opener;
  /** a curated entry lands by its anchor; a label by its element */
  entry?: SettingsEntry;
  anchor?: Anchor;
  el?: HTMLElement;
  curated: boolean;
}

const SOURCE_BY_PANEL = Object.fromEntries(
  Object.entries(SOURCE_CONFIG_ID_BY_SRC).map(([src, panel]) => [panel, src]),
);
const tidy = (text: string | null | undefined) =>
  (text || '')
    .replace(/\s+/g, ' ')
    .replace(/[:：]\s*$/, '')
    .trim();
const CONTROL =
  'input:not([type="hidden"]), select, textarea, [role="radiogroup"], [role="switch"]';

/** the control a label names */
function controlOf(label: HTMLElement): HTMLElement | null {
  const forId = label.getAttribute('for');
  if (forId) return document.getElementById(forId);
  return (
    label.querySelector<HTMLElement>(CONTROL) ??
    label.closest('[data-row], .form-group')?.querySelector<HTMLElement>(CONTROL) ??
    null
  );
}

/** where on the page an element lives */
function placeOf(el: HTMLElement): Pick<Hit, 'tab' | 'view' | 'open'> & { trail: string[] } {
  const view = el.closest<HTMLElement>('[data-stg-view]');
  const block = el.closest<HTMLElement>('[data-stg]');
  const slot = el.closest<HTMLElement>('[data-stg-react]');
  const inSheet = el.closest<HTMLElement>('.src-config-panel');
  // a source's panel is parked outside the pills: its sheet opens on Sources
  const place = inSheet
    ? resolveTab('downloads', 'sources')
    : view
      ? resolveTab(view.dataset.stgTab, view.dataset.stgView)
      : resolveTab(block?.dataset.stg ?? slot?.dataset.stgReact);
  const def = tabDef(place.tab);
  const trail = [def.label];
  if (def.views.length > 1) {
    trail.push(def.views.find((v) => v.key === place.view)?.label ?? place.view);
  }
  let open: Opener | undefined;
  const panel = inSheet;
  const service = el.closest<HTMLElement>('.stg-service[data-service]');
  if (panel && SOURCE_BY_PANEL[panel.id]) {
    open = { source: SOURCE_BY_PANEL[panel.id] };
    trail.push(nameOf(SOURCE_BY_PANEL[panel.id]));
  } else if (service?.dataset.service) {
    open = { service: service.dataset.service };
    trail.push(
      tidy((service.dataset.svcTitle || '').replace(/\s*\(.*$/, '')) || service.dataset.service,
    );
  } else {
    // the card or the folded section it sits in
    const card = el.closest<HTMLElement>('[data-card]');
    const body = el.closest<HTMLElement>('.settings-section-body');
    const title =
      card?.dataset.card ||
      tidy(body?.previousElementSibling?.querySelector('h3')?.textContent) ||
      tidy(el.closest('.settings-group')?.querySelector('h3')?.textContent);
    if (title) trail.push(title);
  }
  return { ...place, open, trail };
}

/** everything findable: the curated entries first, then the page's own labels */
export function buildIndex(side: 'music' | 'video', root: ParentNode = document): Hit[] {
  const hits: Hit[] = [];
  const anchored = new Set<string>();
  for (const entry of SETTINGS_ENTRIES) {
    if (entry.side && entry.side !== side) continue;
    const place = resolveTab(entry.tab);
    const def = tabDef(place.tab);
    if ('id' in entry.anchor) anchored.add(entry.anchor.id);
    hits.push({
      key: entry.id,
      label: entry.label,
      path: [def.label, entry.hint].filter(Boolean).join(' › '),
      words: entry.keywords ?? '',
      tab: place.tab,
      open: entry.open,
      entry,
      curated: true,
    });
  }
  const page = root.querySelector<HTMLElement>('#settings-page');
  if (!page) return hits;
  const seen = new Set<string>();
  for (const el of page.querySelectorAll<HTMLElement>('label, h3, h4')) {
    if (el.closest('[data-stg-search], [role="menu"], [data-order-list]')) continue;
    const other = side === 'video' ? '[data-music-only]' : '[data-video-only]';
    if (el.closest(other)) continue;
    const label = tidy(el.textContent);
    if (label.length < 3 || label.length > 90) continue;
    const control = el.tagName === 'LABEL' ? controlOf(el) : null;
    // the curated entry for this field already speaks for it
    if (control?.id && anchored.has(control.id)) continue;
    const place = placeOf(el);
    // a heading is not filed under itself
    if (place.trail[place.trail.length - 1] === label) place.trail.pop();
    const path = place.trail.join(' › ');
    const key = `${path}|${label.toLowerCase()}`;
    if (seen.has(key)) continue;
    seen.add(key);
    hits.push({
      key: `dom:${key}`,
      label,
      path,
      words: '',
      tab: place.tab,
      view: place.view,
      open: place.open,
      el: control ?? el,
      curated: false,
    });
  }
  return hits;
}

/** the hits for a query, best first. Every word typed must be found */
export function searchHits(index: Hit[], query: string, limit = 12): Hit[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return [];
  const scored: Array<{ hit: Hit; score: number }> = [];
  for (const hit of index) {
    const label = hit.label.toLowerCase();
    const rest = `${hit.path} ${hit.words}`.toLowerCase();
    let score = hit.curated ? 2 : 0;
    let all = true;
    for (const word of words) {
      if (label.startsWith(word)) score += 12;
      else if (label.includes(` ${word}`)) score += 9;
      else if (label.includes(word)) score += 6;
      else if (rest.includes(word)) score += 2;
      else {
        all = false;
        break;
      }
    }
    if (all) scored.push({ hit, score });
  }
  return scored
    .sort((a, b) => b.score - a.score || a.hit.label.length - b.hit.label.length)
    .slice(0, limit)
    .map((x) => x.hit);
}

/** what a setting is set to, in a word. Never a value someone typed */
export function stateOf(hit: Hit): string {
  const el = hit.el ?? (hit.entry ? findAnchor(hit.entry.anchor) : null);
  if (!el) return '';
  if (el instanceof HTMLInputElement) {
    if (el.type === 'checkbox') return el.checked ? 'On' : 'Off';
    if (el.type === 'search') return '';
    return el.value ? 'Set' : 'Not set';
  }
  if (el instanceof HTMLSelectElement) return tidy(el.selectedOptions[0]?.textContent).slice(0, 40);
  if (el instanceof HTMLTextAreaElement) return el.value ? 'Set' : 'Not set';
  return '';
}

type Openers = Window & {
  openServiceModal?: (id: string) => void;
  switchServiceKind?: (kind: string) => void;
  openSourceModal?: (id: string) => void;
  openPeopleSettings?: (part: string) => void;
};

/** open the tab, the pill and the sheet, then show the field */
export async function landHit(hit: Hit): Promise<boolean> {
  nav.go(hit.tab, hit.view);
  const w = window as Openers;
  if (hit.open && 'service' in hit.open) {
    if (hit.open.kind) w.switchServiceKind?.(hit.open.kind);
    w.openServiceModal?.(hit.open.service);
  } else if (hit.open && 'source' in hit.open) w.openSourceModal?.(hit.open.source);
  else if (hit.open && 'people' in hit.open) w.openPeopleSettings?.(hit.open.people);
  const anchor = hit.entry?.anchor ?? hit.anchor;
  const find = () => (anchor ? findAnchor(anchor) : hit.el?.isConnected ? hit.el : null);
  let el = find();
  for (let tries = 0; !el && tries < 20; tries += 1) {
    await new Promise((resolve) => setTimeout(resolve, 50));
    el = find();
  }
  if (!el) return false;
  reveal(el);
  // a click above re-renders its card: find it again before scrolling
  await new Promise((resolve) => setTimeout(resolve, 30));
  flash(find() ?? el);
  return true;
}

/** the label with what was typed lit */
function Lit({ text, query }: { text: string; query: string }) {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  const lower = text.toLowerCase();
  const out: ReactNode[] = [];
  let at = 0;
  while (at < text.length) {
    let best = -1;
    let length = 0;
    for (const word of words) {
      const found = lower.indexOf(word, at);
      if (found !== -1 && (best === -1 || found < best)) {
        best = found;
        length = word.length;
      }
    }
    if (best === -1) break;
    if (best > at) out.push(text.slice(at, best));
    out.push(
      <mark className={s.hit} key={best}>
        {text.slice(best, best + length)}
      </mark>,
    );
    at = best + length;
  }
  out.push(text.slice(at));
  return <>{out}</>;
}

export function SettingsSearch({ side }: { side: 'music' | 'video' }) {
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const [at, setAt] = useState(0);
  // read from the page when the box is entered: every tab is mounted by then
  const [index, setIndex] = useState<Hit[]>([]);
  const box = useRef<HTMLDivElement>(null);
  const hits = useMemo(() => searchHits(index, query), [index, query]);
  const showing = open && query.trim() !== '';

  useEffect(() => {
    if (!showing) return;
    const away = (event: PointerEvent) => {
      if (!box.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('pointerdown', away);
    return () => document.removeEventListener('pointerdown', away);
  }, [showing]);

  const pick = (hit: Hit | undefined) => {
    if (!hit) return;
    setOpen(false);
    setQuery('');
    void landHit(hit);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Escape') {
      // first the results, then the words
      if (showing) setOpen(false);
      else setQuery('');
      event.stopPropagation();
      return;
    }
    if (!showing) return;
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      const step = event.key === 'ArrowDown' ? 1 : -1;
      setAt((n) => (hits.length ? (n + step + hits.length) % hits.length : 0));
    } else if (event.key === 'Enter') {
      event.preventDefault();
      pick(hits[at]);
    }
  };

  return (
    <div className={s.find} ref={box} data-stg-search="">
      <span className={s.findIcon}>
        <Icon name="search" />
      </span>
      <input
        type="text"
        id="stg-search-input"
        className={s.findInput}
        placeholder="Search every setting"
        aria-label="Search settings"
        autoComplete="off"
        role="combobox"
        aria-expanded={showing}
        aria-controls="stg-search-results"
        aria-activedescendant={showing && hits[at] ? `stg-hit-${at}` : undefined}
        value={query}
        onFocus={() => {
          setIndex(buildIndex(side));
          setOpen(true);
        }}
        onChange={(event) => {
          setQuery(event.target.value);
          setAt(0);
          setOpen(true);
        }}
        onKeyDown={onKeyDown}
      />
      <kbd className={s.findKbd}>/</kbd>
      <div
        id="stg-search-results"
        className={s.results}
        role="listbox"
        aria-label="Settings found"
        hidden={!showing}
      >
        {showing && hits.length === 0 ? (
          <p className={s.resultEmpty} role="status">
            No settings found. Try a service name, a folder or a feature.
          </p>
        ) : null}
        {showing
          ? hits.map((hit, i) => (
              <button
                key={hit.key}
                type="button"
                id={`stg-hit-${i}`}
                role="option"
                aria-selected={i === at}
                className={i === at ? `${s.result} ${s.resultOn}` : s.result}
                onMouseEnter={() => setAt(i)}
                onClick={() => pick(hit)}
              >
                <span className={s.resultLabel}>
                  <Lit text={hit.label} query={query} />
                </span>
                <span className={s.resultPath}>{hit.path}</span>
                <span className={s.resultState}>{stateOf(hit)}</span>
              </button>
            ))
          : null}
      </div>
    </div>
  );
}
