import { describe, expect, it } from 'vitest';

import {
  analyseRules,
  isLive,
  refsFromTexts,
  removedSelectors,
  selectorClasses,
} from '../../scripts/css-class-refs.mjs';

/**
 * scripts/css-class-refs.mjs decides which legacy CSS is safe to delete. A
 * false "dead" deletes a rule the app still uses, so these pin the ways the
 * vanilla JS and the Python build class names from strings.
 */

const live = (cls: string, ...sources: string[]) => isLive(cls, refsFromTexts(sources));

describe('css-class-refs: tokens', () => {
  it('a class named anywhere in the code is live', () => {
    expect(live('card-title', `el.innerHTML = '<div class="card-title">'`)).toBe(true);
    expect(live('badge-ok', `const MAP = { ok: 'badge-ok' };`)).toBe(true);
  });

  it('a class nothing names is dead', () => {
    expect(live('card-subtitle', `el.innerHTML = '<div class="card-title">'`)).toBe(false);
  });
});

describe('css-class-refs: concatenated literals', () => {
  it("'foo-' + x keeps every foo-* class live", () => {
    expect(live('state-active', `el.className = 'state-' + s;`)).toBe(true);
    expect(live('row_open', `html += '<tr class="row_' + st + '">';`)).toBe(true);
  });

  it("a constant like const P = 'foo-' counts as a prefix", () => {
    expect(live('enh-card', `const P = 'enh-';\nel.className = P + kind;`)).toBe(true);
  });

  it("'foo' + '-' + x counts as the prefix foo-", () => {
    expect(live('tier-gold', `cls = 'tier' + '-' + t;`)).toBe(true);
  });

  it("x + '-active' keeps <token>-active live", () => {
    expect(live('tab-active', `const base = 'tab'; el.className = base + '-active';`)).toBe(true);
    expect(live('pill_on', `el.className = name + '_on'; const n = 'pill';`)).toBe(true);
  });

  it('a suffix needs a known stem', () => {
    expect(live('ghost-active', `el.className = base + '-active';`)).toBe(false);
  });

  it("a bare 'foo-' in a matcher array is not a prefix", () => {
    const hints = `const hints = { downloads: ['enh-', 'search-mode'] };`;
    expect(live('enh-card', hints)).toBe(false);
    expect(live('enh-card', `const hints = ['x', 'enh-'];`)).toBe(false);
    expect(live('enh-card', `const hints = ['enh-' + v];`)).toBe(true);
  });

  it("['foo', x].join('-') and Python's '-'.join(['foo', x]) count as foo-", () => {
    expect(live('chip-big', `el.className = ['chip', size].join('-');`)).toBe(true);
    expect(live('chip-big', `cls = '-'.join(['chip', size])`)).toBe(true);
  });
});

describe('css-class-refs: template literals', () => {
  it('`foo-${x}` keeps every foo-* class live', () => {
    expect(live('status-failed', 'el.className = `status-${s}`;')).toBe(true);
  });

  it('`${x}-bar` keeps <token>-bar live', () => {
    expect(
      live('enh-source-icon', 'const p = \'enh\';\nhtml = `<i class="${p}-source-icon">`;'),
    ).toBe(true);
  });

  it('`foo-${a}-bar` covers both ends', () => {
    const src = 'const kind = "pill"; html = `<b class="badge-${kind}-lg">`;';
    expect(live('badge-anything', src)).toBe(true);
    expect(live('pill-lg', src)).toBe(true);
  });
});

describe('css-class-refs: classList and className with computed parts', () => {
  it('classList.add/toggle/remove/replace treat glued literals as prefixes', () => {
    expect(live('isOpen', `el.classList.add('is' + state);`)).toBe(true);
    expect(live('modeDark', 'el.classList.toggle(`mode${theme}`, on);')).toBe(true);
    expect(live('navItem', `el.classList.remove('nav' + part);`)).toBe(true);
    expect(live('lvlHigh', `el.classList.replace(old, 'lvl' + v);`)).toBe(true);
  });

  it('className = and className={…} treat glued literals as prefixes', () => {
    expect(live('cardWide', `el.className = 'card' + size;`)).toBe(true);
    expect(live('cardWide', 'const C = () => <div className={`card${size}`} />;')).toBe(true);
  });

  it('a glued suffix counts when its stem is known', () => {
    expect(live('cardWide', `const k = 'card';\nel.className = k + 'Wide';`)).toBe(true);
  });

  it('outside a class context, a literal without - or _ is not a prefix', () => {
    expect(live('isOpen', `log('is' + state);`)).toBe(false);
  });

  it("a static className stops at its quote, so key={`e${i}`} isn't a prefix", () => {
    const jsx = 'const R = () => <div className="row error" key={`e${i}`} />;';
    expect(live('error', jsx)).toBe(true);
    expect(live('everything', jsx)).toBe(false);
  });

  it('a class="…" attribute ends at its own quote', () => {
    const src = `var se = '<span class="vup-se">S' + ep.season + ' E' + ep.number;`;
    expect(live('Sidebar', src)).toBe(false);
  });

  it('a computed className whose values are literals elsewhere stays live', () => {
    expect(live('done', `const CLS = { ok: 'done' };\nel.className = CLS[status];`)).toBe(true);
  });
});

describe('css-class-refs: [class*=] and friends in JS selectors', () => {
  it('querySelector [class*=x] keeps classes containing x live', () => {
    expect(live('lastfm-button-container', `q('[class*="-button-container"]')`)).toBe(true);
  });

  it('[class^=x], [class$=x] and [class|=x] match by start and end', () => {
    expect(live('orb-lastfm', `q("[class^='orb-']")`)).toBe(true);
    expect(live('lastfm-orb', `q('[class$="-orb"]')`)).toBe(true);
    expect(live('lang-en', `q('[class|="lang"]')`)).toBe(true);
    expect(live('other-orb', `q('[class^="orb-"]')`)).toBe(false);
  });

  it("Python's selectors scrape other sites, so they don't count", () => {
    const refs = refsFromTexts([
      { path: 'core/scraper.py', text: `soup.select('[class*="title"]')` },
    ]);
    expect(isLive('card-title-big', refs)).toBe(false);
  });
});

describe('css-class-refs: Python that emits class names', () => {
  it('f-strings, % and .format fragments count as prefixes and suffixes', () => {
    expect(live('badge-ok', `html = f'<span class="badge-{kind}">'`)).toBe(true);
    expect(live('tier-gold', `html = '<b class="tier-%s">' % tier`)).toBe(true);
    expect(live('tier-gold', `html = '<b class="tier-{}">'.format(tier)`)).toBe(true);
    expect(live('gold-tier', `kind = 'gold'\nhtml = f'<b class="{kind}-tier">'`)).toBe(true);
  });

  it('a class name in a Python literal is a token', () => {
    expect(live('copy-btn', `return '<button class="copy-btn">Copy</button>'`)).toBe(true);
  });
});

describe('css-class-refs: selectors', () => {
  it('classes inside :not() and :is() are not required', () => {
    expect(selectorClasses('button:not(.a):not(.b) .c')).toEqual(['c']);
    expect(selectorClasses(':is(.a, .b) .c')).toEqual(['c']);
    expect(selectorClasses('a[href$=".css"].d')).toEqual(['d']);
  });

  it('a rule is dead only when every selector needs a dead class', () => {
    const refs = refsFromTexts([`el.className = 'used';`]);
    const rules = analyseRules(
      `.used { color: red }\n.gone { color: red }\n.used, .gone { color: blue }\nbutton:not(.gone) { min-height: 38px }`,
      refs,
    );
    expect(rules.map((r) => [r.selector, r.dead, r.partlyDead])).toEqual([
      ['.used', false, false],
      ['.gone', true, false],
      ['.used, .gone', false, true],
      ['button:not(.gone)', false, false],
    ]);
  });

  it('removedSelectors flags a removed selector that can still match', () => {
    const refs = refsFromTexts([`el.className = 'used';`]);
    const before = [
      {
        name: 'a.css',
        text: '.used { color: red } .gone { color: red } @media (max-width: 9px) { .used { color: blue } }',
      },
    ];
    const after = [{ name: 'a.css', text: '.used { color: red }' }];
    expect(removedSelectors(before, after, refs)).toEqual({
      gone: ['.gone'],
      live: [],
    });
    expect(removedSelectors(before, [{ name: 'a.css', text: '' }], refs).live).toEqual([
      '.used',
      '@media (max-width: 9px) | .used',
    ]);
  });
});
