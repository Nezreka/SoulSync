import { describe, expect, it } from 'vitest';

import { folderWords } from './home-sources';

describe('a home folder card says what it holds', () => {
  it('says the clips are already in the server library instead of "0 videos"', () => {
    expect(folderWords({ videos: 0, shared: 58, shared_with: 'Home Videos' })).toBe(
      '58 videos, all already in Home Videos, so counted there',
    );
  });
  it('counts its own clips and the shared ones', () => {
    expect(folderWords({ videos: 3, shared: 58, shared_with: 'Home Videos' })).toBe(
      '61 videos · 58 already in Home Videos',
    );
  });
  it('is plain when nothing is shared', () => {
    expect(folderWords({ videos: 1 })).toBe('1 video');
  });
});
