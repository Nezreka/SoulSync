import { describe, expect, it } from 'vitest';

import type { DiscoverMix } from './-discover.mixes';

import {
  MIX_ACTION_DOWNLOAD,
  MIX_ACTION_SYNC,
  MIX_COVER_PLACEHOLDER,
  MIX_COVER_TILES,
  YOUR_MIX_FEEDERS,
  emptyMixRegistry,
  mixActions,
  mixCoverTiles,
  mixCoverUpgradeApplies,
  mixNeedsCoverHydration,
  mixStatusBase,
  mixTrackCount,
  mixUsesSolidCover,
  registerSectionMixes,
  resolveMix,
  shelfMixes,
  shelfVisible,
  upsertShelfMix,
} from './-discover.mixes';

const track = (cover?: string) => ({ track_name: 't', album_cover_url: cover });
const mix = (over: Partial<DiscoverMix> = {}): DiscoverMix => ({ key: 'k', title: 'T', ...over });

describe('the feeder inventory', () => {
  it('names all EIGHT shelf feeders', () => {
    // Traced to their _upsertMixCard call sites. An earlier count of seven was
    // wrong; this list is what stops a feeder going missing in the port.
    expect(YOUR_MIX_FEEDERS.map((f) => f.key)).toEqual([
      'release_radar',
      'discovery_weekly',
      'seasonal_playlist',
      'popular_picks',
      'hidden_gems',
      'listening_mix',
      'daily_mix_*',
      'discovery_shuffle',
    ]);
  });

  it('records that daily mixes are the ONLY feeder with no syncKey', () => {
    const noSync = YOUR_MIX_FEEDERS.filter((f) => f.syncKey === null);
    expect(noSync.map((f) => f.key)).toEqual(['daily_mix_*']);
  });

  it('records the two feeders whose titles are built at runtime', () => {
    expect(YOUR_MIX_FEEDERS.filter((f) => f.title === null).map((f) => f.key)).toEqual([
      'seasonal_playlist',
      'daily_mix_*',
    ]);
  });
});

describe('the track count', () => {
  it('counts loaded tracks', () => {
    expect(mixTrackCount(mix({ tracks: [track(), track()] }))).toBe(2);
  });

  it('reads an EMPTY array as zero rather than falling back to trackCount', () => {
    // [] is truthy, so a mix that loaded and found nothing says "0 tracks"
    // instead of showing a stale count.
    expect(mixTrackCount(mix({ tracks: [], trackCount: 40 }))).toBe(0);
  });

  it('falls back to trackCount before the tracks arrive', () => {
    expect(mixTrackCount(mix({ trackCount: 40 }))).toBe(40);
    expect(mixTrackCount(mix())).toBe(0);
  });
});

describe('the mosaic cover', () => {
  it('is always four tiles', () => {
    expect(MIX_COVER_TILES).toBe(4);
    expect(mixCoverTiles([track('/a.jpg')])).toHaveLength(4);
    expect(mixCoverTiles([])).toHaveLength(4);
  });

  it('pads with the placeholder rather than rendering a ragged grid', () => {
    expect(mixCoverTiles([track('/a.jpg')])).toEqual([
      '/a.jpg',
      MIX_COVER_PLACEHOLDER,
      MIX_COVER_PLACEHOLDER,
      MIX_COVER_PLACEHOLDER,
    ]);
  });

  it('DEDUPES before capping, so a same-album run still finds four covers', () => {
    const tracks = [
      track('/a.jpg'),
      track('/a.jpg'),
      track('/a.jpg'),
      track('/a.jpg'),
      track('/b.jpg'),
      track('/c.jpg'),
      track('/d.jpg'),
    ];
    expect(mixCoverTiles(tracks)).toEqual(['/a.jpg', '/b.jpg', '/c.jpg', '/d.jpg']);
  });

  it('stops at four even with more distinct covers', () => {
    const tracks = ['/a', '/b', '/c', '/d', '/e'].map(track);
    expect(mixCoverTiles(tracks)).toEqual(['/a', '/b', '/c', '/d']);
  });

  it('recognises a section that supplies its own cover', () => {
    expect(mixUsesSolidCover(mix({ coverHtml: '<div/>' }))).toBe(true);
    expect(mixUsesSolidCover(mix())).toBe(false);
  });
});

describe('lazy cover hydration', () => {
  it('runs only for a mix with a loader and no tracks yet', () => {
    expect(mixNeedsCoverHydration(mix({ fetchTracks: () => [] }))).toBe(true);
    expect(mixNeedsCoverHydration(mix({ tracks: [], fetchTracks: () => [] }))).toBe(false);
    expect(mixNeedsCoverHydration(mix())).toBe(false);
  });

  it('KEEPS the placeholder cover when the load found no art', () => {
    // Four placeholder tiles look the same but discard the solid-cover styling
    // some sections rely on.
    expect(mixCoverUpgradeApplies([])).toBe(false);
    expect(mixCoverUpgradeApplies([track(), track()])).toBe(false);
  });

  it('upgrades as soon as one real cover exists', () => {
    expect(mixCoverUpgradeApplies([track(), track('/a.jpg')])).toBe(true);
  });
});

describe('the registry and the shelf are different collections', () => {
  it('registers a section’s mixes WITHOUT putting them on the shelf', () => {
    // Rendering Object.values(registry) would leak decades, Last.fm and
    // ListenBrainz onto the Your Mixes shelf.
    const r = registerSectionMixes(emptyMixRegistry(), [mix({ key: 'decade_80s' })]);
    expect(resolveMix(r, 'decade_80s')).toBeTruthy();
    expect(r.shelfKeys).toEqual([]);
    expect(shelfMixes(r)).toEqual([]);
  });

  it('upserting puts a mix in BOTH', () => {
    const r = upsertShelfMix(emptyMixRegistry(), mix({ key: 'hidden_gems' }));
    expect(resolveMix(r, 'hidden_gems')).toBeTruthy();
    expect(r.shelfKeys).toEqual(['hidden_gems']);
  });

  it('keeps the shelf in insertion order', () => {
    let r = emptyMixRegistry();
    r = upsertShelfMix(r, mix({ key: 'a' }));
    r = upsertShelfMix(r, mix({ key: 'b' }));
    r = upsertShelfMix(r, mix({ key: 'c' }));
    expect(shelfMixes(r).map((m) => m.key)).toEqual(['a', 'b', 'c']);
  });

  it('re-upserting refreshes the mix but does NOT move it', () => {
    // A section reloading must not reshuffle the shelf under the user.
    let r = emptyMixRegistry();
    r = upsertShelfMix(r, mix({ key: 'a', title: 'A' }));
    r = upsertShelfMix(r, mix({ key: 'b' }));
    r = upsertShelfMix(r, mix({ key: 'a', title: 'A updated' }));
    expect(r.shelfKeys).toEqual(['a', 'b']);
    expect(shelfMixes(r)[0].title).toBe('A updated');
  });

  it('resolves ANY registered mix, shelf or not', () => {
    let r = registerSectionMixes(emptyMixRegistry(), [mix({ key: 'lastfm_x' })]);
    r = upsertShelfMix(r, mix({ key: 'hidden_gems' }));
    expect(resolveMix(r, 'lastfm_x')).toBeTruthy();
    expect(resolveMix(r, 'hidden_gems')).toBeTruthy();
  });

  it('returns null for an unknown key instead of throwing', () => {
    expect(resolveMix(emptyMixRegistry(), 'nope')).toBeNull();
  });

  it('hides the shelf until something registers on it', () => {
    expect(shelfVisible(emptyMixRegistry())).toBe(false);
    expect(shelfVisible(registerSectionMixes(emptyMixRegistry(), [mix()]))).toBe(false);
    expect(shelfVisible(upsertShelfMix(emptyMixRegistry(), mix()))).toBe(true);
  });
});

describe('the mix modal', () => {
  it('derives the status base from the syncKey with hyphens', () => {
    // Mirrors startDiscoverPlaylistSync's id convention so a running sync's
    // progress lands on this modal's elements.
    expect(mixStatusBase(mix({ syncKey: 'release_radar' }))).toBe('release-radar');
    expect(mixStatusBase(mix({ syncKey: 'a_b_c' }))).toBe('a-b-c');
  });

  it('prefers an explicit statusBase', () => {
    expect(mixStatusBase(mix({ statusBase: 'custom', syncKey: 'release_radar' }))).toBe('custom');
  });

  it('is empty for a mix with neither, which simply has no live status', () => {
    expect(mixStatusBase(mix())).toBe('');
  });

  it('builds Download + Sync from a syncKey', () => {
    const actions = mixActions(mix({ syncKey: 'hidden_gems' }));
    expect(actions.map((a) => a.label)).toEqual([MIX_ACTION_DOWNLOAD, MIX_ACTION_SYNC]);
    expect(actions[1].primary).toBe(true);
    expect(actions[1].isSync).toBe(true);
  });

  it('closes this modal before Download opens the next one', () => {
    // The download modal opens beneath this one and would be uninteractable.
    expect(mixActions(mix({ syncKey: 'x' }))[0].closeFirst).toBe(true);
    expect(mixActions(mix({ syncKey: 'x' }))[1].closeFirst).toBeUndefined();
  });

  it('prefers a mix’s OWN actions over the built-in pair', () => {
    const custom = [{ label: 'Custom', onclick: 'x' }];
    expect(mixActions(mix({ actions: custom, syncKey: 'ignored' }))).toBe(custom);
  });

  it('gives the daily mixes NO actions, which is the vanilla behaviour', () => {
    // daily_mix_* has neither `actions` nor `syncKey`. Inventing a Download
    // button here would call an endpoint with an undefined key.
    expect(mixActions(mix({ key: 'daily_mix_0' }))).toEqual([]);
  });

  it('respects an explicitly EMPTY action list', () => {
    expect(mixActions(mix({ actions: [], syncKey: 'x' }))).toEqual([]);
  });
});
