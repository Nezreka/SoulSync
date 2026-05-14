import type { ReactNode } from 'react';

import { Dialog } from '@base-ui/react/dialog';
import clsx from 'clsx';

import styles from './dialog.module.css';

export function DialogFrame({
  children,
  className,
  onOpenChange,
  open,
}: {
  children: ReactNode;
  className?: string;
  onOpenChange: (open: boolean) => void;
  open: boolean;
}) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Backdrop className={styles.backdrop} />
        <Dialog.Viewport className={styles.viewport}>
          <Dialog.Popup className={clsx(styles.popup, className)}>{children}</Dialog.Popup>
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
      <Dialog.Close className={styles.close} aria-label={closeLabel}>
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
