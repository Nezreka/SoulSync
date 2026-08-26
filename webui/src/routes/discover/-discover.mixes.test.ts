import { describe, expect, it } from 'vitest';

import type { DiscoverMix } from './-discover.mixes';

import {
  MIX_ACTION_DOWNLOAD,
  MIX_ACTION_PLAY,
  MIX_ACTION_SYNC,
  MIX_COVER_PLACEHOLDER,
  MIX_COVER_TILES,
  LIVE_MIX_FEEDERS,
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
  MIX_FEEDERS,
  MIX_SEL_IDLE_LABEL,
  MIX_NO_PLAYBACK,
  MIX_TRACK_GONE,
  compactRows,
  feederShouldUpsert,
  feederTracks,
  mixSelectionBar,
  mixSetAllSelected,
} from './-discover.mixes';

const track = (cover?: string) => ({ track_name: 't', album_cover_url: cover });
const mix = (over: Partial<DiscoverMix> = {}): DiscoverMix => ({ key: 'k', title: 'T', ...over });

describe('the feeder inventory', () => {
  it('names all eight _upsertMixCard call sites', () => {
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

  it('every feeder is live now, daily mixes included', () => {
    // daily_mix_* was dead (its vanilla producer was unreachable) until the
    // aug 25 rebuild on core/personalized/daily_mixes.py. it is the one
    // feeder without a syncKey - play + download only, until P5 registers a
    // sync playlist type for it.
    expect(YOUR_MIX_FEEDERS.filter((f) => !f.live)).toEqual([]);
    expect(LIVE_MIX_FEEDERS).toHaveLength(8);
    expect(LIVE_MIX_FEEDERS.filter((f) => f.syncKey === null).map((f) => f.key)).toEqual([
      'daily_mix_*',
    ]);
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

  it('leads every mix with Play, then Download + Sync from a syncKey', () => {
    // the play-now bridge (aug 25): listening is always the first offer
    const actions = mixActions(mix({ syncKey: 'hidden_gems' }));
    expect(actions.map((a) => a.label)).toEqual(['▶ Play', 'Download', 'Sync']);
    // the constants pinned separately, so the literals above catch a drift
    expect(MIX_ACTION_PLAY).toBe('▶ Play');
    expect(MIX_ACTION_DOWNLOAD).toBe('Download');
    expect(MIX_ACTION_SYNC).toBe('Sync');
    expect(actions[0].onclick).toBe('play');
    expect(actions[2].primary).toBe(true);
    expect(actions[2].isSync).toBe(true);
  });

  it('closes this modal before Download opens the next one', () => {
    // The download modal opens beneath this one and would be uninteractable.
    expect(mixActions(mix({ syncKey: 'x' }))[1].closeFirst).toBe(true);
    expect(mixActions(mix({ syncKey: 'x' }))[2].closeFirst).toBeUndefined();
  });

  it('prepends Play to a mix’s OWN actions', () => {
    const custom = [{ label: 'Custom', onclick: 'x' }];
    const actions = mixActions(mix({ actions: custom, syncKey: 'ignored' }));
    expect(actions.map((a) => a.label)).toEqual(['▶ Play', 'Custom']);
  });

  it('a mix with neither actions nor syncKey is still playable', () => {
    // daily_mix_* used to get NOTHING here. play needs no download key -
    // it resolves against the library - so it is always offered.
    expect(mixActions(mix({ key: 'daily_mix_0' })).map((a) => a.label)).toEqual(['▶ Play']);
  });

  it('an explicitly EMPTY action list still gets Play', () => {
    expect(mixActions(mix({ actions: [], syncKey: 'x' })).map((a) => a.label)).toEqual(['▶ Play']);
  });
});

describe('the live shelf feeders', () => {
  it('covers the four the audit found missing', () => {
    expect(MIX_FEEDERS.map((f) => f.key)).toEqual([
      'release_radar',
      'discovery_weekly',
      'popular_picks',
      'hidden_gems',
    ]);
  });

  it('every feeder matches a LIVE entry in the inventory', () => {
    // A feeder def for a dead key would resurrect a section users never see.
    const liveKeys = LIVE_MIX_FEEDERS.map((f) => f.key);
    for (const f of MIX_FEEDERS) expect(liveKeys).toContain(f.key);
  });

  it('carries each title, subtitle and syncKey verbatim', () => {
    const byKey = Object.fromEntries(MIX_FEEDERS.map((f) => [f.key, f]));
    expect(byKey.release_radar.title).toBe('Fresh Tape');
    expect(byKey.release_radar.subtitle).toBe('New releases from artists you follow');
    expect(byKey.discovery_weekly.title).toBe('The Archives');
    expect(byKey.popular_picks.subtitle).toBe('Popular tracks from artists you love');
    expect(byKey.hidden_gems.subtitle).toBe('Deeper cuts you might have missed');
    for (const f of MIX_FEEDERS) expect(f.syncKey).toBe(f.key);
  });

  it('upserts a card ONLY when there are tracks', () => {
    // An empty response must not produce a "0 tracks" card — the shelf simply
    // never learns about that mix.
    expect(feederShouldUpsert([{}])).toBe(true);
    expect(feederShouldUpsert([])).toBe(false);
    expect(feederShouldUpsert(null)).toBe(false);
  });

  it('reads tracks only from a successful response', () => {
    expect(feederTracks({ success: true, tracks: [{}, {}] })).toHaveLength(2);
    expect(feederTracks({ success: false, tracks: [{}] })).toEqual([]);
    expect(feederTracks({ success: true })).toEqual([]);
    expect(feederTracks(null)).toEqual([]);
  });
});

describe('the modal track table', () => {
  const t = (over = {}) => ({
    track_name: 'Xtal',
    artist_name: 'Aphex',
    album_name: 'SAW',
    ...over,
  });

  it('numbers rows 1-based while keeping the 0-based index', () => {
    const rows = compactRows([t(), t()]);
    expect(rows.map((r) => r.position)).toEqual([1, 2]);
    expect(rows.map((r) => r.index)).toEqual([0, 1]);
  });

  it('formats duration as m:ss', () => {
    expect(compactRows([t({ duration_ms: 305000 })])[0].duration).toBe('5:05');
    expect(compactRows([t({ duration_ms: 61000 })])[0].duration).toBe('1:01');
  });

  it('is EMPTY for an unknown length, not "0:00"', () => {
    expect(compactRows([t({ duration_ms: 0 })])[0].duration).toBe('');
    expect(compactRows([t()])[0].duration).toBe('');
  });

  it('falls back to the placeholder cover', () => {
    expect(compactRows([t()])[0].cover).toBe(MIX_COVER_PLACEHOLDER);
  });

  it('is NOT selectable by default — that is opt-in per call', () => {
    // The mix modal passes selectable (#1079); the plain renderers do not.
    expect(compactRows([t()])[0].selectable).toBe(false);
    expect(compactRows([t()], true)[0].selectable).toBe(true);
  });
});

describe('the #1079 selection bar', () => {
  it('labels the count and the button', () => {
    expect(mixSelectionBar(3, 10)).toMatchObject({
      countLabel: '3 selected',
      downloadLabel: 'Download selected (3)',
      downloadDisabled: false,
    });
  });

  it('disables and un-counts the button at zero', () => {
    expect(mixSelectionBar(0, 10)).toMatchObject({
      countLabel: '0 selected',
      downloadLabel: MIX_SEL_IDLE_LABEL,
      downloadDisabled: true,
    });
  });

  it('ticks select-all only when every row is selected', () => {
    expect(mixSelectionBar(10, 10).selectAllChecked).toBe(true);
    expect(mixSelectionBar(9, 10).selectAllChecked).toBe(false);
  });

  it('does NOT tick select-all for an empty list', () => {
    // 0 === 0 would otherwise show select-all ticked with nothing to select.
    expect(mixSelectionBar(0, 0).selectAllChecked).toBe(false);
  });

  it('select-all picks every index; clear picks none', () => {
    expect(mixSetAllSelected(3, true)).toEqual([0, 1, 2]);
    expect(mixSetAllSelected(3, false)).toEqual([]);
  });

  it('keeps the preview failure copy', () => {
    expect(MIX_TRACK_GONE).toBe('Track is no longer available');
    expect(MIX_NO_PLAYBACK).toBe('Playback is not available here');
  });
});
