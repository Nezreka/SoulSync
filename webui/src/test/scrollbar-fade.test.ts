import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { beforeAll, describe, expect, it, vi } from 'vitest';

/**
 * General fading scrollbars (scrollbars.css section 4 + scrollbar-fade.js).
 *
 * Thibs asked for a fading scrollbar on the PWA; Broque wanted it general.
 * Both hostile review rounds on this change made the same point: a contract
 * test that reads only scrollbars.css is vacuous, because the rendered
 * cascade is the COMBINATION of style.css's pre-existing global `*`
 * scrollbar design (0,0,1 — ties broken by sheet order) and section 4.
 * So this file pins all three sources together:
 * - the style.css baseline section 4 builds on (width 12px, thumb a30 with
 *   an a10 border, track a10, hover a50) — if that baseline moves, the
 *   section-4 header's claims must be re-derived, and this test is the
 *   tripwire;
 * - index.html linking style.css BEFORE scrollbars.css (the tie-break the
 *   whole design rests on) and loading scrollbar-fade.js;
 * - section 4's own rules: rest a10 core+border on a transparent track,
 *   active a40 core+border via a zero-specificity :where() rule, no size
 *   rules, no hover rule outside the reduced-motion block;
 * - prefers-reduced-motion pinning the pre-existing design exactly;
 * - the marker behaviour itself, evaluated for real in jsdom.
 */

const CSS = readFileSync(resolve(process.cwd(), 'static/scrollbars.css'), 'utf8');
const STYLE_CSS = readFileSync(resolve(process.cwd(), 'static/style.css'), 'utf8');
const JS = readFileSync(resolve(process.cwd(), 'static/scrollbar-fade.js'), 'utf8');
const HTML = readFileSync(resolve(process.cwd(), 'index.html'), 'utf8');

function ruleBodyIn(source: string, selector: string, fromIndex = 0): string {
  const start = source.indexOf(selector, fromIndex);
  if (start === -1) throw new Error(`${selector} not found`);
  const open = source.indexOf('{', start);
  const close = source.indexOf('}', open);
  return source.slice(open + 1, close);
}

function ruleBody(selector: string, fromIndex = 0): string {
  return ruleBodyIn(CSS, selector, fromIndex);
}

// Start at the section's own comment opener (not the words inside it), so
// slices that strip comments line up with real comment boundaries.
const SECTION4_AT = CSS.indexOf('/* ── 4. General fading scrollbars');
const section4 = CSS.slice(SECTION4_AT);

describe('fading scrollbar — the style.css baseline it builds on', () => {
  it('style.css carries the global * scrollbar design at (0,0,1)', () => {
    expect(ruleBodyIn(STYLE_CSS, '*::-webkit-scrollbar {')).toContain('width: 12px');
    const thumb = ruleBodyIn(STYLE_CSS, '*::-webkit-scrollbar-thumb {');
    expect(thumb).toContain('background: var(--white-a30)');
    expect(thumb).toContain('border: 2px solid var(--white-a10)');
    expect(ruleBodyIn(STYLE_CSS, '*::-webkit-scrollbar-track {')).toContain(
      'background: var(--white-a10)',
    );
    expect(ruleBodyIn(STYLE_CSS, '*::-webkit-scrollbar-thumb:hover {')).toContain(
      'background: var(--white-a50)',
    );
  });

  it('index.html links style.css before scrollbars.css (the tie-break)', () => {
    const styleAt = HTML.indexOf("filename='style.css'");
    const scrollbarsAt = HTML.indexOf("filename='scrollbars.css'");
    expect(styleAt).toBeGreaterThan(-1);
    expect(scrollbarsAt).toBeGreaterThan(styleAt);
  });
});

describe('fading scrollbar — wiring', () => {
  it('index.html loads scrollbar-fade.js as a static script tag', () => {
    expect(HTML).toMatch(/<script src="\{\{ url_for\('static', filename='scrollbar-fade\.js',/);
  });

  it('the marker listener is capture-phase and passive', () => {
    expect(JS).toContain("addEventListener('scroll'");
    expect(JS).toContain('capture: true');
    expect(JS).toContain('passive: true');
  });
});

describe('fading scrollbar — CSS contract', () => {
  it('general thumbs rest dim: a10 core and border, colour-only transition', () => {
    // Line-anchored: a bare substring search would land on component rules
    // like `.vd-rail::-webkit-scrollbar-thumb` first.
    const body = ruleBody('\n::-webkit-scrollbar-thumb {', SECTION4_AT);
    expect(body).toContain('background-color: var(--white-a10)');
    expect(body).toContain('border-color: var(--white-a10)');
    expect(body).toContain('background-color 0.4s ease');
    expect(body).toContain('border-color 0.4s ease');
  });

  it('the track and corner go transparent — a deliberate override of the a10 baseline', () => {
    expect(ruleBody('\n::-webkit-scrollbar-track {', SECTION4_AT)).toContain(
      'background-color: transparent',
    );
    expect(ruleBody('\n::-webkit-scrollbar-corner {', SECTION4_AT)).toContain(
      'background-color: transparent',
    );
  });

  it('the scrolling state brightens core and border together via :where()', () => {
    // :where() keeps this at (0,0,1) — tied with the global baseline and
    // below every component rule (0,1,1) — so a custom thumb design wins
    // in both states and is never flattened; the tie inside section 4 is
    // decided by source order (active after rest).
    const body = ruleBody(':where(.is-scrolling)::-webkit-scrollbar-thumb {', SECTION4_AT);
    expect(body).toContain('background-color: var(--white-a40)');
    expect(body).toContain('border-color: var(--white-a40)');
  });

  it('section 4 declares no size rule and no !important', () => {
    expect(section4).not.toContain('!important');
    // The bare size selector must not exist in section 4: width already
    // comes from the style.css baseline, and a height here would leak
    // into width-only component designs per-property.
    expect(() => ruleBody('\n::-webkit-scrollbar {', SECTION4_AT)).toThrow();
    for (const body of [
      ruleBody('\n::-webkit-scrollbar-thumb {', SECTION4_AT),
      ruleBody('\n::-webkit-scrollbar-track {', SECTION4_AT),
      ruleBody(':where(.is-scrolling)::-webkit-scrollbar-thumb {', SECTION4_AT),
    ]) {
      expect(body).not.toMatch(/\b(width|height|display)\s*:/);
    }
  });

  it('section 4 has no hover rule outside the reduced-motion block', () => {
    // Hover brightness is the baseline's a50 (0,1,1), which outranks both
    // section-4 states; a section-4 hover rule outside reduced motion
    // would silently fight it. Comments are stripped first — the header
    // legitimately names the baseline's `:hover` rule.
    const rulesOnly = section4.replace(/\/\*[\s\S]*?\*\//g, '');
    const beforeReduced = rulesOnly.slice(0, rulesOnly.indexOf('@media (prefers-reduced-motion'));
    expect(beforeReduced).not.toContain(':hover');
  });

  it('reduced motion pins the pre-existing design in both states, hover included', () => {
    const reduced = section4.slice(section4.indexOf('@media (prefers-reduced-motion: reduce)'));
    const rest = ruleBodyIn(reduced, '\n  ::-webkit-scrollbar-thumb {');
    expect(rest).toContain('background-color: var(--white-a30)');
    expect(rest).toContain('border-color: var(--white-a10)');
    expect(rest).toContain('transition: none');
    expect(ruleBodyIn(reduced, '\n  ::-webkit-scrollbar-thumb:hover {')).toContain(
      'background-color: var(--white-a30)',
    );
    const active = ruleBodyIn(reduced, ':where(.is-scrolling)::-webkit-scrollbar-thumb {');
    expect(active).toContain('background-color: var(--white-a30)');
    expect(active).toContain('border-color: var(--white-a10)');
  });

  it('horizontal thumbs stay pinned by section 1 — the fade is vertical-only', () => {
    // Section 1 is (0,1,2) and must still exist so rails do not fade below
    // the visibility Broque asked for in #1637; section 4 must not grow a
    // horizontal variant that fights it.
    expect(CSS).toContain('html ::-webkit-scrollbar-thumb:horizontal');
    expect(ruleBody('html ::-webkit-scrollbar-thumb:horizontal,')).toContain('var(--white-a25)');
    expect(section4).not.toContain(':horizontal');
  });
});

// measured in chromium: scroll 0 -> l 0 r 32, 10px -> l 10, end -> l 32 r 0,
// a rail that fits -> 0 / 0. jsdom can't run scroll timelines, so pin the text.
describe('rail edge fade — only on the side with hidden content', () => {
  const section3 = CSS.slice(CSS.indexOf('/* ── 3.'), SECTION4_AT);

  it('the gradient stops read the per-edge vars, not the shared size', () => {
    expect(section3).toContain('#000 var(--hscroll-fade-l)');
    expect(section3).toContain('#000 calc(100% - var(--hscroll-fade-r))');
    expect(section3).not.toContain('#000 var(--hscroll-fade-size)');
  });

  it('the left edge starts clear, the fallback fades the right only', () => {
    expect(section3).toMatch(
      /--hscroll-fade-l: 0px;\s*--hscroll-fade-r: var\(--hscroll-fade-size\);/,
    );
  });

  it('a scroll timeline ramps each edge over its fade-size', () => {
    const supports = section3.slice(section3.indexOf('@supports (animation-timeline: scroll())'));
    expect(supports).toContain('--hscroll-fade-r: 0px;');
    expect(supports).toContain('animation-timeline: scroll(self inline), scroll(self inline);');
    expect(supports).toMatch(
      /0px var\(--hscroll-fade-size\),\s*calc\(100% - var\(--hscroll-fade-size\)\) 100%;/,
    );
    expect(section3).toMatch(/@property --hscroll-fade-l \{\s*syntax: '<length>';/);
    expect(section3).toMatch(/@property --hscroll-fade-r \{\s*syntax: '<length>';/);
  });
});

describe('fading scrollbar — marker behaviour', () => {
  beforeAll(() => {
    vi.useFakeTimers();
    // The script is an IIFE that registers its listener on evaluation.
    // eslint-disable-next-line @typescript-eslint/no-implied-eval
    new Function(JS)();
  });

  function scroller(): HTMLDivElement {
    const el = document.createElement('div');
    document.body.appendChild(el);
    return el;
  }

  it('marks the scrolling element and clears it 1200ms after the last scroll', () => {
    const el = scroller();
    el.dispatchEvent(new Event('scroll'));
    expect(el.classList.contains('is-scrolling')).toBe(true);

    vi.advanceTimersByTime(1199);
    expect(el.classList.contains('is-scrolling')).toBe(true);

    // A fresh scroll restarts the idle clock.
    el.dispatchEvent(new Event('scroll'));
    vi.advanceTimersByTime(1199);
    expect(el.classList.contains('is-scrolling')).toBe(true);

    vi.advanceTimersByTime(1);
    expect(el.classList.contains('is-scrolling')).toBe(false);
    el.remove();
  });

  it('viewport scrolls mark documentElement, not document', () => {
    document.dispatchEvent(new Event('scroll'));
    expect(document.documentElement.classList.contains('is-scrolling')).toBe(true);
    vi.advanceTimersByTime(1200);
    expect(document.documentElement.classList.contains('is-scrolling')).toBe(false);
  });

  it("one scroller going idle does not clear another scroller's marker", () => {
    const a = scroller();
    const b = scroller();
    a.dispatchEvent(new Event('scroll'));
    vi.advanceTimersByTime(600);
    b.dispatchEvent(new Event('scroll'));
    vi.advanceTimersByTime(600);
    expect(a.classList.contains('is-scrolling')).toBe(false);
    expect(b.classList.contains('is-scrolling')).toBe(true);
    vi.advanceTimersByTime(600);
    expect(b.classList.contains('is-scrolling')).toBe(false);
    a.remove();
    b.remove();
  });
});
