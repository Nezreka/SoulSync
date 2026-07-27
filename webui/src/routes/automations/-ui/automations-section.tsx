import { useState } from 'react';

import type { Automation } from '../-automations.types';

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
  onRename?: (groupName: string) => void;
  onDeleteGroup?: (groupName: string) => void;
}

interface Props extends AutomationCardHandlers, GroupActions {
  id: string;
  label: string;
  automations: Automation[];
  /** System/Hub sections: no group actions, and no drop target. */
  isProtected?: boolean;
  groupName?: string;
}

export function AutomationsSection({
  id,
  label,
  automations,
  isProtected = false,
  groupName,
  onBulkToggle,
  onRename,
  onDeleteGroup,
  ...cardHandlers
}: Props) {
  const [collapsed, setCollapsed] = useState(() => readCollapsed(id));

  const toggle = () => {
    setCollapsed((was) => {
      writeCollapsed(id, !was);
      return !was;
    });
  };

  const enabledCount = automations.filter((a) => a.enabled === true || a.enabled === 1).length;
  const allEnabled = enabledCount === automations.length;
  const showGroupActions = Boolean(groupName) && !isProtected;

  return (
    <div
      className={`automations-section${isProtected ? ' section-protected' : ''}${
        collapsed ? ' collapsed' : ''
      }`}
      id={id}
      data-group-name={groupName}
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
        <span className="section-label">{label}</span>
        <span className="section-count">{automations.length}</span>
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
              onClick={() => onRename?.(groupName!)}
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

      <div className="automations-section-body">
        {automations.map((a) => (
          <AutomationCard key={a.id} automation={a} {...cardHandlers} />
        ))}
      </div>
    </div>
  );
}
