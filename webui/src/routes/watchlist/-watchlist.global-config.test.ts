import { describe, expect, it } from 'vitest';

import {
  EMPTY_GLOBAL_CONFIG,
  globalConfigIsSavable,
  includeEverythingChecked,
  setAllIncludes,
} from './-ui/watchlist-global-settings-modal';

describe('globalConfigIsSavable', () => {
  it('always allows a save while the override is off', () => {
    // With the override off these flags are inert, so an all-unchecked config
    // is legitimate. Blocking it here would make the modal impossible to close
    // via Save after unticking everything.
    const config = setAllIncludes({ ...EMPTY_GLOBAL_CONFIG }, false);
    expect(globalConfigIsSavable(config)).toBe(true);
  });

  it('requires at least one release type while the override is on', () => {
    const base = { ...setAllIncludes(EMPTY_GLOBAL_CONFIG, false), global_override_enabled: true };
    expect(globalConfigIsSavable(base)).toBe(false);
    expect(globalConfigIsSavable({ ...base, include_albums: true })).toBe(true);
    expect(globalConfigIsSavable({ ...base, include_eps: true })).toBe(true);
    expect(globalConfigIsSavable({ ...base, include_singles: true })).toBe(true);
  });

  it('does not count content filters as a release type', () => {
    // Saving with only "live" ticked would scan for nothing; the server rejects
    // it too, so the client must not send it.
    const config = {
      ...setAllIncludes(EMPTY_GLOBAL_CONFIG, false),
      global_override_enabled: true,
      include_live: true,
      include_remixes: true,
      include_instrumentals: true,
    };
    expect(globalConfigIsSavable(config)).toBe(false);
  });
});

describe('includeEverythingChecked / setAllIncludes', () => {
  it('is ticked only when all eight flags are on', () => {
    expect(includeEverythingChecked(EMPTY_GLOBAL_CONFIG)).toBe(false);
    expect(includeEverythingChecked(setAllIncludes(EMPTY_GLOBAL_CONFIG, true))).toBe(true);
  });

  it('is not ticked when a single flag is missing', () => {
    const nearly = { ...setAllIncludes(EMPTY_GLOBAL_CONFIG, true), include_instrumentals: false };
    expect(includeEverythingChecked(nearly)).toBe(false);
  });

  it('sets and clears every include flag without touching anything else', () => {
    const start = {
      ...EMPTY_GLOBAL_CONFIG,
      global_override_enabled: true,
      exclude_terms: 'demo, skit',
    };

    const on = setAllIncludes(start, true);
    expect(includeEverythingChecked(on)).toBe(true);
    expect(on.global_override_enabled).toBe(true);
    expect(on.exclude_terms).toBe('demo, skit');

    const off = setAllIncludes(on, false);
    expect(includeEverythingChecked(off)).toBe(false);
    expect(off.include_albums).toBe(false);
    expect(off.include_instrumentals).toBe(false);
    expect(off.exclude_terms).toBe('demo, skit');
  });

  it('does not mutate the config it was given', () => {
    const start = { ...EMPTY_GLOBAL_CONFIG };
    setAllIncludes(start, true);
    expect(start.include_live).toBe(false);
  });
});

describe('#1450 edition preference defaults', () => {
  it('EMPTY_GLOBAL_CONFIG carries the new keys at their server defaults', () => {
    expect(EMPTY_GLOBAL_CONFIG.edition_preference).toBe('all');
    expect(EMPTY_GLOBAL_CONFIG.prefer_explicit_edition).toBe(true);
  });

  it('setAllIncludes leaves the edition keys untouched', () => {
    const on = setAllIncludes(
      {
        ...EMPTY_GLOBAL_CONFIG,
        edition_preference: 'one_standard',
        prefer_explicit_edition: false,
      },
      true,
    );
    expect(on.edition_preference).toBe('one_standard');
    expect(on.prefer_explicit_edition).toBe(false);
  });
});

describe('#1450 withEditionDefaults', () => {
  it('fills the new keys for a stale server response that predates them', async () => {
    const { withEditionDefaults } = await import('./-watchlist.api');
    const merged = withEditionDefaults({
      ...EMPTY_GLOBAL_CONFIG,
      edition_preference: undefined as never,
      prefer_explicit_edition: undefined as never,
    });
    expect(merged.edition_preference).toBe('all');
    expect(merged.prefer_explicit_edition).toBe(true);
  });

  it('keeps the server values and coerces an out-of-enum value to "all"', async () => {
    const { withEditionDefaults } = await import('./-watchlist.api');
    const kept = withEditionDefaults({
      ...EMPTY_GLOBAL_CONFIG,
      edition_preference: 'one_complete',
      prefer_explicit_edition: false,
    });
    expect(kept.edition_preference).toBe('one_complete');
    expect(kept.prefer_explicit_edition).toBe(false);
    const coerced = withEditionDefaults({
      ...EMPTY_GLOBAL_CONFIG,
      edition_preference: 'bogus' as never,
    });
    expect(coerced.edition_preference).toBe('all');
  });
});
