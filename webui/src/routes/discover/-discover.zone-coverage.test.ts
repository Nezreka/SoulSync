import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

import {
  DEFAULT_SECTION_ZONE,
  DISCOVER_LAYOUT,
  DISCOVER_ZONES,
  type DiscoverSectionId,
  type DiscoverZoneId,
} from './-discover.layout';

/**
 * The page's four zones render from the profile's layout, not hardcoded id
 * lists.
 *
 * The old version of this test parsed literal renderZoneSections([...]) calls
 * out of discover-page.tsx. The page now renders each zone's sections from
 * `pageLayout.sectionsByZone[zone]` (GET /api/discover/layout merged over the
 * defaults), so the coverage guarantee moves: every section id must have
 * exactly one default zone, and the page must render all four zones from the
 * layout — a section registered in the layout can no longer be silently absent
 * from a zone list (the Deezer editorial failure mode this test was written
 * for).
 */

const PAGE = readFileSync(join(__dirname, '-ui', 'discover-page.tsx'), 'utf8');

/** Every section the page knows about, in default order — a literal pin. */
const SECTION_IDS: DiscoverSectionId[] = [
  'your-mixes-section',
  'adv-wave',
  'listening-recs-section',
  'recommended-artists-section',
  'discover-bylt-sections',
  'recent-releases',
  'cache-genre-releases',
  'seasonal-albums-section',
  'cache-undiscovered',
  'cache-label-explorer',
  'your-albums-section',
  'your-artists-section',
  'year-mixes-section',
  'cache-deep-cuts',
  'cache-genre-explorer',
  'lastfm-radio',
  'listenbrainz',
  'deezer-editorial',
  'build-a-playlist',
];

/** The ids the layout says belong on the page. */
function layoutIds(): DiscoverSectionId[] {
  return DISCOVER_LAYOUT.flatMap((e) => (e.kind === 'single' ? [e.id] : e.ids));
}

describe('layout default zones cover every section exactly once', () => {
  it('knows all four zones', () => {
    expect(DISCOVER_ZONES.map((z) => z.id)).toEqual(['for-you', 'new-missing', 'library', 'tools']);
  });

  it('assigns every section to exactly one zone, no extras', () => {
    const ids = layoutIds();
    expect(ids).toHaveLength(19);
    const zones = Object.values(DEFAULT_SECTION_ZONE);
    expect(zones).toHaveLength(19);
    // The layout and the zone map must cover exactly these 19 sections — a
    // new section updates the literal above deliberately, not silently.
    expect(new Set(ids)).toEqual(new Set(SECTION_IDS));
    expect(Object.keys(DEFAULT_SECTION_ZONE)).toEqual(SECTION_IDS);
    for (const zone of DISCOVER_ZONES) {
      expect(zones).toContain(zone.id);
    }
    const perZone = Object.values(DEFAULT_SECTION_ZONE).reduce<Record<DiscoverZoneId, number>>(
      (acc, zone) => {
        acc[zone] += 1;
        return acc;
      },
      { 'for-you': 0, 'new-missing': 0, library: 0, tools: 0 },
    );
    expect(perZone).toEqual({ 'for-you': 5, 'new-missing': 6, library: 3, tools: 5 });
  });
});

describe('the page renders all four zones from the profile layout', () => {
  it('drives every zone from the layout, not hardcoded id lists', () => {
    for (const zone of DISCOVER_ZONES) {
      expect(PAGE, `discover-page.tsx should render the ${zone.id} zone from the layout`).toContain(
        `zoneSections('${zone.id}')`,
      );
    }
    expect(PAGE).toContain('useDiscoverLayout');
    expect(PAGE).not.toMatch(/renderZoneSections\(\s*\[/);
  });

  it('keeps StationsRow pinned right after Your Mixes in For You', () => {
    expect(PAGE).toContain('renderForYouSections');
    expect(PAGE).toContain("ids.indexOf('your-mixes-section')");
    expect(PAGE).toContain('<StationsRow');
  });

  it('offers the Layout customizer from the pill rail', () => {
    expect(PAGE).toContain('⚙️');
    expect(PAGE).toContain('DiscoverLayoutModal');
    expect(PAGE).toContain('setLayoutOpen(true)');
  });
});
