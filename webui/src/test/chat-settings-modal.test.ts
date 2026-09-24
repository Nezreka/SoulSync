import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { beforeEach, describe, expect, it } from 'vitest';

import { extractFunction } from './vanilla-extract';

/**
 * the chat settings modal went from one long list to tabs. openSettings and
 * saveSettings still find every field by its data hook, so a hook that got
 * lost in the move would load and save nothing, silently.
 */

const JS = readFileSync(resolve(process.cwd(), 'static/chat.js'), 'utf8');
const HTML = readFileSync(resolve(process.cwd(), '../webui/index.html'), 'utf8');

const start = HTML.indexOf('data-chat-settings-modal');
const end = HTML.indexOf('data-chat-messages', start);
const MODAL = HTML.slice(HTML.lastIndexOf('<div', start), HTML.lastIndexOf('<div', end));

// eslint-disable-next-line @typescript-eslint/no-implied-eval
const setTab = new Function(
  `${extractFunction('_setSettingsTab', JS)}; return _setSettingsTab;`,
)() as (n: string) => void;

describe('chat settings modal markup', () => {
  it.each([
    'data-chat-set-room',
    'data-chat-set-giphy',
    'data-chat-set-filepost',
    'data-chat-set-filepost-expiry',
    'data-chat-set-autojoin',
    'data-chat-set-membersend',
    'data-chat-set-autoprove',
    'data-chat-set-ping',
    'data-chat-set-np',
    'data-chat-avpicker',
    'data-chat-avwho',
    'data-chat-settings-save',
    'data-chat-settings-cancel',
  ])('still has %s', (hook) => {
    expect(MODAL).toContain(hook);
  });

  it('every tab has a panel and every panel has a tab', () => {
    const tabs = [...MODAL.matchAll(/data-chat-settab="([^"]+)"/g)].map((m) => m[1]).sort();
    const panels = [...MODAL.matchAll(/data-chat-setpanel="([^"]+)"/g)].map((m) => m[1]).sort();
    expect(tabs.length).toBeGreaterThan(1);
    expect(tabs).toEqual(panels);
  });
});

describe('switching tabs', () => {
  beforeEach(() => {
    document.body.innerHTML = MODAL;
  });

  it('shows exactly the picked panel and marks its tab', () => {
    setTab('room');
    const shown = [...document.querySelectorAll<HTMLElement>('[data-chat-setpanel]')].filter(
      (p) => !p.hidden,
    );
    expect(shown.map((p) => p.dataset.chatSetpanel)).toEqual(['room']);
    const on = document.querySelectorAll('.chat-set-tab--on');
    expect(on).toHaveLength(1);
    expect((on[0] as HTMLElement).dataset.chatSettab).toBe('room');
    expect(on[0].getAttribute('aria-selected')).toBe('true');
  });

  it('opening the modal lands on the profile tab', () => {
    expect(extractFunction('openSettings', JS)).toContain("_setSettingsTab('profile')");
  });
});
