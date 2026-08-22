/**
 * A playlist's cadence, set from its card.
 *
 * This is what replaces the Auto-Sync board. That board expressed ONE INTEGER
 * per playlist using ten preset lanes plus one per custom interval, a
 * filterable sidebar with source groups and expandable variant sub-groups, drop
 * highlighting, and a bulk popover with its own custom-interval expander. A menu
 * on the card sets the same value with none of it.
 *
 * WEEKLY IS THE INTERESTING CASE. A weekly trigger is `{days[], time, tz}`, so a
 * flat list cannot express "Mon + Wed + Fri at 03:00" — which is why the board
 * had a whole second tab and rendered the same card once per selected day. The
 * common shapes become presets, and anything else opens the existing weekly
 * editor. The board dies; the editor survives, and the once-per-day duplication
 * dies with the board that forced it.
 */

import { useRef, useState } from 'react';

import type { MirroredRow } from '../-sync.autosync';

import {
  AUTO_SYNC_BUCKETS,
  autoSyncIntervalLabel,
  autoSyncParseCustomInterval,
} from '../-sync.autosync';
import { usePopoverDismiss } from './use-popover-dismiss';
import { usePopoverPosition } from './use-popover-position';

/** The day-sets worth a one-click preset; everything else is Custom. */
export const SCHEDULE_WEEKLY_PRESETS: readonly { label: string; days: string[] }[] = [
  { label: 'Weekly on Monday', days: ['mon'] },
  { label: 'Every weekday', days: ['mon', 'tue', 'wed', 'thu', 'fri'] },
  { label: 'Weekends', days: ['sat', 'sun'] },
];

export interface ScheduleMenuProps {
  row: MirroredRow;
  /** Hours between runs, null when it is weekly or unscheduled. */
  hours: number | null;
  weekly: boolean;
  anchor: { top: number; left: number };
  /** The trigger, so clicking the pill again closes rather than rebuilds. */
  anchorEl?: HTMLElement | null;
  onClose: () => void;
  /** Pass null to unschedule. */
  onPickHours: (hours: number | null) => void;
  onPickWeekly: (days: string[]) => void;
  /** Opens the full weekly editor for an arbitrary day-set. */
  onCustomWeekly: () => void;
}

export function ScheduleMenu({
  row,
  hours,
  weekly,
  anchor,
  anchorEl,
  onClose,
  onPickHours,
  onPickWeekly,
  onCustomWeekly,
}: ScheduleMenuProps) {
  const ref = useRef<HTMLDivElement>(null);
  const pos = usePopoverPosition(anchor, ref);
  /** The board's bulk popover had a custom-interval field; the ten presets are
   *  not the whole range, and someone running every 36 hours must keep it. */
  const [custom, setCustom] = useState<string | null>(null);
  const [customError, setCustomError] = useState('');

  usePopoverDismiss({ ref, anchor: anchorEl, onClose });

  const item = (label: string, active: boolean, run: () => void) => (
    <button
      key={label}
      type="button"
      className={`pl-menu-item${active ? ' pl-menu-item--on' : ''}`}
      onClick={() => {
        run();
        onClose();
      }}
    >
      {label}
      {active ? <span className="pl-menu-tick">✓</span> : null}
    </button>
  );

  return (
    <div
      className="pl-menu pl-menu--schedule"
      ref={ref}
      role="menu"
      aria-label={`Sync schedule for ${row.name ?? 'playlist'}`}
      style={{ top: `${pos.top}px`, left: `${pos.left}px` }}
    >
      {item('Not scheduled', hours === null && !weekly, () => {
        onPickHours(null);
      })}

      <div className="pl-menu-heading">Every</div>
      {AUTO_SYNC_BUCKETS.map((h) =>
        item(autoSyncIntervalLabel(h), hours === h, () => {
          onPickHours(h);
        }),
      )}

      {custom === null ? (
        // Ticked only when the current interval is NOT one of the presets,
        // which is exactly when 'custom' is the true answer.
        item('Custom interval…', hours !== null && !AUTO_SYNC_BUCKETS.includes(hours), () => {
          setCustom(String(hours ?? 6));
        })
      ) : (
        <div className="pl-menu-custom">
          <input
            type="number"
            min={1}
            autoFocus
            aria-label="Custom interval in hours"
            placeholder="hours"
            value={custom}
            onChange={(e) => {
              setCustom(e.target.value);
              setCustomError('');
            }}
            onKeyDown={(e) => {
              if (e.key !== 'Enter') return;
              const parsed = autoSyncParseCustomInterval(custom);
              if ('error' in parsed) {
                setCustomError(parsed.error);
                return;
              }
              onPickHours(parsed.hours);
              onClose();
            }}
          />
          <button
            type="button"
            onClick={() => {
              const parsed = autoSyncParseCustomInterval(custom);
              if ('error' in parsed) {
                setCustomError(parsed.error);
                return;
              }
              onPickHours(parsed.hours);
              onClose();
            }}
          >
            Set
          </button>
          {customError ? <small className="pl-menu-error">{customError}</small> : null}
        </div>
      )}

      <div className="pl-menu-heading">Weekly</div>
      {SCHEDULE_WEEKLY_PRESETS.map((preset) =>
        item(preset.label, false, () => {
          onPickWeekly(preset.days);
        }),
      )}
      {/* Arbitrary day-sets, a time and a timezone need the real editor —
          which already exists, and is all that survives of the weekly board. */}
      {item(weekly ? 'Edit weekly schedule…' : 'Custom weekly…', weekly, onCustomWeekly)}
    </div>
  );
}
