/**
 * The Overview's "Up Next" timeline: the next 48 hours of scheduled playlist
 * syncs on one glowing axis, plus the unscheduled tray below it.
 *
 * Entries whose 128px nodes would visually overlap merge into a cluster node;
 * the merge is measured against the track's actual pixel width, so clusters
 * can never collide at any density on any viewport. Clicking a cluster
 * expands its member playlists inline. (The old code only checked lane 0
 * against a fixed 9% gap, so a third node near two others piled into lane 1
 * on top of the second — the pile-up from the wild.)
 *
 * The tray chips are HTML5-draggable; dropping one anywhere on the timeline
 * track calls `onScheduleDrop` with the row and the drop point, and the parent
 * opens the existing ScheduleMenu there. The timeline itself never writes —
 * scheduling goes through the parent's `useCardSchedules` controller.
 */

import { useEffect, useMemo, useRef, useState } from 'react';

import type { MirroredPlaylistRow } from '../../-sync.mirrored';
import type { OverviewTimelineEntry } from './use-overview-model';

import { PlaylistArt, playlistArtUrl } from '../playlist-art';

const WINDOW_MS = 48 * 3600 * 1000;
/** .ov-tl-node width — the cluster math keeps these from ever overlapping. */
const NODE_PX = 128;
/** Breathing room between neighboring cluster nodes, in % of the axis. */
const CLUSTER_PAD_PCT = 1;

interface TimelineCluster {
  entries: OverviewTimelineEntry[];
  /** center of the merged span, in % of the axis */
  pct: number;
}

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

/**
 * The track's pixel width, so node overlap is measured, not guessed.
 * Falls back to a desktop-ish width before mount and where
 * ResizeObserver is unavailable (jsdom).
 */
function useTrackWidth() {
  const ref = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(960);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const update = () => {
      const w = el.getBoundingClientRect().width;
      if (w > 0) setWidth(w);
    };
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return { trackRef: ref, trackWidth: width };
}

/**
 * Greedy-merge entries whose node intervals overlap into clusters.
 *
 * The merged spans are disjoint with breathing room, and every rendered node
 * sits inside its span (span width is always >= node width) — so cluster
 * nodes can never overlap, at any density, on any viewport. No lanes needed.
 */
export function clusterTimeline(
  scheduled: OverviewTimelineEntry[],
  now: number,
  trackWidth: number,
): TimelineCluster[] {
  const halfPct = ((NODE_PX / Math.max(1, trackWidth)) * 100) / 2;
  const pts = scheduled
    .map((entry) => {
      const raw = ((entry.nextRunMs - now) / WINDOW_MS) * 100;
      return { entry, pct: Math.min(97, Math.max(3, raw)) };
    })
    .sort((a, b) => a.entry.nextRunMs - b.entry.nextRunMs);

  const spans: { entries: OverviewTimelineEntry[]; start: number; end: number }[] = [];
  for (const p of pts) {
    const s = p.pct - halfPct;
    const e = p.pct + halfPct;
    const cur = spans[spans.length - 1];
    if (cur && s <= cur.end + CLUSTER_PAD_PCT) {
      cur.entries.push(p.entry);
      cur.end = Math.max(cur.end, e);
    } else {
      spans.push({ entries: [p.entry], start: s, end: e });
    }
  }
  return spans.map((sp) => ({
    entries: sp.entries,
    pct: (sp.start + sp.end) / 2,
  }));
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
  /** first entry's row id of the expanded cluster, if any */
  const [openClusterId, setOpenClusterId] = useState<number | null>(null);
  const sectionRef = useRef<HTMLElement | null>(null);
  const { trackRef, trackWidth } = useTrackWidth();

  const clusters = useMemo(
    () => clusterTimeline(scheduled, now, trackWidth),
    [scheduled, now, trackWidth],
  );

  // Dismiss the expanded cluster: outside click or Escape.
  useEffect(() => {
    if (openClusterId === null) return;
    const onDown = (e: PointerEvent) => {
      if (sectionRef.current && !sectionRef.current.contains(e.target as Node)) {
        setOpenClusterId(null);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpenClusterId(null);
    };
    document.addEventListener('pointerdown', onDown);
    window.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onDown);
      window.removeEventListener('keydown', onKey);
    };
  }, [openClusterId]);

  const openCluster =
    openClusterId === null
      ? undefined
      : clusters.find((c) => c.entries[0].row.id === openClusterId);

  const dropRow = (id: string, clientX: number, clientY: number) => {
    const row = unscheduled.find((r) => String(r.id) === id);
    if (row) onScheduleDrop(row, { top: clientY, left: clientX });
  };

  return (
    <section className="ov-section" aria-label="Up next" ref={sectionRef}>
      <h2 className="ov-h">Up Next</h2>

      <div
        ref={trackRef}
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
        {clusters.map((c) => {
          const first = c.entries[0];
          const name = overviewDisplayName(first.row);
          const isOpen = openClusterId === first.row.id;
          if (c.entries.length === 1) {
            return (
              <div key={first.row.id} className="ov-tl-node-wrap" style={{ left: `${c.pct}%` }}>
                <span className="ov-tl-dot" aria-hidden="true" />
                <button
                  type="button"
                  className="ov-tl-node"
                  onClick={() => onOpenPlaylist(first.row.id)}
                  title={`${name} · ${first.cadenceLabel}`}
                >
                  <span className="ov-tl-art">
                    <PlaylistArt url={playlistArtUrl(first.row)} glyph={name.charAt(0)} />
                  </span>
                  <span className="ov-tl-name">{name}</span>
                  <span className="ov-tl-when">{overviewRelativeLabel(first.nextRunMs, now)}</span>
                </button>
              </div>
            );
          }
          const memberNames = c.entries.map((e) => overviewDisplayName(e.row)).join(', ');
          return (
            <div key={first.row.id} className="ov-tl-node-wrap" style={{ left: `${c.pct}%` }}>
              <span className="ov-tl-dot" aria-hidden="true" />
              <button
                type="button"
                className="ov-tl-node ov-tl-cluster"
                aria-expanded={isOpen}
                onClick={() => setOpenClusterId(isOpen ? null : first.row.id)}
                title={`${c.entries.length} playlists: ${memberNames}`}
              >
                <span className="ov-tl-cluster-art" aria-hidden="true">
                  {c.entries.slice(0, 3).map((e, i) => (
                    <span key={e.row.id} className="ov-tl-cluster-tile" style={{ zIndex: i + 1 }}>
                      <PlaylistArt
                        url={playlistArtUrl(e.row)}
                        glyph={overviewDisplayName(e.row).charAt(0)}
                      />
                    </span>
                  ))}
                  <span className="ov-tl-cluster-count">{c.entries.length}</span>
                </span>
                <span className="ov-tl-name">
                  {name} +{c.entries.length - 1}
                </span>
                <span className="ov-tl-when">{overviewRelativeLabel(first.nextRunMs, now)}</span>
              </button>
            </div>
          );
        })}
        {openCluster && (
          <div
            className="ov-tl-cluster-card"
            role="dialog"
            aria-label={`${openCluster.entries.length} playlists syncing around ${overviewRelativeLabel(openCluster.entries[0].nextRunMs, now)}`}
            style={
              openCluster.pct > 72
                ? { right: `${100 - openCluster.pct}%` }
                : { left: `${openCluster.pct}%`, transform: 'translateX(-50%)' }
            }
          >
            <div className="ov-tl-cluster-card-head" aria-hidden="true">
              {openCluster.entries.length} playlists
            </div>
            {openCluster.entries.map((e) => {
              const n = overviewDisplayName(e.row);
              return (
                <button
                  key={e.row.id}
                  type="button"
                  className="ov-tl-cluster-member"
                  onClick={() => {
                    setOpenClusterId(null);
                    onOpenPlaylist(e.row.id);
                  }}
                  title={`Open ${n}`}
                >
                  <span className="ov-tl-cluster-member-art" aria-hidden="true">
                    <PlaylistArt url={playlistArtUrl(e.row)} glyph={n.charAt(0)} />
                  </span>
                  <span className="ov-tl-cluster-member-name">{n}</span>
                  <span className="ov-tl-cluster-member-when">
                    {overviewRelativeLabel(e.nextRunMs, now)}
                  </span>
                </button>
              );
            })}
          </div>
        )}
        {clusters.some(({ pct }) => pct >= 80) || unscheduled.length === 0 ? null : (
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
