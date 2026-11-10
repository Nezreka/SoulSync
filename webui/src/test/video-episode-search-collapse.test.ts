/**
 * SeadogsBooty (#2040): collapsing an expanded episode left a blank strip
 * under the row, with a coloured vertical bar that marks expanded content.
 * The episode search panel closes on grid rows (0fr), but its grid child was
 * .vdl-ep-srcs itself, whose vertical padding (3px top + 12px bottom) floors
 * the 0fr track: measured in chromium, a closed panel stood 15px tall with
 * the top 12px of the first source row (its top border + brand bar, purple
 * for the reporter's usenet source) showing; with the wrapper below it
 * stands 0px, and the open panel is unchanged. The season body avoids this
 * with one unpadded inner wrapper; the episode panel now uses the same
 * shape. jsdom can't lay out, so this pins the structure.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const read = (p: string) => readFileSync(resolve(process.cwd(), p), 'utf8');
// comments stripped: a rule's captured selector text otherwise drags the
// comment block above it along, and exact selector matches silently miss
const CSS = read('static/video/video-side.css').replace(/\/\*[\s\S]*?\*\//g, '');
const JS = read('static/video/video-download-view.js');

describe('a collapsed episode search panel', () => {
  it('closes on grid rows through one unpadded inner wrapper', () => {
    expect(CSS).toMatch(/\.vdl-ep-search \{[^}]*grid-template-rows: 0fr;/);
    expect(CSS).toMatch(/\.vdl-ep--open \.vdl-ep-search \{ grid-template-rows: 1fr; \}/);
    expect(CSS).toMatch(/\.vdl-ep-inner \{ min-height: 0; \}/);
  });

  it('floors the closed track with nothing on the wrapper or the panel', () => {
    // vertical padding, margin or border on the grid child floors the 0fr
    // track exactly like the srcs padding did, in any selector form — so
    // check every rule whose target (the last compound of a selector part)
    // is the wrapper or the panel, grouped or prefixed included, plus the
    // panel's child-combinator rule. The panel's own padding would leave
    // the same strip. [^{}]* bodies keep rules nested in @media blocks
    // visible instead of swallowing them into the @media match.
    // border-radius is not a floor and is allowed through; descendant
    // rules (the target is something inside the wrapper) are not floors
    // either and are skipped.
    // a compound targets the class only if the class name ends there —
    // .vdl-ep-search-foo is a different class, not this panel
    const compoundTargets = (compound: string, cls: string) => {
      const i = compound.indexOf(cls);
      if (i < 0) return false;
      const next = compound[i + cls.length];
      return next === undefined || ':.#['.includes(next);
    };
    const targetsWrapperOrPanel = (selector: string) =>
      selector.split(',').some((part) => {
        const p = part.trim();
        const norm = p.replace(/\s*([>+~])\s*/g, '$1');
        // child-combinator forms that land on the wrapper: > *, > div,
        // > :first-child, > .vdl-ep-inner (any spacing, any prefix)
        if (/\.vdl-ep-search>(?:\*|div|:first-child|\.vdl-ep-inner(?![\w-]))/.test(norm)) {
          return true;
        }
        const compounds = p.split(/[\s>+~]+/);
        const last = compounds[compounds.length - 1];
        return (
          compoundTargets(last, '.vdl-ep-inner') ||
          compoundTargets(last, '.vdl-ep-search') ||
          last.includes('[data-vdl-ep-search]')
        );
      });
    const blocks = [...CSS.matchAll(/([^{}]+)\{([^{}]*)\}/g)].filter(([, selector]) =>
      targetsWrapperOrPanel(selector),
    );
    expect(blocks.length).toBeGreaterThan(0);
    // a declaration floors the track only if a vertical component of its
    // value is not zero/none — padding: 0, margin: 0 14px (horizontal only),
    // border: 0 and border-top: none are harmless
    const isZeroToken = (t: string) => t === 'none' || /^0(?:\.0+)?(?:px|rem|em|%)?$/.test(t);
    const floorsTrack = (prop: string, v: string) => {
      const tokens = v
        .replace(/!important\s*$/, '')
        .trim()
        .split(/\s+/);
      // padding/margin shorthands: the vertical parts are the 1st token
      // (and the 3rd when present); horizontal values cannot floor a row
      if (prop === 'padding' || prop === 'margin') {
        return !isZeroToken(tokens[0]) || (tokens.length >= 3 && !isZeroToken(tokens[2]));
      }
      return !tokens.every(isZeroToken);
    };
    for (const [, , body] of blocks) {
      // any floor property that appears at all must not floor the track
      for (const [, prop, value] of body.matchAll(
        /(?:^|;)\s*((?:padding|margin|border)(?:-(?:top|bottom|block|block-start|block-end|width|style)){0,2}|height|min-height)\s*:\s*([^;]+)/g,
      )) {
        expect(floorsTrack(prop, value)).toBe(false);
      }
    }
  });

  it('keeps the source list padding one level down, inside the wrapper', () => {
    // the open panel's spacing is unchanged — the padding moved off the
    // grid child, it was not deleted
    expect(CSS).toMatch(/\.vdl-ep-srcs \{[^}]*padding: 3px 14px 12px 42px;/);
  });

  it('wraps every build of the panel content in the inner wrapper', () => {
    expect(JS).toContain(
      `'<div class="vdl-ep-inner"><div class="vdl-ep-srcs"><div class="vdl-src-empty">No source configured.</div></div></div>'`,
    );
    expect(JS).toContain(`'<div class="vdl-ep-inner"><div class="vdl-ep-srcs">' + srcs.map(`);
    // and never emits the padded list as the panel's direct child again
    // (single-quoted or template-literal assignment, as the file writes them)
    expect(JS).not.toMatch(/innerHTML\s*=\s*['`]<div class="vdl-ep-srcs">/);
  });
});
