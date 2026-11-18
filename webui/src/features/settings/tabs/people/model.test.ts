/** the People tab's words, worked out from the API's payloads */

import { describe, expect, it } from 'vitest';

import type { Catalogue, CatalogueRow } from './types';

import { FIXTURE } from './__fixtures__/people-msw';
import {
  aboveCeiling,
  countPeople,
  heroParts,
  impactLine,
  lookReasons,
  needsLine,
  personCells,
  rowById,
  valueText,
} from './model';

const catalogue = FIXTURE.catalogue as unknown as Catalogue;
const rows = rowById(catalogue);
const row = (id: string) => rows[id] as CatalogueRow;
const hero = (c: Parameters<typeof heroParts>[0], waiting: number | null) =>
  heroParts(c, waiting)
    .map((p) => p.text)
    .join('');

describe('the hero sentence', () => {
  it('counts people, templates, changes and waiting requests', () => {
    const c = { people: 7, members: 5, admins: 2, following: 5, changed: 2, look: 0, off: 1 };
    expect(hero(c, 3)).toBe(
      'Seven people use SoulSync here. Five follow a template and two have changes of their own. Three requests are waiting for you.',
    );
    expect(hero({ ...c, changed: 0 }, 0)).toBe(
      'Seven people use SoulSync here. Every member follows a template. No requests are waiting for you.',
    );
    expect(hero({ ...c, people: 1, members: 0, admins: 1, following: 0, changed: 0 }, null)).toBe(
      'Just you use SoulSync here so far.',
    );
    expect(hero({ ...c, following: 4, look: 1, changed: 1 }, 1)).toBe(
      'Seven people use SoulSync here. Four follow a template and one has changes of their own. One needs a look. One request is waiting for you.',
    );
  });
});

describe('the table cells', () => {
  it('reads modes, ratings and quality from the effective map', () => {
    const maya = FIXTURE.perms['3'] as unknown as { effective: Record<string, never> };
    const cells = personCells(maya.effective, FIXTURE.quality as never);
    expect(cells.music).toEqual({ tone: 'dl', text: 'Downloads', sub: 'Clean' });
    expect(cells.movies).toEqual({ tone: 'ask', text: 'Asks first', sub: 'PG-13' });
    expect(cells.tv).toEqual({ tone: 'ask', text: 'Asks first', sub: 'TV-14' });
    // her own change: a movie limit (tv has none, music downloads, never capped)
    expect(cells.requests).toEqual({ tone: 'plain', text: '5 movie asks', sub: 'a week' });
    const limited = personCells(
      { ...maya.effective, 'movies.quality': [0, 2], 'movies.access': true } as never,
      FIXTURE.quality as never,
    );
    expect(limited.movies.sub).toBe('Default, Movie 1080p Quality · PG-13');
    expect(personCells({ 'music.access': false } as never, null).music.text).toBe('Off');
  });

  it('says how many asks someone used once the server counted them', () => {
    const maya = FIXTURE.perms['3'] as unknown as { effective: Record<string, never> };
    // one limited kind: the count and whose asks
    const one = maya.effective as never;
    expect(personCells(one, FIXTURE.quality as never).requests).toEqual({
      tone: 'plain',
      text: '5 movie asks',
      sub: 'a week',
    });
    const used = personCells(one, FIXTURE.quality as never, { movies: { used: 2, limit: 5 } });
    expect(used.requests).toEqual({ tone: 'plain', text: '2 of 5 used', sub: 'movie asks a week' });
    // a count for an older limit (the limit changed since) shows just the limit
    const stale = personCells(one, FIXTURE.quality as never, { movies: { used: 2, limit: 3 } });
    expect(stale.requests.text).toBe('5 movie asks');
    // another kind's count never stands in for this one's
    expect(
      personCells(one, FIXTURE.quality as never, { tv: { used: 2, limit: 5 } }).requests.text,
    ).toBe('5 movie asks');
    // never more used than the limit
    expect(
      personCells(one, FIXTURE.quality as never, { movies: { used: 9, limit: 5 } }).requests.text,
    ).toBe('5 of 5 used');
    // two kinds: each with its own count
    const two = { ...maya.effective, 'tv.request_limit': { n: 5, days: 7 } } as never;
    expect(
      personCells(two, FIXTURE.quality as never, { movies: { used: 1, limit: 5 } }).requests,
    ).toEqual({ tone: 'plain', text: 'Movies 1/5 · TV 5', sub: 'asks a week' });
    // different windows say each
    const mixed = { ...maya.effective, 'tv.request_limit': { n: 2, days: 1 } } as never;
    expect(personCells(mixed, FIXTURE.quality as never).requests).toEqual({
      tone: 'plain',
      text: 'Movies 5 a week · TV 2 a day',
      sub: undefined,
    });
    // a kind that downloads right away is never capped
    const dl = { ...maya.effective, 'movies.get': 'download', 'tv.get': 'download' } as never;
    expect(personCells(dl, FIXTURE.quality as never).requests.text).toBe('No limit');
  });

  it('counts people the way the chips filter them', () => {
    const people = FIXTURE.profiles.profiles.map((p) => ({
      person: p as never,
      perms: (FIXTURE.perms[String(p.id)] as never) ?? null,
      failed: false,
    }));
    expect(countPeople(people)).toEqual({
      people: 8,
      members: 6,
      admins: 2,
      following: 5,
      changed: 3,
      look: 1,
      off: 1,
    });
  });
});

describe('the policy over a template', () => {
  it('knows when a value goes past the ceiling, per kind', () => {
    expect(aboveCeiling(row('movies.get'), 'download', 'ask')).toBe(true);
    expect(aboveCeiling(row('movies.get'), 'ask', 'ask')).toBe(false);
    expect(aboveCeiling(row('movies.max_rating'), 'R', 'PG-13')).toBe(true);
    expect(aboveCeiling(row('movies.max_rating'), 'any', 'any')).toBe(false);
    expect(aboveCeiling(row('tv.max_rating'), 'follow', 'PG')).toBe(false);
    expect(aboveCeiling(row('chat.use'), true, false)).toBe(true);
    expect(aboveCeiling(row('music.hide_explicit'), false, true)).toBe(true);
    expect(aboveCeiling(row('movies.request_limit'), { n: 0, days: 7 }, { n: 10, days: 7 })).toBe(
      true,
    );
    expect(aboveCeiling(row('movies.request_limit'), { n: 5, days: 7 }, { n: 10, days: 7 })).toBe(
      false,
    );
  });

  it('says what a row needs first', () => {
    expect(needsLine(row('movies.get'), { 'movies.access': false }, rows)).toBe(
      'Turn on Movies first.',
    );
    expect(
      needsLine(row('video.search'), { 'movies.access': false, 'tv.access': false }, rows),
    ).toBe('Turn on Movies or TV shows first.');
    expect(
      needsLine(row('video.search'), { 'movies.access': false, 'tv.access': true }, rows),
    ).toBeNull();
  });

  it('says a mode the same way in the policy and a template (#2192)', () => {
    expect(valueText(row('movies.get'), 'none', null, 'P')).toBe('Requests off');
    expect(valueText(row('movies.get'), 'none', null, 'T')).toBe('Requests off');
    expect(valueText(row('movies.request_limit'), { n: 3, days: 1 }, null)).toBe('3 asks a day');
    expect(valueText(row('tv.max_rating'), 'PG-13', null)).toBe('TV-14');
    expect(valueText(row('movies.quality'), [3], FIXTURE.quality as never)).toBe(
      'Movie 4K Quality',
    );
  });
});

describe('the impact line', () => {
  it('says who a policy reaches and what each loses', () => {
    const line = impactLine(FIXTURE.policy_dry_run.impact as never, catalogue, null, 'policy');
    expect(line.lead).toBe('Saving reaches all 5 members now.');
    expect(line.people[0]).toEqual({
      name: 'Priya',
      text: 'Movie requests goes from Auto-approve to Require approval, Movies rating goes from Any to R and 1 more',
    });
    expect(line.tail).toBe(
      "Odd runs on old settings. The policy holds them too, but those settings aren't rewritten.",
    );
  });

  it('says when nothing changes for anyone', () => {
    const line = impactLine(
      { reaches: 1, changes: [], not_following: [] },
      catalogue,
      null,
      'template',
    );
    expect(line.lead).toBe("Saving reaches one person now. Nobody's access changes.");
  });
});

describe('needs a look', () => {
  it("turns the migration's reasons into plain words", () => {
    expect(
      lookReasons([
        "max_rating 'NC-17' has no catalogue rating",
        'effective differs on cap, tv.max_rating',
      ]),
    ).toEqual(["A rating limit of NC-17, which SoulSync can't match to a rating here."]);
    expect(
      lookReasons([
        "page 'hydrabase' isn't in the catalogue",
        'request limit 5000 above 1000',
        'request window 400 days above 365',
        "allowed_pages isn't a list",
      ]),
    ).toEqual([
      "A page called Hydrabase that a template can't give.",
      'A request limit of 5000, more than a template can hold.',
      'A request window of 400 days, longer than a template can hold.',
      "A page list that couldn't be read.",
    ]);
    expect(lookReasons([])).toEqual([
      'Their old settings say something no template can say exactly.',
    ]);
  });
});
