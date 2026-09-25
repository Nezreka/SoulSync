import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { extractFunction } from './vanilla-extract';

/**
 * The acquisition panel: where a title stands right now.
 *
 * The history section below it says what already happened. This says what is
 * true, which is the question the page could not answer - owned/wanted lived in
 * the hero, in-flight grabs lived on the episode rows, failures lived nowhere,
 * and "nothing is hunting this" lived nowhere at all.
 *
 * queued and downloading are a SUBSET of wanted, so the panel must not present
 * them as separate totals that add up to something. It says so out loud.
 */

const SRC = readFileSync(resolve(process.cwd(), 'static/video/video-detail.js'), 'utf8');

function panel(state: unknown): HTMLElement {
  const preamble = `function esc(s) { return String(s == null ? '' : s); }`;
  const body = extractFunction('acqPanelHtml', SRC);
  // _ACQ_STATES is module state the function closes over, so it rides along too.
  const states = SRC.slice(SRC.indexOf('var _ACQ_STATES'), SRC.indexOf('function acqPanelHtml'));
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  const build = new Function(
    `${preamble}\n${states}\n${body}\nreturn acqPanelHtml;`,
  )() as (s: unknown) => string;
  const host = document.createElement('div');
  host.innerHTML = build(state);
  return host;
}

function chips(host: HTMLElement): Record<string, string> {
  const out: Record<string, string> = {};
  host.querySelectorAll('.vd-acq-chip').forEach((el) => {
    out[el.querySelector('.vd-acq-k')?.textContent ?? ''] =
      el.querySelector('.vd-acq-n')?.textContent ?? '';
  });
  return out;
}

const ZERO = { owned: 0, wanted: 0, queued: 0, downloading: 0, failed: 0, ignored: 0 };

describe('the acquisition panel', () => {
  it('shows a chip for every state that is actually true', () => {
    const host = panel({ total: 4, counts: { ...ZERO, owned: 1, wanted: 1, failed: 1, ignored: 1 } });
    expect(chips(host)).toEqual({ Owned: '1', Wanted: '1', Failed: '1', Ignored: '1' });
  });

  it('says nothing rather than printing a row of zeroes', () => {
    // An empty strip is noise; the section hides itself on an empty string.
    expect(panel({ total: 0, counts: { ...ZERO } }).innerHTML).toBe('');
    expect(panel(null).innerHTML).toBe('');
  });

  it('stays quiet when the title is already complete', () => {
    const host = panel({ total: 2, counts: { ...ZERO, owned: 2 } });
    expect(Object.keys(chips(host))).toEqual([]);
    expect(host.innerHTML).toBe('');
  });

  it('orders the chips the way you read them, not by count', () => {
    const host = panel({
      total: 9,
      counts: { owned: 1, wanted: 9, queued: 2, downloading: 3, failed: 1, ignored: 1 },
    });
    expect(Object.keys(chips(host))).toEqual([
      'Owned',
      'Wanted',
      'Queued',
      'Downloading',
      'Failed',
      'Ignored',
    ]);
  });

  it('admits that in-flight grabs are part of wanted, not extra to it', () => {
    // Without this the panel reads as 2 + 1 + 1 = 4 things wanted when there
    // are only 2, which is exactly the confusion it was built to remove.
    const withLive = panel({ total: 5, counts: { ...ZERO, wanted: 2, downloading: 1 } });
    expect(withLive.textContent).toContain('part of wanted');

    const without = panel({ total: 5, counts: { ...ZERO, wanted: 2 } });
    expect(without.textContent).not.toContain('part of wanted');
  });

  it('draws library coverage as a share of the whole title', () => {
    const fill = panel({ total: 4, counts: { ...ZERO, owned: 1, wanted: 3 } }).querySelector(
      '.vd-acq-bar-fill',
    ) as HTMLElement;
    expect(fill.style.width).toBe('25%');
  });

  it('drops the bar once nothing is outstanding', () => {
    // A full green bar under "8 Owned" repeats the chip and nothing else. It
    // earns its place only while something is still missing.
    expect(panel({ total: 8, counts: { ...ZERO, owned: 8 } }).querySelector('.vd-acq-bar')).toBeNull();
    expect(panel({ total: 8, counts: { ...ZERO, owned: 7, wanted: 1 } })
      .querySelector('.vd-acq-bar')).not.toBeNull();
  });

  it('draws no coverage bar when there is nothing to be a share of', () => {
    // total 0 would divide by zero and render width:NaN%.
    const host = panel({ total: 0, counts: { ...ZERO, failed: 1 } });
    expect(host.querySelector('.vd-acq-bar')).toBeNull();
    expect(host.innerHTML).not.toContain('NaN');
  });

  it('survives a payload with counts missing entirely', () => {
    expect(panel({ total: 3 }).innerHTML).toBe('');
  });
});
