import { describe, expect, it } from 'vitest';

import type { ByltPayload, ByltSection, ByltTrack } from './-discover.bylt';

import {
  byltCarouselId,
  byltDuration,
  byltGenerationId,
  byltHasArtistImage,
  byltIsCompact,
  byltIsInsufficient,
  byltIsLegacy,
  byltReasonLabel,
  byltRow,
  byltSections,
  byltShelfKey,
  byltShelfRows,
  byltShelfTitle,
  byltShelfVirtualId,
  byltStatusNote,
  byltTrackToRow,
  byltTracks,
  byltUnavailableNote,
  BYLT_ANCHOR_ID,
  BYLT_CONTAINER_ID,
  BYLT_LOADING_MESSAGE,
  BYLT_RENDERS_EMPTY_STATE,
  BYLT_STALE_MS,
  BYLT_SUBTITLE,
} from './-discover.bylt';

/**
 * The BYLT view model.
 *
 * The container/anchor constants are still here — index.html ships no
 * placeholder for this section, and losing that dependency is easy in a
 * declarative port. Everything else pins the payload the rebuilt endpoint now
 * serves: identity per section and per track, a truthful reason, honest
 * counts, and a freshness policy instead of `Infinity`.
 */

const track = (over: Partial<ByltTrack> = {}): ByltTrack => ({
  id: '9884087',
  name: 'Millicent',
  artist: 'Halogen',
  album: 'Baked',
  image_url: '/img/baked.jpg',
  duration_ms: 187000,
  relation: 'direct',
  relation_detail: 'Halogen',
  ...over,
});

const section = (over: Partial<ByltSection> = {}): ByltSection => ({
  seed_key: 'deezer:111',
  artist_name: 'Katy Perry',
  artist_image: '/img/katy.jpg',
  reason: { kind: 'direct', label: 'Artists similar to Katy Perry' },
  presentation: 'full',
  requested: 1,
  resolved: 1,
  unavailable: 0,
  tracks: [track()],
  ...over,
});

describe('the section contract', () => {
  it('keeps the anchor the vanilla loader depended on', () => {
    expect(BYLT_CONTAINER_ID).toBe('discover-bylt-sections');
    expect(BYLT_ANCHOR_ID).toBe('discover-release-radar');
    expect(BYLT_SUBTITLE).toBe('Because you listen to');
    // one of the few sections that opts OUT of the shared empty state
    expect(BYLT_RENDERS_EMPTY_STATE).toBe(false);
    expect(BYLT_LOADING_MESSAGE).toBe('');
  });

  it('unwraps sections and tolerates a missing list', () => {
    expect(byltSections({ sections: [section()] })).toHaveLength(1);
    expect(byltSections(null)).toEqual([]);
    expect(byltSections({})).toEqual([]);
  });

  it('guards a section with no tracks array, costing only that shelf', () => {
    expect(byltTracks(section({ tracks: undefined }))).toEqual([]);
    expect(byltTracks(section())).toHaveLength(1);
  });

  it('omits the header image when absent', () => {
    expect(byltHasArtistImage(section())).toBe(true);
    expect(byltHasArtistImage(section({ artist_image: undefined }))).toBe(false);
  });

  it('keys a shelf by seed identity, not by position', () => {
    expect(byltShelfKey(section(), 0)).toBe('deezer:111');
    expect(byltShelfKey(section(), 2)).toBe('deezer:111');
    // only a payload with no identity at all falls back to the index
    expect(byltShelfKey({ artist_name: 'X' }, 1)).toBe('X:1');
  });

  it('still ids each grid by index (10377)', () => {
    expect(byltCarouselId(0)).toBe('bylt-carousel-0');
  });
});

describe('rows', () => {
  it('reads name and artist — NOT the artist_name family', () => {
    const row = byltRow(track(), 0);
    expect(row.title).toBe('Millicent');
    expect(row.artist).toBe('Halogen');
    expect(row.album).toBe('Baked');
  });

  it('formats a duration and leaves an unknown one EMPTY', () => {
    expect(byltDuration(187000)).toBe('3:07');
    expect(byltDuration(0)).toBe('');
    expect(byltDuration(undefined)).toBe('');
    // the daily-mix bug: milliseconds must not be multiplied again
    expect(byltDuration(367725)).toBe('6:07');
  });

  it('shows the placeholder only for artless rows', () => {
    expect(byltRow(track(), 0).showPlaceholder).toBe(false);
    expect(byltRow(track({ image_url: undefined }), 0).showPlaceholder).toBe(true);
  });

  it('states the relationship it actually has, and nothing when it has none', () => {
    expect(byltRow(track(), 0).why).toBe('Similar artist: Halogen');
    expect(byltRow(track({ relation: 'genre', relation_detail: 'shoegaze' }), 0).why).toBe(
      'Shares shoegaze',
    );
    expect(byltRow(track({ relation: undefined, relation_detail: '' }), 0).why).toBe('');
  });

  it('reports whether a row can be acted on by identity', () => {
    expect(byltRow(track(), 0).hasIdentity).toBe(true);
    expect(byltRow(track({ id: undefined }), 0).hasIdentity).toBe(false);
  });

  it('labels an owned row owned rather than hiding it', () => {
    expect(byltRow(track({ owned: true }), 0).owned).toBe(true);
  });
});

describe('honesty', () => {
  it('uses the section reason and never invents one', () => {
    expect(byltReasonLabel(section())).toBe('Artists similar to Katy Perry');
    expect(byltReasonLabel(section({ reason: undefined }))).toBe('From your Katy Perry listening');
    expect(byltReasonLabel({})).toBe('');
  });

  it('says what went missing instead of quietly rendering fewer rows', () => {
    expect(byltUnavailableNote(section())).toBe('');
    expect(byltUnavailableNote(section({ requested: 10, resolved: 3, unavailable: 7 }))).toBe(
      '7 of 10 are no longer available',
    );
    expect(
      byltUnavailableNote(
        section({
          requested: 10,
          unavailable: 10,
          unavailable_reasons: { 'source-unsupported': 10 },
        }),
      ),
    ).toContain('metadata source');
  });

  it('separates a stale set from a failed one from an empty one', () => {
    expect(byltStatusNote({ status: 'stale' })).toContain('last good set');
    expect(byltStatusNote({ status: 'failed' })).toContain("Couldn't build");
    expect(byltStatusNote({ status: 'ok' })).toBe('');
    expect(byltStatusNote({ status: 'empty' })).toBe('');
    expect(byltStatusNote(null)).toBe('');
  });

  it('marks pre-generation ordinal rows as legacy', () => {
    expect(byltIsLegacy({ legacy: true })).toBe(true);
    expect(byltIsLegacy({ legacy: false })).toBe(false);
  });

  it('treats anything under the bar as compact, and an empty shelf as insufficient', () => {
    expect(byltIsCompact(section({ presentation: 'full' }))).toBe(false);
    expect(byltIsCompact(section({ presentation: 'compact' }))).toBe(true);
    expect(byltIsInsufficient(section({ tracks: [] }))).toBe(true);
    expect(byltIsInsufficient(section({ presentation: 'insufficient' }))).toBe(true);
    expect(byltIsInsufficient(section())).toBe(false);
  });
});

describe('cache identity', () => {
  it('exposes the generation so the client key follows the content', () => {
    expect(byltGenerationId({ generation_id: 'g7' } as ByltPayload)).toBe('g7');
    expect(byltGenerationId(null)).toBe('none');
  });

  it('has a finite freshness policy, not Infinity', () => {
    expect(Number.isFinite(BYLT_STALE_MS)).toBe(true);
    expect(BYLT_STALE_MS).toBeGreaterThan(0);
  });
});

describe('action shapes', () => {
  it('renames the fields the download and sync converters read', () => {
    const row = byltTrackToRow(track());
    expect(row.track_name).toBe('Millicent');
    expect(row.artist_name).toBe('Halogen');
    expect(row.album_name).toBe('Baked');
    expect(row.album_cover_url).toBe('/img/baked.jpg');
    expect(row.duration_ms).toBe(187000);
    expect(row.id).toBe('9884087');
  });

  it('passes a rich track_data_json through untouched', () => {
    const payload = { id: 'x', name: 'n', artists: [{ name: 'a' }] };
    expect(byltTrackToRow(track({ track_data_json: payload })).track_data_json).toBe(payload);
  });

  it('gives a shelf a seed-scoped operation identity', () => {
    expect(byltShelfVirtualId(section())).toBe('discover_bylt_deezer_111');
    // two shelves can never collide, because the seed is in the key
    expect(byltShelfVirtualId(section({ seed_key: 'deezer:222' }))).toBe(
      'discover_bylt_deezer_222',
    );
    expect(byltShelfTitle(section())).toBe('Because you listen to Katy Perry');
  });

  it('converts a whole shelf at once', () => {
    expect(byltShelfRows(section())).toHaveLength(1);
    expect(byltShelfRows(section({ tracks: undefined }))).toEqual([]);
  });
});
