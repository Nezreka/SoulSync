import { describe, expect, it } from 'vitest';

import { DOCS_SECTIONS } from './-help.sections';

describe('DOCS_SECTIONS', () => {
  it('carries the full document, not a stub', () => {
    // The legacy doc had 8+ sections; an import/extraction accident that
    // dropped sections would otherwise only show up as a shorter page.
    expect(DOCS_SECTIONS.length).toBeGreaterThanOrEqual(8);
    expect(DOCS_SECTIONS[0].id).toBe('getting-started');
    expect(DOCS_SECTIONS[0].title).toBe('Getting Started');
  });

  it('has unique section and child ids (the anchor namespace)', () => {
    const ids = new Set<string>();
    for (const section of DOCS_SECTIONS) {
      expect(ids.has(section.id)).toBe(false);
      ids.add(section.id);
      for (const child of section.children) {
        expect(ids.has(child.id)).toBe(false);
        ids.add(child.id);
      }
    }
  });

  it('renders an anchor for every nav child', () => {
    // Every nav child scrolls to id=<child.id>; a child without its anchor is
    // a dead nav entry (the legacy page had the same implicit contract).
    for (const section of DOCS_SECTIONS) {
      const html = section.content();
      for (const child of section.children) {
        expect(html, `${section.id} → ${child.id}`).toContain(`id="${child.id}"`);
      }
    }
  });

  it('registers the try-it handler when the API section renders', () => {
    const api = DOCS_SECTIONS.find((s) => s.id === 'api');
    expect(api).toBeDefined();
    const html = api!.content();
    // Rendering the section is what arms the Try It buttons…
    expect(typeof window._apiTryIt).toBe('function');
    expect(html).toContain('api-try-btn');
    // …and the example payloads must arrive HTML-escaped, never raw.
    expect(html).not.toContain('<script');
  });
});
