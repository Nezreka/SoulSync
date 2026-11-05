/**
 * SeadogsBooty: picking a preferred audio language showed it as a red pill
 * with ⊘, the reject chip's look, so "prefer English" read as "exclude
 * English". language chips have their own positive style now.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

const read = (p: string) => readFileSync(resolve(process.cwd(), p), 'utf8');
const JS = read('static/video/video-settings.js');
const CSS = read('static/video/video-side.css');

describe('audio language chips', () => {
  it('are pick chips, not reject chips', () => {
    expect(JS).toContain(`class="vq-chip vq-chip--pick' + (on ? ' on' : '') + '" data-vq-lang="`);
    // the reject chips keep the plain class
    expect(JS).toMatch(/class="vq-chip' \+ \(on \? ' on' : ''\) \+ '" data-vq-reject="/);
  });

  it('a picked language is the accent with a tick, out-ranking the red ⊘', () => {
    expect(CSS).toMatch(/\.vq-chip\.vq-chip--pick\.on \{[^}]*accent-rgb/);
    expect(CSS).toMatch(/\.vq-chip\.vq-chip--pick\.on::before \{ content: '✓ '; \}/);
  });
});
