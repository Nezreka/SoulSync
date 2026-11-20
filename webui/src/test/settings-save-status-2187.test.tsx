/**
 * #2187: "All changes saved" showed on a Settings page that was only opened
 * (nothing changed, nothing saved), and on a phone it sat over the section
 * title. The status now opens at rest, which the music side does not show,
 * and on a phone it wraps under the title instead of squeezing it.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { SettingsPage } from '@/features/settings/settings-page';
import { setSettingsSaveState } from '@/features/settings/shell';

const read = (p: string) => readFileSync(resolve(process.cwd(), p), 'utf8').replace(/\r\n/g, '\n');
const PREMIER = read('static/settings-premier.css');
const HTML = renderToStaticMarkup(<SettingsPage tabs={{}} />);

const set = setSettingsSaveState as (state: string) => void;
const btn = () => document.getElementById('save-settings')!;

/** the brace-matched body of the first `@media (max-width: <px>px)` */
function media(css: string, px: number): string {
  const at = css.indexOf(`@media (max-width: ${px}px)`);
  const open = css.indexOf('{', at);
  let depth = 0;
  for (let i = open; i < css.length; i++) {
    if (css[i] === '{') depth++;
    if (css[i] === '}' && --depth === 0) return css.slice(open + 1, i);
  }
  return '';
}

afterEach(() => {
  vi.useRealTimers();
  document.body.innerHTML = '';
});

describe('#2187: no saved status without a save', () => {
  it('the page opens with the status at rest', () => {
    expect(HTML).toMatch(/id="save-settings" data-state="rest"/);
    expect(HTML).not.toMatch(/id="save-settings" data-state="idle"/);
  });

  it('rest is hidden on the music side and kept on the video side', () => {
    expect(PREMIER).toContain(
      'body:not([data-side="video"]) #settings-page .save-button[data-state="rest"] { display: none; }',
    );
    // on the video side it still looks like the quiet status
    expect(PREMIER).toMatch(
      /#settings-page \.save-button\[data-state="rest"\],\n#settings-page \.save-button\[data-state="idle"\]/,
    );
  });

  it('a change leaves rest, and a save comes back to idle, not rest', () => {
    vi.useFakeTimers();
    document.body.innerHTML =
      '<button id="save-settings" data-state="rest"><svg></svg><span>All changes saved</span></button>';
    set('pending');
    expect(btn().dataset.state).toBe('pending');
    set('saving');
    set('saved');
    vi.advanceTimersByTime(2000);
    expect(btn().dataset.state).toBe('idle');
    expect(btn().textContent).toBe('All changes saved');
  });

  it('on a phone the status wraps under the title instead of squeezing it', () => {
    const phone = media(PREMIER, 560);
    expect(phone).toMatch(/#settings-page \.settings-nav-row \{[^}]*flex-wrap: wrap;/);
    expect(phone).toMatch(/#settings-page \.stg-active-section-title \{ flex: 1 1 100%; \}/);
    // the base row's nowrap must not be !important, or the phone wrap loses
    const base = PREMIER.slice(PREMIER.indexOf('#settings-page .settings-nav-row {'));
    expect(base.slice(0, base.indexOf('}'))).not.toMatch(/flex-wrap:\s*nowrap\s*!important/);
  });
});
