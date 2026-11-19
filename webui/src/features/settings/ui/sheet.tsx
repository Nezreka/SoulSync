/**
 * The side sheet: one thing's settings beside the page that opened it, the
 * whole screen on a phone. Head (mark, name, where it is used, status, close),
 * a body that scrolls, and a foot that stays put (a Test with its answer on
 * the left, the main action on the right).
 *
 * It stays mounted while closed, so what was typed in it is still there when
 * it opens again. Escape and the scrim close it; Tab stays inside while it is
 * open, and focus goes back to what opened it.
 */

import { type ReactNode, useEffect, useRef } from 'react';

import { Btn } from './parts';
import s from './settings.module.css';

const FOCUSABLE = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

function focusable(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
    (el) => !el.closest('[hidden]') && el.getAttribute('aria-hidden') !== 'true',
  );
}

export function Sheet({
  open,
  onClose,
  id,
  title,
  titleId,
  sub,
  mark,
  status,
  foot,
  bodyId,
  bodyClass,
  focusFirst = 'input:not([type=hidden]), select, textarea',
  children,
}: {
  open: boolean;
  onClose: () => void;
  id?: string;
  title: ReactNode;
  titleId: string;
  /** where it is used, under the name */
  sub?: ReactNode;
  mark?: ReactNode;
  status?: ReactNode;
  foot?: ReactNode;
  bodyId?: string;
  /** on <body> while open (the page's own scroll lock) */
  bodyClass?: string;
  /** what takes focus on open, inside the body; the sheet itself otherwise */
  focusFirst?: string;
  children?: ReactNode;
}) {
  const sheet = useRef<HTMLElement>(null);
  const body = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  close.current = onClose;

  useEffect(() => {
    const node = sheet.current;
    if (!open || !node) return;
    const previous = document.activeElement as HTMLElement | null;
    if (bodyClass) document.body.classList.add(bodyClass);
    // after the open thing's content is in the body
    const focus = setTimeout(() => {
      (body.current?.querySelector<HTMLElement>(focusFirst) ?? node).focus({ preventScroll: true });
    }, 40);
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        // a menu or picker inside that takes Escape stops it getting here
        event.stopPropagation();
        close.current();
        return;
      }
      if (event.key !== 'Tab') return;
      const items = focusable(node);
      if (!items.length) {
        event.preventDefault();
        node.focus({ preventScroll: true });
        return;
      }
      const edge = event.shiftKey ? items[0] : items[items.length - 1];
      if (document.activeElement === edge || !node.contains(document.activeElement)) {
        event.preventDefault();
        (event.shiftKey ? items[items.length - 1] : items[0]).focus({ preventScroll: true });
      }
    };
    node.addEventListener('keydown', onKeyDown);
    // Escape with focus outside the sheet (the page behind it) closes it too
    const onDocKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !node.contains(event.target as Node)) close.current();
    };
    document.addEventListener('keydown', onDocKey);
    return () => {
      clearTimeout(focus);
      node.removeEventListener('keydown', onKeyDown);
      document.removeEventListener('keydown', onDocKey);
      if (bodyClass) document.body.classList.remove(bodyClass);
      const active = document.activeElement;
      if (previous?.isConnected && (!active || active === document.body || node.contains(active)))
        previous.focus({ preventScroll: true });
    };
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <>
      <div className={s.scrim} hidden={!open} onClick={() => onClose()} data-sheet-scrim="" />
      <aside
        ref={sheet}
        id={id}
        className={s.sheet}
        hidden={!open}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        data-stg-ui=""
      >
        <div className={s.sheetHead}>
          {mark}
          <div className={s.sheetTitles}>
            <h3 className={s.sheetTitle} id={titleId}>
              {title}
            </h3>
            {sub ? <p className={s.sheetSub}>{sub}</p> : null}
          </div>
          {status}
          <Btn kind="ghost" iconOnly icon="x" aria-label="Close" onClick={() => onClose()} />
        </div>
        <div className={s.sheetBody} id={bodyId} ref={body}>
          {children}
        </div>
        {foot ? <div className={s.sheetFoot}>{foot}</div> : null}
      </aside>
    </>
  );
}

/** the left of a sheet's foot: the answer takes the room, the main action sits right */
export function SheetFootSay({ children }: { children: ReactNode }) {
  return <div className={s.sheetFootSay}>{children}</div>;
}

/** a labelled group of fields in a sheet */
export function SheetGroup({ title }: { title: string }) {
  return <h4 className={s.grp}>{title}</h4>;
}
