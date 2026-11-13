import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';

import { useProfile, useReactPageShell } from '@/platform/shell/route-controllers';

import type { Automation, AutomationsSearch, AutomationsSide } from '../-automations.types';

import {
  AUTOMATIONS_QUERY_KEY,
  automationsActivityQueryOptions,
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
import {
  blockLabelLookup,
  displayName,
  formatAction,
  formatTrigger,
  timeUntil,
} from '../-automations.format';
import {
  attentionItems,
  collectionAutomations,
  filterAutomations,
  filterOptions,
  forSide,
  librarySections,
  readAutomationsList,
  upcomingRuns,
} from '../-automations.helpers';
import { useAutomationProgress, useSecondTick } from '../-automations.progress';
import { Route } from '../route';
import { AutomationHub } from './automation-hub';
import { AutomationsActivity } from './automations-activity';
import {
  type LibraryGroupActions,
  type LibraryLens,
  AutomationsLibrary,
} from './automations-library';
import { AutomationsOverview } from './automations-overview';
import { type DeleteGroupChoice, DeleteGroupDialog } from './delete-group-dialog';
import { GroupDropdown } from './group-dropdown';

/**
 * One page for both sides. `side` picks whose automations it shows and whose
 * master switch it flips; the video side reaches it on /automations?media=video.
 */
export function AutomationsPage({ side = 'music' }: { side?: AutomationsSide }) {
  useReactPageShell('automations', side === 'video' ? '?media=video' : undefined);

  const { profileId } = useProfile();
  const search = Route.useSearch();
  const navigate = useNavigate({ from: Route.fullPath });

  const queryClient = useQueryClient();
  const listQuery = useQuery(automationsListQueryOptions(profileId));
  // What every automation did in the last day. The overview's Needs attention
  // reads it too: `last_error` alone forgets a failure after one clean run.
  const activityQuery = useQuery(automationsActivityQueryOptions(profileId, side));
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
  const blocksQuery = useQuery(automationBlocksQueryOptions(side));
  const blockLabel = useMemo(() => blockLabelLookup(blocksQuery.data), [blocksQuery.data]);

  const progressSeed = useQuery(automationsProgressQueryOptions());
  const progress = useAutomationProgress(progressSeed.data);
  // The builder stays in vanilla and is shared by both sides; this hands the
  // shell over for the edit and takes it back on close.
  const openBuilder = useVanillaBuilder(() => void refresh(), side);

  const refresh = () => queryClient.invalidateQueries({ queryKey: AUTOMATIONS_QUERY_KEY });

  // The Guides tab is vanilla markup, and deploying a pipeline from it creates
  // rows behind this page's back. It says so with this event.
  useEffect(() => {
    const changed = () => void queryClient.invalidateQueries({ queryKey: AUTOMATIONS_QUERY_KEY });
    document.addEventListener('soulsync:automations-changed', changed);
    return () => document.removeEventListener('soulsync:automations-changed', changed);
  }, [queryClient]);
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
    mutationFn: (enabled: boolean) => setAutomationsMaster(side, enabled),
    onSuccess: async (_v, enabled) => {
      window.showToast?.(
        `${side === 'video' ? 'Video' : 'Music'} automations ${enabled ? 'resumed' : 'paused'}`,
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

  // Dragging a row onto a group section reuses the same single-row PUT the
  // row's "Move to group" issues, so both paths land on one endpoint.
  const dnd = useAutomationDnd((dragged, toGroup) =>
    assign.mutate({ id: dragged.id, group: toGroup }),
  );

  // Both sides share ONE endpoint; only owned_by separates them.
  const automations = useMemo(
    () => forSide(readAutomationsList(listQuery.data), side),
    [listQuery.data, side],
  );
  const recent = useMemo(() => activityQuery.data?.recent ?? [], [activityQuery.data]);

  const setSearch = (patch: Partial<AutomationsSearch>) =>
    void navigate({ search: (prev) => ({ ...prev, ...patch }), replace: true });

  // ── Navigation ─────────────────────────────────────────────────────
  //
  // Four places: the overview, every automation, what they did, the guides.
  // Inside the list, `nav` is a lens across the sections (all, attention,
  // scheduled, events, off) or, from an older link, one section on its own.

  const sections = useMemo(() => librarySections(automations), [automations]);
  const sectionKeys = useMemo(() => new Set(sections.map((c) => c.key)), [sections]);

  // A search only exists inside the list: a bare ?q= URL (old bookmark, or a
  // shared link) lands there too, rather than on an overview that would
  // silently swallow the query.
  const searchActive = Boolean(search.q || search.trigger || search.action);
  // A fresh install has no overview to show: the "No automations yet" state
  // lives in the list.
  const noneYet = automations.length === 0;
  // The guides are reference material for existing automations; a fresh
  // install sees the empty state alone, not the empty state plus docs.
  const showGuides = !noneYet;
  const wantsGuides = search.view === 'library' && search.nav === 'guides' && !searchActive;
  const place: 'overview' | 'all' | 'activity' | 'guides' =
    wantsGuides && showGuides
      ? 'guides'
      : search.view === 'activity' && !searchActive && !noneYet
        ? 'activity'
        : search.view === 'library' || searchActive || noneYet
          ? 'all'
          : 'overview';

  const LENS_KEYS = ['all', 'attention', 'scheduled', 'events', 'off'];
  // `system` was one section once; it reads as everything now, which shows both
  const activeNav =
    place === 'all' && (LENS_KEYS.includes(search.nav) || sectionKeys.has(search.nav))
      ? search.nav
      : 'all';

  // Leaving the list leaves the search behind with it: otherwise the
  // searchActive half of `place` would trap the other pills out of reach.
  const cleared = { q: '', trigger: '', action: '' };
  const goOverview = () => setSearch({ view: 'overview', nav: 'all', ...cleared });
  const goLens = (nav: string) => setSearch({ view: 'library', nav, ...cleared });
  const goActivity = () => setSearch({ view: 'activity', nav: 'all', ...cleared });
  const goGuides = () => setSearch({ view: 'library', nav: 'guides', ...cleared });
  const onFind = (name: string) => setSearch({ view: 'library', nav: 'all', q: name });

  const attention = useMemo(
    () => attentionItems(automations, recent).map((item) => item.automation),
    [automations, recent],
  );

  // What the lens holds, before the text and type filters. Needs attention is
  // not `collectionAutomations('attention')`: that one only knows the last run.
  const lensMembers = useCallback(
    (key: string) => (key === 'attention' ? attention : collectionAutomations(automations, key)),
    [attention, automations],
  );
  const members = useMemo(() => lensMembers(activeNav), [lensMembers, activeNav]);

  // The filter matches the rendered label text, so the same formatters that
  // build the row must produce the strings the filter searches.
  const labelFor = useCallback(
    (a: Automation) => ({
      trigger: formatTrigger(a.trigger_type, a.trigger_config, blockLabel),
      action: formatAction(a.action_type, blockLabel),
    }),
    [blockLabel],
  );
  const options = useMemo(() => filterOptions(members), [members]);
  const visible = useMemo(
    () => filterAutomations(members, search, labelFor),
    [members, search, labelFor],
  );
  const filtering = Boolean(search.q || search.trigger || search.action);

  const lenses: LibraryLens[] = [
    { key: 'all', label: 'All', count: automations.length },
    { key: 'attention', label: 'Needs attention', count: attention.length },
    { key: 'scheduled', label: 'Scheduled', count: lensMembers('scheduled').length },
    { key: 'events', label: 'Event-driven', count: lensMembers('events').length },
    { key: 'off', label: 'Switched off', count: lensMembers('off').length },
  ];

  const librarySectionRows = useMemo(() => {
    const shown = new Set(visible.map((a) => a.id));
    // an older link to one section shows that section alone
    const only = sectionKeys.has(activeNav) ? activeNav : null;
    // on before off, then by the name the row shows
    const inOrder = (rows: Automation[]) =>
      [...rows].sort(
        (a, b) =>
          Number(b.enabled === true || b.enabled === 1) -
            Number(a.enabled === true || a.enabled === 1) ||
          displayName(a).localeCompare(displayName(b)),
      );
    return sections
      .filter((def) => only === null || def.key === only)
      .map((def) => ({
        def,
        rows: inOrder(collectionAutomations(automations, def.key)).filter(
          (a) => only !== null || shown.has(a.id),
        ),
      }))
      .map((section) =>
        only === null
          ? section
          : { ...section, rows: filterAutomations(section.rows, search, labelFor) },
      );
  }, [sections, sectionKeys, activeNav, visible, automations, search, labelFor]);

  const [groupMenu, setGroupMenu] = useState<{
    id: number;
    current: string | null;
    anchor: { top: number; bottom: number; right: number };
  } | null>(null);
  const [deletingGroup, setDeletingGroup] = useState<{ name: string; count: number } | null>(null);

  // music runs unless someone paused it; video starts paused (most installs
  // predate it), so it is on only once the engine says so
  const masterOn =
    side === 'video' ? masterQuery.data?.video === true : masterQuery.data?.music !== false;

  const idsInGroup = (name: string) =>
    collectionAutomations(automations, `group:${name}`).map((a) => a.id);

  const onDeleteGroupChoice = (choice: DeleteGroupChoice) => {
    if (!deletingGroup) return;
    const { name } = deletingGroup;
    const ids = idsInGroup(name);
    setDeletingGroup(null);
    if (choice === 'ungroup') {
      regroup.mutate({
        ids,
        group: null,
        toast: `Dissolved group "${name}". {n} automations moved to My Automations`,
      });
    } else {
      deleteGroupAll.mutate(ids);
    }
  };

  const groupActionsFor = (name: string): LibraryGroupActions => {
    const groupMembers = collectionAutomations(automations, `group:${name}`);
    const allEnabled =
      groupMembers.length > 0 && groupMembers.every((a) => a.enabled === true || a.enabled === 1);
    return {
      allEnabled,
      onBulkToggle: () =>
        bulkToggle.mutate({ ids: groupMembers.map((a) => a.id), enabled: !allEnabled }),
      onRename: (next: string) =>
        regroup.mutate({
          ids: groupMembers.map((a) => a.id),
          group: next,
          toast: `Renamed to "${next}"`,
        }),
      onDeleteGroup: () => {
        const count = groupMembers.length;
        // Nothing to decide about an empty group; just refresh it away.
        if (count === 0) return void refresh();
        setDeletingGroup({ name, count });
      },
    };
  };

  const showHistory = (a: Automation) =>
    // Body-attached modal, so unlike the builder it needs no shell handoff.
    window.showAutomationHistory?.(a.id, a.name, a.action_type ?? '');

  const cardHandlers = {
    // Every row needs to know the side is paused, or it goes on advertising
    // countdowns and "Listening" for runs the engine is skipping.
    paused: !masterOn,
    onToggle: (a: Automation) => toggle.mutate(a),
    onRun: (a: Automation) => run.mutate(a),
    onDuplicate: (a: Automation) => duplicate.mutate(a),
    onDelete: (a: Automation) => void confirmDelete(a),
    onEdit: (a: Automation) => openBuilder(a.id),
    // The When block on an opened row edits the server's copy directly.
    onRefresh: () => void refresh(),
    onShowHistory: showHistory,
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

  const cardDragProps = (a: Automation) =>
    dnd.cardProps(a.id, a.group_name ?? null, a.is_system === true || a.is_system === 1);

  const pills: { key: typeof place; label: string; count?: number; go: () => void }[] = [
    { key: 'overview', label: 'Overview', go: goOverview },
    { key: 'all', label: 'All automations', count: automations.length, go: () => goLens('all') },
    { key: 'activity', label: 'Activity', go: goActivity },
    ...(showGuides ? [{ key: 'guides' as const, label: 'Guides', go: goGuides }] : []),
  ];

  const armed = automations.filter((a) => a.enabled === true || a.enabled === 1).length;

  return (
    <div className="page-shell automx">
      <div className="automx-page">
        <header className="automx-head">
          <div className="automx-head-main">
            <h1>Automations</h1>
            <HeadStatus
              automations={automations}
              armed={armed}
              paused={!masterOn}
              attention={attention.length}
            />
          </div>
          <div className="automx-head-acts">
            <button
              type="button"
              className={`automx-cap automx-master${masterOn ? ' on' : ''}`}
              disabled={master.isPending}
              aria-pressed={masterOn}
              onClick={() => master.mutate(!masterOn)}
              title={
                masterOn
                  ? 'Automations are live. Click to pause every scheduled and event run on this side. Individual switches keep their state, and manual Run still works.'
                  : 'Automations are paused: nothing runs on a schedule or event. Individual switches keep their state, and manual Run still works.'
              }
            >
              <span className="automx-master-sw" aria-hidden="true" />
              <span className="automx-master-label">{masterOn ? 'Automations on' : 'Paused'}</span>
            </button>
            <button
              type="button"
              className="automx-cap automx-cap-primary automx-new-btn"
              onClick={() => openBuilder()}
            >
              New automation
            </button>
          </div>
        </header>

        <PillBar pills={pills} active={place} />

        {/* Keyed so the view settles in on every navigation, not just the
            first paint. */}
        <div className="automx-view" key={place}>
          {place === 'overview' ? (
            <AutomationsOverview
              automations={automations}
              paused={!masterOn}
              blockLabel={blockLabel}
              recent={recent}
              onRun={(a) => run.mutate(a)}
              onEdit={(a) => openBuilder(a.id)}
              onReviewAttention={() => goLens('attention')}
              onFind={onFind}
              onResume={() => master.mutate(true)}
            />
          ) : place === 'guides' ? (
            <div className="automx-guides">
              <AutomationHub />
            </div>
          ) : place === 'activity' ? (
            <AutomationsActivity
              activity={activityQuery.data}
              loading={activityQuery.isPending}
              automations={automations}
              onShowHistory={showHistory}
            />
          ) : (
            <AutomationsLibrary
              lenses={lenses}
              active={activeNav}
              onLens={goLens}
              sections={librarySectionRows}
              total={automations.length}
              lensTotal={members.length}
              filtering={filtering}
              search={search}
              setSearch={setSearch}
              filterOptions={options}
              cardDragProps={cardDragProps}
              isCardDragging={dnd.isDraggingCard}
              groupActionsFor={groupActionsFor}
              dnd={{ zoneProps: dnd.zoneProps, overKey: dnd.overKey, dragging: dnd.dragging }}
              onNew={() => openBuilder()}
              onClearFilters={() => setSearch(cleared)}
              {...cardHandlers}
            />
          )}
        </div>
      </div>

      {groupMenu ? (
        <GroupDropdown
          groups={sections.filter((c) => c.kind === 'group').map((c) => c.groupName as string)}
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

/**
 * The line under the title: how many are on, and when the next one runs. Its
 * own component so the page does not re-render every second for a countdown.
 */
function HeadStatus({
  automations,
  armed,
  paused,
  attention,
}: {
  automations: Automation[];
  armed: number;
  paused: boolean;
  attention: number;
}) {
  useSecondTick();
  const next = upcomingRuns(automations, paused)[0];
  const off = automations.length - armed;
  if (automations.length === 0) return <p className="automx-head-sub">Nothing set up yet</p>;
  return (
    <p className="automx-head-sub">
      {armed} on{off ? ` · ${off} off` : ''}
      {paused ? (
        ' · paused'
      ) : next?.next_run ? (
        <>
          {' · next run '}
          <i>{timeUntil(next.next_run)}</i>
        </>
      ) : null}
      {attention ? (
        <b>{` · ${attention} ${attention === 1 ? 'needs' : 'need'} attention`}</b>
      ) : null}
    </p>
  );
}

/** The four places, with a pill that slides under the one in view. */
function PillBar<K extends string>({
  pills,
  active,
}: {
  pills: { key: K; label: string; count?: number; go: () => void }[];
  active: K;
}) {
  const bar = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const host = bar.current;
    const tab = host?.querySelector<HTMLElement>('[aria-selected="true"]');
    if (!host || !tab) return;
    host.style.setProperty('--automx-pill-x', `${tab.offsetLeft}px`);
    host.style.setProperty('--automx-pill-w', `${tab.offsetWidth}px`);
  }, [active, pills.length]);

  return (
    <div className="automx-pills" role="tablist" aria-label="Automations" ref={bar}>
      <span className="automx-pill-ink" aria-hidden="true" />
      {pills.map((pill) => (
        <button
          key={pill.key}
          type="button"
          role="tab"
          className="automx-pill"
          aria-selected={pill.key === active}
          onClick={pill.go}
        >
          {pill.label}
          {pill.count !== undefined ? <small>{pill.count}</small> : null}
        </button>
      ))}
    </div>
  );
}
