import type { ComponentProps, ReactNode } from 'react';

import { Dialog } from '@base-ui/react/dialog';
import clsx from 'clsx';

import styles from './dialog.module.css';

/**
 * A modal dialog: base-ui's focus trap, Escape, outside press and scroll lock.
 *
 * By default it brings its own backdrop and frame, and `className` is added to
 * the popup. An overlay that keeps its own look passes `viewportClassName`
 * instead: that class goes on the viewport (backdrop colour, centring,
 * z-index), `className` alone styles the popup, and none of the default frame
 * styles apply.
 */
export function DialogFrame({
  'aria-label': ariaLabel,
  children,
  className,
  initialFocus,
  onOpenChange,
  open,
  viewportClassName,
}: {
  'aria-label'?: string;
  children: ReactNode;
  className?: string;
  initialFocus?: ComponentProps<typeof Dialog.Popup>['initialFocus'];
  onOpenChange: (open: boolean) => void;
  open: boolean;
  viewportClassName?: string;
}) {
  const custom = viewportClassName !== undefined;
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        {custom ? null : <Dialog.Backdrop className={styles.backdrop} />}
        <Dialog.Viewport className={custom ? viewportClassName : styles.viewport}>
          <Dialog.Popup
            aria-label={ariaLabel}
            initialFocus={initialFocus}
            className={custom ? className : clsx(styles.popup, className)}
          >
            {children}
          </Dialog.Popup>
        </Dialog.Viewport>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export function DialogHeader({
  children,
  closeLabel = 'Close dialog',
  title,
}: {
  children?: ReactNode;
  closeLabel?: string;
  title: ReactNode;
}) {
  return (
    <div className={styles.header}>
      <div className={styles.headerContent}>
        <Dialog.Title className={styles.title}>{title}</Dialog.Title>
        {children ? <div className={styles.headerMeta}>{children}</div> : null}
      </div>
      <Dialog.Close className={styles.close} aria-label={closeLabel} title={closeLabel}>
        ×
      </Dialog.Close>
    </div>
  );
}

export function DialogBody({ children }: { children: ReactNode }) {
  return <div className={styles.body}>{children}</div>;
}

export function DialogFooter({ children }: { children: ReactNode }) {
  return <div className={styles.footer}>{children}</div>;
}
