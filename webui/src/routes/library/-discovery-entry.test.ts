import { describe, expect, it } from 'vitest';

import { libraryV2SearchSchema } from './-library-v2.types';

describe('Discovery artist entry', () => {
  it('opens rich cards and the complete discography', () => {
    expect(libraryV2SearchSchema.parse({ artist: 7, view: 'discovery' })).toMatchObject({
      view: 'discovery',
      header: 'rich',
      releases: 'all',
      releaseView: 'cards',
    });
  });
  it('keeps explicit display choices and ordinary library defaults', () => {
    expect(libraryV2SearchSchema.parse({ artist: 7 })).toMatchObject({
      header: 'compact',
      releases: 'library',
      // the discovery tab opens on cards wherever the visit started
      releaseView: 'cards',
    });
    expect(libraryV2SearchSchema.parse({ view: 'discovery', releaseView: 'table' })).toMatchObject({
      header: 'rich',
      releaseView: 'table',
    });
  });
});
