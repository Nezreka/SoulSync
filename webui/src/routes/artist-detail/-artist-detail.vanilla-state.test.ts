import { afterEach, describe, expect, it } from 'vitest';

import {
  clearVanillaArtist,
  syncVanillaArtist,
  syncVanillaEnhancedData,
  syncVanillaSelection,
  type VanillaArtistState,
} from './-artist-detail.vanilla-state';

/** The shape library.js declares, defaults and all. */
function installVanillaState(): VanillaArtistState {
  const state: VanillaArtistState = {
    currentArtistId: null,
    currentArtistName: null,
    currentArtistSource: null,
    enhancedData: null,
    selectedTracks: new Set<string>(),
  };
  (window as { artistDetailPageState?: VanillaArtistState }).artistDetailPageState = state;
  return state;
}

afterEach(() => {
  delete (window as { artistDetailPageState?: VanillaArtistState }).artistDetailPageState;
});

describe('syncVanillaArtist', () => {
  it('writes the artist through to the vanilla page state', () => {
    const state = installVanillaState();
    syncVanillaArtist({ id: 42, name: 'Aphex Twin', source: 'spotify' });

    expect(state.currentArtistId).toBe(42);
    expect(state.currentArtistName).toBe('Aphex Twin');
    expect(state.currentArtistSource).toBe('spotify');
  });

  it('leaves fields it was not given alone', () => {
    const state = installVanillaState();
    syncVanillaArtist({ id: 42, name: 'A', source: 'spotify' });
    syncVanillaArtist({ name: 'B' });

    expect(state.currentArtistName).toBe('B');
    expect(state.currentArtistId).toBe(42);
  });

  it('normalises undefined to null rather than leaving it undefined', () => {
    // The vanilla treats null as "no artist"; undefined would read the same
    // way to a truthiness check but not to `=== null`.
    const state = installVanillaState();
    syncVanillaArtist({ id: undefined, name: undefined, source: undefined });
    expect(state.currentArtistId).toBeNull();
    expect(state.currentArtistName).toBeNull();
  });

  it('clears everything on teardown', () => {
    const state = installVanillaState();
    syncVanillaArtist({ id: 42, name: 'A', source: 'spotify' });
    clearVanillaArtist();

    expect(state.currentArtistId).toBeNull();
    expect(state.currentArtistName).toBeNull();
    expect(state.currentArtistSource).toBeNull();
  });

  it('is a no-op when library.js is not loaded', () => {
    // The React page must not require the vanilla bundle to exist.
    expect(() => syncVanillaArtist({ id: 1 })).not.toThrow();
    expect(() => clearVanillaArtist()).not.toThrow();
    expect(() => syncVanillaEnhancedData(null)).not.toThrow();
    expect(() => syncVanillaSelection(new Set())).not.toThrow();
  });
});

describe('syncVanillaEnhancedData', () => {
  it('hands the payload over and takes it back', () => {
    const state = installVanillaState();
    const data = { albums: [{ id: 1 }] };

    syncVanillaEnhancedData(data);
    expect(state.enhancedData).toBe(data);

    syncVanillaEnhancedData(null);
    expect(state.enhancedData).toBeNull();
  });
});

describe('syncVanillaSelection', () => {
  it('MUTATES the existing Set rather than replacing it', () => {
    // deleteLibraryTrack deletes from this Set after a delete; swapping the
    // object out would leave those writes landing on a Set nobody reads.
    const state = installVanillaState();
    const original = state.selectedTracks;

    syncVanillaSelection(new Set(['1', '2']));

    expect(state.selectedTracks).toBe(original);
    expect([...state.selectedTracks]).toEqual(['1', '2']);
  });

  it('replaces the contents, dropping ids that are no longer ticked', () => {
    const state = installVanillaState();
    syncVanillaSelection(new Set(['1', '2']));
    syncVanillaSelection(new Set(['3']));
    expect([...state.selectedTracks]).toEqual(['3']);
  });

  it('survives a state object with no Set at all', () => {
    const state = installVanillaState();
    (state as { selectedTracks?: Set<string> }).selectedTracks = undefined;
    expect(() => syncVanillaSelection(new Set(['1']))).not.toThrow();
  });
});
