import { renderHook } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { useDiscographyFilters } from './library-v2-page';

describe('discography filters follow the provider (review 4 R4-E-2)', () => {
  it('one provider artist to another in place re-applies the MusicBrainz declutter', () => {
    const { result, rerender } = renderHook(({ source }) => useDiscographyFilters(source), {
      initialProps: { source: 'deezer' as string | null },
    });
    expect(result.current.filters.mbDeclutter).toBeFalsy();
    rerender({ source: 'musicbrainz' });
    expect(result.current.filters.mbDeclutter).toBe(true);
    expect(result.current.filters.categories.live).toBe(false);
    rerender({ source: 'deezer' });
    expect(result.current.filters.mbDeclutter).toBeFalsy();
    expect(result.current.filters.categories.live).toBe(true);
  });
});
