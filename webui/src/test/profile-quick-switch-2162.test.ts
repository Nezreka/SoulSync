import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';

/**
 * #2162: a member in login mode clicked their name in the sidebar and got a
 * "Switch to" list of every other profile, admins included. Runs init.js's
 * real openProfileQuickSwitch against a stubbed page.
 */

const INIT = readFileSync(resolve(process.cwd(), 'static/init.js'), 'utf8');

function extract(name: string): string {
  const start = INIT.indexOf(`function ${name}(`);
  expect(start, `${name} not found`).toBeGreaterThan(-1);
  const end = INIT.indexOf('\n}\n', start);
  return INIT.slice(start, end + 2);
}

type Profile = { id: number; name: string; is_admin: boolean; disabled?: boolean };

const OWNER: Profile = { id: 1, name: 'Owner', is_admin: true };
const SAM: Profile = { id: 2, name: 'Sam', is_admin: false };
const KIM: Profile = { id: 3, name: 'Kim', is_admin: false };

function open(me: Profile, profiles: Profile[], loginMode: boolean): string[] {
  document.body.innerHTML = '<button id="profile-indicator-button"></button>';
  (window as unknown as { openMyAccountsModal?: () => void }).openMyAccountsModal = () => {};
  const fn = new Function(
    'currentProfile',
    'profileLoginMode',
    'pfCloseMenus',
    'pfAvatar',
    'PF_ICONS',
    '_pfWireMenu',
    'handleProfileClick',
    'openProfileEditor',
    'openProfileManager',
    'soulsyncLogout',
    `${extract('pfEl')}\n${extract('pfQuickSwitchTargets')}\n${extract('openProfileQuickSwitch')}
     openProfileQuickSwitch(document.getElementById('profile-indicator-button'), arguments[10]);`,
  );
  fn(
    me,
    loginMode,
    () => {},
    () => document.createElement('span'),
    { edit: '', user: '', people: '', signout: '' },
    () => {},
    () => {},
    () => {},
    () => {},
    () => {},
    profiles,
  );
  const menu = document.getElementById('profile-quick-switch');
  expect(menu).not.toBeNull();
  return Array.from(menu!.querySelectorAll('.pf-menu-label, .pf-menu-item')).map(
    (el) => el.textContent?.trim() || '',
  );
}

afterEach(() => {
  document.body.innerHTML = '';
});

describe('the profile menu in login mode (#2162)', () => {
  it('offers a member nobody to switch to, even if a list slips through', () => {
    const items = open(SAM, [OWNER, SAM, KIM], true);
    expect(items).not.toContain('SWITCH TO');
    expect(items.join(' ')).not.toMatch(/Owner|Kim/);
    // their own things stay
    expect(items).toEqual(['Edit my profile', 'Profile & account', 'Sign out']);
  });

  it('keeps the switch list for an admin', () => {
    const items = open(OWNER, [OWNER, SAM, KIM], true);
    expect(items[0]).toBe('SWITCH TO');
    expect(items.join(' ')).toMatch(/Sam/);
    expect(items).toContain('Manage profiles');
    expect(items).toContain('Sign out');
  });
});

describe('the profile menu with the household picker', () => {
  it('still lets a member switch to the family, minus turned-off profiles', () => {
    const items = open(SAM, [OWNER, SAM, { ...KIM, disabled: true }], false);
    expect(items[0]).toBe('SWITCH TO');
    expect(items.join(' ')).toMatch(/Owner/);
    expect(items.join(' ')).not.toMatch(/Kim/);
    expect(items).not.toContain('Sign out');
  });
});
