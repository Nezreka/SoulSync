import type { ComponentProps, ReactNode } from 'react';

import { Dialog } from '@base-ui/react/dialog';
import clsx from 'clsx';

import styles from './dialog.module.css';

export function DialogFrame({
  children,
  className,
  initialFocus,
  onOpenChange,
  open,
}: {
  children: ReactNode;
  className?: string;
  initialFocus?: ComponentProps<typeof Dialog.Popup>['initialFocus'];
  onOpenChange: (open: boolean) => void;
  open: boolean;
}) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Backdrop className={styles.backdrop} />
        <Dialog.Viewport className={styles.viewport}>
          <Dialog.Popup initialFocus={initialFocus} className={clsx(styles.popup, className)}>
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
  compact = false,
}: {
  children?: ReactNode;
  closeLabel?: string;
  title: ReactNode;
  compact?: boolean;
}) {
  return (
    <div className={clsx(styles.header, compact && styles.headerCompact)}>
      <div className={styles.headerContent}>
        <Dialog.Title className={styles.title}>{title}</Dialog.Title>
        {children ? <div className={styles.headerMeta}>{children}</div> : null}
      </div>
      <Dialog.Close className={styles.close} aria-label={closeLabel} title={closeLabel}>
        <svg
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
        >
          <path d="M6 6l12 12M18 6L6 18" />
        </svg>
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
