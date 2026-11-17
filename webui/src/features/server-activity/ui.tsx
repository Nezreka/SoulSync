/** small shared pieces: avatar, poster, badges, facts, states, menus */

import type { KeyboardEvent, ReactNode, Ref } from 'react';

import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';

import type { MediaKind, PlayMethod } from '@/shell/server-activity-types';

import { avatarUrl, imageUrl } from './api';
import { METHOD_LABEL, type QualityBadge, avatarTone, initials, isAudioKind } from './format';
import { ChevronDown, KindIcon, RetryIcon } from './icons';
import s from './shared.module.css';

export function cx(...names: (string | false | null | undefined)[]): string {
  return names.filter(Boolean).join(' ');
}

/** an opened part stays drawn while it folds shut, then goes */
export function useDrawn(open: boolean, ms = 380): boolean {
  const [drawn, setDrawn] = useState(open);
  useEffect(() => {
    if (open) {
      setDrawn(true);
      return;
    }
    const timer = setTimeout(() => setDrawn(false), ms);
    return () => clearTimeout(timer);
  }, [open, ms]);
  return open || drawn;
}

// ── people and artwork ──

export function Avatar({
  name,
  url,
  size = 20,
}: {
  name: string;
  url?: string | null;
  size?: number;
}) {
  const [failed, setFailed] = useState(false);
  const src = !failed ? avatarUrl(url) : '';
  return (
    <span
      className={s.avatar}
      style={{
        width: size,
        height: size,
        fontSize: Math.max(8, Math.round(size * 0.42)),
        background: avatarTone(name),
      }}
      aria-hidden="true"
    >
      {initials(name)}
      {src ? (
        <img className={s.avatarImg} src={src} alt="" onError={() => setFailed(true)} />
      ) : null}
    </span>
  );
}

export function Poster({
  thumb,
  kind,
  size = 'card',
}: {
  thumb: string | null | undefined;
  kind: MediaKind;
  size?: 'card' | 'row' | 'shelf';
}) {
  const [state, setState] = useState<'loading' | 'ok' | 'failed'>('loading');
  const src = imageUrl(thumb);
  const square = isAudioKind(kind);
  useEffect(() => setState('loading'), [src]);
  return (
    <span className={s.poster} data-size={size} data-square={square || undefined}>
      <span className={s.posterGlyph}>
        <KindIcon kind={kind} size={size === 'card' ? 20 : 14} />
      </span>
      {src && state !== 'failed' ? (
        <img
          className={s.posterImg}
          data-loaded={state === 'ok' || undefined}
          src={src}
          alt=""
          loading="lazy"
          decoding="async"
          onLoad={() => setState('ok')}
          onError={() => setState('failed')}
        />
      ) : null}
    </span>
  );
}

// ── badges ──

export function MethodBadge({ method, label }: { method: PlayMethod | null; label?: string }) {
  if (!method) return null;
  return (
    <span className={s.badge} data-method={method}>
      <i className={s.dot} aria-hidden="true" />
      {label || METHOD_LABEL[method]}
    </span>
  );
}

export function Badge({ badge }: { badge: QualityBadge }) {
  return (
    <span className={s.badge} data-hires={badge.hires || undefined}>
      {badge.from != null ? (
        <>
          {badge.from}
          <span className={s.arrow} aria-label="to">
            →
          </span>
          {badge.to}
        </>
      ) : (
        badge.text
      )}
    </span>
  );
}

export function TextBadge({ children }: { children: ReactNode }) {
  return <span className={s.badge}>{children}</span>;
}

// ── facts ──

export function Fact({
  label,
  value,
  sub,
  meter,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  meter?: number | null;
}) {
  return (
    <div className={s.fact}>
      <div className={s.factLabel}>{label}</div>
      <div className={s.factValue}>{value}</div>
      {sub ? <div className={s.factSub}>{sub}</div> : null}
      {meter != null ? (
        <div className={s.meter} aria-hidden="true">
          <i style={{ transform: `scaleX(${Math.max(0, Math.min(100, meter)) / 100})` }} />
        </div>
      ) : null}
    </div>
  );
}

// ── states ──

export function EmptyState({
  icon,
  title,
  children,
  action,
}: {
  icon: ReactNode;
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className={s.empty} role="status">
      <div className={s.emptyIcon}>{icon}</div>
      <div className={s.emptyTitle}>{title}</div>
      {children ? <div className={s.emptyText}>{children}</div> : null}
      {action ? <div className={s.emptyAction}>{action}</div> : null}
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className={s.empty} role="alert">
      <div className={s.emptyIcon} data-tone="warn">
        <RetryIcon size={22} />
      </div>
      <div className={s.emptyTitle}>That didn't load</div>
      <div className={s.emptyText}>{message}</div>
      <div className={s.emptyAction}>
        <button type="button" className={s.button} onClick={onRetry}>
          Try again
        </button>
      </div>
    </div>
  );
}

export function Skel({
  w,
  h = 12,
  round,
  className,
}: {
  w?: number | string;
  h?: number | string;
  round?: boolean;
  className?: string;
}) {
  return (
    <span
      className={cx(s.skel, className)}
      data-round={round || undefined}
      style={{ width: w, height: h }}
      aria-hidden="true"
    />
  );
}

export function Button({
  children,
  onClick,
  tone,
  disabled,
  type = 'button',
  busy,
  ref,
}: {
  children: ReactNode;
  onClick?: () => void;
  tone?: 'stop' | 'go' | 'ghost' | 'link' | 'quiet' | 'primary';
  disabled?: boolean;
  type?: 'button' | 'submit';
  busy?: boolean;
  ref?: Ref<HTMLButtonElement>;
}) {
  return (
    <button
      ref={ref}
      type={type}
      className={s.button}
      data-tone={tone}
      data-busy={busy || undefined}
      disabled={disabled}
      onClick={onClick}
    >
      {children}
    </button>
  );
}

// ── segmented control: a radio group, arrows move ──

export interface SegOption<T extends string | number> {
  value: T;
  label: ReactNode;
  count?: number | null;
}

export function Segmented<T extends string | number>({
  options,
  value,
  onChange,
  label,
  role = 'radiogroup',
  idFor,
  controls,
}: {
  options: SegOption<T>[];
  value: T;
  onChange: (value: T) => void;
  label: string;
  /** tablist for the drawer's own tabs */
  role?: 'radiogroup' | 'tablist';
  idFor?: (value: T) => string;
  controls?: string;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const at = Math.max(
    0,
    options.findIndex((o) => o.value === value),
  );
  // the thumb slides under the chosen button; measured, as labels differ in width
  const [thumb, setThumb] = useState<{ x: number; w: number } | null>(null);
  useLayoutEffect(() => {
    const measure = () => {
      const el = refs.current[at];
      if (el && el.offsetWidth) setThumb({ x: el.offsetLeft, w: el.offsetWidth });
    };
    measure();
    const box = refs.current[at]?.parentElement;
    if (!box || typeof ResizeObserver === 'undefined') return;
    const watch = new ResizeObserver(measure);
    watch.observe(box);
    return () => watch.disconnect();
  }, [at, options.length]);
  const onKey = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const step =
      event.key === 'ArrowRight' || event.key === 'ArrowDown'
        ? 1
        : event.key === 'ArrowLeft' || event.key === 'ArrowUp'
          ? -1
          : 0;
    let next = index;
    if (step) next = (index + step + options.length) % options.length;
    else if (event.key === 'Home') next = 0;
    else if (event.key === 'End') next = options.length - 1;
    else return;
    event.preventDefault();
    onChange(options[next].value);
    refs.current[next]?.focus();
  };
  const tabs = role === 'tablist';
  return (
    <div className={s.seg} role={role} aria-label={label}>
      {thumb ? (
        <span
          className={s.segThumb}
          aria-hidden="true"
          style={{ width: thumb.w, transform: `translateX(${thumb.x}px)` }}
        />
      ) : null}
      {options.map((option, index) => {
        const on = option.value === value;
        return (
          <button
            key={String(option.value)}
            ref={(el) => {
              refs.current[index] = el;
            }}
            type="button"
            id={idFor?.(option.value)}
            role={tabs ? 'tab' : 'radio'}
            aria-checked={tabs ? undefined : on}
            aria-selected={tabs ? on : undefined}
            aria-controls={tabs && on ? controls : undefined}
            tabIndex={on ? 0 : -1}
            className={s.segButton}
            data-on={on || undefined}
            data-thumb={thumb ? true : undefined}
            onClick={() => onChange(option.value)}
            onKeyDown={(event) => onKey(event, index)}
          >
            {option.label}
            {option.count != null ? <span className={s.segCount}>{option.count}</span> : null}
          </button>
        );
      })}
    </div>
  );
}

// ── a menu button that picks one value (person, playback, range) ──

export interface MenuOption {
  value: string;
  label: string;
  hint?: string;
  avatar?: { name: string; url: string | null };
}

export function MenuSelect({
  label,
  value,
  options,
  onChange,
  align = 'start',
}: {
  label: string;
  value: string;
  options: MenuOption[];
  onChange: (value: string) => void;
  align?: 'start' | 'end';
}) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const root = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const id = useId();
  const current = options.find((o) => o.value === value) ?? options[0];

  useEffect(() => {
    if (!open) return;
    const away = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('pointerdown', away);
    return () => document.removeEventListener('pointerdown', away);
  }, [open]);

  useEffect(() => {
    if (open) list.current?.focus({ preventScroll: true });
  }, [open]);

  const show = (at: number) => {
    setActive(at);
    setOpen(true);
  };
  const pick = (at: number) => {
    onChange(options[at].value);
    setOpen(false);
    button.current?.focus();
  };

  const onButtonKey = (event: KeyboardEvent) => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      show(Math.max(0, options.indexOf(current)));
    }
  };
  const onListKey = (event: KeyboardEvent) => {
    if (event.key === 'Escape' || event.key === 'Tab') {
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        button.current?.focus();
      }
      setOpen(false);
      return;
    }
    if (event.key === 'ArrowDown') setActive((a) => (a + 1) % options.length);
    else if (event.key === 'ArrowUp') setActive((a) => (a - 1 + options.length) % options.length);
    else if (event.key === 'Home') setActive(0);
    else if (event.key === 'End') setActive(options.length - 1);
    else if (event.key === 'Enter' || event.key === ' ') pick(active);
    else return;
    event.preventDefault();
  };

  return (
    <div className={s.menu} ref={root}>
      <button
        ref={button}
        type="button"
        className={s.pill}
        data-set={value !== options[0]?.value || undefined}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        aria-label={`${label}: ${current?.label ?? ''}`}
        onClick={() => (open ? setOpen(false) : show(Math.max(0, options.indexOf(current))))}
        onKeyDown={onButtonKey}
      >
        {current?.avatar ? <Avatar name={current.avatar.name} url={current.avatar.url} /> : null}
        <span className={s.pillText}>{current?.label}</span>
        <ChevronDown size={12} className={s.pillChevron} />
      </button>
      {open ? (
        <div
          ref={list}
          id={id}
          role="listbox"
          tabIndex={-1}
          aria-label={label}
          aria-activedescendant={`${id}-${active}`}
          className={s.menuList}
          data-align={align}
          onKeyDown={onListKey}
        >
          {options.map((option, index) => (
            <div
              key={option.value}
              id={`${id}-${index}`}
              role="option"
              aria-selected={option.value === value}
              className={s.menuItem}
              data-active={index === active || undefined}
              onPointerEnter={() => setActive(index)}
              onClick={() => pick(index)}
            >
              {option.avatar ? (
                <Avatar name={option.avatar.name} url={option.avatar.url} size={22} />
              ) : null}
              <span className={s.menuLabel}>{option.label}</span>
              {option.hint ? <span className={s.menuHint}>{option.hint}</span> : null}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
