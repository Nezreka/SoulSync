import { useEffect, useRef, useState } from 'react';

import {
  inlineEditDisplay,
  inlineEditInput,
  inlineEditUrl,
  inlineEditValue,
} from '../-artist-detail.enhanced-album';

interface Props {
  className: string;
  /** Falsy for a non-admin or a missing row — the cell is then plain text. */
  editable: boolean;
  entityType: 'track' | 'album';
  entityId: unknown;
  field: string;
  /** The raw value the input starts from. */
  value: string | number | null | undefined;
  /** Applied locally after a successful PUT, as updateLocalEnhancedData did. */
  onSaved: (field: string, value: string | number | null) => void;
  children: React.ReactNode;
}

/**
 * A table cell that becomes an input on click (startInlineEdit / saveInlineEdit,
 * library.js:5963).
 *
 * The vanilla replaced the cell's innerHTML and stashed the original so a
 * failed save could put it back. React owns this DOM, so the edit is state and
 * the "restore" is simply not committing — but the observable behaviour is the
 * same: Enter and blur save, Escape reverts, and a rejected save leaves the
 * cell showing what it showed before.
 */
export function EditableCell({
  className,
  editable,
  entityType,
  entityId,
  field,
  value,
  onSaved,
  children,
}: Props) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);
  /**
   * Guards the blur handler: a save triggered by Enter — or an Escape — must
   * not fire again when the input loses focus on unmount.
   *
   * Not test-observable. React listens for blur at the root, and by the time
   * the input is unmounted the event no longer reaches the handler under jsdom,
   * so both guards survive mutation. They are kept because a real browser does
   * fire blur on a focused element that is removed.
   */
  const savingRef = useRef(false);

  useEffect(() => {
    if (editing) {
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }, [editing]);

  if (!editable) {
    return <td className={className}>{children}</td>;
  }

  const start = () => {
    if (editing) return;
    savingRef.current = false;
    setDraft(value == null ? '' : String(value));
    setEditing(true);
  };

  const save = async () => {
    if (savingRef.current) return;
    savingRef.current = true;
    setEditing(false);

    const parsed = inlineEditValue(field, draft);
    try {
      const response = await fetch(inlineEditUrl(entityType, entityId), {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ [field]: parsed }),
      });
      const result = await response.json();
      if (!result.success) throw new Error(result.error);

      onSaved(field, parsed);
      window.showToast?.(`Updated ${field}`, 'success');
    } catch (error) {
      // Nothing was committed, so the cell already shows its old value.
      window.showToast?.(`Failed to update: ${(error as Error).message}`, 'error');
    }
  };

  const input = inlineEditInput(field);

  return (
    <td
      className={className}
      onClick={(e) => {
        e.stopPropagation();
        start();
      }}
    >
      {editing ? (
        <input
          ref={inputRef}
          type={input.type}
          className={input.className}
          step={input.step}
          min={input.min}
          value={draft}
          onClick={(e) => e.stopPropagation()}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            // Stopped so a keystroke in the cell cannot reach a page-level
            // shortcut handler, exactly as the vanilla did.
            e.stopPropagation();
            if (e.key === 'Enter') {
              e.preventDefault();
              void save();
            } else if (e.key === 'Escape') {
              savingRef.current = true;
              setEditing(false);
            }
          }}
          onBlur={() => void save()}
        />
      ) : (
        children
      )}
    </td>
  );
}

export { inlineEditDisplay };
