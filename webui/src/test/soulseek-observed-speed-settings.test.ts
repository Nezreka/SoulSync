/* Execute the local classic settings script against the test DOM. */
/* eslint-disable @typescript-eslint/no-implied-eval */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, expect, it } from 'vitest';

const source = readFileSync(resolve(process.cwd(), 'static/settings.js'), 'utf8');
const markup = readFileSync(resolve(process.cwd(), 'index.html'), 'utf8');
const helper = source.slice(
  source.indexOf('function _cfgInt('),
  source.indexOf('function _cfgBool('),
);
const expression = source.match(/min_observed_download_speed_kbps: (_cfgInt\([^\n]+\)),/)?.[1];
const load = source.match(
  /document\.getElementById\('soulseek-min-observed-download-speed'\)\.value = [^;]+;/,
)?.[0];
const enabledExpression = source.match(
  /observed_speed_fallback_enabled: (document\.getElementById\('soulseek-observed-speed-fallback-enabled'\)\.checked),/,
)?.[1];
const enabledLoad = source.match(
  /document\.getElementById\('soulseek-observed-speed-fallback-enabled'\)\.checked = [^;]+;/,
)?.[0];

afterEach(() => {
  document.body.innerHTML = '';
});

it.each(['0', '250', '1200'])('loads and saves the observed-speed minimum %s', (value) => {
  const input = markup.match(
    /<input type="number" id="soulseek-min-observed-download-speed"[\s\S]*?>/,
  )?.[0];
  expect(input).toBeTruthy();
  expect(expression).toBeTruthy();
  expect(load).toBeTruthy();
  document.body.innerHTML = input!;

  new Function('document', 'settings', load!)(document, {
    soulseek: { min_observed_download_speed_kbps: Number(value) },
  });

  expect(
    (document.getElementById('soulseek-min-observed-download-speed') as HTMLInputElement).value,
  ).toBe(value);
  expect(new Function('document', `${helper}; return ${expression}`)(document)).toBe(Number(value));
});

it.each([true, false])('loads and saves observed-speed enabled=%s', (enabled) => {
  const input = markup.match(
    /<input type="checkbox" id="soulseek-observed-speed-fallback-enabled"[^>]*>/,
  )?.[0];
  expect(input).toBeTruthy();
  expect(enabledExpression).toBeTruthy();
  expect(enabledLoad).toBeTruthy();
  document.body.innerHTML = input!;

  new Function('document', 'settings', enabledLoad!)(document, {
    soulseek: { observed_speed_fallback_enabled: enabled },
  });

  expect(new Function('document', `return ${enabledExpression}`)(document)).toBe(enabled);
});

it('omits the setting when the field is absent instead of overwriting it', () => {
  const value = new Function('document', `${helper}; return ${expression}`)(document);
  expect(JSON.stringify({ min_observed_download_speed_kbps: value })).toBe('{}');
});
