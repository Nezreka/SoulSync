/**
 * the sidebar's places in a profile's own order (player settings
 * sidebar_order / sidebar_hidden). a place is one of the player's pages
 * ("/player/movies") or a library with a page of its own ("lib:5").
 *
 * places stay in their section (Watch, Listen): the order is the order
 * within each. a place the saved order doesn't name keeps its usual spot
 * after the ones it does (a library added later shows up without being
 * arranged), and a saved place the player no longer has is skipped.
 */

export const HOME_ID = '/player';

export function arrangeNav<T extends { id: string }>(
  items: readonly T[],
  order: readonly string[] | undefined,
  hidden: readonly string[] | undefined,
): T[] {
  const gone = new Set((hidden ?? []).filter((id) => id !== HOME_ID));
  const rank = new Map((order ?? []).map((id, i) => [id, i]));
  return items
    .filter((item) => !gone.has(item.id))
    .map((item, i) => ({ item, i, r: rank.get(item.id) }))
    .sort((a, b) =>
      a.r !== undefined && b.r !== undefined
        ? a.r - b.r
        : a.r !== undefined
          ? -1
          : b.r !== undefined
            ? 1
            : a.i - b.i,
    )
    .map((x) => x.item);
}

/** the ids in order with one moved up (-1) or down (+1); out of range changes nothing */
export function moveNav(ids: readonly string[], id: string, step: -1 | 1): string[] {
  const from = ids.indexOf(id);
  const to = from + step;
  if (from < 0 || to < 0 || to >= ids.length) return ids.slice();
  const out = ids.slice();
  [out[from], out[to]] = [out[to], out[from]];
  return out;
}
