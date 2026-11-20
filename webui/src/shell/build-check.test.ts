import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  SERVER_BUILD_EVENT,
  checkServerBuild,
  installBuildCheck,
  isStale,
  pageBuild,
  resetBuildCheck,
} from './build-check';

/**
 * A tab served by one build, talking to a server that now runs another,
 * offers a reload in a toast. It never reloads by itself, and it says so
 * once per tab, not on every reconnect.
 */

function served(build: string) {
  document.head.innerHTML = `<meta name="soulsync-build" content="${build}">`;
}

let shown: Array<{ message: string; opts: unknown }> = [];

beforeEach(() => {
  resetBuildCheck();
  shown = [];
  window.showToast = (message: string, _type?: string, _ctx?: unknown, opts?: unknown) => {
    shown.push({ message, opts });
  };
});

afterEach(() => {
  document.head.innerHTML = '';
});

describe('the build check', () => {
  it('reads the build the page was served with', () => {
    served('3.6.8+abc1234/1700');
    expect(pageBuild()).toBe('3.6.8+abc1234/1700');
  });

  it('the same build is not stale; a different one is', () => {
    served('3.6.8+abc1234/1700');
    expect(isStale('3.6.8+abc1234/1700')).toBe(false);
    expect(isStale('3.6.9+def5678/1800')).toBe(true);
  });

  it('nothing to compare is never stale', () => {
    served('');
    expect(isStale('3.6.9')).toBe(false);
    served('3.6.8');
    expect(isStale('')).toBe(false);
    expect(isStale(undefined)).toBe(false);
    expect(isStale(42)).toBe(false);
  });

  it('a new build offers a reload once and never reloads by itself', () => {
    served('3.6.8+abc1234/1700');
    const reload = vi.fn();
    expect(checkServerBuild('3.6.9+def5678/1800', reload)).toBe(true);
    expect(checkServerBuild('3.6.9+def5678/1800', reload)).toBe(true);
    expect(shown).toHaveLength(1);
    expect(shown[0].message).toBe('SoulSync was updated. Reload this tab to get the new version.');
    expect(reload).not.toHaveBeenCalled();
    const action = (shown[0].opts as { action: { label: string; run: () => void } }).action;
    expect(action.label).toBe('Reload');
    action.run();
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it('the same build says nothing', () => {
    served('3.6.8+abc1234/1700');
    expect(checkServerBuild('3.6.8+abc1234/1700')).toBe(false);
    expect(shown).toEqual([]);
  });

  it('listens for the socket event and for a lazy chunk that failed to load', () => {
    served('3.6.8+abc1234/1700');
    const win = new EventTarget() as unknown as Window;
    installBuildCheck(win);
    win.dispatchEvent(
      new CustomEvent(SERVER_BUILD_EVENT, { detail: { build: '3.6.8+abc1234/1700' } }),
    );
    expect(shown).toEqual([]);
    win.dispatchEvent(new CustomEvent(SERVER_BUILD_EVENT, { detail: { build: '3.6.9+x/1' } }));
    expect(shown).toHaveLength(1);

    resetBuildCheck();
    shown = [];
    const win2 = new EventTarget() as unknown as Window;
    installBuildCheck(win2);
    win2.dispatchEvent(new Event('vite:preloadError'));
    expect(shown).toHaveLength(1);
  });
});
