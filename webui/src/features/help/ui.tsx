/**
 * The sheet's building blocks, shared so every view (P3 to P6) looks like
 * the mockup without copying its css: section labels, list rows, tiles,
 * buttons, key caps, and the placeholder a view shows until it is built.
 */

import type { ReactNode } from 'react';

import s from './help.module.css';
import { Icon, type IconName } from './icons';

export function Label({ children }: { children: ReactNode }) {
  return <h3 className={s.label}>{children}</h3>;
}

export function List({ children, label }: { children: ReactNode; label?: string }) {
  return (
    <div className={s.list} role={label ? 'group' : undefined} aria-label={label}>
      {children}
    </div>
  );
}

export interface RowProps {
  icon?: IconName;
  title: ReactNode;
  sub?: ReactNode;
  /** the string 'chev' for a view or page, 'tab' for a link that opens a new tab, or any node */
  end?: ReactNode;
  onClick?: () => void;
}

export function Row({ icon, title, sub, end, onClick }: RowProps) {
  const inner = (
    <>
      {icon ? (
        <span className={s.rowIcon} aria-hidden="true">
          <Icon name={icon} size={15} />
        </span>
      ) : null}
      <span className={s.rowText}>
        <span className={s.rowTitle}>{title}</span>
        {sub ? <span className={s.rowSub}>{sub}</span> : null}
      </span>
      {end === 'chev' ? (
        <span className={s.rowEnd} aria-hidden="true">
          <Icon name="chev" size={16} />
        </span>
      ) : end === 'tab' ? (
        <span className={s.rowEnd}>
          opens a tab
          <Icon name="external" size={12} />
        </span>
      ) : end ? (
        <span className={s.rowEnd}>{end}</span>
      ) : null}
    </>
  );
  if (!onClick) {
    return (
      <div className={s.row} data-static>
        {inner}
      </div>
    );
  }
  return (
    <button type="button" className={s.row} onClick={onClick}>
      {inner}
    </button>
  );
}

export function Tile({
  icon,
  label,
  chip,
  chipLabel,
  onClick,
}: {
  icon: IconName;
  label: string;
  chip?: string | null;
  /** read out instead of the bare chip text, e.g. "3.6.7 is new" */
  chipLabel?: string;
  onClick: () => void;
}) {
  return (
    <button type="button" className={s.tile} onClick={onClick}>
      <Icon name={icon} size={18} />
      <span className={s.tileLabel}>
        <span>{label}</span>
        {chip ? (
          <span className={s.chip} aria-label={chipLabel}>
            {chip}
          </span>
        ) : null}
      </span>
    </button>
  );
}

export function Button({
  kind = 'secondary',
  icon,
  children,
  onClick,
}: {
  kind?: 'primary' | 'secondary' | 'ghost';
  icon?: IconName;
  children: ReactNode;
  onClick?: () => void;
}) {
  return (
    <button type="button" className={s.btn} data-kind={kind} onClick={onClick}>
      {icon ? <Icon name={icon} size={14} /> : null}
      {children}
    </button>
  );
}

export function Actions({ children }: { children: ReactNode }) {
  return <div className={s.acts}>{children}</div>;
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className={s.kbd}>{children}</kbd>;
}

/** what a view shows until its phase lands: honest, and still useful */
export function Placeholder({
  title,
  text,
  children,
}: {
  title: string;
  text: string;
  children?: ReactNode;
}) {
  return (
    <div className={s.soon}>
      <p className={s.soonTitle}>{title}</p>
      <p className={s.soonText}>{text}</p>
      {children ? <Actions>{children}</Actions> : null}
    </div>
  );
}
