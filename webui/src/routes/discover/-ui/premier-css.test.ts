import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * layout fixes jsdom can't see, pinned as text. each was measured in real
 * chromium (sept 29) and each looks like something to "simplify" away.
 */

const CSS = readFileSync(resolve(process.cwd(), 'static/discover-premier.css'), 'utf8').replace(
  /\r\n/g,
  '\n',
);

function rule(selectorStart: string, media?: string): string {
  const scope = media ? CSS.slice(CSS.indexOf(media)) : CSS;
  const at = scope.indexOf(`${selectorStart} {`);
  expect(at, `${selectorStart} not found`).toBeGreaterThanOrEqual(0);
  return scope.slice(at, scope.indexOf('}', at));
}

describe('discover-premier.css', () => {
  it('never pins the hero to a fixed height on a phone', () => {
    // 290px with overflow hidden clipped the title and piled the actions onto
    // the faces strip.
    const phone = rule('    .discover-hero', '@media (max-width: 768px)');
    expect(phone).toContain('height: auto !important');
    expect(CSS).not.toMatch(/\.discover-hero\s*\{[^}]*height:\s*290px/);
  });

  it('starts the faces strip at the left, so an overflowing row can scroll to its first face', () => {
    // style.css centres the indicators; centred overflow is unreachable.
    expect(rule('.discover-hero-indicators')).toContain('justify-content: flex-start !important');
  });

  it('keeps stacked headers on the left on a phone', () => {
    expect(
      rule('    .discovery-zone-head,\n    .discover-section-header', '@media (max-width: 768px)'),
    ).toContain('align-items: flex-start !important');
  });

  it('lets the dial size to its content', () => {
    // style.css's leftover min-height: 100% pushed the dial into the next shelf.
    expect(rule('.adv-wave,\n.discovery-zone .adv-wave')).toContain('min-height: auto !important');
  });

  it('fades the hero still out over its video at the video layer’s own pace', () => {
    // the still only fades under the video-live class, and at 1.4s: the same
    // duration as the video layer's own fade-in, so the two cross in step
    // instead of one finishing while the other is still half there.
    expect(rule('.discover-hero--video-live .discover-hero-background')).toContain('opacity: 0');
    expect(rule('.discover-hero-background')).toContain('opacity 1.4s ease');
    expect(rule('.dsc-backdrop-video')).toContain('transition: opacity 1.4s ease');
  });

  it('stops the still breathing while the video owns the hero, or the fade never runs', () => {
    // style.css breathes .discover-hero-background (opacity 1 to 0.75,
    // forever), and a running animation overrides the opacity declaration:
    // without animation: none the video-live rule above is dead css and
    // the still keeps breathing over the video instead of fading out.
    expect(rule('.discover-hero--video-live .discover-hero-background')).toContain(
      'animation: none',
    );
  });

  it('fades the still back in, still not breathing, while the preview closes', () => {
    // the exit mirrors the entrance: opacity back to 1 over the same 1.4s
    // transition, with the breathe animation still off — a breathing still
    // has no opacity left for the transition to carry.
    const closing = rule('.discover-hero--video-closing .discover-hero-background');
    expect(closing).toContain('opacity: 1');
    expect(closing).toContain('animation: none');
  });

  it('fades the hero’s video out while closing, in a rule that beats its full-strength rule', () => {
    expect(rule('.discover-hero--video-closing .dsc-backdrop-video.ready')).toContain('opacity: 0');
    // the selector is exactly that, opening its own rule at a line start:
    // a longer selector that merely ended with it would match the rule()
    // lookup above without being the rule the cascade argument is about.
    expect(CSS).toContain('\n.discover-hero--video-closing .dsc-backdrop-video.ready {\n');
    // equal specificity (0,3,0) with the hero's .ready rule above it, so
    // source order is the only thing that makes the closing rule win: it
    // has to stand after it in the file.
    const readyAt = CSS.indexOf('.discover-hero .dsc-backdrop-video.ready {');
    const closingAt = CSS.indexOf('.discover-hero--video-closing .dsc-backdrop-video.ready {');
    expect(readyAt).toBeGreaterThanOrEqual(0);
    expect(closingAt).toBeGreaterThan(readyAt);
    // and it is the last hero-scoped .ready rule in the file: a later one
    // at equal specificity would win the same way and silently undo the
    // fade. the count is scoped to rules that could match the hero's
    // video — the .dsc-promo .ready rule further down can never match
    // inside the hero, so it is not a rival and does not count.
    const after = CSS.slice(closingAt + '.discover-hero--video-closing'.length);
    expect(after).not.toMatch(/\.discover-hero[^{]*\.dsc-backdrop-video\.ready/);
  });

  it('stacks the hero’s video above its still and below its scrim and words', () => {
    // z-index auto (the still) < 0 (the video) < 1 (the scrim) < 2 (the
    // words): if the video ever climbs over the scrim the title drowns in
    // motion, and the hero's video plays at full strength, not the
    // banners' 0.9 — it replaces the still outright.
    expect(rule('.dsc-backdrop-video')).toContain('z-index: 0');
    expect(rule('.discover-hero-overlay')).toContain('z-index: 1');
    expect(rule('.discover-hero-content')).toContain('z-index: 2');
    expect(rule('.discover-hero .dsc-backdrop-video.ready')).toContain('opacity: 1');
  });

  it('parks the preview switch third in the hero’s frosted cluster', () => {
    // help sits at right: 20px and block at right: 58px (style.css); the
    // preview takes the next slot left rather than covering either.
    expect(CSS).toContain('.discover-blacklist-btn,\n.discover-hero-preview-btn {');
    expect(CSS).toMatch(
      /\.discover-hero-preview-btn \{\n    position: absolute;\n    top: 20px;\n    right: 96px;\n    z-index: 10;/,
    );
  });

  it('highlights the Deezer genre the component actually marks', () => {
    // the component sets .is-active; premier used to style .active, which
    // nothing sets, and its !important background beat style.css's is-active.
    expect(CSS).toContain('.dz-ed-genre.is-active {');
    expect(CSS).not.toContain('.dz-ed-genre.active {');
  });
});
