/**
 * The draft resolver must land where core.permissions lands: the fixture's
 * effective maps were computed by the server code, so the client's own
 * clamp/resolve is checked against them row by row.
 */

import { describe, expect, it } from 'vitest';

import type { Catalogue, ProfilePermissions, QualityOptions, Template } from './types';

import FIX from './__fixtures__/permissions.json';
import {
  buildModel,
  initialDraft,
  resolve,
  saveBody,
  setValue,
  switchTemplate,
  unsavedCount,
} from './model';

const model = (p: unknown) =>
  buildModel(
    FIX.catalogue as unknown as Catalogue,
    FIX.templates.templates as unknown as Template[],
    p as ProfilePermissions,
    FIX.quality as unknown as QualityOptions,
  );

describe('the draft resolves like the server', () => {
  it.each([
    ['maya', FIX.maya],
    ['legacy', FIX.legacy],
    ['admin', FIX.admin],
  ])('%s: every row matches core.permissions', (_name, payload) => {
    const m = model(payload);
    const { effective } = resolve(m, initialDraft(payload as unknown as ProfilePermissions));
    expect(effective).toEqual(payload.effective);
  });

  it('nothing to save until something changes, and a change back is nothing', () => {
    const m = model(FIX.maya);
    const d = initialDraft(m.saved);
    expect(saveBody(m, d)).toBeNull();
    expect(unsavedCount(m, d)).toBe(0);
    const changed = setValue(m, d, 'tv.get', 'download');
    expect(saveBody(m, changed)).toEqual({ set: { 'tv.get': 'download' }, reset: [] });
    const back = setValue(m, changed, 'tv.get', 'ask');
    expect(saveBody(m, back)).toBeNull();
  });

  it('a change the policy holds down is kept, not dropped', () => {
    const m = model(FIX.maya);
    const d = setValue(m, initialDraft(m.saved), 'requests.see_all', true);
    const { raw, effective } = resolve(m, d);
    expect(raw['requests.see_all']).toBe(true);
    expect(effective['requests.see_all']).toBe(false);
    expect(saveBody(m, d)).toEqual({ set: { 'requests.see_all': true }, reset: [] });
  });

  it('a template switch keeps only the changes that still differ', () => {
    const m = model(FIX.maya);
    const d = switchTemplate(m, initialDraft(m.saved), 1);
    expect(Object.keys(d.overrides).sort()).toEqual([
      'movies.quality',
      'movies.quality_default',
      'requests.limit',
    ]);
    // the server keeps the same three through the switch, so nothing else moves
    expect(saveBody(m, d)).toEqual({ template_id: 1, set: {}, reset: [] });
  });
});
