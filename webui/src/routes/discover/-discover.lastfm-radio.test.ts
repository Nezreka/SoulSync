import { describe, expect, it } from 'vitest';

import {
  LASTFM_CONFIGURED_URL,
  LASTFM_DISPLAY_SEPARATOR,
  LASTFM_GENERATE_ERROR,
  LASTFM_GENERATE_FAILED,
  LASTFM_MIN_QUERY_LENGTH,
  LASTFM_RADIO_CARD_KIND,
  LASTFM_RADIO_GENERATE_URL,
  LASTFM_RADIO_PLAYLISTS_URL,
  LASTFM_SEARCH_DEBOUNCE_MS,
  lastfmGenerateBody,
  lastfmGenerateOutcome,
  lastfmHasResults,
  lastfmPlaylists,
  lastfmQueryIsEmpty,
  lastfmQueryTooShort,
  lastfmSearchUrl,
  lastfmSectionVisibility,
  lastfmSelectionLabel,
  resultShowsListeners,
  resultSubtitle,
} from './-discover.lastfm-radio';

describe('the search box', () => {
  it('debounces at 400ms', () => {
    expect(LASTFM_SEARCH_DEBOUNCE_MS).toBe(400);
  });

  it('hides the dropdown IMMEDIATELY for an empty query, before the debounce', () => {
    expect(lastfmQueryIsEmpty('')).toBe(true);
    expect(lastfmQueryIsEmpty('   ')).toBe(true);
    expect(lastfmQueryIsEmpty(null)).toBe(true);
    expect(lastfmQueryIsEmpty(' x ')).toBe(false);
  });

  it('requires two characters, checked AFTER the debounce fires', () => {
    // A one-character query still schedules a timer; the callback then returns
    // without touching the dropdown, so whatever was showing stays.
    expect(LASTFM_MIN_QUERY_LENGTH).toBe(2);
    expect(lastfmQueryTooShort('a')).toBe(true);
    expect(lastfmQueryTooShort('ab')).toBe(false);
  });

  it('encodes the query', () => {
    expect(lastfmSearchUrl('a & b')).toBe('/api/lastfm/search/tracks?q=a%20%26%20b');
  });

  it('hides the dropdown on empty results rather than showing a "none" row', () => {
    expect(lastfmHasResults({ results: [{ name: 'x' }] })).toBe(true);
    expect(lastfmHasResults({ results: [] })).toBe(false);
    expect(lastfmHasResults({})).toBe(false);
    expect(lastfmHasResults(null)).toBe(false);
  });
});

describe('a search result', () => {
  it('shows listeners only for a strictly positive count', () => {
    expect(resultShowsListeners({ listeners: 1 })).toBe(true);
    expect(resultShowsListeners({ listeners: 0 })).toBe(false);
    expect(resultShowsListeners({})).toBe(false);
  });

  it('renders the FULL count, not the dead span’s "Nk" abbreviation', () => {
    // The vanilla builds a <span …-result-listeners>4k listeners</span> and then
    // uses it only as a boolean; that span never reaches the DOM and its class
    // does not exist in any stylesheet. What renders is toLocaleString().
    expect(resultSubtitle({ artist: 'a-ha', listeners: 4321 })).toBe(
      `a-ha · ${(4321).toLocaleString()} listeners`,
    );
    expect(resultSubtitle({ artist: 'a-ha', listeners: 4321 })).not.toContain('4k');
  });

  it('shows the artist alone with no separator at zero listeners', () => {
    expect(resultSubtitle({ artist: 'a-ha', listeners: 0 })).toBe('a-ha');
    expect(resultSubtitle({ artist: 'a-ha' })).toBe('a-ha');
    expect(resultSubtitle({})).toBe('');
  });
});

describe('selecting a track', () => {
  it('labels the input with an EM DASH separator', () => {
    expect(lastfmSelectionLabel('Take On Me', 'a-ha')).toBe('Take On Me — a-ha');
    expect(LASTFM_DISPLAY_SEPARATOR).toBe(' — ');
  });

  it('keeps a track whose own name contains the separator intact', () => {
    // The dead generateLastfmRadio re-parsed this string by splitting on the
    // same separator, which would mangle this. The port holds the selection as
    // state instead, so the label is display-only.
    expect(lastfmSelectionLabel('Life — A Song', 'Artist')).toBe('Life — A Song — Artist');
  });
});

describe('generating', () => {
  it('posts the track and artist under their API names', () => {
    expect(lastfmGenerateBody('Take On Me', 'a-ha')).toEqual({
      track_name: 'Take On Me',
      artist_name: 'a-ha',
    });
    expect(LASTFM_RADIO_GENERATE_URL).toBe('/api/lastfm/radio/generate');
  });

  it('clears the spinner before toasting a failure', () => {
    // Otherwise "Building radio for…" sits there next to an error toast.
    expect(lastfmGenerateOutcome({ success: false, error: 'no similar tracks' })).toEqual({
      ok: false,
      clearContainer: true,
      message: 'no similar tracks',
    });
  });

  it('falls back to a generic message', () => {
    expect(lastfmGenerateOutcome({ success: false })).toMatchObject({
      message: LASTFM_GENERATE_FAILED,
    });
    expect(lastfmGenerateOutcome(null)).toMatchObject({ message: LASTFM_GENERATE_FAILED });
    expect(LASTFM_GENERATE_ERROR).toBe('Error generating Last.fm radio');
  });

  it('reports success plainly', () => {
    expect(lastfmGenerateOutcome({ success: true })).toEqual({ ok: true });
  });
});

describe('the section’s visibility', () => {
  it('hides entirely when Last.fm is not configured', () => {
    expect(lastfmSectionVisibility(true, false)).toBe('hide');
    expect(lastfmSectionVisibility(true, undefined)).toBe('hide');
  });

  it('shows when configured', () => {
    expect(lastfmSectionVisibility(true, true)).toBe('show');
  });

  it('LEAVES IT ALONE when the check itself fails', () => {
    // A transient error must not blank a section that is already rendering.
    expect(lastfmSectionVisibility(false, true)).toBe('leave-alone');
    expect(lastfmSectionVisibility(false, undefined)).toBe('leave-alone');
  });

  it('reads the configured endpoint', () => {
    expect(LASTFM_CONFIGURED_URL).toBe('/api/lastfm/configured');
  });
});

describe('the radio playlists', () => {
  it('reuses the ListenBrainz card builder under its own kind', () => {
    expect(LASTFM_RADIO_CARD_KIND).toBe('lastfm_radio');
    expect(LASTFM_RADIO_PLAYLISTS_URL).toBe('/api/discover/listenbrainz/lastfm-radio');
  });

  it('empties the container for an unsuccessful or empty response', () => {
    expect(lastfmPlaylists({ success: true, playlists: [{}, {}] })).toHaveLength(2);
    expect(lastfmPlaylists({ success: true, playlists: [] })).toEqual([]);
    expect(lastfmPlaylists({ success: false, playlists: [{}] })).toEqual([]);
    expect(lastfmPlaylists({ success: true })).toEqual([]);
    expect(lastfmPlaylists(null)).toEqual([]);
  });
});
