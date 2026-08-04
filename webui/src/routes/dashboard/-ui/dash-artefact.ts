/**
 * Test-only helpers for the dashboard artefact differentials: extract a
 * region of the RECORDED vanilla markup and structurally compare it against a
 * React render.
 *
 * The source is dash-vanilla-fixture.html — a byte-for-byte capture of
 * index.html's #dashboard-page block (lines 2225-2954) taken at the P9 flip,
 * right before that markup was deleted. The discover arc's recording-context
 * method: the differential keeps pinning the port against what the vanilla
 * REALLY was, not against a hand-maintained copy.
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { expect } from 'vitest';

const FIXTURE_HTML = join(__dirname, 'dash-vanilla-fixture.html');

/** The recorded #dashboard-page block. */
export function vanillaDashboardHtml(): string {
  return readFileSync(FIXTURE_HTML, 'utf-8');
}

/** Slice a dashboard `<article … data-card="X">…</article>` region.
 *  Dashboard cards never nest articles, so the first close ends the region. */
export function extractDashArticle(startMarker: string): string {
  const html = vanillaDashboardHtml();
  const start = html.indexOf(startMarker);
  expect(start).toBeGreaterThan(-1);
  const end = html.indexOf('</article>', start);
  expect(end).toBeGreaterThan(start);
  return html.slice(start, end + '</article>'.length);
}

export function parseVanilla(regionHtml: string): Element {
  const doc = new DOMParser().parseFromString(regionHtml, 'text/html');
  return doc.body.firstElementChild!;
}

const normalize = (text: string | null) => (text ?? '').replace(/\s+/g, ' ').trim();

/** An element's OWN text (direct text nodes only), normalized — textContent
 *  would trip on the vanilla's inter-element indentation. */
function ownText(el: Element): string {
  return normalize(
    Array.from(el.childNodes)
      .filter((node) => node.nodeType === 3 /* TEXT_NODE */)
      .map((node) => node.textContent ?? '')
      .join(' '),
  );
}

/** The attributes compared beyond id/class; `onclick` is deliberately absent
 *  (React binds listeners instead). */
const ATTRS = ['src', 'alt', 'title', 'aria-hidden', 'data-card', 'data-status-ready'] as const;

export function compareTrees(vanilla: Element, ported: Element, path: string): void {
  expect(`${path} tag:${ported.tagName}`).toBe(`${path} tag:${vanilla.tagName}`);
  expect(`${path} id:${ported.id}`).toBe(`${path} id:${vanilla.id}`);
  expect(`${path} class:${Array.from(ported.classList).join('.')}`).toBe(
    `${path} class:${Array.from(vanilla.classList).join('.')}`,
  );
  for (const attr of ATTRS) {
    expect(`${path} ${attr}:${ported.getAttribute(attr) ?? ''}`).toBe(
      `${path} ${attr}:${vanilla.getAttribute(attr) ?? ''}`,
    );
  }
  const vStyle = (vanilla as HTMLElement).style?.display ?? '';
  const pStyle = (ported as HTMLElement).style?.display ?? '';
  expect(`${path} display:${pStyle}`).toBe(`${path} display:${vStyle}`);
  expect(`${path} text:${ownText(ported)}`).toBe(`${path} text:${ownText(vanilla)}`);

  const vKids = Array.from(vanilla.children);
  const pKids = Array.from(ported.children);
  expect(`${path} children:${pKids.map((kid) => kid.tagName).join(',')}`).toBe(
    `${path} children:${vKids.map((kid) => kid.tagName).join(',')}`,
  );
  vKids.forEach((vKid, index) => {
    const label = vKid.id || Array.from(vKid.classList).join('.') || vKid.tagName;
    compareTrees(vKid, pKids[index], `${path}>${label}`);
  });
}
