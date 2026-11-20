/**
 * The page's search: it finds labels, sections and services wherever they
 * live (a pill, a sheet, a folded section), says where, and landing on one
 * opens the tab, the pill and the sheet and lights the field.
 */

import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { nav } from '../nav';
import { type Hit, SettingsSearch, buildIndex, landHit, searchHits, stateOf } from './search';

function page(html: string) {
  document.body.innerHTML = `<div id="settings-page">${html}</div>`;
}

const PAGE = `
  <div data-stg-tab="downloads" data-stg-view="behaviour" hidden>
    <div data-card="Retry logic">
      <h4>Retry logic</h4>
      <div data-row><label for="retry-exhaustive">Exhaustive retry (separate budget per source)</label>
        <input type="checkbox" id="retry-exhaustive" checked></div>
      <div data-row><label for="made-up-timeout">Peer patience:</label>
        <input type="number" id="made-up-timeout" value=""></div>
    </div>
  </div>
  <div id="soulseek-settings-container" class="src-config-panel">
    <div class="form-group"><label>Cleanup scope:</label>
      <select id="made-up-scope"><option value="own" selected>Only SoulSync's own transfers</option></select></div>
  </div>
  <div class="settings-section-header" data-stg="appearance"><h3>Post-Processing</h3></div>
  <div class="settings-section-body collapsed" data-stg="appearance">
    <div class="form-group" data-video-only><label>Video only thing</label><input type="text" id="vo"></div>
    <div class="form-group"><label>Write multi-artist tags</label><input type="checkbox" id="made-up-tags"></div>
  </div>
  <div data-stg="advanced" class="settings-group"><h3>SoulSync API</h3>
    <div class="form-group"><label>Secret thing</label><input type="password" id="made-up-secret" value="hunter2"></div>
  </div>
  <div data-stg="logs" class="settings-group"><label>Follow the made-up log</label><input type="checkbox" id="made-up-follow"></div>
`;

const find = (index: Hit[], label: string) => index.find((hit) => hit.label === label) as Hit;

beforeEach(() => {
  nav.reset();
  page(PAGE);
});
afterEach(() => {
  document.body.innerHTML = '';
  vi.restoreAllMocks();
});

describe('what it finds', () => {
  it('every label on the page, with where it lives', () => {
    const index = buildIndex('music');
    expect(find(index, 'Peer patience')).toMatchObject({
      path: 'Downloads › Behaviour › Retry logic',
      tab: 'downloads',
      view: 'behaviour',
    });
    // inside a source's sheet
    expect(find(index, 'Cleanup scope')).toMatchObject({
      path: 'Downloads › Sources › Soulseek',
      open: { source: 'soulseek' },
    });
    // a folded section of a tab not rebuilt yet
    expect(find(index, 'Write multi-artist tags').path).toBe('Appearance › Post-Processing');
    // old tab keys are where their content went
    expect(find(index, 'Follow the made-up log')).toMatchObject({
      path: 'System › Logs',
      tab: 'advanced',
      view: 'logs',
    });
    // sections too: a block under the old System key lands on its first pill
    expect(find(index, 'SoulSync API').path).toBe('System › General');
  });

  it('the curated entries speak for their field, so it is not listed twice', () => {
    const index = buildIndex('music');
    // "retry-exhaustive" has no curated entry here; "Concurrent downloads" does
    expect(index.filter((hit) => hit.label === 'Concurrent downloads')).toHaveLength(1);
    expect(find(index, 'Concurrent downloads').curated).toBe(true);
  });

  it("leaves out the other side's settings", () => {
    expect(find(buildIndex('music'), 'Video only thing')).toBeUndefined();
    expect(find(buildIndex('video'), 'Video only thing')).toBeTruthy();
  });

  it('matches every word typed, labels before where they live', () => {
    const index = buildIndex('music');
    expect(searchHits(index, 'peer pat')[0].label).toBe('Peer patience');
    expect(searchHits(index, 'soulseek cleanup').map((hit) => hit.label)).toContain(
      'Cleanup scope',
    );
    expect(searchHits(index, 'zzzz')).toEqual([]);
    expect(searchHits(index, '   ')).toEqual([]);
  });

  it('says what a setting is set to, never what was typed in it', () => {
    const index = buildIndex('music');
    expect(stateOf(find(index, 'Exhaustive retry (separate budget per source)'))).toBe('On');
    expect(stateOf(find(index, 'Peer patience'))).toBe('Not set');
    expect(stateOf(find(index, 'Cleanup scope'))).toBe("Only SoulSync's own transfers");
    expect(stateOf(find(index, 'Secret thing'))).toBe('Set');
  });
});

describe('landing on one', () => {
  it('opens the tab and the pill, then lights the field', async () => {
    const hit = find(buildIndex('music'), 'Peer patience');
    await act(async () => {
      await landHit(hit);
    });
    expect(nav.get()).toMatchObject({ tab: 'downloads', view: 'behaviour' });
    expect(document.activeElement?.id).toBe('made-up-timeout');
    expect(document.querySelector('[data-row].stg-search-target')).toBeTruthy();
  });

  it("opens a source's sheet for a field inside it", async () => {
    const open = vi.fn();
    (window as unknown as { openSourceModal: unknown }).openSourceModal = open;
    await act(async () => {
      await landHit(find(buildIndex('music'), 'Cleanup scope'));
    });
    expect(open).toHaveBeenCalledWith('soulseek');
    expect(nav.get()).toMatchObject({ tab: 'downloads', view: 'sources' });
  });
});

describe('the box', () => {
  it('lists results as typed, arrows move, Enter lands, Escape closes', async () => {
    render(<SettingsSearch side="music" />, {
      container: document.body.appendChild(document.createElement('div')),
    });
    const input = screen.getByRole('combobox', { name: 'Search settings' });
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: 'peer' } });
    const options = within(screen.getByRole('listbox')).getAllByRole('option');
    expect(options[0].textContent).toContain('Peer patience');
    expect(options[0].textContent).toContain('Downloads › Behaviour › Retry logic');
    // what was typed is lit in the label
    expect(options[0].querySelector('mark')?.textContent).toBe('Peer');
    expect(options[0].getAttribute('aria-selected')).toBe('true');
    fireEvent.keyDown(input, { key: 'Escape' });
    expect(screen.getByRole('listbox', { hidden: true }).hidden).toBe(true);
    fireEvent.change(input, { target: { value: 'peer pat' } });
    await act(async () => {
      fireEvent.keyDown(input, { key: 'Enter' });
      await new Promise((resolve) => setTimeout(resolve, 80));
    });
    expect(nav.get()).toMatchObject({ tab: 'downloads', view: 'behaviour' });
    expect((input as HTMLInputElement).value).toBe('');
  });

  it('says so when nothing matches', () => {
    render(<SettingsSearch side="music" />, {
      container: document.body.appendChild(document.createElement('div')),
    });
    const input = screen.getByRole('combobox', { name: 'Search settings' });
    fireEvent.focus(input);
    fireEvent.change(input, { target: { value: 'qqqqq' } });
    expect(screen.getByRole('status').textContent).toMatch(/No settings found/);
  });
});
