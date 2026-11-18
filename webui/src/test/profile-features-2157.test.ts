/**
 * #2157 (SeadogsBooty): an admin sets what a profile shows, from the
 * profile editor, and can lock it. These run the real vanilla functions out
 * of init.js: the switches follow the sides and pages in the form, a video
 * only profile never gets a switch for its only side, and the lock and the
 * reset land in the state the editor saves.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { HttpResponse, http, server } from '@/test/msw';

import { extractFunction } from './vanilla-extract';

const INIT = readFileSync(resolve(process.cwd(), 'static/init.js'), 'utf8');

const NAMES = [
  'pfEl',
  'pfSwitch',
  'pfReadJson',
  'profilePageSide',
  'pfFeaturesOffered',
  'pfFeaturesLine',
  'pfFeatureChooser',
  '_pfLoadProfileFeatures',
  'pfFeaturesBody',
  '_pfSidesAllow',
  'pfPagesSummary',
];

type Fst = {
  off: string[];
  locked: boolean;
  dirty: boolean;
  reset: boolean;
  offDirty?: boolean;
  lockDirty?: boolean;
};
type Chooser = { box: HTMLElement; render: () => void };
type Api = {
  pfFeaturesOffered: (f: unknown[], sides: string, pages: string[] | null) => string[];
  pfFeaturesLine: (p: unknown, off: string[], locked: boolean) => string;
  pfFeatureChooser: (p: unknown, fst: Fst, o?: object) => Chooser;
  pfFeaturesBody: (fst: Fst) => Record<string, unknown>;
  pfPagesSummary: (st: { sides: string; allowed_pages: string[] | null }) => string;
  _pfLoadProfileFeatures: (
    id: number,
    box: HTMLElement,
    st: { sides: string; allowed_pages: string[] | null },
    ready: (fst: Fst, chooser: Chooser) => void,
  ) => Promise<void>;
};

const api = new Function(
  `let _pfUid = 0;\nconst PROFILE_PAGE_GROUPS = [{ side: 'music', pages: ['dashboard', 'import', 'stats'] }, { side: 'video', pages: ['video-dashboard', 'video-tools'] }];\n${NAMES.map((n) => extractFunction(n, INIT)).join('\n')}\nreturn { ${NAMES.join(', ')} };`,
)() as Api;

const FEATURES = [
  { id: 'discover', label: 'Discover', desc: '', group: 'music', pages: ['discover'] },
  { id: 'music_videos', label: 'Music videos', desc: '', group: 'music', pages: [] },
  { id: 'video', label: 'Movies & TV', desc: '', group: 'more', pages: ['video-dashboard'] },
  { id: 'chat', label: 'Chat & Arcade', desc: '', group: 'more', pages: ['chat', 'video-chat'] },
  {
    id: 'video_discover',
    label: 'Video Discover',
    desc: '',
    group: 'video',
    pages: ['video-discover'],
  },
];
const PAYLOAD = {
  success: true,
  features: FEATURES,
  groups: [
    { id: 'music', label: 'Music' },
    { id: 'more', label: 'More than music' },
    { id: 'video', label: 'Movies & TV' },
  ],
  presets: [
    { id: 'full', label: 'Everything', desc: '', off: [] },
    {
      id: 'simple',
      label: 'Simple',
      desc: '',
      off: ['music_videos', 'video', 'chat', 'video_discover'],
    },
  ],
  off: ['chat'],
  locked: false,
  follows_default: false,
  default: { off: ['music_videos'], locked: true, set: true },
};

const switchTitles = (box: HTMLElement) =>
  [...box.querySelectorAll('.pf-switch-title')].map((el) => el.textContent);
const switchFor = (box: HTMLElement, title: string) =>
  [...box.querySelectorAll<HTMLLabelElement>('.pf-switch-row')]
    .find((row) => row.querySelector('.pf-switch-title')!.textContent === title)!
    .querySelector('input')!;

beforeEach(() => {
  document.body.innerHTML = '';
});
afterEach(() => server.resetHandlers());

describe('which switches an admin sees for a profile', () => {
  it('a video-only profile gets no switch for its only side', () => {
    const ids = api.pfFeaturesOffered(FEATURES, 'video', null);
    expect(ids).toEqual(['chat', 'video_discover']);
  });

  it('follows the sides and pages the form has', () => {
    expect(api.pfFeaturesOffered(FEATURES, 'music', null)).toEqual([
      'discover',
      'music_videos',
      'chat',
    ]);
    expect(api.pfFeaturesOffered(FEATURES, 'both', ['search', 'video-discover'])).toEqual([
      'music_videos',
      'video_discover',
    ]);
    expect(api.pfFeaturesOffered(FEATURES, 'both', null)).toEqual(FEATURES.map((f) => f.id));
  });

  it('names the setup in one line', () => {
    expect(api.pfFeaturesLine(PAYLOAD, [], false)).toBe('Everything');
    expect(api.pfFeaturesLine(PAYLOAD, PAYLOAD.presets[1].off, true)).toBe('Simple, locked');
    expect(api.pfFeaturesLine(PAYLOAD, ['chat'], false)).toBe('1 part hidden');
  });
});

describe('the chooser', () => {
  it('a switch, a preset and the lock all land in the saved state', () => {
    const fst: Fst = { off: [], locked: false, dirty: false, reset: true };
    const { box } = api.pfFeatureChooser(PAYLOAD, fst, { lockTitle: 'Lock it' });
    document.body.append(box);
    switchFor(box, 'Discover').click();
    // a switch is the set half; a reset from before stays in the save (#2164)
    expect(fst).toMatchObject({ off: ['discover'], dirty: true, offDirty: true, reset: true });
    box.querySelectorAll<HTMLButtonElement>('.pf-preset')[1].click();
    expect(fst.off).toEqual(PAYLOAD.presets[1].off);
    expect(box.querySelectorAll('.pf-preset')[1].getAttribute('aria-checked')).toBe('true');
    switchFor(box, 'Lock it').click();
    expect(fst.locked).toBe(true);
    expect(fst.lockDirty).toBe(true);
  });

  it("drops the video side's own switches while it's off", () => {
    const fst: Fst = { off: [], locked: false, dirty: false, reset: false };
    const { box } = api.pfFeatureChooser(PAYLOAD, fst);
    document.body.append(box);
    expect(switchTitles(box)).toContain('Video Discover');
    switchFor(box, 'Movies & TV').click();
    expect(switchTitles(box)).not.toContain('Video Discover');
  });
});

describe('the editor section', () => {
  it('loads the profile, resets to the default and says so', async () => {
    server.use(http.get('*/api/profiles/7/features', () => HttpResponse.json(PAYLOAD)));
    const box = document.createElement('div');
    document.body.append(box);
    let got: { fst: Fst; chooser: Chooser } | null = null;
    await api._pfLoadProfileFeatures(
      7,
      box,
      { sides: 'both', allowed_pages: null },
      (fst, chooser) => {
        got = { fst, chooser };
      },
    );
    expect(got).not.toBeNull();
    const { fst } = got!;
    expect(fst).toMatchObject({ off: ['chat'], locked: false, dirty: false });
    expect(switchFor(box, 'Chat & Arcade').checked).toBe(false);

    const reset = [...box.querySelectorAll('button')].find(
      (b) => b.textContent === 'Reset to default',
    )!;
    reset.click();
    expect(fst).toMatchObject({ off: ['music_videos'], locked: true, dirty: true, reset: true });
    expect(box.textContent).toContain('Follows what new profiles see.');
    expect(reset.disabled).toBe(true);
    expect(api.pfFeaturesBody(fst)).toEqual({ reset: true });
    // changing a switch after a reset is a set of its own again, on top of
    // the reset (the lock goes back to the default's too)
    switchFor(box, 'Discover').click();
    expect(box.textContent).not.toContain('Follows what new profiles see.');
    expect(reset.disabled).toBe(false);
    expect(api.pfFeaturesBody(fst)).toEqual({ reset: true, off: ['music_videos', 'discover'] });
  });

  it('a video-only profile in the form gets no Movies & TV switch', async () => {
    server.use(http.get('*/api/profiles/7/features', () => HttpResponse.json(PAYLOAD)));
    const box = document.createElement('div');
    await api._pfLoadProfileFeatures(7, box, { sides: 'video', allowed_pages: null }, () => {});
    expect(switchTitles(box)).not.toContain('Movies & TV');
    expect(switchTitles(box)).toContain('Video Discover');
  });

  it('says when it could not load', async () => {
    server.use(
      http.get('*/api/profiles/7/features', () =>
        HttpResponse.json({ success: false, error: 'Admin only' }, { status: 403 }),
      ),
    );
    const box = document.createElement('div');
    let called = false;
    await api._pfLoadProfileFeatures(7, box, { sides: 'both', allowed_pages: null }, () => {
      called = true;
    });
    expect(called).toBe(false);
    expect(box.textContent).toContain('Admin only');
  });
});

describe('#2164: the editor never passes their own pick off as the admin set', () => {
  const MEMBER = {
    ...PAYLOAD,
    // what they see now: their own pick
    off: ['discover', 'chat'],
    // what the admin controls: nothing set for them, so the default
    set_off: ['music_videos'],
    admin_set: false,
    own_off: ['discover', 'chat'],
    lock_set: false,
    follows_default: false,
  };

  async function load(body: object) {
    server.use(http.get('*/api/profiles/7/features', () => HttpResponse.json(body)));
    const box = document.createElement('div');
    document.body.append(box);
    let fst: Fst | null = null;
    await api._pfLoadProfileFeatures(7, box, { sides: 'both', allowed_pages: null }, (f) => {
      fst = f;
    });
    return { box, fst: fst! };
  }

  it('the switches start from the admin set, their pick is named apart', async () => {
    const { box, fst } = await load(MEMBER);
    expect(fst.off).toEqual(['music_videos']);
    expect(switchFor(box, 'Discover').checked).toBe(true);
    expect(switchFor(box, 'Chat & Arcade').checked).toBe(true);
    expect(box.textContent).toContain('They picked their own (2 parts hidden)');
    expect(box.textContent).toContain('Follows what new profiles see.');
    // their pick is something to reset
    const reset = [...box.querySelectorAll('button')].find(
      (b) => b.textContent === 'Reset to default',
    )!;
    expect(reset.disabled).toBe(false);
  });

  it('locking alone sends just the lock and says it replaces their pick', async () => {
    const { box, fst } = await load(MEMBER);
    switchFor(box, 'Lock it').click();
    expect(api.pfFeaturesBody(fst)).toEqual({ locked: true });
    expect(box.textContent).toContain('Saving replaces it with this.');
  });

  it('a switch sends the set, not the lock', async () => {
    const { box, fst } = await load(MEMBER);
    switchFor(box, 'Discover').click();
    expect(api.pfFeaturesBody(fst)).toEqual({ off: ['music_videos', 'discover'] });
  });

  it('locked, it says their old pick is ignored', async () => {
    const { box } = await load({ ...MEMBER, locked: true, off: ['music_videos'] });
    expect(box.textContent).toContain("It's ignored while this is locked.");
  });

  it('no pick of their own, no note', async () => {
    const { box } = await load({ ...MEMBER, own_off: null, off: ['music_videos'] });
    expect(box.textContent).not.toContain('They picked their own');
  });
});

describe('#2164: the page list says what they can open at a glance', () => {
  it('counts the pages on the sides they have', () => {
    expect(api.pfPagesSummary({ sides: 'both', allowed_pages: null })).toBe('Pages: all of them');
    expect(api.pfPagesSummary({ sides: 'both', allowed_pages: ['dashboard', 'video-tools'] })).toBe(
      'Pages: 2 of 5',
    );
    expect(
      api.pfPagesSummary({ sides: 'music', allowed_pages: ['dashboard', 'video-tools'] }),
    ).toBe('Pages: 1 of 3');
  });
});
