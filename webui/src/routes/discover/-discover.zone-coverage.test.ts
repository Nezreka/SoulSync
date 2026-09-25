import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { describe, expect, it } from 'vitest';

import { DISCOVER_LAYOUT, type DiscoverSectionId } from './-discover.layout';

/**
 * A section is only on screen if it is in a ZONE list.
 *
 * DISCOVER_LAYOUT gives a section its order and its empty policy, and it is
 * natural to assume that is what puts it on the page. It is not. The page
 * renders through renderZoneSections([...]) calls with hardcoded ids, so a
 * section can be fully registered — in the union, in the layout, with a render
 * case and a hasContent branch — and still never appear anywhere.
 *
 * That is exactly what happened to the Deezer editorial shelf: everything was
 * wired except the zone list, and the row was simply absent from the page with
 * nothing to explain it.
 */

const PAGE = readFileSync(
  join(__dirname, '-ui', 'discover-page.tsx'),
  'utf8',
);

/** Every id passed to a renderZoneSections([...]) call. */
function zoneRenderedIds(): Set<string> {
  const ids = new Set<string>();
  const call = /renderZoneSections\(\s*\[([\s\S]*?)\]\s*\)/g;
  let match: RegExpExecArray | null;
  while ((match = call.exec(PAGE)) !== null) {
    for (const id of match[1].matchAll(/'([a-z0-9-]+)'/g)) ids.add(id[1]);
  }
  return ids;
}

/** The ids the layout says belong on the page. */
function layoutIds(): DiscoverSectionId[] {
  return DISCOVER_LAYOUT.flatMap((e) => (e.kind === 'single' ? [e.id] : e.ids));
}

describe('every laid-out section is actually rendered by a zone', () => {
  it('finds the zone lists at all', () => {
    // a regex that matched nothing would make the check below vacuous
    const rendered = zoneRenderedIds();
    expect(rendered.size).toBeGreaterThan(10);
    expect(rendered.has('listenbrainz')).toBe(true);
  });

  it('leaves no section registered but invisible', () => {
    const rendered = zoneRenderedIds();
    // 'adv-wave' is the dial, rendered inline rather than as a zone section
    const orphans = layoutIds().filter((id) => id !== 'adv-wave' && !rendered.has(id));
    expect(orphans, 'these sections are in DISCOVER_LAYOUT but no zone renders them').toEqual([]);
  });

  it('renders the Deezer editorial shelf', () => {
    expect(zoneRenderedIds().has('deezer-editorial')).toBe(true);
  });
});
