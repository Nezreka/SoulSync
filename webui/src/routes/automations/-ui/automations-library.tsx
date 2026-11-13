import { useState } from 'react';

import type { AutomationRunState } from '../-automations.progress';
import type { Automation, AutomationsSearch } from '../-automations.types';

import { formatAction, formatTrigger } from '../-automations.format';
import { type CollectionDef, collectionGroupName } from '../-automations.helpers';
import { AUTO_FILTER_BAR_MIN } from '../-automations.types';
import { type AutomationCardHandlers, AutomationCard } from './automation-card';

export interface LibraryGroupActions {
  /** Every member is enabled: drives the Enable all / Disable all label. */
  allEnabled: boolean;
  onBulkToggle: () => void;
  onRename: (next: string) => void;
  onDeleteGroup: () => void;
}

export interface LibraryLens {
  key: string;
  label: string;
  count: number;
}

export interface LibrarySection {
  def: CollectionDef;
  /** the section's automations after the lens and the filters */
  rows: Automation[];
}

export interface SectionDnd {
  zoneProps: (
    key: string,
    group: string | null,
    opts?: { isProtected?: boolean; onExpand?: () => void },
  ) => Record<string, unknown>;
  overKey: string | null;
  dragging: boolean;
}

interface Props extends AutomationCardHandlers {
  /** All, Needs attention, Scheduled, Event-driven, Switched off. */
  lenses: LibraryLens[];
  /** the lens in force, or a section key when one section is shown alone */
  active: string;
  onLens: (key: string) => void;
  sections: LibrarySection[];
  /** every automation on this side, before any lens or filter */
  total: number;
  /** how many the lens holds before the text and type filters */
  lensTotal: number;
  filtering: boolean;
  search: AutomationsSearch;
  setSearch: (patch: Partial<AutomationsSearch>) => void;
  filterOptions: { triggers: string[]; actions: string[] };
  progressFor: (id: number) => AutomationRunState | undefined;
  cardDragProps: (a: Automation) => Record<string, unknown>;
  isCardDragging: (id: number) => boolean;
  /** Bulk toggle, rename and delete for a user group. */
  groupActionsFor: (groupName: string) => LibraryGroupActions;
  dnd: SectionDnd;
  onNew: () => void;
  onClearFilters: () => void;
}

const SECTION_NOTES: Record<string, string> = {
  operations: 'The pipeline: wishlist, watchlist, scans, database',
  maintenance: 'Cleanup, backups and the Tools jobs on a timer',
  ungrouped: 'Ones you built, not filed into a group',
};

const EMPTY_LENS: Record<string, { title: string; text: string }> = {
  attention: {
    title: 'Nothing needs attention',
    text: 'Every automation that is on ran as configured.',
  },
  off: { title: 'Nothing switched off', text: 'Every automation is on.' },
  scheduled: { title: 'No scheduled automations', text: 'Ones on a timer show here.' },
  events: {
    title: 'No event-driven automations',
    text: 'Ones that wait for something to happen show here.',
  },
};

function SectionHead({
  def,
  count,
  open,
  onToggle,
  actions,
}: {
  def: CollectionDef;
  count: number;
  open: boolean;
  onToggle: () => void;
  actions?: LibraryGroupActions;
}) {
  const [renaming, setRenaming] = useState<string | null>(null);
  const groupName = collectionGroupName(def.key);
  const system = def.key === 'operations' || def.key === 'maintenance';
  const note =
    SECTION_NOTES[def.key] ?? (groupName !== null ? 'A group of yours' : def.description);

  const commitRename = () => {
    setRenaming((draft) => {
      if (draft === null) return null;
      const next = draft.trim();
      if (next && groupName && next !== groupName) actions?.onRename(next);
      return null;
    });
  };

  return (
    <div className="automx-sec-head">
      {renaming === null ? (
        <button type="button" className="automx-sec-toggle" aria-expanded={open} onClick={onToggle}>
          <h3>{def.label}</h3>
          <span className="automx-sec-note">
            {count} · {note}
          </span>
        </button>
      ) : (
        <input
          className="automx-rename-input"
          aria-label={`Rename group ${groupName ?? ''}`}
          value={renaming}
          autoFocus
          onChange={(e) => setRenaming(e.target.value)}
          onKeyDown={(e) => {
            e.stopPropagation();
            if (e.key === 'Enter') {
              e.preventDefault();
              commitRename();
            }
            if (e.key === 'Escape') setRenaming(null);
          }}
          onBlur={commitRename}
        />
      )}
      <span className="automx-sec-side">
        {system ? <span className="automx-chip">System</span> : null}
        {actions && open ? (
          <>
            <button
              type="button"
              className="automx-cap"
              title={actions.allEnabled ? 'Disable all' : 'Enable all'}
              onClick={actions.onBulkToggle}
            >
              {actions.allEnabled ? 'Disable all' : 'Enable all'}
            </button>
            <button
              type="button"
              className="automx-cap"
              title="Rename group"
              onClick={() => setRenaming(groupName ?? '')}
            >
              Rename
            </button>
            <button
              type="button"
              className="automx-cap automx-cap-danger"
              title="Delete group"
              onClick={actions.onDeleteGroup}
            >
              Delete
            </button>
          </>
        ) : null}
        <button
          type="button"
          className="automx-icon-btn automx-chevron"
          aria-label={`${open ? 'Collapse' : 'Expand'} ${def.label}`}
          aria-expanded={open}
          onClick={onToggle}
        >
          <svg viewBox="0 0 16 16" aria-hidden="true">
            <path d="m4 6 4 4 4-4" />
          </svg>
        </button>
      </span>
    </div>
  );
}

/**
 * Every automation, as sections of rows.
 *
 * The lenses across the top (All, Needs attention, Scheduled, Event-driven,
 * Switched off) cut across the sections. Maintenance starts folded under All:
 * it is fifty rows that mirror the Tools page jobs, and it would bury the
 * twenty a person came for. Any lens or search unfolds it.
 */
export function AutomationsLibrary({
  lenses,
  active,
  onLens,
  sections,
  total,
  lensTotal,
  filtering,
  search,
  setSearch,
  filterOptions,
  progressFor,
  cardDragProps,
  isCardDragging,
  groupActionsFor,
  dnd,
  onNew,
  onClearFilters,
  ...cardHandlers
}: Props) {
  // sections the user folded or unfolded by hand; the rest follow the default
  const [folded, setFolded] = useState<Record<string, boolean>>({});
  const [openId, setOpenId] = useState<number | null>(null);

  const plain = active === 'all' && !filtering;
  const isOpen = (key: string) => folded[key] ?? !(plain && key === 'maintenance');
  const shown = sections.reduce((n, s) => n + s.rows.length, 0);

  // A fresh install: the only thing on the page is the invitation.
  if (total === 0) {
    return (
      <div className="automx-empty">
        <div className="automx-empty-title">No automations yet</div>
        <div className="automx-empty-text">
          Automations watch for things happening and do the tedious work for you: retrying
          downloads, scanning for new releases, tidying the library.
        </div>
        <button type="button" className="automx-cap automx-cap-primary" onClick={onNew}>
          New automation
        </button>
      </div>
    );
  }

  return (
    <div className="automx-library">
      <div className="automx-bar">
        <div className="automx-lenses" role="group" aria-label="Show">
          {lenses.map((lens) => (
            <button
              key={lens.key}
              type="button"
              className={`automx-cap automx-lens${lens.key === 'attention' && lens.count > 0 ? ' alert' : ''}`}
              aria-pressed={active === lens.key}
              onClick={() => onLens(lens.key)}
            >
              {lens.label} <small>{lens.count}</small>
            </button>
          ))}
        </div>
        {/* one group, so a narrow bar moves the search and its two filters to
            the next line together instead of orphaning the last one */}
        <div className="automx-filters">
          <input
            type="search"
            className="automx-search"
            placeholder="Search automations"
            aria-label="Search automations"
            value={search.q}
            onChange={(e) => setSearch({ q: e.target.value })}
          />
          {lensTotal >= AUTO_FILTER_BAR_MIN ? (
            <>
              <select
                className="automx-select"
                aria-label="Filter by trigger"
                value={search.trigger}
                onChange={(e) => setSearch({ trigger: e.target.value })}
              >
                <option value="">All Triggers</option>
                {filterOptions.triggers.map((t) => (
                  <option key={t} value={t}>
                    {formatTrigger(t, {}, cardHandlers.blockLabel)}
                  </option>
                ))}
              </select>
              <select
                className="automx-select"
                aria-label="Filter by action"
                value={search.action}
                onChange={(e) => setSearch({ action: e.target.value })}
              >
                <option value="">All Actions</option>
                {filterOptions.actions.map((t) => (
                  <option key={t} value={t}>
                    {formatAction(t, cardHandlers.blockLabel)}
                  </option>
                ))}
              </select>
            </>
          ) : null}
          {filtering ? (
            <span className="automx-filter-count">
              {shown} of {lensTotal}
            </span>
          ) : null}
        </div>
      </div>

      {shown === 0 && filtering ? (
        <div className="automx-empty automx-empty-small">
          <div className="automx-empty-title">No matches</div>
          <div className="automx-empty-text">Nothing in here matches those filters.</div>
          <button type="button" className="automx-cap" onClick={onClearFilters}>
            Clear filters
          </button>
        </div>
      ) : shown === 0 && EMPTY_LENS[active] ? (
        <div className="automx-empty automx-empty-small">
          <div className="automx-empty-title">{EMPTY_LENS[active].title}</div>
          <div className="automx-empty-text">{EMPTY_LENS[active].text}</div>
        </div>
      ) : (
        sections.map(({ def, rows }) => {
          const groupName = collectionGroupName(def.key);
          const system = def.key === 'operations' || def.key === 'maintenance';
          const droppable = groupName !== null || def.key === 'ungrouped';
          // an empty section only shows when nothing is narrowing the list:
          // the unfiled one, as the place to start
          if (rows.length === 0 && !(plain && def.key === 'ungrouped')) return null;
          const open = isOpen(def.key);
          const toggle = () => setFolded((f) => ({ ...f, [def.key]: !open }));
          const zone = droppable
            ? dnd.zoneProps(def.key, groupName, {
                onExpand: () => setFolded((f) => ({ ...f, [def.key]: false })),
              })
            : {};
          return (
            <section
              key={def.key}
              className={`automx-sec${open ? ' open' : ''}${
                droppable && dnd.overKey === def.key ? ' automx-drop-target' : ''
              }${system && dnd.dragging ? ' automx-drop-off' : ''}`}
              data-section={def.key}
              aria-label={def.label}
              {...zone}
            >
              <SectionHead
                def={def}
                count={rows.length}
                open={open}
                onToggle={toggle}
                actions={groupName !== null ? groupActionsFor(groupName) : undefined}
              />
              {open ? (
                <div className="automx-sec-body">
                  {rows.length === 0 ? (
                    <div className="automx-empty automx-empty-small">
                      <div className="automx-empty-title">Nothing here yet</div>
                      <div className="automx-empty-text">
                        New automation builds one. Drag a row onto a group to file it.
                      </div>
                    </div>
                  ) : (
                    rows.map((a) => (
                      <AutomationCard
                        key={a.id}
                        automation={a}
                        progress={progressFor(a.id)}
                        dragProps={cardDragProps(a)}
                        isDragging={isCardDragging(a.id)}
                        open={openId === a.id}
                        onOpenChange={(next) => setOpenId(next ? a.id : null)}
                        {...cardHandlers}
                      />
                    ))
                  )}
                </div>
              ) : null}
            </section>
          );
        })
      )}
    </div>
  );
}
