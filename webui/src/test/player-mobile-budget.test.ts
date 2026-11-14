import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { extractFunction } from './vanilla-extract';

/**
 * What the media player and the sidebar may spend on a phone.
 *
 * Three things ran for nobody: the sidebar visualizer's loop (phones hide it),
 * the Now Playing overlay's blur layer while it was closed, and the sidebar's
 * decorations while the drawer was off screen. And two ran harder than they
 * needed to: the visualizers at every frame, and the album-art bars by
 * resizing, which laid the player out sixty times a second.
 */

const JS = readFileSync(resolve(process.cwd(), 'static/media-player.js'), 'utf8');
const CSS = readFileSync(resolve(process.cwd(), 'static/style.css'), 'utf8');
const MOBILE = readFileSync(resolve(process.cwd(), 'static/mobile.css'), 'utf8');

function build(matches: (query: string) => boolean, classes: string[] = [], cores = 12) {
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  return new Function(
    'window',
    'document',
    'navigator',
    'performance',
    `${extractFunction('npLiteMotion', JS)}
     const NP_LITE_FRAME_MS = 32;
     const _npVizLast = { np: 0, sidebar: 0 };
     ${extractFunction('npVizFrameDue', JS)}
     ${extractFunction('sidebarVizAllowed', JS)}
     return { npLiteMotion, npVizFrameDue, sidebarVizAllowed };`,
  )(
    { matchMedia: (q: string) => ({ matches: matches(q) }), performance: { now: () => clock.now } },
    { body: { classList: { contains: (c: string) => classes.includes(c) } } },
    { hardwareConcurrency: cores, deviceMemory: 8 },
    { now: () => clock.now },
  ) as {
    npLiteMotion: () => boolean;
    npVizFrameDue: (which: string) => boolean;
    sidebarVizAllowed: () => boolean;
  };
}

const clock = { now: 1000 };

describe('the visualizers’ frame budget', () => {
  it('a desktop draws every frame', () => {
    const api = build(() => false);
    expect(api.npLiteMotion()).toBe(false);
    clock.now = 1000;
    expect(api.npVizFrameDue('np')).toBe(true);
    clock.now = 1001;
    expect(api.npVizFrameDue('np')).toBe(true);
  });

  it('a phone draws every other frame, each visualizer on its own clock', () => {
    const api = build((q) => q === '(max-width: 768px)');
    expect(api.npLiteMotion()).toBe(true);
    clock.now = 5000;
    expect(api.npVizFrameDue('np')).toBe(true);
    clock.now = 5016;
    expect(api.npVizFrameDue('np')).toBe(false);
    expect(api.npVizFrameDue('sidebar')).toBe(true);
    clock.now = 5033;
    expect(api.npVizFrameDue('np')).toBe(true);
  });

  it('reduced effects, max performance and a small machine ask for the same', () => {
    expect(build(() => false, ['reduce-effects']).npLiteMotion()).toBe(true);
    expect(build(() => false, ['max-performance']).npLiteMotion()).toBe(true);
    expect(build(() => false, [], 4).npLiteMotion()).toBe(true);
    expect(build((q) => q === '(pointer: coarse)').npLiteMotion()).toBe(true);
  });

  it('every visualizer loop asks before it draws', () => {
    for (const fn of ['drawBars', 'drawWave', 'drawSpectrum', 'drawMirror', 'drawEqualizer']) {
      expect(JS).toContain(
        `sidebarVizAnimFrame = requestAnimationFrame(${fn});\n            if (!npVizFrameDue('sidebar')) return;`.replace(
          /\n/g,
          JS.includes('\r\n') ? '\r\n' : '\n',
        ),
      );
    }
    expect(extractFunction('npStartVisualizerLoop', JS)).toContain(
      "if (!npVizFrameDue('np')) return;",
    );
  });
});

describe('nothing is drawn for a surface nobody can see', () => {
  it('the sidebar visualizer does not run at phone width, where it is hidden', () => {
    expect(build((q) => q === '(max-width: 768px)').sidebarVizAllowed()).toBe(false);
    expect(build((q) => q === '(prefers-reduced-motion: reduce)').sidebarVizAllowed()).toBe(false);
    expect(build(() => false).sidebarVizAllowed()).toBe(true);
    expect(extractFunction('startSidebarVisualizer', JS)).toContain(
      'if (!sidebarVizAllowed()) return;',
    );
    // and the phone layout really does hide it
    expect(MOBILE).toMatch(/\.sidebar-visualizer \{\s*display: none !important;/);
  });

  it('a closed Now Playing overlay is gone, not just see-through', () => {
    const at = CSS.indexOf('\n.np-modal-overlay.hidden {');
    const rule = CSS.slice(at, CSS.indexOf('}', at));
    expect(rule).toContain('visibility: hidden');
    // it leaves after the fade, not before it
    expect(rule).toMatch(/visibility 0s linear var\(--dur-slow\)/);
  });

  it('a phone drops the blur behind a player that covers the whole screen', () => {
    const at = MOBILE.indexOf('    .np-modal-overlay {');
    expect(at).toBeGreaterThan(-1);
    expect(MOBILE.slice(at, MOBILE.indexOf('}', at))).toContain('backdrop-filter: none');
    // and sizes the player to the screen that is really there
    expect(MOBILE).toContain('height: 100dvh;');
  });

  it('the drawer’s decorations stop moving while it is off screen', () => {
    expect(MOBILE).toMatch(
      /\.sidebar:not\(\.mobile-open\) \*,[\s\S]{0,120}animation-play-state: paused;/,
    );
  });
});

describe('the album-art bars', () => {
  it('scale, they do not resize', () => {
    const loop = extractFunction('npStartVisualizerLoop', JS);
    expect(loop).toContain('artBars[i].style.transform = `scaleY(');
    expect(loop).not.toContain('style.height');
    const at = CSS.indexOf('\n.np-art-viz-bar {');
    const rule = CSS.slice(at, CSS.indexOf('}', at));
    expect(rule).toContain('transition: transform');
    expect(rule).not.toContain('transition: height');
  });

  it('are looked up once, not on every frame', () => {
    const loop = extractFunction('npStartVisualizerLoop', JS);
    const draw = loop.slice(loop.indexOf('function draw()'));
    expect(draw).not.toContain("querySelectorAll('.np-art-viz-bar')");
  });
});
