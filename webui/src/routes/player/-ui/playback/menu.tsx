/**
 * a small popover menu for the playback controls (speed, sleep, chapters):
 * a button that opens a list above it; a click outside or escape closes it.
 */

import { useEffect, useRef, useState, type ReactNode } from 'react';

import s from './playback.module.css';

export interface MenuOption {
  key: string;
  label: string;
  /** short, on the right: a time, a count */
  hint?: string;
  /** a line under the label, when the choice needs a word of explanation */
  detail?: string;
  selected?: boolean;
  /** stays open after it's picked (a step you press several times) */
  keepOpen?: boolean;
  /** a heading over this option and the ones after it, until the next group */
  group?: string;
  onSelect: () => void;
}

export function PopMenu({
  label,
  icon,
  badge,
  options,
  title,
}: {
  label: string;
  icon: ReactNode;
  /** a short value on the button ("1.5x", "12m") */
  badge?: string;
  options: MenuOption[];
  title?: string;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    const key = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopImmediatePropagation();
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', close);
    document.addEventListener('keydown', key, true);
    return () => {
      document.removeEventListener('mousedown', close);
      document.removeEventListener('keydown', key, true);
    };
  }, [open]);

  return (
    <div className={s.menuWrap} ref={ref}>
      <button
        type="button"
        className={s.ctl}
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        title={label}
        data-active={badge ? 'true' : undefined}
        onClick={() => setOpen((v) => !v)}
        data-nav
      >
        {icon}
        {badge ? <span className={s.ctlBadge}>{badge}</span> : null}
      </button>
      {open ? (
        <div className={s.menu} role="menu" aria-label={label}>
          {title ? <div className={s.menuTitle}>{title}</div> : null}
          {options.map((o, i) => [
            o.group && o.group !== options[i - 1]?.group ? (
              <div key={`g-${o.group}`} className={s.menuGroup} role="presentation">
                {o.group}
              </div>
            ) : null,
            <button
              key={o.key}
              type="button"
              role="menuitemradio"
              aria-checked={!!o.selected}
              className={s.menuItem}
              onClick={() => {
                o.onSelect();
                if (!o.keepOpen) setOpen(false);
              }}
              data-nav
            >
              <span className={s.menuText}>
                <span className={s.menuLabel}>{o.label}</span>
                {o.detail ? <span className={s.menuDetail}>{o.detail}</span> : null}
              </span>
              {o.hint ? <span className={s.menuHint}>{o.hint}</span> : null}
              {o.selected ? (
                <span className={s.menuCheck} aria-hidden="true">
                  ✓
                </span>
              ) : null}
            </button>,
          ])}
        </div>
      ) : null}
    </div>
  );
}
