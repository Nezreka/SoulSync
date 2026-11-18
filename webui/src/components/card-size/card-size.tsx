/**
 * #2108: a card size slider for the library grids (music artists and albums,
 * video posters). Each grid keeps its own size in this browser; untouched, a
 * grid keeps its own responsive columns. The grid never gets wider than its
 * box: a card is at most the row's width, so there is no sideways scroll.
 */

import { useCallback, useState, type CSSProperties } from 'react';

import css from './card-size.module.css';

export interface CardSizeRange {
  min: number;
  max: number;
  /** what the grid's own css picks on a desktop, where the slider starts */
  base: number;
}

function read(key: string, range: CardSizeRange): number | null {
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return null;
    const value = Number(raw);
    if (!Number.isFinite(value)) return null;
    return Math.min(range.max, Math.max(range.min, Math.round(value)));
  } catch {
    return null;
  }
}

function write(key: string, value: number | null) {
  try {
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, String(value));
  } catch {
    /* private mode: the size just isn't kept */
  }
}

/** the chosen width in px, or null for the grid's own */
export function useCardSize(
  key: string,
  range: CardSizeRange,
): [number | null, (value: number | null) => void] {
  const [size, setSize] = useState<number | null>(() => read(key, range));
  const set = useCallback(
    (value: number | null) => {
      const next =
        value === null || value === range.base
          ? null
          : Math.min(range.max, Math.max(range.min, Math.round(value)));
      write(key, next);
      setSize(next);
    },
    [key, range.base, range.max, range.min],
  );
  return [size, set];
}

/** the grid's columns for a chosen size; nothing when it is the grid's own */
export function cardGridStyle(size: number | null): CSSProperties | undefined {
  if (size === null) return undefined;
  return { gridTemplateColumns: `repeat(auto-fill, minmax(min(${size}px, 100%), 1fr))` };
}

export function CardSizeSlider({
  label,
  size,
  range,
  onChange,
}: {
  /** what the cards are, for the screen reader: "Artist card size" */
  label: string;
  size: number | null;
  range: CardSizeRange;
  onChange: (value: number | null) => void;
}) {
  const value = size ?? range.base;
  return (
    <div className={css.wrap} title="Card size (double-click to reset)">
      <svg viewBox="0 0 24 24" width="13" height="13" aria-hidden="true" className={css.icon}>
        <rect x="4" y="4" width="7" height="7" rx="1.5" />
        <rect x="13" y="4" width="7" height="7" rx="1.5" />
        <rect x="4" y="13" width="7" height="7" rx="1.5" />
        <rect x="13" y="13" width="7" height="7" rx="1.5" />
      </svg>
      <input
        type="range"
        className={css.slider}
        aria-label={label}
        min={range.min}
        max={range.max}
        step={4}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        onDoubleClick={() => onChange(null)}
      />
      <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" className={css.icon}>
        <rect x="4" y="4" width="16" height="16" rx="2.5" />
      </svg>
      {size !== null ? (
        <button
          type="button"
          className={css.reset}
          aria-label={`Reset ${label.toLowerCase()}`}
          title="Back to the default size"
          onClick={() => onChange(null)}
        >
          Reset
        </button>
      ) : null}
    </div>
  );
}
