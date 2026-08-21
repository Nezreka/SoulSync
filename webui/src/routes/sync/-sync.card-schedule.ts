/**
 * A playlist's sync interval, on its card.
 *
 * The Auto-Sync modal expresses one integer per playlist with ten preset lanes
 * (plus one more for every custom interval anyone has ever created), a
 * filterable sidebar with source groups and expandable variant sub-groups,
 * drop highlighting, and a bulk popover with its own custom-interval expander.
 * A dropdown on the card sets the same value with none of that.
 *
 * The board is not removed — it stays as a VIEW, which is what it is good at:
 * seeing everything on one cadence at a glance. What changes is that it is no
 * longer the only way to set the value.
 *
 * ONE ENDPOINT, LAZILY. `useAutoSync` loads five endpoints and only when the
 * modal opens; cards need intervals on every visit, and paying five requests
 * per page load to render a dropdown would undo the page's own speed. So this
 * fetches `/api/automations` alone and reuses `buildAutoSyncScheduleState` to
 * read it — the same pure builder the board uses, so the card and the board can
 * never disagree about what a playlist is scheduled for.
 */

import { useCallback, useEffect, useState } from 'react';

import type { MirroredRow } from './-sync.autosync';

import {
  AUTO_SYNC_BUCKETS,
  autoSyncCanSchedulePlaylist,
  autoSyncIntervalLabel,
  autoSyncSchedulePayload,
  autoSyncTriggerForHours,
  buildAutoSyncScheduleState,
} from './-sync.autosync';
import { createAutomation, deleteAutomation, fetchAutomations, updateAutomation } from './-sync.api';

/** What a card knows about its own schedule. */
export interface CardSchedule {
  /** Hours between runs, or null when it runs on a weekly day instead. */
  hours: number | null;
  /** The automation backing it, needed to update or delete. */
  automationId: number | string;
  /** True when the schedule is a weekly day rather than an interval. */
  weekly: boolean;
}

export type CardScheduleMap = Readonly<Record<string, CardSchedule>>;

/** The options a card's dropdown offers, plus its "off" entry. */
export const CARD_SCHEDULE_OPTIONS: readonly { value: string; label: string }[] = [
  { value: '', label: 'Not scheduled' },
  ...AUTO_SYNC_BUCKETS.map((h) => ({ value: String(h), label: autoSyncIntervalLabel(h) })),
];

/**
 * Read every owned schedule out of an automations payload.
 *
 * Delegates to the board's own builder rather than re-deriving: the mapping
 * from an automation to "this playlist runs every N hours" has real subtlety in
 * it (ownership, trigger shape, id extraction) and two copies would drift.
 */
export function cardSchedulesFrom(automations: unknown[]): CardScheduleMap {
  const state = buildAutoSyncScheduleState([], (automations || []) as never[]);
  const out: Record<string, CardSchedule> = {};
  for (const [playlistId, entry] of Object.entries(state.playlistSchedules)) {
    out[playlistId] = { hours: entry.hours, automationId: entry.automation_id, weekly: false };
  }
  for (const [playlistId, entry] of Object.entries(state.weeklySchedules)) {
    // A weekly schedule has no interval to show. The card says so rather than
    // pretending it is unscheduled, which would invite someone to overwrite a
    // weekly plan by picking an hourly one without realising.
    out[playlistId] = { hours: null, automationId: entry.automation_id, weekly: true };
  }
  return out;
}

/** The label a card shows for its current schedule. */
export function cardScheduleLabel(schedule: CardSchedule | undefined): string {
  if (!schedule) return 'Not scheduled';
  if (schedule.weekly) return 'Weekly';
  return schedule.hours ? autoSyncIntervalLabel(schedule.hours) : 'Not scheduled';
}

export interface CardScheduleController {
  schedules: CardScheduleMap;
  /** Null until the first load lands — cards render no control before then. */
  loaded: boolean;
  /** Set an interval, or pass null to unschedule. */
  set: (row: MirroredRow, hours: number | null) => Promise<void>;
  /** Playlist ids currently being written, so a card can disable its select. */
  busy: ReadonlySet<string>;
}

export interface UseCardSchedulesOptions {
  toast?: (message: string, kind: string) => void;
}

export function useCardSchedules(options: UseCardSchedulesOptions = {}): CardScheduleController {
  const [schedules, setSchedules] = useState<CardScheduleMap>({});
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState<ReadonlySet<string>>(() => new Set());

  const load = useCallback(async () => {
    try {
      const res = await fetchAutomations();
      const data = (await res.json()) as { automations?: unknown[] } | unknown[];
      const rows = Array.isArray(data) ? data : (data.automations ?? []);
      setSchedules(cardSchedulesFrom(rows));
    } catch {
      // Best-effort: a card without its interval still works, and a failed
      // read must not stop the library rendering.
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const set = useCallback(
    async (row: MirroredRow, hours: number | null) => {
      // MirroredRow.id is optional on the type; a row without one cannot be
      // scheduled at all, and guessing an id would schedule the wrong playlist.
      if (row.id === undefined) return;
      const playlistId = String(row.id);
      const toast = options.toast ?? ((m: string, k: string) => window.showToast?.(m, k));

      if (hours !== null && !autoSyncCanSchedulePlaylist(row)) {
        toast('That playlist source cannot be refreshed by Auto-Sync.', 'info');
        return;
      }

      setBusy((prev) => new Set(prev).add(playlistId));
      try {
        const existing = schedules[playlistId];
        if (hours === null) {
          if (!existing) return;
          const res = await deleteAutomation(existing.automationId);
          if (!res.ok) throw new Error('Failed to unschedule');
          toast(`${row.name || 'Playlist'} is no longer scheduled`, 'success');
        } else {
          const payload = autoSyncSchedulePayload(row, row.id, {
            trigger_type: 'schedule',
            trigger_config: autoSyncTriggerForHours(hours),
          });
          // A weekly schedule is REPLACED rather than updated in place: the two
          // are different trigger shapes, and updating one into the other is
          // how you end up with a row that claims both.
          const res =
            existing && !existing.weekly
              ? await updateAutomation(existing.automationId, payload)
              : await createAutomation(payload);
          const data = (await res.json()) as { error?: string };
          if (!res.ok || data.error) throw new Error(data.error || 'Failed to save schedule');
          if (existing?.weekly) await deleteAutomation(existing.automationId);
          toast(`${row.name || 'Playlist'} syncs ${autoSyncIntervalLabel(hours)}`, 'success');
        }
        await load();
      } catch (err) {
        toast(`Error: ${err instanceof Error ? err.message : String(err)}`, 'error');
      } finally {
        setBusy((prev) => {
          const next = new Set(prev);
          next.delete(playlistId);
          return next;
        });
      }
    },
    [schedules, load, options.toast],
  );

  return { schedules, loaded, set, busy };
}
