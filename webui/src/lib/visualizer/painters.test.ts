import type { VizFrame } from './frame';

import { createFrame } from './frame';
import { paintMini } from './mini';
import { PAINTERS, TRAILS } from './painters';
import { createStore, withStore } from './state';
import { stubCtx } from './test-ctx';
import { THEME_IDS } from './themes';

function frame(over: Partial<VizFrame> = {}): VizFrame {
  return { ...createFrame(), ...over };
}

function idleFrame(t: number): VizFrame {
  return frame({
    freq: Uint8Array.from({ length: 64 }, (_, i) => 38 + ((i * 7) % 52)),
    wave: Uint8Array.from({ length: 64 }, (_, i) => 128 + Math.round(26 * Math.sin(i / 5))),
    energy: 0.22,
    idle: true,
    t,
    dt: 1 / 60,
  });
}

function loudFrame(t: number, hit: boolean): VizFrame {
  return frame({
    freq: Uint8Array.from({ length: 64 }, (_, i) => (i < 10 ? 240 : 120 + ((i * 37) % 120))),
    wave: Uint8Array.from({ length: 64 }, (_, i) => 128 + Math.round(110 * Math.sin(i / 3))),
    waveFull: Uint8Array.from({ length: 2048 }, (_, i) => 128 + Math.round(110 * Math.sin(i / 20))),
    energy: 0.95,
    beat: hit ? 1 : 0.4,
    onset: hit,
    idle: false,
    t,
    dt: 1 / 60,
    pal: { r: 236.4, g: 72.2, b: 120.9 },
    pal2: { r: 70, g: 150, b: 240 },
    title: 'numb',
  });
}

describe('painters', () => {
  beforeEach(() => {
    // soft() wants an offscreen canvas; hand it a stub context
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(
      () => stubCtx().ctx as unknown as ReturnType<HTMLCanvasElement['getContext']>,
    );
  });

  it('has a painter for every theme', () => {
    for (const id of THEME_IDS) expect(typeof PAINTERS[id], id).toBe('function');
    expect(Object.keys(PAINTERS).sort()).toEqual([...THEME_IDS].sort());
  });

  it('trails only name real themes', () => {
    for (const id of Object.keys(TRAILS)) expect(THEME_IDS).toContain(id);
  });

  for (const id of THEME_IDS) {
    it(`${id} paints idle and loud frames with no NaN`, () => {
      const store = createStore();
      const { ctx, bad } = stubCtx();
      withStore(store, () => {
        for (let i = 0; i < 20; i++) PAINTERS[id](ctx, 1280, 800, idleFrame(i / 60));
        for (let i = 0; i < 40; i++)
          PAINTERS[id](ctx, 1280, 800, loudFrame(1 + i / 60, i % 8 === 0));
        // a slow frame and a zero-dt frame still behave
        PAINTERS[id](ctx, 1280, 800, { ...loudFrame(3, true), dt: 0.1 });
        PAINTERS[id](ctx, 640, 360, { ...loudFrame(3.1, false), dt: 0 });
      });
      expect(bad, id).toEqual([]);
    });
  }

  it('vinyl draws the cover when there is art, the initial when not', () => {
    const img = document.createElement('img');
    const withArt = stubCtx();
    withStore(createStore(), () =>
      PAINTERS.vinyl(withArt.ctx, 1280, 800, { ...loudFrame(1, false), art: img }),
    );
    expect(withArt.calls.some((c) => c.name === 'drawImage' && c.args[0] === img)).toBe(true);
    const noArt = stubCtx();
    withStore(createStore(), () => PAINTERS.vinyl(noArt.ctx, 1280, 800, loudFrame(1, false)));
    expect(noArt.calls.some((c) => c.name === 'fillText' && c.args[0] === 'N')).toBe(true);
  });

  it('keeps theme state per store', () => {
    const a = createStore();
    const b = createStore();
    const { ctx } = stubCtx();
    withStore(a, () => PAINTERS.bloom(ctx, 800, 600, loudFrame(1, true)));
    expect(a.themes.has('bloom')).toBe(true);
    expect(b.themes.has('bloom')).toBe(false);
  });

  it('mini glow paints without NaN', () => {
    const { ctx, bad } = stubCtx();
    paintMini(
      ctx,
      340,
      70,
      loudFrame(1, true),
      { r: 236, g: 72, b: 120 },
      { r: 70, g: 150, b: 240 },
    );
    paintMini(ctx, 340, 70, idleFrame(0), { r: 236, g: 72, b: 120 }, { r: 70, g: 150, b: 240 });
    expect(bad).toEqual([]);
  });
});
