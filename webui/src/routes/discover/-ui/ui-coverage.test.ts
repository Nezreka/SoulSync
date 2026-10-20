import { readdirSync, readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * Keep the completed Discover UI inventory from losing a component or feeder.
 * This source-level check catches missing pieces, not rendering or behavior;
 * those are covered by the component and hook suites.
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
  /*
   * NOT the tabbed UI. The live loader (loadDecadeBrowserTabs, 2647) abandoned
   * the decade tabs — "#discover redesign: Time Machine is now a shelf of
   * decade mix cards": it hides #decade-tabs and renders decadeMix cards via
   * _renderMixGrid into the shared mix shelf/modal, all of which is ported.
   * The tab controller is a closed dead cycle (_getDecadeBrowserTabsCtrl ←
   * loadDecadeTracks ← switchDecadeTab ← the ctrl's own tab buttons) that the
   * live path never enters, so there is no tab-contents component to build.
   * What remains is the hook FEEDER: fetch available decades, decadeMix them
   * onto the shelf, and wire Sync/Download through these track converters.
   * Resolves at #251 via the route-directory scan.
   */
  'decade shelf feeder': ['decadeTrackToSpotify', 'decadeHasTracks'],
};

describe('every UI piece the page needs has a component', () => {
  // -ui components PLUS the route level (route.tsx, the page hook): shelf
  // FEEDERS live in the hook, not in a component, and must still count.
  const components = [
    ...readdirSync(UI)
      .filter((f) => f.endsWith('.tsx') && !f.endsWith('.test.tsx'))
      .map((f) => readFileSync(join(UI, f), 'utf8')),
    ...readdirSync(ROUTE)
      .filter(
        (f) =>
          /\.(tsx|ts)$/.test(f) &&
          !/\.test\.tsx?$/.test(f) &&
          // Module files are the SUPPLY side; the consumers that satisfy a
          // feeder entry are route.tsx and the page hooks (-discover.use-*).
          (!f.startsWith('-discover.') || f.startsWith('-discover.use-')),
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

  it('keeps every required component or feeder', () => {
    const missing = Object.entries(REQUIRED_UI)
      .filter(([, symbols]) => !built(symbols))
      .map(([name]) => name)
      .sort();

    expect(
      missing,
      missing.length ? `\nThese lost their component:\n  ${missing.join('\n  ')}\n` : undefined,
    ).toEqual([]);
    // An empty inventory must not silently pass.
    expect(Object.keys(REQUIRED_UI).length).toBeGreaterThan(20);
  });
});
