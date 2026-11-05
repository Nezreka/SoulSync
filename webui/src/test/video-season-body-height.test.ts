/**
 * SeadogsBooty, Dexter: Resurrection S1: with a few episodes' release results
 * open you couldn't scroll to the later episodes. an open season was capped
 * at max-height: 5000px with overflow hidden, so everything past 5000px was
 * cut off. it opens on grid rows now (measured in chromium: 8000px of content
 * opens to its full height). jsdom can't lay out, so this pins the rule.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

const read = (p: string) => readFileSync(resolve(process.cwd(), p), 'utf8');
const CSS = read('static/video/video-side.css');
const JS = read('static/video/video-download-view.js');

describe('an open season', () => {
  it('has no height cap', () => {
    expect(CSS).not.toMatch(/\.vdl-season--open \.vdl-season-body \{ max-height/);
    expect(CSS).toMatch(/\.vdl-season-body \{[^}]*grid-template-rows: 0fr;/);
    expect(CSS).toMatch(/\.vdl-season--open \.vdl-season-body \{ grid-template-rows: 1fr; \}/);
    expect(CSS).toMatch(/\.vdl-season-inner \{ min-height: 0; \}/);
  });

  it('wraps its content in the one inner row the grid needs', () => {
    expect(JS).toContain(`'<div class="vdl-season-body"><div class="vdl-season-inner">'`);
  });
});
