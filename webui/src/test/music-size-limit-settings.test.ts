/* Execute the local classic settings script against the test DOM. */
/* eslint-disable @typescript-eslint/no-implied-eval */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, expect, it } from 'vitest';

const source = readFileSync(resolve(process.cwd(), 'static/settings.js'), 'utf8');
const markup = readFileSync(resolve(process.cwd(), 'index.html'), 'utf8');
const helper = source.slice(
  source.indexOf('function _cfgFloat('),
  source.indexOf('// Same rule for numbers.'),
);
const expression = source.match(/max_mb_per_minute: (\(\(\) => \{[\s\S]*?\}\)\(\)),/)?.[1];
const load = source.match(/const _sizeCap = [^;]+;\s*if \(_sizeCap\) _sizeCap.value = [^;]+;/)?.[0];

afterEach(() => {
  document.body.innerHTML = '';
});

it.each(['0', '10', '0.5'])('loads and saves the size cap %s', (value) => {
  const input = markup.match(/<input type="number" id="music-max-mb-per-minute"[\s\S]*?>/)?.[0];
  expect(input).toBeTruthy();
  expect(expression).toBeTruthy();
  expect(load).toBeTruthy();
  document.body.innerHTML = input!;
  new Function('document', 'settings', load!)(document, {
    download_source: { max_mb_per_minute: Number(value) },
  });
  expect((document.getElementById('music-max-mb-per-minute') as HTMLInputElement).value).toBe(
    value,
  );
  expect(new Function('document', `${helper}; return ${expression}`)(document)).toBe(Number(value));
});

it('omits the setting when the field is absent instead of overwriting it', () => {
  const value = new Function('document', `${helper}; return ${expression}`)(document);
  expect(JSON.stringify({ max_mb_per_minute: value })).toBe('{}');
});
