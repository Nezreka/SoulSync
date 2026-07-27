import { useEffect } from 'react';

export type DeleteGroupChoice = 'ungroup' | 'delete_all';

interface Props {
  groupName: string;
  count: number;
  onChoose: (choice: DeleteGroupChoice) => void;
  onCancel: () => void;
}

/**
 * Deleting a group asks what to do with its contents.
 *
 * Three outcomes, not two, so showConfirmDialog cannot express it: keep the
 * automations and move them to My Automations, delete the group AND every
 * automation in it, or cancel. Collapsing that into a yes/no would either hide
 * the safe option or make the destructive one the default.
 */
export function DeleteGroupDialog({ groupName, count, onChoose, onCancel }: Props) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCancel();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onCancel]);

  const plural = count !== 1 ? 's' : '';

  return (
    <div
      className="modal-overlay"
      style={{ display: 'flex' }}
      // Clicking the backdrop cancels; clicks inside the dialog must not.
      onClick={(e) => {
        if (e.target === e.currentTarget) onCancel();
      }}
    >
      <div className="delete-group-dialog" role="dialog" aria-modal="true">
        <div className="delete-group-icon">🗑️</div>
        <h3 className="delete-group-title">Delete Group &quot;{groupName}&quot;</h3>
        <p className="delete-group-message">
          This group contains {count} automation{plural}. What would you like to do?
        </p>
        <div className="delete-group-actions">
          <button
            type="button"
            className="delete-group-btn delete-group-keep"
            onClick={() => onChoose('ungroup')}
          >
            Keep Automations — move to My Automations
          </button>
          <button
            type="button"
            className="delete-group-btn delete-group-remove"
            onClick={() => onChoose('delete_all')}
          >
            Delete Everything — remove group and all {count} automation{plural}
          </button>
          <button type="button" className="delete-group-btn delete-group-cancel" onClick={onCancel}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
