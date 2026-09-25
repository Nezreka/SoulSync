import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { formatNotify } from '../routes/automations/-automations.icons';

/**
 * ntfy and Gotify have to be pickable, configurable AND readable back.
 *
 * The builder needs three separate things per action type and they live in
 * three different places: the picker list, the config form, and the collector
 * that reads the fields back out. A type wired into only two of them looks
 * completely fine until you save and everything you typed is gone.
 */

const JS = readFileSync(resolve(process.cwd(), 'static/stats-automations.js'), 'utf8');

describe.each(['ntfy', 'gotify'])('%s in the automation builder', (type) => {
  it('is offered in the notifications group', () => {
    const group = JS.slice(JS.indexOf("group: 'Notifications'"), JS.indexOf("group: 'Chaining'"));
    expect(group).toContain(`type: '${type}'`);
  });

  it('renders a config form', () => {
    expect(JS).toContain(`if (blockType === '${type}')`);
  });

  it('has a collector, or everything typed into it is dropped on save', () => {
    const collectors = JS.slice(JS.indexOf("if (type === 'telegram') {"));
    expect(collectors).toContain(`if (type === '${type}') {`);
  });

  it('has a human label rather than falling through to the raw type', () => {
    expect(JS).toContain(`if (type === '${type}') return `);
  });
});

describe('the two services are not treated as interchangeable', () => {
  it('gives ntfy its own 1-5 priority and gotify its own 0-10', () => {
    const ntfy = JS.slice(JS.indexOf("if (blockType === 'ntfy')"), JS.indexOf("if (blockType === 'gotify')"));
    const gotify = JS.slice(JS.indexOf("if (blockType === 'gotify')"), JS.indexOf("if (blockType === 'webhook')"));
    // ntfy: a named 5-step scale
    expect(ntfy).toContain("'Urgent'");
    expect(ntfy).not.toContain('max="10"');
    // gotify: a real 0-10 number
    expect(gotify).toContain('max="10"');
    expect(gotify).toContain('min="0"');
  });

  it('asks gotify for an APP token, since a client token silently fails', () => {
    const gotify = JS.slice(JS.indexOf("if (blockType === 'gotify')"), JS.indexOf("if (blockType === 'webhook')"));
    expect(gotify).toContain('APP token');
  });

  it('does not trim the ntfy password, which may start or end with a space', () => {
    const coll = JS.slice(JS.indexOf("if (type === 'ntfy') {"), JS.indexOf("if (type === 'gotify') {"));
    expect(coll).toMatch(/password: document\.getElementById\([^)]*\)\?\.value \|\| ''/);
  });
});

describe('the react side names them too', () => {
  it('labels both instead of showing the raw action type', () => {
    expect(formatNotify('ntfy')).toBe('ntfy');
    expect(formatNotify('gotify')).toBe('Gotify');
  });
});
