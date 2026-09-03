import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { extractFunction } from './vanilla-extract';

/**
 * The stuck-row diagnostics drawer.
 *
 * The row's tooltip said how often it had been searched and gave a headline
 * refusal. It could not say WHICH releases came back, why each lost, where the
 * file would land, which external ids the search was keyed on, or whether
 * something was already downloading - so "why is this stuck" meant reading
 * three screens and guessing. Twice in one day that guess was wrong.
 */

const JS = readFileSync(resolve(process.cwd(), 'static/video/video-wishlist.js'), 'utf8');
const CSS = readFileSync(resolve(process.cwd(), 'static/video/video-side.css'), 'utf8');

function render(payload: unknown): HTMLElement {
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  const fn = new Function(
    `function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;');}\n` +
      `${extractFunction('diagRowsHTML', JS)}\nreturn diagRowsHTML;`,
  )() as (d: unknown) => string;
  const host = document.createElement('div');
  host.innerHTML = fn(payload);
  return host;
}

const BASE = {
  row: { search_attempts: 9, last_search_at: '2026-09-02 12:00',
         last_refusal: '15 results for this title, but not this episode yet',
         search_snapshot: null },
  ids: { tmdb_id: 10160, tvdb_id: 1234, imdb_id: 'tt0123456' },
  target_dir: '/media/TV',
  downloads: [],
};

function line(host: HTMLElement, key: string) {
  const el = Array.from(host.querySelectorAll('.vwsh-diag-line')).find(
    (n) => n.querySelector('.vwsh-diag-k')?.textContent === key,
  );
  return el?.querySelector('.vwsh-diag-v')?.textContent ?? null;
}

describe('the drawer', () => {
  it('answers the questions the tooltip could not', () => {
    const host = render(BASE);
    expect(line(host, 'Attempts')).toBe('9 searches');
    expect(line(host, 'Would land in')).toBe('/media/TV');
    expect(line(host, 'TVDB')).toBe('1234');
    expect(line(host, 'Latest reason')).toContain('not this episode yet');
  });

  it('marks a MISSING id, because that is often the answer', () => {
    const host = render({ ...BASE, ids: { tmdb_id: 10160, tvdb_id: null, imdb_id: null } });
    const tvdb = Array.from(host.querySelectorAll('.vwsh-diag-line')).find(
      (n) => n.querySelector('.vwsh-diag-k')?.textContent === 'TVDB',
    );
    expect(tvdb?.className).toContain('vwsh-diag-line--gap');
    expect(line(host, 'TVDB')).toBe('—');
  });

  it('says when the row is actually mid-download, not stuck', () => {
    const host = render({ ...BASE,
      downloads: [{ status: 'downloading', progress: 42.4, release_title: 'BB S28E27' }] });
    expect(line(host, 'In flight')).toBe('downloading · 42% · BB S28E27');
  });

  it('lists each release and the rule that turned it down', () => {
    const host = render({ ...BASE, row: { ...BASE.row, search_snapshot: {
      chain: ['torrent'],
      sources: { torrent: { ran: true, results: 15, accepted: 0, samples: [
        { title: 'Big Brother US S28E25 1080p', accepted: false, rejected: 'Wrong episode' },
        { title: 'Big Brother US S28E27 2160p', accepted: false, rejected: '4K not enabled' },
      ] } } } } });
    const rels = Array.from(host.querySelectorAll('.vwsh-diag-rel'));
    expect(rels).toHaveLength(2);
    expect(rels[0].textContent).toContain('Big Brother US S28E25 1080p');
    expect(rels[0].textContent).toContain('Wrong episode');
    expect(rels[1].textContent).toContain('4K not enabled');
    expect(host.querySelector('.vwsh-diag-src')?.textContent).toContain('15 found, 0 usable');
  });

  it('marks an accepted release differently from a refused one', () => {
    const host = render({ ...BASE, row: { ...BASE.row, search_snapshot: {
      chain: ['torrent'],
      sources: { torrent: { ran: true, results: 1, accepted: 1, samples: [
        { title: 'Good one', accepted: true, rejected: null }] } } } } });
    const rel = host.querySelector('.vwsh-diag-rel');
    expect(rel?.className).toContain('vwsh-diag-rel--ok');
    expect(rel?.textContent).toContain('usable');
  });

  it('says a source could not search rather than implying it found nothing', () => {
    const host = render({ ...BASE, row: { ...BASE.row, search_snapshot: {
      chain: ['soulseek'],
      sources: { soulseek: { ran: false, reason: 'slskd is unreachable', samples: [] } } } } });
    expect(host.querySelector('.vwsh-diag-src')?.textContent)
      .toContain('could not search: slskd is unreachable');
  });

  it('explains an empty receipt list rather than showing a blank block', () => {
    // Rows searched before receipts existed have none, and a silent gap there
    // reads as "no releases found", which is a different and wrong answer.
    const host = render({ ...BASE, row: { ...BASE.row, search_snapshot: {
      chain: ['torrent'], sources: { torrent: { ran: true, results: 15, accepted: 0 } } } } });
    expect(host.querySelector('.vwsh-diag-rel--none')?.textContent)
      .toContain('not been searched since receipts were added');
  });

  it('renders with no snapshot at all', () => {
    expect(render(BASE).querySelector('.vwsh-diag-rels')).toBeNull();
    expect(render({}).querySelectorAll('.vwsh-diag-line').length).toBeGreaterThan(0);
  });
});

describe('the drawer is reachable and self-contained', () => {
  it('hangs off the warning marker you already hover', () => {
    expect(JS).toContain('data-vwsh-why');
    expect(JS).toContain('vwsh-failing-btn');
    expect(extractFunction('onGridClick', JS)).toContain('data-vwsh-why');
  });

  it('reads the row identity off the row, not from a closure', () => {
    // Re-deriving it is how a drawer ends up describing the wrong episode.
    const body = extractFunction('onGridClick', JS);
    expect(body).toContain("host.getAttribute('data-tmdb')");
    expect(body).toContain("host.getAttribute('data-e')");
  });

  it('toggles closed on a second click', () => {
    expect(extractFunction('openDiagDrawer', JS)).toContain('panel.remove()');
  });

  it('styles everything it emits', () => {
    for (const cls of ['.vwsh-diag', '.vwsh-diag-line--gap', '.vwsh-diag-rel--ok',
                       '.vwsh-diag-rel--none', '.vwsh-diag-src']) {
      expect(CSS, `${cls} emitted but never styled`).toContain(cls);
    }
  });
});
