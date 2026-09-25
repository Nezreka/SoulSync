import type { ReactNode } from 'react';

import { DialogFrame, DialogHeader } from '@/components/dialog/dialog';

import styles from './library-v2-page.module.css';

/** Shared, viewport-bound workspace for library previews. Chrome stays outside
 * the scrolling results, and the portal sits above the shell's floating tools. */
export function LibraryToolDialog({
  title,
  description,
  footer,
  children,
  onClose,
  fitContent = false,
}: {
  title: string;
  description?: ReactNode;
  footer?: ReactNode;
  children: ReactNode;
  onClose: () => void;
  fitContent?: boolean;
}) {
  return (
    <DialogFrame
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      className={`${styles.modal} ${styles.modalWorkspace} ${styles.modalFramed} ${styles.toolWorkspace} ${fitContent ? styles.toolFitContent : ''}`}
    >
      <DialogHeader title={title} closeLabel="Close" compact>
        <span className={styles.toolDescription}>{description}</span>
      </DialogHeader>
      <div className={styles.toolDialogBody}>{children}</div>
      {footer ? <div className={styles.toolDialogFooter}>{footer}</div> : null}
    </DialogFrame>
  );
}
