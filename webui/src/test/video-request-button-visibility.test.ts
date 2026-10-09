import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * The video request button (`.vreq-card-btn`) is hover-revealed: it starts at
 * `opacity: 0` and fades in when its container is hovered.
 *
 * The detail page's episode rows (`.vd-ep` — YouTube video rows and TV
 * episode rows) render the button for profiles without download rights, but
 * were missing from the fade-in selector list. The button was in the DOM and
 * the row-hover revealed its container, yet the button itself stayed at
 * `opacity: 0` — so members never saw a request button on any YouTube video
 * or TV episode, while admins (whose grab buttons carry no such rule) were
 * unaffected. This pins every container the button can land in.
 *
 * jsdom does not lay out, so this pins the DECLARATIONS that have to agree.
 */

const CSS = readFileSync(resolve(process.cwd(), 'static/video/video-side.css'), 'utf8');

// Every surface VideoRequests.cardButton() renders into. Cards come from the
// paintCards ribbon selector in video-requests.js; `.vd-ep` rows come from
// ytEpisodeRow/episodeRow in video-detail.js.
const BUTTON_CONTAINERS = ['.vsr-card', '.vd-sim-card', '.vwlp-card', '.vd-ep'];

describe('video request button visibility', () => {
  it('starts hidden until its container is hovered (the hover-reveal design)', () => {
    const at = CSS.indexOf('\n.vreq-card-btn {');
    expect(at, '.vreq-card-btn has no own rule').toBeGreaterThan(-1);
    const body = CSS.slice(at, CSS.indexOf('}', at) + 1);
    expect(body).toContain('opacity: 0');
  });

  it('fades in for every container the button renders into', () => {
    for (const container of BUTTON_CONTAINERS) {
      expect(
        CSS.includes(`${container}:hover .vreq-card-btn`),
        `${container}:hover never reveals .vreq-card-btn — the button stays invisible there`,
      ).toBe(true);
    }
  });

  it('mirrors the episode-row container states (keyboard focus, expanded row)', () => {
    // .vd-ep-get (the button's wrapper) becomes visible on hover,
    // focus-within, and open rows — the button must follow it in all three,
    // or keyboard users and expanded rows lose it again.
    for (const state of [':hover', ':focus-within', '']) {
      const selector = state ? `.vd-ep${state} .vreq-card-btn` : '.vd-ep--open .vreq-card-btn';
      expect(
        CSS.includes(selector),
        `${selector} missing — the request button would stay hidden in that row state`,
      ).toBe(true);
    }
  });
});
