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
    // pin-ok: verifying that the set of layout ids matches the keys of DEFAULT_SECTION_ZONE
    expect(new Set(ids)).toEqual(new Set(Object.keys(DEFAULT_SECTION_ZONE)));
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

  it('offers the Layout customizer from the section nav', () => {
    expect(PAGE).toContain(
      '<DiscoverNav items={navItems} onOpenLayout={() => setLayoutOpen(true)} />',
    );
    expect(PAGE).toContain('DiscoverLayoutModal');
    expect(PAGE).toContain('setLayoutOpen(true)');
  });
});
