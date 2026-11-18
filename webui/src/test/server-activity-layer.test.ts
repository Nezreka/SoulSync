import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * The Server Activity drawer and the floating buttons (#2090).
 *
 * The activity, notification and help buttons float at the bottom right on
 * the top layer. The drawer slides in over that same corner, so one layer
 * lower the three buttons sat on top of its playback cards. It is on the
 * buttons' layer, and its root is added to the page after them, so it covers
 * them while it is open. The drawer is React now (features/server-activity).
 */

const read = (p: string) => readFileSync(resolve(process.cwd(), p), 'utf8');
const CSS = read('static/style.css');
const TOKENS = read('static/tokens.css');
const DRAWER = read('src/features/server-activity/drawer.module.css');
const HOST = read('src/features/server-activity/host.tsx');
const HTML = read('index.html');

function layer(selector: string): number {
  const at = CSS.indexOf(`\n${selector} {`);
  expect(at, `${selector} has no rule`).toBeGreaterThan(-1);
  const rule = CSS.slice(at, CSS.indexOf('}', at));
  const found = /z-index:\s*(?:(\d+)|var\((--z-[\w-]+)\))/.exec(rule);
  expect(found, `${selector} sets no layer`).not.toBeNull();
  return found![1] ? Number(found![1]) : token(found![2]);
}

function token(name: string): number {
  const found = new RegExp(`${name}:\\s*(\\d+)`).exec(TOKENS);
  expect(found, `${name} is not a token`).not.toBeNull();
  return Number(found![1]);
}

function moduleLayer(selector: string): number {
  const at = DRAWER.indexOf(`\n${selector} {`);
  expect(at, `${selector} has no rule`).toBeGreaterThan(-1);
  const rule = DRAWER.slice(at, DRAWER.indexOf('}', at));
  const found = /z-index:\s*var\((--z-[\w-]+)\)/.exec(rule);
  expect(found, `${selector} sets no layer`).not.toBeNull();
  return token(found![1]);
}

describe('the server activity drawer covers the floating buttons', () => {
  it('sits on the buttons’ own layer, not under it', () => {
    const drawer = moduleLayer('.root');
    for (const button of ['.activity-float-btn', '.notif-bell-btn', '.helper-float-btn']) {
      expect(drawer, button).toBeGreaterThanOrEqual(layer(button));
    }
  });

  it('is added to the page after the buttons, so the same layer puts it on top', () => {
    for (const id of ['activity-float-btn', 'notif-bell-btn', 'helper-float-btn']) {
      expect(HTML).toContain(`id="${id}"`);
    }
    expect(HTML).not.toContain('soulsync-server-activity-root');
    expect(HOST).toContain('document.body.appendChild(el)');
  });

  it('keeps its dimmed backdrop under the drawer panel, inside its own layer', () => {
    const local = (selector: string) => {
      const at = DRAWER.indexOf(`\n${selector} {`);
      const rule = DRAWER.slice(at, DRAWER.indexOf('}', at));
      return Number(/z-index:\s*(\d)\s*;/.exec(rule)![1]);
    };
    expect(local('.scrim')).toBeLessThan(local('.drawer'));
  });

  it('the old string-built drawer and its css are gone', () => {
    expect(CSS).not.toMatch(/\.sact-/);
  });
});
