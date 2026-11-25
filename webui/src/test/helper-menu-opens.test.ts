/**
 * The ? button at the bottom right. It used to open helper.js's menu of
 * modes (Element Info, helper search, Setup Progress, Troubleshoot, the old
 * tours). Those are gone (help plan P7): toggleHelperMode is a shim that
 * hands over to the React help sheet, window.SoulSyncHelp
 * (features/help/host.tsx). This runs the real helper.js.
 */
import { readFileSync, readdirSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  READY_EVENT,
  installHelpApi,
  installHelpKeys,
  uninstallHelpKeys,
} from '@/features/help/host';
import { closePalette, isPaletteOpen } from '@/features/help/search/palette';
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
  vi.useRealTimers();
  vi.unstubAllGlobals();
  localStorage.clear();
  document.body.innerHTML = '';
});

describe('the help button', () => {
  it('opens the help sheet on a React page', () => {
    const w = boot();
    installHelpApi();
    delete w.currentPage;
    expect(() => w.toggleHelperMode()).not.toThrow();
    expect(useHelpStore.getState().open).toBe(true);
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

  it('? from the page opens the sheet', () => {
    boot();
    installHelpApi();
    installHelpKeys();
    document.body.dispatchEvent(new KeyboardEvent('keydown', { key: '?', bubbles: true }));
    expect(useHelpStore.getState().open).toBe(true);
  });

  it('Ctrl K opens the palette', () => {
    boot();
    installHelpApi();
    installHelpKeys();
    document.body.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'k', ctrlKey: true, bubbles: true }),
    );
    expect(isPaletteOpen()).toBe(true);
    closePalette();
  });

  it('knows the page from the shell when a vanilla page is not active', () => {
    const w = boot();
    w.currentPage = 'library';
    expect(w._helperPageId()).toBe('library');
  });

  it("helper.js binds no keys at all: ?, F1, Ctrl K and Esc are the sheet's", () => {
    expect(HELPER).not.toContain("'keydown'");
    expect(HELPER).not.toContain('"keydown"');
  });

  it('the old modes are gone, not just unreachable', () => {
    for (const name of [
      'HELPER_CONTENT',
      'HELPER_MENU_ITEMS',
      'HELPER_TOURS',
      'openHelperMenu',
      'activateHelperMode',
      'openHelperSearch',
      'SETUP_STEPS',
      'openSetupPanel',
      'TROUBLESHOOT_RULES',
      'KEYBOARD_SHORTCUTS',
      'WHATS_NEW',
      'VERSION_MODAL_SECTIONS',
      'startTour',
    ]) {
      expect(HELPER, name).not.toContain(name);
    }
  });

  it('the shim hands over and never builds the menu', () => {
    const shim = /function toggleHelperMode\(\) \{[\s\S]*?\n\}/.exec(HELPER)?.[0] ?? '';
    expect(shim).toContain('window.SoulSyncHelp');
  });
});

describe('the first launch pointer', () => {
  async function bootFresh(setupComplete: boolean) {
    vi.useFakeTimers();
    const fetchMock = vi.fn(async () => ({
      ok: true,
      json: async () => ({ setup_complete: setupComplete }),
    }));
    vi.stubGlobal('fetch', fetchMock);
    const w = boot();
    installHelpApi();
    document.dispatchEvent(new Event('DOMContentLoaded'));
    await vi.advanceTimersByTimeAsync(2600);
    return { w, fetchMock };
  }

  it('an unfinished install gets one pointer, and it opens the sheet', async () => {
    const { fetchMock } = await bootFresh(false);
    expect(fetchMock).toHaveBeenCalledWith('/api/setup/status');
    const tip = document.querySelector<HTMLButtonElement>('.helper-first-launch-tip');
    expect(tip).not.toBeNull();
    expect(tip!.tagName).toBe('BUTTON');
    expect(document.getElementById('helper-float-btn')).toHaveClass('undiscovered');
    tip!.click();
    expect(useHelpStore.getState().open).toBe(true);
    expect(document.querySelector('.helper-first-launch-tip')).toBeNull();
    expect(document.getElementById('helper-float-btn')).not.toHaveClass('undiscovered');
    expect(localStorage.getItem('soulsync_setup_welcome_dismissed')).toBe('1');
  });

  it('a set-up install gets no pointer', async () => {
    await bootFresh(true);
    expect(document.querySelector('.helper-first-launch-tip')).toBeNull();
  });

  it('once dismissed, the server is never asked again', async () => {
    localStorage.setItem('soulsync_setup_welcome_dismissed', '1');
    const { fetchMock } = await bootFresh(false);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(document.querySelector('.helper-first-launch-tip')).toBeNull();
  });

  it('pressing the ? also clears the pointer', async () => {
    const { w } = await bootFresh(false);
    expect(document.querySelector('.helper-first-launch-tip')).not.toBeNull();
    w.toggleHelperMode();
    expect(document.querySelector('.helper-first-launch-tip')).toBeNull();
  });
});

describe('Ctrl/Cmd K has one owner', () => {
  /** every handwritten script under static and src: no tests, no build */
  function sources(): string[] {
    const out: string[] = [];
    const walk = (dir: string) => {
      for (const ent of readdirSync(dir, { withFileTypes: true })) {
        const path = join(dir, ent.name);
        if (ent.isDirectory()) {
          if (ent.name === 'dist' || ent.name === 'node_modules') continue;
          walk(path);
        } else if (
          /\.(js|ts|tsx)$/.test(ent.name) &&
          !/\.test(-utils)?\.|\.d\.ts$/.test(ent.name)
        ) {
          out.push(path);
        }
      }
    };
    walk(resolve(process.cwd(), 'static'));
    walk(resolve(process.cwd(), 'src'));
    return out;
  }

  it('only the help host binds it (the Settings search and the old global search took it first)', () => {
    const kCheck = /(key|toLowerCase\(\))\s*[!=]==?\s*['"][kK]['"]/;
    const owners: string[] = [];
    for (const file of sources()) {
      const text = readFileSync(file, 'utf8');
      const lines = text.split('\n');
      lines.forEach((line, i) => {
        if (!kCheck.test(line)) return;
        const near = lines.slice(Math.max(0, i - 3), i + 4).join('\n');
        if (/ctrlKey|metaKey/.test(near))
          owners.push(relative(process.cwd(), file).replace(/\\/g, '/'));
      });
    }
    expect([...new Set(owners)]).toEqual(['src/features/help/host.tsx']);
  });
});

describe('the button itself (#2171)', () => {
  // 3.6.7's ? turned sideways on click (its old helper-mode state) and opened
  // nothing. the sheet replaced that menu: the button shows open by its
  // colour, and nothing anywhere turns it
  const css = readdirSync(resolve(process.cwd(), 'static'))
    .filter((f) => f.endsWith('.css'))
    .map((f) => readFileSync(resolve(process.cwd(), 'static', f), 'utf8'))
    .join('\n');
  const rules = [...css.matchAll(/([^{}]*helper-float-btn[^{}]*)\{([^}]*)\}/g)];

  it('no rule for it rotates it', () => {
    expect(rules.length).toBeGreaterThan(0);
    for (const [, selector, body] of rules) expect(body, selector.trim()).not.toMatch(/rotate/);
  });

  it('open, it stays upright', () => {
    const open = rules.find(([, sel]) => sel.trim() === '.helper-float-btn[aria-expanded="true"]');
    expect(open?.[2]).toMatch(/transform:\s*none/);
  });
});
