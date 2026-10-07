import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { extractFunction } from './vanilla-extract';

/**
 * Specials are season 0 — a real, requestable season. The request button
 * used to treat season 0 as "no season given" (`!o.season`), so specials
 * rows rendered no request button at all for profiles without download
 * rights (and the backend 400'd the POST for the same reason). This pins
 * that season 0 renders a button while a genuinely missing season does not.
 */

const SRC = readFileSync(resolve(process.cwd(), 'static/video/video-requests.js'), 'utf8');

function cardButton(o: Record<string, unknown>): string {
  const preamble = `
    function esc(s) { return String(s == null ? '' : s); }
    window = {};
  `;
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  const build = new Function(
    `${preamble}\n${extractFunction('validRequestSeason', SRC)}\n${extractFunction('cardButton', SRC)}\nreturn cardButton;`,
  )() as (arg: unknown) => string;
  return build(o);
}

const EP = { kind: 'episode', tmdbId: 14209, title: 'Black Clover' };

describe('episode request button and the specials season', () => {
  it('renders for season 0 (specials)', () => {
    const html = cardButton({ ...EP, season: 0, episode: 2 });
    expect(html).toContain('data-vreq-card');
    expect(html).toContain('data-season="0"');
  });

  it('still renders for regular seasons', () => {
    expect(cardButton({ ...EP, season: 1, episode: 2 })).toContain('data-vreq-card');
  });

  it('renders nothing when the season is genuinely missing', () => {
    for (const season of [null, undefined, '']) {
      expect(
        cardButton({ ...EP, season, episode: 2 }),
        `season ${String(season)} should not render a button`,
      ).toBe('');
    }
  });

  it('renders nothing without an episode or title id', () => {
    expect(cardButton({ ...EP, season: 0 })).toBe('');
    expect(cardButton({ kind: 'episode', season: 0, episode: 2, title: 'x' })).toBe('');
  });
});
