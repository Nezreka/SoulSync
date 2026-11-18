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
 */

const FILTERS = readFileSync(
  resolve(process.cwd(), 'src/routes/artist-detail/-ui/discography-filters.tsx'),
  'utf8',
);

describe('the view toggle', () => {
  it('names the two sources instead of grading the UI', () => {
    expect(FILTERS).toContain('Discography');
    expect(FILTERS).toContain('Your library');
    expect(FILTERS).not.toMatch(/>\s*Standard\s*</);
    expect(FILTERS).not.toMatch(/>\s*Enhanced\s*</);
  });

  it('keeps the data-view values, which css selects on', () => {
    // Renaming these would break .enhanced-view-toggle-btn[data-view="..."] in
    // style.css without making anything clearer.
    expect(FILTERS).toContain('data-view="standard"');
    expect(FILTERS).toContain('data-view="enhanced"');
  });

  it('explains the difference on hover, since the names are short', () => {
    expect(FILTERS).toContain('Everything this artist released');
    expect(FILTERS).toContain('What you actually own');
  });
});
