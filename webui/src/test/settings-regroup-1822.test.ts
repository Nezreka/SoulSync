import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// #1822: settings were "buried". Library held discovery and playlists, Advanced
// held notifications, metasync and the sidebar weather. Sections now sit where
// people look for them, and each setting says what it does under it.

const html = readFileSync(resolve(process.cwd(), 'index.html'), 'utf8');
const source = readFileSync(resolve(process.cwd(), 'static/settings.js'), 'utf8');

function sectionTab(doc: Document, heading: string): string | null {
  const h3 = [...doc.querySelectorAll('#settings-page h3')].find(
    (el) => el.textContent?.trim() === heading,
  );
  return h3?.closest('[data-stg]')?.getAttribute('data-stg') ?? null;
}

describe('where sections live', () => {
  const doc = new DOMParser().parseFromString(html, 'text/html');

  it.each([
    ['MetaSync', 'connections'],
    ['Notifications', 'connections'],
    ['Discovery', 'discovery'],
    ['Playlists', 'discovery'],
    ['Listening Stats', 'discovery'],
    ['Discovery Pool Settings', 'discovery'],
    ['Import Lists', 'discovery'],
    ['Sidebar Weather', 'appearance'],
    ['Filtering', 'downloads'],
    ['Video Quality', 'quality'],
    ['Album Releases', 'quality'],
    ['Video Preferences', 'library'],
    ['Database Maintenance', 'advanced'],
  ])('%s is on %s', (heading, tab) => {
    expect(sectionTab(doc, heading)).toBe(tab);
  });

  it('has a tab for every section tag and no tab left empty', () => {
    const tabs = [...doc.querySelectorAll('.stg-tab')].map((t) => t.getAttribute('data-tab'));
    const used = new Set(
      [...doc.querySelectorAll('#settings-page [data-stg]')].map((el) =>
        el.getAttribute('data-stg'),
      ),
    );
    expect([...used].every((tag) => tabs.includes(tag))).toBe(true);
    expect(tabs.every((tab) => used.has(tab))).toBe(true);
    expect(doc.querySelector('.stg-tab[data-tab="advanced"] .stg-tab-label')?.textContent).toBe(
      'System',
    );
  });
});

const helpBlock = source.slice(
  source.indexOf('function _settingHelpBody('),
  source.indexOf('function toggleSettingHelp('),
);
const lineBlock = source.slice(
  source.indexOf('function settingHelpFirstLine('),
  source.indexOf('window.initSettingHelpLines = initSettingHelpLines;'),
);
const help = new Function(
  'document',
  `${helpBlock}\n${lineBlock}\nreturn { settingHelpFirstLine, initSettingHelpLines };`,
)(document) as {
  settingHelpFirstLine: (text: string) => { line: string; rest: boolean };
  initSettingHelpLines: (root?: Element) => void;
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

  it('finds a body for every info icon on the real page', () => {
    const doc = new DOMParser().parseFromString(html, 'text/html');
    document.body.replaceChildren(doc.getElementById('settings-page')!);
    help.initSettingHelpLines();
    const icons = document.querySelectorAll(
      '#settings-page .info-icon[onclick*="toggleSettingHelp"]',
    );
    const lines = document.querySelectorAll('#settings-page .stg-help-line');
    expect(icons.length).toBeGreaterThan(90);
    // nearly every icon has a body; the handful that don't keep their icon
    expect(lines.length).toBeGreaterThan(icons.length * 0.9);
  });
});

describe('loaders follow their sections', () => {
  const navBlock = source.slice(
    source.indexOf('function switchSettingsTab('),
    source.indexOf('function filterSettings('),
  );
  const g = globalThis as Record<string, unknown>;
  beforeEach(() => {
    g.loadSidebarWeatherSettings = vi.fn();
    g.loadMetaSyncSettings = vi.fn();
    document.body.innerHTML = '<div id="settings-page"></div>';
  });
  afterEach(() => {
    delete g.loadSidebarWeatherSettings;
    delete g.loadMetaSyncSettings;
  });

  it('loads the weather on Appearance and MetaSync on Connections', () => {
    const nav = new Function(
      'document',
      'window',
      '_logViewerStop',
      '_logViewerInit',
      `${navBlock}; return switchSettingsTab;`,
    )(document, window, vi.fn(), vi.fn()) as (tab: string) => void;
    nav('appearance');
    expect(g.loadSidebarWeatherSettings).toHaveBeenCalledOnce();
    expect(g.loadMetaSyncSettings).not.toHaveBeenCalled();
    nav('connections');
    expect(g.loadMetaSyncSettings).toHaveBeenCalledOnce();
    nav('advanced');
    expect(g.loadSidebarWeatherSettings).toHaveBeenCalledOnce();
  });
});
