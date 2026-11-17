/**
 * The ? button at the bottom right. It used to open helper.js's menu of
 * modes (and on React pages did nothing: a const read inside its own
 * declaration threw, fixed in d7e754141). Now it opens the React help sheet:
 * toggleHelperMode is a shim that hands over to window.SoulSyncHelp
 * (features/help/host.tsx). This runs the real helper.js.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import {
  READY_EVENT,
  installHelpApi,
  installHelpKeys,
  uninstallHelpKeys,
} from '@/features/help/host';
import { useHelpStore } from '@/features/help/store';

const HELPER = readFileSync(resolve(process.cwd(), 'static/helper.js'), 'utf8');

type HelperWindow = Window & {
  toggleHelperMode: () => void;
  _helperPageId: () => string;
  currentPage?: unknown;
};

function boot(): HelperWindow {
  document.body.innerHTML =
    '<div id="webui-react-root" class="page active"></div>' +
    '<button class="helper-float-btn undiscovered" id="helper-float-btn"><span>?</span></button>';
  // classic script semantics: top-level functions land on window
  (0, eval)(HELPER);
  return window as unknown as HelperWindow;
}

beforeEach(() => {
  useHelpStore.setState(useHelpStore.getInitialState(), true);
});

afterEach(() => {
  uninstallHelpKeys();
  delete window.SoulSyncHelp;
  document.querySelectorAll('.helper-menu').forEach((m) => m.remove());
  document.body.innerHTML = '';
});

describe('the help button', () => {
  it('opens the help sheet, not the old menu, on a React page', () => {
    const w = boot();
    installHelpApi();
    delete w.currentPage;
    expect(() => w.toggleHelperMode()).not.toThrow();
    expect(useHelpStore.getState().open).toBe(true);
    expect(document.querySelector('.helper-menu')).toBeNull();
    // and again closes it
    w.toggleHelperMode();
    expect(useHelpStore.getState().open).toBe(false);
  });

  it('the button in index.html calls the shim and says it opens a dialog', () => {
    const html = readFileSync(resolve(process.cwd(), 'index.html'), 'utf8');
    const tag = /<button class="helper-float-btn"[^>]*>/.exec(html)?.[0] ?? '';
    expect(tag).toContain('onclick="toggleHelperMode()"');
    expect(tag).toContain('aria-label="Help"');
    expect(tag).toContain('aria-haspopup="dialog"');
    expect(tag).toContain('aria-expanded="false"');
    expect(tag).not.toContain('click any element');
  });

  it('a click on the button goes through the shim', () => {
    const w = boot();
    installHelpApi();
    const btn = document.getElementById('helper-float-btn') as HTMLButtonElement;
    btn.addEventListener('click', () => w.toggleHelperMode());
    btn.click();
    expect(useHelpStore.getState().open).toBe(true);
    expect(document.querySelector('.helper-menu')).toBeNull();
    // found it: the idle glow stops for good
    expect(document.getElementById('helper-float-btn')).not.toHaveClass('undiscovered');
  });

  it('a click before the React bundle ran opens the sheet once it is ready', () => {
    const w = boot();
    w.toggleHelperMode();
    expect(useHelpStore.getState().open).toBe(false);
    installHelpApi();
    window.dispatchEvent(new CustomEvent(READY_EVENT));
    expect(useHelpStore.getState().open).toBe(true);
  });

  it('? from the page opens the sheet, never the old menu', () => {
    boot();
    installHelpApi();
    installHelpKeys();
    document.body.dispatchEvent(new KeyboardEvent('keydown', { key: '?', bubbles: true }));
    expect(useHelpStore.getState().open).toBe(true);
    expect(document.querySelector('.helper-menu')).toBeNull();
  });

  it('Ctrl K no longer opens the old helper search', () => {
    boot();
    installHelpApi();
    installHelpKeys();
    document.body.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'k', ctrlKey: true, bubbles: true }),
    );
    expect(document.querySelector('.helper-search-overlay, .helper-search')).toBeNull();
    expect(useHelpStore.getState().open).toBe(true);
  });

  it('knows the page from the shell when a vanilla page is not active', () => {
    const w = boot();
    w.currentPage = 'library';
    expect(w._helperPageId()).toBe('library');
  });

  it('no caller reads a const inside its own declaration any more', () => {
    expect(HELPER).not.toMatch(/const currentPage = [^\n]*typeof currentPage/);
  });

  it('helper.js leaves ?, F1 and Ctrl K to the sheet, so one key press is one toggle', () => {
    // every boot() above adds another copy of helper.js's listeners, so a
    // runtime count can't tell one handler from three; read the source
    const start = HELPER.indexOf('// ── Keyboard Navigation');
    expect(start).toBeGreaterThan(-1);
    const keys = HELPER.slice(start, HELPER.indexOf('// ═══', start));
    expect(keys).toContain("addEventListener('keydown'");
    expect(keys).not.toMatch(/'\?'|'F1'|'k'|'K'/);
    expect(keys).not.toContain('toggleHelperMode');
    expect(keys).not.toContain("activateHelperMode('search')");
  });

  it('the shim hands over and never builds the menu', () => {
    const shim = /function toggleHelperMode\(\) \{[\s\S]*?\n\}/.exec(HELPER)?.[0] ?? '';
    expect(shim).toContain('window.SoulSyncHelp');
    expect(shim).not.toContain('openHelperMenu');
  });
});
