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
  '<input data-vmg-ovr="aliases" value="Password, Password Plus">' +
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
      manual_aliases: ['Password', 'Password Plus'],
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
      manual_aliases: [],
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


describe('manual alternative titles', () => {
  const JS = readFileSync(resolve(process.cwd(), 'static/video/video-manage-panel.js'), 'utf8');

  it('is offered as a plain comma-separated list', () => {
    expect(JS).toContain("inputHtml2('aliases'");
    expect(JS).toContain('Also known as');
  });

  it('says TMDB aliases are already covered, so it is not filled in by rote', () => {
    // TMDB already supplies "Big Brother US" for "Big Brother (US)". Re-typing
    // what is already matched is wasted effort and invites collisions.
    // Matched without the apostrophe: the source carries it as a \\u2019 escape,
    // so asserting the rendered character compares against the wrong thing.
    expect(JS).toContain('own aliases are already matched');
  });

  it('tells the user when the change takes effect', () => {
    // The alias set is read on the NEXT search, not retroactively. Without this
    // the honest report is "I added it and nothing happened".
    expect(JS).toContain('applies from the next search');
  });

  it('sits with the quality ladder, not under the narrowing overrides', () => {
    // Aliases WIDEN what matches; everything under "Acquisition overrides"
    // narrows it. Filing it there would read as one more restriction.
    const body = JS.slice(JS.indexOf('function bodyHtml'), JS.indexOf('function inputHtml2'));
    expect(body.indexOf("inputHtml2('aliases'"))
      .toBeLessThan(body.indexOf('Acquisition overrides'));
  });

  it('groups the acquisition settings under their own heading', () => {
    // Quality profile and series type used to sit at the tail of "Artwork &
    // state", so a section about posters appeared to own the quality ladder.
    const body = JS.slice(JS.indexOf('function bodyHtml'), JS.indexOf('function inputHtml2'));
    const artwork = body.indexOf('Artwork &amp; state');
    const acq = body.indexOf("'<div class=\"vmg-sect\">Acquisition</div>'");
    const profile = body.indexOf('data-vmg-quality-profile');
    expect(acq).toBeGreaterThan(artwork);
    expect(profile).toBeGreaterThan(acq);
  });
});
