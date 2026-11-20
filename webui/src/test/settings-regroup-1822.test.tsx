import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { resolveTab, tabDef } from '@/features/settings/nav';
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
    // settings-bic: a pill of the Connections page, which the page draws as one slot
    expect(html).toContain('<div data-stg-react="connections" data-stg-part="page"');
    expect(tabDef('connections').views.map((v) => v.key)).toContain('metasync');
  });

  it('Video Quality is on quality, its own pill (settings-bic phase 3)', () => {
    const page = readFileSync(
      resolve(process.cwd(), 'src/features/settings/tabs/quality/quality-page.tsx'),
      'utf8',
    );
    expect(page).toMatch(/<ViewPanel tab=\{TAB\} view="video"[^>]*>\s*<VideoQualitySections \/>/);
    expect(html).toContain('<div data-stg-react="quality" data-stg-part="page"');
    expect(tabDef('quality').views.map((v) => v.label)).toContain('Movies and TV');
  });

  it('the video pages card is on Appearance (settings-bic phase 4, DESIGN 3.3)', () => {
    const section = readFileSync(
      resolve(process.cwd(), 'src/features/settings/tabs/library-video-prefs.tsx'),
      'utf8',
    );
    expect(section).toContain('<div data-ui="" data-video-only=""');
    expect(section).toContain('title="Video pages"');
    // settings-bic phase 5: Appearance is one page, the card in it
    const page = readFileSync(
      resolve(process.cwd(), 'src/features/settings/tabs/appearance-tab.tsx'),
      'utf8',
    );
    expect(page).toContain('<VideoPagesCard />');
    expect(html).toContain('<div data-stg-react="appearance" data-stg-part="page"');
  });

  it('Notifications is a pill of Connections, on the parts (settings-bic phase 6)', () => {
    const page = readFileSync(
      resolve(process.cwd(), 'src/features/settings/tabs/connections/connections-page.tsx'),
      'utf8',
    );
    expect(page).toMatch(
      /<ViewPanel tab=\{TAB\} view="notifications"[^>]*>[\s\S]*<NotificationsCard \/>/,
    );
    const lists = readFileSync(
      resolve(process.cwd(), 'src/features/settings/tabs/video-row-lists.tsx'),
      'utf8',
    );
    const body = lists.split('export function NotificationsCard(')[1];
    expect(body).toContain('title="Notifications"');
    expect(body).toContain('id="video-notifications"');
    expect(html).toContain('<div data-stg-react="connections" data-stg-part="page"');
    expect(tabDef('connections').views.map((v) => v.key)).toContain('notifications');
  });

  it('Import lists, playlists and recommendations are pills of Discovery (settings-bic phase 5)', () => {
    const page = readFileSync(
      resolve(process.cwd(), 'src/features/settings/tabs/discovery/discovery-page.tsx'),
      'utf8',
    );
    expect(page).toMatch(
      /<ViewPanel tab=\{TAB\} view="import-lists"[^>]*>[\s\S]*<ImportListsCard \/>/,
    );
    expect(page).toMatch(/<ViewPanel tab=\{TAB\} view="playlists"[^>]*>[\s\S]*<Playlists /);
    expect(html).toContain('<div data-stg-react="discovery" data-stg-part="page"');
    expect(tabDef('discovery').views.map((v) => v.key)).toEqual([
      'recommendations',
      'playlists',
      'import-lists',
    ]);
    expect(tabDef('discovery').views.find((v) => v.key === 'import-lists')?.side).toBe('video');
  });

  it('Database Maintenance is a pill of System, with Clear quarantine (settings-bic phase 6)', () => {
    const read = (f: string) =>
      readFileSync(resolve(process.cwd(), 'src/features/settings/tabs/' + f), 'utf8');
    const page = read('system/system-page.tsx');
    expect(page).toContain("const TAB = 'advanced';");
    expect(page).toContain('<MaintenanceView store={store}');
    expect(read('system-tab.tsx')).toContain('title="Database"');
    expect(read('system-tab.tsx')).toContain('id="quarantine-clear-btn"');
    // it left the AcoustID sheet
    expect(read('connections-extras.tsx')).not.toContain('/api/quarantine/clear');
    expect(html).toContain('<div data-stg-react="advanced" data-stg-part="page"');
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
    // the Logs slot is System's now (a pill of it): old keys resolve to their tab
    expect([...used].every((tag) => tabs.includes(resolveTab(tag).tab))).toBe(true);
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
