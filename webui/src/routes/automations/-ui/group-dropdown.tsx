import { useEffect, useRef, useState } from 'react';

interface Props {
  /** Every group name that currently exists, for the "move to" list. */
  groups: string[];
  currentGroup: string | null;
  /** Screen position of the button that opened this, as a fixed-position anchor. */
  anchor: { top: number; bottom: number; right: number };
  onAssign: (groupName: string | null) => void;
  onClose: () => void;
}

/**
 * "Assign group" popup.
 *
 * The vanilla version appended this to document.body to escape an
 * overflow:hidden ancestor, positioned it with getBoundingClientRect, and
 * flipped it upward when there was no room below. Same behaviour here, but
 * React owns the node and its lifetime — no _activeGroupDropdown global to
 * leave dangling if a re-render removes the card underneath it.
 */
export function GroupDropdown({ groups, currentGroup, anchor, onAssign, onClose }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const [flipUp, setFlipUp] = useState(false);
  const [draft, setDraft] = useState('');

  // Measure after paint, matching the vanilla flip test: open upward only if it
  // does not fit below AND does fit above.
  useEffect(() => {
    const height = ref.current?.offsetHeight ?? 0;
    setFlipUp(anchor.bottom + 4 + height > window.innerHeight && anchor.top - 4 - height > 0);
  }, [anchor]);

  // Any click outside closes it, as the vanilla document listener did.
  useEffect(() => {
    const onDocClick = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) onClose();
    };
    // Deferred so the click that OPENED the dropdown does not immediately
    // close it on the same tick.
    const id = setTimeout(() => document.addEventListener('click', onDocClick), 0);
    return () => {
      clearTimeout(id);
      document.removeEventListener('click', onDocClick);
    };
  }, [onClose]);

  return (
    <div
      ref={ref}
      className="auto-group-dropdown"
      style={{
        position: 'fixed',
        right: window.innerWidth - anchor.right,
        left: 'auto',
        ...(flipUp ? { bottom: window.innerHeight - anchor.top + 4 } : { top: anchor.bottom + 4 }),
      }}
    >
      {currentGroup ? (
        <>
          <div className="auto-group-option ungroup" onClick={() => onAssign(null)}>
            Remove from group
          </div>
          <div className="auto-group-divider" />
        </>
      ) : null}

      {groups.map((g) => (
        <div
          key={g}
          className={`auto-group-option${g === currentGroup ? ' active' : ''}`}
          onClick={() => onAssign(g)}
        >
          {g}
        </div>
      ))}
      {groups.length > 0 ? <div className="auto-group-divider" /> : null}

      <input
        className="auto-group-input"
        placeholder="New group name..."
        aria-label="New group name"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key !== 'Enter') return;
          e.preventDefault();
          // Trim, and ignore an empty submit rather than creating a nameless
          // group — the vanilla handler passed the trimmed value straight on,
          // where '' became null and silently UNgrouped instead.
          const name = draft.trim();
          if (name) onAssign(name);
        }}
      />
    </div>
  );
}
