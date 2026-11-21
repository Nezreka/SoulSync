/**
 * SeadogsBooty: picking a preferred audio language showed it as a red pill
 * with ⊘, the reject chip's look, so "prefer English" read as "exclude
 * English". language chips have their own positive style now.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const read = (p: string) => readFileSync(resolve(process.cwd(), p), 'utf8');
// the chips are React (src/features/settings/tabs/video-quality.tsx, SETTINGS_PORT.md video pass V1)
const JS = read('src/features/settings/tabs/video-quality.tsx');
const CSS = read('static/video/video-side.css');

describe('audio language chips', () => {
  it('are pick chips, not reject chips', () => {
    // (settings-bic phase 3) both are the parts' Chips: on is the accent with a
    // tick, never the red ⊘; each keeps its own hook
    expect(JS).toMatch(/<Chips\s+id="vq-langs"[^>]*itemAttr="data-vq-lang"/);
    expect(JS).toMatch(/<Chips\s+id="vq-rejects"[^>]*itemAttr="data-vq-reject"/);
    const PARTS = read('src/features/settings/ui/parts.tsx');
    const chips = PARTS.slice(PARTS.indexOf('export function Chips('));
    expect(chips.slice(0, chips.indexOf('\n}\n'))).toContain('<Icon name="check"');
  });

  it('a picked language is the accent with a tick, out-ranking the red ⊘', () => {
    expect(CSS).toMatch(/\.vq-chip\.vq-chip--pick\.on \{[^}]*accent-rgb/);
    expect(CSS).toMatch(/\.vq-chip\.vq-chip--pick\.on::before \{ content: '✓ '; \}/);
  });
});
