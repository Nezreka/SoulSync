import { useCallback, useState } from 'react';

/**
 * How many cards fit across one shelf row.
 *
 * the All view shows one full row per kind, then "Show all". a fixed preview
 * of 12 left 9 albums with a hole on the right on a wide screen and wrapped to
 * a ragged second row on a narrow one, and "Show all" only appeared past 12.
 * the math mirrors the css grid: repeat(auto-fill, minmax(min, 1fr)) with gap.
 *
 * null until measured (and in jsdom, which has no layout or ResizeObserver),
 * so callers keep their fixed cap.
 */
export function useShelfColumns(minWidth: number, gap: number) {
  const [columns, setColumns] = useState<number | null>(null);

  const ref = useCallback(
    (node: HTMLElement | null) => {
      if (!node || typeof ResizeObserver === 'undefined') return;
      const measure = () => {
        const width = node.clientWidth;
        if (width > 0) setColumns(shelfColumns(width, minWidth, gap));
      };
      measure();
      const observer = new ResizeObserver(measure);
      observer.observe(node);
      return () => observer.disconnect();
    },
    [minWidth, gap],
  );

  return [ref, columns] as const;
}

export function shelfColumns(width: number, minWidth: number, gap: number): number {
  return Math.max(1, Math.floor((width + gap) / (minWidth + gap)));
}
