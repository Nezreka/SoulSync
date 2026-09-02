import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { extractFunction } from './vanilla-extract';

/**
 * Per-title acquisition overrides, front half.
 *
 * These narrow ONE title's download rules without touching the global config,
 * so the load-bearing rule is that "nothing chosen" means "follow the global
 * settings" rather than "allow nothing". An empty allow-list that reads as a
 * filter would quietly stop a title being grabbed at all.
 */

const SRC = readFileSync(resolve(process.cwd(), 'static/video/video-manage-panel.js'), 'utf8');

// eslint-disable-next-line @typescript-eslint/no-implied-eval
const parseList = new Function(
  `${extractFunction('parseList', SRC)}\nreturn parseList;`,
)() as (t: string) => string[];

function overrides(html: string) {
  const overlay = document.createElement('div');
  overlay.innerHTML = html;
  const preamble = `var state = { overlay: overlay };`;
  const bodies = ['parseList', 'currentOverrides'].map((n) => extractFunction(n, SRC)).join('\n');
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  return new Function('overlay', `${preamble}\n${bodies}\nreturn currentOverrides();`)(
    overlay,
  ) as Record<string, unknown>;
}

const FORM =
  '<input type="checkbox" data-vmg-src value="torrent" checked>' +
  '<input type="checkbox" data-vmg-src value="usenet">' +
  '<input type="checkbox" data-vmg-src value="soulseek" checked>' +
  '<input data-vmg-ovr="rg-allow" value="NTb, FLUX">' +
  '<input data-vmg-ovr="rg-block" value="YIFY">' +
  '<select data-vmg-pack-pref><option value="never" selected>never</option></select>';

describe('reading a release-group list', () => {
  it('trims, drops blanks, and de-dupes case-insensitively', () => {
    expect(parseList('NTb, , ntb ,FLUX')).toEqual(['NTb', 'FLUX']);
  });

  it('keeps the casing the user typed for the first occurrence', () => {
    expect(parseList('ntb, NTb')).toEqual(['ntb']);
  });

  it('reads an empty field as no restriction at all', () => {
    // NOT as "allow nothing" - an empty allow-list that filtered would stop the
    // title being grabbed by anything, which is the opposite of not setting one.
    expect(parseList('')).toEqual([]);
    expect(parseList('   ')).toEqual([]);
    expect(parseList(',,, ,')).toEqual([]);
    expect(parseList(null as unknown as string)).toEqual([]);
  });
});

describe('collecting the override form', () => {
  it('sends exactly what is ticked, in the engine chain order', () => {
    expect(overrides(FORM)).toEqual({
      preferred_sources: ['torrent', 'soulseek'],
      release_group_allow: ['NTb', 'FLUX'],
      release_group_block: ['YIFY'],
      pack_preference: 'never',
    });
  });

  it('sends empty lists when nothing is chosen, meaning follow the global config', () => {
    const bare =
      '<input type="checkbox" data-vmg-src value="torrent">' +
      '<input data-vmg-ovr="rg-allow" value="">' +
      '<input data-vmg-ovr="rg-block" value="">';
    expect(overrides(bare)).toEqual({
      preferred_sources: [],
      release_group_allow: [],
      release_group_block: [],
      pack_preference: 'auto',
    });
  });

  it('defaults pack preference to auto on a movie, which has no season packs', () => {
    // The movie panel renders no pack selector at all.
    expect(overrides('<input data-vmg-ovr="rg-allow" value="">').pack_preference).toBe('auto');
  });
});

describe('the override panel is wired like the quality profile beside it', () => {
  it('saves immediately on change rather than waiting for the metadata Save', () => {
    expect(SRC).toContain("method: 'PUT'");
    const save = extractFunction('saveOverrides', SRC);
    expect(save).toContain("'/overrides'");
    expect(save).toContain('currentOverrides()');
  });

  it('offers the three sources the engine actually tries', () => {
    const chain = SRC.slice(SRC.indexOf('var SOURCES ='), SRC.indexOf('var PACK_PREFS'));
    for (const s of ['torrent', 'usenet', 'soulseek']) expect(chain).toContain(`'${s}'`);
  });

  it('spells out that an untouched form changes nothing', () => {
    expect(SRC).toContain('None ticked follows the global download order.');
    expect(SRC).toContain('Follow the global setting');
  });
});
