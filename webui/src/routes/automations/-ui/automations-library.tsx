import { useState } from 'react';

import type { Automation, AutomationsSearch } from '../-automations.types';

import { formatAction, formatTrigger } from '../-automations.format';
import { type CollectionDef, collectionGroupName } from '../-automations.helpers';
import { AUTO_FILTER_BAR_MIN } from '../-automations.types';
import { type AutomationCardHandlers, AutomationCard } from './automation-card';

export interface LibraryGroupActions {
  /** Every member is enabled — drives the Enable all / Disable all label. */
  allEnabled: boolean;
  onBulkToggle: () => void;
  onRename: (next: string) => void;
  onDeleteGroup: () => void;
}

interface Props extends AutomationCardHandlers {
  def: CollectionDef;
  /** Collection members before the text/trigger/action filter. */
  members: Automation[];
  /** Members after the filter. */
  visible: Automation[];
  filtering: boolean;
  search: AutomationsSearch;
  setSearch: (patch: Partial<AutomationsSearch>) => void;
  filterOptions: { triggers: string[]; actions: string[] };
  progressFor: (id: number) => import('../-automations.progress').AutomationRunState | undefined;
  cardDragProps: (a: Automation) => Record<string, unknown>;
  isCardDragging: (id: number) => boolean;
  /** Present only when browsing a user group. */
  groupActions?: LibraryGroupActions;
  onNew: () => void;
  onClearFilters: () => void;
}

const COLLECTION_ICONS: Record<string, string> = {
  all: '▦',
  attention: '⚠',
  scheduled: '◷',
  events: '⚡',
  off: '○',
  system: '⚙',
  ungrouped: '◇',
  group: '📁',
};

function EmptyState({
  def,
  filtering,
  onClearFilters,
  onNew,
}: {
  def: CollectionDef;
  filtering: boolean;
  onClearFilters: () => void;
  onNew: () => void;
}) {
  // A fresh install lands here: the only thing on the page is the invitation.
  if (def.key === 'all' && !filtering) {
    return (
      <div className="automx-empty">
        <div className="automx-empty-icon">⚡</div>
        <div className="automx-empty-title">No automations yet</div>
        <div className="automx-empty-text">
          Automations watch for things happening and do the tedious work for you — retrying
          downloads, scanning for new releases, tidying the library.
        </div>
        <button type="button" className="automx-new-btn automx-new-btn-lg" onClick={onNew}>
          + New Automation
        </button>
      </div>
    );
  }
  if (filtering) {
    return (
      <div className="automx-empty automx-empty-small">
        <div className="automx-empty-title">No matches</div>
        <div className="automx-empty-text">Nothing in here matches those filters.</div>
        <button type="button" className="automx-text-btn" onClick={onClearFilters}>
          Clear filters
        </button>
      </div>
    );
  }
  const copy: Record<string, { title: string; text: string }> = {
    attention: {
      title: 'Nothing needs attention',
      text: 'Every automation is running as configured. Enjoy the quiet.',
    },
    off: {
      title: 'Nothing switched off',
      text: 'Every automation is armed.',
    },
    scheduled: {
      title: 'No scheduled automations',
      text: 'Timer-driven automations will appear here.',
    },
    events: {
      title: 'No event-driven automations',
      text: 'Automations that listen for events will appear here.',
    },
  };
  const groupName = collectionGroupName(def.key);
  if (groupName !== null) {
    return (
      <div className="automx-empty automx-empty-small">
        <div className="automx-empty-title">This group is empty</div>
        <div className="automx-empty-text">
          Drag cards here from another collection, or use the 📁 button on a card.
        </div>
      </div>
    );
  }
  const c = copy[def.key] ?? { title: 'Nothing here yet', text: '' };
  return (
    <div className="automx-empty automx-empty-small">
      <div className="automx-empty-title">{c.title}</div>
      {c.text ? <div className="automx-empty-text">{c.text}</div> : null}
    </div>
  );
}

/**
 * One collection, browsed: header, filter bar, card grid.
 *
 * This is what the old page's sections became — instead of every family
 * stacked in one scroll, the sidebar picks the family and this shows it.
 * Everything the old section headers did (counts, bulk toggle, rename,
 * delete group) moved into this header when a group is being browsed.
 */
export function AutomationsLibrary({
  def,
  members,
  visible,
  filtering,
  search,
  setSearch,
  filterOptions,
  progressFor,
  cardDragProps,
  isCardDragging,
  groupActions,
  onNew,
  onClearFilters,
  ...cardHandlers
}: Props) {
  const [renaming, setRenaming] = useState<string | null>(null);
  const groupName = collectionGroupName(def.key);
  const icon = def.kind === 'group' ? COLLECTION_ICONS.group : (COLLECTION_ICONS[def.key] ?? '◇');

  const commitRename = () => {
    setRenaming((draft) => {
      if (draft === null) return null;
      const next = draft.trim();
      if (next && groupName && next !== groupName) groupActions?.onRename(next);
      return null;
    });
  };

  return (
    <div className="automx-library">
      <div className="automx-library-head">
        <span className="automx-library-icon" aria-hidden="true">
          {icon}
        </span>
        <div className="automx-library-titles">
          {renaming === null ? (
            <h2 className="automx-library-title">{def.label}</h2>
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
          <p className="automx-library-sub">
            {def.description} · {members.length}{' '}
            {members.length === 1 ? 'automation' : 'automations'}
          </p>
        </div>
        {groupActions ? (
          <div className="automx-library-actions">
            <button
              type="button"
              className="automx-chip-btn"
              title={groupActions.allEnabled ? 'Disable all' : 'Enable all'}
              onClick={groupActions.onBulkToggle}
            >
              {groupActions.allEnabled ? '⏸ Disable all' : '▶ Enable all'}
            </button>
            <button
              type="button"
              className="automx-chip-btn"
              title="Rename group"
              onClick={() => setRenaming(groupName ?? '')}
            >
              ✏️ Rename
            </button>
            <button
              type="button"
              className="automx-chip-btn automx-chip-danger"
              title="Delete group"
              onClick={groupActions.onDeleteGroup}
            >
              🗑 Delete
            </button>
          </div>
        ) : null}
      </div>

      {members.length >= AUTO_FILTER_BAR_MIN ? (
        <div className="automx-filter-bar">
          <input
            type="text"
            className="automx-filter-search"
            placeholder="Filter automations…"
            aria-label="Filter automations"
            value={search.q}
            onChange={(e) => setSearch({ q: e.target.value })}
          />
          <select
            className="automx-filter-select"
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
            className="automx-filter-select"
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
          <span className="automx-filter-count">
            {filtering ? `${visible.length} of ${members.length}` : ''}
          </span>
        </div>
      ) : null}

      {visible.length > 0 ? (
        <div className="automations-grid">
          {visible.map((a) => (
            <AutomationCard
              key={a.id}
              automation={a}
              progress={progressFor(a.id)}
              dragProps={cardDragProps(a)}
              isDragging={isCardDragging(a.id)}
              {...cardHandlers}
            />
          ))}
        </div>
      ) : (
        <EmptyState def={def} filtering={filtering} onClearFilters={onClearFilters} onNew={onNew} />
      )}
    </div>
  );
}
