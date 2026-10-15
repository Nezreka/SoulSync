/**
 * the nebula must stay still while a wishlist run is going.
 *
 * reported as the whole page flickering and flashing faint grid lines. two
 * causes, both measured in chromium against ~90 orbs:
 *  - every orb ran paint-heavy infinite animations (box-shadow, filter, a
 *    blurred spin). the main thread pinned and the compositor showed tile seams.
 *  - a poll refetch reorders orbs, and moving a node restarts its css
 *    animations, so moved orbs replayed the entrance from opacity 0.
 */

import { act, cleanup, render } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { WishlistArtistGroup } from '../-wishlist.types';

import { WishlistOrb } from './wishlist-orb';

vi.mock('../../../features/downloads/inspector-modal', () => ({
  openWishlistInspector: vi.fn(),
}));

afterEach(cleanup);

const STATIC = join(__dirname, '..', '..', '..', '..', 'static');
const CSS =
  readFileSync(join(STATIC, 'style.css'), 'utf8') +
  '\n' +
  readFileSync(join(STATIC, 'wishlist-premier.css'), 'utf8');

function keyframes(name: string): string {
  const start = CSS.indexOf(`@keyframes ${name}`);
  if (start < 0) return '';
  // keyframe blocks nest one level: walk braces to the matching close
  let depth = 0;
  for (let i = CSS.indexOf('{', start); i < CSS.length; i++) {
    if (CSS[i] === '{') depth++;
    else if (CSS[i] === '}' && --depth === 0) return CSS.slice(start, i + 1);
  }
  return '';
}

/** every infinite animation on an orb, as [selector, keyframes name]. */
function infiniteOrbAnimations(): [string, string][] {
  const found: [string, string][] = [];
  const rule = /([^{}]*\.wl-orb[^{}]*)\{([^{}]*)\}/g;
  for (const match of CSS.matchAll(rule)) {
    const animation = /(?:^|;)\s*animation\s*:([^;]*)/.exec(match[2]);
    if (!animation || !animation[1].includes('infinite')) continue;
    for (const part of animation[1].split(',')) {
      const name = part.trim().split(/\s+/)[0];
      found.push([match[1].trim(), name]);
    }
  }
  return found;
}

/** jsdom has no AnimationEvent, so react listens for the webkit name there. */
function animationEnd(target: HTMLElement, animationName: string) {
  const event = Object.assign(new Event('webkitAnimationEnd', { bubbles: true }), {
    animationName,
  });
  act(() => {
    target.dispatchEvent(event);
  });
}

function group(over: Partial<WishlistArtistGroup> = {}): WishlistArtistGroup {
  return { name: 'Aphex Twin', albums: [], singles: [], total: 1, failingCount: 0, ...over };
}

function renderOrb() {
  return render(
    <WishlistOrb
      group={group()}
      index={3}
      artistImages={new Map()}
      currentCycle="albums"
      processing={false}
      expanded={false}
      onToggleExpand={() => {}}
      onRemoveAlbum={() => {}}
      onRemoveTrack={() => {}}
    />,
  );
}

describe('orb motion stays cheap', () => {
  it('finds the orb animations it guards', () => {
    // a parser that matches nothing would pass the checks below vacuously
    expect(infiniteOrbAnimations().length).toBeGreaterThan(0);
  });

  it('never loops an animation that repaints (box-shadow, filter)', () => {
    for (const [selector, name] of infiniteOrbAnimations()) {
      const body = keyframes(name);
      expect(body, `${selector} -> ${name}`).not.toBe('');
      expect(body, `${selector} -> ${name}`).not.toMatch(/box-shadow|filter/);
    }
  });

  it('keeps the processing state static', () => {
    const processing = /[^{}]*\.orb-processing[^{}]*\{([^{}]*)\}/g;
    const rules = [...CSS.matchAll(processing)];
    expect(rules.length).toBeGreaterThan(0);
    for (const match of rules) expect(match[1]).not.toMatch(/animation|filter|box-shadow/);
  });
});

describe('the entrance plays once', () => {
  it('drops the entrance once it has played, so a reorder cannot replay it', () => {
    const { container } = renderOrb();
    const orb = container.querySelector('.wl-orb-group') as HTMLElement;
    expect(orb.className).not.toContain('wl-orb-entered');
    expect(orb.style.animationDelay).toBe('180ms');

    animationEnd(orb, 'orbEntrance');

    expect(orb.className).toContain('wl-orb-entered');
    expect(orb.style.animationDelay).toBe('');
    expect(CSS).toMatch(/\.wl-orb-group\.wl-orb-entered\s*\{\s*animation:\s*none;?\s*\}/);
  });

  it('ignores animations that end on a child', () => {
    const { container } = renderOrb();
    const orb = container.querySelector('.wl-orb-group') as HTMLElement;
    const glow = container.querySelector('.wl-orb-glow') as HTMLElement;

    animationEnd(glow, 'orbEntrance');
    animationEnd(orb, 'orbPulse');

    expect(orb.className).not.toContain('wl-orb-entered');
  });
});
