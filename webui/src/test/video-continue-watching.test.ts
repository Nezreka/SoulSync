import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { extractFunction } from './vanilla-extract';

/**
 * Continue watching — the resume rail on the video dashboard.
 *
 * The video dashboard was entirely about OWNING: Recently Added, Library,
 * Upcoming, System Stats, Quick Actions. The music dashboard tells a story that
 * ends in PLAYING; this is the video half of that, and the data for it has been
 * sitting in the schema unread since the scanner shipped (view_offset_ms is
 * literally commented "resume position (Continue Watching)").
 *
 * The judgements this pins:
 *
 * * The card answers "how much is LEFT", not "how far in you are". Elapsed time
 *   needs the runtime to mean anything; remaining time is already the answer.
 * * An up-next card has no progress, so it says what it is rather than drawing a
 *   0% bar, which reads as stalled.
 * * Nothing to resume means the band disappears. An empty rail headed "Continue
 *   watching" is worse than no rail.
 */

const JS = readFileSync(
  resolve(process.cwd(), 'static/video/video-dashboard.js'),
  'utf8',
);
const CSS = readFileSync(resolve(process.cwd(), 'static/video/video-side.css'), 'utf8');
const HTML = readFileSync(resolve(process.cwd(), 'index.html'), 'utf8');

function fn(name: string) {
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  return new Function(`${extractFunction(name, JS)}; return ${name};`)();
}

const remaining = fn('_remaining') as (it: Record<string, unknown>) => string;
const pct = fn('_pct') as (it: Record<string, unknown>) => number;

describe('the number on the card', () => {
  it('says how much is LEFT, not how far in you are', () => {
    // "47 minutes in" needs the runtime to mean anything. "23 min left" is the
    // answer already.
    expect(remaining({ runtime_minutes: 100, view_offset_ms: 77 * 60000 })).toBe('23 min left');
  });

  it('reads in hours once there is more than an hour to go', () => {
    expect(remaining({ runtime_minutes: 180, view_offset_ms: 8 * 60000 })).toBe('2h 52m left');
    expect(remaining({ runtime_minutes: 120, view_offset_ms: 0 })).toBe('2h left');
  });

  it('says nothing rather than guessing when the runtime is unknown', () => {
    // A thin scan leaves runtime null; inventing "0 min left" would be a lie.
    expect(remaining({ view_offset_ms: 10 * 60000 })).toBe('');
    expect(remaining({ runtime_minutes: 0, view_offset_ms: 10 * 60000 })).toBe('');
  });

  it('never shows a negative or zero remainder', () => {
    expect(remaining({ runtime_minutes: 100, view_offset_ms: 200 * 60000 })).toBe('');
  });
});

describe('the progress bar', () => {
  it('tracks how far through the title you are', () => {
    expect(pct({ runtime_minutes: 100, view_offset_ms: 50 * 60000 })).toBe(50);
  });

  it('shows a sliver rather than nothing for a barely-started title', () => {
    // A 0-width bar on something you HAVE started reads as "not started".
    expect(pct({ runtime_minutes: 200, view_offset_ms: 30_000 })).toBe(1);
  });

  it('is absent, not zero, when there is no progress to show', () => {
    // An up-next card draws no bar at all; a 0% bar reads as stalled.
    expect(pct({ runtime_minutes: 100, view_offset_ms: 0 })).toBe(0);
    expect(pct({ view_offset_ms: 5000 })).toBe(0);
  });

  it('cannot overflow its track', () => {
    expect(pct({ runtime_minutes: 100, view_offset_ms: 500 * 60000 })).toBe(100);
  });
});

describe('the rail', () => {
  it('disappears when there is nothing to resume', () => {
    expect(JS).toContain('section.hidden = items.length === 0');
    expect(HTML).toContain('data-video-continue-section hidden');
  });

  it('hides itself on a failed load rather than showing a broken band', () => {
    const load = extractFunction('loadContinueWatching', JS);
    expect(load).toContain('.catch(function () { section.hidden = true; })');
  });

  it('loads before the rest of the page, because it answers the first question', () => {
    const shown = extractFunction('onPageShown', JS);
    expect(shown.indexOf('loadContinueWatching')).toBeLessThan(shown.indexOf('loadUpcoming'));
  });

  it('is a real link, so middle-click and new-tab work', () => {
    // It was a <button> calling window.openVideoDetail, which does not exist -
    // so clicking a card did nothing at all. Every other video surface links
    // with a plain href, and a button swallows middle-click, ctrl-click and
    // "open in new tab".
    expect(JS).toContain("'<a class=\"vcw-card\" href=\"' + href + '\" '");
    expect(JS).toContain("'/video-detail/library/'");
    expect(JS).not.toContain('window.openVideoDetail');
  });

  it('sends a show to the show page and a movie to the movie page', () => {
    const card = extractFunction('_continueCard', JS);
    expect(card).toContain("it.kind === 'show' ? 'show/' + it.show_id");
    expect(card).toContain("'movie/' + it.id");
  });
});

describe('the card layout', () => {
  it('is landscape, because that is how a resume row is read', () => {
    // A portrait poster is how you BROWSE; the still of the scene you stopped
    // on is what you recognise.
    const art = CSS.slice(CSS.indexOf('.vcw-art {'), CSS.indexOf('.vcw-card:hover .vcw-art'));
    expect(art).toContain('aspect-ratio: 16 / 9');
  });

  it('lets a long title ellipsise instead of widening the card', () => {
    // A flex item defaults to min-width:auto and refuses to shrink below its
    // nowrap min-content width - a long name widened the card from 264px to
    // 319px and no ellipsis ever appeared. Measured in Chrome; jsdom cannot
    // see this at all, which is why it is pinned in the stylesheet.
    const card = CSS.slice(CSS.indexOf('.vcw-card {'), CSS.indexOf('.vcw-art {'));
    expect(card).toContain('min-width: 0');
    expect(card).toContain('max-width: 264px');
  });

  it('does not promise playback it cannot deliver', () => {
    // SoulSync does not play video - the media server does - so a play triangle
    // on this card is a lie. It opens the title, and the chevron says so.
    const card = extractFunction('_continueCard', JS);
    expect(card).toContain('vcw-open');
    expect(card).not.toContain('vcw-play');
    expect(card).not.toContain('&#9654;');
  });

  it('keeps the open affordance out of the way until hover', () => {
    const open = CSS.slice(CSS.indexOf('.vcw-open {'), CSS.indexOf('.vcw-badge'));
    expect(open).toContain('opacity: 0');
    expect(CSS).toContain('.vcw-card:hover .vcw-open');
  });

  it('respects a reduced-motion preference', () => {
    const rm = CSS.slice(CSS.indexOf('@media (prefers-reduced-motion: reduce)'));
    expect(rm).toContain('.vcw-card:hover .vcw-art { transform: none; }');
    expect(rm).toContain('.vcw-open');
  });
});
