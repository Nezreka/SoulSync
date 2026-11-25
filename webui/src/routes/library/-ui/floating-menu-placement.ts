/** where a floating menu sits next to its button, kept on the screen.
 *
 *  under the button with right edges lined up, above it when there is no room
 *  below. it never leaves the screen: on a phone the album menu used to line
 *  its right edge up with a button near the left and slide off the left edge,
 *  its bottom items below the fold (#2183). now the left edge is clamped, and
 *  a menu taller than the room it gets scrolls inside a capped height. */

export interface FloatingMenuPlacement {
  top: number;
  left: number;
  /** set when the menu is taller than its room: it scrolls inside this */
  maxHeight?: number;
  /** set when the menu is wider than the screen */
  maxWidth?: number;
  /** too little room on the left for a submenu to fly out: open it in place */
  inlineSubmenu: boolean;
}

/** a submenu (Enrich) flies out to the left; it needs about this much room */
export const SUBMENU_ROOM = 240;

export function floatingMenuPlacement(
  anchor: { top: number; bottom: number; right: number },
  menu: { width: number; height: number },
  viewport: { width: number; height: number },
  { gap = 8, margin = 12 }: { gap?: number; margin?: number } = {},
): FloatingMenuPlacement {
  const out: FloatingMenuPlacement = { top: 0, left: 0, inlineSubmenu: false };

  const roomX = viewport.width - margin * 2;
  const width = Math.min(menu.width, roomX);
  if (menu.width > roomX) out.maxWidth = roomX;
  const lined = anchor.right - width;
  out.left = Math.max(margin, Math.min(lined, viewport.width - margin - width));

  const below = anchor.bottom + gap;
  const roomBelow = viewport.height - margin - below;
  const roomAbove = anchor.top - gap - margin;
  if (menu.height <= roomBelow) {
    out.top = below;
  } else if (menu.height <= roomAbove) {
    out.top = anchor.top - gap - menu.height;
  } else if (roomBelow >= SUBMENU_ROOM) {
    // neither side holds it all: below, scrolling. above would put it at the
    // top of a phone, under the floating menu button
    out.top = below;
    out.maxHeight = roomBelow;
  } else if (roomAbove >= SUBMENU_ROOM) {
    out.top = margin;
    out.maxHeight = roomAbove;
  } else {
    // a sliver either side: the whole screen, over the button
    out.top = margin;
    out.maxHeight = viewport.height - margin * 2;
  }

  // a scrolling menu clips anything that flies out of it, and a menu hard by
  // the left edge has nowhere to fly a submenu to
  out.inlineSubmenu = out.maxHeight !== undefined || out.left < SUBMENU_ROOM;
  return out;
}
