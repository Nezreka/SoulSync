import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * The Server Activity drawer and the floating buttons (#2090).
 *
 * The activity, notification and help buttons float at the bottom right on
 * the top layer. The drawer slides in over that same corner one layer lower,
 * so the three buttons sat on top of its playback cards. It is on the
 * buttons' layer now, and is added to the page after them, so it covers them
 * while it is open.
 */

const CSS = readFileSync(resolve(process.cwd(), 'static/style.css'), 'utf8');
const HTML = readFileSync(resolve(process.cwd(), 'index.html'), 'utf8');
const TS = readFileSync(resolve(process.cwd(), 'src/shell/server-activity.ts'), 'utf8');

function layer(selector: string): number {
  const at = CSS.indexOf(`\n${selector} {`);
  expect(at, `${selector} has no rule`).toBeGreaterThan(-1);
  const rule = CSS.slice(at, CSS.indexOf('}', at));
  const found = /z-index:\s*(\d+)/.exec(rule);
  expect(found, `${selector} sets no layer`).not.toBeNull();
  return Number(found![1]);
}

describe('the server activity drawer covers the floating buttons', () => {
  it('sits on the buttons’ own layer, not under it', () => {
    const drawer = layer('.sact-drawer');
    for (const button of ['.activity-float-btn', '.notif-bell-btn', '.helper-float-btn']) {
      expect(drawer, button).toBeGreaterThanOrEqual(layer(button));
    }
  });

  it('is added to the page after the buttons, so the same layer puts it on top', () => {
    // the buttons are in the page's own markup; the drawer is appended to the
    // end of the body the first time it opens
    for (const id of ['activity-float-btn', 'notif-bell-btn', 'helper-float-btn']) {
      expect(HTML).toContain(`id="${id}"`);
    }
    expect(HTML).not.toContain('class="sact-drawer"');
    expect(TS).toContain('document.body.appendChild(drawer)');
  });

  it('keeps its dimmed backdrop under the drawer, and the stop dialog over both', () => {
    expect(layer('.sact-scrim-bg')).toBeLessThan(layer('.sact-drawer'));
    expect(layer('.sact-stop-ov')).toBeGreaterThan(layer('.sact-drawer'));
  });
});
