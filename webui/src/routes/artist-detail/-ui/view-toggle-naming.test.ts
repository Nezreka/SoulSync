import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * The two views are two SOURCES, and the toggle should say so.
 *
 * "Standard" and "Enhanced" described how much UI you got, not what you were
 * looking at, and gave no hint that one of them only shows things you own.
 * Boulder: "instead of standard view and enhanced view on that page. it shoudl
 * be discography and User library. because thats what it is right?"
 *
 * It is. The types file settles it: "Absent => source-only artist: no library
 * record, no ownership, no Enhanced view." An artist you own nothing by has no
 * second view at all, because there is no library side to show.
 *
 * Upstream also asserted the button labels in discography-filters.tsx. That
 * control belonged to the legacy artist-detail page, which this branch
 * deleted — Library v2 splits the same two sources as "My Library" and "All
 * Releases" — so only the helper half, which reads the live helper.js, still
 * has a subject here.
 */

const HELPER = readFileSync(resolve(process.cwd(), 'static/helper.js'), 'utf8');

describe('the helper popovers agree with the buttons', () => {
  it('would otherwise call the same control two different things', () => {
    const standard = HELPER.slice(
      HELPER.indexOf('[data-view="standard"]'),
      HELPER.indexOf('[data-view="enhanced"]'),
    );
    const enhanced = HELPER.slice(HELPER.indexOf('[data-view="enhanced"]'));
    expect(standard).toContain("title: 'Discography'");
    expect(enhanced.slice(0, 400)).toContain("title: 'Your library'");
  });

  it('says the library view is empty for an artist you own nothing by', () => {
    const enhanced = HELPER.slice(HELPER.indexOf('[data-view="enhanced"]'));
    expect(enhanced.slice(0, 600)).toContain('own nothing by');
  });
});
