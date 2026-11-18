/**
 * #2166 follow-ups 5 and 9: adding someone and inviting someone start from a
 * template (not the old column presets), and Manage profiles opens
 * Settings > People. Runs init.js's real functions.
 */

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { extractFunction } from './vanilla-extract';

const INIT = readFileSync(resolve(process.cwd(), 'static/init.js'), 'utf8');
const fn = (name: string) => extractFunction(name, INIT);

const TEMPLATES = {
  success: true,
  signup_template_id: 4,
  templates: [
    { id: 1, name: 'Adult', description: 'Downloads anything', followers: [] },
    { id: 2, name: 'Teen', description: '', followers: [{ id: 7, name: 'Maya' }] },
    { id: 4, name: 'Guest', description: 'Listens to music', followers: [] },
  ],
};

type Picker = {
  box: HTMLElement;
  ready: Promise<boolean>;
  value: () => { id: number; name: string } | null;
};

function picker(body: unknown, ok = true): Picker {
  vi.stubGlobal(
    'fetch',
    vi.fn(() =>
      ok
        ? Promise.resolve(new Response(JSON.stringify(body), { status: 200 }))
        : Promise.reject(new Error('offline')),
    ),
  );
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  return new Function(
    `${fn('pfEl')}\n${fn('pfReadJson')}\n${fn('pfTemplatePicker')}\nreturn pfTemplatePicker();`,
  )() as Picker;
}

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.innerHTML = '';
});

describe('the template picker', () => {
  it('shows every template and starts on the one new people start as', async () => {
    const p = picker(TEMPLATES);
    expect(await p.ready).toBe(true);
    const cards = [...p.box.querySelectorAll<HTMLElement>('[data-template-id]')];
    expect(cards.map((c) => c.querySelector('.pf-preset-name')!.textContent)).toEqual([
      'Adult',
      'Teen',
      'Guest',
    ]);
    expect(p.value()!.id).toBe(4);
    expect(cards[2].getAttribute('aria-checked')).toBe('true');
    // no description: who follows it instead
    expect(cards[1].textContent).toContain('1 person follows it');
    cards[1].click();
    expect(p.value()!.name).toBe('Teen');
    expect(cards[1].getAttribute('aria-checked')).toBe('true');
    expect(cards[2].getAttribute('aria-checked')).toBe('false');
  });

  it('falls back to the first template without a sign-up template', async () => {
    const p = picker({ ...TEMPLATES, signup_template_id: null });
    await p.ready;
    expect(p.value()!.id).toBe(1);
  });

  it('says so and picks nothing when the list can not load', async () => {
    const p = picker(null, false);
    expect(await p.ready).toBe(false);
    expect(p.value()).toBeNull();
    expect(p.box.textContent).toContain("Couldn't load the templates");
  });
});

describe('adding someone (the create step)', () => {
  const body = fn('openProfileEditor');
  const branch = body.slice(
    body.indexOf('} else if (isCreate) {'),
    body.indexOf('// admin switch (editing someone else only)'),
  );

  it('offers templates, the PIN and the password, and none of the old column switches', () => {
    expect(branch).toContain('pfTemplatePicker()');
    expect(branch).toContain('pinSwitch.row');
    expect(branch).toContain('passwordInput');
    expect(branch).toContain("Fine-tune anything in their permissions once they're added.");
    expect(branch).not.toMatch(/PROFILE_PRESETS|dlSwitch|pfPagePicker|explicitSwitch|ratingRow/);
  });

  it('makes the profile small, then puts it on the template with no changes', () => {
    const submit = extractFunction('submitCreate', body);
    expect(submit).not.toContain('accessPayload()');
    expect(submit).toContain('can_download: false');
    expect(submit).toMatch(
      /send\(`\/api\/profiles\/\$\{newId\}\/permissions`, 'PUT', \{ template_id: template\.id, reset: 'all' \}\)/,
    );
    // a failed template step is said plainly, the profile still exists
    expect(submit).toContain("they don't follow ${template.name} yet");
  });
});

describe('inviting someone', () => {
  function runInvite() {
    const posts: unknown[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string, init?: RequestInit) => {
        if (url === '/api/profiles/invites' && init?.method === 'POST') {
          posts.push(JSON.parse(typeof init.body === 'string' ? init.body : '{}'));
          return Promise.resolve(
            new Response(
              JSON.stringify({ success: true, path: '/invite/abc', expires_hours: 72 }),
              {
                status: 201,
              },
            ),
          );
        }
        return Promise.resolve(new Response(JSON.stringify(TEMPLATES), { status: 200 }));
      }),
    );
    const modal = {
      body: document.createElement('div'),
      foot: document.createElement('div'),
      titleEl: document.createElement('h2'),
      subEl: document.createElement('p'),
      open: vi.fn(),
      close: vi.fn(),
    };
    document.body.append(modal.body, modal.foot);
    // eslint-disable-next-line @typescript-eslint/no-implied-eval
    new Function(
      'pfModal',
      'PF_INVITE_EXPIRY',
      'pfInviteUrl',
      'loadInviteList',
      'showToast',
      `var _pfUid = 0;\n${fn('pfEl')}\n${fn('pfReadJson')}\n${fn('pfTemplatePicker')}\n${fn('openInviteCreator')}\nreturn openInviteCreator();`,
    )(
      () => modal,
      [[72, '3 days']],
      (p: string) => `http://x${p}`,
      () => {},
      () => {},
    );
    return { modal, posts };
  }
  const flush = () => new Promise((r) => setTimeout(r, 0));

  it('sends the picked template, not column settings', async () => {
    const { modal, posts } = runInvite();
    for (let i = 0; i < 5; i++) await flush();
    expect(modal.body.querySelector('.pf-preset')).not.toBeNull();
    expect(modal.body.textContent).not.toContain('More');
    modal.body.querySelector<HTMLElement>('[data-template-id="2"]')!.click();
    const create = [...modal.foot.querySelectorAll('button')].find(
      (b) => b.textContent === 'Create link',
    )!;
    create.click();
    for (let i = 0; i < 5; i++) await flush();
    expect(posts).toEqual([expect.objectContaining({ preset: { template_id: 2 } })]);
  });
});

describe('Manage profiles opens Settings > People (#2166)', () => {
  function run(open: unknown) {
    (window as unknown as Record<string, unknown>).openPeopleSettings = open;
    // eslint-disable-next-line @typescript-eslint/no-implied-eval
    const go = new Function(
      'pfCloseMenus',
      'currentProfile',
      'hideProfilePicker',
      'pfCloseLayer',
      `${fn('pfOpenPeople')}\nreturn pfOpenPeople;`,
    )(
      () => {},
      { id: 1 },
      () => {},
      () => {},
    ) as (pill?: string) => boolean;
    return go;
  }
  afterEach(() => {
    delete (window as unknown as Record<string, unknown>).openPeopleSettings;
  });

  it('opens People when the settings bundle is there', () => {
    const open = vi.fn();
    expect(run(open)()).toBe(true);
    expect(open).toHaveBeenCalledWith(undefined);
    expect(run(open)('templates')).toBe(true);
    expect(open).toHaveBeenLastCalledWith('templates');
  });

  it('leaves the old Profiles modal as the fallback', () => {
    expect(run(undefined)()).toBe(false);
    expect(fn('openProfileManager')).toMatch(/if \(pfOpenPeople\(\)\) return;/);
  });

  it('the old What new profiles see card sends admins to the templates', () => {
    const list = fn('loadProfileManageList');
    expect(list).toContain("pfOpenPeople('templates')");
  });
});
