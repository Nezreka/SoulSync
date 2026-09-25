import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';

import { describe, expect, it } from 'vitest';

/**
 * Cross-page navigation goes through window.navigateToPage, never the raw
 * SoulSyncWebRouter bridge.
 *
 * globals.d.ts says it above the declaration: navigateToPage is the entry that
 * does the permission guard, the sidebar chrome (setActivePageChrome) and the
 * currentPage bookkeeping before handing off to the router. The bridge does
 * only the last part — so the URL changes, the page changes, and the sidebar
 * carries on highlighting wherever you came from.
 *
 * Reported on the Deezer editorial shelf: clicking a playlist landed the user
 * on Sync with Discover still selected. Three call sites had it, and one of
 * them was the precedent the other two were copied from, which is why this is a
 * test rather than three fixes.
 */

const SRC = join(__dirname, '..', '..');

function sourceFiles(dir: string, found: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const path = join(dir, entry);
    if (statSync(path).isDirectory()) {
      sourceFiles(path, found);
    } else if (/\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry) && !entry.endsWith('.d.ts')) {
      found.push(path);
    }
  }
  return found;
}

/** Call sites that navigate to another PAGE through the raw bridge. */
function bridgeNavigations(): string[] {
  const offenders: string[] = [];
  for (const file of sourceFiles(SRC)) {
    const source = readFileSync(file, 'utf8');
    // the shell's own router plumbing legitimately talks to the bridge
    if (file.includes(join('platform', 'shell')) || file.includes(join('shell', ''))) continue;
    for (const line of source.split('\n')) {
      if (/SoulSyncWebRouter\??\.?\s*\.?navigateToPage/.test(line)) {
        offenders.push(`${file.slice(SRC.length + 1)}: ${line.trim()}`);
      }
    }
  }
  return offenders;
}

describe('cross-page navigation uses the sidebar-aware entry', () => {
  it('scans a real tree', () => {
    // a walk that found nothing would make the check below vacuous
    expect(sourceFiles(SRC).length).toBeGreaterThan(100);
  });

  it('no route navigates through the raw router bridge', () => {
    expect(
      bridgeNavigations(),
      'these move the URL but leave the sidebar on the previous page; call window.navigateToPage',
    ).toEqual([]);
  });
});
