import { afterEach, describe, expect, it } from 'vitest';

import { recallPage, rememberPage } from './-library-v2.remembered';

function asProfile(profileId: number | null) {
  window.SoulSyncWebShellBridge = {
    getCurrentProfileContext: () => (profileId == null ? null : { profileId, isAdmin: true }),
  } as unknown as typeof window.SoulSyncWebShellBridge;
}

afterEach(() => {
  window.localStorage.clear();
  delete window.SoulSyncWebShellBridge;
});

describe('the remembered artists page', () => {
  const key = ['library-v2', 'artists', '', 'name', 'all', 1];

  it('gives back the last page for the same profile and view', () => {
    asProfile(1);
    rememberPage(key, { artists: [{ id: 7 }] });

    expect(recallPage(key)).toEqual({ artists: [{ id: 7 }] });
  });

  it("never shows one profile another profile's library", () => {
    asProfile(1);
    rememberPage(key, { artists: [{ id: 7 }] });

    asProfile(2);
    expect(recallPage(key)).toBeUndefined();
  });

  it('another view or page does not reuse it', () => {
    asProfile(1);
    rememberPage(key, { artists: [{ id: 7 }] });

    expect(recallPage([...key.slice(0, -1), 2])).toBeUndefined();
  });

  it('without a profile nothing is kept or given', () => {
    asProfile(null);
    rememberPage(key, { artists: [] });

    expect(window.localStorage.length).toBe(0);
    expect(recallPage(key)).toBeUndefined();
  });

  it('a broken entry is ignored', () => {
    asProfile(1);
    window.localStorage.setItem('soulsync.library.artistsPage.v1', '{not json');

    expect(recallPage(key)).toBeUndefined();
  });
});
