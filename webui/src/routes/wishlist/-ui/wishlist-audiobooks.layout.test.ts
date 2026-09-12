import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * The audiobook wishlist is a wall of covers, like the video wishlist.
 *
 * It used to be a vertical list of rows with a 62px thumbnail on the left,
 * which reads as a spreadsheet: the artwork was decoration and the text carried
 * everything. The video wishlist does the opposite and is the model — a grid of
 * posters, art dominant, title and meta underneath, the card itself almost
 * invisible until pointed at (.vwsh-movie / .vwsh-movie-art, video-side.css).
 *
 * Read from the source because the failure mode is visual and structural: every
 * control still present, every class still styled, and the geometry matching
 * the page it is meant to resemble.
 */

const HERE = __dirname;
const TSX = readFileSync(join(HERE, 'wishlist-audiobooks.tsx'), 'utf8');
const CSS = readFileSync(join(HERE, 'wishlist-audiobooks.module.css'), 'utf8');
const VIDEO_CSS = readFileSync(
  join(HERE, '..', '..', '..', '..', 'static', 'video', 'video-side.css'),
  'utf8',
);

function rule(name: string, source = CSS): string {
  const match = new RegExp(`\\.${name}\\s*\\{([^}]*)\\}`).exec(source);
  return match ? match[1] : '';
}

describe('the cards are a poster grid', () => {
  it('renders a grid, not a stack of rows', () => {
    expect(TSX).toContain('styles.grid');
    expect(TSX).not.toContain('styles.rows');
    expect(rule('grid')).toContain('grid-template-columns');
  });

  it('gives the cover its own square tile', () => {
    // a 62px thumbnail was the whole complaint
    const art = rule('art');
    expect(art).toContain('aspect-ratio: 1');
    expect(art).toContain('overflow: hidden');
    expect(rule('cover')).toContain('object-fit: cover');
  });

  it('puts the text under the art rather than beside it', () => {
    const card = rule('card');
    expect(card).toContain('flex-direction: column');
  });

  it('matches the video wishlist geometry it is modelled on', () => {
    // same auto-fill grid and the same hover lift, so the two pages read as
    // one product rather than two
    expect(rule('grid')).toContain('auto-fill');
    expect(VIDEO_CSS).toContain('auto-fill');
    expect(rule('card:hover .art', CSS) || CSS).toContain('translateY(-3px)');
    expect(VIDEO_CSS).toContain('translateY(-3px)');
  });
});

describe('nothing was lost in the rebuild', () => {
  it('keeps every control the list had', () => {
    for (const label of ['Find releases', 'Remove']) {
      expect(TSX).toContain(label);
    }
    expect(TSX).toContain('changeNarratorMode');
  });

  it('still shows the status, now on the artwork', () => {
    expect(TSX).toContain('STATUS_LABELS[item.status]');
    expect(rule('badge')).toContain('position: absolute');
  });

  it('still shows the attempt trail and the last error', () => {
    expect(TSX).toContain('attempt_count');
    expect(TSX).toContain('last_error');
  });

  it('keeps the series line', () => {
    expect(TSX).toContain('series_title');
  });
});

describe('the controls stay usable', () => {
  it('reveals the actions on hover and on keyboard focus', () => {
    // focus-within matters: hover-only would hide them from the keyboard
    expect(CSS).toContain('.card:focus-within .actions');
  });

  it('shows them outright where there is no hover', () => {
    expect(CSS).toContain('@media (hover: none)');
  });

  it('gives the cover link a visible focus ring', () => {
    expect(rule('coverLink:focus-visible', CSS) || CSS).toContain('outline');
  });
});

describe('every class the markup uses is styled', () => {
  it('has no unstyled class', () => {
    const used = [...TSX.matchAll(/styles\.([A-Za-z]+)/g)].map((m) => m[1]);
    const defined = new Set([...CSS.matchAll(/^\.([A-Za-z]+)/gm)].map((m) => m[1]));
    const missing = [...new Set(used)].filter((name) => !defined.has(name));
    expect(missing, 'these render unstyled').toEqual([]);
  });
});
