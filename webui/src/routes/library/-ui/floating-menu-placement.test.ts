import { describe, expect, it } from 'vitest';

import { SUBMENU_ROOM, floatingMenuPlacement } from './floating-menu-placement';

// the album menu on a phone (#2183): the button near the left, the menu wider
// than the room left of the button's right edge and taller than either side
const phone = { width: 390, height: 844 };
const phoneButton = { top: 450, bottom: 482, right: 150 };
const albumMenu = { width: 220, height: 640 };

describe('floatingMenuPlacement', () => {
  it('keeps the album menu on a phone screen (#2183)', () => {
    const p = floatingMenuPlacement(phoneButton, albumMenu, phone);
    expect(p.left).toBeGreaterThanOrEqual(12);
    expect(p.left + albumMenu.width).toBeLessThanOrEqual(phone.width - 12);
    // under the button, scrolling inside what is left of the screen
    expect(p.top).toBe(490);
    expect(p.maxHeight).toBe(phone.height - 12 - 490);
    expect(p.inlineSubmenu).toBe(true);
  });

  it('lines right edges up on a desktop, where it fits', () => {
    const p = floatingMenuPlacement(
      { top: 254, bottom: 286, right: 602 },
      { width: 220, height: 480 },
      { width: 1400, height: 900 },
    );
    expect(p).toEqual({ top: 294, left: 382, inlineSubmenu: false });
  });

  it('goes above the button when only above has room', () => {
    const p = floatingMenuPlacement(
      { top: 700, bottom: 732, right: 900 },
      { width: 220, height: 400 },
      { width: 1400, height: 900 },
    );
    expect(p.top).toBe(700 - 8 - 400);
    expect(p.maxHeight).toBeUndefined();
  });

  it('never runs off the right edge either', () => {
    const p = floatingMenuPlacement(
      { top: 10, bottom: 40, right: 2000 },
      { width: 220, height: 100 },
      { width: 390, height: 844 },
    );
    expect(p.left).toBe(390 - 12 - 220);
  });

  it('caps a menu wider than the screen', () => {
    const p = floatingMenuPlacement(
      phoneButton,
      { width: 500, height: 100 },
      { width: 320, height: 600 },
    );
    expect(p.left).toBe(12);
    expect(p.maxWidth).toBe(296);
  });

  it('takes the whole screen when neither side has room', () => {
    const p = floatingMenuPlacement(
      { top: 200, bottom: 230, right: 300 },
      { width: 220, height: 600 },
      { width: 390, height: 420 },
    );
    expect(p.top).toBe(12);
    expect(p.maxHeight).toBe(420 - 24);
    expect(p.inlineSubmenu).toBe(true);
  });

  it('lets the submenu fly out when there is room on the left', () => {
    const p = floatingMenuPlacement(
      { top: 100, bottom: 130, right: SUBMENU_ROOM + 300 },
      { width: 220, height: 300 },
      { width: 1400, height: 900 },
    );
    expect(p.inlineSubmenu).toBe(false);
  });
});
