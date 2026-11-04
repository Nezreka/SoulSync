import { describe, expect, it } from 'vitest';

import { libraryV2SearchSchema } from './-library-v2.types';

describe('Discovery artist entry', () => {
  it('keeps the header unset until the route applies the arrival preference', () => {
    expect(libraryV2SearchSchema.parse({ artist: 7, view: 'discovery' })).toMatchObject({
      view: 'discovery',
      releases: 'all',
      releaseView: 'cards',
    });
    expect(libraryV2SearchSchema.parse({ artist: 7, view: 'discovery' }).header).toBeUndefined();
  });
  it('keeps explicit display choices and ordinary library defaults', () => {
    // The route applies a preference on arrival; schema parsing must retain
    // whether a bookmark explicitly chose a header.
    expect(libraryV2SearchSchema.parse({ artist: 7 })).toMatchObject({
      releases: 'library',
      // the discovery tab opens on cards wherever the visit started
      releaseView: 'cards',
    });
    expect(libraryV2SearchSchema.parse({ view: 'discovery', releaseView: 'table' })).toMatchObject({
      releaseView: 'table',
    });
    expect(libraryV2SearchSchema.parse({ artist: 7, header: 'compact' }).header).toBe('compact');
  });
});
