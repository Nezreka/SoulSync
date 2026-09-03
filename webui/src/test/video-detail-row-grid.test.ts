import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { extractFunction } from './vanilla-extract';

/**
 * The episode row grid has to have a column for every child it can produce.
 *
 * An OWNED episode emits six: index, thumbnail, info, the "Owned" badge, the
 * action buttons, and the chevron. The grid declared five, so the chevron fell
 * onto a second implicit row at the far left of the card. It only happened on
 * owned rows, which is why it read as an expand affordance instead of a broken
 * layout - until a fully-owned season made every row do it at once.
 */

const JS = readFileSync(resolve(process.cwd(), 'static/video/video-detail.js'), 'utf8');
const CSS = readFileSync(resolve(process.cwd(), 'static/video/video-side.css'), 'utf8');

function columns(selector: string): string[] {
  const at = CSS.indexOf(`\n${selector} {`);
  expect(at, `${selector} has no own rule`).toBeGreaterThan(-1);
  const rule = CSS.slice(at, CSS.indexOf('}', at) + 1);
  const decl = /grid-template-columns:\s*([^;]+);/.exec(rule);
  expect(decl, `${selector} declares no columns`).not.toBeNull();
  // minmax(a, b) holds a comma; collapse it so the split counts tracks.
  return decl![1].replace(/minmax\([^)]*\)/g, 'minmax').trim().split(/\s+/);
}

/** Direct children the row builder emits for one episode shape. */
function childCount(ep: Record<string, unknown>): number {
  const preamble = `
    function esc(s) { return String(s == null ? '' : s); }
    function fmtDate(d) { return String(d); }
    function sizedArt(u) { return u || ''; }
    function proxied(u) { return u || ''; }
    function mediaRes(r) { return r || ''; }
    function runtimeLabel(m) { return m ? m + 'm' : ''; }
    var selectedSeason = 1;
    var data = { source: 'library', kind: 'show' };
    window.VideoGrab = {};
  `;
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  const build = new Function(
    `${preamble}\n${extractFunction('episodeRow', JS)}\nreturn episodeRow;`,
  )() as (e: unknown) => string;
  const host = document.createElement('div');
  host.innerHTML = build(ep);
  const row = host.querySelector('.vd-ep') as HTMLElement;
  return row.children.length;
}

describe('the episode row grid fits every child it emits', () => {
  it('gives an owned episode a column for its badge AND its chevron', () => {
    const owned = childCount({ episode_number: 1, title: 'E1', owned: true, overview: 'x' });
    expect(owned).toBe(6);
    expect(columns('.vd-ep').length).toBeGreaterThanOrEqual(owned);
  });

  it('still fits a missing episode, which emits one fewer', () => {
    const missing = childCount({ episode_number: 2, title: 'E2', owned: false, overview: 'x' });
    expect(missing).toBe(5);
    expect(columns('.vd-ep').length).toBeGreaterThanOrEqual(missing);
  });

  it('keeps the youtube row in step with its own child count', () => {
    // No index column there, and its badge lives inside the action group.
    expect(columns('.vd-ep--yt').length).toBe(4);
  });

  it('stops the badge and buttons stretching to fill their column', () => {
    // A grid item fills its track unless told otherwise, which turned the Owned
    // badge into a 300px pill with its text stranded at the left edge.
    // justify-content places the TRACKS; only justify-self stops an ITEM growing.
    const rule = CSS.slice(CSS.indexOf('.vd-ep-badge, .vd-ep-get, .vd-ep-chev'));
    expect(rule.slice(0, 90)).toContain('justify-self: start');
    // ...but the info column is meant to fill its track, so it must be excluded.
    expect(rule.slice(0, 90)).not.toContain('.vd-ep-info');
  });

  it('caps the text column so the actions stay beside the episode', () => {
    expect(columns('.vd-ep')).toContain('minmax');
    expect(CSS.slice(CSS.indexOf('\n.vd-ep {'), CSS.indexOf('\n.vd-ep {') + 300))
      .toContain('justify-content: start');
  });
});
