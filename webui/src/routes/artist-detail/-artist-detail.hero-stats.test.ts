import { describe, expect, it } from 'vitest';

import {
  buildGenreChips,
  categoryStats,
  cleanArtistBio,
  formatHeroNumber,
  heroImage,
  heroImageSrc,
  heroReleaseImage,
  isUsableHeroImageUrl,
  nextHeroImageStage,
  summaryStats,
  totalReleaseCount,
} from './-artist-detail.hero-stats';

describe('isUsableHeroImageUrl', () => {
  it('rejects the literal string "null", not just null', () => {
    // The backend can serialise a missing image as the text "null", which
    // would otherwise be fetched as a relative url and 404.
    expect(isUsableHeroImageUrl('null')).toBe(false);
    expect(isUsableHeroImageUrl(null)).toBe(false);
    expect(isUsableHeroImageUrl('   ')).toBe(false);
    expect(isUsableHeroImageUrl('')).toBe(false);
    expect(isUsableHeroImageUrl('a.jpg')).toBe(true);
  });
});

describe('heroReleaseImage', () => {
  it('scans albums, then eps, then singles', () => {
    expect(
      heroReleaseImage({
        albums: [{}],
        eps: [{ image_url: 'ep.jpg' }],
        singles: [{ image_url: 's.jpg' }],
      }),
    ).toBe('ep.jpg');
  });

  it('skips unusable urls rather than returning them', () => {
    expect(heroReleaseImage({ albums: [{ image_url: 'null' }, { image_url: 'ok.jpg' }] })).toBe(
      'ok.jpg',
    );
  });

  it('returns empty when nothing has art', () => {
    expect(heroReleaseImage({ albums: [{}] })).toBe('');
    expect(heroReleaseImage(undefined)).toBe('');
  });
});

describe('heroImage', () => {
  it("prefers the artist's own photo", () => {
    const img = heroImage({ image_url: 'artist.jpg' }, { albums: [{ image_url: 'rel.jpg' }] });
    expect(img.primary).toBe('artist.jpg');
    expect(img.artistImage).toBe('artist.jpg');
  });

  it('falls back to release art when the artist has none', () => {
    const img = heroImage({}, { albums: [{ image_url: 'rel.jpg' }] });
    expect(img.primary).toBe('rel.jpg');
    expect(img.artistImage).toBe('');
  });

  it('has no primary at all when neither exists', () => {
    expect(heroImage({}, {}).primary).toBe('');
  });
});

describe('hero image fallback chain', () => {
  it('tries Deezer first when there is a deezer id', () => {
    const img = heroImage({ image_url: 'a.jpg', deezer_id: 7 }, {});
    expect(nextHeroImageStage('primary', { deezer_id: 7 }, img)).toBe('deezer');
    expect(heroImageSrc('deezer', { deezer_id: 7 }, img)).toBe(
      'https://api.deezer.com/artist/7/image?size=big',
    );
  });

  it('then falls to release art, then the icon', () => {
    const img = heroImage(
      { image_url: 'a.jpg', deezer_id: 7 },
      { albums: [{ image_url: 'rel.jpg' }] },
    );
    expect(nextHeroImageStage('deezer', { deezer_id: 7 }, img)).toBe('release');
    expect(nextHeroImageStage('release', { deezer_id: 7 }, img)).toBe('fallback');
  });

  it('does NOT retry release art when it was already the primary', () => {
    // The artist had no photo, so the release cover IS the primary — retrying
    // it would loop. This is what triedReleaseFallback pre-set guarded.
    const img = heroImage({ deezer_id: 7 }, { albums: [{ image_url: 'rel.jpg' }] });
    expect(nextHeroImageStage('deezer', { deezer_id: 7 }, img)).toBe('fallback');
  });

  it('skips Deezer entirely without a deezer id', () => {
    const img = heroImage({ image_url: 'a.jpg' }, { albums: [{ image_url: 'rel.jpg' }] });
    expect(nextHeroImageStage('primary', {}, img)).toBe('release');
  });

  it('goes straight to the icon with no deezer id and no release art', () => {
    expect(nextHeroImageStage('primary', {}, heroImage({ image_url: 'a.jpg' }, {}))).toBe(
      'fallback',
    );
  });
});

describe('categoryStats', () => {
  it('shows owned/total and a proportional bar', () => {
    expect(
      categoryStats([{ owned: true }, { owned: false }, { owned: true }, { owned: false }]),
    ).toEqual({
      text: '2/4',
      width: '50%',
      checking: false,
    });
  });

  it('treats an EMPTY category as 100% complete, not 0%', () => {
    // You cannot be missing anything from a bucket with nothing in it.
    expect(categoryStats([])).toEqual({ text: '0/0', width: '100%', checking: false });
  });

  it('shows a full animating bar while any check is pending', () => {
    expect(categoryStats([{ owned: null }, { owned: true }])).toEqual({
      text: '...',
      width: '100%',
      checking: true,
    });
  });
});

describe('summaryStats', () => {
  it('counts ALBUMS only, but checks pending across every bucket', () => {
    const stats = summaryStats({
      albums: [{ owned: true }, { owned: false }],
      eps: [{ owned: true }],
      singles: [],
    });
    expect(stats).toEqual({ owned: '1', missing: '1', completion: '50%' });
  });

  it('goes to ... when a SINGLE is still checking, though singles are not counted', () => {
    const stats = summaryStats({ albums: [{ owned: true }], singles: [{ owned: null }] });
    expect(stats).toEqual({ owned: '...', missing: '...', completion: 'Checking...' });
  });

  it('uses 0% for an empty album list — unlike categoryStats, which uses 100%', () => {
    expect(summaryStats({ albums: [] }).completion).toBe('0%');
  });
});

describe('formatHeroNumber', () => {
  it('abbreviates millions and thousands, trimming a trailing .0', () => {
    expect(formatHeroNumber(1_200_000)).toBe('1.2M');
    expect(formatHeroNumber(1_000_000)).toBe('1M');
    expect(formatHeroNumber(3400)).toBe('3.4K');
    expect(formatHeroNumber(1000)).toBe('1K');
  });

  it('leaves values under 1000 alone', () => {
    expect(formatHeroNumber(999)).toBe('999');
  });

  it('renders 0 for absent or non-positive values', () => {
    expect(formatHeroNumber(0)).toBe('0');
    expect(formatHeroNumber(undefined)).toBe('0');
    expect(formatHeroNumber(-5)).toBe('0');
  });
});

describe('cleanArtistBio', () => {
  it('removes anchors WHOLE, including their text', () => {
    // Last.fm always appends a "Read more on Last.fm" link.
    expect(cleanArtistBio('Great band. <a href="https://last.fm">Read more on Last.fm</a>')).toBe(
      'Great band.',
    );
  });

  it('strips other tags but keeps their text', () => {
    expect(cleanArtistBio('<b>Bold</b> claim')).toBe('Bold claim');
  });

  it('returns empty when only a link remains, so the block hides', () => {
    expect(cleanArtistBio('<a href="x">Read more</a>')).toBe('');
    expect(cleanArtistBio('   ')).toBe('');
    expect(cleanArtistBio(undefined)).toBe('');
  });
});

describe('totalReleaseCount', () => {
  it('sums every bucket — the Download Discography button keys off it', () => {
    expect(totalReleaseCount({ albums: [{}, {}], eps: [{}], singles: [] })).toBe(3);
    expect(totalReleaseCount({})).toBe(0);
    expect(totalReleaseCount(undefined)).toBe(0);
  });
});

describe('buildGenreChips', () => {
  it("lists the artist's own genres first, undimmed", () => {
    expect(buildGenreChips({ genres: ['IDM', 'Ambient'] })).toEqual([
      { label: 'IDM', fromLastfm: false },
      { label: 'Ambient', fromLastfm: false },
    ]);
  });

  it('appends Last.fm tags as dimmed extras', () => {
    const chips = buildGenreChips({ genres: ['IDM'], lastfm_tags: ['electronic'] });
    expect(chips).toEqual([
      { label: 'IDM', fromLastfm: false },
      { label: 'electronic', fromLastfm: true },
    ]);
  });

  it('parses lastfm_tags when it arrives as a JSON STRING', () => {
    // The backend stores it serialised; a bare array check would drop them.
    const chips = buildGenreChips({ lastfm_tags: '["techno","acid"]' });
    expect(chips.map((c) => c.label)).toEqual(['techno', 'acid']);
  });

  it('swallows malformed tags rather than taking the hero down', () => {
    expect(buildGenreChips({ genres: ['IDM'], lastfm_tags: '{not json' })).toEqual([
      { label: 'IDM', fromLastfm: false },
    ]);
  });

  it('dedups against existing genres case-INSENSITIVELY', () => {
    // The tag must differ in case from the genre, or a case-SENSITIVE
    // comparison gives the same answer and the test proves nothing: the
    // existing-set is already lowercased, so a lowercase tag matches either way.
    const chips = buildGenreChips({ genres: ['Techno'], lastfm_tags: ['TECHNO', 'acid'] });
    expect(chips.map((c) => c.label)).toEqual(['Techno', 'acid']);
  });

  it('caps the Last.fm extras at 5, but does not cap real genres', () => {
    const chips = buildGenreChips({
      genres: ['a', 'b', 'c', 'd', 'e', 'f', 'g'],
      lastfm_tags: ['t1', 't2', 't3', 't4', 't5', 't6', 't7'],
    });
    expect(chips.filter((c) => !c.fromLastfm)).toHaveLength(7);
    expect(chips.filter((c) => c.fromLastfm)).toHaveLength(5);
  });

  it('handles an artist with neither', () => {
    expect(buildGenreChips({})).toEqual([]);
  });
});
