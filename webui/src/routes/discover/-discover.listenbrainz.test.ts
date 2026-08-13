import { describe, expect, it } from 'vitest';

import {
  LB_DEFAULT_TAB,
  LB_DEFAULT_TRACK_COUNT,
  LB_EMPTY_CATEGORY,
  LB_HYDRATE_DEFERS_A_TICK,
  LB_LOAD_FAILED,
  LB_OTHER_GROUP,
  LB_PLACEHOLDER_IMAGE,
  LB_TABS,
  groupLbPlaylists,
  lbActiveSubTab,
  lbFirstActiveTab,
  lbGroupFor,
  lbIdentifier,
  lbMixKey,
  lbPickUsername,
  lbPlaylistData,
  lbPlaylistMix,
  lbShowsConnectPrompt,
  lbStatusBase,
  lbSubtitle,
  lbSyncMatchedId,
  lbSyncTotalId,
  lbTabHasData,
  lbTrackCount,
  lbTrackCountLabel,
  lbTrackRows,
  lbUsesSubTabs,
} from './-discover.listenbrainz';

const pl = (title: string, extra: Record<string, unknown> = {}) => ({
  playlist: { title, identifier: `https://listenbrainz.org/playlist/mbid-${title}`, ...extra },
});

describe('the three tabs', () => {
  it('keeps ids and labels verbatim, in order', () => {
    expect(LB_TABS.map((t) => t.id)).toEqual(['recommendations', 'user', 'collaborative']);
    expect(LB_TABS.map((t) => t.label)).toEqual([
      '🎁 Recommendations',
      '📚 Your Playlists',
      '🤝 Collaborative',
    ]);
    expect(LB_DEFAULT_TAB).toBe('recommendations');
  });

  it('counts a tab as having data only with a non-empty array', () => {
    expect(lbTabHasData({ success: true, playlists: [{}] })).toBe(true);
    expect(lbTabHasData({ success: true, playlists: [] })).toBe(false);
    expect(lbTabHasData({ success: false, playlists: [{}] })).toBe(false);
    expect(lbTabHasData(null)).toBe(false);
  });

  it('takes the FIRST username offered, even from an empty tab', () => {
    // A user with only collaborative playlists still gets their name.
    expect(lbPickUsername(null, 'alice')).toBe('alice');
    expect(lbPickUsername('alice', 'bob')).toBe('alice');
    expect(lbPickUsername(null, undefined)).toBeNull();
  });

  it('names the subtitle after the user when known', () => {
    expect(lbSubtitle('alice')).toBe('Playlists for alice');
    expect(lbSubtitle(null)).toBe('Playlists from ListenBrainz');
  });

  it('activates the first tab WITH DATA, not simply the first tab', () => {
    expect(lbFirstActiveTab({ recommendations: false, user: true })).toBe('user');
    expect(lbFirstActiveTab({ collaborative: true })).toBe('collaborative');
    expect(lbFirstActiveTab({})).toBeNull();
  });

  it('replaces the whole strip with a connect prompt when nothing has data', () => {
    expect(lbShowsConnectPrompt(0)).toBe(true);
    expect(lbShowsConnectPrompt(1)).toBe(false);
  });

  it('keeps the failure copy', () => {
    expect(LB_EMPTY_CATEGORY).toBe('No playlists in this category');
    expect(LB_LOAD_FAILED).toBe('Failed to load playlists');
  });
});

describe('sub-tab grouping', () => {
  it('recognises the five ListenBrainz playlist families by title', () => {
    expect(lbGroupFor(pl('Weekly Jams for alice'))).toBe('Weekly Jams');
    expect(lbGroupFor(pl('Weekly Exploration for alice'))).toBe('Weekly Exploration');
    expect(lbGroupFor(pl('Top Discoveries of 2025'))).toBe('Top Discoveries');
    expect(lbGroupFor(pl('Top Missed Recordings of 2025'))).toBe('Top Missed Recordings');
    expect(lbGroupFor(pl('Daily Jams for alice'))).toBe('Daily Jams');
  });

  it('matches case-insensitively', () => {
    expect(lbGroupFor(pl('WEEKLY JAMS'))).toBe('Weekly Jams');
  });

  it('falls back to Other', () => {
    expect(lbGroupFor(pl('Something Else'))).toBe(LB_OTHER_GROUP);
    expect(lbGroupFor({ playlist: {} })).toBe(LB_OTHER_GROUP);
  });

  it('keeps first-seen order for recognised groups', () => {
    const { groupOrder } = groupLbPlaylists([
      pl('Daily Jams a'),
      pl('Weekly Jams b'),
      pl('Daily Jams c'),
    ]);
    expect(groupOrder).toEqual(['Daily Jams', 'Weekly Jams']);
  });

  it('moves Other to the END even when seen first', () => {
    const { groupOrder } = groupLbPlaylists([pl('Mystery'), pl('Weekly Jams b')]);
    expect(groupOrder).toEqual(['Weekly Jams', LB_OTHER_GROUP]);
  });

  it('leaves Other alone when it is already last', () => {
    const { groupOrder } = groupLbPlaylists([pl('Weekly Jams b'), pl('Mystery')]);
    expect(groupOrder).toEqual(['Weekly Jams', LB_OTHER_GROUP]);
  });

  it('collects every playlist into its group', () => {
    const { groups } = groupLbPlaylists([pl('Daily Jams a'), pl('Daily Jams b')]);
    expect(groups['Daily Jams']).toHaveLength(2);
  });

  it('shows sub-tabs only for recommendations with >1 playlist AND >1 group', () => {
    // A sub-tab bar with a single tab is pure chrome.
    expect(lbUsesSubTabs('recommendations', 3, 2)).toBe(true);
    expect(lbUsesSubTabs('recommendations', 3, 1)).toBe(false);
    expect(lbUsesSubTabs('recommendations', 1, 1)).toBe(false);
    expect(lbUsesSubTabs('user', 3, 2)).toBe(false);
  });

  it('preserves the active sub-tab across a re-render when it still exists', () => {
    expect(lbActiveSubTab('Weekly Jams', ['Daily Jams', 'Weekly Jams'])).toBe('Weekly Jams');
    expect(lbActiveSubTab('Gone', ['Daily Jams'])).toBe('Daily Jams');
    expect(lbActiveSubTab(null, ['Daily Jams'])).toBe('Daily Jams');
    expect(lbActiveSubTab(null, [])).toBeNull();
  });
});

describe('the playlist card', () => {
  it('unwraps the LB envelope but accepts a bare playlist', () => {
    expect(lbPlaylistData({ playlist: { title: 'A' } }).title).toBe('A');
    expect(lbPlaylistData({ title: 'B' }).title).toBe('B');
  });

  it('takes the MBID from the LAST path segment', () => {
    expect(
      lbIdentifier({ playlist: { identifier: 'https://listenbrainz.org/playlist/abc-123' } }),
    ).toBe('abc-123');
    expect(lbIdentifier({ playlist: {} })).toBe('');
    expect(lbIdentifier({ playlist: { identifier: 42 } })).toBe('');
  });

  it('defaults the track count to 50 — a GUESS shown before any fetch', () => {
    expect(LB_DEFAULT_TRACK_COUNT).toBe(50);
    expect(lbTrackCount(pl('x'))).toBe(50);
  });

  it('prefers a positive annotation count, then the embedded track array', () => {
    expect(lbTrackCount(pl('x', { annotation: { track_count: 7 } }))).toBe(7);
    expect(lbTrackCount(pl('x', { track: [{}, {}, {}] }))).toBe(3);
    expect(lbTrackCount(pl('x', { annotation: { track_count: 7 }, track: [{}] }))).toBe(7);
  });

  it('keeps the optimistic 50 rather than showing zero', () => {
    // Both guards are `> 0`, so an empty playlist does not display "0 tracks".
    expect(lbTrackCount(pl('x', { annotation: { track_count: 0 } }))).toBe(50);
    expect(lbTrackCount(pl('x', { track: [] }))).toBe(50);
  });

  it('keys the mix by TAB and identifier, so one playlist can appear twice', () => {
    // Without the tab, a second registration would overwrite the first in the
    // shared mix registry.
    expect(lbMixKey('user', 'abc')).toBe('lb-user-abc');
    expect(lbMixKey('collaborative', 'abc')).not.toBe(lbMixKey('user', 'abc'));
  });

  it('uses -sync-total/-sync-matched, NOT the generic completed/pending', () => {
    // Which is exactly why the card supplies its own statusHtml.
    expect(lbStatusBase('abc')).toBe('discover-lb-playlist-abc');
    expect(lbSyncTotalId('abc')).toBe('discover-lb-playlist-abc-sync-total');
    expect(lbSyncMatchedId('abc')).toBe('discover-lb-playlist-abc-sync-matched');
    expect(lbSyncTotalId('abc')).not.toContain('completed');
  });

  it('titles and credits the card, with defaults', () => {
    const mix = lbPlaylistMix(pl('Weekly Jams', { creator: 'listenbrainz' }), 'recommendations');
    expect(mix.title).toBe('Weekly Jams');
    expect(mix.subtitle).toBe('by listenbrainz');
    const bare = lbPlaylistMix({ playlist: {} }, 'user');
    expect(bare.title).toBe('Untitled Playlist');
    expect(bare.subtitle).toBe('by ListenBrainz');
  });

  it('gives every card a Download and a Sync action', () => {
    const actions = lbPlaylistMix(pl('x'), 'user').actions ?? [];
    expect(actions.map((a) => a.label)).toEqual(['Download', 'Sync']);
  });

  it('defers cover hydration a tick, because the cards do not exist yet', () => {
    // The caller injects the HTML synchronously; a same-tick hydrate finds no
    // elements and silently leaves every card on its placeholder mosaic.
    expect(LB_HYDRATE_DEFERS_A_TICK).toBe(true);
  });
});

describe('the track table', () => {
  const t = (over: Record<string, unknown> = {}) => ({
    track_name: 'Xtal',
    artist_name: 'Aphex Twin',
    album_name: 'SAW',
    duration_ms: 305000,
    ...over,
  });

  it('numbers rows from one and formats m:ss', () => {
    const rows = lbTrackRows([t(), t()]);
    expect(rows.map((r) => r.position)).toEqual([1, 2]);
    expect(rows[0].duration).toBe('5:05');
  });

  it('shows NO duration for an unknown length', () => {
    expect(lbTrackRows([t({ duration_ms: 0 })])[0].duration).toBe('');
    expect(lbTrackRows([t({ duration_ms: undefined })])[0].duration).toBe('');
  });

  it('cleans the artist — the only track renderer here that does', () => {
    // ListenBrainz credits often carry "feat." strings other sources strip.
    expect(lbTrackRows([t({ artist_name: 'Aphex Twin feat. Someone' })])[0].artist).toBe(
      'Aphex Twin',
    );
  });

  it('falls back for a nameless track or artist', () => {
    const row = lbTrackRows([{}])[0];
    expect(row.name).toBe('Unknown Track');
    expect(row.artist).toBe('Unknown Artist');
    expect(row.album).toBe('');
  });

  it('uses an INLINE data-uri placeholder, needing no network round trip', () => {
    expect(lbTrackRows([t({ album_cover_url: undefined })])[0].cover).toBe(LB_PLACEHOLDER_IMAGE);
    expect(LB_PLACEHOLDER_IMAGE.startsWith('data:image/svg+xml;base64,')).toBe(true);
  });

  it('pluralises the count line', () => {
    expect(lbTrackCountLabel('alice', 1)).toBe('by alice • 1 track');
    expect(lbTrackCountLabel('alice', 2)).toBe('by alice • 2 tracks');
    expect(lbTrackCountLabel('alice', 0)).toBe('by alice • 0 tracks');
  });
});
