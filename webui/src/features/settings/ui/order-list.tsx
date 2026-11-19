/**
 * A list whose order is the setting (settings-bic DESIGN.md section 5): the
 * download sources, and later the quality targets, the art sources and the
 * subtitle providers.
 *
 * Every way of moving a row ends in the same `onChange(next, change)`, and
 * only when the order really changed:
 *  - the handle, dragged with a mouse, a finger or a pen (pointer events; the
 *    handle alone has touch-action: none, so the page still scrolls from
 *    anywhere else on the row),
 *  - the handle with the keyboard: Space or Enter picks the row up, the arrows
 *    move it, Space or Enter drops it, Escape puts it back; Alt with an arrow
 *    moves it one place without picking it up,
 *  - the ⋯ menu, a sheet from the bottom on a phone: every move as a button.
 *
 * Rows not in the order (the pool) sit under it: "Use" adds one at the end,
 * dragging one in places it, dragging a row out removes it. Nothing here
 * listens to the browser's own drag and drop, so text dragged in from
 * elsewhere is never taken for a row.
 *
 * While a row is in the hand it is fixed to the pointer and a slot the height
 * of a row stands where it would land, saying what the drop means. The other
 * rows slide (FLIP, skipped under prefers-reduced-motion). A live region says
 * each step.
 */

import clsx from 'clsx';
import {
  type CSSProperties,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from 'react';

import { Icon, type IconName } from './icons';
import { Btn, StatusWords, type Tone } from './parts';
import s from './settings.module.css';

export interface OrderMenuEntry {
  key: string;
  label: string;
  icon?: IconName;
  onSelect: () => void;
  disabled?: boolean;
}

export interface OrderItem {
  id: string;
  name: string;
  mark: ReactNode;
  status?: { tone: Tone; text: string };
  /** in the order but not working: dashed, amber */
  warn?: boolean;
  /** the row's own button (Settings, Set up) */
  action?: ReactNode;
  /** ⋯ menu entries between the moves and the removal */
  menu?: OrderMenuEntry[];
  /** the row's own control stays on a phone too (a switch per row); a
   *  button that the menu repeats gives way there */
  keepAction?: boolean;
}

export interface PoolItem {
  id: string;
  name: string;
  mark: ReactNode;
  line: ReactNode;
  /** beside Use (a gear) */
  action?: ReactNode;
}

export interface OrderChange {
  type: 'move' | 'add' | 'remove';
  id: string;
  /** places in the order, from 0; -1 for "not in it" */
  from: number;
  to: number;
  by: 'drag' | 'keys' | 'menu' | 'button';
}

export interface OrderListProps {
  /** what the list is, for a screen reader: "Download order for music" */
  label: string;
  items: OrderItem[];
  /** what a row's place means, in words, for any order (the live one while dragging) */
  role: (id: string, index: number, order: string[]) => string;
  /** what the slot says while a row is over it */
  dropText?: (id: string, index: number, order: string[]) => string;
  /** added to "Dropped": the order read back */
  afterText?: (order: string[]) => string;
  onChange: (next: string[], change: OrderChange) => void;
  pool?: PoolItem[];
  /** between the order and the pool (its section label) */
  poolHead?: ReactNode;
  poolEmpty?: string;
  /** the fewest rows the order may hold */
  minItems?: number;
  /** why the last one cannot go */
  keepText?: string;
  removeLabel?: string;
  addLabel?: string;
  /** on <body> while a row's menu is open: on a phone the menu is a sheet
   *  from the bottom, and the page's floating buttons must step aside */
  menuBodyClass?: string;
  /** a line drawn after the row at this place (the upgrade cutoff): its words */
  marker?: { after: number; text: string } | null;
}

interface Drag {
  id: string;
  fromPool: boolean;
  from: number;
  pointerType: string;
  startX: number;
  startY: number;
  x: number;
  y: number;
  started: boolean;
  rect: { left: number; top: number; width: number; height: number };
  to: number | 'pool';
}

const DRAG_START_PX = 4;
const EDGE_PX = 70;

const reducedMotion = () =>
  typeof window.matchMedia === 'function' &&
  window.matchMedia('(prefers-reduced-motion: reduce)').matches;

function scrollParent(el: HTMLElement | null): HTMLElement | null {
  let node = el?.parentElement ?? null;
  while (node) {
    const overflow = getComputedStyle(node).overflowY;
    if ((overflow === 'auto' || overflow === 'scroll') && node.scrollHeight > node.clientHeight)
      return node;
    node = node.parentElement;
  }
  return null;
}

const place = (ids: string[], id: string, to: number) => {
  const next = ids.filter((x) => x !== id);
  next.splice(Math.max(0, Math.min(to, next.length)), 0, id);
  return next;
};
const same = (a: string[], b: string[]) => a.length === b.length && a.every((x, i) => x === b[i]);

export function OrderList({
  label,
  items,
  role,
  dropText,
  afterText,
  onChange,
  pool,
  poolHead,
  poolEmpty,
  minItems = 1,
  keepText = 'Keep at least one',
  removeLabel = 'Stop using',
  addLabel = 'Use',
  menuBodyClass,
  marker,
}: OrderListProps) {
  const ids = items.map((item) => item.id);
  const byId = new Map(items.map((item) => [item.id, item]));
  const poolById = new Map((pool ?? []).map((item) => [item.id, item]));
  const nameOf = (id: string) => byId.get(id)?.name ?? poolById.get(id)?.name ?? id;
  const canRemove = ids.length > minItems;

  const [drag, setDrag] = useState<Drag | null>(null);
  const [held, setHeld] = useState<{ id: string; from: number; order: string[] } | null>(null);
  const [menu, setMenu] = useState<{ id: string; up: boolean } | null>(null);
  const [say, setSay] = useState('');

  const listEl = useRef<HTMLUListElement>(null);
  const poolEl = useRef<HTMLUListElement>(null);
  const rowEls = useRef(new Map<string, HTMLElement>());
  const gripEls = useRef(new Map<string, HTMLButtonElement>());
  const moreEls = useRef(new Map<string, HTMLButtonElement>());
  const menuEl = useRef<HTMLDivElement>(null);
  // what the pointer handlers read: the render's values go stale under them
  const live = useRef({ ids, drag, canRemove, onChange });
  live.current = { ids, drag, canRemove, onChange };

  // ── slide (FLIP): where each row was, played from there to where it is ──
  const before = useRef<Map<string, DOMRect> | null>(null);
  const focusAfter = useRef<{ id: string; what: 'grip' | 'more' } | null>(null);
  const capture = () => {
    const rects = new Map<string, DOMRect>();
    for (const [id, el] of rowEls.current)
      if (el.isConnected) rects.set(id, el.getBoundingClientRect());
    before.current = rects;
  };
  useLayoutEffect(() => {
    // a row React moved in the document loses focus: give it back
    const focus = focusAfter.current;
    if (focus) {
      focusAfter.current = null;
      const el = (focus.what === 'grip' ? gripEls : moreEls).current.get(focus.id);
      if (el && document.activeElement !== el) el.focus({ preventScroll: true });
    }
    const was = before.current;
    before.current = null;
    if (!was || reducedMotion()) return;
    for (const [id, el] of rowEls.current) {
      const from = was.get(id);
      if (!from || typeof el.animate !== 'function') continue;
      const now = el.getBoundingClientRect();
      const dx = from.left - now.left;
      const dy = from.top - now.top;
      if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5) continue;
      el.animate([{ transform: `translate(${dx}px, ${dy}px)` }, { transform: 'none' }], {
        duration: 200,
        easing: 'cubic-bezier(0, 0, 0.2, 1)',
      });
    }
  });

  const commit = (next: string[], change: OrderChange) => {
    if (same(next, live.current.ids)) return false;
    capture();
    live.current.onChange(next, change);
    return true;
  };

  // ── the pointer ──

  /** where the row in the hand would land, from where its middle is */
  const target = (d: Drag, y: number): number | 'pool' => {
    const middle = d.rect.top + d.rect.height / 2 + (y - d.startY);
    const list = listEl.current?.getBoundingClientRect();
    const poolBox = poolEl.current?.getBoundingClientRect();
    if (d.fromPool) {
      // only once it is brought up to the order
      if (!list || middle > list.bottom + d.rect.height / 2) return 'pool';
    } else if (poolBox && live.current.canRemove && middle > poolBox.top - 4) {
      return 'pool';
    }
    let index = 0;
    for (const id of live.current.ids) {
      if (id === d.id) continue;
      const box = rowEls.current.get(id)?.getBoundingClientRect();
      if (box && box.top + box.height / 2 < middle) index += 1;
    }
    return index;
  };

  const onPointerMove = useRef<(event: PointerEvent) => void>(() => undefined);
  const onPointerUp = useRef<(event: PointerEvent) => void>(() => undefined);
  const onDragKey = useRef<(event: KeyboardEvent) => void>(() => undefined);
  const scroller = useRef<{ el: HTMLElement | null; frame: number }>({ el: null, frame: 0 });

  const stopDrag = () => {
    window.removeEventListener('pointermove', move);
    window.removeEventListener('pointerup', up);
    window.removeEventListener('pointercancel', cancel);
    window.removeEventListener('keydown', key, true);
    cancelAnimationFrame(scroller.current.frame);
    scroller.current.frame = 0;
    document.body.style.userSelect = '';
  };
  // stable listeners that call the latest closures
  const [move] = useState(() => (event: PointerEvent) => onPointerMove.current(event));
  const [up] = useState(() => (event: PointerEvent) => onPointerUp.current(event));
  const [cancel] = useState(() => () => {
    stopDrag();
    if (live.current.drag?.started) capture();
    setDrag(null);
  });
  const [key] = useState(() => (event: KeyboardEvent) => onDragKey.current(event));
  useEffect(() => stopDrag, []); // eslint-disable-line react-hooks/exhaustive-deps

  const update = (x: number, y: number) => {
    const d = live.current.drag;
    if (!d) return;
    let started = d.started;
    if (!started) {
      if (Math.hypot(x - d.startX, y - d.startY) < DRAG_START_PX) return;
      started = true;
      document.body.style.userSelect = 'none';
      setSay(
        d.fromPool
          ? `Picked up ${nameOf(d.id)}`
          : `Picked up ${nameOf(d.id)}, position ${d.from + 1} of ${live.current.ids.length}`,
      );
    }
    const to = target(d, y);
    // the others slide to make room
    if (to !== d.to || !d.started) capture();
    const next = { ...d, x, y, started, to };
    live.current.drag = next;
    setDrag(next);
  };

  onPointerMove.current = (event) => {
    update(event.clientX, event.clientY);
    // near the top or bottom edge: the page scrolls under the row
    const loop = () => {
      const d = live.current.drag;
      scroller.current.frame = 0;
      if (!d?.started) return;
      const el = scroller.current.el;
      const box = el ? el.getBoundingClientRect() : { top: 0, bottom: window.innerHeight };
      const by =
        d.y < box.top + EDGE_PX
          ? -Math.ceil((box.top + EDGE_PX - d.y) / 6)
          : d.y > box.bottom - EDGE_PX
            ? Math.ceil((d.y - (box.bottom - EDGE_PX)) / 6)
            : 0;
      if (!by) return;
      if (el) el.scrollTop += by;
      else window.scrollBy(0, by);
      update(d.x, d.y);
      scroller.current.frame = requestAnimationFrame(loop);
    };
    if (!scroller.current.frame) scroller.current.frame = requestAnimationFrame(loop);
  };

  onPointerUp.current = () => {
    const d = live.current.drag;
    stopDrag();
    setDrag(null);
    live.current.drag = null;
    if (!d?.started) return;
    const order = live.current.ids;
    const total = order.length;
    if (d.fromPool) {
      if (d.to === 'pool') {
        capture();
        return;
      }
      const next = place(order, d.id, d.to);
      commit(next, { type: 'add', id: d.id, from: -1, to: d.to, by: 'drag' });
      setSay(`Dropped. ${nameOf(d.id)} is at position ${d.to + 1} of ${total + 1}.`);
      return;
    }
    if (d.to === 'pool') {
      commit(
        order.filter((x) => x !== d.id),
        { type: 'remove', id: d.id, from: d.from, to: -1, by: 'drag' },
      );
      setSay(`Dropped. ${nameOf(d.id)} is no longer used.`);
      return;
    }
    const next = place(order, d.id, d.to);
    if (!commit(next, { type: 'move', id: d.id, from: d.from, to: d.to, by: 'drag' })) {
      // put down where it was: nothing changed, nothing is saved
      capture();
      setSay(`Dropped. ${nameOf(d.id)} is back at position ${d.from + 1} of ${total}.`);
      return;
    }
    setSay(
      `Dropped. ${afterText?.(next) ?? `${nameOf(d.id)} is at position ${d.to + 1} of ${total}.`}`,
    );
  };

  onDragKey.current = (event) => {
    if (event.key !== 'Escape') return;
    event.preventDefault();
    event.stopPropagation();
    const d = live.current.drag;
    cancel();
    if (d?.started) setSay(`Cancelled. ${nameOf(d.id)} was not moved.`);
  };

  const startDrag = (id: string, fromPool: boolean) => (event: ReactPointerEvent<HTMLElement>) => {
    if (event.button !== 0 && event.pointerType === 'mouse') return;
    if (held || menu) return;
    const row = rowEls.current.get(id);
    if (!row) return;
    const box = row.getBoundingClientRect();
    const from = ids.indexOf(id);
    const d: Drag = {
      id,
      fromPool,
      from,
      pointerType: event.pointerType,
      startX: event.clientX,
      startY: event.clientY,
      x: event.clientX,
      y: event.clientY,
      started: false,
      rect: { left: box.left, top: box.top, width: box.width, height: box.height },
      to: fromPool ? 'pool' : from,
    };
    live.current.drag = d;
    setDrag(d);
    scroller.current.el = scrollParent(row);
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
    window.addEventListener('pointercancel', cancel);
    window.addEventListener('keydown', key, true);
    // a finger starts at once: the handle does not scroll the page
    if (event.pointerType !== 'mouse') update(event.clientX, event.clientY + DRAG_START_PX);
  };

  // ── the keyboard ──

  const step = (id: string, delta: number, by: 'keys' | 'menu', what: 'grip' | 'more') => {
    const from = ids.indexOf(id);
    const to = from + delta;
    if (from < 0 || to < 0 || to >= ids.length) return;
    const next = place(ids, id, to);
    focusAfter.current = { id, what };
    commit(next, { type: 'move', id, from, to, by });
    setSay(`${nameOf(id)}, moved to position ${to + 1} of ${ids.length}`);
  };

  const dropHeld = () => {
    if (!held) return;
    const to = held.order.indexOf(held.id);
    setHeld(null);
    focusAfter.current = { id: held.id, what: 'grip' };
    const changed = commit(held.order, {
      type: 'move',
      id: held.id,
      from: held.from,
      to,
      by: 'keys',
    });
    setSay(
      changed
        ? `Dropped. ${afterText?.(held.order) ?? `${nameOf(held.id)} is at position ${to + 1} of ${ids.length}.`}`
        : `Dropped. ${nameOf(held.id)} is back at position ${held.from + 1} of ${ids.length}.`,
    );
  };
  const cancelHeld = () => {
    if (!held) return;
    capture();
    focusAfter.current = { id: held.id, what: 'grip' };
    setHeld(null);
    setSay(`Cancelled. ${nameOf(held.id)} is back at position ${held.from + 1} of ${ids.length}.`);
  };

  const onGripKey = (id: string) => (event: ReactKeyboardEvent<HTMLButtonElement>) => {
    const arrow = event.key === 'ArrowUp' ? -1 : event.key === 'ArrowDown' ? 1 : 0;
    if (event.key === ' ' || event.key === 'Enter') {
      event.preventDefault();
      if (held) dropHeld();
      else if (ids.length > 1) {
        const from = ids.indexOf(id);
        setHeld({ id, from, order: ids.slice() });
        setSay(
          `Picked up ${nameOf(id)}, position ${from + 1} of ${ids.length}. Arrow keys move it, Space drops it, Escape puts it back.`,
        );
      }
      return;
    }
    if (event.key === 'Escape' && held) {
      event.preventDefault();
      event.stopPropagation();
      cancelHeld();
      return;
    }
    if (!arrow) return;
    if (event.altKey && !held) {
      event.preventDefault();
      step(id, arrow, 'keys', 'grip');
      return;
    }
    if (!held) return;
    event.preventDefault();
    const at = held.order.indexOf(id);
    const to = at + arrow;
    if (to < 0 || to >= held.order.length) return;
    capture();
    focusAfter.current = { id, what: 'grip' };
    setHeld({ ...held, order: place(held.order, id, to) });
    setSay(`${nameOf(id)}, moved to position ${to + 1} of ${ids.length}`);
  };

  /** focus left the handle with a row picked up: it goes back. Checked a
   *  moment later, because a row React moves loses focus and gets it back */
  const onGripBlur = (id: string) => () => {
    if (!held) return;
    setTimeout(() => {
      if (document.activeElement !== gripEls.current.get(id)) {
        setHeld((h) => {
          if (h?.id === id) setSay(`Cancelled. ${nameOf(id)} was not moved.`);
          return h?.id === id ? null : h;
        });
      }
    }, 0);
  };

  // ── the ⋯ menu ──

  const closeMenu = (refocus = true) => {
    if (!menu) return;
    const id = menu.id;
    setMenu(null);
    if (refocus) moreEls.current.get(id)?.focus({ preventScroll: true });
  };
  useEffect(() => {
    if (!menu) return;
    menuEl.current?.querySelector<HTMLElement>('[role="menuitem"]:not([disabled])')?.focus();
    const onDown = (event: PointerEvent) => {
      const at = event.target as Node;
      if (menuEl.current?.contains(at) || moreEls.current.get(menu.id)?.contains(at)) return;
      setMenu(null);
    };
    document.addEventListener('pointerdown', onDown);
    if (menuBodyClass) document.body.classList.add(menuBodyClass);
    return () => {
      document.removeEventListener('pointerdown', onDown);
      if (menuBodyClass) document.body.classList.remove(menuBodyClass);
    };
  }, [menu]); // eslint-disable-line react-hooks/exhaustive-deps

  const onMenuKey = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      closeMenu();
      return;
    }
    if (event.key === 'Tab') {
      closeMenu(false);
      return;
    }
    const dir = event.key === 'ArrowDown' ? 1 : event.key === 'ArrowUp' ? -1 : 0;
    if (!dir && event.key !== 'Home' && event.key !== 'End') return;
    event.preventDefault();
    const entries = Array.from(
      event.currentTarget.querySelectorAll<HTMLElement>('[role="menuitem"]:not([disabled])'),
    );
    if (!entries.length) return;
    const at = entries.indexOf(document.activeElement as HTMLElement);
    const next =
      event.key === 'Home'
        ? 0
        : event.key === 'End'
          ? entries.length - 1
          : (at + dir + entries.length) % entries.length;
    entries[next].focus();
  };

  const remove = (id: string, by: 'menu' | 'drag') => {
    if (!canRemove) return;
    const from = ids.indexOf(id);
    commit(
      ids.filter((x) => x !== id),
      { type: 'remove', id, from, to: -1, by },
    );
    setSay(`${nameOf(id)} is no longer used.`);
  };
  const add = (id: string) => {
    if (ids.includes(id)) return;
    commit([...ids, id], { type: 'add', id, from: -1, to: ids.length, by: 'button' });
    setSay(`${nameOf(id)} added at position ${ids.length + 1} of ${ids.length + 1}.`);
  };

  // ── drawing ──

  const dragging = drag?.started ? drag : null;
  // the order as it would be if the row were let go now
  const shown = held
    ? held.order
    : dragging
      ? dragging.to === 'pool'
        ? ids.filter((x) => x !== dragging.id)
        : place(ids, dragging.id, dragging.to)
      : ids;
  const fixedStyle = (d: Drag): CSSProperties => ({
    position: 'fixed',
    left: d.rect.left + (d.fromPool ? d.x - d.startX : 0),
    top: d.rect.top + (d.y - d.startY),
    width: d.rect.width,
    height: d.rect.height,
    margin: 0,
    pointerEvents: 'none',
  });
  const setRow = (id: string) => (el: HTMLElement | null) => {
    if (el) rowEls.current.set(id, el);
    else rowEls.current.delete(id);
  };
  const slotAt = dragging && dragging.to !== 'pool' ? dragging.to : -1;
  const slotHeight = dragging
    ? dragging.fromPool
      ? (rowEls.current.get(ids[0])?.offsetHeight ?? dragging.rect.height)
      : dragging.rect.height
    : 0;

  const menuFor = (item: OrderItem, index: number) => (
    <>
      <div className={s.menuScrim} onClick={() => closeMenu()} />
      <div
        ref={menuEl}
        className={clsx(s.menu, menu?.up && s.menuUp)}
        role="menu"
        aria-label={`${item.name}: move or change`}
        onKeyDown={onMenuKey}
      >
        <div className={s.menuGrab} aria-hidden="true" />
        <div className={s.menuWho} aria-hidden="true">
          {item.mark}
          <span>
            <b className={s.menuWhoName}>{item.name}</b>
            <span className={s.menuWhoLine}>{role(item.id, index, ids)}</span>
          </span>
        </div>
        <button
          type="button"
          role="menuitem"
          className={s.menuItem}
          disabled={index === 0}
          onClick={() => {
            setMenu(null);
            step(item.id, -1, 'menu', 'more');
          }}
        >
          <Icon name="up" />
          Move up
          <kbd className={s.menuKbd}>Alt ↑</kbd>
        </button>
        <button
          type="button"
          role="menuitem"
          className={s.menuItem}
          disabled={index === ids.length - 1}
          onClick={() => {
            setMenu(null);
            step(item.id, 1, 'menu', 'more');
          }}
        >
          <Icon name="down" />
          Move down
          <kbd className={s.menuKbd}>Alt ↓</kbd>
        </button>
        {(item.menu ?? []).map((entry) => (
          <button
            key={entry.key}
            type="button"
            role="menuitem"
            className={s.menuItem}
            disabled={entry.disabled}
            onClick={() => {
              closeMenu();
              entry.onSelect();
            }}
          >
            {entry.icon ? <Icon name={entry.icon} /> : null}
            {entry.label}
          </button>
        ))}
        <hr className={s.menuHr} />
        <button
          type="button"
          role="menuitem"
          className={clsx(s.menuItem, canRemove && s.menuDanger)}
          disabled={!canRemove}
          onClick={() => {
            setMenu(null);
            remove(item.id, 'menu');
          }}
        >
          <Icon name="x" />
          {removeLabel}
          {canRemove ? null : <span className={s.menuKbd}>{keepText}</span>}
        </button>
      </div>
    </>
  );

  return (
    <div data-order-list="">
      <ul
        ref={listEl}
        className={s.order}
        aria-label={label}
        data-busy={dragging || held ? '' : undefined}
      >
        {/* first in the DOM, so the last row is still :last-child (its rail stops) */}
        {marker && marker.after >= 0 && marker.after < ids.length - 1 ? (
          <li
            className={s.marker}
            data-marker=""
            role="presentation"
            style={{ order: marker.after * 2 + 1 }}
          >
            <span>{marker.text}</span>
          </li>
        ) : null}
        {items.map((item) => {
          const index = shown.indexOf(item.id);
          const inHand = dragging?.id === item.id;
          const isHeld = held?.id === item.id;
          const first = index === 0 && !(inHand && dragging.to === 'pool');
          return (
            <li
              key={item.id}
              ref={setRow(item.id)}
              data-id={item.id}
              data-first={first ? '' : undefined}
              className={clsx(
                s.src,
                item.mark == null && s.srcNoMark,
                first && s.srcFirst,
                item.warn && s.srcWarn,
                inHand && s.srcLift,
                isHeld && s.srcHeld,
              )}
              style={inHand ? fixedStyle(dragging) : { order: index * 2 }}
            >
              <button
                type="button"
                ref={(el) => {
                  if (el) gripEls.current.set(item.id, el);
                  else gripEls.current.delete(item.id);
                }}
                className={s.grip}
                data-grip=""
                aria-label={`Reorder ${item.name}, ${ids.indexOf(item.id) + 1} of ${ids.length}`}
                aria-pressed={isHeld}
                title="Drag to reorder. Space picks up, arrows move."
                disabled={ids.length < 2 && !pool?.length}
                onPointerDown={startDrag(item.id, false)}
                onKeyDown={onGripKey(item.id)}
                onBlur={onGripBlur(item.id)}
              >
                <Icon name="grip" />
              </button>
              <span className={s.rank} aria-hidden="true">
                {index < 0 ? '' : index + 1}
              </span>
              {item.mark}
              <div className={s.srcText}>
                <div className={s.srcName}>{item.name}</div>
                <div className={s.srcRole}>
                  {isHeld
                    ? `Moving. Now ${index + 1} of ${ids.length}.`
                    : index < 0
                      ? removeLabel
                      : role(item.id, index, shown)}
                </div>
                {item.status ? (
                  <div className={s.srcStatLine}>
                    <StatusWords tone={item.status.tone}>{item.status.text}</StatusWords>
                  </div>
                ) : null}
              </div>
              {item.status ? (
                <StatusWords tone={item.status.tone} className={s.srcStatus}>
                  {item.status.text}
                </StatusWords>
              ) : (
                <span />
              )}
              <div className={s.srcActs}>
                {item.action ? (
                  <span className={clsx(s.srcSet, item.keepAction && s.srcSetKeep)}>
                    {item.action}
                  </span>
                ) : null}
                <Btn
                  kind="ghost"
                  small
                  iconOnly
                  icon="more"
                  data-more={item.id}
                  aria-label={`More for ${item.name}`}
                  aria-haspopup="menu"
                  aria-expanded={menu?.id === item.id}
                  onClick={(event) => {
                    moreEls.current.set(item.id, event.currentTarget);
                    const box = event.currentTarget.getBoundingClientRect();
                    setMenu(
                      menu?.id === item.id
                        ? null
                        : { id: item.id, up: box.bottom + 260 > window.innerHeight },
                    );
                  }}
                />
                {menu?.id === item.id ? menuFor(item, ids.indexOf(item.id)) : null}
              </div>
            </li>
          );
        })}
        {slotAt >= 0 && dragging ? (
          <li
            className={s.gap}
            aria-hidden="true"
            data-slot=""
            style={{ order: slotAt * 2 - 1, height: slotHeight }}
          >
            {dropText?.(dragging.id, slotAt, shown) ?? ''}
          </li>
        ) : null}
      </ul>

      {pool ? (
        <>
          {poolHead}
          <ul
            ref={poolEl}
            className={clsx(
              s.unused,
              dragging && !dragging.fromPool && dragging.to === 'pool' && s.unusedOver,
            )}
            data-pool=""
          >
            {pool.length === 0 && poolEmpty ? <li className={s.unEmpty}>{poolEmpty}</li> : null}
            {pool.map((item) => {
              const inHand = dragging?.id === item.id;
              return (
                <li
                  key={item.id}
                  ref={setRow(item.id)}
                  data-id={item.id}
                  className={clsx(s.un, s.unGrab, inHand && s.srcLift)}
                  style={inHand ? fixedStyle(dragging) : undefined}
                  onPointerDown={(event) => {
                    // a mouse or pen drags the row itself; a finger scrolls
                    // the page and uses the button
                    if (event.pointerType === 'touch') return;
                    if ((event.target as HTMLElement).closest('button, a, input')) return;
                    startDrag(item.id, true)(event);
                  }}
                >
                  {item.mark}
                  <div className={s.srcText}>
                    <div className={s.unName}>{item.name}</div>
                    <div className={s.unLine}>{item.line}</div>
                  </div>
                  <div className={s.unActs}>
                    {item.action}
                    <Btn
                      small
                      icon="plus"
                      data-use={item.id}
                      aria-label={`${addLabel} ${item.name}`}
                      onClick={() => add(item.id)}
                    >
                      {addLabel}
                    </Btn>
                  </div>
                </li>
              );
            })}
          </ul>
        </>
      ) : null}

      <div className={s.srOnly} role="status" aria-live="polite" aria-atomic="true" data-live="">
        {say}
      </div>
    </div>
  );
}
