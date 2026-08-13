import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { useCallback, useMemo, useState } from 'react';

import { useProfile, useReactPageShell } from '@/platform/shell/route-controllers';

import type { Automation } from '../-automations.types';

import {
  AUTOMATIONS_QUERY_KEY,
  automationsListQueryOptions,
  automationBlocksQueryOptions,
  automationsMasterQueryOptions,
  automationsProgressQueryOptions,
  assignAutomationGroup,
  bulkToggleAutomations,
  deleteAutomation,
  duplicateAutomation,
  regroupAutomations,
  runAutomation,
  setAutomationsMaster,
  toggleAutomation,
} from '../-automations.api';
import { useVanillaBuilder } from '../-automations.builder';
import { useAutomationDnd } from '../-automations.dnd';
import { blockLabelLookup, formatAction, formatTrigger } from '../-automations.format';
import {
  automationHealth,
  buildAutomationsView,
  sectionGlow,
  sectionSummary,
  filterAutomations,
  filterOptions,
  forMusicSide,
  readAutomationsList,
} from '../-automations.helpers';
import { useAutomationProgress } from '../-automations.progress';
import { Route } from '../route';
import { AutomationHub } from './automation-hub';
import { AutomationsSection, groupSectionId } from './automations-section';
import { type DeleteGroupChoice, DeleteGroupDialog } from './delete-group-dialog';
import { GroupDropdown } from './group-dropdown';

export function AutomationsPage() {
  useReactPageShell('automations');

  const { profileId } = useProfile();
  const search = Route.useSearch();
  const navigate = useNavigate({ from: Route.fullPath });

  const queryClient = useQueryClient();
  const listQuery = useQuery(automationsListQueryOptions(profileId));
  const masterQuery = useQuery(automationsMasterQueryOptions());
  // Live run state, merged from the socket mirror. Not query-cached: it is a
  // stream of transient frames, not a resource with a canonical server copy.
  //
  // Seeded from /api/automations/progress so a page opened DURING a run shows
  // that run immediately. Without the seed the card stays blank until the next
  // socket frame — which for a long quiet phase can be a while. loadAutomations
  // did the same catch-up fetch right after painting.
  // Labels for trigger/action types the static maps do not cover. Cached
  // indefinitely — block definitions only change when the app ships new ones.
  const blocksQuery = useQuery(automationBlocksQueryOptions());
  const blockLabel = useMemo(() => blockLabelLookup(blocksQuery.data), [blocksQuery.data]);

  const progressSeed = useQuery(automationsProgressQueryOptions());
  const progress = useAutomationProgress(progressSeed.data);
  // The builder stays in vanilla and is shared with the video page; this hands
  // the shell over for the edit and takes it back on close.
  const openBuilder = useVanillaBuilder(() => void refresh());

  const refresh = () => queryClient.invalidateQueries({ queryKey: AUTOMATIONS_QUERY_KEY });
  const fail = (error: Error) => window.showToast?.(`Error: ${error.message}`, 'error');

  const toggle = useMutation({
    mutationFn: (a: Automation) => toggleAutomation(a.id),
    // Silent on success, as the vanilla toggle was — the switch itself is the
    // feedback, and a toast per flick would be noise.
    onSuccess: () => refresh(),
    onError: fail,
  });

  const run = useMutation({
    mutationFn: (a: Automation) => runAutomation(a.id),
    onSuccess: () => {
      window.showToast?.('Automation triggered', 'success');
      // The run is async server-side; the vanilla page waited 1.5s before
      // refetching so last_run/run_count have a chance to move. Refetching
      // immediately would just redraw the same card.
      setTimeout(() => void refresh(), 1500);
    },
    onError: fail,
  });

  const duplicate = useMutation({
    mutationFn: (a: Automation) => duplicateAutomation(a.id),
    onSuccess: async () => {
      window.showToast?.('Automation duplicated', 'success');
      await refresh();
    },
    onError: fail,
  });

  const remove = useMutation({
    mutationFn: (a: Automation) => deleteAutomation(a.id),
    onSuccess: async () => {
      window.showToast?.('Automation deleted', 'success');
      await refresh();
    },
    onError: fail,
  });

  const bulkToggle = useMutation({
    mutationFn: ({ ids, enabled }: { ids: number[]; enabled: boolean }) =>
      bulkToggleAutomations(ids, enabled),
    onSuccess: async (updated, { enabled }) => {
      window.showToast?.(`${enabled ? 'Enabled' : 'Disabled'} ${updated} automations`, 'success');
      await refresh();
    },
    onError: fail,
  });

  const master = useMutation({
    mutationFn: (enabled: boolean) => setAutomationsMaster('music', enabled),
    onSuccess: async (_v, enabled) => {
      window.showToast?.(
        `Music automations ${enabled ? 'resumed' : 'paused'}`,
        enabled ? 'success' : 'info',
      );
      await refresh();
    },
    onError: fail,
  });

  // Destructive, so it is confirm-gated exactly as the vanilla handler was.
  const confirmDelete = async (a: Automation) => {
    const ok = await window.showConfirmDialog?.({
      title: 'Delete Automation',
      message: `Delete automation "${a.name}"?`,
      confirmText: 'Delete',
      destructive: true,
    });
    if (ok === false) return;
    remove.mutate(a);
  };

  const assign = useMutation({
    mutationFn: ({ id, group }: { id: number; group: string | null }) =>
      assignAutomationGroup(id, group),
    onSuccess: async (_v, { group }) => {
      window.showToast?.(group ? `Moved to "${group}"` : 'Removed from group', 'success');
      await refresh();
    },
    onError: fail,
  });

  const regroup = useMutation({
    mutationFn: ({ ids, group }: { ids: number[]; group: string | null; toast: string }) =>
      regroupAutomations(ids, group),
    onSuccess: async (updated, { toast }) => {
      window.showToast?.(toast.replace('{n}', String(updated)), 'success');
      await refresh();
    },
    onError: fail,
  });

  // Deleting every automation in a group is N deletes; the API has no bulk
  // delete. Sequential rather than parallel so a mid-way failure leaves a
  // comprehensible state instead of a scatter of partial results.
  const deleteGroupAll = useMutation({
    mutationFn: async (ids: number[]) => {
      for (const id of ids) await deleteAutomation(id);
      return ids.length;
    },
    onSuccess: async (n) => {
      window.showToast?.(`Deleted ${n} automation${n !== 1 ? 's' : ''}`, 'success');
      await refresh();
    },
    onError: fail,
  });

  // Dragging a card into another group's body reuses the same single-row PUT
  // the 📁 dropdown issues, so both paths land on one endpoint.
  const dnd = useAutomationDnd((dragged, toGroup) =>
    assign.mutate({ id: dragged.id, group: toGroup }),
  );

  const idsInGroup = (name: string) =>
    view.groups.find((g) => g.name === name)?.automations.map((a) => a.id) ?? [];

  const onDeleteGroupChoice = (choice: DeleteGroupChoice) => {
    if (!deletingGroup) return;
    const { name } = deletingGroup;
    const ids = idsInGroup(name);
    setDeletingGroup(null);
    if (choice === 'ungroup') {
      regroup.mutate({
        ids,
        group: null,
        toast: `Dissolved group "${name}" — {n} automations moved to My Automations`,
      });
    } else {
      deleteGroupAll.mutate(ids);
    }
  };

  const masterOn = masterQuery.data?.music !== false;

  const cardHandlers = {
    // Every card needs to know the side is paused, or it goes on advertising
    // countdowns and "Listening" for runs the engine is skipping.
    paused: !masterOn,
    onToggle: (a: Automation) => toggle.mutate(a),
    onRun: (a: Automation) => run.mutate(a),
    onDuplicate: (a: Automation) => duplicate.mutate(a),
    onDelete: (a: Automation) => void confirmDelete(a),
    onEdit: (a: Automation) => openBuilder(a.id),
    // Body-attached modal, so unlike the builder it needs no shell handoff.
    onShowHistory: (a: Automation) =>
      window.showAutomationHistory?.(a.id, a.name, a.action_type ?? ''),
    progressFor: (id: number) => progress[id],
    blockLabel,
    onAssignGroup: (a: Automation, event: React.MouseEvent) => {
      const rect = (event.currentTarget as HTMLElement).getBoundingClientRect();
      setGroupMenu({
        id: a.id,
        current: a.group_name ?? null,
        anchor: { top: rect.top, bottom: rect.bottom, right: rect.right },
      });
    },
  };

  const groupActions = {
    onRename: (name: string, next: string) =>
      regroup.mutate({ ids: idsInGroup(name), group: next, toast: `Renamed to "${next}"` }),
    onDeleteGroup: (name: string) => {
      const count = idsInGroup(name).length;
      // Nothing to decide about an empty group; just refresh it away.
      if (count === 0) return void refresh();
      setDeletingGroup({ name, count });
    },
  };

  // Both sides share ONE endpoint; only owned_by separates them.
  const automations = useMemo(
    () => forMusicSide(readAutomationsList(listQuery.data)),
    [listQuery.data],
  );

  // The vanilla filter matched the rendered label text, so the same formatters
  // that build the card must produce the strings the filter searches.
  const labelFor = useCallback(
    (a: Automation) => ({
      trigger: formatTrigger(a.trigger_type, a.trigger_config, blockLabel),
      action: formatAction(a.action_type, blockLabel),
    }),
    [blockLabel],
  );

  // Stats and the filter-bar threshold describe the WHOLE set: filtering down
  // to one card must not make the bar read "1 Active" or hide the very filter
  // being used. Only the section contents narrow.
  const view = useMemo(() => buildAutomationsView(automations), [automations]);
  const options = useMemo(() => filterOptions(automations), [automations]);
  const visible = useMemo(
    () => filterAutomations(automations, search, labelFor),
    [automations, search, labelFor],
  );
  const shown = useMemo(() => new Set(visible.map((a) => a.id)), [visible]);
  const keep = useMemo(
    () => ({
      system: view.system.filter((a) => shown.has(a.id)),
      groups: view.groups
        .map((g) => ({ ...g, automations: g.automations.filter((a) => shown.has(a.id)) }))
        .filter((g) => g.automations.length > 0),
      ungrouped: view.ungrouped.filter((a) => shown.has(a.id)),
    }),
    [view, shown],
  );

  const [groupMenu, setGroupMenu] = useState<{
    id: number;
    current: string | null;
    anchor: { top: number; bottom: number; right: number };
  } | null>(null);
  const [deletingGroup, setDeletingGroup] = useState<{ name: string; count: number } | null>(null);

  const health = useMemo(() => automationHealth(automations, !masterOn), [automations, masterOn]);
  const filtering = Boolean(search.q || search.trigger || search.action || search.health);
  const isEmpty = automations.length === 0;

  const setSearch = (patch: Partial<typeof search>) =>
    void navigate({ search: (prev) => ({ ...prev, ...patch }), replace: true });

  // NOTE: deliberately no id attributes below.
  //
  // The vanilla markup in index.html keeps #automations-list, #auto-filter-bar,
  // #auto-filter-search and friends until the cleanup PR, and three vanilla
  // functions still reach for them with getElementById — which returns the
  // FIRST match in document order. Rendering the same ids here would create
  // duplicates and hand those functions the wrong node. Every rule that styles
  // this page is class-scoped (only #automations-list-view, the outer wrapper
  // we do not render, is an id rule), so nothing is lost by omitting them.
  return (
    <div className="page-shell automations-container">
      <div className="dashboard-header">
        <div className="dashboard-header-sweep" aria-hidden="true">
          <span />
        </div>
        <div className="header-text">
          <h2 className="header-title">
            <img src="/static/automation.png" className="page-header-icon" alt="" />
            <span>Automations</span>
          </h2>
          <p className="header-subtitle">Configure scheduled tasks and automated workflows</p>
        </div>
        <div className="header-spacer" />
        <div className="header-actions">
          <button type="button" className="auto-new-btn" onClick={() => openBuilder()}>
            + New Automation
          </button>
        </div>
      </div>

      <div className="automations-stats">
        {/* The master switch is prepended to this bar and stays reachable even
            with nothing configured — pausing is a side-wide control. */}
        <button
          type="button"
          className={`auto-master-toggle${masterOn ? ' on' : ''}`}
          disabled={master.isPending}
          onClick={() => master.mutate(!masterOn)}
          title={
            masterOn
              ? 'Automations are live. Click to pause every scheduled and event run on this side — individual switches keep their state, and manual Run still works.'
              : 'Automations are paused: nothing runs on a schedule or event. Individual switches keep their state, and manual Run still works.'
          }
        >
          <span className="auto-master-sw" />
          <span className="auto-master-label">
            {masterOn ? 'Automations on' : 'Automations paused'}
          </span>
        </button>
        {isEmpty ? null : (
          <>
            {/* The verdict, not an inventory. Active/System/Custom counted rows
                in a table; these answer "is automation working", and each one
                is a lens rather than a number you can only look at. */}
            <button
              type="button"
              className={`auto-stat auto-stat-lens${search.health === '' ? ' active' : ''}`}
              onClick={() => setSearch({ health: '' })}
              title="Show every automation"
            >
              <strong>{health.armed}</strong> Armed
            </button>
            {health.failing > 0 ? (
              <button
                type="button"
                className={`auto-stat auto-stat-lens failing${
                  search.health === 'failing' ? ' active' : ''
                }`}
                onClick={() => setSearch({ health: 'failing' })}
                title="Automations whose last run failed"
              >
                <strong>{health.failing}</strong> Failing
              </button>
            ) : null}
            {health.neverRun > 0 ? (
              <button
                type="button"
                className={`auto-stat auto-stat-lens waiting${
                  search.health === 'never' ? ' active' : ''
                }`}
                onClick={() => setSearch({ health: 'never' })}
                title="Enabled, but has not run yet"
              >
                <strong>{health.neverRun}</strong> Never run
              </button>
            ) : null}
            {view.stats.total - health.armed > 0 ? (
              <button
                type="button"
                className={`auto-stat auto-stat-lens${search.health === 'off' ? ' active' : ''}`}
                onClick={() => setSearch({ health: 'off' })}
                title="Switched off"
              >
                <strong>{view.stats.total - health.armed}</strong> Off
              </button>
            ) : null}
            {health.ok ? <span className="auto-stat-allgood">All good</span> : null}
          </>
        )}
      </div>

      {view.showFilterBar ? (
        <div className="auto-filter-bar">
          <input
            type="text"
            className="auto-filter-search"
            placeholder="Filter automations…"
            aria-label="Filter automations"
            value={search.q}
            onChange={(e) => setSearch({ q: e.target.value })}
          />
          <select
            className="auto-filter-select"
            aria-label="Filter by trigger"
            value={search.trigger}
            onChange={(e) => setSearch({ trigger: e.target.value })}
          >
            <option value="">All Triggers</option>
            {options.triggers.map((t) => (
              <option key={t} value={t}>
                {formatTrigger(t, {}, blockLabel)}
              </option>
            ))}
          </select>
          <select
            className="auto-filter-select"
            aria-label="Filter by action"
            value={search.action}
            onChange={(e) => setSearch({ action: e.target.value })}
          >
            <option value="">All Actions</option>
            {options.actions.map((t) => (
              <option key={t} value={t}>
                {formatAction(t, blockLabel)}
              </option>
            ))}
          </select>
          {/* Blank unless a filter is active, exactly as the vanilla count did. */}
          <span className="auto-filter-count">
            {filtering ? `${visible.length} of ${automations.length}` : ''}
          </span>
        </div>
      ) : null}

      <div className="automations-list">
        {/* Presence and count come from the UNFILTERED set: the vanilla filter
            only hid cards, so a section never disappeared mid-search and its
            count never moved. Only the cards inside narrow. */}
        {view.system.length > 0 ? (
          <AutomationsSection
            id="auto-section-system"
            label="System"
            summary={sectionSummary(view.system)}
            glow={sectionGlow('system')}
            automations={keep.system}
            totalCount={view.system.length}
            allAutomations={view.system}
            isProtected
            zoneProps={dnd.zoneProps('system', null, { isProtected: true })}
            isDragActive={dnd.dragging}
            cardDragProps={(a) =>
              dnd.cardProps(a.id, a.group_name ?? null, a.is_system === true || a.is_system === 1)
            }
            isCardDragging={dnd.isDraggingCard}
            {...cardHandlers}
          />
        ) : null}

        {view.groups.map((group) => (
          <AutomationsSection
            key={group.name}
            id={groupSectionId(group.name)}
            label={`📁 ${group.name}`}
            summary={sectionSummary(group.automations)}
            glow={sectionGlow('group', group.name)}
            automations={keep.groups.find((g) => g.name === group.name)?.automations ?? []}
            totalCount={group.automations.length}
            allAutomations={group.automations}
            groupName={group.name}
            zoneProps={dnd.zoneProps(`group:${group.name}`, group.name)}
            isDropTarget={dnd.overKey === `group:${group.name}`}
            isDragActive={dnd.dragging}
            cardDragProps={(a) =>
              dnd.cardProps(a.id, a.group_name ?? null, a.is_system === true || a.is_system === 1)
            }
            isCardDragging={dnd.isDraggingCard}
            {...groupActions}
            onBulkToggle={(name, allEnabled) =>
              bulkToggle.mutate({
                // Ids come from the unfiltered data. _bulkToggleGroup scraped
                // the DOM, which was equivalent there because its filter only
                // set display:none and left the cards in place. React does not
                // render filtered-out cards at all, so a DOM query here would
                // genuinely miss them and quietly toggle a subset of the group.
                ids: view.groups.find((g) => g.name === name)?.automations.map((a) => a.id) ?? [],
                enabled: !allEnabled,
              })
            }
            {...cardHandlers}
          />
        ))}

        {/* The Hub is reference material, so it sits BELOW the user's own
            automations rather than between them and System. Hidden on an empty
            list: a fresh install should see the empty state alone, not the
            empty state plus a wall of documentation. */}
        {view.ungrouped.length > 0 ? (
          <AutomationsSection
            id="auto-section-custom"
            label="My Automations"
            summary={sectionSummary(view.ungrouped)}
            glow={sectionGlow('ungrouped')}
            automations={keep.ungrouped}
            totalCount={view.ungrouped.length}
            allAutomations={view.ungrouped}
            zoneProps={dnd.zoneProps('ungrouped', null)}
            isDropTarget={dnd.overKey === 'ungrouped'}
            isDragActive={dnd.dragging}
            cardDragProps={(a) =>
              dnd.cardProps(a.id, a.group_name ?? null, a.is_system === true || a.is_system === 1)
            }
            isCardDragging={dnd.isDraggingCard}
            {...cardHandlers}
          />
        ) : null}
        {isEmpty ? null : <AutomationHub />}
      </div>

      {isEmpty ? (
        <div className="automations-empty">
          <div className="automations-empty-icon">⚡</div>
          <div className="automations-empty-title">No automations yet</div>
          <div className="automations-empty-text">
            Create your first automation to schedule tasks and trigger actions automatically.
          </div>
          <button type="button" className="auto-new-btn" onClick={() => openBuilder()}>
            + New Automation
          </button>
        </div>
      ) : null}

      {groupMenu ? (
        <GroupDropdown
          groups={view.groups.map((g) => g.name)}
          currentGroup={groupMenu.current}
          anchor={groupMenu.anchor}
          onClose={() => setGroupMenu(null)}
          onAssign={(name) => {
            assign.mutate({ id: groupMenu.id, group: name });
            setGroupMenu(null);
          }}
        />
      ) : null}

      {deletingGroup ? (
        <DeleteGroupDialog
          groupName={deletingGroup.name}
          count={deletingGroup.count}
          onChoose={onDeleteGroupChoice}
          onCancel={() => setDeletingGroup(null)}
        />
      ) : null}
    </div>
  );
}
