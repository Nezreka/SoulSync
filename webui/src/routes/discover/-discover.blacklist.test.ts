import { describe, expect, it } from 'vitest';

import {
  BLACKLIST_EMPTY,
  BLACKLIST_LOAD_FAILED,
  BLACKLIST_MIN_QUERY_LENGTH,
  BLACKLIST_NO_RESULTS,
  BLACKLIST_SEARCH_DEBOUNCE_MS,
  BLACKLIST_SEARCH_LIMIT,
  BLACKLIST_SUBTITLE,
  BLACKLIST_TITLE,
  BLACKLIST_URL,
  blacklistBlockBody,
  blacklistBlockEffects,
  blacklistBlockedToast,
  blacklistDeleteUrl,
  blacklistEntries,
  blacklistEntryDate,
  blacklistQueryTooShort,
  blacklistSearchBody,
  blacklistSearchResults,
  blacklistUnblockedToast,
} from './-discover.blacklist';

describe('the search box', () => {
  it('debounces at 300ms and needs two characters', () => {
    expect(BLACKLIST_SEARCH_DEBOUNCE_MS).toBe(300);
    expect(BLACKLIST_MIN_QUERY_LENGTH).toBe(2);
  });

  it('checks the length BEFORE the debounce, unlike Last.fm radio', () => {
    // So a one-character query hides the results at once rather than scheduling
    // a timer that then does nothing.
    expect(blacklistQueryTooShort('a')).toBe(true);
    expect(blacklistQueryTooShort('  a  ')).toBe(true);
    expect(blacklistQueryTooShort('ab')).toBe(false);
  });

  it('asks for a short list — this is a picker, not a browser', () => {
    expect(BLACKLIST_SEARCH_LIMIT).toBe(8);
    expect(blacklistSearchBody('aphex')).toEqual({ query: 'aphex', limit: 8 });
  });

  it('reads spotify_artists OR the generic artists key', () => {
    // The shared endpoint answers source-specific when it can and generic
    // otherwise; assuming Spotify would blank the list for other setups.
    expect(blacklistSearchResults({ spotify_artists: [{ name: 'A' }] })).toHaveLength(1);
    expect(blacklistSearchResults({ artists: [{ name: 'B' }] })).toHaveLength(1);
    expect(
      blacklistSearchResults({ spotify_artists: [{ name: 'A' }], artists: [{ name: 'B' }] })[0]
        .name,
    ).toBe('A');
  });

  it('copes with neither key present', () => {
    expect(blacklistSearchResults({})).toEqual([]);
    expect(blacklistSearchResults(null)).toEqual([]);
    expect(blacklistSearchResults({ artists: 'nope' as never })).toEqual([]);
  });

  it('keeps the no-results copy', () => {
    expect(BLACKLIST_NO_RESULTS).toBe('No artists found');
  });
});

describe('blocking', () => {
  it('blocks BY NAME, not by id', () => {
    expect(blacklistBlockBody('Aphex Twin')).toEqual({ artist_name: 'Aphex Twin' });
  });

  it('clears the search and reloads the list on success', () => {
    // Otherwise the just-blocked artist sits in the results still offering a
    // "Block" button that now does nothing visible.
    expect(blacklistBlockEffects('Aphex Twin', { success: true })).toEqual({
      toast: 'Blocked Aphex Twin from discovery',
      clearSearch: true,
      reloadList: true,
    });
  });

  it('does nothing at all on failure', () => {
    expect(blacklistBlockEffects('Aphex Twin', { success: false })).toBeNull();
    expect(blacklistBlockEffects('Aphex Twin', null)).toBeNull();
  });

  it('names the artist in both toasts', () => {
    expect(blacklistBlockedToast('A')).toBe('Blocked A from discovery');
    expect(blacklistUnblockedToast('A')).toBe('Unblocked A');
  });

  it('deletes by id', () => {
    expect(BLACKLIST_URL).toBe('/api/discover/artist-blacklist');
    expect(blacklistDeleteUrl(7)).toBe('/api/discover/artist-blacklist/7');
  });
});

describe('the blocked list', () => {
  it('needs success and a real array', () => {
    expect(blacklistEntries({ success: true, entries: [{ id: 1 }] })).toHaveLength(1);
    expect(blacklistEntries({ success: true, entries: [] })).toEqual([]);
    expect(blacklistEntries({ success: false, entries: [{ id: 1 }] })).toEqual([]);
    expect(blacklistEntries({ success: true })).toEqual([]);
    expect(blacklistEntries(null)).toEqual([]);
  });

  it('formats the blocked-at date by locale', () => {
    const iso = '2026-07-31T12:00:00.000Z';
    expect(blacklistEntryDate(iso)).toBe(new Date(iso).toLocaleDateString());
  });

  it('renders BLANK rather than "Invalid Date" without a timestamp', () => {
    expect(blacklistEntryDate(undefined)).toBe('');
    expect(blacklistEntryDate('')).toBe('');
  });

  it('keeps the modal copy', () => {
    expect(BLACKLIST_TITLE).toBe('Blocked Artists');
    expect(BLACKLIST_SUBTITLE).toBe(
      "These artists won't appear in any discovery playlist across all sources",
    );
    expect(BLACKLIST_EMPTY).toBe('No blocked artists yet — search above to block one');
    expect(BLACKLIST_LOAD_FAILED).toBe('Failed to load');
  });
});
