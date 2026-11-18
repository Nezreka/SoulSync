import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

import { extractFunction } from './vanilla-extract';

/**
 * #2191: a member's notification panel keeps their own notes. a refusal
 * ("Chat & Arcade isn't part of your profile") or a progress line
 * ("Fetching full discography...") is said once and never kept; a new
 * profile in the same tab starts the panel over; and chat's live push
 * never asks /api/chat for a profile without Chat & Arcade. these run the
 * real functions out of downloads.js and chat.js.
 */

const DOWNLOADS = readFileSync(resolve(process.cwd(), 'static/downloads.js'), 'utf8');
const CHAT = readFileSync(resolve(process.cwd(), 'static/chat.js'), 'utf8');
const INIT = readFileSync(resolve(process.cwd(), 'static/init.js'), 'utf8');

function lift<T>(
  source: string,
  names: string[],
  returns: string,
  scope: Record<string, unknown> = {},
): T {
  const keys = Object.keys(scope);
  // eslint-disable-next-line @typescript-eslint/no-implied-eval
  const fn = new Function(
    ...keys,
    `${names.map((n) => extractFunction(n, source)).join('\n')}\nreturn ${returns};`,
  );
  return fn(...keys.map((k) => scope[k])) as T;
}

type Note = { message: string };

function toastHarness() {
  const state = {
    history: [] as Note[],
    unreadCount: 0,
    panelOpen: false,
    maxHistory: 50,
    pending: [] as unknown[],
  };
  const journal: unknown[] = [];
  const show = lift<
    (m: string, t?: string, h?: unknown, o?: { history?: boolean; journal?: boolean }) => void
  >(DOWNLOADS, ['showToast'], 'showToast', {
    _recentToastKeys: new Map(),
    _notifState: state,
    _updateNotifBadge: () => undefined,
    _queueNotifJournal: (type: string, message: string) => journal.push({ type, message }),
    _renderNotifFeed: () => undefined,
  });
  return { state, journal, show };
}

describe('what the panel keeps', () => {
  it('keeps an ordinary note, and journals it', () => {
    const { state, journal, show } = toastHarness();
    show('Wind and Truth is in your library now', 'success');
    expect(state.history.map((n) => n.message)).toEqual(['Wind and Truth is in your library now']);
    expect(state.unreadCount).toBe(1);
    expect(journal).toHaveLength(1);
  });

  it('says a refusal or a progress line once, and keeps neither', () => {
    const { state, journal, show } = toastHarness();
    show("Chat & Arcade isn't part of your profile. Ask an admin if you'd like it.", 'info', null, {
      history: false,
    });
    show('Fetching full discography…', 'info', null, { history: false });
    expect(state.history).toEqual([]);
    expect(state.unreadCount).toBe(0);
    expect(journal).toEqual([]);
  });

  it('the refusal the fetch guard shows is one of those', () => {
    const at = INIT.indexOf('showToast(body.message');
    expect(at).toBeGreaterThan(0);
    const call = INIT.slice(at, INIT.indexOf(';', at));
    expect(call).toContain('{ history: false }');
  });
});

describe('a new profile in the same tab', () => {
  it('starts the panel over: no notes, no system numbers', () => {
    const state = {
      history: [{ message: "the admin's note" }],
      unreadCount: 3,
      pending: [{ type: 'info', message: 'x' }],
      sysStats: { uptime: '1:30:17', memory_usage: '15.2%' },
      seeded: true,
      openFamilies: { a: true },
      panelOpen: false,
    };
    const seeded: string[] = [];
    lift<() => void>(DOWNLOADS, ['_notifResetForProfile'], '_notifResetForProfile', {
      _notifState: state,
      _notifHealth: { slskd: 'down' },
      _updateNotifBadge: () => undefined,
      _updateNotifDlBadge: () => undefined,
      _notifSysHTML: () => '',
      _renderNotifFeed: () => undefined,
      _seedNotifPanelFromJournal: () => seeded.push('journal'),
      _seedNotifSys: () => seeded.push('sys'),
    })();
    expect(state.history).toEqual([]);
    expect(state.unreadCount).toBe(0);
    expect(state.pending).toEqual([]);
    expect(state.sysStats).toBeNull();
    expect(state.seeded).toBe(false);
    // its own journal, and the numbers only if it's an admin (_seedNotifSys asks)
    expect(seeded).toEqual(['journal', 'sys']);
  });
});

describe("chat's live push", () => {
  const allowed = (perms: unknown) =>
    lift<() => boolean>(CHAT, ['_chatAllowed'], '_chatAllowed', {
      window: { SoulSyncPerms: perms },
    })();

  it('is ignored by a profile without Chat & Arcade', () => {
    const p = (on: boolean) => ({ live: () => true, on: (id: string) => id === 'chat.use' && on });
    expect(allowed(p(false))).toBe(false);
    expect(allowed(p(true))).toBe(true);
  });

  it('is taken while the map is still on its way (the server only sends it to chat)', () => {
    expect(allowed(undefined)).toBe(true);
    expect(allowed({ live: () => false, on: () => false })).toBe(true);
  });

  it('never asks /api/chat for a profile without it', () => {
    let asked = 0;
    const onRoomMessages = lift<(d: unknown) => void>(CHAT, ['onRoomMessages'], 'onRoomMessages', {
      _chatAllowed: () => false,
      _ensureSelf: () => {
        asked += 1;
      },
    });
    onRoomMessages({ messages: [{ message: 'hi' }] });
    expect(asked).toBe(0);
  });
});
