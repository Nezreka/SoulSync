import { expect, test, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

/**
 * M01: nothing in the hero foot may sit on anything else.
 *
 * The dots and the Watch All / View Recommended pills were two absolutely
 * positioned boxes both anchored at bottom: 24px. jsdom cannot see that, and
 * a class-name assertion cannot either. This lays the real component out under
 * the real stylesheets in a real browser and measures the rectangles.
 */

const STYLE = readFileSync(resolve(process.cwd(), 'static/style.css'), 'utf8');
const MOBILE = readFileSync(resolve(process.cwd(), 'static/mobile.css'), 'utf8');

const HERO_HTML = readFileSync(
  resolve(process.cwd(), 'tests/layout/__fixtures__/discover-hero.fixture.txt'),
  'utf8',
);

function markup() {
  return HERO_HTML;
}

async function layout(page: Page, width: number, height: number, html: string) {
  // Measure the resting layout, and match how the review captured evidence.
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.setViewportSize({ width, height });
  await page.setContent(
    `<style>${STYLE}</style><style>${MOBILE}</style>` +
      `<div class="discover-container"><div class="discover-command-grid">` +
      `<div class="discover-command-hero">${html}</div></div></div>`,
  );
  // The stylesheet leans on these; without them accent colours resolve to
  // nothing and some paddings collapse.
  await page.addStyleTag({
    content: ':root { --accent-rgb: 88,101,242; --accent: #5865f2; --sidebar-w: 240px; }',
  });
}

/** Every control a user is meant to be able to hit in the hero. */
const CONTROLS = [
  '#discover-hero-discography',
  '#discover-hero-add',
  '#discover-hero-watch-all',
  '#discover-hero-view-all',
  '.hero-indicator',
];

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

function overlaps(a: Box, b: Box) {
  return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
}

for (const [w, h] of [
  [1440, 900],
  [1024, 768],
  [768, 1024],
  [390, 844],
  [320, 740],
] as const) {
  test(`hero controls never intersect at ${w}x${h}`, async ({ page }) => {
    await layout(page, w, h, markup());
    const found = await boxes(page);
    expect(found.length).toBeGreaterThan(10);
    for (const control of found) {
      expect(control.x, `${control.sel} left boundary`).toBeGreaterThanOrEqual(0);
      expect(control.x + control.w, `${control.sel} right boundary`).toBeLessThanOrEqual(w);
    }

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

  test(`every hero control is hittable at its centre at ${w}x${h}`, async ({ page }) => {
    await layout(page, w, h, markup());
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

test('an 80-character title does not push the actions out of the hero', async ({ page }) => {
  await layout(page, 390, 844, markup());
  const { titleLen, actionsInside } = await page.evaluate(() => {
    const hero = document.querySelector('.discover-hero')!.getBoundingClientRect();
    const title = document.querySelector('.discover-hero-title')!.textContent!;
    const inside = [
      ...document.querySelectorAll('.discover-hero-actions > *, .discover-hero-controls button'),
    ].every((el) => {
      const r = el.getBoundingClientRect();
      return r.top >= hero.top - 1 && r.bottom <= hero.bottom + 1 && r.width > 0 && r.height > 0;
    });
    return { titleLen: title.length, actionsInside: inside };
  });
  expect(titleLen).toBeGreaterThan(60);
  expect(actionsInside).toBe(true);
});

test('a missing artist image still produces a finished hero', async ({ page }) => {
  // The fixture artist carries no image_url, so this is the real no-art path.
  await layout(page, 1440, 900, markup());
  const placeholder = await page.locator('.hero-image-placeholder').count();
  expect(placeholder).toBe(1);
  const found = await boxes(page);
  for (let i = 0; i < found.length; i++) {
    for (let j = i + 1; j < found.length; j++) {
      expect(overlaps(found[i], found[j])).toBe(false);
    }
  }
});

test('the indicator target is finger-sized while the dot stays small', async ({ page }) => {
  await layout(page, 390, 844, markup());
  const sizes = await page.evaluate(() => {
    const btn = document.querySelector('.hero-indicator')!.getBoundingClientRect();
    const dot = document.querySelector('.hero-indicator-dot')!.getBoundingClientRect();
    return { btn: [btn.width, btn.height], dot: [dot.width, dot.height] };
  });
  expect(sizes.btn[0]).toBeGreaterThanOrEqual(36);
  expect(sizes.btn[1]).toBeGreaterThanOrEqual(36);
  expect(sizes.dot[1]).toBeLessThanOrEqual(12);
});
