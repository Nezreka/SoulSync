/**
 * The header field's results, inside the sheet (plan 4.4): grouped Actions,
 * Settings, Pages, Help, best match first, from the same index the Ctrl K
 * palette reads. Arrow keys move from the field into the list and through
 * it; Enter in the field runs the first row.
 *
 * Also the pieces the palette shares: the search hook, highlighted text and
 * the icons.
 */

import type { ReactNode } from 'react';

import { Fragment, useEffect, useMemo, useRef, useState } from 'react';

import type { ViewProps } from '../view-props';
import type { SearchIconName } from './actions';

import sheet from '../help.module.css';
import { Icon, type IconName } from '../icons';
import { isVideoSide } from '../page';
import { Kbd, Label } from '../ui';
import { loadDocs, loadedDocs } from './docs';
import {
  buildItems,
  handoffFor,
  highlightText,
  runItem,
  searchItems,
  suggestions,
  type Group,
  type Handoff,
  type Hit,
  type Viewer,
} from './index';
import { type Range } from './rank';
import s from './search.module.css';

// ── shared pieces ─────────────────────────────────────────────────────

const OWN_ICONS: Partial<Record<SearchIconName, string>> = {
  scan: 'M3 8V5a2 2 0 0 1 2-2h3M16 3h3a2 2 0 0 1 2 2v3M21 16v3a2 2 0 0 1-2 2h-3M8 21H5a2 2 0 0 1-2-2v-3M7 12h10',
  swap: 'M7 4 3 8l4 4M3 8h14M17 20l4-4-4-4M21 16H7',
};

export function SearchIcon({ name, size = 15 }: { name: SearchIconName; size?: number }) {
  const own = OWN_ICONS[name];
  if (!own) return <Icon name={name as IconName} size={size} />;
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <path d={own} />
    </svg>
  );
}

/** text with its matched ranges marked */
export function Marked({ text, ranges }: { text: string; ranges: Range[] }) {
  if (!ranges.length) return <>{text}</>;
  const parts: ReactNode[] = [];
  let at = 0;
  for (const [start, end] of ranges) {
    if (start > at) parts.push(<Fragment key={`t${at}`}>{text.slice(at, start)}</Fragment>);
    parts.push(
      <mark key={`m${start}`} className={s.mark}>
        {text.slice(start, end)}
      </mark>,
    );
    at = end;
  }
  if (at < text.length) parts.push(<Fragment key={`t${at}`}>{text.slice(at)}</Fragment>);
  return <>{parts}</>;
}

export function MarkedQuery({ text, query }: { text: string; query: string }) {
  return <Marked text={text} ranges={highlightText(text, query)} />;
}

/** what a row says under or after its title: the body snippet, else its sub */
export function hitSub(hit: Hit): string {
  return hit.snippet ?? hit.item.sub ?? '';
}

export function useViewer(isAdmin: boolean, page: string): Viewer {
  const side = isVideoSide() ? 'video' : 'music';
  return useMemo(() => ({ isAdmin, page, side }), [isAdmin, page, side]);
}

export interface SearchState {
  groups: Group[];
  handoff: Handoff | null;
  /** the docs are still being read: Help results may still come */
  reading: boolean;
}

/** the index for this viewer, the groups for this query */
export function useSearch(query: string, viewer: Viewer): SearchState {
  const [docs, setDocs] = useState(loadedDocs);
  const [tried, setTried] = useState(false);
  useEffect(() => {
    if (docs?.length) return;
    let live = true;
    void loadDocs().then((d) => {
      if (!live) return;
      setDocs(d);
      setTried(true);
    });
    return () => {
      live = false;
    };
  }, [docs]);
  const items = useMemo(() => buildItems(viewer, docs), [viewer, docs]);
  const groups = useMemo(
    () => (query.trim() ? searchItems(items, query, viewer) : suggestions(items)),
    [items, query, viewer],
  );
  const handoff = useMemo(() => handoffFor(query, viewer), [query, viewer]);
  return { groups, handoff, reading: !docs?.length && !tried };
}

// ── the inline view ───────────────────────────────────────────────────

const ROW = 'button[data-search-row]';

export function SearchResultsView({ query, isAdmin, page }: ViewProps) {
  const viewer = useViewer(isAdmin, page);
  const { groups, handoff, reading } = useSearch(query, viewer);
  const box = useRef<HTMLDivElement>(null);
  const first = useRef<(() => void) | null>(null);
  const top = groups[0]?.hits[0];
  first.current = top ? () => runItem(top.item) : (handoff?.run ?? null);

  // the header field is the sheet's: listen beside it, not on it
  useEffect(() => {
    const root = box.current?.closest('[data-help-root]');
    if (!root) return;
    const onKey = (event: Event) => {
      const e = event as KeyboardEvent;
      const target = e.target as HTMLElement;
      const rows = Array.from(box.current?.querySelectorAll<HTMLElement>(ROW) ?? []);
      const inField = target.matches('input[type="search"]');
      const at = rows.indexOf(target);
      if (inField && e.key === 'Enter' && first.current) {
        e.preventDefault();
        first.current();
        return;
      }
      if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
      if ((!inField && at < 0) || !rows.length) return;
      e.preventDefault();
      if (inField) {
        (e.key === 'ArrowDown' ? rows[0] : rows[rows.length - 1]).focus();
        return;
      }
      const next = e.key === 'ArrowDown' ? at + 1 : at - 1;
      if (next < 0 || next >= rows.length) {
        root.querySelector<HTMLElement>('input[type="search"]')?.focus();
        return;
      }
      rows[next].focus();
    };
    root.addEventListener('keydown', onKey);
    return () => root.removeEventListener('keydown', onKey);
  }, []);

  const total = groups.reduce((n, g) => n + g.hits.length, 0);
  const text = query.trim();
  return (
    <div ref={box}>
      <p className={s.srOnly} role="status" aria-live="polite">
        {total ? `${total} ${total === 1 ? 'result' : 'results'}` : reading ? '' : 'No results'}
      </p>
      {!total && !reading ? (
        <p className={s.inlineEmpty}>
          Nothing in help{isAdmin ? ', settings' : ''} or pages matches “{text}”.
        </p>
      ) : null}
      {groups.map((group) => (
        <div key={group.label} className={s.inlineGroup}>
          <Label>{group.label}</Label>
          <div className={sheet.list} role="group" aria-label={group.label}>
            {group.hits.map((hit) => (
              <ResultButton key={hit.item.id} hit={hit} query={query} />
            ))}
          </div>
        </div>
      ))}
      {reading ? <p className={s.reading}>Reading the guides</p> : null}
      {handoff ? (
        <div className={s.inlineGroup}>
          <Label>{viewer.side === 'video' ? 'Movies and TV' : 'Music'}</Label>
          <div className={sheet.list}>
            <button type="button" className={sheet.row} data-search-row onClick={handoff.run}>
              <span className={sheet.rowIcon} aria-hidden="true">
                <Icon name="search" size={15} />
              </span>
              <span className={sheet.rowText}>
                <span className={sheet.rowTitle}>
                  {handoff.label} “<MarkedQuery text={text} query={query} />”
                </span>
                <span className={sheet.rowSub}>{handoff.hint}</span>
              </span>
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function ResultButton({ hit, query }: { hit: Hit; query: string }) {
  const sub = hitSub(hit);
  return (
    <button type="button" className={sheet.row} data-search-row onClick={() => runItem(hit.item)}>
      <span className={sheet.rowIcon} aria-hidden="true">
        <SearchIcon name={hit.item.icon} />
      </span>
      <span className={sheet.rowText}>
        <span className={sheet.rowTitle}>
          <MarkedQuery text={hit.item.title} query={query} />
        </span>
        {sub ? (
          <span className={sheet.rowSub}>
            <MarkedQuery text={sub} query={query} />
          </span>
        ) : null}
      </span>
      {hit.item.keys?.length ? (
        <span className={sheet.rowEnd} aria-hidden="true">
          {hit.item.keys.map((k) => (
            <Kbd key={k}>{k}</Kbd>
          ))}
        </span>
      ) : null}
    </button>
  );
}
