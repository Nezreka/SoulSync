/**
 * Where the step card goes: beside its target, never over it when there is
 * room, always inside the window. On a phone it docks to the bottom (or the
 * top, when the target sits low).
 */

export interface Box {
  top: number;
  left: number;
  width: number;
  height: number;
}

export interface Size {
  width: number;
  height: number;
}

export type Side = 'below' | 'above' | 'right' | 'left' | 'center' | 'dock-bottom' | 'dock-top';

export interface Placement {
  top: number;
  left: number;
  side: Side;
}

export const GAP = 14;
export const MARGIN = 16;
/** the ring sits this far outside the target */
export const RING_PAD = 6;

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, Math.max(lo, v)));

export function placeCard(target: Box | null, card: Size, view: Size, phone: boolean): Placement {
  if (phone) {
    const low = !!target && target.top + target.height / 2 > view.height * 0.55;
    return low
      ? { top: MARGIN, left: MARGIN, side: 'dock-top' }
      : { top: view.height - card.height - MARGIN, left: MARGIN, side: 'dock-bottom' };
  }
  if (!target) {
    return {
      top: Math.max(MARGIN, view.height - card.height - 32),
      left: Math.max(MARGIN, (view.width - card.width) / 2),
      side: 'center',
    };
  }
  const bottom = target.top + target.height;
  const right = target.left + target.width;
  const xCentered = clamp(
    target.left + target.width / 2 - card.width / 2,
    MARGIN,
    view.width - card.width - MARGIN,
  );
  const yCentered = clamp(
    target.top + target.height / 2 - card.height / 2,
    MARGIN,
    view.height - card.height - MARGIN,
  );
  const fits = {
    below: view.height - bottom - GAP - MARGIN >= card.height,
    above: target.top - GAP - MARGIN >= card.height,
    right: view.width - right - GAP - MARGIN >= card.width,
    left: target.left - GAP - MARGIN >= card.width,
  };
  // a tall target (the sidebar) reads better with the card beside it
  const tall = target.height > view.height * 0.45;
  const order: (keyof typeof fits)[] = tall
    ? ['right', 'left', 'below', 'above']
    : ['below', 'above', 'right', 'left'];
  for (const side of order) {
    if (!fits[side]) continue;
    if (side === 'below') return { top: bottom + GAP, left: xCentered, side };
    if (side === 'above') return { top: target.top - GAP - card.height, left: xCentered, side };
    if (side === 'right') return { top: yCentered, left: right + GAP, side };
    return { top: yCentered, left: target.left - GAP - card.width, side };
  }
  // no room anywhere (a big window): over it, low and centred
  return {
    top: Math.max(MARGIN, view.height - card.height - 32),
    left: Math.max(MARGIN, (view.width - card.width) / 2),
    side: 'center',
  };
}

/** the hole in the scrim: the target plus a little air, kept inside the window */
export function holeFor(target: Box, view: Size): Box {
  const top = Math.max(0, target.top - RING_PAD);
  const left = Math.max(0, target.left - RING_PAD);
  const bottom = Math.min(view.height, target.top + target.height + RING_PAD);
  const right = Math.min(view.width, target.left + target.width + RING_PAD);
  return { top, left, width: Math.max(0, right - left), height: Math.max(0, bottom - top) };
}
