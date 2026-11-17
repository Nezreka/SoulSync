/**
 * Ctrl/Cmd K is the help palette's on every page, Settings too: the Settings
 * page used to take it first for its own search box. Its search is on `/`
 * now, and only on the Settings page, never while typing.
 */
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { installHelpKeys, uninstallHelpKeys } from '@/features/help/host';
import { closePalette, isPaletteOpen } from '@/features/help/search/palette';
import { useHelpStore } from '@/features/help/store';

import { onSettingsSearchKey } from './shell';

function settingsPage(visible = true): HTMLInputElement {
  document.body.innerHTML =
    '<div id="settings-page"><input id="stg-search-input" /><input id="other-field" /></div>';
  const page = document.getElementById('settings-page')!;
  // jsdom has no layout: offsetParent is how the shell tells the page is showing
  Object.defineProperty(page, 'offsetParent', { configurable: true, get: () => (visible ? document.body : null) });
  return document.getElementById('stg-search-input') as HTMLInputElement;
}

function press(key: string, init: KeyboardEventInit = {}, target: Element = document.body) {
  const event = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...init });
  target.dispatchEvent(event);
  return event;
}

beforeEach(() => {
  useHelpStore.setState(useHelpStore.getInitialState(), true);
  // the settings handler first, as the shell bundle loads before the help bundle
  document.addEventListener('keydown', onSettingsSearchKey);
  installHelpKeys();
});

afterEach(async () => {
  closePalette();
  // let the palette's root unmount before the body under it goes
  await new Promise((r) => setTimeout(r, 0));
  document.removeEventListener('keydown', onSettingsSearchKey);
  uninstallHelpKeys();
  document.body.innerHTML = '';
});

describe('keys on the Settings page', () => {
  it('Ctrl K opens the help palette, not the Settings search', () => {
    const input = settingsPage();
    const event = press('k', { ctrlKey: true });
    expect(isPaletteOpen()).toBe(true);
    expect(document.activeElement).not.toBe(input);
    expect(event.defaultPrevented).toBe(true);
  });

  it('Cmd K too', () => {
    settingsPage();
    press('K', { metaKey: true });
    expect(isPaletteOpen()).toBe(true);
  });

  it('/ goes to the Settings search', () => {
    const input = settingsPage();
    input.value = 'plex';
    const event = press('/');
    expect(document.activeElement).toBe(input);
    expect(input.selectionStart).toBe(0);
    expect(input.selectionEnd).toBe(4);
    expect(event.defaultPrevented).toBe(true);
    expect(isPaletteOpen()).toBe(false);
  });

  it('/ while typing in a field is just a slash', () => {
    const input = settingsPage();
    const other = document.getElementById('other-field')!;
    other.focus();
    const event = press('/', {}, other);
    expect(document.activeElement).toBe(other);
    expect(document.activeElement).not.toBe(input);
    expect(event.defaultPrevented).toBe(false);
  });

  it('/ does nothing when Settings is not the page showing', () => {
    const input = settingsPage(false);
    const event = press('/');
    expect(document.activeElement).not.toBe(input);
    expect(event.defaultPrevented).toBe(false);
  });

  it('/ leaves a window on top alone', () => {
    const input = settingsPage();
    document.body.insertAdjacentHTML('beforeend', '<div role="dialog" aria-modal="true"></div>');
    press('/');
    expect(document.activeElement).not.toBe(input);
  });
});
