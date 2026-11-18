/**
 * #2166: an admin opening someone's profile from Manage profiles gets the
 * person editor sheet. The vanilla editor stays for name, picture, PIN and
 * the library folder (identityOnly, which the sheet opens), for your own
 * profile, for adding someone, and as the fallback when the react bundle
 * didn't load. Runs init.js's real openProfileEditor up to the point it
 * builds its modal.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { extractFunction } from './vanilla-extract';

const INIT = readFileSync(resolve(process.cwd(), 'static/init.js'), 'utf8');

const BUILT = 'built the vanilla modal';

type Opts = { mode: string; profile?: object | null; identityOnly?: boolean };

function run(me: object | null, opts: Opts) {
  const modal = vi.fn(() => {
    throw new Error(BUILT);
  });
  const fn = new Function(
    'currentProfile',
    'pfCloseMenus',
    'pfModal',
    '_pfProfilesCache',
    '_pfManageState',
    'profileLoginMode',
    'PROFILE_COLORS',
    'PROFILE_RATINGS',
    'PROFILE_LIMIT_PERIODS',
    '_pfSidesOf',
    'pfNoDownloads',
    'normalizeProfilePageList',
    'normalizeProfilePageId',
    'pfOpenAsProfileId',
    `${extractFunction('openProfileEditor', INIT)}\nreturn openProfileEditor(arguments[14]);`,
  );
  const result = fn(
    me,
    () => {},
    modal,
    [],
    null,
    false,
    [['#123', 'Blue']],
    ['G', 'PG'],
    [[7, 'week']],
    () => 'both',
    () => false,
    (x: string[]) => x,
    (x: string) => x,
    () => null,
    opts,
  ) as Promise<void>;
  return { modal, result };
}

const ADMIN = { id: 1, name: 'Boulder', is_admin: true };
const MAYA = { id: 7, name: 'Maya', is_admin: false };

afterEach(() => {
  delete window.openPersonEditor;
});

describe('Manage profiles opens the person editor sheet (#2166)', () => {
  it('an admin editing someone gets the sheet, not the old form', async () => {
    const open = vi.fn(() => true);
    window.openPersonEditor = open;
    const { modal, result } = run(ADMIN, { mode: 'edit', profile: MAYA });
    await result;
    expect(open).toHaveBeenCalledWith(7);
    expect(modal).not.toHaveBeenCalled();
  });

  it('the sheet sends name, picture, PIN and library back to the vanilla editor', async () => {
    const open = vi.fn(() => true);
    window.openPersonEditor = open;
    const { modal, result } = run(ADMIN, { mode: 'edit', profile: MAYA, identityOnly: true });
    await expect(result).rejects.toThrow(BUILT);
    expect(open).not.toHaveBeenCalled();
    // closing it goes back to the sheet, and its second step is PIN & library
    const args = (modal.mock.calls[0] as unknown[])[0] as { onClose: unknown };
    expect(typeof args.onClose).toBe('function');
  });

  it('no sheet (react bundle missing) or a refused open: the old editor with every field', async () => {
    const { modal, result } = run(ADMIN, { mode: 'edit', profile: MAYA });
    await expect(result).rejects.toThrow(BUILT);
    window.openPersonEditor = vi.fn(() => false);
    const again = run(ADMIN, { mode: 'edit', profile: MAYA });
    await expect(again.result).rejects.toThrow(BUILT);
    expect(modal).toHaveBeenCalled();
  });

  it('your own profile and a new one stay on the vanilla editor', async () => {
    const open = vi.fn(() => true);
    window.openPersonEditor = open;
    await expect(run(ADMIN, { mode: 'self' }).result).rejects.toThrow(BUILT);
    await expect(run(ADMIN, { mode: 'create' }).result).rejects.toThrow(BUILT);
    expect(open).not.toHaveBeenCalled();
  });

  it('a member never reaches the sheet from here', async () => {
    const open = vi.fn(() => true);
    window.openPersonEditor = open;
    await expect(run(MAYA, { mode: 'self' }).result).rejects.toThrow(BUILT);
    expect(open).not.toHaveBeenCalled();
  });

  it('identityOnly keeps the PIN and the library and drops the access controls', () => {
    const body = extractFunction('openProfileEditor', INIT);
    const branch = body.slice(
      body.indexOf('} else if (permsElsewhere) {'),
      body.indexOf('// admin switch (editing someone else only)'),
    );
    expect(branch).toContain('pinSwitch.row');
    expect(branch).toContain('ownLibraryGroup()');
    expect(branch).not.toMatch(/PROFILE_PRESETS|dlSwitch|pfPagePicker|_pfLoadProfileFeatures/);
  });
});
