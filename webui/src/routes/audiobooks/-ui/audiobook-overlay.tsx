import type { ReactNode } from 'react';

import { createPortal } from 'react-dom';

import { useAccessibleModal } from '@/components/dialog';

import styles from './audiobooks-page.module.css';

interface AudiobookOverlayProps {
  onClose: () => void;
  label: string;
  children: ReactNode;
}

/**
 * Portals an audiobook overlay to <body> and gives it modal behaviour.
 *
 * The portal is not a nicety. `position: fixed` escapes overflow, but NOT a
 * transformed ancestor: a transform (or filter, or backdrop-filter) makes an
 * element the containing block for its fixed descendants, and the overlay then
 * clips to that ancestor instead of the viewport. Book cards animate in with
 * `animation-fill-mode: both`, which leaves a transform applied forever, and
 * they sit inside a horizontally-scrolling rail — so a dialog opened from a
 * card's heart button rendered inside the rail and was invisible.
 *
 * Rendering at <body> makes where it was opened from irrelevant.
 *
 * Escape, focus trap, initial focus, focus restore and the body scroll lock all
 * come from useAccessibleModal, which exists for exactly this: bolting modal
 * behaviour onto markup that already has its own styling.
 */
export function AudiobookOverlay({ onClose, label, children }: AudiobookOverlayProps) {
  // onBackdropClick closes only when the click landed on the backdrop itself,
  // so a click inside the dialog does not need its own stopPropagation.
  const { ref, onBackdropClick } = useAccessibleModal<HTMLDivElement>(onClose);

  if (typeof document === 'undefined') return null;

  return createPortal(
    <div className={styles.modalBackdrop} onClick={onBackdropClick} role="presentation">
      <div ref={ref} role="dialog" aria-modal="true" aria-label={label}>
        {children}
      </div>
    </div>,
    document.body,
  );
}
