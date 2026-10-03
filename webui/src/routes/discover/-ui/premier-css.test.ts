import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * layout fixes jsdom can't see, pinned as text. each was measured in real
 * chromium (sept 29) and each looks like something to "simplify" away.
 */

const CSS = readFileSync(resolve(process.cwd(), 'static/discover-premier.css'), 'utf8').replace(
  /\r\n/g,
  '\n',
);

function rule(selectorStart: string, media?: string): string {
  const scope = media ? CSS.slice(CSS.indexOf(media)) : CSS;
  const at = scope.indexOf(`${selectorStart} {`);
  expect(at, `${selectorStart} not found`).toBeGreaterThanOrEqual(0);
  return scope.slice(at, scope.indexOf('}', at));
}

describe('discover-premier.css', () => {
  it('never pins the hero to a fixed height on a phone', () => {
    // 290px with overflow hidden clipped the title and piled the actions onto
    // the faces strip.
    const phone = rule('    .discover-hero', '@media (max-width: 768px)');
    expect(phone).toContain('height: auto !important');
    expect(CSS).not.toMatch(/\.discover-hero\s*\{[^}]*height:\s*290px/);
  });

  it('starts the faces strip at the left, so an overflowing row can scroll to its first face', () => {
    // style.css centres the indicators; centred overflow is unreachable.
    expect(rule('.discover-hero-indicators')).toContain('justify-content: flex-start !important');
  });

  it('keeps stacked headers on the left on a phone', () => {
    expect(
      rule('    .discovery-zone-head,\n    .discover-section-header', '@media (max-width: 768px)'),
    ).toContain('align-items: flex-start !important');
  });

  it('lets the dial size to its content', () => {
    // style.css's leftover min-height: 100% pushed the dial into the next shelf.
    expect(rule('.adv-wave,\n.discovery-zone .adv-wave')).toContain('min-height: auto !important');
  });

  it('highlights the Deezer genre the component actually marks', () => {
    // the component sets .is-active; premier used to style .active, which
    // nothing sets, and its !important background beat style.css's is-active.
    expect(CSS).toContain('.dz-ed-genre.is-active {');
    expect(CSS).not.toContain('.dz-ed-genre.active {');
  });
});
