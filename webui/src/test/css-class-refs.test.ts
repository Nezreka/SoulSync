import { describe, expect, it } from 'vitest';

import {
  analyseRules,
  atRuleKey,
  isLive,
  refsFromTexts,
  removedSelectors,
  selectorClasses,
  selectorKey,
  stripComments,
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

describe('css-class-refs: comments', () => {
  const liveIn = (cls: string, path: string, text: string) =>
    isLive(cls, refsFromTexts([{ path, text }]));

  it("a class or prefix named only in a JS comment is dead (helper.js's // 'dashboard-')", () => {
    expect(liveIn('dashboard-action-x', 'static/helper.js', `// 'dashboard-'\nfoo();`)).toBe(false);
    expect(liveIn('old-card', 'static/a.js', `/* el.className = 'old-card'; */ run();`)).toBe(
      false,
    );
    expect(
      liveIn('old-card', 'src/a.tsx', `return <div>{/* <i className="old-card" /> */}</div>;`),
    ).toBe(false);
    expect(liveIn('old-card', 'static/a.js', `el.className = 'old-card'; // kept`)).toBe(true);
  });

  it("'//' and '/*' inside strings, template literals, URLs and regexes are not comments", () => {
    expect(
      liveIn('cdn-x', 'static/a.js', `const u = 'https://x.io/'; el.className = 'cdn-x';`),
    ).toBe(true);
    expect(liveIn('live-a', 'static/a.js', `s = "a // b"; el.className = 'live-a';`)).toBe(true);
    expect(liveIn('live-b', 'static/a.js', `s = \`/* \${'live-b'} */\`;`)).toBe(true);
    expect(liveIn('live-c', 'static/a.js', `if (/\\/\\//.test(p)) el.className = 'live-c';`)).toBe(
      true,
    );
    expect(liveIn('live-d', 'static/a.js', `x = p.split(/[/]/); el.className = 'live-d';`)).toBe(
      true,
    );
    expect(liveIn('live-e', 'src/a.tsx', `<a>see https://x.io</a>; <b className="live-e" />`)).toBe(
      true,
    );
  });

  it('a slash after a value divides, so what follows is not mistaken for a regex', () => {
    const js = `const w = a / 2; // 'gone-'\nel.className = 'kept';`;
    expect(stripComments('a.js', js)).toBe(`const w = a / 2;  \nel.className = 'kept';`);
  });

  it("Python's # comments are dropped but '#' in strings stays", () => {
    expect(liveIn('old-row', 'api/x.py', `# html = '<tr class="old-row">'\npass`)).toBe(false);
    expect(liveIn('hash-row', 'api/x.py', `c = '#fff'; h = '<tr class="hash-row">'`)).toBe(true);
    expect(liveIn('doc-row', 'api/x.py', `"""\n# not a comment: doc-row\n"""`)).toBe(true);
  });

  it('HTML comments are dropped, and JS comments inside <script>', () => {
    expect(liveIn('old-pane', 'index.html', `<!-- <div class="old-pane"></div> -->`)).toBe(false);
    expect(liveIn('old-pane', 'index.html', `<script>// x.className = 'old-pane'\n</script>`)).toBe(
      false,
    );
    expect(liveIn('new-pane', 'index.html', `<!-- note --><div class="new-pane"></div>`)).toBe(
      true,
    );
  });

  it('comments become blanks that keep every newline, so line counts stay', () => {
    const js = `a();\n/* one\ntwo */\nb(); // c\n`;
    expect(stripComments('a.js', js).split('\n')).toHaveLength(js.split('\n').length);
  });

  it('sources without a known path are scanned as written', () => {
    expect(live('kept-card', `// 'kept-card'`)).toBe(true);
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

  it('layout-only changes are not removals: @media and selectors are keyed after normalising', () => {
    const refs = refsFromTexts([`el.className = 'used';`]);
    const minified = [
      {
        name: 'a.css',
        text: '@media (max-width:900px){.used>.used,.used .used{flex:1}}@media (min-resolution:1.5dppx) and (max-width:.5px){.used{color:red}}',
      },
    ];
    const reflowed = [
      {
        name: 'a.css',
        text: `@media ( max-width: 900px ) {
    .used > .used,
    .used  .used {
        flex: 1;
    }
}

@media (min-resolution: 1.5dppx) and (max-width: 0.5px) {
    .used {
        color: red;
    }
}`,
      },
    ];
    expect(removedSelectors(minified, reflowed, refs)).toEqual({
      gone: [],
      live: [],
    });
    expect(removedSelectors(reflowed, minified, refs)).toEqual({
      gone: [],
      live: [],
    });
    // A real change of context still counts.
    const narrower = [
      {
        name: 'a.css',
        text: '@media (max-width: 899px) { .used > .used { flex: 1 } }',
      },
    ];
    expect(removedSelectors(minified, narrower, refs).live).toContain(
      '@media (max-width: 900px) | .used > .used',
    );
  });

  it('keys keep what layout cannot change: descendant spaces, quotes and [attr] text', () => {
    expect(atRuleKey('MEDIA', '(max-width:.5px)')).toBe('@media (max-width: 0.5px)');
    expect(selectorKey('.a>.b+.c~.d')).toBe('.a > .b + .c ~ .d');
    expect(selectorKey(':is( .a ,.b )')).toBe(':is(.a, .b)');
    expect(selectorKey('.a :hover')).not.toBe(selectorKey('.a:hover'));
    expect(selectorKey('[class~="x>y"]')).toBe('[class~="x>y"]');
    expect(selectorKey('.a[data-x="a  b"]')).toBe('.a[data-x="a  b"]');
  });
});
