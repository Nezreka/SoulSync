import { useMemo, useState } from 'react';

import type { SamplePeaks } from '../-sample-studio.types';

import { formatTime, slicesFromOnsets, suggestChops } from '../-sample-studio.helpers';
import styles from './sample-studio-page.module.css';

interface ChopTrayProps {
  onsets: number[];
  inPoint: number;
  outPoint: number;
  bpm: number | null;
  durationS: number;
  peaks: SamplePeaks | undefined;
  /** True once the analysis row is done (or errored) — gates suggestions. */
  analysisReady: boolean;
  onAuditionSlice: (start: number, end: number) => void;
  onMergeSlices: (start: number, end: number) => void;
  /** A suggested chop was picked: make it the loop region. */
  onUseSuggestion: (start: number, end: number) => void;
}

/**
 * Chop tray, redesigned around a guided default: the most promising regions
 * ("Suggested chops", scored by loudness × transient density on the bar
 * grid) are shown first. The full transient-slice firehose is one toggle
 * away for power users — same selection/merge mechanics as before.
 */
export function ChopTray({
  onsets,
  inPoint,
  outPoint,
  bpm,
  durationS,
  peaks,
  analysisReady,
  onAuditionSlice,
  onMergeSlices,
  onUseSuggestion,
}: ChopTrayProps) {
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [showAll, setShowAll] = useState(false);

  const suggestions = useMemo(
    () => (analysisReady ? suggestChops({ bpm, durationS, peaks, onsets, count: 8 }) : []),
    [analysisReady, bpm, durationS, peaks, onsets],
  );

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

  return (
    <div className={styles.chopTray}>
      <div className={styles.chopTrayHeader}>
        <span>
          Suggested chops{' '}
          {suggestions.length > 0 && (
            <span className={styles.resultCount}>{suggestions.length}</span>
          )}
        </span>
      </div>
      {!analysisReady ? (
        <div className={styles.emptyHint}>
          Suggestions land with the analysis — meanwhile the waveform above is ready to chop by ear.
        </div>
      ) : suggestions.length === 0 ? (
        <div className={styles.emptyHint}>
          No strong candidates in this track — set the region by ear, or open all transient slices
          below.
        </div>
      ) : (
        <>
          <div className={styles.chopList}>
            {suggestions.map((s, i) => (
              <div
                key={i}
                role="button"
                tabIndex={0}
                className={styles.chopChip}
                data-kind="suggested"
                onClick={() => onUseSuggestion(s.start, s.end)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    onUseSuggestion(s.start, s.end);
                  }
                }}
                title={`${s.label} (${formatTime(s.start)} → ${formatTime(s.end)}) — click to make it the loop region`}
              >
                <span className={styles.chopChipTime}>{s.label}</span>
                <button
                  type="button"
                  className={styles.chopAuditionBtn}
                  onClick={(e) => {
                    e.stopPropagation();
                    onAuditionSlice(s.start, s.end);
                  }}
                  title={`Audition ${s.label}`}
                >
                  ▶
                </button>
              </div>
            ))}
          </div>
          <div className={styles.emptyHint}>
            Click a suggestion to make it the loop region, or audition it with ▶ first.
          </div>
        </>
      )}

      <button
        type="button"
        className={styles.disclosureToggle}
        onClick={() => setShowAll((v) => !v)}
        aria-expanded={showAll}
      >
        {showAll ? '▾' : '▸'} All transient slices ({slices.length})
      </button>
      {showAll && (
        <>
          <div className={styles.chopTrayHeader}>
            <span>
              Transient slices <span className={styles.resultCount}>{slices.length}</span>
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
          {slices.length === 0 ? (
            <div className={styles.emptyHint}>No slices in the current region.</div>
          ) : (
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
          )}
          <div className={styles.emptyHint}>
            Slices follow the detected transients. Audition with ▶, select several, then merge them
            into the loop.
          </div>
        </>
      )}
    </div>
  );
}
