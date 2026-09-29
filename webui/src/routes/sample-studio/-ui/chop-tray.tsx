import { useMemo, useState } from 'react';

import { formatTime, slicesFromOnsets } from '../-sample-studio.helpers';
import styles from './sample-studio-page.module.css';

interface ChopTrayProps {
  onsets: number[];
  inPoint: number;
  outPoint: number;
  onAuditionSlice: (start: number, end: number) => void;
  onMergeSlices: (start: number, end: number) => void;
}

/**
 * Transient chop tray: the in/out region is split at detected onsets into
 * slices. Click a slice to select it (multi-select), audition individual
 * slices, or merge the selection back into the loop region.
 */
export function ChopTray({
  onsets,
  inPoint,
  outPoint,
  onAuditionSlice,
  onMergeSlices,
}: ChopTrayProps) {
  const [selected, setSelected] = useState<Set<number>>(new Set());

  const slices = useMemo(
    () => slicesFromOnsets(onsets, inPoint, outPoint),
    [onsets, inPoint, outPoint],
  );

  const toggle = (i: number) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i);
      else next.add(i);
      return next;
    });
  };

  const merge = () => {
    const idx = [...selected].sort((a, b) => a - b);
    if (idx.length === 0) return;
    const start = slices[idx[0]].start;
    const end = slices[idx[idx.length - 1]].end;
    onMergeSlices(start, end);
    setSelected(new Set());
  };

  if (slices.length === 0) return null;

  return (
    <div className={styles.chopTray}>
      <div className={styles.chopTrayHeader}>
        <span>
          Chops <span className={styles.resultCount}>{slices.length}</span>
        </span>
        <span className={styles.chopActions}>
          <button
            type="button"
            className={styles.transportBtn}
            disabled={selected.size === 0}
            onClick={merge}
            title="Set the loop region to the selected slices"
          >
            Merge {selected.size > 0 ? `(${selected.size})` : ''} → loop
          </button>
          <button
            type="button"
            className={styles.transportBtn}
            disabled={selected.size === 0}
            onClick={() => setSelected(new Set())}
          >
            Clear
          </button>
        </span>
      </div>
      <div className={styles.chopList}>
        {slices.map((s, i) => (
          <div
            key={i}
            role="button"
            tabIndex={0}
            className={styles.chopChip}
            data-selected={selected.has(i)}
            onClick={() => toggle(i)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                toggle(i);
              }
            }}
            title={`${formatTime(s.start)} → ${formatTime(s.end)} — click to select`}
          >
            <span className={styles.chopChipTime}>
              {formatTime(s.start)}–{formatTime(s.end)}
            </span>
            <button
              type="button"
              className={styles.chopAuditionBtn}
              onClick={(e) => {
                e.stopPropagation();
                onAuditionSlice(s.start, s.end);
              }}
              title={`Audition ${formatTime(s.start)} → ${formatTime(s.end)}`}
            >
              ▶
            </button>
          </div>
        ))}
      </div>
      <div className={styles.emptyHint}>
        Slices follow the detected transients. Audition with ▶, select several, then merge them into
        the loop.
      </div>
    </div>
  );
}
