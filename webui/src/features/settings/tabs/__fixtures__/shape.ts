/**
 * Compares a ported tab with the markup it replaced (SETTINGS_PORT.md). Every
 * element as tag, id, classes and the attributes the page's css and scripts
 * key on, in document order, plus the words a person reads.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

const ATTRS = [
  'data-stg',
  'data-level',
  'type',
  'placeholder',
  'for',
  'role',
  'tabindex',
  'value',
  'title',
];

export function shape(root: Element) {
  const elements = [...root.querySelectorAll('*')].map((el) => {
    const attrs = ATTRS.filter((name) => el.tagName !== 'INPUT' || name !== 'value')
      .map((name) => [name, el.getAttribute(name)] as const)
      .filter(([, value]) => value !== null)
      .map(([name, value]) => `[${name}=${value}]`)
      .join('');
    // state classes the vanilla markup set from script, not in the html
    const classes = [...el.classList]
      .filter((c) => c !== 'setting-overridden')
      .sort()
      .join('.');
    return `${el.tagName.toLowerCase()}${el.id ? '#' + el.id : ''}${classes ? '.' + classes : ''}${attrs}`;
  });
  // the words, whatever the whitespace between the tags
  const text = (root.textContent || '').replace(/\s+/g, '');
  return { elements, text };
}

/**
 * The frozen vanilla markup, parsed. `afterInit` runs settings.js's own
 * initSettingHelpLines over it first: that is what the live page looked like,
 * with the first line of each help under its setting.
 */
export function vanilla(name: string, { afterInit = false } = {}): Element {
  const host = document.createElement('div');
  host.innerHTML = readFileSync(
    resolve(process.cwd(), 'src/features/settings/tabs/__fixtures__', name),
    'utf8',
  );
  if (afterInit) helpLines()(host);
  return host;
}

function helpLines(): (root: Element) => void {
  const source = readFileSync(resolve(process.cwd(), 'static/settings.js'), 'utf8');
  const fn = (name: string) => {
    const start = source.indexOf(`function ${name}(`);
    let depth = 0;
    for (let i = source.indexOf('{', start); i < source.length; i += 1) {
      if (source[i] === '{') depth += 1;
      if (source[i] === '}' && (depth -= 1) === 0) return source.slice(start, i + 1);
    }
    throw new Error(`${name} not found in settings.js`);
  };
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  return new Function(
    'document',
    `${fn('settingHelpFirstLine')}\n${fn('_settingHelpBody')}\n${fn('initSettingHelpLines')}\nreturn initSettingHelpLines;`,
  )(document) as (root: Element) => void;
}
