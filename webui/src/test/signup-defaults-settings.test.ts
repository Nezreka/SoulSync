import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * Settings > Security > New sign-ups: the side a profile made by sign-in
 * starts on and its request limit. The server reads security.signup_sides,
 * security.signup_request_limit and security.signup_request_limit_days
 * (core/security/signup_defaults.py) and clamps them itself; these pin the
 * form fields to those keys in both directions, load and save.
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

describe('new sign-up settings', () => {
  it('the form offers the three sides the server accepts, music first', () => {
    const select = between(HTML, '<select id="security-signup-sides"', '</select>');
    const values = [...select.matchAll(/<option value="([^"]+)"/g)].map((m) => m[1]);
    expect(values).toEqual(['music', 'video', 'both']);
  });

  it('the side and the request limit are separate settings rows', () => {
    // a settings row is one label + one control on a line (#settings-page
    // .form-group is a flex row); two settings in one row put the second
    // label beside the first one's help text on a wide screen
    const side = between(
      HTML,
      '<div class="form-group" id="security-signup-defaults">',
      '</select>',
    );
    expect(side).not.toContain('security-signup-request-limit');
    const limit = between(
      HTML,
      '<div class="form-group" id="security-signup-request-limit-row">',
      'id="security-signup-request-limit-days"',
    );
    expect(limit).toContain('<label for="security-signup-request-limit">');
  });

  it('the limit fields carry the profile editor bounds', () => {
    expect(HTML).toMatch(/id="security-signup-request-limit" min="0"\s+max="1000"/);
    expect(HTML).toMatch(/id="security-signup-request-limit-days" min="1"\s+max="365"/);
  });

  it('save sends each field under its server key', () => {
    const security = between(SETTINGS, '        security: {', '        }\n    };');
    expect(security).toContain(
      "signup_sides: _cfgStr('security-signup-sides', { fallback: 'music' })",
    );
    expect(security).toContain("signup_request_limit: _cfgInt('security-signup-request-limit', 0)");
    expect(security).toContain(
      "signup_request_limit_days: _cfgInt('security-signup-request-limit-days', 7)",
    );
  });

  it('load fills each field from its server key, with the defaults when unset', () => {
    expect(SETTINGS).toContain(
      "signupSides.value = ['music', 'video', 'both'].includes(sides) ? sides : 'music';",
    );
    expect(SETTINGS).toContain('signupLimit.value = settings.security?.signup_request_limit ?? 0;');
    expect(SETTINGS).toContain(
      'signupDays.value = settings.security?.signup_request_limit_days ?? 7;',
    );
  });
});
