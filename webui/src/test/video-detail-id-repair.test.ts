import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { extractFunction } from './vanilla-extract';

/**
 * A missing id is a job, not a headline.
 *
 * The manage panel has had a full re-match flow for a while - search a service,
 * pick the right title, apply it - and nothing on the detail page pointed at it.
 * The health band knows exactly which id is missing, so the chip that reports
 * the gap is the right place to start the repair.
 *
 * The one rule that isn't obvious: only a LIBRARY row can be re-matched. A tmdb
 * preview has no row to repair, so its chips must stay inert rather than open a
 * panel that would 404.
 */

const DETAIL = readFileSync(resolve(process.cwd(), 'static/video/video-detail.js'), 'utf8');
const MANAGE = readFileSync(resolve(process.cwd(), 'static/video/video-manage-panel.js'), 'utf8');

function mount(): { host: HTMLElement; render: (d: unknown) => void } {
  const host = document.createElement('div');
  const preamble = `
    function esc(s) { return String(s == null ? '' : s); }
    function q() { return host; }
  `;
  const bodies = ['countLabel', 'healthChip', 'idChip', 'renderHealth']
    .map((n) => extractFunction(n, DETAIL))
    .join('\n');
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  const render = new Function('host', `${preamble}\n${bodies}\nreturn renderHealth;`)(host) as (
    d: unknown,
  ) => void;
  return { host, render };
}

function fixTargets(host: HTMLElement): string[] {
  return Array.from(host.querySelectorAll('[data-vd-health-fix]')).map(
    (el) => el.getAttribute('data-vd-health-fix') ?? '',
  );
}

const OWNED_SHOW = { kind: 'show', source: 'library', id: 41, tmdb_id: 1396 };

describe('repairing a missing id from the health band', () => {
  it('turns every missing id on a library title into a repair button', () => {
    const m = mount();
    m.render(OWNED_SHOW); // tvdb + imdb missing
    expect(fixTargets(m.host)).toEqual(['tvdb', 'imdb']);
  });

  it('leaves a present id as a plain chip, not a button', () => {
    const m = mount();
    m.render({ ...OWNED_SHOW, tvdb_id: 81189, imdb_id: 'tt0903747' });
    expect(fixTargets(m.host)).toEqual([]);
    expect(m.host.querySelectorAll('button')).toHaveLength(0);
  });

  it('offers no repair on a preview, which has no library row to repair', () => {
    const m = mount();
    m.render({ kind: 'movie', source: 'tmdb', tmdb_id: 27205 });
    expect(m.host.textContent).toBe('');
    expect(fixTargets(m.host)).toEqual([]);
  });

  it('keeps the repair button styled as a missing chip', () => {
    const m = mount();
    m.render(OWNED_SHOW);
    const btn = m.host.querySelector('[data-vd-health-fix="tvdb"]') as HTMLElement;
    expect(btn.tagName).toBe('BUTTON');
    expect(btn.className).toContain('vd-health-chip--missing');
    expect(btn.textContent).toContain('TVDB');
  });
});

describe('the manage panel honours a focused service', () => {
  type Focus = { state: Record<string, unknown> | null; applyFocusMatch: () => void };

  function loadFocus(svc: string | null, host: HTMLElement) {
    const preamble = `
      var MATCH_LABELS = { tmdb: 'TMDB', tvdb: 'TVDB', imdb: 'IMDb' };
      var opened = [];
      var state = ${svc === null ? 'null' : `{ overlay: host, focusMatch: ${JSON.stringify(svc)} }`};
      function openMatchSearch(s) { opened.push(s); }
    `;
    const body = extractFunction('applyFocusMatch', MANAGE);
    // eslint-disable-next-line @typescript-eslint/no-implied-eval
    return new Function(
      'host',
      `${preamble}\n${body}\napplyFocusMatch();\nreturn { opened: opened, left: state && state.focusMatch };`,
    )(host) as { opened: string[]; left: string | null | undefined };
  }

  function overlay(): HTMLElement {
    const el = document.createElement('div');
    el.innerHTML = '<div data-vmg-matches><input data-vmg-imdb-in /></div>';
    (el.querySelector('[data-vmg-matches]') as HTMLElement).scrollIntoView = () => {};
    // focus() only moves activeElement for a node that is actually in the document.
    document.body.appendChild(el);
    return el;
  }

  it('opens that service’s search straight away', () => {
    expect(loadFocus('tvdb', overlay()).opened).toEqual(['tvdb']);
  });

  it('focuses the IMDb field instead, because IMDb is typed not searched', () => {
    const host = overlay();
    const r = loadFocus('imdb', host);
    expect(r.opened).toEqual([]);
    expect(host.ownerDocument.activeElement).toBe(host.querySelector('[data-vmg-imdb-in]'));
  });

  it('fires once, so a later match reload does not yank the panel back', () => {
    const r = loadFocus('tvdb', overlay());
    expect(r.left).toBeNull();
  });

  it('does nothing when the panel was opened normally', () => {
    expect(loadFocus(null, overlay()).opened).toEqual([]);
  });

  it('ignores a service it does not know', () => {
    expect(loadFocus('trakt', overlay()).opened).toEqual([]);
  });
});

describe('a youtube episode row links out cleanly', () => {
  function row(ep: Record<string, unknown>): HTMLElement {
    const preamble = `
      var selectedSeason = 1;
      var data = { source: 'youtube' };
      function esc(s) { return String(s == null ? '' : s); }
      function fmtDate(d) { return String(d); }
      function ytProx(u) { return u || ''; }
    `;
    const body = extractFunction('ytEpisodeRow', DETAIL);
    // eslint-disable-next-line @typescript-eslint/no-implied-eval
    const build = new Function(`${preamble}\n${body}\nreturn ytEpisodeRow;`)() as (
      e: unknown,
    ) => string;
    const host = document.createElement('div');
    host.innerHTML = build(ep);
    return host;
  }

  it('links to the video, marked so the row click passes it through', () => {
    const host = row({ episode_number: 1, title: 'A video', youtube_id: 'dQw4w9WgXcQ' });
    const a = host.querySelector('.vd-ep-vid') as HTMLAnchorElement;
    expect(a.textContent).toBe('YouTube');
    expect(a.getAttribute('href')).toBe('https://www.youtube.com/watch?v=dQw4w9WgXcQ');
    // Without data-vd-ext the delegated row handler swallows the click and
    // expands the description panel instead of opening the video.
    expect(a.hasAttribute('data-vd-ext')).toBe(true);
    expect(a.getAttribute('rel')).toContain('noopener');
  });

  it('renders no external chip when there is no id', () => {
    const host = row({ episode_number: 1, title: 'A video', youtube_id: '' });
    expect(host.querySelector('.vd-ep-vid')).toBeNull();
  });
});
