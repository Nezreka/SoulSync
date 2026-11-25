import { cleanup, render, screen, within } from '@testing-library/react';
/**
 * The shortcut registry stays honest: every key it lists has a handler in
 * the file it names, and the Shortcuts view shows exactly the registry,
 * with Mac keys on a Mac and a note on a touch only device.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { ViewProps } from './view-props';

import {
  SHORTCUTS,
  SHORTCUT_GROUPS,
  keyLabel,
  shortcutHint,
  shortcutsIn,
  spokenKeys,
} from './shortcut-registry';
import { ShortcutsView } from './shortcuts';

const PROPS: ViewProps = {
  entry: { view: 'shortcuts' },
  query: '',
  isAdmin: true,
  page: 'dashboard',
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('the registry', () => {
  it.each(SHORTCUTS.map((s) => [s.id, s] as const))(
    '%s is handled where it says',
    (_id, shortcut) => {
      const text = readFileSync(resolve(process.cwd(), shortcut.source.file), 'utf8');
      expect(text).toContain(shortcut.source.has);
    },
  );

  it('has unique ids, a group each, and no empty group', () => {
    expect(new Set(SHORTCUTS.map((s) => s.id)).size).toBe(SHORTCUTS.length);
    const groups = new Set(SHORTCUT_GROUPS.map((g) => g.id));
    for (const s of SHORTCUTS) expect(groups.has(s.group), s.id).toBe(true);
    for (const g of SHORTCUT_GROUPS) expect(shortcutsIn(g.id).length, g.id).toBeGreaterThan(0);
  });

  it('lists the keys the plan names, and the docs / the old list missed', () => {
    const ids = SHORTCUTS.map((s) => s.id);
    for (const id of [
      'help.open',
      'help.search',
      'close',
      'player.play',
      'player.seek',
      'player.volume',
      'player.mute',
      'docs.search',
      'settings.search',
      'tour.step',
    ]) {
      expect(ids).toContain(id);
    }
  });

  it('has no copy with em or en dashes', () => {
    for (const s of SHORTCUTS) expect(`${s.label} ${s.where ?? ''}`).not.toMatch(/[–—]/);
  });

  it('writes Ctrl as ⌘ on a Mac, and says it the way a screen reader should', () => {
    expect(keyLabel('Mod', false)).toBe('Ctrl');
    expect(keyLabel('Mod', true)).toBe('⌘');
    expect(keyLabel('Alt', true)).toBe('⌥');
    expect(shortcutHint('help.search', false)).toBe('Ctrl K');
    expect(shortcutHint('help.search', true)).toBe('⌘K');
    expect(shortcutHint('help.open', false)).toBe('? or F1');
    expect(shortcutHint('nope')).toBe('');
    const search = SHORTCUTS.find((s) => s.id === 'help.search')!;
    expect(spokenKeys(search, false)).toBe('Control K');
    expect(spokenKeys(search, true)).toBe('Command K');
    const seek = SHORTCUTS.find((s) => s.id === 'player.seek')!;
    expect(spokenKeys(seek, false)).toBe('Left arrow, Right arrow');
  });
});

describe('the Shortcuts view', () => {
  function mac(on: boolean) {
    vi.spyOn(navigator, 'platform', 'get').mockReturnValue(on ? 'MacIntel' : 'Win32');
    vi.spyOn(navigator, 'userAgent', 'get').mockReturnValue(on ? 'Macintosh' : 'Windows NT');
  }

  it('renders every group and every shortcut in the registry', () => {
    mac(false);
    render(<ShortcutsView {...PROPS} />);
    for (const g of SHORTCUT_GROUPS) {
      const list = screen.getByRole('list', { name: g.label });
      expect(within(list).getAllByRole('listitem')).toHaveLength(shortcutsIn(g.id).length);
    }
    expect(screen.getAllByRole('listitem')).toHaveLength(SHORTCUTS.length);
    const anywhere = screen.getByRole('list', { name: 'Anywhere' });
    expect(within(anywhere).getByText('Open help')).toBeInTheDocument();
    expect(within(anywhere).getByText('Control K')).toBeInTheDocument();
    expect(screen.getByText(/On a Mac, Ctrl is ⌘/)).toBeInTheDocument();
  });

  it('shows ⌘ on a Mac and drops the Mac footnote', () => {
    mac(true);
    render(<ShortcutsView {...PROPS} />);
    const row = document.querySelector('[data-shortcut="help.search"]')!;
    expect(row.querySelectorAll('kbd')[0]).toHaveTextContent('⌘');
    expect(screen.queryByText(/On a Mac, Ctrl is ⌘/)).toBeNull();
    expect(screen.getByText('Shortcuts pause while you type in a field.')).toBeInTheDocument();
  });

  it('puts "or" between alternatives and nothing between a pair', () => {
    mac(false);
    render(<ShortcutsView {...PROPS} />);
    const open = document.querySelector('[data-shortcut="help.open"]')!;
    expect(open.textContent).toContain('?orF1');
    const seek = document.querySelector('[data-shortcut="player.seek"]')!;
    expect(seek.querySelectorAll('kbd')).toHaveLength(2);
    expect(seek.querySelector('[aria-hidden="true"]')!.textContent).toBe('←→');
  });

  it('tells a touch only device the shortcuts need a keyboard', () => {
    vi.stubGlobal(
      'matchMedia',
      vi.fn((q: string) => ({
        matches: q.includes('pointer: coarse'),
        media: q,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      })),
    );
    render(<ShortcutsView {...PROPS} />);
    expect(screen.getByText(/Shortcuts need a keyboard/)).toBeInTheDocument();
  });
});
