import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { expect, test, type Page } from '@playwright/test';

import { extractFunction } from '../../src/test/vanilla-extract';

/**
 * V01: the video hero's carousel dots may not sit on the Trailer button.
 *
 * On a phone the hero body is full width and the dots were parked at
 * bottom-right, so they landed on the actions row. mobile.css's blanket
 * button min-height then stretched each 9px dot into a lozenge, which is the
 * "tall pills over Trailer" in the review screenshot.
 *
 * This runs the page's REAL renderHero/paintHeroBody in the browser, so the
 * markup measured here is the markup the page ships.
 */

const JS = readFileSync(resolve(process.cwd(), 'static/video/video-discover.js'), 'utf8');
const VIDEO_CSS = readFileSync(resolve(process.cwd(), 'static/video/video-side.css'), 'utf8');
const STYLE = readFileSync(resolve(process.cwd(), 'static/style.css'), 'utf8');
const MOBILE = readFileSync(resolve(process.cwd(), 'static/mobile.css'), 'utf8');

const HERO_FNS = ['renderHero', 'paintHeroBody', 'preloadNextHero']
  .map((n) => extractFunction(n, JS))
  .join('\n');

const ITEMS = [
  {
    kind: 'show',
    title: 'Silo and the Very Long Subtitle That Wraps On A Phone',
    year: 2023,
    rating: 8.1,
    tmdb_id: 1,
    library_id: 7,
    overview: 'A ruined and toxic world, ten thousand people live in a silo.',
    backdrop: '',
    logo: '',
  },
  { kind: 'movie', title: 'Dune', year: 2021, rating: 7.9, tmdb_id: 2, backdrop: '', logo: '' },
  { kind: 'movie', title: 'Arrival', year: 2016, rating: 7.9, tmdb_id: 3, backdrop: '', logo: '' },
  { kind: 'show', title: 'Severance', year: 2022, rating: 8.7, tmdb_id: 4, backdrop: '', logo: '' },
  { kind: 'movie', title: 'Sicario', year: 2015, rating: 7.6, tmdb_id: 5, backdrop: '', logo: '' },
];

async function buildHero(page: Page, width: number, height: number, animate = false) {
  // The hero body slides up 16px over 0.6s on entry (from opacity 0), so a
  // measurement taken mid-animation is 16px off its resting place. Reduced
  // motion is also how the review captured its evidence.
  await page.emulateMedia({ reducedMotion: animate ? null : 'reduce' });
  await page.setViewportSize({ width, height });
  await page.setContent(
    `<style>${STYLE}</style><style>${MOBILE}</style><style>${VIDEO_CSS}</style>` +
      `<div class="vdsc-page" data-vdsc-page><div class="vdsc-hero" data-vdsc-hero></div></div>`,
  );
  await page.addStyleTag({ content: ':root { --accent-rgb: 88,101,242; --accent: #5865f2; }' });
  await page.evaluate(
    ([fns, items]) => {
      const $ = (s: string) => document.querySelector(s);
      const esc = (s: unknown) => String(s ?? '').replace(/[&<>"']/g, (c) => c);
      const hueOf = () => 230;
      const state = { hero: { items, idx: 0, timer: null } };
      const startHeroTimer = () => {};
      // eslint-disable-next-line no-new-func
      new Function('$', 'esc', 'hueOf', 'state', 'startHeroTimer', `${fns}; renderHero();`)(
        $,
        esc,
        hueOf,
        state,
        startHeroTimer,
      );
    },
    [HERO_FNS, ITEMS] as const,
  );
}

const CONTROLS = ['.vdsc-hero-cta', '.vdsc-hero-trailer', '.vdsc-dot'];

async function boxes(page: Page) {
  return page.evaluate((selectors) => {
    const out: { sel: string; i: number; x: number; y: number; w: number; h: number }[] = [];
    for (const sel of selectors) {
      document.querySelectorAll(sel).forEach((el, i) => {
        const r = el.getBoundingClientRect();
        out.push({ sel, i, x: r.x, y: r.y, w: r.width, h: r.height });
      });
    }
    return out;
  }, CONTROLS);
}

type Box = Awaited<ReturnType<typeof boxes>>[number];
const overlaps = (a: Box, b: Box) =>
  a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;

for (const [w, h] of [
  [1440, 900],
  [1024, 768],
  [768, 1024],
  [390, 844],
  [320, 740],
] as const) {
  test(`video hero controls never intersect at ${w}x${h}`, async ({ page }) => {
    await buildHero(page, w, h);
    const found = await boxes(page);
    expect(found.filter((b) => b.sel === '.vdsc-dot')).toHaveLength(5);

    const collisions: string[] = [];
    for (let i = 0; i < found.length; i++) {
      for (let j = i + 1; j < found.length; j++) {
        if (overlaps(found[i], found[j])) {
          collisions.push(`${found[i].sel}[${found[i].i}] over ${found[j].sel}[${found[j].i}]`);
        }
      }
    }
    expect(collisions, collisions.join('\n')).toEqual([]);
  });

  test(`every video hero control is hittable at ${w}x${h}`, async ({ page }) => {
    await buildHero(page, w, h);
    const misses = await page.evaluate((selectors) => {
      const bad: string[] = [];
      for (const sel of selectors) {
        document.querySelectorAll(sel).forEach((el, i) => {
          const r = el.getBoundingClientRect();
          if (r.width === 0 || r.height === 0) {
            bad.push(`${sel}[${i}] has no size`);
            return;
          }
          const hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
          if (!hit || (hit !== el && !el.contains(hit))) {
            bad.push(`${sel}[${i}] centre hits ${hit ? hit.className || hit.tagName : 'nothing'}`);
          }
        });
      }
      return bad;
    }, CONTROLS);
    expect(misses, misses.join('\n')).toEqual([]);
  });
}

test('the settled layout matches the reduced-motion one', async ({ page }) => {
  // Same check with the entry animation actually running, once it has ended.
  await buildHero(page, 390, 844, true);
  await page.waitForTimeout(900);
  const found = await boxes(page);
  const collisions: string[] = [];
  for (let i = 0; i < found.length; i++) {
    for (let j = i + 1; j < found.length; j++) {
      if (overlaps(found[i], found[j])) {
        collisions.push(`${found[i].sel}[${found[i].i}] over ${found[j].sel}[${found[j].i}]`);
      }
    }
  }
  expect(collisions, collisions.join('\n')).toEqual([]);
});

test('a dot is a small dot in a big target, at every width', async ({ page }) => {
  for (const w of [1440, 390]) {
    await buildHero(page, w, 844);
    const size = await page.evaluate(() => {
      const btn = document.querySelector('.vdsc-dot')!.getBoundingClientRect();
      const before = getComputedStyle(document.querySelector('.vdsc-dot')!, '::before');
      return { w: btn.width, h: btn.height, dotH: parseFloat(before.height) };
    });
    // mobile.css stretches any plain button to 38px; the target is bigger than
    // that on purpose, so nothing can turn the dot itself into a lozenge.
    expect(size.w, `width at ${w}`).toBeGreaterThanOrEqual(40);
    expect(size.h, `height at ${w}`).toBeGreaterThanOrEqual(40);
    expect(size.dotH, `dot at ${w}`).toBeLessThanOrEqual(12);
  }
});
