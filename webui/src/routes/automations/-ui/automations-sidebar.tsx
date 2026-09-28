import type { Automation } from '../-automations.types';

import {
  type CollectionDef,
  collectionCount,
  collectionGroupName,
  collectionHealth,
} from '../-automations.helpers';

/**
 * Icons for the smart collections. User groups all share the folder glyph;
 * their identity is the name, not an icon.
 */
const COLLECTION_ICONS: Record<string, string> = {
  all: '▦',
  attention: '⚠',
  scheduled: '◷',
  events: '⚡',
  off: '○',
  system: '⚙',
  ungrouped: '◇',
  guides: '📖',
  group: '📁',
};

function iconFor(def: CollectionDef): string {
  if (def.kind === 'group') return COLLECTION_ICONS.group;
  return COLLECTION_ICONS[def.key] ?? '◇';
}

export interface SidebarDnd {
  zoneProps: (
    key: string,
    group: string | null,
    opts?: { isProtected?: boolean },
  ) => Record<string, unknown>;
  overKey: string | null;
  dragging: boolean;
}

interface Props {
  collections: CollectionDef[];
  automations: Automation[];
  /** 'overview' or the active collection key when view=library. */
  active: string;
  onOverview: () => void;
  onSelect: (navKey: string) => void;
  query: string;
  onQuery: (q: string) => void;
  masterOn: boolean;
  masterPending: boolean;
  onMasterToggle: () => void;
  onNew: () => void;
  showGuides: boolean;
  dnd: SidebarDnd;
}

/**
 * The library sidebar: the page's new navigation.
 *
 * The old page stacked every family vertically — System, each group, then the
 * ungrouped remainder — so reaching anything meant scrolling past everything
 * else. The sidebar turns those families plus a few smart lenses into one
 * click each: the main pane shows exactly one collection at a time.
 *
 * Group rows double as drop targets: dragging a card onto a group in the
 * sidebar re-files it, reusing the same zone keys the old section bodies used
 * ('system', `group:<name>`, 'ungrouped'), so the drop contract is unchanged.
 */
export function AutomationsSidebar({
  collections,
  automations,
  active,
  onOverview,
  onSelect,
  query,
  onQuery,
  masterOn,
  masterPending,
  onMasterToggle,
  onNew,
  showGuides,
  dnd,
}: Props) {
  const smart = collections.filter(
    (c) => !['system', 'ungrouped', 'guides'].includes(c.key) && c.kind !== 'group',
  );
  const system = collections.find((c) => c.key === 'system');
  const groups = collections.filter((c) => c.kind === 'group');
  const ungrouped = collections.find((c) => c.key === 'ungrouped');
  const guides = showGuides ? collections.find((c) => c.key === 'guides') : undefined;

  const renderRow = (def: CollectionDef) => {
    const count = collectionCount(automations, def.key);
    const health = collectionHealth(automations, def.key);
    const isActive = active === def.key;
    const isAttention = def.key === 'attention';
    const groupName = collectionGroupName(def.key);
    // System is protected (never a drop target); groups and the ungrouped
    // bucket accept drops, keyed exactly as the old section bodies were.
    const dropKey =
      def.key === 'system'
        ? null
        : groupName !== null
          ? def.key
          : def.key === 'ungrouped'
            ? 'ungrouped'
            : null;
    const zoneProps =
      dropKey !== null
        ? dnd.zoneProps(
            dropKey,
            groupName,
            def.key === 'system' ? { isProtected: true } : undefined,
          )
        : {};
    const isDropTarget = dropKey !== null && dnd.overKey === dropKey;

    return (
      <button
        key={def.key}
        type="button"
        className={`automx-nav-item${isActive ? ' active' : ''}${
          isDropTarget ? ' automx-drop-target' : ''
        }${isAttention && (count ?? 0) > 0 ? ' automx-nav-attention' : ''}`}
        title={def.description}
        onClick={() => onSelect(def.key)}
        {...zoneProps}
      >
        <span className="automx-nav-icon" aria-hidden="true">
          {iconFor(def)}
        </span>
        <span className="automx-nav-label">{def.label}</span>
        {def.kind === 'group' || def.key === 'system' || def.key === 'ungrouped' ? (
          <span
            className={`automx-health-dot ${health}`}
            title={
              health === 'bad'
                ? 'Something in here is failing'
                : health === 'warn'
                  ? 'Something in here is off or never ran'
                  : health === 'ok'
                    ? 'All healthy'
                    : undefined
            }
          />
        ) : null}
        {count !== null ? (
          <span className={`automx-nav-count${isAttention && count > 0 ? ' alert' : ''}`}>
            {count}
          </span>
        ) : null}
      </button>
    );
  };

  return (
    <aside className="automx-sidebar">
      <div className="automx-search">
        <span className="automx-search-icon" aria-hidden="true">
          ⌕
        </span>
        <input
          type="text"
          className="automx-search-input"
          placeholder="Search automations…"
          aria-label="Search automations"
          value={query}
          onChange={(e) => onQuery(e.target.value)}
        />
        {query ? (
          <button
            type="button"
            className="automx-search-clear"
            aria-label="Clear search"
            onClick={() => onQuery('')}
          >
            ×
          </button>
        ) : null}
      </div>

      <nav className="automx-nav" aria-label="Automations navigation">
        <button
          type="button"
          className={`automx-nav-item automx-nav-overview${active === 'overview' ? ' active' : ''}`}
          onClick={onOverview}
        >
          <span className="automx-nav-icon" aria-hidden="true">
            ◉
          </span>
          <span className="automx-nav-label">Overview</span>
        </button>

        <div className="automx-nav-heading">Library</div>
        {smart.map(renderRow)}

        <div className="automx-nav-heading">Groups</div>
        {system ? renderRow(system) : null}
        {groups.map(renderRow)}
        {ungrouped ? renderRow(ungrouped) : null}

        {guides ? (
          <>
            <div className="automx-nav-heading">Resources</div>
            {renderRow(guides)}
          </>
        ) : null}
      </nav>

      <div className="automx-sidebar-foot">
        <button
          type="button"
          className={`automx-master${masterOn ? ' on' : ''}`}
          disabled={masterPending}
          onClick={onMasterToggle}
          title={
            masterOn
              ? 'Automations are live. Click to pause every scheduled and event run on this side — individual switches keep their state, and manual Run still works.'
              : 'Automations are paused: nothing runs on a schedule or event. Individual switches keep their state, and manual Run still works.'
          }
        >
          <span className="automx-master-sw" aria-hidden="true" />
          <span className="automx-master-label">{masterOn ? 'Automations on' : 'Paused'}</span>
        </button>
        <button type="button" className="automx-new-btn" onClick={onNew}>
          + New Automation
        </button>
      </div>
    </aside>
  );
}
