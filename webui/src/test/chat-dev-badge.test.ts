import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { extractFunction } from './vanilla-extract';

/**
 * boulder is the lead dev, contributors can earn a plain dev tag. the badge
 * used to be one hardcoded name pasted into five places, so a second dev
 * would have come out as a second "creator & lead developer".
 */

const JS = readFileSync(resolve(process.cwd(), 'static/chat.js'), 'utf8');

// the real lists, not a copy, so adding a dev can't drift from what's tested
function listLine(name: string): string {
  const m = JS.match(new RegExp(`var ${name} = [^;]+;`));
  if (!m) throw new Error(`${name} not found in chat.js`);
  return m[0];
}

const deps = [
  listLine('LEAD_DEV'),
  listLine('CHAT_DEVS'),
  ...['isLeadDev', 'isDev', 'devTitle', 'devBadge'].map((n) => extractFunction(n, JS)),
].join('\n');

// eslint-disable-next-line @typescript-eslint/no-implied-eval
const api = new Function(`${deps}; return { isDev, isLeadDev, devBadge, devTitle };`)() as {
  isDev: (n: string) => boolean;
  isLeadDev: (n: string) => boolean;
  devBadge: (n: string, mod?: string) => string;
  devTitle: (n: string) => string;
};

describe('dev tags', () => {
  it('boulder gets lead dev', () => {
    expect(api.isLeadDev('BoulderBadgeDad')).toBe(true);
    expect(api.devBadge('BoulderBadgeDad')).toContain('LEAD DEV');
    expect(api.devTitle('BoulderBadgeDad')).toBe('SoulSync Creator & Lead Developer');
  });

  it('ezra9 gets a plain dev tag, not lead', () => {
    expect(api.isDev('ezra9')).toBe(true);
    expect(api.isLeadDev('ezra9')).toBe(false);
    const badge = api.devBadge('Ezra9');
    expect(badge).toContain('> DEV<');
    expect(badge).not.toContain('LEAD');
    expect(api.devTitle('ezra9')).toBe('SoulSync Developer');
  });

  it('everyone else gets nothing', () => {
    expect(api.isDev('randomuser')).toBe(false);
    expect(api.isDev('')).toBe(false);
    expect(api.devBadge('randomuser')).toBe('');
  });

  it('size modifiers ride along', () => {
    expect(api.devBadge('ezra9', 'inline')).toContain('chat-dev-badge chat-dev-badge--inline');
    expect(api.devBadge('ezra9', 'lg')).toContain('chat-dev-badge--lg');
  });

  it('no badge is hardcoded outside the helper anymore', () => {
    expect(JS.match(/> DEV<\/span>/g) ?? []).toHaveLength(0);
    // the title string lives in devTitle only, not pasted into markup
    expect(JS.match(/'SoulSync Creator & Lead Developer'/g) ?? []).toHaveLength(1);
    expect(JS).not.toContain('Creator &amp; Lead Developer');
  });
});

describe('sidebar groups', () => {
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  const bucket = new Function(
    `var state = { selfName: 'me' }; function isFriend(n) { return n === 'pal'; }
     ${deps}; ${extractFunction('_bucketUsers', JS)}; return _bucketUsers;`,
  )() as (names: string[], cls: Record<string, string>) => Record<string, string[]>;

  it('lead dev sits in its own group, apart from the other devs', () => {
    const b = bucket(['ezra9', 'BoulderBadgeDad', 'me', 'pal', 'someone', 'old'], {
      old: 'vanilla',
    });
    expect(b.leads).toEqual(['BoulderBadgeDad']);
    expect(b.devs).toEqual(['ezra9']);
    expect(b.self).toEqual(['me']);
    expect(b.friends).toEqual(['pal']);
    expect(b.apps).toEqual(['someone']);
    expect(b.rest).toEqual(['old']);
  });

  it('the lead group renders first', () => {
    const body = extractFunction('renderUsersList', JS);
    expect(body.indexOf('Lead Developer')).toBeGreaterThan(-1);
    expect(body.indexOf('Lead Developer')).toBeLessThan(body.indexOf('🛠️ Developer'));
  });
});
