import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { extractFunction } from './vanilla-extract';

/**
 * downloading now, the live band on the video dashboard.
 *
 * the music dashboard shows real in-flight transfer rows. video showed three
 * numbers in a stats card, so you could see THAT four things were downloading
 * but never what, how far along, or how long left.
 *
 * what this pins:
 *
 * * /downloads/active is a misleading name. it returns the last 100 rows of any
 *   status, completed ones included, so the band has to filter. video-detail.js
 *   already learned this the hard way.
 * * a queued or searching row draws no bar. a 0% bar reads as stalled, which is
 *   a different and worse thing to say.
 * * nothing in flight means the band disappears, same bargain the resume rail
 *   above it makes.
 */

const JS = readFileSync(resolve(process.cwd(), 'static/video/video-dashboard.js'), 'utf8');
const CSS = readFileSync(resolve(process.cwd(), 'static/video/video-side.css'), 'utf8');
const HTML = readFileSync(resolve(process.cwd(), 'index.html'), 'utf8');

function fn(name: string, deps = '') {
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  return new Function(`${deps}${extractFunction(name, JS)}; return ${name};`)();
}

const isActive = fn('_dlActive') as (s: string) => boolean;
const status = fn('_dlStatus') as (d: Record<string, unknown>) => string;
const pct = fn('_dlPct') as (d: Record<string, unknown>) => number;

describe('which rows belong in the band', () => {
  it('takes the four in-flight states', () => {
    for (const s of ['downloading', 'queued', 'searching', 'importing']) {
      expect(isActive(s), `${s} is in flight`).toBe(true);
    }
  });

  it('drops everything the endpoint hands back that is already over', () => {
    // the reason the band filters at all. /downloads/active returns history.
    for (const s of ['completed', 'failed', 'cancelled', 'import_failed', '']) {
      expect(isActive(s), `${s} is not in flight`).toBe(false);
    }
  });
});

describe('the status pill', () => {
  it('says the import phase when the importer gave one', () => {
    // "importing" twice over is noise; the phase is the new information
    expect(status({ status: 'importing', import_phase: 'Renaming' })).toBe('Renaming');
  });

  it('falls back to a plain word when there is no phase', () => {
    expect(status({ status: 'importing' })).toBe('Importing');
    expect(status({ status: 'searching' })).toBe('Searching');
    expect(status({ status: 'queued' })).toBe('Queued');
    expect(status({ status: 'downloading' })).toBe('Downloading');
  });
});

describe('the progress number', () => {
  it('reads a plain percent', () => {
    expect(pct({ progress: 47 })).toBe(47);
  });

  it('reads a 0-1 fraction too, because both are written to this column', () => {
    expect(pct({ progress: 0.47 })).toBe(47);
    expect(pct({ progress: 1 })).toBe(100);
  });

  it('never leaves the 0-100 range whatever the monitor writes', () => {
    expect(pct({ progress: 140 })).toBe(100);
    expect(pct({ progress: -5 })).toBe(0);
  });

  it('treats a missing or unparseable progress as zero, not NaN', () => {
    // NaN would reach a style="width:NaN%" and silently paint nothing
    expect(pct({})).toBe(0);
    expect(pct({ progress: null })).toBe(0);
    expect(pct({ progress: 'nope' })).toBe(0);
  });
});

describe('the poster', () => {
  const art = fn('_dlArt') as (d: Record<string, unknown>) => string;

  it('uses poster_url when the grab carried one', () => {
    expect(art({ poster_url: '/api/video/poster/show/2948' })).toBe('/api/video/poster/show/2948');
  });

  it('falls back to the library id, which is why half the band was art-less', () => {
    // poster_url is only whatever the grab caller happened to pass, and
    // wishlist-driven grabs routinely pass nothing. a library row can always
    // resolve its own art.
    expect(art({ media_source: 'library', media_id: '2948', kind: 'show' }))
      .toBe('/api/video/poster/show/2948?w=120');
  });

  it('has nothing to offer for a tmdb row that carried no poster', () => {
    // media_id is deliberately null for tmdb grabs, so there is no library row
    // to resolve. the letter is the honest answer.
    expect(art({ media_source: 'tmdb', media_id: null, kind: 'movie' })).toBe('');
    expect(art({})).toBe('');
  });

  it('encodes the id rather than pasting it into a url', () => {
    const src = extractFunction('_dlArt', JS);
    expect(src).toContain('encodeURIComponent');
  });
});

describe('the card', () => {
  const ROW = JS.slice(JS.indexOf('function _dlCard'), JS.indexOf('function loadActiveDownloads'));
  // the real function, so these assert on rendered html rather than on source
  // text. every dependency it closes over has to come with it.
  const deps = ['formatBytes', 'formatSpeed', '_esc', '_dlActive', '_dlStatus', '_dlPct', '_dlSub', '_dlArt']
    .map((n) => extractFunction(n, JS))
    .join('\n');
  const card = fn('_dlCard', `${deps}\n`) as (d: Record<string, unknown>) => string;

  it('draws a bar only for the states that have real progress', () => {
    expect(card({ title: 'A', status: 'downloading', progress: 40 })).toContain('vdn-bar');
    expect(card({ title: 'A', status: 'importing', progress: 40 })).toContain('vdn-bar');
    expect(card({ title: 'A', status: 'queued' })).not.toContain('vdn-bar');
    expect(card({ title: 'A', status: 'searching' })).not.toContain('vdn-bar');
  });

  it('escapes the title and the poster url', () => {
    const html = card({ title: '<img onerror=alert(1)>', status: 'queued',
                        poster_url: '"><script>x</script>' });
    expect(html).not.toContain('<img onerror=alert(1)>');
    expect(html).not.toContain('<script>');
    expect(html).toContain('&lt;img');
  });

  it('lets a poster that 404s uncover the letter instead of leaving a hole', () => {
    // a background-image cannot report a failure; an <img> can
    expect(ROW).toContain('<img');
    expect(ROW).toContain('onerror=');
    expect(ROW).toContain('vdn-letter');
  });

  it('renders the letter even when there IS a poster', () => {
    // it sits UNDERNEATH the image, so onerror can uncover it. checking the
    // string is in the source proves nothing: wrapping it in `art ? '' : ...`
    // keeps the string and breaks the behaviour, which is exactly what a
    // negative-check caught here.
    const html = card({ title: 'Severance', status: 'downloading', progress: 40,
                        poster_url: '/api/video/poster/show/9' });
    expect(html).toContain('vdn-letter');
    expect(html).toContain('<img');
  });

  it('still renders the letter when there is no poster at all', () => {
    const html = card({ title: 'Andor', status: 'queued' });
    expect(html).toContain('vdn-letter');
    expect(html).not.toContain('<img');
  });
});

describe('the loader', () => {
  const LOAD = JS.slice(
    JS.indexOf('function loadActiveDownloads'),
    JS.indexOf('function scheduleDownloadPoll'),
  );

  it('hides the whole band when nothing is in flight', () => {
    expect(LOAD).toContain('section.hidden = rows.length === 0');
  });

  it('filters the endpoint rather than trusting its name', () => {
    expect(LOAD).toContain('_dlActive(x.status)');
  });

  it('keeps the last good render when a poll blips', () => {
    // a transient failure is not news, and blanking the band would look like
    // the downloads stopped
    const cat = LOAD.slice(LOAD.indexOf('.catch('));
    expect(cat).not.toContain('innerHTML');
    expect(cat).not.toContain('section.hidden');
  });

  it('caps the rows and says how many it left out', () => {
    expect(LOAD).toContain('DL_MAX_ROWS');
    expect(LOAD).toContain('vdn-more');
  });
});

describe('the poll cadence', () => {
  const SCHED = JS.slice(
    JS.indexOf('function scheduleDownloadPoll'),
    JS.indexOf('// Attention badges'),
  );

  it('decides the interval from the fetch RESULT, not from the pre-fetch DOM', () => {
    // reading section.hidden before the fetch resolves always reads the old
    // state, so a fresh download would sit on the idle interval before its bar
    // first moved
    expect(JS).toContain('scheduleDownloadPoll(rows.length > 0)');
    expect(SCHED).toContain('busy ? DL_POLL_ACTIVE_MS : DL_POLL_IDLE_MS');
  });

  it('does not fetch while the dashboard is not the visible page', () => {
    expect(SCHED).toContain('dashboardVisible()');
  });

  it('clears the previous timer so revisiting the page cannot stack them', () => {
    expect(SCHED).toContain('if (dlPollTimer) clearTimeout(dlPollTimer)');
  });

  it('polls slower than the downloads page, which is the primary surface', () => {
    const active = Number(/DL_POLL_ACTIVE_MS = (\d+)/.exec(JS)?.[1]);
    const idle = Number(/DL_POLL_IDLE_MS = (\d+)/.exec(JS)?.[1]);
    expect(active).toBeGreaterThan(2000);
    expect(idle).toBeGreaterThan(active);
  });
});

describe('the markup and styles exist', () => {
  it.each(['data-video-dl-section', 'data-video-dl-list'])('%s is in index.html', (hook) => {
    expect(new RegExp(`${hook}(?=[\\s=>])`).test(HTML)).toBe(true);
  });

  it('starts hidden, so a fresh install never sees an empty band', () => {
    const at = HTML.indexOf('data-video-dl-section');
    expect(HTML.slice(at, at + 40)).toContain('hidden');
  });

  it('links out with data-video-goto, the hook that actually routes in-app', () => {
    // data-video-page is only wired for .video-nav buttons; a plain link
    // carrying it full-reloads the app
    const at = HTML.indexOf('class="vdn-all"');
    expect(HTML.slice(at, at + 200)).toContain('data-video-goto="video-downloads"');
  });

  it.each(['.vdn-card', '.vdn-art', '.vdn-name', '.vdn-bar-fill', '.vdn-pill', '.vdn-grid'])(
    '%s is styled',
    (cls) => {
      expect(CSS.includes(`${cls} `) || CSS.includes(`${cls},`) || CSS.includes(`${cls}--`)).toBe(true);
    },
  );

  it('lets the title shrink instead of pushing the row off the page', () => {
    // a flex item defaults to min-width:auto and refuses to shrink; the resume
    // cards hit this exact trap and rendered 319px wide instead of 264px
    const meta = CSS.slice(CSS.indexOf('.vdn-body {'), CSS.indexOf('.vdn-name {'));
    expect(meta).toContain('min-width: 0');
  });
});

describe('it does not squat in another component\'s namespace', () => {
  it('uses vdn-, because vdl- belongs to the download modal', () => {
    // video-download-view.js owns ~170 vdl- classes. sharing the prefix is how
    // two unrelated components end up sharing a rule by accident.
    const mine = JS.slice(JS.indexOf('function _dlArt'), JS.indexOf('function scheduleDownloadPoll'));
    expect(mine).not.toMatch(/vdl-/);
    const at = HTML.indexOf('data-video-dl-section');
    expect(HTML.slice(at - 900, at + 500)).not.toMatch(/class="vdl-/);
  });

  it('lays the cards out in a grid, not one full-width row each', () => {
    // two downloads across a full-width band is mostly empty page
    const grid = CSS.slice(CSS.indexOf('.vdn-grid {'), CSS.indexOf('.vdn-card {'));
    expect(grid).toContain('grid-template-columns');
    expect(grid).toContain('auto-fill');
  });
});
