import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { useCallback, useMemo } from 'react';

import { useProfile, useReactPageShell } from '@/platform/shell/route-controllers';

import type { Automation } from '../-automations.types';

import {
  AUTOMATIONS_QUERY_KEY,
  automationsListQueryOptions,
  automationsMasterQueryOptions,
  bulkToggleAutomations,
  deleteAutomation,
  duplicateAutomation,
  runAutomation,
  setAutomationsMaster,
  toggleAutomation,
} from '../-automations.api';
import { formatAction, formatTrigger } from '../-automations.format';
import {
  buildAutomationsView,
  filterAutomations,
  filterOptions,
  forMusicSide,
  readAutomationsList,
} from '../-automations.helpers';
import { Route } from '../route';
import { AutomationsSection, groupSectionId } from './automations-section';

export function AutomationsPage() {
  useReactPageShell('automations');

  const { profileId } = useProfile();
  const search = Route.useSearch();
  const navigate = useNavigate({ from: Route.fullPath });

  const queryClient = useQueryClient();
  const listQuery = useQuery(automationsListQueryOptions(profileId));
  const masterQuery = useQuery(automationsMasterQueryOptions());

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

  const cardHandlers = {
    onToggle: (a: Automation) => toggle.mutate(a),
    onRun: (a: Automation) => run.mutate(a),
    onDuplicate: (a: Automation) => duplicate.mutate(a),
    onDelete: (a: Automation) => void confirmDelete(a),
    onEdit: (a: Automation) => window.showAutomationBuilder?.(a.id),
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
      trigger: formatTrigger(a.trigger_type, a.trigger_config),
      action: formatAction(a.action_type),
    }),
    [],
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

  const filtering = Boolean(search.q || search.trigger || search.action);
  const masterOn = masterQuery.data?.music !== false;
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
          <button
            type="button"
            className="auto-new-btn"
            onClick={() => window.showAutomationBuilder?.()}
          >
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
            <span className="auto-stat">
              <strong>{view.stats.active}</strong> Active
            </span>
            <span className="auto-stat">
              <strong>{view.stats.system}</strong> System
            </span>
            <span className="auto-stat">
              <strong>{view.stats.custom}</strong> Custom
            </span>
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
                {formatTrigger(t, {})}
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
                {formatAction(t)}
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
        {keep.system.length > 0 ? (
          <AutomationsSection
            id="auto-section-system"
            label="System"
            automations={keep.system}
            isProtected
            {...cardHandlers}
          />
        ) : null}

        {keep.groups.map((group) => (
          <AutomationsSection
            key={group.name}
            id={groupSectionId(group.name)}
            label={`📁 ${group.name}`}
            automations={group.automations}
            groupName={group.name}
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

        {keep.ungrouped.length > 0 ? (
          <AutomationsSection
            id="auto-section-custom"
            label="My Automations"
            automations={keep.ungrouped}
            {...cardHandlers}
          />
        ) : null}
      </div>

      {isEmpty ? (
        <div className="automations-empty">
          <div className="automations-empty-icon">⚡</div>
          <div className="automations-empty-title">No automations yet</div>
          <div className="automations-empty-text">
            Create your first automation to schedule tasks and trigger actions automatically.
          </div>
          <button
            type="button"
            className="auto-new-btn"
            onClick={() => window.showAutomationBuilder?.()}
          >
            + New Automation
          </button>
        </div>
      ) : null}
    </div>
  );
}
