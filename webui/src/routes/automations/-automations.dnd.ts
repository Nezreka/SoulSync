import { useCallback, useRef, useState } from 'react';

/** The card currently being dragged, and the group it came from. */
export interface AutomationDrag {
  id: number;
  groupName: string | null;
}

/**
 * Drag a card between group sections.
 *
 * Mirrors the vanilla behaviour: only non-system cards are draggable, only
 * non-protected section bodies accept a drop, protected sections are dimmed
 * while a drag is in flight, and a collapsed section expands after hovering
 * over it for a moment.
 *
 * The enter/leave counter is the important detail. dragenter and dragleave
 * both fire for CHILD elements, so a naive handler clears the highlight the
 * moment the pointer crosses a card inside the drop zone. Counting keeps the
 * zone lit until the pointer genuinely leaves it.
 */
export const DRAG_EXPAND_MS = 500;

export function useAutomationDnd(onDrop: (drag: AutomationDrag, toGroup: string | null) => void) {
  const [drag, setDrag] = useState<AutomationDrag | null>(null);
  // Which section body is lit. One at a time, so this is a single value rather
  // than per-section state.
  const [over, setOver] = useState<string | null>(null);
  const enterCount = useRef(0);
  const expandTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearExpandTimer = () => {
    if (expandTimer.current) {
      clearTimeout(expandTimer.current);
      expandTimer.current = null;
    }
  };

  const cardProps = useCallback((id: number, groupName: string | null, isSystem: boolean) => {
    // System automations are not movable — they live in the protected section.
    if (isSystem) return {};
    return {
      draggable: true,
      onDragStart: (e: React.DragEvent) => {
        setDrag({ id, groupName });
        e.dataTransfer.setData('text/plain', String(id));
        e.dataTransfer.effectAllowed = 'move';
      },
      onDragEnd: () => {
        setDrag(null);
        setOver(null);
        enterCount.current = 0;
        clearExpandTimer();
      },
    };
  }, []);

  const zoneProps = useCallback(
    (
      sectionKey: string,
      groupName: string | null,
      opts: { isProtected?: boolean; onExpand?: () => void } = {},
    ) => {
      // Protected sections (System, Hub) are never drop targets.
      if (opts.isProtected) return {};
      return {
        onDragOver: (e: React.DragEvent) => {
          if (!drag) return;
          // Without preventDefault the browser refuses the drop outright.
          e.preventDefault();
          e.dataTransfer.dropEffect = 'move';
        },
        onDragEnter: () => {
          if (!drag) return;
          enterCount.current += 1;
          setOver(sectionKey);
          if (opts.onExpand && !expandTimer.current) {
            expandTimer.current = setTimeout(() => {
              expandTimer.current = null;
              opts.onExpand?.();
            }, DRAG_EXPAND_MS);
          }
        },
        onDragLeave: () => {
          if (!drag) return;
          enterCount.current -= 1;
          if (enterCount.current <= 0) {
            enterCount.current = 0;
            setOver(null);
            clearExpandTimer();
          }
        },
        onDrop: (e: React.DragEvent) => {
          e.preventDefault();
          setOver(null);
          enterCount.current = 0;
          clearExpandTimer();
          const dragged = drag;
          setDrag(null);
          if (!dragged) return;
          // Dropping a card back in its own group is a no-op, not a PUT.
          if (dragged.groupName === groupName) return;
          onDrop(dragged, groupName);
        },
      };
    },
    [drag, onDrop],
  );

  return {
    /** Truthy while a drag is in flight — protected sections dim on this. */
    dragging: drag !== null,
    /** Section key currently lit as a drop target. */
    overKey: over,
    isDraggingCard: (id: number) => drag?.id === id,
    cardProps,
    zoneProps,
  };
}
