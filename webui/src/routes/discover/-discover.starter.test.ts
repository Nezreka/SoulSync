import { describe, expect, it } from 'vitest';

import {
  buildLine,
  canSavePicks,
  MIN_PICKS,
  pickKey,
  pickProgressLabel,
  seedFill,
  starterOffers,
  togglePick,
  type StarterStatus,
} from './-discover.starter';

function status(over: Partial<StarterStatus> = {}): StarterStatus {
  return {
    seeds: 1,
    threshold: 5,
    needs_seeds: true,
    dismissed: false,
    show_banner: true,
    picked: [],
    connections: { listenbrainz: false, lastfm: false },
    spotify_import: { available: false, href: null },
    build: { state: 'idle', done: 0, total: 0, stored: 0 },
    ...over,
  };
}

describe('what the starter banner offers', () => {
  it('leads with picking artists, then the accounts not yet connected', () => {
    expect(starterOffers(status()).map((o) => o.id)).toEqual(['pick', 'listenbrainz', 'lastfm']);
  });

  it('never offers an account that is already connected', () => {
    const ids = starterOffers(status({ connections: { listenbrainz: true, lastfm: false } })).map(
      (o) => o.id,
    );
    expect(ids).toEqual(['pick', 'lastfm']);
  });

  it('offers the spotify import only once the server has it, linking to it', () => {
    const offers = starterOffers(status({ spotify_import: { available: true, href: '/stats' } }));
    const spotify = offers.find((o) => o.id === 'spotify');
    expect(spotify?.href).toBe('/stats');
    expect(starterOffers(status()).some((o) => o.id === 'spotify')).toBe(false);
  });
});

describe('picking artists', () => {
  const a = { name: 'Tool', deezer_id: '1', image_url: null };
  const b = { name: 'Deftones', deezer_id: '2', image_url: null };

  it('toggles by deezer id and keeps the picking order', () => {
    let picked = togglePick([], a);
    picked = togglePick(picked, b);
    expect(picked.map((p) => p.name)).toEqual(['Tool', 'Deftones']);
    expect(togglePick(picked, { ...a, name: 'tool' }).map((p) => p.name)).toEqual(['Deftones']);
  });

  it('keys a pick without an id by its folded name', () => {
    expect(pickKey({ name: ' Björk ', deezer_id: null })).toBe('nm:björk');
  });

  it(`needs ${MIN_PICKS} before it can save, and says how many are left`, () => {
    expect(canSavePicks(MIN_PICKS - 1)).toBe(false);
    expect(canSavePicks(MIN_PICKS)).toBe(true);
    expect(pickProgressLabel(2)).toBe('2 of 5 · 3 more to go');
    expect(pickProgressLabel(7)).toBe('7 picked');
  });
});

describe('the build line and seed meter', () => {
  it('says how far the first build has got, only while it runs', () => {
    expect(buildLine({ state: 'running', done: 2, total: 5, stored: 20 })).toBe(
      'Building your first picks · 2 of 5 artists',
    );
    expect(buildLine({ state: 'done', done: 5, total: 5, stored: 50 })).toBeNull();
    expect(buildLine(null)).toBeNull();
  });

  it('fills the meter toward the threshold and never past it', () => {
    expect(seedFill({ seeds: 2, threshold: 5 })).toBeCloseTo(0.4);
    expect(seedFill({ seeds: 9, threshold: 5 })).toBe(1);
  });
});
