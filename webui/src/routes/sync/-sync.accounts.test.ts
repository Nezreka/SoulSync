/**
 * The two account tabs' pure core, pinned against sync-spotify.js 1640-1642 /
 * 1823-1829 and sync-services.js 2471 / 2499-2509 / 2646-2654.
 */

import { describe, expect, it } from 'vitest';

import {
  arlShimRow,
  deezerArlId,
  deezerArlStatusClass,
  deezerArlStatusLabel,
  selectionInfoText,
  spotifyStatusClass,
} from './-sync.accounts';

describe('deezerArlId', () => {
  it('prefixes, and takes numbers or strings', () => {
    expect(deezerArlId(42)).toBe('deezer_arl_42');
    expect(deezerArlId('42')).toBe('deezer_arl_42');
  });
});

describe('spotifyStatusClass (1640-1642)', () => {
  it('maps each status the backend actually sends', () => {
    expect(spotifyStatusClass('Synced 2 days ago')).toBe('status-synced');
    expect(spotifyStatusClass('Needs Sync')).toBe('status-needs-sync');
    expect(spotifyStatusClass('Last Sync: 3 days ago')).toBe('status-needs-sync');
    expect(spotifyStatusClass('Never Synced')).toBe('status-never-synced');
  });

  it('is prefix-matched, not equality — the vanilla uses startsWith', () => {
    expect(spotifyStatusClass('Synced')).toBe('status-synced');
    expect(spotifyStatusClass('Last Sync')).toBe('status-needs-sync');
    // Not a prefix of either → the default.
    expect(spotifyStatusClass('Syncing')).toBe('status-never-synced');
  });

  it('treats an absent status as never-synced (the vanilla would throw)', () => {
    expect(spotifyStatusClass(undefined)).toBe('status-never-synced');
    expect(spotifyStatusClass('')).toBe('status-never-synced');
  });
});

describe('deezerArl status (2499-2500, 2509) — TWO states, not three', () => {
  it('has no Needs Sync arm at all', () => {
    expect(deezerArlStatusClass('Synced 2 days ago')).toBe('status-synced');
    // The exact input Spotify maps to status-needs-sync.
    expect(deezerArlStatusClass('Needs Sync')).toBe('status-never-synced');
    expect(deezerArlStatusClass('Last Sync: 3 days ago')).toBe('status-never-synced');
  });

  it('guards the absent case where Spotify does not', () => {
    expect(deezerArlStatusClass(undefined)).toBe('status-never-synced');
    expect(deezerArlStatusLabel(undefined)).toBe('Never Synced');
    expect(deezerArlStatusLabel('')).toBe('Never Synced');
    expect(deezerArlStatusLabel('Synced 2 days ago')).toBe('Synced 2 days ago');
  });
});

describe('arlShimRow — the same row, TWO different track counts', () => {
  const row = {
    id: 7,
    name: 'Road Trip',
    track_count: 40,
    image_url: 'http://cover',
    owner: 'boulder',
  };

  it('the modal-time shim counts the FETCHED tracks (2650)', () => {
    expect(arlShimRow(row, 'modal', [{}, {}, {}])).toEqual({
      id: 'deezer_arl_7',
      name: 'Road Trip',
      track_count: 3,
      image_url: 'http://cover',
      owner: 'boulder',
    });
  });

  it('the load-time shim takes the ROW count (2471)', () => {
    expect(arlShimRow(row, 'load').track_count).toBe(40);
  });

  it('each defaults the way its own vanilla line does', () => {
    expect(arlShimRow({ id: 7 }, 'modal').track_count).toBe(0);
    expect(arlShimRow({ id: 7 }, 'load').track_count).toBe(0);
    expect(arlShimRow({ id: 7 }, 'load').image_url).toBe('');
    expect(arlShimRow({ id: 7 }, 'load').owner).toBe('');
  });
});

describe('selectionInfoText (1823-1829)', () => {
  it('pluralises above one, not at one', () => {
    expect(selectionInfoText(0)).toBe('Select playlists to sync');
    expect(selectionInfoText(1)).toBe('1 playlist selected');
    expect(selectionInfoText(2)).toBe('2 playlists selected');
  });
});
