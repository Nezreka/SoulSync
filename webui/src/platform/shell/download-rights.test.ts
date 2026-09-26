import { afterEach, describe, expect, it, vi } from 'vitest';

import { discogFooter } from '@/routes/artist-detail/-artist-detail.discography-modal';

import { acquireVerb, profileAsksFirst } from './download-rights';

// a profile that can't download still gets the album: its wishlist add is a
// request, so the controls say Request instead of vanishing
describe('download rights', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('asks first only when init.js says the profile cannot download', () => {
    vi.stubGlobal('canDownload', () => false);
    expect(profileAsksFirst()).toBe(true);
    expect(acquireVerb()).toBe('Request');
    vi.stubGlobal('canDownload', () => true);
    expect(profileAsksFirst()).toBe(false);
    expect(acquireVerb()).toBe('Download');
  });

  it('downloads when the shell is not there yet', () => {
    vi.stubGlobal('canDownload', undefined);
    expect(acquireVerb()).toBe('Download');
  });

  it('labels the discography submit as a request', () => {
    expect(discogFooter([{ tracks: 10 }, { tracks: 3 }], true).submitText).toBe('Request 2');
    expect(discogFooter([{ tracks: 10 }], false).submitText).toBe('Add 1 to Wishlist');
    expect(discogFooter([], true).submitText).toBe('Select releases');
  });
});
