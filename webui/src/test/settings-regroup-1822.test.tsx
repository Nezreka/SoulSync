import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { SettingsPage } from '@/features/settings/settings-page';
import { switchSettingsTab } from '@/features/settings/shell';
import { withHelpLines } from '@/features/settings/tabs/__fixtures__/shape';
import { helpFirstLine } from '@/features/settings/tabs/parts';

// #1822: settings were "buried". Library held discovery and playlists, Advanced
// held notifications, metasync and the sidebar weather. Sections now sit where
// people look for them, and each setting says what it does under it.

// the page is React (settings-page.tsx, SETTINGS_PORT.md step V4c): its own
// markup, with the slots each tab renders into, as index.html had it
const html = renderToStaticMarkup(<SettingsPage tabs={{}} />);

describe('where sections live', () => {
  const doc = new DOMParser().parseFromString(html, 'text/html');

  it('MetaSync is on connections (React, SETTINGS_PORT.md step 10d)', () => {
    const section = readFileSync(
      resolve(process.cwd(), 'src/features/settings/tabs/connections-metasync.tsx'),
      'utf8',
    );
    expect(section).toMatch(/tab="connections"\s+title="MetaSync"/);
    expect(html).toContain('<div data-stg-react="connections" data-stg-part="metasync"');
  });

  it('Video Quality is on quality (React, SETTINGS_PORT.md video pass V1)', () => {
    const section = readFileSync(
      resolve(process.cwd(), 'src/features/settings/tabs/video-quality.tsx'),
      'utf8',
    );
    expect(section).toContain("const TAB = 'quality';");
    expect(section).toMatch(/tab=\{TAB\}\s+title="Video Quality"/);
    expect(html).toContain('<div data-stg-react="quality" data-stg-part="video"');
  });

  it('Video Preferences is on library (React, SETTINGS_PORT.md video pass V2c)', () => {
    const section = readFileSync(
      resolve(process.cwd(), 'src/features/settings/tabs/library-video-prefs.tsx'),
      'utf8',
    );
    expect(section).toContain("const TAB = 'library';");
    expect(section).toMatch(/tab=\{TAB\}\s+title="Video Preferences"/);
    expect(html).toContain('<div data-stg-react="library" data-stg-part="video-prefs"');
  });

  it.each([
    ['NotificationsSection', 'Notifications', 'connections', 'notifications'],
    ['ImportListsSection', 'Import Lists', 'discovery', 'import-lists'],
  ])('%s: %s is on %s (React, SETTINGS_PORT.md video pass V3c)', (fn, heading, tab, part) => {
    const lists = readFileSync(
      resolve(process.cwd(), 'src/features/settings/tabs/video-row-lists.tsx'),
      'utf8',
    );
    const body = lists.split(`export function ${fn}(`)[1].split('</Section>')[0];
    expect(body).toContain(`tab="${tab}"`);
    expect(body).toContain(`title="${heading}"`);
    expect(html).toContain(`<div data-stg-react="${tab}" data-stg-part="${part}"`);
  });

  it('Database Maintenance is on advanced (React, SETTINGS_PORT.md step 9a)', () => {
    const tab = readFileSync(
      resolve(process.cwd(), 'src/features/settings/tabs/system-tab.tsx'),
      'utf8',
    );
    expect(tab).toContain("const TAB = 'advanced';");
    expect(tab).toContain('title="Database Maintenance"');
    expect(html).toContain('<div data-stg-react="advanced" data-stg-part="database"');
  });

  it('has a tab for every section tag and no tab left empty', () => {
    const tabs = [...doc.querySelectorAll('.stg-tab')].map((t) => t.getAttribute('data-tab'));
    // a tab ported to React has slots where its sections were: the blocks
    // themselves are rendered into them (SETTINGS_PORT.md). Sidebar Weather
    // (Appearance), Playlists, Listening Stats, Discovery and Discovery Pool
    // Settings (Discovery) and the music Quality sections, Album Releases among
    // them, are: src/features/settings/tabs/*.test.tsx
    const used = new Set(
      [...doc.querySelectorAll('#settings-page [data-stg], #settings-page [data-stg-react]')].map(
        (el) => el.getAttribute('data-stg') ?? el.getAttribute('data-stg-react'),
      ),
    );
    expect([...used].every((tag) => tabs.includes(tag))).toBe(true);
    expect(tabs.every((tab) => used.has(tab))).toBe(true);
    expect(doc.querySelector('.stg-tab[data-tab="advanced"] .stg-tab-label')?.textContent).toBe(
      'System',
    );
  });
});

// what settings.js did to put a help line under each setting: the rule is
// parts.tsx's helpFirstLine (React renders the lines), and the pass over the
// markup is the fixtures' frozen copy (SETTINGS_PORT.md step V4b)
const help = {
  settingHelpFirstLine: helpFirstLine,
  initSettingHelpLines: (root?: Element) =>
    withHelpLines(root ?? document.getElementById('settings-page')!),
};

describe('help under each setting', () => {
  it('takes the first sentence and says when there is more', () => {
    expect(
      help.settingHelpFirstLine('  Skips explicit tracks.  Does not affect manual search. '),
    ).toEqual({
      line: 'Skips explicit tracks.',
      rest: true,
    });
    expect(help.settingHelpFirstLine('One line only.')).toEqual({
      line: 'One line only.',
      rest: false,
    });
    expect(help.settingHelpFirstLine('')).toEqual({ line: '', rest: false });
  });

  it('cuts a long first sentence at a word', () => {
    const long = `${'word '.repeat(60)}end.`;
    const { line, rest } = help.settingHelpFirstLine(long);
    expect(line.length).toBeLessThanOrEqual(141);
    expect(line.endsWith('…')).toBe(true);
    expect(rest).toBe(true);
  });

  it('adds a line per info icon and hides the icon when there is nothing more', () => {
    document.body.innerHTML = `
      <div id="settings-page">
        <div class="form-group"><label>A</label>
          <span class="info-icon" onclick="toggleSettingHelp(this)">i</span></div>
        <div class="help-text setting-help-body" hidden>Short and done.</div>
        <div class="form-group"><label>B</label>
          <span class="info-icon" onclick="toggleSettingHelp(this)">i</span>
          <small class="setting-help-body" hidden>First part. And a second part.</small></div>
      </div>`;
    help.initSettingHelpLines();
    help.initSettingHelpLines(); // idempotent
    const lines = [...document.querySelectorAll('.stg-help-text')].map((el) => el.textContent);
    expect(lines).toEqual(['Short and done.', 'First part.']);
    const icons = document.querySelectorAll<HTMLElement>('.info-icon');
    expect(icons[0].hidden).toBe(true);
    expect(icons[1].hidden).toBe(false);
    // the icon moved to the end of its line and still finds its help
    expect(icons[1].closest('.stg-help-line')).not.toBeNull();
    expect((icons[1] as HTMLElement & { __helpBody?: Element }).__helpBody?.textContent).toBe(
      'First part. And a second part.',
    );
  });

  it('leaves no vanilla info icon on the real page', () => {
    const doc = new DOMParser().parseFromString(html, 'text/html');
    document.body.replaceChildren(doc.getElementById('settings-page')!);
    help.initSettingHelpLines();
    // 104 when the React port began. the last went with Experimental
    // (SETTINGS_PORT.md step 10a): every help line is React's now, and each
    // tab's parity test checks its lines against the frozen markup
    expect(
      document.querySelectorAll('#settings-page .info-icon[onclick*="toggleSettingHelp"]'),
    ).toHaveLength(0);
    expect(document.querySelectorAll('#settings-page .stg-help-line')).toHaveLength(0);
  });
});

describe('loaders follow their sections', () => {
  const g = globalThis as Record<string, unknown>;
  beforeEach(() => {
    g.loadMetaSyncSettings = vi.fn();
    document.body.innerHTML = '<div id="settings-page"></div>';
  });
  afterEach(() => {
    delete g.loadMetaSyncSettings;
  });

  it('hands Appearance and Connections to their React tabs', () => {
    // the tab switch is shell.ts's (settings.js's until step V4b)
    const nav = switchSettingsTab;
    // Appearance is React: opening it is handed to the host, and the weather
    // tile reloads itself from there (appearance.test.tsx)
    const show = vi.fn();
    const shell = window as unknown as { SoulSyncSettings?: { show: (tab: string) => void } };
    shell.SoulSyncSettings = { show };
    nav('appearance');
    expect(show).toHaveBeenLastCalledWith('appearance');
    expect(g.loadMetaSyncSettings).not.toHaveBeenCalled();
    // MetaSync is React too, and loads itself each time Connections opens
    // (connections-metasync.test.tsx); the tab switch no longer calls it
    nav('connections');
    expect(show).toHaveBeenLastCalledWith('connections');
    expect(g.loadMetaSyncSettings).not.toHaveBeenCalled();
    delete shell.SoulSyncSettings;
  });
});
