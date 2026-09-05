import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * The surfaces #1141 named must actually ASK for a thumbnail.
 *
 * This exists because of a real mistake: the server side of `?v=` was built,
 * tested and shipped while zero call sites used it. Every test passed, the
 * Settings toggle appeared, and turning it on would have changed nothing a
 * user could see — the resize was dead code. `git grep v=grid` across the
 * frontend returned nothing.
 *
 * A source-level check rather than a render: the failure mode is a call site
 * quietly losing the wrapper during a refactor, and this catches that in the
 * one place someone would look.
 */
const SURFACES: Array<[string, string]> = [
  ['discover album shelves', 'src/routes/discover/-ui/album-shelves.tsx'],
  ['dashboard content rails', 'src/routes/dashboard/-ui/content-rails.tsx'],
  ['library artist + album grids', 'src/routes/library/-ui/library-v2-page.tsx'],
];

describe('the named surfaces request a sized image', () => {
  it.each(SURFACES)('%s uses thumb()', (_label, relative) => {
    const source = readFileSync(resolve(__dirname, '../../', relative), 'utf8');

    expect(source).toContain("from '@/platform/artwork-thumb'");
    // Non-greedy across the argument list: a call site may nest parens,
    // e.g. thumb(String(album.thumb_url), 'card').
    expect(source).toMatch(/thumb\([\s\S]*?'(grid|card|hero)'\)/);
  });
});
