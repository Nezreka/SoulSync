import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { beforeAll, describe, expect, it } from 'vitest';

/**
 * #2174, the Help & Docs page itself: a member's menu, its search and its
 * header follow what they may read, and the Report a problem button (the
 * diagnostics, once Copy Debug Info) is the admin's. runs the real docs.js.
 */

const STATIC = join(process.cwd(), 'static');
let perms: Record<string, boolean> = {};
let admin = false;

beforeAll(() => {
  window.getCurrentProfileContext = () => ({ profileId: admin ? 1 : 4, isAdmin: admin }) as never;
  window.SoulSyncPerms = {
    on: (id: string) => perms[id] === true,
    mode: () => 'ask',
    get: () => ({ live: true, isAdmin: admin }),
  } as never;
  document.body.innerHTML =
    '<div class="docs-layout"><aside><div class="docs-sidebar-header"><h3>Docs</h3></div>' +
    '<nav id="docs-nav"></nav></aside><main id="docs-content"></main></div>';
  perms = { 'music.access': true, 'music.search': true, 'music.library': true };
  (0, eval)(readFileSync(join(STATIC, 'docs.js'), 'utf8'));
  for (const f of readdirSync(join(STATIC, 'docs-content'))
    .filter((x) => x.endsWith('.js'))
    .sort()) {
    (0, eval)(readFileSync(join(STATIC, 'docs-content', f), 'utf8'));
  }
  (window as unknown as { initializeDocsPage: () => void }).initializeDocsPage();
});

const navText = () => document.getElementById('docs-nav')!.textContent ?? '';

describe('the docs page for a member', () => {
  it('lists none of the admin guides, and no diagnostics', () => {
    expect(navText()).toContain('Overview');
    expect(navText()).not.toContain('Docker & Deployment');
    expect(navText()).not.toContain('Settings');
    expect(navText()).not.toContain('API Reference');
    expect(document.getElementById('dd-debug-btn')).toBeNull();
  });

  it('a link to an admin guide opens the first guide they may read instead', () => {
    window.navigateToDocsSection!('gs-docker');
    return new Promise<void>((done) =>
      setTimeout(() => {
        expect(document.getElementById('docs-gs-docker')).toBeNull();
        done();
      }, 120),
    );
  });

  it('follows an admin change: new rights bring their guides, the admin gets everything', () => {
    expect(navText()).not.toContain('Playlist Sync');
    perms = { ...perms, 'music.playlist_sync': true };
    window.dispatchEvent(new CustomEvent('ss:perms-changed'));
    expect(navText()).toContain('Playlist Sync');

    admin = true;
    window.dispatchEvent(new CustomEvent('ss:webui-profile-context-changed'));
    expect(navText()).toContain('Docker & Deployment');
    expect(document.getElementById('dd-debug-btn')).not.toBeNull();

    admin = false;
    window.dispatchEvent(new CustomEvent('ss:perms-changed'));
    expect(navText()).not.toContain('Docker & Deployment');
    expect(document.getElementById('dd-debug-btn')).toBeNull();
  });
});
