import { readdirSync, readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * Which of the page's UI pieces actually have a component.
 *
 * WHY THIS EXISTS. I reported the component layer as complete. It was not: five
 * cache shelves, the BYLT sections, the seasonal PLAYLIST section and six modals
 * had no component at all. Every one of their logic modules was ported and
 * tested, the whole suite was green, and every other gate passed — because
 * nothing anywhere asked the question "is there UI for this?".
 *
 * "Done" was my judgement, and my judgement was wrong. So it is a test now.
 *
 * ── How it works ───────────────────────────────────────────────────────────
 *
 * Each entry names a UI piece and a few symbols only its component could
 * plausibly use. If no component mentions any of them, the piece is missing.
 * That is deliberately loose: the point is to catch a piece that does not exist
 * AT ALL, not to police how it is built.
 *
 * MISSING_UI is a ratchet. It may only shrink, and a piece that gains a
 * component fails the test until it is removed from the list — so the list
 * cannot quietly go stale the way a comment would.
 */

const UI = resolve(process.cwd(), 'src/routes/discover/-ui');

/** Every UI piece the discover page needs, and the symbols that would build it. */
const REQUIRED_UI: Record<string, string[]> = {
  hero: ['DiscoverHero'],
  'section shell': ['DiscoverSection'],
  'recommendation shelves': ['RecommendedShelf'],
  'recommended modal': ['RecommendedModal'],
  'your artists shelf': ['YourArtistsShelf'],
  'your albums shelf': ['YourAlbumsShelf'],
  'recent releases': ['RecentReleasesShelf'],
  'seasonal albums': ['SeasonalAlbumsShelf'],
  'mix shelf': ['MixShelf'],
  'mix modal': ['MixModal'],
  'sync panel': ['SyncStatus'],
  'lastfm radio': ['LastfmRadioSection'],
  listenbrainz: ['ListenBrainzSection'],
  'adventurousness dial': ['AdventurousnessDial'],
  'download sidebar': ['DownloadBar'],
  'build a playlist': ['BuildPlaylistSection'],
  'artist map hub': ['ArtistMapHub'],
  'artist map overlay': ['ArtMapOverlay'],
  'artist map panel': ['ArtMapPanel'],
  'artist map chrome': ['ArtMapTooltipView'],
  'artist web overlay': ['ArtWebOverlay'],
  'artist web panel': ['ArtWebArtistCard'],

  // ── Not built yet ────────────────────────────────────────────────────────
  'cache-* shelves': ['cacheDiscoverCard', 'CACHE_SECTIONS', 'genrePill'],
  'BYLT sections': ['byltTrackCard', 'byltSections', 'BYLT_CONTAINER_ID'],
  'seasonal playlist section': ['seasonalMixTitles', 'seasonalHasPlaylist'],
  'blacklist modal': ['blacklistEntries', 'BLACKLIST_TITLE'],
  'your artists modal': ['artistsModalPager', 'applyArtistsModalFilter'],
  'artist info modal': ['infoStats', 'infoMatchBadges'],
  'your artists sources modal': ['ARTISTS_SOURCE_INFO', 'toggleArtistSource'],
  'your albums batch modal': ['prepareBatchRows', 'batchFooter'],
  'your albums sources modal': ['YOUR_ALBUMS_SOURCE_INFO', 'toggleSource'],
  'artist map info modal': ['artMapInfoBest', 'artMapInfoSourceOrder'],
  'decade tab contents': ['decadeTrackToSpotify', 'decadeHasTracks'],
};

/**
 * The pieces with no component yet. This number may only go DOWN.
 *
 * Each one is a section or modal a user can reach in the vanilla today, so the
 * route flip cannot happen while this list is non-empty without losing it.
 */
const MISSING_UI = [
  'BYLT sections',
  'artist info modal',
  'artist map info modal',
  'blacklist modal',
  'cache-* shelves',
  'decade tab contents',
  'seasonal playlist section',
  'your albums batch modal',
  'your albums sources modal',
  'your artists modal',
  'your artists sources modal',
];

describe('every UI piece the page needs has a component', () => {
  const components = readdirSync(UI)
    .filter((f) => f.endsWith('.tsx') && !f.endsWith('.test.tsx'))
    .map((f) => readFileSync(join(UI, f), 'utf8'))
    .join('\n');

  /**
   * Is this piece built?
   *
   * A COMPONENT name (capitalised) must be DECLARED — `export function X`. Merely
   * naming it is not enough: another component importing `SyncStatus` kept this
   * reporting the sync panel as built after its declaration had been renamed
   * away. A helper name (lowercase, or a CONST) only has to be REFERENCED, since
   * a component uses those rather than defining them.
   *
   * Word-bounded either way, so `SyncStatusRenamed` does not satisfy
   * `SyncStatus`.
   */
  const built = (symbols: string[]) =>
    symbols.some((s) =>
      /^[A-Z][a-z]/.test(s)
        ? new RegExp(`export function ${s}\\b`).test(components)
        : new RegExp(`\\b${s}\\b`).test(components),
    );

  it('matches the recorded gap exactly, so the list can only shrink', () => {
    const missing = Object.entries(REQUIRED_UI)
      .filter(([, symbols]) => !built(symbols))
      .map(([name]) => name)
      .sort();

    const gained = missing.filter((m) => !MISSING_UI.includes(m));
    const fixed = MISSING_UI.filter((m) => !missing.includes(m));

    expect(
      missing,
      gained.length
        ? `\nUI that USED to exist and no longer does:\n  ${gained.join('\n  ')}\n`
        : fixed.length
          ? `\nBuilt now — remove from MISSING_UI:\n  ${fixed.join('\n  ')}\n`
          : undefined,
    ).toEqual([...MISSING_UI].sort());
  });

  it('confirms the pieces that ARE built', () => {
    const shouldExist = Object.keys(REQUIRED_UI).filter((k) => !MISSING_UI.includes(k));
    const broken = shouldExist.filter((k) => !built(REQUIRED_UI[k]));
    expect(
      broken,
      broken.length ? `\nThese lost their component:\n  ${broken.join('\n  ')}\n` : undefined,
    ).toEqual([]);
    // A guard that asserted nothing would also pass an empty REQUIRED_UI.
    expect(shouldExist.length).toBeGreaterThan(20);
  });
});
