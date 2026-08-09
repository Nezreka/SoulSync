/**
 * The 🔗 "edit original playlist link" prompt (auto-sync.js 2410-2441).
 *
 * DECLARED DIVERGENCE: the vanilla uses window.prompt, which this repo forbids
 * (the same rule that turned the rename into an inline input). The question
 * text, the pre-filled current value and the empty-value rejection are the
 * vanilla's; Cancel and Escape are its `nextRef === null` return.
 *
 * The chrome is the export picker's — that overlay is the only modal shape the
 * mirrored tab has, and a prompt has no styling of its own to transcribe.
 */

import { useState } from 'react';

import type { MirroredPlaylistRow } from '../-sync.mirrored';

import { SOURCE_REF_REQUIRED, sourceRefLabel, sourceRefPrompt } from '../-sync.pipeline';

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
    <div
      id="mirrored-source-ref-modal"
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 10000,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'rgba(0,0,0,0.6)',
        backdropFilter: 'blur(4px)',
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        style={{
          width: 420,
          maxWidth: '92vw',
          background: 'linear-gradient(135deg,rgba(26,26,30,0.98),rgba(18,18,22,0.99))',
          border: '1px solid rgba(var(--accent-rgb),0.25)',
          borderRadius: 18,
          padding: 22,
          boxShadow: '0 18px 50px rgba(0,0,0,0.55)',
        }}
      >
        <div style={{ fontSize: 16, fontWeight: 700, color: '#fff', marginBottom: 4 }}>
          {sourceRefPrompt(row.source, row.name ?? '')}
        </div>
        <div style={{ fontSize: 13, color: 'rgba(255,255,255,0.55)', marginBottom: 14 }}>
          The {sourceRefLabel(row.source)} this mirror re-syncs from.
        </div>
        <input
          className="mirrored-source-ref-input"
          autoFocus
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') submit();
            if (e.key === 'Escape') onClose();
          }}
          style={{
            width: '100%',
            padding: '10px 12px',
            borderRadius: 10,
            border: '1px solid rgba(255,255,255,0.12)',
            background: 'rgba(255,255,255,0.05)',
            color: '#fff',
            fontSize: 13,
          }}
        />
        <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', marginTop: 16 }}>
          <button
            type="button"
            onClick={onClose}
            style={{
              background: 'none',
              border: 'none',
              color: 'rgba(255,255,255,0.5)',
              cursor: 'pointer',
              fontSize: 13,
            }}
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={submit}
            style={{
              padding: '8px 16px',
              borderRadius: 10,
              border: '1px solid rgba(var(--accent-rgb),0.35)',
              background: 'rgba(var(--accent-rgb),0.12)',
              color: '#fff',
              cursor: 'pointer',
              fontSize: 13,
            }}
          >
            Save
          </button>
        </div>
      </div>
    </div>
  );
}
