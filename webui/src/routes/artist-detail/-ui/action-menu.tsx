import { type ReactNode, useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';

import { BodyPortal } from './portal';

export interface ActionMenuItem {
  key: string;
  label: ReactNode;
  icon?: ReactNode;
  /** hook class for the item button; the old per-action classes live on here so tests and the reorganize painter still find them. */
  className?: string;
  /** red row, separated from the rest. */
  danger?: boolean;
  disabled?: boolean;
  title?: string;
  /** extra attributes, e.g. data-album-id for the reorganize painter. */
  data?: Record<string, string>;
  onSelect: () => void;
}

interface Props {
  /** the trigger; rendered as given, the menu just wires open/close onto it. */
  trigger: (props: {
    ref: (el: HTMLElement | null) => void;
    onClick: (e: React.MouseEvent) => void;
    'aria-haspopup': 'menu';
    'aria-expanded': boolean;
  }) => ReactNode;
  items: ActionMenuItem[];
  /** optional header line, e.g. the source's name and last-attempt time. */
  heading?: ReactNode;
  align?: 'left' | 'right';
  /** test/style hook on the popup. */
  className?: string;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
}

const MENU_GAP = 6;

/**
 * a small dropdown menu that lives at body level.
 *
 * the album panels clip their overflow while they animate open, so a menu
 * positioned inside a row gets cut off at the bottom of the panel. this one
 * measures the trigger and drops itself into the body with fixed
 * coordinates, flipping upward when the viewport runs out.
 *
 * every item click stops propagation: the whole album row is a toggle and a
 * click that bubbles out of the menu would fold the panel shut.
 */
export function ActionMenu({
  trigger,
  items,
  heading,
  align = 'right',
  className,
  open: controlledOpen,
  onOpenChange,
}: Props) {
  const [uncontrolledOpen, setUncontrolledOpen] = useState(false);
  const open = controlledOpen ?? uncontrolledOpen;
  const setOpen = useCallback(
    (next: boolean) => {
      setUncontrolledOpen(next);
      onOpenChange?.(next);
    },
    [onOpenChange],
  );

  const triggerRef = useRef<HTMLElement | null>(null);
  const popupRef = useRef<HTMLDivElement | null>(null);
  const [pos, setPos] = useState<{ top: number; left: number; up: boolean } | null>(null);

  useLayoutEffect(() => {
    if (!open || !triggerRef.current) return;
    const rect = triggerRef.current.getBoundingClientRect();
    const popup = popupRef.current;
    const height = popup?.offsetHeight ?? 0;
    const width = popup?.offsetWidth ?? 0;
    const viewportH = window.innerHeight || 800;
    const viewportW = window.innerWidth || 1200;
    const up = rect.bottom + MENU_GAP + height > viewportH && rect.top - MENU_GAP - height > 0;
    const top = up ? rect.top - MENU_GAP - height : rect.bottom + MENU_GAP;
    let left = align === 'right' ? rect.right - width : rect.left;
    left = Math.max(8, Math.min(left, viewportW - width - 8));
    setPos({ top, left, up });
  }, [open, align, items.length]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        setOpen(false);
        triggerRef.current?.focus();
      }
    };
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (popupRef.current?.contains(t) || triggerRef.current?.contains(t)) return;
      setOpen(false);
    };
    document.addEventListener('keydown', onKey, true);
    document.addEventListener('mousedown', onDown, true);
    window.addEventListener('resize', close);
    window.addEventListener('scroll', close, true);
    function close() {
      setOpen(false);
    }
    return () => {
      document.removeEventListener('keydown', onKey, true);
      document.removeEventListener('mousedown', onDown, true);
      window.removeEventListener('resize', close);
      window.removeEventListener('scroll', close, true);
    };
  }, [open, setOpen]);

  useEffect(() => {
    if (!open) return;
    // first item takes focus so arrow keys work straight away
    const first = popupRef.current?.querySelector<HTMLElement>('[role="menuitem"]:not([disabled])');
    first?.focus();
  }, [open, pos]);

  const moveFocus = (delta: number) => {
    const nodes = [
      ...(popupRef.current?.querySelectorAll<HTMLElement>('[role="menuitem"]:not([disabled])') ??
        []),
    ];
    if (!nodes.length) return;
    const index = nodes.indexOf(document.activeElement as HTMLElement);
    const next = (index + delta + nodes.length) % nodes.length;
    nodes[next].focus();
  };

  return (
    <>
      {trigger({
        ref: (el) => {
          triggerRef.current = el;
        },
        onClick: (e) => {
          e.stopPropagation();
          setOpen(!open);
        },
        'aria-haspopup': 'menu',
        'aria-expanded': open,
      })}
      {open ? (
        <BodyPortal>
          <div
            ref={popupRef}
            role="menu"
            className={`lib-menu${pos?.up ? ' up' : ''}${className ? ` ${className}` : ''}`}
            style={{
              position: 'fixed',
              top: pos?.top ?? -9999,
              left: pos?.left ?? -9999,
              visibility: pos ? 'visible' : 'hidden',
            }}
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => {
              if (e.key === 'ArrowDown') {
                e.preventDefault();
                moveFocus(1);
              } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                moveFocus(-1);
              }
            }}
          >
            {heading ? <div className="lib-menu-heading">{heading}</div> : null}
            {items.map((item, i) => {
              const prevDanger = i > 0 && items[i - 1].danger;
              return (
                <div key={item.key}>
                  {item.danger && !prevDanger ? <div className="lib-menu-sep" /> : null}
                  <button
                    type="button"
                    role="menuitem"
                    className={`lib-menu-item${item.danger ? ' danger' : ''}${
                      item.className ? ` ${item.className}` : ''
                    }`}
                    disabled={item.disabled}
                    title={item.title}
                    {...Object.fromEntries(
                      Object.entries(item.data ?? {}).map(([k, v]) => [`data-${k}`, v]),
                    )}
                    onClick={(e) => {
                      e.stopPropagation();
                      setOpen(false);
                      item.onSelect();
                    }}
                  >
                    {item.icon ? <span className="lib-menu-icon">{item.icon}</span> : null}
                    <span className="lib-menu-label">{item.label}</span>
                  </button>
                </div>
              );
            })}
          </div>
        </BodyPortal>
      ) : null}
    </>
  );
}
