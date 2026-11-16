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

/** settings.js's initSettingHelpLines over `root`, as the page runs it over
 *  everything at start (the parked vanilla panels included) */
export function withHelpLines(root: Element): Element {
  helpLines()(root);
  return root;
}

/**
 * settings.js's initSettingHelpLines, as the live page ran it over the vanilla
 * markup at start (frozen here when settings.js went, SETTINGS_PORT.md step
 * V4b: the fixtures are that page, and parts.tsx renders the same lines).
 */
function helpLines(): (root: Element) => void {
  type Icon = HTMLElement & { __helpBody?: Element };
  const firstLine = (text: string) => {
    const clean = (text || '').replace(/\s+/g, ' ').trim();
    if (!clean) return { line: '', rest: false };
    const match = clean.match(/^(.+?[.!?])(\s|$)/);
    let line = match ? match[1] : clean;
    if (line.length > 140) {
      const cut = line.slice(0, 140);
      line = cut.slice(0, Math.max(cut.lastIndexOf(' '), 80)).replace(/[,;:\s]+$/, '') + '…';
    }
    return { line, rest: line.length < clean.length };
  };
  const helpBody = (icon: Icon): Element | null => {
    if (icon.__helpBody) return icon.__helpBody;
    const row = icon.closest('.setting-row') || icon;
    let el = row.nextElementSibling;
    while (el && !el.classList.contains('setting-help-body')) el = el.nextElementSibling;
    if (!el) {
      const sib = icon.closest('.form-group')?.nextElementSibling ?? null;
      if (sib && sib.classList.contains('setting-help-body')) el = sib;
    }
    return el || null;
  };
  return (root) => {
    for (const icon of root.querySelectorAll<Icon>('.info-icon[onclick*="toggleSettingHelp"]')) {
      if (icon.dataset.helpLine) continue;
      icon.dataset.helpLine = '1';
      const group = icon.closest('.form-group');
      const body = group ? helpBody(icon) : null;
      if (!group || !body) continue;
      const { line, rest } = firstLine(body.textContent || '');
      if (!line) continue;
      const small = document.createElement('small');
      small.className = 'stg-help-line';
      const text = document.createElement('span');
      text.className = 'stg-help-text';
      text.textContent = line;
      small.append(text);
      group.append(small);
      if (rest) {
        icon.__helpBody = body;
        small.append(icon);
      } else icon.hidden = true;
    }
  };
}
