import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * Settings > Security > Sign in with OIDC: the provider's settings (and the
 * admin's connect row inside them) show only while the toggle is on. Hidden,
 * not removed: the fields keep their values and still save. Runs the real
 * syncOidcProviderBlock from settings.js against a stub page.
 */

const read = (p: string) => readFileSync(resolve(process.cwd(), p), 'utf8').replace(/\r\n/g, '\n');

const HTML = read('index.html');
const SETTINGS = read('static/settings.js');

function between(src: string, start: string, end: string): string {
  const a = src.indexOf(start);
  expect(a, `marker not found: ${start}`).toBeGreaterThan(-1);
  const b = src.indexOf(end, a);
  expect(b, `end marker not found: ${end}`).toBeGreaterThan(-1);
  return src.slice(a, b);
}

function blockHiddenWhen(checked: boolean | null): boolean {
  const fn = between(SETTINGS, 'function syncOidcProviderBlock(', 'window.syncOidcProviderBlock');
  const toggle = checked === null ? null : { checked };
  const block = { hidden: false };
  const document = {
    getElementById: (id: string) =>
      id === 'security-oidc-signin' ? toggle : id === 'security-oidc-provider' ? block : null,
  };
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  new Function('document', `${fn}\nsyncOidcProviderBlock();`)(document);
  return block.hidden;
}

describe('the oidc provider settings follow their toggle', () => {
  it('hides the provider block while sign in with oidc is off', () => {
    expect(blockHiddenWhen(false)).toBe(true);
  });

  it('shows it while sign in with oidc is on', () => {
    expect(blockHiddenWhen(true)).toBe(false);
  });

  it('hides it when the toggle is missing', () => {
    expect(blockHiddenWhen(null)).toBe(true);
  });

  it('runs when the toggle changes and when settings load', () => {
    expect(HTML).toMatch(/id="security-oidc-signin"\s+onchange="syncOidcProviderBlock\(\)"/);
    const load = between(
      SETTINGS,
      "const oidcOn = document.getElementById('security-oidc-signin');",
      'for (const [id, key] of [',
    );
    expect(load).toContain('syncOidcProviderBlock();');
  });

  it('the block it hides is the one holding the provider fields and the admin row', () => {
    const block = between(HTML, 'id="security-oidc-provider"', 'id="security-signup-defaults"');
    for (const id of [
      'security-oidc-issuer',
      'security-oidc-client-secret',
      'security-oidc-admin',
    ]) {
      expect(block).toContain(`id="${id}"`);
    }
  });
});
