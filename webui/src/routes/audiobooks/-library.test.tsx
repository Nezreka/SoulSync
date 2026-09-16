import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * the library is a place with a url, not a dialog over the browse page.
 * read from the source: the route exists, the browse page links to it, and
 * nothing on the browse page still opens the modal.
 */
const ROUTE = readFileSync(join(__dirname, 'library.tsx'), 'utf8');
const PAGE = readFileSync(join(__dirname, '-ui', 'audiobooks-page.tsx'), 'utf8');
const TREE = readFileSync(join(__dirname, '..', '..', 'routeTree.gen.ts'), 'utf8');

describe('/audiobooks/library', () => {
  it('is a registered route rendering the embedded panel with a way back', () => {
    expect(ROUTE).toContain("createFileRoute('/audiobooks/library')");
    expect(ROUTE).toContain('<AudiobookLibraryPanel embedded />');
    expect(ROUTE).toContain('<AudiobookBackButton />');
    expect(TREE).toContain("'/audiobooks/library'");
  });

  it('is where the browse page sends you, instead of opening a modal', () => {
    expect(PAGE).toContain('to="/audiobooks/library"');
    expect(PAGE).not.toContain('AudiobookLibraryModal');
    expect(PAGE).not.toContain('setShowLibrary');
  });
});
