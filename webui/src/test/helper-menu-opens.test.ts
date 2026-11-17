/**
 * The ? button at the bottom right did nothing on React pages. helper.js read
 * `const currentPage = ... (typeof currentPage === 'string' ? currentPage : '')`:
 * the const read inside its own declaration throws ("Cannot access
 * 'currentPage' before initialization"), and on a React page nothing earlier
 * in the || chain answers, so every click died there. Tour navigation and
 * troubleshoot carried the same line. This runs the real helper.js.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';

const HELPER = readFileSync(resolve(process.cwd(), 'static/helper.js'), 'utf8');

type HelperWindow = Window & {
  toggleHelperMode: () => void;
  _helperPageId: () => string;
  currentPage?: unknown;
};

function boot(): HelperWindow {
  document.body.innerHTML =
    '<div id="webui-react-root" class="page active"></div>' +
    '<button class="helper-float-btn" id="helper-float-btn"><span>?</span></button>';
  // classic script semantics: top-level functions land on window
  (0, eval)(HELPER);
  return window as unknown as HelperWindow;
}

afterEach(() => {
  document.querySelectorAll('.helper-menu').forEach((m) => m.remove());
  document.body.innerHTML = '';
});

describe('the help button', () => {
  it('opens its menu on a React page, where no vanilla page is active', () => {
    const w = boot();
    delete w.currentPage;
    expect(() => w.toggleHelperMode()).not.toThrow();
    const menu = document.querySelector('.helper-menu');
    expect(menu).not.toBeNull();
    expect(menu!.querySelectorAll('button').length).toBeGreaterThan(0);
  });

  it('knows the page from the shell when a vanilla page is not active', () => {
    const w = boot();
    w.currentPage = 'library';
    expect(w._helperPageId()).toBe('library');
  });

  it('no caller reads a const inside its own declaration any more', () => {
    expect(HELPER).not.toMatch(/const currentPage = [^\n]*typeof currentPage/);
  });
});
