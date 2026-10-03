import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { useCallback, useMemo, useState } from 'react';

import { useProfile, useReactPageShell } from '@/platform/shell/route-controllers';

import type { Automation, AutomationsSearch } from '../-automations.types';

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
  buildCollections,
  collectionAutomations,
  collectionGroupName,
  filterAutomations,
  filterOptions,
  forMusicSide,
  readAutomationsList,
} from '../-automations.helpers';
import { useAutomationProgress } from '../-automations.progress';
import { Route } from '../route';
import { AutomationHub } from './automation-hub';
import { AutomationsLibrary, type LibraryGroupActions } from './automations-library';
import { AutomationsOverview } from './automations-overview';
import { AutomationsSidebar } from './automations-sidebar';
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

  // Dragging a card onto a sidebar group reuses the same single-row PUT the
  // card's 📁 dropdown issues, so both paths land on one endpoint.
  const dnd = useAutomationDnd((dragged, toGroup) =>
    assign.mutate({ id: dragged.id, group: toGroup }),
  );

  // Both sides share ONE endpoint; only owned_by separates them.
  const automations = useMemo(
    () => forMusicSide(readAutomationsList(listQuery.data)),
    [listQuery.data],
  );

  const setSearch = (patch: Partial<AutomationsSearch>) =>
    void navigate({ search: (prev) => ({ ...prev, ...patch }), replace: true });

  // ── Navigation ─────────────────────────────────────────────────────
  //
  // The page is two views: the overview dashboard and the library. Inside the
  // library the sidebar picks one collection; the main pane shows only that
  // collection. Changing collections resets the text/trigger/action filters —
  // carrying a query into a different collection is never what was meant.

  const collections = useMemo(() => buildCollections(automations), [automations]);
  const collectionKeys = useMemo(() => new Set(collections.map((c) => c.key)), [collections]);

  // A search only exists inside the library: a bare ?q= URL (old bookmark, or
  // a shared link) lands there too, on All, rather than on an overview that
  // would silently swallow the query.
  const searchActive = Boolean(search.q || search.trigger || search.action);
  // A fresh install has no overview to show — the "No automations yet" state
  // lives in the library, so the sidebar and the content agree on All rather
  // than the sidebar saying Overview while the content shows a collection.
  const inLibrary = search.view === 'library' || searchActive || automations.length === 0;

  const navKey = inLibrary && collectionKeys.has(search.nav) ? search.nav : 'all';
  // The guides are reference material for existing automations; a fresh
  // install sees the empty state alone, not the empty state plus docs.
  const showGuides = automations.length > 0;
  const activeNav = inLibrary && navKey === 'guides' && !showGuides ? 'all' : navKey;

  // Leaving the library leaves the search behind with it — otherwise the
  // searchActive half of inLibrary would trap the overview out of reach.
  const goOverview = () => setSearch({ view: 'overview', q: '', trigger: '', action: '' });
  const goCollection = (nav: string) =>
    setSearch({ view: 'library', nav, q: '', trigger: '', action: '' });
  const onSidebarSearch = (q: string) =>
    setSearch({ view: 'library', nav: 'all', q, trigger: '', action: '' });
  const onFind = (name: string) => setSearch({ view: 'library', nav: 'all', q: name });

  // The collection's members, then the filter-bar controls narrowing them.
  // The filter dropdowns list the collection's own types — "All Triggers"
  // inside Scheduled should not offer event triggers that cannot match.
  const members = useMemo(
    () => collectionAutomations(automations, activeNav),
    [automations, activeNav],
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
  const options = useMemo(() => filterOptions(members), [members]);
  const visible = useMemo(
    () => filterAutomations(members, search, labelFor),
    [members, search, labelFor],
  );
  const filtering = Boolean(search.q || search.trigger || search.action);

  const [groupMenu, setGroupMenu] = useState<{
    id: number;
    current: string | null;
    anchor: { top: number; bottom: number; right: number };
  } | null>(null);
  const [deletingGroup, setDeletingGroup] = useState<{ name: string; count: number } | null>(null);

  const masterOn = masterQuery.data?.music !== false;

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
        toast: `Dissolved group "${name}" — {n} automations moved to My Automations`,
      });
    } else {
      deleteGroupAll.mutate(ids);
    }
  };

  const groupName = collectionGroupName(activeNav);
  const groupMembers = groupName !== null ? members : [];
  const groupAllEnabled =
    groupMembers.length > 0 && groupMembers.every((a) => a.enabled === true || a.enabled === 1);
  const groupActions: LibraryGroupActions | undefined =
    groupName !== null
      ? {
          allEnabled: groupAllEnabled,
          onBulkToggle: () =>
            bulkToggle.mutate({
              ids: groupMembers.map((a) => a.id),
              enabled: !groupAllEnabled,
            }),
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
            setDeletingGroup({ name: groupName, count });
          },
        }
      : undefined;

  const cardHandlers = {
    // Every card needs to know the side is paused, or it goes on advertising
    // countdowns and "Listening" for runs the engine is skipping.
    paused: !masterOn,
    onToggle: (a: Automation) => toggle.mutate(a),
    onRun: (a: Automation) => run.mutate(a),
    onDuplicate: (a: Automation) => duplicate.mutate(a),
    onDelete: (a: Automation) => void confirmDelete(a),
    onEdit: (a: Automation) => openBuilder(a.id),
    // The cadence control on the card face edits the server's copy directly.
    onRefresh: () => void refresh(),
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

  const cardDragProps = (a: Automation) =>
    dnd.cardProps(a.id, a.group_name ?? null, a.is_system === true || a.is_system === 1);

  return (
    <div className="page-shell automx">
      <AutomationsSidebar
        collections={collections}
        automations={automations}
        active={inLibrary ? activeNav : 'overview'}
        onOverview={goOverview}
        onSelect={goCollection}
        query={inLibrary ? search.q : ''}
        onQuery={onSidebarSearch}
        masterOn={masterOn}
        masterPending={master.isPending}
        onMasterToggle={() => master.mutate(!masterOn)}
        onNew={() => openBuilder()}
        showGuides={showGuides}
        dnd={{ zoneProps: dnd.zoneProps, overKey: dnd.overKey, dragging: dnd.dragging }}
      />

      <main className="automx-main">
        {/* Keyed so the view crossfades on every navigation, not just the
            first paint. */}
        <div className="automx-view" key={inLibrary ? `library:${activeNav}` : 'overview'}>
          {!inLibrary ? (
            <AutomationsOverview
              automations={automations}
              paused={!masterOn}
              blockLabel={blockLabel}
              onRun={(a) => run.mutate(a)}
              onEdit={(a) => openBuilder(a.id)}
              onReviewAttention={() => goCollection('attention')}
              onFind={onFind}
              onResume={() => master.mutate(true)}
            />
          ) : activeNav === 'guides' ? (
            <div className="automx-guides">
              <AutomationHub />
            </div>
          ) : (
            <AutomationsLibrary
              def={collections.find((c) => c.key === activeNav) ?? collections[0]}
              members={members}
              visible={visible}
              filtering={filtering}
              search={search}
              setSearch={setSearch}
              filterOptions={options}
              cardDragProps={cardDragProps}
              isCardDragging={dnd.isDraggingCard}
              groupActions={groupActions}
              onNew={() => openBuilder()}
              onClearFilters={() => setSearch({ q: '', trigger: '', action: '' })}
              {...cardHandlers}
            />
          )}
        </div>
      </main>

      {groupMenu ? (
        <GroupDropdown
          groups={collections.filter((c) => c.kind === 'group').map((c) => c.groupName as string)}
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
