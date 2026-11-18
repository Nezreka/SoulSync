/**
 * The page's markup as the Python tests read it (SETTINGS_PORT.md step V4c).
 * They pinned the Settings page in index.html; it is React now, so its shell
 * (without the tabs' own content) is kept rendered in
 * __fixtures__/settings-page.html, and this keeps that file the page's:
 * it fails when the page changes and the file does not. Write it again with
 * SETTINGS_PAGE_SNAPSHOT=write.
 */

import { readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { SettingsPage } from './settings-page';

const FILE = resolve(process.cwd(), 'src/features/settings/__fixtures__/settings-page.html');

describe('the page markup the Python tests read', () => {
  it('is the page as it renders', () => {
    const markup = `${renderToStaticMarkup(<SettingsPage tabs={{}} />)}\n`;
    if (process.env.SETTINGS_PAGE_SNAPSHOT === 'write') writeFileSync(FILE, markup);
    expect(readFileSync(FILE, 'utf8').replace(/\r\n/g, '\n')).toBe(markup);
  });
});
