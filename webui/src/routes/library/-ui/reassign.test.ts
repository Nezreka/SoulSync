import { describe, expect, it } from 'vitest';

import { reassignSubject } from './reassign';

describe('reassignSubject', () => {
  it('labels the album as a Library v2 row', () => {
    // Not decoration: the service refuses a bare id, because the hint this
    // flow writes is resolved against lib2_track_files and a legacy id would
    // quietly name a different track's file for deletion.
    expect(reassignSubject(4242)).toBe('lib2:4242');
  });
});
