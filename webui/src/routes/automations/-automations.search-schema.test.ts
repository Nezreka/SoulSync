import { describe, expect, it } from 'vitest';

import { automationsSearchSchema } from './-automations.types';

/**
 * The pre-overhaul page linked `?health=failing|never|off` from its verdict
 * strip. Those bookmarks must land on the smart collection that replaced the
 * strip instead of being silently dropped.
 */
describe('automations search schema legacy health param', () => {
  it('maps health=failing to the Needs attention collection', () => {
    expect(automationsSearchSchema.parse({ health: 'failing' })).toMatchObject({
      view: 'library',
      nav: 'attention',
    });
  });

  it('maps health=never to the Needs attention collection', () => {
    expect(automationsSearchSchema.parse({ health: 'never' })).toMatchObject({
      view: 'library',
      nav: 'attention',
    });
  });

  it('maps health=off to the Switched off collection', () => {
    expect(automationsSearchSchema.parse({ health: 'off' })).toMatchObject({
      view: 'library',
      nav: 'off',
    });
  });

  it('leaves explicit new-style view/nav alone when health is also present', () => {
    expect(
      automationsSearchSchema.parse({ view: 'library', nav: 'scheduled', health: 'failing' }),
    ).toMatchObject({ view: 'library', nav: 'scheduled' });
  });

  it('ignores an unknown health value', () => {
    expect(automationsSearchSchema.parse({ health: 'bogus' })).toMatchObject({
      view: 'overview',
      nav: 'all',
    });
  });

  it('never emits health back into the parsed search', () => {
    const parsed = automationsSearchSchema.parse({ health: 'failing' });
    expect('health' in parsed).toBe(false);
  });
});
