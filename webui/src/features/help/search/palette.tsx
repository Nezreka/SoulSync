/**
 * The Ctrl/Cmd K palette (plan 4.4, mockup "Ctrl K search"): one search over
 * help, settings, pages and actions, from anywhere. A centred modal with the
 * combobox pattern: the field is role="combobox", the results a listbox with
 * labelled groups, aria-activedescendant follows the arrow keys, Enter runs,
 * Esc closes and focus goes back where it was. The last row hands the words
 * to the Search page.
 *
 * Its own root, mounted the first time it opens, so nothing costs anything
 * until someone presses the keys. host.tsx's Ctrl K branch calls
 * togglePalette(); while the help sheet is open Ctrl K goes to the sheet's
 * own field instead (the same search, inline).
 */

import type { KeyboardEvent as ReactKeyboardEvent, ReactNode } from 'react';

import { Dialog } from '@base-ui/react/dialog';
import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { create } from 'zustand';

import { Icon } from '../icons';
import { currentPageId } from '../page';
import { useHelpStore } from '../store';
import { Kbd } from '../ui';
import { runItem, type Hit } from './index';
import { MarkedQuery, SearchIcon, hitSub, useSearch, useViewer } from './results';
import s from './search.module.css';

interface PaletteState {
  open: boolean;
  query: string;
  setOpen: (open: boolean) => void;
  setQuery: (query: string) => void;
}

export const usePalette = create<PaletteState>((set) => ({
  open: false,
  query: '',
  // a fresh open starts empty, on the suggestions
  setOpen: (open) => set(open ? { open, query: '' } : { open }),
  setQuery: (query) => set({ query }),
}));

const ROOT_ID = 'soulsync-palette-root';

function ensureRoot(): void {
  if (typeof document === 'undefined' || document.getElementById(ROOT_ID)) return;
  const el = document.createElement('div');
  el.id = ROOT_ID;
  document.body.appendChild(el);
  createRoot(el).render(<Palette />);
}

export function openPalette(): void {
  ensureRoot();
  usePalette.getState().setOpen(true);
}

export function closePalette(): void {
  usePalette.getState().setOpen(false);
}

export function isPaletteOpen(): boolean {
  return usePalette.getState().open;
}

export function togglePalette(): void {
  if (isPaletteOpen()) closePalette();
  else openPalette();
}

/** one row of the listbox: a hit, or the hand-off to Search */
interface Row {
  key: string;
  run: () => void;
  hit?: Hit;
}

function isMac(): boolean {
  return typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || '');
}

export function Palette() {
  const open = usePalette((st) => st.open);
  const query = usePalette((st) => st.query);
  const setOpen = usePalette((st) => st.setOpen);
  const setQuery = usePalette((st) => st.setQuery);
  const isAdmin = useHelpStore((st) => st.isAdmin);

  if (!open) return null;
  return (
    <PaletteDialog
      query={query}
      isAdmin={isAdmin}
      onQuery={setQuery}
      onClose={() => setOpen(false)}
    />
  );
}

function PaletteDialog({
  query,
  isAdmin,
  onQuery,
  onClose,
}: {
  query: string;
  isAdmin: boolean;
  onQuery: (q: string) => void;
  onClose: () => void;
}) {
  // the page under the palette, read as it opens
  const [here] = useState(currentPageId);
  const viewer = useViewer(isAdmin, here);
  const { groups, handoff, reading } = useSearch(query, viewer);
  const input = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const picked = useRef(false);
  const base = useId();
  const listId = `${base}-list`;
  const [active, setActive] = useState(0);
  const text = query.trim();

  const rows = useMemo<Row[]>(() => {
    const out: Row[] = [];
    for (const g of groups) {
      for (const hit of g.hits) out.push({ key: hit.item.id, hit, run: () => runItem(hit.item) });
    }
    if (handoff) out.push({ key: 'handoff', run: handoff.run });
    return out;
  }, [groups, handoff]);

  // a new query starts at the top
  useEffect(() => setActive(0), [query]);
  const current = Math.min(active, Math.max(0, rows.length - 1));
  const optionId = (i: number) => `${base}-opt-${i}`;

  useEffect(() => {
    if (!rows.length) return;
    const el = document.getElementById(optionId(current));
    el?.scrollIntoView?.({ block: 'nearest' });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current, rows.length]);

  const pick = useCallback(
    (row: Row | undefined) => {
      if (!row) return;
      picked.current = true;
      onClose();
      row.run();
    },
    [onClose],
  );

  const onKeyDown = (event: ReactKeyboardEvent<HTMLInputElement>) => {
    const n = rows.length;
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      if (!n) return;
      const step = event.key === 'ArrowDown' ? 1 : -1;
      setActive((current + step + n) % n);
      return;
    }
    if ((event.key === 'PageDown' || event.key === 'PageUp') && n) {
      event.preventDefault();
      setActive(event.key === 'PageDown' ? Math.min(n - 1, current + 5) : Math.max(0, current - 5));
      return;
    }
    if (event.key === 'Enter') {
      event.preventDefault();
      pick(rows[current]);
      return;
    }
    if (event.key === 'F1') {
      // the sheet, instead of under the palette
      event.preventDefault();
      onClose();
      useHelpStore.getState().openSheet();
    }
  };

  const placeholder = isAdmin ? 'Search help, settings and actions' : 'Search help and pages';
  const total = rows.length;
  const found = groups.reduce((n, g) => n + g.hits.length, 0);
  let index = -1;

  return (
    <Dialog.Root open onOpenChange={(next) => (next ? null : onClose())}>
      <Dialog.Portal>
        <Dialog.Backdrop className={s.scrim} />
        <Dialog.Viewport className={s.viewport}>
          <Dialog.Popup
            className={s.pal}
            aria-label="Search everything"
            initialFocus={input}
            finalFocus={() => !picked.current}
            data-help-palette=""
          >
            <div className={s.palIn}>
              <Icon name="search" size={18} />
              <input
                ref={input}
                className={s.palInput}
                type="search"
                role="combobox"
                aria-label={placeholder}
                aria-expanded={total > 0}
                aria-controls={listId}
                aria-autocomplete="list"
                aria-activedescendant={total ? optionId(current) : undefined}
                placeholder={placeholder}
                autoComplete="off"
                spellCheck={false}
                enterKeyHint="go"
                value={query}
                onChange={(e) => onQuery(e.target.value)}
                onKeyDown={onKeyDown}
              />
              {/* a tap target on a phone, where there is no Esc key */}
              <Dialog.Close className={s.esc} aria-label="Close search">
                <Kbd>Esc</Kbd>
              </Dialog.Close>
            </div>

            <div ref={list} id={listId} className={s.palList} role="listbox" aria-label="Results">
              {groups.map((group) => {
                const labelId = `${base}-${group.kind}-${group.label}`;
                return (
                  <div key={group.label} role="group" aria-labelledby={labelId}>
                    <div id={labelId} className={s.pg} role="presentation">
                      {group.label}
                    </div>
                    {group.hits.map((hit) => {
                      index += 1;
                      const i = index;
                      return (
                        <Option
                          key={hit.item.id}
                          id={optionId(i)}
                          active={i === current}
                          onHover={() => setActive(i)}
                          onPick={() => pick(rows[i])}
                          icon={<SearchIcon name={hit.item.icon} />}
                          title={<MarkedQuery text={hit.item.title} query={query} />}
                          sub={hitSub(hit)}
                          query={query}
                          end={
                            i === current ? (
                              'Enter'
                            ) : hit.item.keys?.length ? (
                              <>
                                {hit.item.keys.map((k) => (
                                  <Kbd key={k}>{k}</Kbd>
                                ))}
                              </>
                            ) : null
                          }
                        />
                      );
                    })}
                  </div>
                );
              })}
              {reading && text ? (
                <div className={s.reading} role="presentation">
                  Reading the guides
                </div>
              ) : null}
              {text && !found && !reading ? (
                <div className={s.empty} role="presentation">
                  <p className={s.emptyTitle}>Nothing matches “{text}”</p>
                  <p className={s.emptyText}>
                    Try fewer words, or what the thing does: “scan”, “folder”, “password”.
                  </p>
                </div>
              ) : null}
              {handoff ? (
                <div role="group" aria-labelledby={`${base}-handoff`}>
                  <div id={`${base}-handoff`} className={s.pg} role="presentation">
                    {viewer.side === 'video' ? 'Movies and TV' : 'Music'}
                  </div>
                  {(() => {
                    const i = rows.length - 1;
                    return (
                      <Option
                        id={optionId(i)}
                        active={i === current}
                        onHover={() => setActive(i)}
                        onPick={() => pick(rows[i])}
                        icon={<Icon name="search" size={15} />}
                        title={
                          <>
                            {handoff.label} “<MarkedQuery text={text} query={query} />”
                          </>
                        }
                        sub={handoff.hint}
                        query=""
                        end={i === current ? 'Enter' : null}
                      />
                    );
                  })()}
                </div>
              ) : null}
            </div>

            <div className={s.palFoot} aria-hidden="true">
              <span className={s.footKeys}>
                <Kbd>↑</Kbd>
                <Kbd>↓</Kbd>
                move
              </span>
              <span className={s.footKeys}>
                <Kbd>Enter</Kbd>
                open
              </span>
              <span className={s.footKeys}>
                <Kbd>Esc</Kbd>
                close
              </span>
              <span className={s.footNote}>
                {isAdmin
                  ? 'Help, settings, actions and pages in one place'
                  : 'Help and pages in one place'}
              </span>
            </div>
            <p className={s.srOnly} role="status" aria-live="polite">
              {text
                ? found
                  ? `${found} ${found === 1 ? 'result' : 'results'}`
                  : reading
                    ? ''
                    : 'No results'
                : ''}
            </p>
            <p className={s.srOnly}>
              {isMac() ? 'Command' : 'Control'} K opens this from anywhere.
            </p>
          </Dialog.Popup>
        </Dialog.Viewport>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function Option({
  id,
  active,
  onHover,
  onPick,
  icon,
  title,
  sub,
  query,
  end,
}: {
  id: string;
  active: boolean;
  onHover: () => void;
  onPick: () => void;
  icon: ReactNode;
  title: ReactNode;
  sub: string;
  query: string;
  end: ReactNode;
}) {
  return (
    <div
      id={id}
      className={s.pr}
      role="option"
      aria-selected={active}
      data-active={active || undefined}
      // mousemove, not mouseenter: a list scrolling under a still pointer must not steal the keys
      onMouseMove={active ? undefined : onHover}
      onMouseDown={(e) => e.preventDefault()}
      onClick={onPick}
    >
      <span className={s.prIcon} aria-hidden="true">
        {icon}
      </span>
      <span className={s.prT}>
        {title}
        {sub ? (
          <span className={s.prSub}>
            <MarkedQuery text={sub} query={query} />
          </span>
        ) : null}
      </span>
      {end ? <span className={s.prR}>{end}</span> : null}
    </div>
  );
}
