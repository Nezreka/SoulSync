import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * #2040: an episode's source panel in the download view left a strip behind
 * after it was opened and closed, with the first source's edge showing in it.
 *
 * The panel closes by taking its grid row to 0fr. A row can only close as far
 * as its child will shrink, and a child's own top and bottom padding never
 * shrinks. Before the first open the panel is empty, so nothing showed until
 * it had been opened once. Measured in a browser: 15px closed before, 0 after.
 */

const CSS = readFileSync(resolve(process.cwd(), 'static/video/video-side.css'), 'utf8');

function rule(selector: string): string {
  const at = CSS.indexOf(`\n${selector} {`);
  expect(at, `${selector} rule exists`).toBeGreaterThan(-1);
  return CSS.slice(at, CSS.indexOf('}', at));
}

describe('a closed episode in the download view takes no room', () => {
  it('the collapsing child clips what is inside it', () => {
    const child = rule('.vdl-ep-search > *');
    expect(child).toContain('min-height: 0');
    expect(child).toContain('overflow: hidden');
  });

  it('carries no top or bottom padding of its own', () => {
    const pad = /padding:\s*([^;]+);/.exec(rule('.vdl-ep-srcs'))?.[1].trim().split(/\s+/) ?? [];
    // top right bottom left
    expect(pad.length).toBe(4);
    expect(pad[0]).toBe('0');
    expect(pad[2]).toBe('0');
  });

  it('keeps the spacing an open panel had, as margin on its contents', () => {
    expect(rule('.vdl-ep-srcs > :first-child')).toContain('margin-top: 3px');
    expect(rule('.vdl-ep-srcs > :last-child')).toContain('margin-bottom: 12px');
  });
});
