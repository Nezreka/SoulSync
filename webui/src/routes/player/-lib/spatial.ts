/**
 * remote-style focus: arrow keys move focus to the nearest focusable thing in
 * that direction, the way a TV remote does. pure geometry, so it is tested
 * apart from the dom.
 *
 * a candidate must sit in the direction pressed (its near edge past the
 * current element's far edge, with a little slack). among those, distance
 * along the axis counts, and distance across it counts three times as much,
 * so down from a card in a rail goes to the card below it, not the one that
 * happens to be closest diagonally.
 */

export type Direction = 'up' | 'down' | 'left' | 'right';

export interface Box {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

const SLACK = 4;
const CROSS_WEIGHT = 3;

function centre(box: Box) {
  return { x: (box.left + box.right) / 2, y: (box.top + box.bottom) / 2 };
}

export function inDirection(from: Box, to: Box, dir: Direction): boolean {
  switch (dir) {
    case 'right':
      return to.left >= from.right - SLACK && centre(to).x > centre(from).x;
    case 'left':
      return to.right <= from.left + SLACK && centre(to).x < centre(from).x;
    case 'down':
      return to.top >= from.bottom - SLACK && centre(to).y > centre(from).y;
    case 'up':
      return to.bottom <= from.top + SLACK && centre(to).y < centre(from).y;
  }
}

/** how far it is to go from one box to another in that direction */
export function score(from: Box, to: Box, dir: Direction): number {
  const horizontal = dir === 'left' || dir === 'right';
  // gap along the axis, edge to edge
  const along = horizontal
    ? dir === 'right'
      ? to.left - from.right
      : from.left - to.right
    : dir === 'down'
      ? to.top - from.bottom
      : from.top - to.bottom;
  // overlap across the axis is free; the rest costs extra
  const across = horizontal
    ? Math.max(0, Math.max(from.top, to.top) - Math.min(from.bottom, to.bottom))
    : Math.max(0, Math.max(from.left, to.left) - Math.min(from.right, to.right));
  return Math.max(0, along) + across * CROSS_WEIGHT;
}

/** do the two boxes share any of the cross axis (same row for left/right)? */
export function inLine(from: Box, to: Box, dir: Direction): boolean {
  return dir === 'left' || dir === 'right'
    ? Math.min(from.bottom, to.bottom) - Math.max(from.top, to.top) > SLACK
    : Math.min(from.right, to.right) - Math.max(from.left, to.left) > SLACK;
}

/**
 * the index of the best candidate, or -1 when nothing lies that way. things
 * in line (the same row going left/right, the same column going up/down)
 * win first; only when nothing is in line does the nearest anything count.
 */
export function pickNext(from: Box, candidates: readonly Box[], dir: Direction): number {
  let best = -1;
  let bestScore = Infinity;
  let bestInLine = false;
  candidates.forEach((box, index) => {
    if (box === from) return;
    if (!inDirection(from, box, dir)) return;
    const lined = inLine(from, box, dir);
    if (bestInLine && !lined) return;
    const s = score(from, box, dir);
    if ((lined && !bestInLine) || s < bestScore) {
      bestScore = s;
      best = index;
      bestInLine = lined;
    }
  });
  return best;
}

export function directionForKey(key: string): Direction | null {
  switch (key) {
    case 'ArrowUp':
      return 'up';
    case 'ArrowDown':
      return 'down';
    case 'ArrowLeft':
      return 'left';
    case 'ArrowRight':
      return 'right';
    default:
      return null;
  }
}
