import { useEffect, useState } from 'react';

import type { Automation } from '../-automations.types';

import { DRAG_EXPAND_MS } from '../-automations.dnd';
import { type AutomationCardHandlers, AutomationCard } from './automation-card';

/**
 * Collapse state key.
 *
 * Deliberately the SAME key the vanilla page used, so anyone who had sections
 * collapsed keeps them collapsed across the migration instead of having every
 * section spring open once.
 */
export function sectionStorageKey(id: string): string {
  return `auto_section_${id}`;
}

function readCollapsed(id: string): boolean {
  try {
    return localStorage.getItem(sectionStorageKey(id)) === '1';
  } catch {
    // Safari private mode and friends throw on localStorage access; a section
    // that cannot remember its state must still render.
    return false;
  }
}

function writeCollapsed(id: string, collapsed: boolean): void {
  try {
    localStorage.setItem(sectionStorageKey(id), collapsed ? '1' : '0');
  } catch {
    /* not remembering is acceptable; failing to collapse is not */
  }
}

/** Section id for a user group, matching the vanilla sanitiser. */
export function groupSectionId(groupName: string): string {
  return `auto-section-group-${groupName.replace(/\W+/g, '_')}`;
}

export interface GroupActions {
  onBulkToggle?: (groupName: string, allEnabled: boolean) => void;
  /** Commit a rename. Called only with a changed, non-empty name. */
  onRename?: (groupName: string, newName: string) => void;
  onDeleteGroup?: (groupName: string) => void;
}

interface Props extends AutomationCardHandlers, GroupActions {
  /** Live run state lookup, threaded down to each card. */
  progressFor?: (id: number) => import('../-automations.progress').AutomationRunState | undefined;
  blockLabel?: (type: string) => string | undefined;
  /** Drop-zone handlers for this section body, empty when protected. */
  zoneProps?: Record<string, unknown>;
  /** This body is the current drop target. */
  isDropTarget?: boolean;
  /** A drag is in flight — protected sections dim to show they refuse drops. */
  isDragActive?: boolean;
  /** Per-card drag handlers, keyed by automation. */
  cardDragProps?: (a: Automation) => Record<string, unknown>;
  isCardDragging?: (id: number) => boolean;

  id: string;
  label: string;
  /** One line of status, so a collapsed family still says something. */
  summary?: string;
  /** `R,G,B` (or a var() indirection) for this family's --tile-glow. */
  glow?: string;
  automations: Automation[];
  /**
   * Size of the section BEFORE filtering, for the header count.
   *
   * The vanilla filter only set display:none on cards — sections and their
   * counts were already rendered from the unfiltered data, so the count stayed
   * put while you typed. Defaults to what is rendered.
   */
  totalCount?: number;
  /** The unfiltered members, for decisions that must not depend on the filter. */
  allAutomations?: Automation[];
  /** System/Hub sections: no group actions, and no drop target. */
  isProtected?: boolean;
  groupName?: string;
}

export function AutomationsSection({
  id,
  label,
  summary,
  glow,
  automations,
  isProtected = false,
  groupName,
  totalCount,
  allAutomations,
  zoneProps,
  isDropTarget,
  isDragActive,
  cardDragProps,
  isCardDragging,
  onBulkToggle,
  onRename,
  onDeleteGroup,
  progressFor,
  ...cardHandlers
}: Props) {
  const [collapsed, setCollapsed] = useState(() => readCollapsed(id));
  // null = not renaming. The vanilla version swapped the label for an input
  // in place, so the section header keeps its layout while editing.
  const [rename, setRename] = useState<string | null>(null);

  // A no-op rename (empty or unchanged) just closes the editor: the vanilla
  // handler bailed on both rather than issuing a pointless PUT.
  const commitRename = () => {
    setRename((draft) => {
      if (draft === null) return null;
      const next = draft.trim();
      if (next && groupName && next !== groupName) onRename?.(groupName, next);
      return null;
    });
  };

  // Hovering a COLLAPSED section during a drag opens it, so you can drop into
  // a group you cannot see. Owned here rather than plumbed from the page: the
  // collapsed state lives in this component, and the timer must die with it.
  useEffect(() => {
    if (!isDropTarget || !collapsed) return;
    const timer = setTimeout(() => {
      writeCollapsed(id, false);
      setCollapsed(false);
    }, DRAG_EXPAND_MS);
    return () => clearTimeout(timer);
  }, [isDropTarget, collapsed, id]);

  const toggle = () => {
    setCollapsed((was) => {
      writeCollapsed(id, !was);
      return !was;
    });
  };

  // Judged over the whole group: with a filter active, "Disable all" must not
  // flip to "Enable all" just because the only visible card happens to be off.
  const forToggle = allAutomations ?? automations;
  const enabledCount = forToggle.filter((a) => a.enabled === true || a.enabled === 1).length;
  const allEnabled = forToggle.length > 0 && enabledCount === forToggle.length;
  const showGroupActions = Boolean(groupName) && !isProtected;

  return (
    <div
      className={`automations-section${isProtected ? ' section-protected' : ''}${
        collapsed ? ' collapsed' : ''
      }${isProtected && isDragActive ? ' no-drop' : ''}`}
      id={id}
      data-group-name={groupName}
      style={glow ? ({ ['--tile-glow' as string]: glow } as React.CSSProperties) : undefined}
    >
      {/* Matches the vanilla header: the whole bar toggles, except clicks that
          land inside .section-actions. */}
      <div
        className="automations-section-header"
        onClick={(e) => {
          if ((e.target as HTMLElement).closest('.section-actions')) return;
          toggle();
        }}
      >
        <span className="section-chevron">▼</span>
        {rename === null ? (
          <span className="section-label">{label}</span>
        ) : (
          <span className="section-label">
            <input
              className="section-rename-input"
              aria-label={`Rename group ${groupName ?? ''}`}
              value={rename}
              autoFocus
              onClick={(e) => e.stopPropagation()}
              onChange={(e) => setRename(e.target.value)}
              onKeyDown={(e) => {
                e.stopPropagation();
                if (e.key === 'Enter') {
                  e.preventDefault();
                  commitRename();
                }
                if (e.key === 'Escape') setRename(null);
              }}
              // Blur SAVES, matching the vanilla input — clicking away is
              // treated as accepting the edit, not discarding it.
              onBlur={commitRename}
            />
          </span>
        )}
        <span className="section-count">{totalCount ?? automations.length}</span>
        {/* The family's own status, so collapsing one does not hide whether it
            is healthy — the whole reason to group was to be able to collapse. */}
        {summary ? <span className="section-summary">{summary}</span> : null}
        {showGroupActions ? (
          <div className="section-actions" onClick={(e) => e.stopPropagation()}>
            <button
              type="button"
              className="section-action-btn"
              title={allEnabled ? 'Disable all' : 'Enable all'}
              onClick={() => onBulkToggle?.(groupName!, allEnabled)}
            >
              {allEnabled ? '⏸' : '▶'}
            </button>
            <button
              type="button"
              className="section-action-btn"
              title="Rename group"
              onClick={() => setRename(groupName!)}
            >
              ✏️
            </button>
            <button
              type="button"
              className="section-action-btn section-action-danger"
              title="Delete group"
              onClick={() => onDeleteGroup?.(groupName!)}
            >
              🗑️
            </button>
          </div>
        ) : null}
        <span className="section-line" />
      </div>

      <div
        className={`automations-section-body${isDropTarget ? ' drop-target' : ''}`}
        {...zoneProps}
      >
        {/* The cards live in their own container, NOT directly in the body:
            .automations-grid is `repeat(2, 1fr)`, so omitting it silently
            collapses the page to one card per row. Every call site — music and
            video alike — passes useGrid, hence the grid class here. */}
        <div className="automations-grid">
          {automations.map((a) => (
            <AutomationCard
              key={a.id}
              automation={a}
              progress={progressFor?.(a.id)}
              dragProps={cardDragProps?.(a)}
              isDragging={isCardDragging?.(a.id)}
              {...cardHandlers}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
