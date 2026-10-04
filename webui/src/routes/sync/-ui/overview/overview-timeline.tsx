/**
 * The Overview's "Up Next" timeline: the next 48 hours of scheduled playlist
 * syncs on one glowing axis, plus the unscheduled tray below it.
 *
 * The tray chips are HTML5-draggable; dropping one anywhere on the timeline
 * track calls `onScheduleDrop` with the row and the drop point, and the parent
 * opens the existing ScheduleMenu there. The timeline itself never writes —
 * scheduling goes through the parent's `useCardSchedules` controller.
 */

import { useMemo, useState } from 'react';

import type { MirroredPlaylistRow } from '../../-sync.mirrored';
import type { OverviewTimelineEntry } from './use-overview-model';

import { PlaylistArt, playlistArtUrl } from '../playlist-art';

const WINDOW_MS = 48 * 3600 * 1000;
/** Two lanes are enough: nodes closer than this share of the axis collide. */
const LANE_GAP_PCT = 9;

/** The name a user actually sees — custom first, then display, then source. */
export function overviewDisplayName(row: MirroredPlaylistRow): string {
  return row.custom_name || row.display_name || row.name || 'Untitled playlist';
}

/** "in 2h" / "in 16h" / "tomorrow" / "in 3d", from the model's snapshot now. */
export function overviewRelativeLabel(nextRunMs: number, now: number): string {
  const mins = Math.max(0, Math.round((nextRunMs - now) / 60000));
  if (mins < 1) return 'now';
  if (mins < 60) return `in ${mins}m`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `in ${hours}h`;
  const days = Math.round(hours / 24);
  if (days <= 1) return 'tomorrow';
  return `in ${days}d`;
}

const MARKERS: { label: string; pct: number }[] = [
  { label: 'now', pct: 0 },
  { label: 'in 2h', pct: (2 / 48) * 100 },
  { label: 'in 16h', pct: (16 / 48) * 100 },
  { label: 'tomorrow', pct: 50 },
  { label: '48h', pct: 100 },
];

export interface OverviewTimelineProps {
  scheduled: OverviewTimelineEntry[];
  scheduledLater: number;
  unscheduled: MirroredPlaylistRow[];
  now: number;
  onOpenPlaylist: (playlistId: number) => void;
  onScheduleDrop: (row: MirroredPlaylistRow, anchor: { top: number; left: number }) => void;
}

export function OverviewTimeline({
  scheduled,
  scheduledLater,
  unscheduled,
  now,
  onOpenPlaylist,
  onScheduleDrop,
}: OverviewTimelineProps) {
  const [dragOver, setDragOver] = useState(false);

  const placed = useMemo(() => {
    const lanes: number[][] = [[], []];
    return scheduled.map((entry) => {
      const raw = ((entry.nextRunMs - now) / WINDOW_MS) * 100;
      const pct = Math.min(97, Math.max(3, raw));
      let lane = 0;
      if (lanes[0].some((p) => Math.abs(p - pct) < LANE_GAP_PCT)) lane = 1;
      lanes[lane].push(pct);
      return { entry, pct, lane };
    });
  }, [scheduled, now]);

  const dropRow = (id: string, clientX: number, clientY: number) => {
    const row = unscheduled.find((r) => String(r.id) === id);
    if (row) onScheduleDrop(row, { top: clientY, left: clientX });
  };

  return (
    <section className="ov-section" aria-label="Up next">
      <h2 className="ov-h">Up Next</h2>

      <div
        className={`ov-tl-track${dragOver ? ' ov-tl-track--over' : ''}`}
        onDragOver={(e) => {
          if (!e.dataTransfer.types.includes('text/plain')) return;
          e.preventDefault();
          e.dataTransfer.dropEffect = 'copy';
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          const id = e.dataTransfer.getData('text/plain');
          if (id) dropRow(id, e.clientX, e.clientY);
        }}
      >
        <div className="ov-tl-line" aria-hidden="true" />
        {MARKERS.map((m) => (
          <div
            key={m.label}
            className="ov-tl-marker"
            style={{ left: `${m.pct}%` }}
            aria-hidden="true"
          >
            <span className="ov-tl-tick" />
            <span className="ov-tl-marker-label">{m.label}</span>
          </div>
        ))}
        {placed.map(({ entry, pct, lane }) => {
          const name = overviewDisplayName(entry.row);
          return (
            <div key={entry.row.id} className="ov-tl-node-wrap" style={{ left: `${pct}%` }}>
              <span className="ov-tl-dot" aria-hidden="true" />
              <button
                type="button"
                className={`ov-tl-node ov-tl-node--lane${lane}`}
                onClick={() => onOpenPlaylist(entry.row.id)}
                title={`${name} · ${entry.cadenceLabel}`}
              >
                <span className="ov-tl-art">
                  <PlaylistArt url={playlistArtUrl(entry.row)} glyph={name.charAt(0)} />
                </span>
                <span className="ov-tl-name">{name}</span>
                <span className="ov-tl-when">{overviewRelativeLabel(entry.nextRunMs, now)}</span>
              </button>
            </div>
          );
        })}
        {placed.some(({ pct }) => pct >= 80) || unscheduled.length === 0 ? null : (
          <div className="ov-tl-drop" aria-hidden="true">
            drop to schedule
          </div>
        )}
      </div>

      {scheduled.length === 0 && (
        <p className="ov-quiet">
          Nothing scheduled in the next 48 hours — drag a playlist onto the timeline.
        </p>
      )}
      {scheduledLater > 0 && (
        <p className="ov-quiet">+{scheduledLater} more scheduled beyond 48h</p>
      )}

      {unscheduled.length > 0 && (
        <div className="ov-tl-tray">
          <span className="ov-tl-tray-label">Not scheduled</span>
          <div className="ov-tl-chips">
            {unscheduled.map((row) => {
              const name = overviewDisplayName(row);
              return (
                <button
                  key={row.id}
                  type="button"
                  className="ov-tl-chip"
                  draggable
                  onDragStart={(e) => {
                    e.dataTransfer.setData('text/plain', String(row.id));
                    e.dataTransfer.effectAllowed = 'copy';
                  }}
                  onClick={(e) => {
                    // Keyboard path for scheduling: dragging is mouse-only,
                    // so Enter/Space opens the schedule menu at the chip.
                    const box = e.currentTarget.getBoundingClientRect();
                    onScheduleDrop(row, { top: box.bottom + 8, left: box.left });
                  }}
                  title={`Schedule ${name} — drag onto the timeline, or press Enter`}
                >
                  <span className="ov-tl-chip-art" aria-hidden="true">
                    <PlaylistArt url={playlistArtUrl(row)} glyph={name.charAt(0)} />
                  </span>
                  {name}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </section>
  );
}
