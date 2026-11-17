import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * #2155: the sidebar Service Status panel (which services the server runs on
 * and whether they're up) is the admin's. A regular profile saw it on both
 * sides. Runs init.js's real updateProfileIndicator against a small sidebar.
 */

const INIT = readFileSync(resolve(process.cwd(), 'static/init.js'), 'utf8');

function extract(name: string): string {
  const start = INIT.indexOf(`function ${name}() {`);
  expect(start, `${name} not found`).toBeGreaterThan(-1);
  const end = INIT.indexOf('\n}\n', start);
  return INIT.slice(start, end + 2);
}

type Profile = { id: number; name: string; is_admin: boolean };

function run(profile: Profile) {
  document.body.innerHTML = `
    <div id="profile-indicator"><span id="profile-indicator-avatar"></span>
      <span id="profile-indicator-name"></span><button id="profile-indicator-button"></button></div>
    <div class="status-section status-section--clickable" data-music-only>music status</div>
    <div class="status-section status-section--clickable" data-video-only>video status</div>`;
  const fn = new Function(
    'currentProfile',
    'renderProfileAvatar',
    'canDownload',
    'normalizeProfilePageList',
    'toggleProfileQuickSwitch',
    'profileAllowedSides',
    `${extract('updateProfileIndicator')}\nupdateProfileIndicator();`,
  );
  fn(
    profile,
    () => {},
    () => true,
    (x: unknown) => x,
    () => {},
    () => 'both',
  );
  return Array.from(document.querySelectorAll<HTMLElement>('.status-section'));
}

describe('the service status panel is admin only (#2155)', () => {
  it('is hidden on both sides for a regular profile', () => {
    const sections = run({ id: 3, name: 'Member', is_admin: false });
    expect(sections).toHaveLength(2);
    for (const el of sections) expect(el.style.display).toBe('none');
  });

  it('stays for an admin', () => {
    for (const el of run({ id: 2, name: 'Second admin', is_admin: true })) {
      expect(el.style.display).toBe('');
    }
    for (const el of run({ id: 1, name: 'Owner', is_admin: true })) {
      expect(el.style.display).toBe('');
    }
  });
});
