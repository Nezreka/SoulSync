/**
 * The 🔗 "edit original playlist link" prompt (auto-sync.js 2410-2441).
 *
 * DECLARED DIVERGENCE: the vanilla uses window.prompt, which this repo forbids
 * (the same rule that turned the rename into an inline input). The question
 * text, the pre-filled current value and the empty-value rejection are the
 * vanilla's; Cancel and Escape are its `nextRef === null` return.
 *
 * The chrome is the export picker's — that overlay is the only modal shape the
 * mirrored tab has, and a prompt has no styling of its own to transcribe. It
 * sits on DialogFrame, which brings the focus trap, Escape and outside press.
 */

import { useState } from 'react';

import { DialogFrame } from '@/components/dialog';

import type { MirroredPlaylistRow } from '../-sync.mirrored';

import { SOURCE_REF_REQUIRED, sourceRefLabel, sourceRefPrompt } from '../-sync.pipeline';
import styles from './source-ref-modal.module.css';

export interface SourceRefModalProps {
  row: MirroredPlaylistRow;
  /** getMirroredSourceRef's answer for this row — the prompt's default (606). */
  currentRef: string;
  onClose: () => void;
  /** A non-empty, trimmed value (2416-2421). */
  onSubmit: (sourceRef: string) => void;
}

export function SourceRefModal({ row, currentRef, onClose, onSubmit }: SourceRefModalProps) {
  const [value, setValue] = useState(currentRef);

  const submit = () => {
    const trimmed = value.trim();
    if (!trimmed) {
      window.showToast?.(SOURCE_REF_REQUIRED, 'error');
      return;
    }
    onSubmit(trimmed);
  };

  return (
    <DialogFrame
      open
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      viewportClassName={styles.overlay}
      className={styles.popup}
      id="mirrored-source-ref-modal"
      aria-label={sourceRefPrompt(row.source, row.name ?? '')}
    >
      <div className={styles.title}>{sourceRefPrompt(row.source, row.name ?? '')}</div>
      <div className={styles.hint}>The {sourceRefLabel(row.source)} this mirror re-syncs from.</div>
      <input
        className={`mirrored-source-ref-input ${styles.input}`}
        autoFocus
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') submit();
        }}
      />
      <div className={styles.actions}>
        <button type="button" className={styles.cancel} onClick={onClose}>
          Cancel
        </button>
        <button type="button" className={styles.save} onClick={submit}>
          Save
        </button>
      </div>
    </DialogFrame>
  );
}
