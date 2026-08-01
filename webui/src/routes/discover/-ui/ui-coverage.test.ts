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
const ROUTE = resolve(process.cwd(), 'src/routes/discover');

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

  'BYLT sections': ['ByltSections'],

  // ── Not built yet ────────────────────────────────────────────────────────
  'cache-* shelves': ['CacheShelf', 'GenreExplorerSection'],
  /*
   * Discovered while building the cache shelves: the Genre Explorer pills open
   * `openGenreDeepDive` (discover.js 10790) — a full modal with artist/track/
   * album grids — which no entry recorded. New REQUIRED entry, born missing.
   */
  'genre deep dive modal': ['GenreDiveModal'],
  /*
   * NOT a section. The vanilla's loader renders nothing into
   * #seasonal-playlist-section — it collapses it (_collapseOldMixSection,
   * 4370) and upserts a MIX CARD onto the Your Mixes shelf. This entry is the
   * page hook's FEEDER: fetch the seasonal tracks and upsertShelfMix with the
   * seasonalMixTitles title. It resolves with #251, which is why the scan
   * covers the route directory as well as -ui/.
   */
  'seasonal playlist shelf feeder': ['seasonalMixTitles', 'seasonalHasPlaylist'],
  'blacklist modal': ['BlacklistModal'],
  'your artists modal': ['YourArtistsModal'],
  'artist info modal': ['ArtistInfoModal'],
  'your artists sources modal': ['ARTISTS_SOURCE_INFO', 'toggleArtistSource'],
  'your albums batch modal': ['YourAlbumsBatchModal'],
  'your albums sources modal': ['YOUR_ALBUMS_SOURCE_INFO', 'toggleSource'],
  /*
   * NOT a second modal. `openYourArtistInfoModal_direct` (10285) adapts a map
   * node into a pool entry and opens the SAME info modal; the adapter is
   * ported in -discover.artist-map.entry.ts. What remains is hook wiring —
   * feed the adapter's pool into <ArtistInfoModal> from the map's context
   * menu and island card — which is #251's, so these symbols resolve when the
   * route level references them.
   */
  'artist map info modal wiring': ['artMapInfoBest', 'artMapInfoSourceOrder'],
  'decade tab contents': ['decadeTrackToSpotify', 'decadeHasTracks'],
};

/**
 * The pieces with no component yet. This number may only go DOWN.
 *
 * Each one is a section or modal a user can reach in the vanilla today, so the
 * route flip cannot happen while this list is non-empty without losing it.
 */
const MISSING_UI = [
  'artist map info modal wiring',
  'genre deep dive modal',
  'decade tab contents',
  'seasonal playlist shelf feeder',
];

describe('every UI piece the page needs has a component', () => {
  // -ui components PLUS the route level (route.tsx, the page hook): shelf
  // FEEDERS live in the hook, not in a component, and must still count.
  const components = [
    ...readdirSync(UI)
      .filter((f) => f.endsWith('.tsx') && !f.endsWith('.test.tsx'))
      .map((f) => readFileSync(join(UI, f), 'utf8')),
    ...readdirSync(ROUTE)
      .filter(
        (f) => /\.(tsx|ts)$/.test(f) && !/\.test\.tsx?$/.test(f) && !f.startsWith('-discover.'),
      )
      .map((f) => readFileSync(join(ROUTE, f), 'utf8')),
  ].join('\n');

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
