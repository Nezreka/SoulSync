/**
 * "Whole album via Lidarr" is drawn hidden and shown only when the chain has
 * Lidarr and another source. its class sets display:flex, which beats the
 * hidden attribute, so it showed for everyone (Boulder, Nov 21 2026: no
 * Lidarr at all, the switch on every download-missing dialog). jsdom has no
 * cascade worth trusting here, so this pins the rule as text.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const css = readFileSync(resolve(process.cwd(), 'static/style.css'), 'utf8');

describe('the whole-album-via-lidarr switch', () => {
  it('stays hidden while it is hidden', () => {
    expect(css).toMatch(/\.force-download-toggle\[hidden\]\s*\{\s*display:\s*none;?\s*\}/);
  });
});
