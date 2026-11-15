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

/** the frozen vanilla markup, parsed */
export function vanilla(name: string): Element {
  const host = document.createElement('div');
  host.innerHTML = readFileSync(
    resolve(process.cwd(), 'src/features/settings/tabs/__fixtures__', name),
    'utf8',
  );
  return host;
}
