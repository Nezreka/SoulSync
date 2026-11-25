/**
 * a dialog in the player's own look: a panel over a dimmed page, a title,
 * what it's for, and its buttons. escape or a click outside closes it.
 * ConfirmSheet asks before something that can't be undone.
 */

import { useEffect, useRef, type ReactNode } from 'react';

import { XIcon } from './icons';
import s from './sheet.module.css';
import { inPlayer } from './trailer';

export function Sheet({
  title,
  subtitle,
  onClose,
  children,
  footer,
  wide,
}: {
  title: string;
  subtitle?: ReactNode;
  onClose: () => void;
  children?: ReactNode;
  footer?: ReactNode;
  wide?: boolean;
}) {
  const panel = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const back = document.activeElement as HTMLElement | null;
    // the first field, else the first button
    const first =
      panel.current?.querySelector<HTMLElement>('input, textarea, select') ??
      panel.current?.querySelector<HTMLElement>('button:not([data-close])');
    first?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      e.preventDefault();
      e.stopImmediatePropagation();
      closeRef.current();
    };
    document.addEventListener('keydown', onKey, true);
    return () => {
      document.removeEventListener('keydown', onKey, true);
      back?.focus?.();
    };
  }, []);
  return inPlayer(
    <div className={s.backdrop} onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div
        ref={panel}
        className={s.panel}
        data-wide={wide ? 'true' : undefined}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <header className={s.head}>
          <div className={s.titles}>
            <h2 className={s.title}>{title}</h2>
            {subtitle ? <div className={s.subtitle}>{subtitle}</div> : null}
          </div>
          <button
            type="button"
            className={s.close}
            aria-label="Close"
            onClick={onClose}
            data-close
            data-nav
          >
            <XIcon />
          </button>
        </header>
        {children ? <div className={s.body}>{children}</div> : null}
        {footer ? <footer className={s.foot}>{footer}</footer> : null}
      </div>
    </div>,
  );
}

export function ConfirmSheet({
  title,
  children,
  confirm,
  danger,
  busy,
  onConfirm,
  onClose,
}: {
  title: string;
  children?: ReactNode;
  confirm: string;
  danger?: boolean;
  busy?: boolean;
  onConfirm: () => void;
  onClose: () => void;
}) {
  return (
    <Sheet
      title={title}
      onClose={onClose}
      footer={
        <>
          <button type="button" className={s.btn} onClick={onClose} data-nav>
            Cancel
          </button>
          <button
            type="button"
            className={danger ? s.btnDanger : s.btnPrimary}
            onClick={onConfirm}
            disabled={busy}
            data-nav
          >
            {busy ? 'Working…' : confirm}
          </button>
        </>
      }
    >
      {children}
    </Sheet>
  );
}
