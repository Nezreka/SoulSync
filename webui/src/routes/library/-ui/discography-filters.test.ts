import { describe, expect, it } from 'vitest';

import {
  classifyReleaseContent,
  DEFAULT_DISCOGRAPHY_FILTERS,
  passesDiscographyFilters,
  type DiscographyFilterState,
} from './discography-filters';

function filters(patch: Partial<DiscographyFilterState>): DiscographyFilterState {
  return { ...DEFAULT_DISCOGRAPHY_FILTERS, ...patch };
}

describe('classifyReleaseContent (ldp-04, ported from library.js #877)', () => {
  it('detects live releases from the title in every legacy spelling', () => {
    expect(classifyReleaseContent({ title: 'Live at Wembley' }).isLive).toBe(true);
    expect(classifyReleaseContent({ title: 'Nevermind (Live in Rome)' }).isLive).toBe(true);
    expect(classifyReleaseContent({ title: 'Nevermind [Live]' }).isLive).toBe(true);
    expect(classifyReleaseContent({ title: 'Alive' }).isLive).toBe(false);
  });

  it('treats the compilation album type as authoritative on top of the title', () => {
    expect(classifyReleaseContent({ title: 'Greatest Hits' }).isCompilation).toBe(true);
    expect(
      classifyReleaseContent({ title: 'Untitled', album_type: 'compilation' }).isCompilation,
    ).toBe(true);
    expect(classifyReleaseContent({ title: 'Untitled', album_type: 'album' }).isCompilation).toBe(
      false,
    );
  });

  it('reads either title or name, since the two payload shapes differ', () => {
    expect(classifyReleaseContent({ name: 'Song (feat. Someone)' }).isFeatured).toBe(true);
  });
});

describe('passesDiscographyFilters', () => {
  it('keeps everything under the default show-all state', () => {
    expect(
      passesDiscographyFilters({ title: 'Live at Wembley' }, DEFAULT_DISCOGRAPHY_FILTERS, false),
    ).toBe(true);
  });

  it('hides each content type when its Include toggle is off', () => {
    const noLive = filters({ content: { live: false, compilations: true, featured: true } });
    expect(passesDiscographyFilters({ title: 'Live at Wembley' }, noLive, true)).toBe(false);
    expect(passesDiscographyFilters({ title: 'Studio Record' }, noLive, true)).toBe(true);
  });

  it('applies the single-select ownership status', () => {
    const owned = filters({ ownership: 'owned' });
    const missing = filters({ ownership: 'missing' });
    expect(passesDiscographyFilters({ title: 'A' }, owned, true)).toBe(true);
    expect(passesDiscographyFilters({ title: 'A' }, owned, false)).toBe(false);
    expect(passesDiscographyFilters({ title: 'A' }, missing, false)).toBe(true);
    expect(passesDiscographyFilters({ title: 'A' }, missing, true)).toBe(false);
  });

  it('never hides a release whose ownership is still undetermined', () => {
    expect(passesDiscographyFilters({ title: 'A' }, filters({ ownership: 'owned' }), null)).toBe(
      true,
    );
  });
});
