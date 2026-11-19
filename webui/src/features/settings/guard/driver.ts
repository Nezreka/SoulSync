/**
 * How the guard finds, reads and changes one form control, by kind. Nothing
 * here knows a class name, a tab or a section: a control is found by its id,
 * or by its own name / data-* attributes when it has no id.
 */

import { fireEvent } from '@testing-library/react';

export const CONTROL_SELECTOR =
  'input, select, textarea, [role="switch"], [role="radiogroup"], [role="slider"], [contenteditable]:not([contenteditable="false"])';
export const BUTTON_SELECTOR =
  'button, input[type="button"], input[type="submit"], input[type="reset"]';

export type Kind =
  | 'checkbox'
  | 'radio'
  | 'select'
  | 'text'
  | 'number'
  | 'range'
  | 'color'
  | 'date'
  | 'hidden'
  | 'file'
  | 'radiogroup'
  | 'switch'
  | 'slider'
  | 'editable';

export type Value = string | boolean | null;

/** how a control is found again. `weak` says why the locator is not the
 *  control's own id, name or data-* attributes:
 *   row      = its own data-* only tell it apart inside a row, found by the
 *              row's id or data-*
 *   context  = it needs an aria-label, a placeholder or its type
 *   position = it needs its place among look-alikes */
export interface Locator {
  sel: string;
  nth?: number;
  weak?: 'row' | 'context' | 'position';
}

// attributes that are not identity: what the password managers are told,
// what the shell marks, and state
const IGNORED_ATTRS = new Set([
  'data-bwignore',
  'data-1p-ignore',
  'data-lpignore',
  'data-redact-wired',
  'data-was-redacted',
  'data-state',
  'data-testid',
]);
// wrappers that are the page's structure, which the redesign moves
const STRUCTURE_ATTRS =
  /^data-(stg|stg-react|stg-part|music-only|video-only|svc-side|react-panel|people)$/;

const quote = (value: string) => `"${value.replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`;
const idSelector = (id: string) => (/^[A-Za-z_][\w-]*$/.test(id) ? `#${id}` : `[id=${quote(id)}]`);

function dataAttrs(el: Element): string {
  return [...el.attributes]
    .filter(
      (a) =>
        a.name.startsWith('data-') && !IGNORED_ATTRS.has(a.name) && !STRUCTURE_ATTRS.test(a.name),
    )
    .map((a) => (a.value === '' ? `[${a.name}]` : `[${a.name}=${quote(a.value)}]`))
    .join('');
}

function ownSelector(el: Element): { strong: string; soft: string } {
  const tag = el.tagName.toLowerCase();
  const name = el.getAttribute('name');
  let strong = dataAttrs(el);
  if (name) {
    strong += `[name=${quote(name)}]`;
    const type = el.getAttribute('type');
    if ((type === 'radio' || type === 'checkbox') && el.hasAttribute('value'))
      strong += `[value=${quote(el.getAttribute('value') ?? '')}]`;
  }
  let soft = strong;
  for (const attr of ['type', 'role', 'aria-label', 'placeholder', 'list']) {
    const value = el.getAttribute(attr);
    if (value) soft += `[${attr}=${quote(value)}]`;
  }
  return { strong: strong ? tag + strong : '', soft: tag + soft };
}

const SIMPLE_ID = /^#[A-Za-z_][\w-]*$/;

/** how many of `among` the selector finds, stopping at two */
function count(among: Element[], sel: string): number {
  let n = 0;
  for (const el of among) {
    if (el.matches(sel)) n += 1;
    if (n >= 2) break;
  }
  return n;
}

/** a locator for every control in `all`, each unique among them */
export function locateAll(root: ParentNode, all: Element[]): Locator[] {
  const ids = new Map<string, number>();
  const byTag = new Map<string, Element[]>();
  for (const el of all) {
    if (el.id) ids.set(el.id, (ids.get(el.id) ?? 0) + 1);
    const list = byTag.get(el.tagName);
    if (list) list.push(el);
    else byTag.set(el.tagName, [el]);
  }
  return all.map((el) => {
    if (el.id && ids.get(el.id) === 1) return { sel: idSelector(el.id) };
    // the selectors below start with the tag, so only its own kind can match
    const peers = byTag.get(el.tagName) ?? [];
    const { strong, soft } = ownSelector(el);
    if (strong && count(peers, strong) === 1) return { sel: strong };
    if (count(peers, soft) === 1) return { sel: soft, weak: 'context' };
    // the nearest ancestors that have an id or data-* of their own
    const anchors: string[] = [];
    for (
      let anc = el.parentElement;
      anc && anc !== root && anchors.length < 3;
      anc = anc.parentElement
    ) {
      const own = anc.id ? idSelector(anc.id) : dataAttrs(anc);
      if (!own) continue;
      anchors.unshift(own);
      const within = anchors.join(' ');
      if (strong && count(peers, `${within} ${strong}`) === 1)
        return { sel: `${within} ${strong}`, weak: 'row' };
      if (count(peers, `${within} ${soft}`) === 1)
        return { sel: `${within} ${soft}`, weak: 'context' };
    }
    const sel = anchors.length ? `${anchors.join(' ')} ${soft}` : soft;
    return { sel, nth: peers.filter((other) => other.matches(sel)).indexOf(el), weak: 'position' };
  });
}

export const keyOf = (loc: Locator) => (loc.nth === undefined ? loc.sel : `${loc.sel} |${loc.nth}`);

/** every control the selector finds, among the page's controls */
export function findAll(root: ParentNode, sel: string, all?: HTMLElement[]): HTMLElement[] {
  if (SIMPLE_ID.test(sel)) {
    const el = document.getElementById(sel.slice(1));
    return el && (root as Node).contains(el) ? [el] : [];
  }
  return (all ?? controlsIn(root)).filter((el) => el.matches(sel));
}

export function find(root: ParentNode, loc: Locator, all?: HTMLElement[]): HTMLElement | null {
  return findAll(root, loc.sel, all)[loc.nth ?? 0] ?? null;
}

export function kindOf(el: Element): Kind {
  const tag = el.tagName.toLowerCase();
  const role = el.getAttribute('role');
  if (tag === 'select') return 'select';
  if (tag === 'textarea') return 'text';
  if (tag === 'input') {
    const type = (el.getAttribute('type') ?? 'text').toLowerCase();
    if (type === 'checkbox') return 'checkbox';
    if (
      type === 'radio' ||
      type === 'number' ||
      type === 'range' ||
      type === 'color' ||
      type === 'hidden' ||
      type === 'file'
    )
      return type;
    if (type === 'date' || type === 'time' || type === 'datetime-local') return 'date';
    return 'text';
  }
  if (role === 'switch') return 'switch';
  if (role === 'radiogroup') return 'radiogroup';
  if (role === 'slider') return 'slider';
  return 'editable';
}

const radios = (el: Element) => [...el.querySelectorAll<HTMLElement>('[role="radio"]')];
const radioName = (el: Element) =>
  el.getAttribute('data-value') ??
  el.getAttribute('value') ??
  el.getAttribute('aria-label') ??
  (el.textContent ?? '').trim();

export function read(el: Element): Value {
  switch (kindOf(el)) {
    case 'checkbox':
    case 'radio':
      return (el as HTMLInputElement).checked;
    case 'switch':
      return el.getAttribute('aria-checked') === 'true';
    case 'radiogroup': {
      const on = radios(el).find((r) => r.getAttribute('aria-checked') === 'true');
      return on ? radioName(on) : null;
    }
    case 'slider':
      return el.getAttribute('aria-valuenow');
    case 'editable':
      return el.textContent;
    default:
      return (el as HTMLInputElement).value;
  }
}

export const isDisabled = (el: Element) =>
  (el as HTMLInputElement).disabled === true ||
  (el as HTMLInputElement).readOnly === true ||
  el.getAttribute('aria-disabled') === 'true';

function otherNumber(el: HTMLInputElement): string | null {
  const num = (raw: string | null) => (raw === null || raw === '' ? null : Number(raw));
  const min = num(el.getAttribute('min'));
  const max = num(el.getAttribute('max'));
  const stepRaw = el.getAttribute('step');
  const step = stepRaw && stepRaw !== 'any' && Number(stepRaw) > 0 ? Number(stepRaw) : 1;
  const current = num(el.value) ?? num(el.getAttribute('placeholder')) ?? min ?? 0;
  if (Number.isNaN(current)) return String((min ?? 0) + step);
  const round = (n: number) => String(Number(n.toFixed(6)));
  if (max === null || current + step <= max) return round(current + step);
  if (min === null || current - step >= min) return round(current - step);
  return null;
}

function otherText(el: HTMLInputElement | HTMLTextAreaElement): string {
  const current = el.value;
  const type = (el.getAttribute('type') ?? 'text').toLowerCase();
  const hint = `${current} ${el.getAttribute('placeholder') ?? ''}`.trim();
  if (type === 'password') return 'guard-secret';
  if (type === 'email') return 'guard@guard.example';
  if (/^wss?:\/\//.test(hint)) return 'ws://guard.example:4321';
  if (type === 'url' || /^https?:\/\//.test(hint)) return 'http://guard.example:4321';
  if (/^-?\d+(\.\d+)?$/.test(current)) return String(Number(current) + 1);
  return current ? `${current}-guard` : 'guard-probe';
}

/** the deterministic other value a probe gives the control, or null when it
 *  has none (a select with one option, a number field boxed in) */
export function otherValue(el: Element): Value {
  switch (kindOf(el)) {
    case 'checkbox':
    case 'switch':
      return !read(el);
    case 'radio':
      return true;
    case 'select': {
      const select = el as HTMLSelectElement;
      const option = [...select.options].find((o) => !o.disabled && o.value !== select.value);
      return option ? option.value : null;
    }
    case 'number':
    case 'range':
      return otherNumber(el as HTMLInputElement);
    case 'color':
      return (el as HTMLInputElement).value.toLowerCase() === '#123456' ? '#654321' : '#123456';
    case 'date':
      return (el as HTMLInputElement).value === '2026-01-02' ? '2026-01-03' : '2026-01-02';
    case 'text':
      return otherText(el as HTMLInputElement);
    case 'radiogroup': {
      const off = radios(el).find((r) => r.getAttribute('aria-checked') !== 'true');
      return off ? radioName(off) : null;
    }
    default:
      return null;
  }
}

/** change the control the way a person does: the events a browser fires for
 *  it, the `change` and the blur included (some sections save on those, see
 *  tabs/commit.ts) */
export function change(el: HTMLElement, to: Value) {
  switch (kindOf(el)) {
    case 'checkbox':
    case 'switch':
      if (read(el) !== to) fireEvent.click(el);
      return;
    case 'radio':
      fireEvent.click(el);
      return;
    case 'radiogroup': {
      const target = radios(el).find((r) => radioName(r) === to);
      if (target) fireEvent.click(target);
      return;
    }
    case 'select':
      fireEvent.focus(el);
      fireEvent.input(el, { target: { value: to } });
      fireEvent.change(el);
      fireEvent.blur(el);
      return;
    case 'text':
    case 'number':
    case 'range':
    case 'color':
    case 'date':
      fireEvent.focus(el);
      fireEvent.input(el, { target: { value: to } });
      fireEvent.change(el);
      fireEvent.blur(el);
      return;
    default:
  }
}

/** every control on the page, in document order */
export const controlsIn = (root: ParentNode) => [
  ...root.querySelectorAll<HTMLElement>(CONTROL_SELECTOR),
];

/** what every control is and shows now, cheap enough to take after each
 *  probe: a page back at rest has the same controls, with the same
 *  attributes (a row's name is in its fields' labels), showing the same */
export function snapshot(root: ParentNode): string[] {
  return controlsIn(root).map((el) => {
    let mark = el.tagName;
    for (const attr of el.attributes)
      if (attr.name !== 'class' && attr.name !== 'style' && attr.name !== 'value')
        mark += ` ${attr.name}=${attr.value}`;
    return `${mark} -> ${String(read(el))}`;
  });
}

export interface ButtonMark {
  /** the button's id, when it has one */
  id?: string;
  /** else its words and the nearest ancestor that has an id */
  label?: string;
  anchor?: string;
}

export function markButton(root: Element, el: Element): ButtonMark {
  if (el.id) return { id: el.id };
  const words =
    el.getAttribute('aria-label') ||
    (el.textContent ?? '').replace(/\s+/g, ' ').trim() ||
    el.getAttribute('title') ||
    (el as HTMLInputElement).value ||
    '';
  let anchor = '';
  for (let anc = el.parentElement; anc && anc !== root; anc = anc.parentElement) {
    if (anc.id) {
      anchor = anc.id;
      break;
    }
  }
  return { label: words.slice(0, 80), anchor };
}

export const buttonKey = (mark: ButtonMark) =>
  mark.id ? `#${mark.id}` : `${mark.anchor ? `#${mark.anchor} ` : ''}"${mark.label}"`;
