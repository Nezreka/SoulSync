import { expect, test, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { extractFunction } from '../../src/test/vanilla-extract';

/**
 * #2159: the Top 10 numerals sat on top of the posters and were cut off, and
 * rows didn't agree on poster size or heading style.
 *
 * Builds a ranked rail, a plain rail and a personalized rail with the page's
 * REAL card / rankedCard markup and the shipped CSS, then measures them.
 */

const JS = readFileSync(resolve(process.cwd(), 'static/video/video-discover.js'), 'utf8');
const TOKENS = readFileSync(resolve(process.cwd(), 'static/tokens.css'), 'utf8');
const STYLE = readFileSync(resolve(process.cwd(), 'static/style.css'), 'utf8');
const VIDEO_CSS = readFileSync(resolve(process.cwd(), 'static/video/video-side.css'), 'utf8');

const FNS = ['esc', 'hueOf', 'card', 'rankedCard'].map((n) => extractFunction(n, JS)).join('\n');

async function build(page: Page, width: number) {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.setViewportSize({ width, height: 1000 });
  await page.setContent(
    `<style>${TOKENS}</style><style>${STYLE}</style><style>${VIDEO_CSS}</style>` +
      `<div class="vdsc-page vdsc2" data-vdsc-page style="padding:0 24px">` +
      `<section class="vdsc-shelf vdsc-shelf--ranked vdsc-shelf--in" data-s="ranked"><div class="vdsc-shelf-head"><h3 class="vdsc-shelf-title">Movies</h3></div><div class="vdsc-rail" data-vdsc-rail></div></section>` +
      `<section class="vdsc-shelf vdsc-shelf--in" data-s="plain"><div class="vdsc-shelf-head"><h3 class="vdsc-shelf-title">In Theaters Now</h3></div><div class="vdsc-rail" data-vdsc-rail></div></section>` +
      `<section class="vdsc-shelf vdsc-shelf--in vdsc-shelf--ml" data-s="ml"><div class="vdsc-shelf-head"><h3 class="vdsc-shelf-title">More like Dune</h3></div><div class="vdsc-rail" data-vdsc-rail></div></section>` +
      `</div>`,
  );
  await page.evaluate((fns) => {
    // eslint-disable-next-line @typescript-eslint/no-implied-eval
    const api = new Function('NOW_YEAR', 'window', `${fns}; return { card, rankedCard };`)(
      2026,
      {},
    ) as { card: (it: object) => string; rankedCard: (it: object, r: number) => string };
    const items = Array.from({ length: 10 }, (_, i) => ({
      kind: 'movie',
      tmdb_id: i + 1,
      title: 'Title ' + (i + 1),
      year: 2026,
      rating: 7.5,
      poster: '',
    }));
    const rail = (s: string) =>
      document.querySelector(`[data-s="${s}"] [data-vdsc-rail]`) as HTMLElement;
    rail('ranked').innerHTML = items.map((it, i) => api.rankedCard(it, i + 1)).join('');
    rail('plain').innerHTML = items.map(api.card).join('');
    rail('ml').innerHTML = items.map(api.card).join('');
  }, FNS);
}

for (const width of [1440, 820, 390]) {
  test(`top 10 numerals sit beside the poster, whole, at ${width}px`, async ({ page }) => {
    await build(page, width);
    const r = await page.evaluate(() => {
      const rail = document.querySelector('[data-s="ranked"] [data-vdsc-rail]') as HTMLElement;
      const rb = rail.getBoundingClientRect();
      return Array.from(rail.querySelectorAll('.vsr-ranked')).map((w) => {
        const n = (w.querySelector('.vsr-rank') as HTMLElement).getBoundingClientRect();
        const p = (w.querySelector('.vsr-poster') as HTMLElement).getBoundingClientRect();
        return {
          // beside: left of the poster and level with it, not stacked above it
          beside: n.right <= p.left + 0.5 && n.bottom > p.top && n.top < p.bottom,
          clippedY: n.top < rb.top - 0.5 || n.bottom > rb.bottom + 0.5,
          numBottom: n.bottom,
          posterBottom: p.bottom,
        };
      });
    });
    expect(r).toHaveLength(10);
    for (const it of r) {
      expect(it.beside).toBe(true);
      expect(it.clippedY).toBe(false);
      expect(it.numBottom).toBeLessThanOrEqual(it.posterBottom + 1);
    }
  });

  test(`every rail uses one poster size and one heading style at ${width}px`, async ({ page }) => {
    await build(page, width);
    const r = await page.evaluate(() =>
      ['ranked', 'plain', 'ml'].map((s) => {
        const sec = document.querySelector(`[data-s="${s}"]`) as HTMLElement;
        const t = getComputedStyle(sec.querySelector('.vdsc-shelf-title') as HTMLElement);
        return {
          poster: Math.round(
            (sec.querySelector('.vsr-poster') as HTMLElement).getBoundingClientRect().width,
          ),
          size: t.fontSize,
          weight: t.fontWeight,
          color: t.color,
        };
      }),
    );
    expect(new Set(r.map((x) => x.poster)).size).toBe(1);
    expect(new Set(r.map((x) => `${x.size}/${x.weight}/${x.color}`)).size).toBe(1);
  });
}
